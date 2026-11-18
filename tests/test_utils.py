"""Tests of the database connector and of setting run states."""

from typing import Any

import mongomock
from pymongo.errors import PyMongoError
import pytest

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
