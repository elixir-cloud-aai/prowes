"""Controller for GA4GH WES API endpoints.

`GET /service-info` is served by FOCA (`foca.api.service_info`; cf.
`pro_wes.ga4gh.wes.service_info`).
"""

import logging
from typing import Any, NoReturn

from connexion import request
from flask import request as flask_request
from foca.utils.logging import log_traffic

from pro_wes.exceptions import MissingBoundary, TasksNotServed, UnsupportedMediaType
from pro_wes.ga4gh.wes.workflow_runs import WorkflowRuns

logger = logging.getLogger(__name__)

# Media types of run requests: those that Connexion reads as forms (cf.
# `FORM_CONTENT_TYPES` of `connexion.utils`); WES declares
# `multipart/form-data` only, but forms without attachments are accepted
# URL-encoded, too
RUN_REQUEST_MEDIA_TYPES = frozenset(
    {"multipart/form-data", "application/x-www-form-urlencoded"}
)


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


def check_run_request_body() -> None:
    """Refuse run requests whose body Connexion can't read as a form.

    Called before each request (`before_request` of the Flask app, cf.
    `pro_wes.app.init_app`), so before Connexion's validation, which fails
    with an `AttributeError` (`500`) on bodies that aren't forms. Other
    requests are left to the operations.

    Raises:
        pro_wes.exceptions.UnsupportedMediaType: The run request isn't a
            form (`415`), e.g., JSON, or has no media type.
        pro_wes.exceptions.MissingBoundary: The run request is
            `multipart/form-data` without a boundary (`400`).
    """
    endpoint = flask_request.endpoint or ""
    if flask_request.method != "POST" or endpoint.rpartition(".")[2] != (
        _RUN_WORKFLOW_ENDPOINT
    ):
        return
    if flask_request.mimetype not in RUN_REQUEST_MEDIA_TYPES:
        raise UnsupportedMediaType
    if (
        flask_request.mimetype == "multipart/form-data"
        and not flask_request.mimetype_params.get("boundary")
    ):
        raise MissingBoundary


# Name of the Flask endpoint of `POST /runs` within its blueprint, as
# Connexion names it: module and name of the controller, dots replaced
_RUN_WORKFLOW_ENDPOINT = f"{RunWorkflow.__module__}.{RunWorkflow.__name__}".replace(
    ".", "_"
)
