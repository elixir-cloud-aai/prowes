"""Utility functions for MongoDB document insertion, updates and retrieval."""

from collections.abc import Mapping
import logging
from typing import Any

from pymongo.collection import Collection, ReturnDocument

from pro_wes.ga4gh.wes.models import STORED_CONTEXT, DbDocument, State

logger = logging.getLogger(__name__)


class DbDocumentConnector:
    """MongoDB connector to a given `pro_wes.ga4gh.wes.models.DbDocument` document.

    Args:
        collection: Database collection.
        task_id: Celery task identifier.
    """

    def __init__(
        self,
        collection: Collection[dict[str, Any]],
        task_id: str,
    ) -> None:
        """Class constructor."""
        self.collection: Collection[dict[str, Any]] = collection
        self.task_id: str = task_id

    def get_document(
        self,
        projection: Mapping[str, Any] | None = None,
    ) -> DbDocument:
        """Get document associated with task.

        Args:
            projection: A projection object indicating which fields of the
                document to return. By default, all fields except the MongoDB
                identifier `_id` are returned.

        Returns:
            Instance of `pro_wes.ga4gh.wes.models.DbDocument` associated with
                the task.

        Raise:
            ValueError: Returned document does not conform to schema.
        """
        if projection is None:
            projection = {"_id": False}

        document_unvalidated = self.collection.find_one(
            filter={"task_id": self.task_id},
            projection=projection,
        )
        try:
            document: DbDocument = DbDocument.model_validate(
                document_unvalidated, context=STORED_CONTEXT
            )
        except Exception as exc:
            raise ValueError(
                f"Database document does not conform to schema: {document_unvalidated}"
            ) from exc
        return document

    def update_run_state(
        self,
        state: str = "UNKNOWN",
    ) -> None:
        """Update run status.

        For states of the run as known to proWES (e.g., reported by the WES
        instance); clears the mark of `SYSTEM_ERROR`s set by proWES itself
        (cf. `set_system_error()`).

        Args:
            state: New run status; one of `pro_wes.ga4gh.wes.models.State`.

        Raises:
            ValueError: The state is not a known run state.
        """
        try:
            State(state)
        except Exception as exc:
            raise ValueError(f"Unknown state: {state}") from exc
        self._set_state(state=state, system_error_by_prowes=False)

    def set_system_error(self) -> None:
        """Set the run status to `SYSTEM_ERROR`, as set by proWES itself.

        For runs that fail in proWES (e.g., the run request can't be sent) or
        that proWES loses track of, without knowing their state at the WES
        instance; such runs can still be canceled if they have a remote run.
        """
        self._set_state(state=State.SYSTEM_ERROR.value, system_error_by_prowes=True)

    def _set_state(self, *, state: str, system_error_by_prowes: bool) -> None:
        """Store the run status, and whether proWES set it to `SYSTEM_ERROR`.

        Args:
            state: New run status; one of `pro_wes.ga4gh.wes.models.State`.
            system_error_by_prowes: Whether proWES itself set the state to
                `SYSTEM_ERROR`.
        """
        self.collection.find_one_and_update(
            {"task_id": self.task_id},
            {
                "$set": {
                    "run_log.state": state,
                    "system_error_by_prowes": system_error_by_prowes,
                }
            },
        )
        logger.info("[%s] %s", self.task_id, state)

    def upsert_fields_in_root_object(
        self,
        root: str,
        **kwargs: Any,
    ) -> DbDocument:
        """Insert or update fields in the same root object.

        Args:
            root: Root object of the fields.
            **kwargs: Fields to insert or update, with their values.

        Returns:
            Updated document.
        """
        document_unvalidated = self.collection.find_one_and_update(
            {"task_id": self.task_id},
            {"$set": {".".join([root, key]): value for (key, value) in kwargs.items()}},
            return_document=ReturnDocument.AFTER,
        )
        try:
            document: DbDocument = DbDocument.model_validate(
                document_unvalidated, context=STORED_CONTEXT
            )
        except Exception as exc:
            raise ValueError(
                f"Database document does not conform to schema: {document_unvalidated}"
            ) from exc
        return document
