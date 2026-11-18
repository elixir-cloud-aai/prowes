"""Tests of the Helm chart (`deployment/`).

Most resources are rendered by FOCA's library chart foca-common, which has
tests of its own; these tests check the app's settings, values and checks,
the app's own templates (storage of the runs, Flower) and how the chart fits
the image and the app's base config. The chart is rendered with `helm
template` (no cluster needed); the tests are skipped where Helm isn't
installed (GitHub's Ubuntu runners have it), and need the library chart
(`helm dependency build deployment`).
"""

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import pytest
import yaml

from tests.helm import (
    APP,
    BROKER,
    BROKER_SECRET,
    CHART,
    FLOWER,
    HELM,
    MONGODB,
    MONGODB_SECRET,
    STORAGE,
    WORKER,
    render,
    render_notes,
)

ROOT = Path(__file__).resolve().parents[1]
CHART_YAML = yaml.safe_load((CHART / "Chart.yaml").read_text())
# Default image tag
APP_VERSION = CHART_YAML["appVersion"]
NO_INGRESS = ("--set", "prowes.ingress.enabled=false")
OPENSHIFT = (
    "--api-versions",
    "route.openshift.io/v1",
    "--api-versions",
    "security.openshift.io/v1",
)
FLOWER_ON = (
    "--set",
    "flower.enabled=true",
    "--set",
    "flower.existingSecret=flower",
)
# A value from a Secret
EXTRA_ENV = {
    "name": "WES_PASSWORD",
    "valueFrom": {"secretKeyRef": {"name": "wes", "key": "password"}},
}
WITH_EXTRA_ENV = ("--set-json", f"prowes.extraEnv={json.dumps([EXTRA_ENV])}")
RWX = ("--set-json", 'prowes.storage.accessModes=["ReadWriteMany"]')
# ingress-nginx's size limit of request bodies, and an app limit above the
# default 10 MiB
BODY_SIZE = "nginx.ingress.kubernetes.io/proxy-body-size"
LIMIT_20_MIB = ["--set", "config.server.max_content_length=20971520"]

# Rendered manifests, by kind and name
Docs = dict[tuple[str, str], dict[str, Any]]

pytestmark = pytest.mark.skipif(HELM is None, reason="Helm is not installed")


def _render(*args: str, **kwargs: Any) -> Docs:
    """Render the chart and index the documents by kind and name.

    Args:
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `tests.helm.render()`.

    Returns:
        The rendered manifests, by kind and name.
    """
    return {
        (doc["kind"], doc["metadata"]["name"]): doc
        for doc in render("--set", "hostName=wes.example.org", *args, **kwargs)
    }


def _render_error(*args: str, **kwargs: Any) -> str:
    """Render the chart, expecting it to fail.

    Args:
        args: Arguments of `helm template`.
        kwargs: Keyword arguments of `tests.helm.render()`.

    Returns:
        The error output of Helm.
    """
    with pytest.raises(subprocess.CalledProcessError) as exc:
        _render(*args, **kwargs)
    stderr: str = exc.value.stderr
    return stderr


def _pod(docs: Docs, name: str, kind: str = "Deployment") -> dict[str, Any]:
    """Get the pod spec of a workload.

    Args:
        docs: Rendered manifests.
        name: Name of the workload.
        kind: Kind of the workload.

    Returns:
        The pod spec.
    """
    pod: dict[str, Any] = docs[(kind, name)]["spec"]["template"]["spec"]
    return pod


def _container(docs: Docs, name: str) -> dict[str, Any]:
    """Get the (only) container of a Deployment.

    Args:
        docs: Rendered manifests.
        name: Name of the Deployment.

    Returns:
        The container spec.
    """
    (container,) = _pod(docs, name)["containers"]
    return container


def _env(container: dict[str, Any]) -> dict[str, Any]:
    """Get the environment variables of a container.

    Args:
        container: Container spec.

    Returns:
        The variables, by name.
    """
    return {var["name"]: var for var in container.get("env", [])}


def _mounts(container: dict[str, Any]) -> dict[str, str]:
    """Get the volume mounts of a container.

    Args:
        container: Container spec.

    Returns:
        The names of the mounted volumes, by mount path.
    """
    return {mount["mountPath"]: mount["name"] for mount in container["volumeMounts"]}


