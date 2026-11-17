"""WES fields of the service info (`GET /service-info`).

The service info is served by FOCA (`foca.api.service_info`) from config
section `service_info`: the fields of the GA4GH service info and the WES
fields that are configured (e.g., `workflow_type_versions`) from
`service_info.service`, the system state counts from :py:func:`runtime_fields`,
which FOCA calls for each request.
"""

import logging
from typing import Any

from foca import get_collection, get_config

from pro_wes.ga4gh.wes.states import States
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

logger = logging.getLogger(__name__)


def runtime_fields() -> dict[str, Any]:
    """Get the fields of the service info that are computed at runtime.

    Called by FOCA (`service_info.runtime_fields` in the configuration) in an
    app context, on startup and for each request.

    Returns:
        System state counts (`system_state_counts`).
    """
    return {"system_state_counts": state_counts()}


def state_counts() -> dict[str, int]:
    """Count the stored runs by state.

    Must be called in an app context.

    Returns:
        Number of runs in each state that proWES knows; runs in other states
        are left out (with a warning).
    """
    counts = dict.fromkeys(States.ALL, 0)
    cursor = get_collection(RUN_DB, RUN_COLLECTION).find(
        filter={},
        projection={"run_log.state": True, "_id": False},
    )
    for record in cursor:
        state = record.get("run_log", {}).get("state")
        if state in counts:
            counts[state] += 1
        else:
            logger.warning("Run with unknown state not counted: %s", state)
    return counts


def workflow_type_versions() -> dict[str, Any]:
    """Get the workflow types and versions of the service info.

    Must be called in an app context.

    Returns:
        Workflow types and their versions, as configured
        (`service_info.service.workflow_type_versions`); empty if not set.
    """
    conf = get_config().service_info
    if conf is None:
        return {}
    versions: dict[str, Any] = conf.service.get("workflow_type_versions") or {}
    return versions
