"""Tests of the Helm chart (`deployment/`): the image's non-root user.

The chart is rendered with `helm template` (no cluster needed); the tests
are skipped where Helm isn't installed (GitHub's Ubuntu runners have it).
"""

from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = Path(__file__).parents[1] / "deployment"
HELM = shutil.which("helm")
# Deployments of the app and the Celery worker (default `appName`s)
DEPLOYMENTS = ("prowes", "celery-worker")

pytestmark = pytest.mark.skipif(HELM is None, reason="Helm is not installed")


def _deployments(cluster_type: str) -> dict[str, dict[str, Any]]:
    """Render the chart and get the pod specs of its deployments.

    Args:
        cluster_type: Value of `clusterType` (`kubernetes` or `openshift`).

    Returns:
        Pod specs by deployment name.
    """
    assert HELM is not None
    rendered = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
        [HELM, "template", "test", str(CHART), "--set", f"clusterType={cluster_type}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        doc["metadata"]["name"]: doc["spec"]["template"]["spec"]
        for doc in yaml.safe_load_all(rendered)
        if doc and doc["kind"] == "Deployment"
    }


@pytest.mark.parametrize("name", DEPLOYMENTS)
def test_kubernetes_data_writable_for_user(name: str) -> None:
    """On Kubernetes, `/data` is handed to the image's user 1000.

    By `fsGroup`, where Kubernetes manages the volume's ownership, and by the
    init container (as root), where it doesn't (e.g., NFS).

    Args:
        name: Name of the deployment.
    """
    spec = _deployments("kubernetes")[name]
    assert spec["securityContext"] == {"fsGroup": 1000}
    (init,) = spec["initContainers"]
    assert init["command"] == ["sh", "-c"]
    (script,) = init["args"]
    assert script.endswith("&& chown 1000:0 /data && chmod 2775 /data")


@pytest.mark.parametrize("name", DEPLOYMENTS)
def test_openshift_assigns_user(name: str) -> None:
    """On OpenShift, the chart sets neither `fsGroup` nor ownership.

    OpenShift assigns user and group itself, and its restricted security
    context constraints refuse a fixed `fsGroup`.

    Args:
        name: Name of the deployment.
    """
    spec = _deployments("openshift")[name]
    assert "securityContext" not in spec
    (init,) = spec["initContainers"]
    assert init["command"] == ["mkdir"]