def _volumes(docs: Docs, name: str) -> dict[str, dict[str, Any]]:
    """Get the volumes of a Deployment's pods.

    Args:
        docs: Rendered manifests.
        name: Name of the Deployment.

    Returns:
        The volume sources, by volume name.
    """
    return {
        volume["name"]: {key: value for key, value in volume.items() if key != "name"}
        for volume in _pod(docs, name)["volumes"]
    }


def _overlay(docs: Docs) -> dict[str, Any]:
    """Get the config overlay of the app.

    Args:
        docs: Rendered manifests.

    Returns:
        The parsed `overlay.yaml` of the app's ConfigMap.
    """
    overlay: dict[str, Any] = yaml.safe_load(
        docs[("ConfigMap", f"{APP}-config")]["data"]["overlay.yaml"]
    )
    return overlay


def test_default_resources() -> None:
    """By default, the chart deploys the app, worker, MongoDB and broker."""
    docs = _render()
    assert set(docs) == {
        ("ConfigMap", f"{APP}-config"),
        ("ConfigMap", f"{BROKER}-config"),
        ("ConfigMap", f"{MONGODB}-init-script"),
        ("Deployment", APP),
        ("Deployment", MONGODB),
        ("Deployment", WORKER),
        ("Ingress", APP),
        ("NetworkPolicy", BROKER),
        ("NetworkPolicy", MONGODB),
        ("PersistentVolumeClaim", BROKER),
        ("PersistentVolumeClaim", MONGODB),
        ("PersistentVolumeClaim", STORAGE),
        ("Service", APP),
        ("Service", BROKER),
        ("Service", MONGODB),
        ("StatefulSet", BROKER),
    }


def test_ci_values_render() -> None:
    """The values of CI (`deployment/ci/`) render, also with the optional parts."""
    ci = CHART / "ci"
    docs = _render("-f", str(ci / "test-values.yaml"))
    assert ("Deployment", APP) in docs
    docs = _render(
        "-f", str(ci / "test-values.yaml"), "-f", str(ci / "optional-values.yaml")
    )
    assert ("Deployment", FLOWER) in docs
    assert docs[("Deployment", APP)]["spec"]["replicas"] == 2


def test_image_defaults_to_app_version() -> None:
    """The app and the worker run the image of the chart's `appVersion`."""
    assert APP_VERSION == "dev"
    docs = _render()
    for name in (APP, WORKER):
        container = _container(docs, name)
        assert container["image"] == f"elixircloud/prowes:{APP_VERSION}"
        assert container["imagePullPolicy"] == "Always"


def test_image_tag() -> None:
    """`prowes.tag` replaces the default tag; images may include their own."""
    docs = _render("--set", "prowes.tag=1.2.3")
    assert _container(docs, APP)["image"] == "elixircloud/prowes:1.2.3"
    docs = _render("--set", "prowes.image=registry.example.org/prowes:4.5.6")
    assert _container(docs, WORKER)["image"] == "registry.example.org/prowes:4.5.6"


def test_probes_swagger_ui() -> None:
    """The app's probes request the Swagger UI of the image's base config."""
    config = yaml.safe_load((ROOT / "pro_wes" / "config.yaml").read_text())
    (spec,) = config["api"]["specs"]
    base_path = spec["connexion"]["base_path"]
    container = _container(_render(), APP)
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"]["path"] == f"{base_path}/ui/"


def test_config_overlay() -> None:
    """The overlay sets the database host and merges `config`."""
    service_info = {"service": {"id": "org.example.wes", "name": "Example"}}
    docs = _render("--set-json", f"config.service_info={json.dumps(service_info)}")
    overlay = _overlay(docs)
    assert overlay == {"db": {"host": MONGODB}, "service_info": service_info}
    for name in (APP, WORKER):
        container = _container(docs, name)
        assert _env(container)["FOCA_CONFIG_OVERLAY"]["value"] == (
            "/etc/prowes/overlay.yaml"
        )
        assert _mounts(container)["/etc/prowes"] == "config"


