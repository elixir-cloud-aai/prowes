"""Controller for GA4GH WES API endpoints.

`GET /service-info` is served by FOCA (`foca.api.service_info`; cf.
`pro_wes.ga4gh.wes.service_info`).
"""

import logging
from typing import Any

from connexion import request
from foca.utils.logging import log_traffic

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
            https://github.com/ga4gh/workflow-execution-service-schemas/blob/c5406f1d3740e21b93d3ac71a4c8d7b874011519/openapi/workflow_execution_service.swagger.yaml#L579-584
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
            https://github.com/ga4gh/workflow-execution-service-schemas/blob/c5406f1d3740e21b93d3ac71a4c8d7b874011519/openapi/workflow_execution_service.swagger.yaml#L495-L510
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
            https://github.com/ga4gh/workflow-execution-service-schemas/blob/c5406f1d3740e21b93d3ac71a4c8d7b874011519/openapi/workflow_execution_service.swagger.yaml#L511-L533

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
            https://github.com/ga4gh/workflow-execution-service-schemas/blob/c5406f1d3740e21b93d3ac71a4c8d7b874011519/openapi/workflow_execution_service.swagger.yaml#L585-L594

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
            https://github.com/ga4gh/workflow-execution-service-schemas/blob/c5406f1d3740e21b93d3ac71a4c8d7b874011519/openapi/workflow_execution_service.swagger.yaml#L579-L584

    Raises:
        pro_wes.exceptions.Forbidden: The requester is not allowed to
            access the resource.
        pro_wes.exceptions.RunNotFound: The requested workflow run is not
            available.
    """
    workflow_run = WorkflowRuns()
    response = workflow_run.cancel_run(run_id=run_id, **kwargs)
    return response
