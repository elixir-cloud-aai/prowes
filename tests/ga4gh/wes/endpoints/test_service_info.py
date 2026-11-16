"""Unit tests for the service info controller (`ServiceInfo`)."""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from flask import Flask
from foca.models.config import Config, MongoConfig
import mongomock
import pytest

from pro_wes.exceptions import NotFound
from pro_wes.ga4gh.wes.service_info import ServiceInfo
from pro_wes.ga4gh.wes.states import States

MONGO_CONFIG: dict[str, Any] = {
    "host": "mongodb",
    "port": 27017,
    "dbs": {
        "runStore": {
            "collections": {
                "runs": {"indexes": [{"keys": [("run_id", 1)]}]},
                "service_info": {"indexes": [{"keys": [("id", 1)]}]},
            },
        },
    },
}
SERVICE_INFO: dict[str, Any] = {
    "contactUrl": "mailto:support@example.com",
    "createdAt": "2019-06-04T12:58:19Z",
    "description": "This service provides...",
    "documentationUrl": "https://docs.myservice.example.com",
    "environment": "test",
    "id": "org.ga4gh.myservice",
    "name": "My project",
    "organization": {"name": "My organization", "url": "https://example.com"},
    "type": {"artifact": "wes", "group": "org.ga4gh", "version": "1.0.1"},
    "updatedAt": "2019-06-04T12:58:19Z",
    "version": "1.0.0",
}


@pytest.fixture
def app() -> Iterator[Flask]:
    """Get an app with mongomock collections for service info and runs.

    Yields:
        App, within its app context.
    """
    flask_app = Flask(__name__)
    flask_app.config.foca = Config(db=MongoConfig(**MONGO_CONFIG))  # type: ignore[attr-defined]
    database = mongomock.MongoClient().db
    collections = flask_app.config.foca.db.dbs["runStore"].collections  # type: ignore[attr-defined]
    collections["service_info"].client = database.service_info
    collections["runs"].client = database.runs
    with flask_app.app_context():
        yield flask_app


@pytest.mark.usefixtures("app")
def test_get_service_info_not_found() -> None:
    """Service info that was never set is not found."""
    with pytest.raises(NotFound):
        ServiceInfo().get_service_info()


@pytest.mark.usefixtures("app")
def test_set_and_get_service_info() -> None:
    """Service info that was set is returned, with zero state counts."""
    service_info = ServiceInfo()
    service_info.set_service_info(data=deepcopy(SERVICE_INFO))
    response = service_info.get_service_info()
    counts = response.pop("system_state_counts")
    assert response == SERVICE_INFO
    assert counts == dict.fromkeys(States.ALL, 0)


@pytest.mark.usefixtures("app")
def test_set_service_info_replaces() -> None:
    """Setting service info again replaces it."""
    service_info = ServiceInfo()
    service_info.set_service_info(data=deepcopy(SERVICE_INFO))
    updated = {**SERVICE_INFO, "name": "Other project"}
    service_info.set_service_info(data=deepcopy(updated))
    assert service_info.get_service_info(get_counts=False) == updated


def test_get_service_info_counts_states(app: Flask) -> None:
    """System state counts reflect the states of the stored runs."""
    collections = app.config.foca.db.dbs["runStore"].collections  # type: ignore[attr-defined]
    collections["runs"].client.insert_many(
        [
            {"run_log": {"state": "RUNNING"}},
            {"run_log": {"state": "RUNNING"}},
            {"run_log": {"state": "COMPLETE"}},
        ]
    )
    service_info = ServiceInfo()
    service_info.set_service_info(data=deepcopy(SERVICE_INFO))
    counts = service_info.get_service_info()["system_state_counts"]
    assert counts["RUNNING"] == 2
    assert counts["COMPLETE"] == 1
    assert sum(counts.values()) == 3