def test_mongodb_database() -> None:
    """The app user gets the database of the base config, `runStore`."""
    config = yaml.safe_load((ROOT / "pro_wes" / "config.yaml").read_text())
    assert list(config["db"]["dbs"]) == ["runStore"]
    script = _render()[("ConfigMap", f"{MONGODB}-init-script")]["data"][
        "create-app-user.js"
    ]
    assert "db.getSiblingDB('runStore')" in script
    env = _env(_container(_render(), APP))
    assert env["MONGO_USERNAME"]["valueFrom"]["secretKeyRef"] == {
        "name": MONGODB_SECRET,
        "key": "username",
    }


def test_worker() -> None:
    """The worker runs proWES's Celery app, connected to the broker."""
    container = _container(_render(), WORKER)
    assert container["command"][:4] == [
        "celery",
        "--app",
        "pro_wes.celery_worker",
        "worker",
    ]
    assert _module_exists("pro_wes.celery_worker")
    env = _env(container)
    assert env["CELERY_BROKER_URL"]["valueFrom"]["secretKeyRef"] == {
        "name": BROKER_SECRET,
        "key": "broker-url",
    }


def _module_exists(name: str) -> bool:
    """Check that a module of the app exists.

    Args:
        name: Dotted name of the module.

    Returns:
        Whether the module's file exists in the repository.
    """
    return (ROOT / (name.replace(".", "/") + ".py")).is_file()


def test_storage_claim() -> None:
    """The app mounts the storage of the runs at the configured path.

    The worker doesn't use it, so it doesn't mount it; the claim is
    `ReadWriteOnce` by default, so the app's pods are replaced, not rolled.
    """
    config = yaml.safe_load((ROOT / "pro_wes" / "config.yaml").read_text())
    assert config["custom"]["post_runs"]["storage_path"] == "/data"
    docs = _render()
    claim = docs[("PersistentVolumeClaim", STORAGE)]
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"]["requests"]["storage"] == "1Gi"
    assert "annotations" not in claim["metadata"]
    assert _mounts(_container(docs, APP))["/data"] == "data"
    assert _volumes(docs, APP)["data"] == {
        "persistentVolumeClaim": {"claimName": STORAGE}
    }
    assert "/data" not in _mounts(_container(docs, WORKER))
    assert "data" not in _volumes(docs, WORKER)
    assert docs[("Deployment", APP)]["spec"]["strategy"] == {"type": "Recreate"}
    assert docs[("Deployment", WORKER)]["spec"]["strategy"]["type"] == "RollingUpdate"


def test_storage_writable_for_image_user() -> None:
    """`fsGroup` makes the storage writable for the image's user (1000)."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "install -d -o 1000 -g 0 -m 2775 /data" in dockerfile
    assert re.search(r"^USER 1000$", dockerfile, re.MULTILINE)
    context = _pod(_render(), APP)["securityContext"]
    assert context["fsGroup"] == 1000
    assert context["fsGroupChangePolicy"] == "OnRootMismatch"
    assert context["runAsNonRoot"] is True


def test_openshift() -> None:
    """On OpenShift, `fsGroup` is omitted and a Route exposes the app."""
    docs = _render(*NO_INGRESS, *OPENSHIFT)
    for name in (APP, WORKER):
        assert "fsGroup" not in _pod(docs, name)["securityContext"]
    assert ("Route", APP) in docs
    assert ("Ingress", APP) not in docs


def test_storage_shared() -> None:
    """With `ReadWriteMany`, the app's pods are rolled and may be several."""
    docs = _render(*RWX, "--set", "prowes.replicas=2")
    assert docs[("PersistentVolumeClaim", STORAGE)]["spec"]["accessModes"] == [
        "ReadWriteMany"
    ]
    deployment = docs[("Deployment", APP)]
    assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
    assert deployment["spec"]["replicas"] == 2


def test_storage_replicas_need_rwx() -> None:
    """Several replicas of the app need a `ReadWriteMany` claim."""
    error = _render_error("--set", "prowes.replicas=2")
    assert "prowes.replicas must be 1 unless" in error
    error = _render_error(
        "--set", "prowes.replicas=2", "--set", "prowes.storage.existingClaim=runs"
    )
    assert "prowes.replicas must be 1 unless" in error


def test_storage_existing_claim() -> None:
    """An existing claim is mounted instead of the chart's own."""
    docs = _render("--set", "prowes.storage.existingClaim=runs")
    assert ("PersistentVolumeClaim", STORAGE) not in docs
    assert _volumes(docs, APP)["data"] == {
        "persistentVolumeClaim": {"claimName": "runs"}
    }


