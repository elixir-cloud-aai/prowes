"""Controller for GA4GH WES API endpoints.

`GET /service-info` is served by FOCA (`foca.api.service_info`; cf.
`pro_wes.ga4gh.wes.service_info`).

The operations on runs get the owner of the caller (`user_id`, cf.
:py:func:`pro_wes.security.current_owner`), which replaces any `user_id`
of the request: callers create, list, read and cancel only their own runs.
"""

import logging
from typing import Any, NoReturn

from connexion import request
from foca.utils.logging import log_traffic

from pro_wes.exceptions import TasksNotServed
from pro_wes.ga4gh.wes.workflow_runs import WorkflowRuns
from pro_wes.security import current_owner

logger = logging.getLogger(__name__)


def _set_owner(kwargs: dict[str, Any]) -> None:
    """Set the owner of the caller as `user_id` of the keyword arguments.

    Args:
        kwargs: Keyword arguments of a controller, changed in place: `user_id`
            is the owner of the caller (cf.
            :py:func:`pro_wes.security.current_owner`), replacing any value
            of the request; without one (authentication disabled), it is
            removed, so that runs have no owner.

    Raises:
        pro_wes.exceptions.Forbidden: The caller's token has no subject that
            can own runs.
    """
    kwargs.pop("user_id", None)
    owner = current_owner()
    if owner is not None:
        kwargs["user_id"] = owner


@log_traffic
def RunWorkflow(*args: Any, **kwargs: Any) -> dict[str, str]:
    """Start workflow run.

    Controller for `POST /runs`.

    Args:
        *args: Positional arguments passed along with request.
        **kwargs: Additional keyword arguments passed along with request.

    Returns:
        Response object according to WES API schema `RunId`. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Raises:
        pro_wes.exceptions.Forbidden: The caller's token has no subject
            that can own runs (cf. :py:func:`pro_wes.security.current_owner`).
    """
    workflow_run = WorkflowRuns()
    _set_owner(kwargs)
    response = workflow_run.run_workflow(
        request=request,
        **kwargs,
    )
    return response


@log_traffic
def ListRuns(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return list of workflow runs.

    Controller for `GET /runs`.

    Args:
        *args: Positional arguments passed along with request.
        **kwargs: Keyword arguments passed along with request.

    Returns:
        Response object according to WES API schema `RunListResponse`. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Raises:
        pro_wes.exceptions.Forbidden: The caller's token has no subject
            that can own runs (cf. :py:func:`pro_wes.security.current_owner`).
    """
    workflow_run = WorkflowRuns()
    _set_owner(kwargs)
    response = workflow_run.list_runs(**kwargs)
    return response


@log_traffic
def GetRunLog(run_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return detailed information about a workflow run.

    Controller for `GET /runs/{run_id}`.

    Args:
        run_id: Workflow run identifier.
        *args: Positional arguments passed along with request.
        **kwargs: Additional keyword arguments passed along with request.

    Returns:
        Response object according to WES API schema `RunLog`. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Raises:
        pro_wes.exceptions.Forbidden: The run belongs to another caller, or
            the caller's token has no subject that can own runs (cf.
            :py:func:`pro_wes.security.current_owner`).
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
    _set_owner(kwargs)
    response = workflow_run.get_run_log(run_id=run_id, **kwargs)
    return response


@log_traffic
def GetRunStatus(run_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return status information about a workflow run.

    Controller for `GET /runs/{run_id}/status`.

    Args:
        run_id: Workflow run identifier.
        *args: Positional arguments passed along with request.
        **kwargs: Additional keyword arguments passed along with request.

    Returns:
        Response object according to WES API schema `RunStatus`. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Raises:
        pro_wes.exceptions.Forbidden: The run belongs to another caller, or
            the caller's token has no subject that can own runs (cf.
            :py:func:`pro_wes.security.current_owner`).
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
    _set_owner(kwargs)
    response = workflow_run.get_run_status(run_id=run_id, **kwargs)
    return response


@log_traffic
def CancelRun(run_id: str, *args: Any, **kwargs: Any) -> dict[str, str]:
    """Cancel workflow run.

    Controller for `POST /runs/{run_id}/cancel`.

    Args:
        run_id: Workflow run identifier.
        *args: Positional arguments passed along with request.
        **kwargs: Additional keyword arguments passed along with request.

    Returns:
        Response object according to WES API schema `RunId`. Cf.
            `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

    Raises:
        pro_wes.exceptions.Forbidden: The run belongs to another caller, or
            the caller's token has no subject that can own runs (cf.
            :py:func:`pro_wes.security.current_owner`).
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
    _set_owner(kwargs)
    response = workflow_run.cancel_run(run_id=run_id, **kwargs)
    return response


@log_traffic
def ListTasks(run_id: str, *args: Any, **kwargs: Any) -> NoReturn:
    """Return the tasks of a workflow run (WES 1.1.0).

    Controller for `GET /runs/{run_id}/tasks`; not served yet (#124).

    Args:
        run_id: Workflow run identifier.
        *args: Positional arguments passed along with request.
        **kwargs: Additional keyword arguments passed along with request.

    Raises:
        pro_wes.exceptions.TasksNotServed: Always (`501 Not Implemented`).
    """
    raise TasksNotServed


@log_traffic
def GetTask(run_id: str, task_id: str, *args: Any, **kwargs: Any) -> NoReturn:
    """Return a task of a workflow run (WES 1.1.0).

    Controller for `GET /runs/{run_id}/tasks/{task_id}`; not served yet
    (#124).

    Args:
        run_id: Workflow run identifier.
        task_id: Task identifier.
        *args: Positional arguments passed along with request.
        **kwargs: Additional keyword arguments passed along with request.

    Raises:
        pro_wes.exceptions.TasksNotServed: Always (`501 Not Implemented`).
    """
    raise TasksNotServed
