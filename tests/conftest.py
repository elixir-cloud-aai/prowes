"""Shared fixtures.

The tests create the app from `pro_wes/config.yaml` and a test overlay that
moves the storage of the runs into a temporary directory and polls without
waiting. They use FOCA's pytest plugin (`foca.testing.pytest_plugin`): an
in-memory database (mongomock) for each test, or the MongoDB at the URI of
the environment variable `MONGODB_TEST_URI`, and no network access to other
hosts. Remote WES services are replaced by fakes in the tests.
"""

from collections.abc import Callable, Iterator
import os
from pathlib import Path
from typing import Any

from connexion import App
from foca.database import register_mongodb
import mongomock
from pymongo import MongoClient
import pytest

pytest_plugins = ["foca.testing.pytest_plugin"]

# The Celery app (`pro_wes.celery_worker`) is created, with its MongoDB
# client, when its module is imported (e.g., with the controllers), so the
# client is replaced by an in-memory one before the test modules are
# imported; the apps of the tests get the client of FOCA's fixture `mongodb`
_IMPORT_CLIENT: MongoClient[dict[str, Any]] = mongomock.MongoClient()


def _import_client(*args: Any, **kwargs: Any) -> MongoClient[dict[str, Any]]:
    """Get the in-memory client used while the test modules are imported.

    Args:
        *args: Positional arguments of `MongoClient`; ignored.
        **kwargs: Keyword arguments of `MongoClient`; ignored.

    Returns:
        In-memory MongoDB client.
    """
    return _IMPORT_CLIENT


register_mongodb.MongoClient = _import_client  # type: ignore[misc, assignment]

# Identifier of the service info of the test overlay
SERVICE_INFO_ID = "org.example.prowes.test"


@pytest.fixture
def storage_path(tmp_path: Path) -> Path:
    """Get the storage directory of the runs of the test app.

    Args:
        tmp_path: Temporary directory of the test.

    Returns:
        Storage directory; not created yet.
    """
    return tmp_path / "runs"


@pytest.fixture
def app_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    storage_path: Path,
    mongodb: MongoClient[dict[str, Any]],
) -> Iterator[None]:
    """Set the environment of the test app: the test overlay and database.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
        tmp_path: Temporary directory of the test.
        storage_path: Storage directory of the runs.
        mongodb: Database of the test (FOCA's fixture).

    Yields:
        Nothing; the environment is restored afterwards.
    """
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "custom:\n"
        "  post_runs:\n"
        f"    storage_path: {storage_path}\n"
        "    polling_wait: 0\n"
        "    polling_attempts: 2\n"
        "service_info:\n"
        "  service:\n"
        f"    id: {SERVICE_INFO_ID}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FOCA_CONFIG_OVERLAY", str(overlay))
    yield


@pytest.fixture
def add_overlay(
    app_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Callable[[str], None]:
    """Get a function that merges further config over the test overlay.

    Args:
        app_env: Environment of the test app, with the test overlay.
        monkeypatch: Monkeypatch fixture of the test.
        tmp_path: Temporary directory of the test.

    Returns:
        Function taking the YAML text of a further overlay, which apps
        created afterwards merge over the test overlay.
    """
    count = 0

    def _add(text: str) -> None:
        """Merge further config over the overlays set so far.

        Args:
            text: YAML text of the overlay.
        """
        nonlocal count
        count += 1
        path = tmp_path / f"overlay-{count}.yaml"
        path.write_text(text, encoding="utf-8")
        overlays = os.environ["FOCA_CONFIG_OVERLAY"]
        monkeypatch.setenv("FOCA_CONFIG_OVERLAY", f"{overlays}:{path}")

    return _add


@pytest.fixture
def foca_app(app_env: None) -> App:
    """Get the app, created as on startup (FOCA's fixture `foca_client`).

    Args:
        app_env: Environment of the test app.

    Returns:
        The app.
    """
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    return init_app()