def test_storage_keep() -> None:
    """With `resourcePolicy: keep`, Helm keeps the claim."""
    docs = _render("--set", "prowes.storage.resourcePolicy=keep")
    claim = docs[("PersistentVolumeClaim", STORAGE)]
    assert claim["metadata"]["annotations"] == {"helm.sh/resource-policy": "keep"}


def test_storage_disabled() -> None:
    """Without storage, the runs are kept in an `emptyDir` of `size`."""
    docs = _render("--set", "prowes.storage.enabled=false")
    assert ("PersistentVolumeClaim", STORAGE) not in docs
    assert _volumes(docs, APP)["data"] == {"emptyDir": {"sizeLimit": "1Gi"}}
    assert docs[("Deployment", APP)]["spec"]["strategy"]["type"] == "RollingUpdate"
    notes = render_notes(*NO_INGRESS, "--set", "prowes.storage.enabled=false")
    assert "The storage of the runs is an emptyDir" in notes
    error = _render_error(
        "--set", "prowes.storage.enabled=false", "--set", "prowes.replicas=2"
    )
    assert "prowes.replicas must be 1 unless" in error


def test_storage_must_be_map() -> None:
    """`prowes.storage` must be a map."""
    error = _render_error("--set", "prowes.storage=null")
    assert "prowes.storage must be a map" in error


def test_storage_name_collision() -> None:
    """The app's name must not collide with the claim's.

    The app's name is truncated before the suffix is added, so a name of 63
    characters that ends in `-data` would be that of the claim, too.
    """
    name = "a" * 58 + "-data"
    error = _render_error("--set", f"fullnameOverride={name}")
    assert f"would both be named {name}" in error


def test_extra_env() -> None:
    """`prowes.extraEnv` is added to the app's and the worker's containers."""
    docs = _render(*WITH_EXTRA_ENV)
    for name in (APP, WORKER):
        assert _env(_container(docs, name))["WES_PASSWORD"] == EXTRA_ENV
    # The app still has the storage of the runs
    assert _mounts(_container(docs, APP))["/data"] == "data"


@pytest.mark.parametrize(
    "name",
    [
        "FOCA_CONFIG_OVERLAY",
        "GUNICORN_CMD_ARGS",
        "MONGO_USERNAME",
        "MONGO_PASSWORD",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "MONGO_DBNAME",
        "MONGO_URI",
        "MONGO_HOST",
        "MONGO_PORT",
        "PORT",
    ],
)
def test_extra_env_reserved(name: str) -> None:
    """`prowes.extraEnv` must not set the variables of the chart.

    Args:
        name: Name of a variable that the chart sets.
    """
    env = [{"name": name, "value": "x"}]
    error = _render_error("--set-json", f"prowes.extraEnv={json.dumps(env)}")
    assert f"prowes.extraEnv must not set {name}" in error


def test_extra_env_duplicate() -> None:
    """`prowes.extraEnv` must not set a variable twice."""
    env = [{"name": "X", "value": "1"}, {"name": "X", "value": "2"}]
    error = _render_error("--set-json", f"prowes.extraEnv={json.dumps(env)}")
    assert "prowes.extraEnv sets X more than once" in error


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--set", "applicationDomain=example.org"), "applicationDomain was removed"),
        (("--set", "clusterType=openshift"), "clusterType was removed"),
        (("--set", "storageAccessMode=ReadWriteMany"), "storageAccessMode was removed"),
        (("--set", "celeryWorker.appName=celery-worker"), "celeryWorker was removed"),
        (("--set", "rabbitmq.appName=rabbitmq"), "rabbitmq was removed"),
        (("--set", "prowes.appName=prowes"), "prowes.appName was removed"),
        (("--set", "mongodb.appName=mongodb"), "mongodb.appName was removed"),
        (("--set", "flower.appName=flower"), "flower.appName was removed"),
        (("--set", "prowes.initResources.limits.cpu=50m"), "initResources was removed"),
        (("--set", "mongodb.secret.databaseUser=u"), "mongodb.secret was removed"),
        (("--set", "mongodb.volumeSize=1Gi"), "mongodb.volumeSize was removed"),
        (("--set", "flower.basicAuth=admin:admin"), "flower.basicAuth was removed"),
    ],
)
def test_legacy_values(args: tuple[str, str], message: str) -> None:
    """Values of the legacy chart fail, naming their replacement.

    Args:
        args: Arguments setting a value of the legacy chart.
        message: Expected part of the error.
    """
    error = _render_error(*args)
    assert message in error
    assert "From the legacy chart" in error


