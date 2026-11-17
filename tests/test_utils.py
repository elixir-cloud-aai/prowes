"""Tests of the database connector and the WES client's run logs."""

from typing import Any

import mongomock
from pymongo.errors import PyMongoError
import pytest
import requests

from pro_wes.exceptions import EngineUnavailable
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.tasks.utils import set_run_state
from pro_wes.utils.db import DbDocumentConnector

TASK_ID = "task-1"


@pytest.fixture
def collection() -> Any:
    """Get an in-memory collection of runs with one run.

    Returns:
        Collection.
    """
    runs: Any = mongomock.MongoClient().db.runs
    runs.insert_one({"task_id": TASK_ID, "run_log": {"run_id": "RUN001"}})
    return runs


def test_get_document(collection: Any) -> None:
    """Stored documents are returned as models."""
    connector = DbDocumentConnector(collection=collection, task_id=TASK_ID)
    assert connector.get_document().run_log.run_id == "RUN001"


def test_get_document_unsupported_type(collection: Any) -> None:
    """Stored runs of workflow types no longer configured are returned.

    Args:
        collection: Collection of runs.
    """
    collection.update_one(
        {"task_id": TASK_ID},
        {
            "$set": {
                "run_log.request": {
                    "workflow_params": "{}",
                    "workflow_type": "WDL",
                    "workflow_type_version": "1.0",
                    "workflow_url": "https://example.org/workflow.wdl",
                }
            }
        },
    )
    connector = DbDocumentConnector(collection=collection, task_id=TASK_ID)
    assert connector.get_document().run_log.run_id == "RUN001"
    document = connector.upsert_fields_in_root_object(root="run_log", state="RUNNING")
    assert document.run_log.state == "RUNNING"


def test_get_document_missing(collection: Any) -> None:
    """Missing documents don't conform to the schema."""
    connector = DbDocumentConnector(collection=collection, task_id="other")
    with pytest.raises(ValueError, match="does not conform"):
        connector.get_document()
    with pytest.raises(ValueError, match="does not conform"):
        connector.upsert_fields_in_root_object(root="run_log", state="RUNNING")


def test_update_run_state_unknown(collection: Any) -> None:
    """Unknown states are refused."""
    connector = DbDocumentConnector(collection=collection, task_id=TASK_ID)
    with pytest.raises(ValueError, match="Unknown state"):
        connector.update_run_state(state="OTHER")


def test_set_run_state_database_error(
    collection: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Database errors when setting states are logged, not raised."""

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        """Fail as the database.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Raises:
            PyMongoError: Always.
        """
        raise PyMongoError("down")

    monkeypatch.setattr(DbDocumentConnector, "update_run_state", _fail)
    set_run_state(collection=collection, run_id="RUN001", task_id=TASK_ID)
    assert "Could not update state" in caplog.text


class _Response:
    """Response of the fake session.

    Args:
        body: JSON body, or an exception to raise when it is read.
    """

    def __init__(self, body: Any) -> None:
        """Create the response."""
        self.body = body

    def json(self) -> Any:
        """Get the JSON body.

        Returns:
            JSON body.

        Raises:
            Exception: The body is an exception.
        """
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_get_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run logs are returned as sent."""
    client = WesClient(host="https://wes.example.org")
    log = {"run_id": "R1", "state": "WEIRD", "extra": 1}
    monkeypatch.setattr(client.session, "get", lambda *_a, **_k: _Response(log))
    assert client.get_run(run_id="R1") == log


@pytest.mark.parametrize(
    "body", [["not", "an", "object"], ValueError("no JSON"), requests.ConnectionError()]
)
def test_get_run_unavailable(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:
    """Run logs that aren't JSON objects mean the WES is unavailable."""
    client = WesClient(host="https://wes.example.org")
    monkeypatch.setattr(client.session, "get", lambda *_a, **_k: _Response(body))
    with pytest.raises(EngineUnavailable):
        client.get_run(run_id="R1")
