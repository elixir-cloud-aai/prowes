"""Tests of the image's runtime settings (`Dockerfile`).

The command (Gunicorn with proWES's settings), the health check, the user
and the storage directory of the runs. The image itself is built and started
by CI (job `smoke` of `.github/workflows/ci.yml`).
"""

import importlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from connexion import App
import yaml

import pro_wes.gunicorn

ROOT = Path(__file__).parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def _instructions(name: str) -> list[str]:
    """Get the arguments of all instructions of a kind in the Dockerfile.

    Args:
        name: Instruction (e.g., `USER`).

    Returns:
        The arguments of each instruction, in order, with continued lines
        joined; comments are left out.
    """
    lines = [
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    text = "\n".join(lines).replace("\\\n", " ")
    return [
        args.strip()
        for args in re.findall(rf"^{name}\s+(.+)$", text, flags=re.MULTILINE)
    ]


def _image_command() -> list[str]:
    """Get the command of the image (its last `CMD`, in exec form).

    Returns:
        The command and its arguments.
    """
    command = json.loads(_instructions("CMD")[-1])
    assert isinstance(command, list)
    return [str(arg) for arg in command]


def _health_check_url() -> str:
    """Get the URL that the image's health check probes.

    Returns:
        The default of the environment variable `HEALTHCHECK_URL`.
    """
    (url,) = [
        args.partition("=")[2]
        for args in _instructions("ENV")
        if args.startswith("HEALTHCHECK_URL=")
    ]
    return url


def test_image_command() -> None:
    """The image serves the app with Gunicorn and proWES's settings.

    The same command as documented and used by the Helm chart, without
    further options, so that `GUNICORN_CMD_ARGS` and the environment
    variables of `pro_wes/gunicorn.py` apply.
    """
    assert _image_command() == [
        "gunicorn",
        "--config",
        "python:pro_wes.gunicorn",
        "pro_wes.wsgi:app",
    ]


def test_image_app_loads(foca_app: App) -> None:
    """Gunicorn gets a WSGI app from the image's command.

    Args:
        foca_app: The app (sets up the environment of the test app).
    """
    module, _, name = _image_command()[-1].partition(":")
    app = getattr(importlib.import_module(module), name)
    assert callable(app)


def test_health_check_port() -> None:
    """The image's health check probes the port Gunicorn listens on."""
    (bind,) = pro_wes.gunicorn.bind
    assert urlsplit(_health_check_url()).port == int(bind.rpartition(":")[2])


def test_health_check_path(foca_app: App) -> None:
    """The image's health check probes a page of the app (the Swagger UI).

    Args:
        foca_app: The app.
    """
    response = foca_app.app.test_client().get(urlsplit(_health_check_url()).path)
    assert response.status_code == 200


def test_runs_as_non_root() -> None:
    """The image runs as the non-root user of the FOCA base image."""
    assert _instructions("USER")[-1] == "1000"


def test_storage_writable_for_app_only() -> None:
    """The storage of the runs is the only directory made writable.

    It is owned by the user of the image and writable for group 0 (for
    arbitrary user IDs, e.g., on OpenShift), not for everyone; nothing else
    is made writable (e.g., the package directory, as before).
    """
    config = yaml.safe_load((ROOT / "pro_wes/config.yaml").read_text())
    storage = config["custom"]["post_runs"]["storage_path"]
    runs = _instructions("RUN")
    assert f"install -d -o 1000 -g 0 -m 2775 {storage}" in runs
    for run in runs:
        assert not re.search(r"\bchmod\b|\bchown\b", run), run