def test_flower() -> None:
    """Flower connects to the broker, with basic authentication."""
    docs = _render(*FLOWER_ON)
    container = _container(docs, FLOWER)
    env = _env(container)
    assert env["CELERY_BROKER_URL"]["valueFrom"]["secretKeyRef"] == {
        "name": BROKER_SECRET,
        "key": "broker-url",
    }
    assert env["FLOWER_BASIC_AUTH"]["valueFrom"]["secretKeyRef"] == {
        "name": "flower",
        "key": "basic-auth",
    }
    compose = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())
    assert container["image"] == compose["services"]["flower"]["image"]
    assert ("Service", FLOWER) in docs
    assert ("NetworkPolicy", FLOWER) in docs
    assert ("NetworkPolicy", f"{BROKER}-from-flower") in docs
    notes = render_notes(*NO_INGRESS, *FLOWER_ON)
    assert "kubectl port-forward svc/prowes-flower" in notes


def test_flower_needs_secret() -> None:
    """Flower requires its credentials."""
    error = _render_error("--set", "flower.enabled=true")
    assert "flower.existingSecret must be set" in error


def test_notes() -> None:
    """The notes warn of the missing authentication and show the URLs."""
    notes = render_notes("--set", "hostName=wes.example.org")
    assert "serves the WES API without authentication" in notes
    assert "https://wes.example.org/ga4gh/wes/v1/service-info" in notes
    assert "The Ingress has no TLS" in notes
    notes = render_notes(*NO_INGRESS)
    assert "kubectl port-forward svc/prowes 8080:8080" in notes
    assert "http://localhost:8080/ga4gh/wes/v1/ui/" in notes


def test_library_chart_pinned() -> None:
    """`Chart.yaml`, `Chart.lock` and `charts.sha256` pin the library chart."""
    lock = yaml.safe_load((CHART / "Chart.lock").read_text())
    (dependency,) = CHART_YAML["dependencies"]
    (locked,) = lock["dependencies"]
    assert dependency == locked
    (line,) = (CHART / "charts.sha256").read_text().splitlines()
    digest, path = line.split()
    assert path == f"charts/foca-common-{locked['version']}.tgz"
    archive = CHART / path
    if archive.is_file():
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    schema = json.loads((CHART / "values.schema.json").read_text())
    assert f"foca-common {locked['version']}" in schema["$comment"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ("--set", "prowes.storage.sise=2Gi"),
            "at '/prowes/storage': additional properties 'sise' not allowed",
        ),
        (("--set", "prowes.replicas=x"), "at '/prowes/replicas': got string"),
    ],
)
def test_values_schema(args: tuple[str, str], message: str) -> None:
    """The values schema rejects misspelt keys and values of the wrong type.

    Args:
        args: Arguments setting an invalid value.
        message: Expected part of the error.
    """
    assert message in _render_error(*args)


@pytest.mark.parametrize(
    "value",
    ['{"name": "X"}', '[{"value": "x"}]', '["X=x"]'],
)
def test_extra_env_invalid(value: str) -> None:
    """`prowes.extraEnv` must be a list of variables with names.

    The schema rejects these values before the chart's own check would.

    Args:
        value: Value of `prowes.extraEnv`, as JSON.
    """
    error = _render_error("--set-json", f"prowes.extraEnv={value}")
    assert "at '/prowes" in error
    assert "extraEnv" in error


def test_flower_needs_broker_secret() -> None:
    """Flower requires the broker URL, also with an external broker."""
    error = _render_error(
        *FLOWER_ON,
        "--set",
        "broker.enabled=false",
        "--set",
        "worker.enabled=false",
        broker_secret=None,
    )
    assert "broker.existingSecret must be set if flower.enabled" in error


def test_flower_name_collision() -> None:
    """The app's name must not collide with Flower's."""
    name = "a" * 56 + "-flower"
    error = _render_error(*FLOWER_ON, "--set", f"fullnameOverride={name}")
    assert f"would both be named {name}" in error


