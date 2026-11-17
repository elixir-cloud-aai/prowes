"""Utility functions for Celery background tasks."""

import logging

from pymongo import collection as Collection
from pymongo.errors import PyMongoError

from pro_wes.utils.db import DbDocumentConnector

# Get logger instance
logger = logging.getLogger(__name__)


def set_run_state(
    collection: Collection,
    run_id: str,
    task_id: str | None = None,
    state: str = "UNKNOWN",
) -> None:
    """Set/update state of run associated with Celery task."""
    if not task_id:
        document = collection.find_one(
            filter={"run_id": run_id},
            projection={
                "task_id": True,
                "_id": False,
            },
        )
        _task_id = document["task_id"]
    else:
        _task_id = task_id
    try:
        db_connector = DbDocumentConnector(
            collection=collection,
            task_id=_task_id,
        )
        db_connector.update_run_state(state=state)
    except PyMongoError as exc:
        logger.exception(
            "Database error. Could not update state of run '%s' (task id:"
            " '%s') to state '%s'. Original error message: %s: %s",
            run_id,
            task_id,
            state,
            type(exc).__name__,
            exc,
        )
    finally:
        if document:
            logger.info(
                "State of run '%s' (task id: '%s') changed to '%s'.",
                run_id,
                task_id,
                state,
            )
