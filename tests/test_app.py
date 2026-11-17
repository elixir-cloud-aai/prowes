"""Tests of the app factory (`pro_wes.app`) and the configuration."""

from pathlib import Path

from connexion import App
from foca import get_config, get_custom_config
from foca.testing import Client
import pytest

from pro_wes.app import init_app
from pro_wes.config_models import CustomConfig
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

BASE_PATH = "/ga4gh/wes/v1"


def test_init_app_creates_storage(foca_app: App, storage_path: Path) -> None:
    """The app creates the storage directory of the runs."""
    assert foca_app is not None
    assert storage_path.is_dir()


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