def test_flower_network_policy_peers() -> None:
    """Flower admits only the peers of `flower.networkPolicy.ingressFrom`."""
    policy = _render(*FLOWER_ON)[("NetworkPolicy", FLOWER)]
    assert policy["spec"]["ingress"] == []
    peers = [{"podSelector": {"matchLabels": {"app": "proxy"}}}]
    peers_arg = f"flower.networkPolicy.ingressFrom={json.dumps(peers)}"
    policy = _render(*FLOWER_ON, "--set-json", peers_arg)[("NetworkPolicy", FLOWER)]
    assert policy["spec"]["ingress"] == [
        {"from": peers, "ports": [{"port": 5555, "protocol": "TCP"}]}
    ]


def test_notes_variants() -> None:
    """The notes describe the HTTPRoute, the Route and a disabled worker."""
    parents = [{"name": "gateway", "namespace": "gateway-system"}]
    host = ("--set", "hostName=wes.example.org")
    notes = render_notes(
        *host,
        *NO_INGRESS,
        "--set",
        "prowes.httpRoute.enabled=true",
        "--set-json",
        f"prowes.httpRoute.parentRefs={json.dumps(parents)}",
    )
    assert "The HTTPRoute serves the app" in notes
    notes = render_notes(*host, *NO_INGRESS, *OPENSHIFT)
    assert "https://wes.example.org/ga4gh/wes/v1/ui/" in notes
    assert "kubectl port-forward" not in notes
    notes = render_notes(*NO_INGRESS, "--set", "prowes.route.enabled=false")
    assert "(prowes.route.enabled is false)" in notes
    notes = render_notes(*NO_INGRESS, "--set", "worker.enabled=false")
    assert "The Celery worker is disabled" in notes


def test_external_mongodb() -> None:
    """With an external MongoDB, the overlay has its host, which is required."""
    docs = _render("--set", "mongodb.enabled=false", "--set", "config.db.host=db")
    assert ("Deployment", MONGODB) not in docs
    assert _overlay(docs)["db"] == {"host": "db"}
    env = _env(_container(docs, APP))
    assert env["MONGO_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": MONGODB_SECRET,
        "key": "password",
    }
    error = _render_error("--set", "mongodb.enabled=false")
    assert "config.db.host must be set" in error


def test_mongodb_without_auth() -> None:
    """Without authentication, no MongoDB credentials are passed."""
    docs = _render("--set", "mongodb.auth.enabled=false", mongodb_secret=None)
    for name in (APP, WORKER):
        assert "MONGO_USERNAME" not in _env(_container(docs, name))


def test_external_broker() -> None:
    """With an external broker, only its URL is passed to the app and worker."""
    docs = _render("--set", "broker.enabled=false")
    assert ("StatefulSet", BROKER) not in docs
    for name in (APP, WORKER):
        env = _env(_container(docs, name))
        assert env["CELERY_BROKER_URL"]["valueFrom"]["secretKeyRef"] == {
            "name": BROKER_SECRET,
            "key": "broker-url",
        }
    error = _render_error(broker_secret=None)
    assert "broker.existingSecret must be set" in error


def test_max_content_length() -> None:
    """The chart knows the base config's size limit of request bodies.

    The values schema (and the library) refuse limits that FOCA refuses,
    e.g., zero.
    """
    config = yaml.safe_load((ROOT / "pro_wes" / "config.yaml").read_text())
    assert config["server"]["max_content_length"] == 10485760
    helpers = (CHART / "templates" / "_helpers.tpl").read_text()
    assert "maxContentLength: 10485760" in helpers
    error = _render_error("--set", "config.server.max_content_length=0")
    assert "/config/server/max_content_length" in error


def test_storage_path_from_config() -> None:
    """The storage is mounted at the storage path that `config` sets."""
    docs = _render("--set", "config.custom.post_runs.storage_path=/runs")
    mounts = _mounts(_container(docs, APP))
    assert mounts["/runs"] == "data"
    assert "/data" not in mounts
    error = _render_error("--set", "config.custom.post_runs.storage_path=runs")
    assert "storage_path must be an absolute path" in error


