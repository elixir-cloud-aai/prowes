"""Utility functions for Celery background tasks."""

import logging
from typing import Any

from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from pro_wes.utils.db import DbDocumentConnector

# Get logger instance
logger = logging.getLogger(__name__)


def set_run_state(
    collection: Collection[dict[str, Any]],
    run_id: str,
    task_id: str | None = None,
    state: str = "UNKNOWN",
) -> None:
    """Set/update state of run associated with Celery task.

    Database errors are logged, not raised.

    Args:
        collection: Collection of the runs.
        run_id: Run identifier.
        task_id: Identifier of the Celery task of the run; looked up by run
            identifier if not given.
        state: New state of the run.

    Raises:
        ValueError: No run with the identifier was found, or the state is
            unknown.
    """
    if not task_id:
        document = collection.find_one(
            filter={"run_log.run_id": run_id},
            projection={
                "task_id": True,
                "_id": False,
            },
        )
        if document is None:
            raise ValueError(f"Run '{run_id}' not found.")
        task_id = document["task_id"]
    db_connector = DbDocumentConnector(
        collection=collection,
        task_id=str(task_id),
    )
    try:
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
        return
    logger.info(
        "State of run '%s' (task id: '%s') changed to '%s'.",
        run_id,
        task_id,
        state,
    )
