"""Controller for GA4GH WES API endpoints.

`GET /service-info` is served by FOCA (`foca.api.service_info`; cf.
`pro_wes.ga4gh.wes.service_info`).

The operations on runs get the owner of the caller (`user_id`, cf.
:py:func:`pro_wes.security.current_owner`), which replaces any `user_id`
of the request: callers create, list, read and cancel only their own runs.
"""

import logging
from typing import Any, NoReturn

# Flask's request too: `check_run_request_body` runs before Connexion's
# handling of the request
from connexion import request
from flask import request as flask_request
from foca import get_config
from foca.security.auth import validate_token
from foca.utils.logging import log_traffic
from werkzeug.exceptions import Unauthorized

from pro_wes.exceptions import (
    MissingBoundary,
    TasksNotServed,
    UnsupportedMediaType,
    UrlEncodedAttachment,
)
from pro_wes.ga4gh.wes.workflow_runs import WorkflowRuns
from pro_wes.security import current_owner

logger = logging.getLogger(__name__)

# Media types of run requests: those that Connexion reads as forms (cf.
# `FORM_CONTENT_TYPES` of `connexion.utils`); WES declares
# `multipart/form-data` only, but forms without attachments are accepted
# URL-encoded, too
RUN_REQUEST_MEDIA_TYPES = frozenset(
    {"multipart/form-data", "application/x-www-form-urlencoded"}
)


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


# Name of the Flask endpoint of `POST /runs` within its blueprint, as
# Connexion 2 names it: module and name of the controller, with dots
# replaced (cf. `check_run_request_body`; `test_run_workflow_endpoint`
# checks it against the routes of the app)
RUN_WORKFLOW_ENDPOINT = f"{RunWorkflow.__module__}.{RunWorkflow.__name__}".replace(
    ".", "_"
)


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


def check_run_request_body() -> None:
    """Refuse run requests whose body Connexion can't read as a form.

    Called before each request (`before_request` of the Flask app, cf.
    `pro_wes.app.init_app`), so before Connexion's validation, which fails
    with an `AttributeError` (`500`) on bodies that aren't forms. Other
    requests are left to the operations, and so are run requests without a
    valid token where one is required: Connexion refuses them with `401`
    before it reads the body, so that clients learn about the body only once
    they are authenticated.

    Raises:
        pro_wes.exceptions.UnsupportedMediaType: The run request isn't a
            form (`415`), e.g., JSON, or has no media type.
        pro_wes.exceptions.MissingBoundary: The run request is
            `multipart/form-data` without a boundary (`400`).
        pro_wes.exceptions.UrlEncodedAttachment: The run request is
            URL-encoded with workflow attachments (`400`).
    """
    endpoint = flask_request.endpoint or ""
    if flask_request.method != "POST" or endpoint.rpartition(".")[2] != (
        RUN_WORKFLOW_ENDPOINT
    ):
        return
    problem = _run_request_body_problem()
    if problem is not None and _authenticated():
        raise problem


def _run_request_body_problem() -> Exception | None:
    """Tell why Connexion can't read the body of a run request as a form.

    Returns:
        The error to raise, or `None` if the body is a form that Connexion
        reads.
    """
    if flask_request.mimetype not in RUN_REQUEST_MEDIA_TYPES:
        return UnsupportedMediaType()
    if (
        flask_request.mimetype == "multipart/form-data"
        and not flask_request.mimetype_params.get("boundary")
    ):
        return MissingBoundary()
    # URL-encoded forms have no files: attachments would be lost
    if (
        flask_request.mimetype == "application/x-www-form-urlencoded"
        and "workflow_attachment" in flask_request.form
    ):
        return UrlEncodedAttachment()
    return None


def _authenticated() -> bool:
    """Tell whether the request passes Connexion's token validation.

    Only called for run requests that are refused, so that the token of
    other requests is validated once, by Connexion.

    Returns:
        `True` if no token is required (`disable_auth` of the API
        specification) or the request has a valid bearer token (cf.
        `foca.security.auth.validate_token`), `False` otherwise.
    """
    if all(spec.disable_auth for spec in get_config().api.specs):
        return True
    parts = flask_request.headers.get("Authorization", "").split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    try:
        validate_token(parts[1].strip())
    except Unauthorized:
        return False
    return True
