"""Controller for GA4GH WES API endpoints.

`GET /service-info` is served by FOCA (`foca.api.service_info`; cf.
`pro_wes.ga4gh.wes.service_info`).
"""

import logging
from typing import Any

from connexion import request
from foca.utils.logging import log_traffic

from pro_wes.exceptions import TasksNotServed
from pro_wes.ga4gh.wes.workflow_runs import WorkflowRuns

logger = logging.getLogger(__name__)


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
    """
    workflow_run = WorkflowRuns()
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
    """
    workflow_run = WorkflowRuns()
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
        pro_wes.exceptions.Forbidden: The requester is not allowed to
            access the resource.
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
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
        pro_wes.exceptions.Forbidden: The requester is not allowed to
            access the resource.
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
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
        pro_wes.exceptions.Forbidden: The requester is not allowed to
            access the resource.
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
    response = workflow_run.cancel_run(run_id=run_id, **kwargs)
    return response


@log_traffic
def ListTasks(run_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
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
def GetTask(run_id: str, task_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
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
