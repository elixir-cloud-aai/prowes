"""Readers of the app configuration.

Kept apart from the models (`pro_wes.ga4gh.wes.models`) and the service info
(`pro_wes.ga4gh.wes.service_info`), so that both can use them without
importing each other.
"""

from typing import Any

from foca import get_config


def workflow_type_versions() -> dict[str, Any]:
    """Get the workflow types and versions of the service info.

    Must be called in an app context.

    Returns:
        Workflow types and their versions, as configured
        (`service_info.service.workflow_type_versions`); empty if not set.

    Raises:
        RuntimeError: No service info is configured (the app doesn't start
            without one, as FOCA serves `GET /service-info` from it).
    """
    conf = get_config().service_info
    if conf is None:
        raise RuntimeError("No service info configured (section `service_info`)")
    versions: dict[str, Any] = conf.service.get("workflow_type_versions") or {}
    return versions
