"""Tests of the app factory (`pro_wes.app`) and the configuration."""

from pathlib import Path
from typing import Any

from connexion import App
from foca import get_collection, get_config, get_custom_config
from foca.testing import Client
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import pytest

from pro_wes.app import init_app
from pro_wes.config_models import CustomConfig
from pro_wes.ga4gh.wes.service_info import ServiceInfo
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB, SERVICE_INFO_COLLECTION
from tests.conftest import SERVICE_INFO_ID

BASE_PATH = "/ga4gh/wes/v1"


def test_init_app_creates_storage(foca_app: App, storage_path: Path) -> None:
    """The app creates the storage directory of the runs."""
    assert foca_app is not None
    assert storage_path.is_dir()


def test_init_app_stores_service_info(
    foca_app: App, mongodb: MongoClient[dict[str, Any]]
) -> None:
    """The service info of the configuration is stored as JSON values."""
    # in the database of the test
    assert mongodb[RUN_DB][SERVICE_INFO_COLLECTION].count_documents({}) == 1
    with foca_app.app.app_context():
        stored = get_collection(RUN_DB, SERVICE_INFO_COLLECTION).find_one(
            {}, {"_id": False}
        )
    assert stored is not None
    assert stored["id"] == SERVICE_INFO_ID
    # URLs and timestamps as strings, not as objects of pydantic
    assert stored["organization"]["url"] == "https://example.org/"
    assert stored["contactUrl"] == "support@example.org"
    assert stored["createdAt"] == "2020-01-01T00:00:00Z"


def test_init_app_keeps_stored_service_info(app_env: None) -> None:
    """A service info in the database is kept on startup."""
    app = init_app()
    with app.app.app_context():
        collection = get_collection(RUN_DB, SERVICE_INFO_COLLECTION)
        collection.update_one({}, {"$set": {"name": "Changed"}})
    app = init_app()
    with app.app.app_context():
        collection = get_collection(RUN_DB, SERVICE_INFO_COLLECTION)
        stored = collection.find_one({}, {"_id": False})
    assert stored is not None
    assert stored["name"] == "Changed"


def test_init_app_service_info_stored_concurrently(
    app_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup goes on if another worker stored the service info first."""

    def _conflict(*_args: Any, **_kwargs: Any) -> None:
        """Fail as if another worker stored the service info first.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Raises:
            DuplicateKeyError: Always.
        """
        raise DuplicateKeyError("E11000 duplicate key error")

    monkeypatch.setattr(ServiceInfo, "set_service_info", _conflict)
    assert init_app() is not None


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


def test_get_service_info(foca_client: Client) -> None:
    """`GET /service-info` returns the stored service info with state counts."""
    response = foca_client.get(f"{BASE_PATH}/service-info")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["id"] == SERVICE_INFO_ID
    assert body["system_state_counts"]["RUNNING"] == 0


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
    assert {RUN_COLLECTION, SERVICE_INFO_COLLECTION} <= set(collections)
