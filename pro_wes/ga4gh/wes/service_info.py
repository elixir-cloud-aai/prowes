"""Controllers for the `/service-info route."""

import logging
from typing import Any

from bson.objectid import ObjectId
from foca import get_collection
from pymongo.collection import Collection

from pro_wes.exceptions import NotFound
from pro_wes.ga4gh.wes.states import States
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB, SERVICE_INFO_COLLECTION

logger = logging.getLogger(__name__)


class ServiceInfo:
    """Class for WES API service info server-side controller methods.

    Creates service info upon first request, if it does not exist.

    Attributes:
        db_client_service_info: Database collection storing service info objects.
        db_client_runs: Database collection storing workflow run objects.
        object_id: Database identifier for service info.
    """

    def __init__(self) -> None:
        """Class constructor."""
        self.db_client_service_info: Collection[dict[str, Any]] = get_collection(
            RUN_DB, SERVICE_INFO_COLLECTION
        )
        self.db_client_runs: Collection[dict[str, Any]] = get_collection(
            RUN_DB, RUN_COLLECTION
        )
        self.object_id: str = "000000000000000000000000"

    def get_service_info(self, get_counts: bool = True) -> dict[str, Any]:
        """Get latest service info from database.

        Args:
            get_counts: Whether system state counts should be returned.

        Returns:
            Latest service info details.

        Raises:
            NotFound: Service info was not found.
        """
        service_info = self.db_client_service_info.find_one(
            {"_id": ObjectId(self.object_id)},
            {"_id": False},
        )
        if service_info is None:
            raise NotFound
        if get_counts:
            service_info["system_state_counts"] = self._get_state_counts()
        return service_info

    def set_service_info(
        self,
        data: dict[str, Any],
    ) -> None:
        """Create or update service info.

        Arguments:
            data: Dictionary of service info values. Cf.
        """
        self.db_client_service_info.replace_one(
            filter={"_id": ObjectId(self.object_id)},
            replacement=data,
            upsert=True,
        )
        logger.info("Service info set.")

    def _get_state_counts(self) -> dict[str, int]:
        """Gets current system state counts."""
        current_counts = dict.fromkeys(States.ALL, 0)
        cursor = self.db_client_runs.find(
            filter={},
            projection={
                "run_log.state": True,
                "_id": False,
            },
        )
        for record in cursor:
            current_counts[record["run_log"]["state"]] += 1
        return current_counts