def _annotations(resource: str, annotations: Mapping[str, str | None]) -> list[str]:
    """Set annotations of the Ingress or the OpenShift Route.

    Args:
        resource: Key of the resource in `prowes` (`ingress` or `route`).
        annotations: Values of the annotations, by name; `None` removes one.

    Returns:
        Arguments of `helm template`.
    """
    args = []
    for name, value in annotations.items():
        key = f"prowes.{resource}.annotations.{name.replace('.', r'\.')}"
        if value is None:
            args += ["--set-json", f"{key}=null"]
        else:
            args += ["--set-string", f"{key}={value}"]
    return args


def _body_size(value: str | None) -> list[str]:
    """Set the ingress-nginx size limit of request bodies of the Ingress.

    Args:
        value: Value of the annotation; `None` removes it.

    Returns:
        Arguments of `helm template`.
    """
    return _annotations("ingress", {BODY_SIZE: value})


def test_ingress_body_size() -> None:
    """The Ingress lets ingress-nginx allow the app's size limit, 10 MiB.

    ingress-nginx rejects bodies over 1 MiB by default, before the app could
    answer with its problem details; `null` removes the annotation.
    """
    annotations = _render()[("Ingress", APP)]["metadata"]["annotations"]
    assert annotations == {BODY_SIZE: "10m"}
    docs = _render(*_body_size(None))
    assert "annotations" not in docs[("Ingress", APP)]["metadata"]


@pytest.mark.parametrize(
    "args",
    [
        _body_size("0"),
        _body_size("10485760"),
        _body_size("10240k"),
        _body_size("1G"),
        [*LIMIT_20_MIB, *_body_size("20M")],
        ["--set", "config.server.max_content_length=1048576", *_body_size("1m")],
        # Without the annotation, the chart can't know the controller's limit
        [*LIMIT_20_MIB, *_body_size(None)],
        # The check applies to the Ingress only
        [*LIMIT_20_MIB, *NO_INGRESS],
        [*LIMIT_20_MIB, *NO_INGRESS, *OPENSHIFT],
    ],
)
def test_ingress_body_size_allowed(args: list[str]) -> None:
    """Limits of ingress-nginx at or above the app's limit, or none, render.

    Args:
        args: Arguments setting the limits.
    """
    _render(*args)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            LIMIT_20_MIB,
            f"prowes.ingress.annotations: {BODY_SIZE} (10m) must allow at least"
            " the app's config.server.max_content_length (20971520 bytes)",
        ),
        (_body_size("1m"), "set it to 10m or more"),
        (_body_size("10485759"), "(10485759) must allow at least"),
        (
            ["--set", "config.server.max_content_length=10485761"],
            "set it to 11m or more",
        ),
        (_body_size("10 MB"), "must be a size of ingress-nginx"),
        (_body_size("1.5m"), "must be a size of ingress-nginx"),
        (_body_size(""), "must be a size of ingress-nginx"),
    ],
)
def test_ingress_body_size_too_small(args: list[str], message: str) -> None:
    """A limit of ingress-nginx below the app's limit fails to render.

    Requests between the two limits would get ingress-nginx's own `413`
    page instead of the app's problem details.

    Args:
        args: Arguments setting the limits.
        message: Expected part of the error.
    """
    assert message in _render_error(*args)


def test_ingress_rate_limits() -> None:
    """Rate limits of ingress-nginx are off by default, set by annotations."""
    limits = {
        "nginx.ingress.kubernetes.io/limit-rps": "10",
        "nginx.ingress.kubernetes.io/limit-burst-multiplier": "5",
        "nginx.ingress.kubernetes.io/limit-connections": "20",
    }
    docs = _render(*_annotations("ingress", limits))
    annotations = docs[("Ingress", APP)]["metadata"]["annotations"]
    assert annotations == {BODY_SIZE: "10m", **limits}


def test_route_rate_limits() -> None:
    """Rate limits of the OpenShift router are off by default, set by annotations.

    The router has no size limit of request bodies, so the Route needs none.
    """
    docs = _render(*NO_INGRESS, *OPENSHIFT)
    assert "annotations" not in docs[("Route", APP)]["metadata"]
    limits = {
        "haproxy.router.openshift.io/rate-limit-connections": "true",
        "haproxy.router.openshift.io/rate-limit-connections.rate-http": "100",
    }
    docs = _render(*NO_INGRESS, *OPENSHIFT, *_annotations("route", limits))
    assert docs[("Route", APP)]["metadata"]["annotations"] == limits
