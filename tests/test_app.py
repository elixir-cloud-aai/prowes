"""Tests of the app factory (`pro_wes.app`) and the configuration."""

from collections.abc import Callable
from pathlib import Path
import runpy
import sys
from typing import Any

from connexion import App
from foca import get_config, get_custom_config
from foca.testing import Client
import pytest

from pro_wes.app import init_app, main
from pro_wes.config_models import CustomConfig
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

BASE_PATH = "/ga4gh/wes/v1"


def test_init_app_creates_storage(foca_app: App, storage_path: Path) -> None:
    """The app creates the storage directory of the runs."""
    assert foca_app is not None
    assert storage_path.is_dir()


def test_init_app_refuses_unwritable_storage(app_env: None, storage_path: Path) -> None:
    """The app refuses to start if it can't write to the storage directory.

    E.g., a volume that the image's user can't write to: otherwise, the app
    would start and only `POST /runs` would fail.
    """
    storage_path.mkdir()
    storage_path.chmod(0o555)
    try:
        with pytest.raises(PermissionError, match="not writable"):
            init_app()
    finally:
        storage_path.chmod(0o755)


def test_config(foca_app: App) -> None:
    """The configuration has FOCA 0.22's sections and the typed custom one."""
    with foca_app.app.app_context():
        config = get_config()
        custom = get_custom_config(CustomConfig)
    assert config.server.debug is False
    assert config.exceptions.media_type == "application/json"
    assert config.jobs is not None
    assert config.jobs.host == "rabbitmq"
    assert config.jobs.port == 5672
    assert custom.post_runs.id_charset == "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def test_config_broker_from_environment(
    app_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host and port of the broker come from the environment, if set."""
    monkeypatch.setenv("RABBIT_HOST", "broker.example.org")
    monkeypatch.setenv("RABBIT_PORT", "5673")
    app = init_app()
    with app.app.app_context():
        jobs = get_config().jobs
    assert jobs is not None
    assert jobs.host == "broker.example.org"
    assert jobs.port == 5673


def test_errors_are_problem_details(foca_client: Client) -> None:
    """Errors are problem details, with the media type of the WES API."""
    response = foca_client.get(f"{BASE_PATH}/runs/UNKNOWN")
    assert response.status_code == 404
    assert response.headers["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not found"
    assert body["detail"] == "The requested run wasn't found."


def test_db_names_configured(foca_app: App) -> None:
    """The names of `pro_wes.utils.db_names` are those of the configuration."""
    with foca_app.app.app_context():
        db = get_config().db
    assert db is not None
    assert db.dbs is not None
    collections = db.dbs[RUN_DB].collections
    assert collections is not None
    assert RUN_COLLECTION in collections


@pytest.fixture
def served_apps(monkeypatch: pytest.MonkeyPatch) -> list[App]:
    """Record the apps that would be served, instead of serving them.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Apps whose development server (`App.run()`) was started, in order.
    """
    runs: list[App] = []

    def _run(self: App, **kwargs: Any) -> None:
        """Record the app instead of serving it.

        Args:
            self: The app.
            **kwargs: Arguments of `App.run()`; none expected, as the
                host, port and debug mode come from the config.
        """
        assert kwargs == {}
        runs.append(self)

    monkeypatch.setattr(App, "run", _run)
    return runs


@pytest.mark.parametrize("debug", [False, True])
def test_main_runs_development_server(
    add_overlay: Callable[[str], None],
    served_apps: list[App],
    storage_path: Path,
    debug: bool,
) -> None:
    """`main()` runs the app on the host and port of the config (`server`)."""
    add_overlay(f"server:\n  host: 127.0.0.1\n  port: 8093\n  debug: {debug}\n")
    main()
    assert len(served_apps) == 1
    app = served_apps[0]
    assert (app.host, app.port, app.debug) == ("127.0.0.1", 8093, debug)
    assert storage_path.is_dir()


def test_module_runs_main(
    app_env: None, monkeypatch: pytest.MonkeyPatch, served_apps: list[App]
) -> None:
    """`python -m pro_wes.app` runs the app with Flask's development server.

    By default on the loopback interface only.
    """
    # Run as by `-m`, i.e., without the module imported by the tests
    monkeypatch.delitem(sys.modules, "pro_wes.app")
    runpy.run_module("pro_wes.app", run_name="__main__")
    assert len(served_apps) == 1
    assert (served_apps[0].host, served_apps[0].port) == ("127.0.0.1", 8080)
