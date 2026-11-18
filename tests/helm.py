"""Helpers for tests of the Helm chart (`deployment/`)."""

from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import yaml

# A rendered manifest (Kubernetes object)
Manifest = dict[str, Any]

CHART = Path(__file__).parents[1] / "deployment"
HELM = shutil.which("helm")
# Release rendered by default, and the names of its resources
RELEASE = "prowes"
APP = "prowes"
MONGODB = "prowes-mongodb"
WORKER = "prowes-worker"
BROKER = "prowes-broker"
FLOWER = "prowes-flower"
STORAGE = "prowes-data"
# Names of the Secrets with the MongoDB credentials and the broker URL,
# required by default (authentication, worker and bundled broker enabled)
MONGODB_SECRET = "mongodb-credentials"  # noqa: S105 (a Secret's name, no credential)
BROKER_SECRET = "celery"  # noqa: S105 (a Secret's name, no credential)
# Templates added to a copy of the chart to render its notes into a ConfigMap,
# as `helm template` does not render notes (and `helm install --dry-run` needs
# a cluster)
NOTES_HELPER = '{{- define "notes-test" }}NOTES{{- end }}'
NOTES_CONFIGMAP = """apiVersion: v1
kind: ConfigMap
metadata:
  name: notes-test
data:
  notes: {{ include "notes-test" . | toJson }}
"""


def render(
    *args: str,
    mongodb_secret: str | None = MONGODB_SECRET,
    broker_secret: str | None = BROKER_SECRET,
    release: str = RELEASE,
    chart: Path = CHART,
) -> list[Manifest]:
    """Render the chart with `helm template` arguments.

    Args:
        args: Arguments of `helm template`, after the defaults set by the
            other arguments, so they take precedence.
        mongodb_secret: Name of the Secret with the MongoDB credentials; not
            set if `None`.
        broker_secret: Name of the Secret with the broker URL; not set if
            `None`.
        release: Name of the release.
        chart: Chart directory.

    Returns:
        The rendered manifests.

    Raises:
        RuntimeError: The chart's dependencies are not built (see "Testing the
            chart" in `docs/deployment.md`).
        subprocess.CalledProcessError: Helm failed, e.g., as a required value
            is not set.
    """
    assert HELM is not None
    if not list((chart / "charts").glob("foca-common-*.tgz")):
        raise RuntimeError(
            "The library chart is missing: run `helm dependency build deployment`"
        )
    defaults: list[str] = []
    if mongodb_secret is not None:
        defaults += ["--set", f"mongodb.auth.existingSecret={mongodb_secret}"]
    if broker_secret is not None:
        defaults += ["--set", f"broker.existingSecret={broker_secret}"]
    rendered = subprocess.run(  # noqa: S603 (argument list, no shell)
        [HELM, "template", release, str(chart), *defaults, *args],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def render_notes(*args: str, **kwargs: Any) -> str:
    """Render the chart's notes (`templates/NOTES.txt`).

    Args:
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `render()`, except `chart`.

    Returns:
        The rendered notes.
    """
    notes = (CHART / "templates" / "NOTES.txt").read_text()
    with tempfile.TemporaryDirectory() as tmp_dir:
        chart = Path(tmp_dir) / "chart"
        shutil.copytree(CHART, chart)
        templates = chart / "templates"
        (templates / "_notes-test.tpl").write_text(NOTES_HELPER.replace("NOTES", notes))
        (templates / "notes-test.yaml").write_text(NOTES_CONFIGMAP)
        docs = render(*args, chart=chart, **kwargs)
    (configmap,) = [doc for doc in docs if doc["metadata"]["name"] == "notes-test"]
    notes_text: str = configmap["data"]["notes"]
    return notes_text
