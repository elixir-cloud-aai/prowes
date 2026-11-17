"""Utility functions for MongoDB document insertion, updates and retrieval."""

from collections.abc import Mapping
import logging
from typing import Any

from pymongo.collection import Collection, ReturnDocument

from pro_wes.ga4gh.wes.models import DbDocument, State

logger = logging.getLogger(__name__)

# Validation context of stored documents: their run requests aren't checked
# against the service info of this process (cf. `RunRequest`)
STORED = {"stored": True}


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
                document_unvalidated, context=STORED
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

        Args:
            state: New run status; one of `pro_wes.ga4gh.wes.models.State`.

        Raises:
            Passed
        """
        try:
            State(state)
        except Exception as exc:
            raise ValueError(f"Unknown state: {state}") from exc
        self.collection.find_one_and_update(
            {"task_id": self.task_id},
            {"$set": {"run_log.state": state}},
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
                document_unvalidated, context=STORED
            )
        except Exception as exc:
            raise ValueError(
                f"Database document does not conform to schema: {document_unvalidated}"
            ) from exc
        return document
