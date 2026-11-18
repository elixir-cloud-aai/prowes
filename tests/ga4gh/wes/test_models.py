"""Tests of the models of the WES API (`pro_wes.ga4gh.wes.models`)."""

from collections.abc import Iterator
from typing import Any

from connexion import App
from pydantic import ValidationError
import pytest

from pro_wes.config_models import Defaults, PostRuns, Wes
from pro_wes.exceptions import NoSuitableEngine
from pro_wes.ga4gh.service_info.models import Service
from pro_wes.ga4gh.wes import models
from pro_wes.ga4gh.wes.models import (
    DbDocument,
    DefaultWorkflowEngineParameter,
    ErrorResponse,
    RunListResponse,
    RunLog,
    RunRequest,
    State,
    WorkflowEngineVersion,
)

RUN_REQUEST: dict[str, Any] = {
    "workflow_params": '{"input": "value"}',
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}


@pytest.fixture
def app_context(foca_app: App) -> Iterator[None]:
    """Enter the app context of the app, which has the service info stored.

    Args:
        foca_app: The app.

    Yields:
        Nothing; within the app context.
    """
    with foca_app.app.app_context():
        yield


@pytest.mark.usefixtures("app_context")
def test_run_request_optional_fields_default_to_empty_object() -> None:
    """Missing, empty and `null` optional fields become `{}`."""
    assert RunRequest(**RUN_REQUEST).tags == "{}"
    request = RunRequest(**RUN_REQUEST, tags="", workflow_engine_parameters="null")
    assert request.tags == "{}"
    assert request.workflow_engine_parameters == "{}"
    assert RunRequest(**RUN_REQUEST, tags=None).tags == "{}"


@pytest.mark.usefixtures("app_context")
@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_params": ""},
        {"workflow_params": "null"},
        {"workflow_params": "{"},
        {"workflow_params": "[1]"},
        {"tags": "not json"},
        {"workflow_url": ""},
        {"workflow_type_version": ""},
    ],
)
def test_run_request_invalid(changes: dict[str, str]) -> None:
    """Invalid fields of run requests are refused."""
    with pytest.raises(ValidationError):
        RunRequest(**{**RUN_REQUEST, **changes})


@pytest.mark.usefixtures("app_context")
@pytest.mark.parametrize(
    "changes",
    [{"workflow_type": "WDL"}, {"workflow_type_version": "v2.0"}],
)
def test_run_request_unsupported(changes: dict[str, str]) -> None:
    """Workflow types and versions that the service info lacks are refused."""
    with pytest.raises(NoSuitableEngine):
        RunRequest(**{**RUN_REQUEST, **changes})


@pytest.mark.usefixtures("app_context")
def test_stored_run_request_not_checked_against_service_info() -> None:
    """Stored runs load whatever the service info of this process supports.

    The worker reads runs that the app accepted, possibly with another
    configuration, or before workflow types were removed from it.
    """
    stored = {"run_log": {"request": {**RUN_REQUEST, "workflow_type": "WDL"}}}
    document = DbDocument.model_validate(stored, context=models.STORED_CONTEXT)
    assert document.run_log.request is not None
    assert document.run_log.request.workflow_type == "WDL"
    with pytest.raises(NoSuitableEngine):
        DbDocument.model_validate(stored)


def test_run_request_type_without_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow types configured without versions support no version."""
    monkeypatch.setattr(models, "workflow_type_versions", lambda: {"CWL": {}})
    with pytest.raises(NoSuitableEngine):
        RunRequest(**RUN_REQUEST)


def test_run_log_state_values() -> None:
    """States are stored as their values, also by default."""
    assert RunLog().model_dump()["state"] == "UNKNOWN"
    assert RunLog(state=State.RUNNING).model_dump()["state"] == "RUNNING"
    assert DbDocument().model_dump()["run_log"]["state"] == "UNKNOWN"


def test_optional_fields_default_to_none() -> None:
    """Optional fields without value are `None`, as with pydantic 1."""
    assert ErrorResponse(status_code=500).msg is None
    assert RunListResponse().next_page_token is None
    assert DefaultWorkflowEngineParameter().name is None
    service = Service.model_validate(
        {
            "id": "org.example.prowes",
            "name": "proWES",
            "type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.0.1"},
            "organization": {"name": "Example", "url": "https://example.org"},
            "version": "1.0.0",
        }
    )
    assert service.contactUrl is None
    assert service.model_dump(mode="json")["organization"]["url"] == (
        "https://example.org/"
    )


@pytest.mark.parametrize("charset", ["", "string.ascii_uppercase + string.digits"])
def test_id_charset_invalid(charset: str) -> None:
    """Run identifier characters that FOCA can't use stop the app on startup."""
    with pytest.raises(ValidationError):
        PostRuns(id_charset=charset)


@pytest.mark.parametrize("timeout", [None, 0, -1, "never"])
def test_timeouts_required(timeout: Any) -> None:
    """Timeouts of requests to WES instances must be positive numbers (#174).

    Before, `null` disabled them; FOCA's guarded client needs a deadline.

    Args:
        timeout: Invalid timeout.
    """
    with pytest.raises(ValidationError):
        PostRuns(timeout_post=timeout)
    with pytest.raises(ValidationError):
        Defaults(timeout=timeout)


def test_timeouts() -> None:
    """Timeouts may have fractions of seconds; the job timeout may be `null`."""
    assert PostRuns(timeout_post=2.5, timeout_job=None).timeout_post == 2.5
    assert Defaults(timeout=0.5).timeout == 0.5


@pytest.mark.parametrize("size", [None, 0, -1])
def test_max_response_size_invalid(size: Any) -> None:
    """Responses of WES instances are always limited, to a positive size.

    Args:
        size: Invalid size.
    """
    with pytest.raises(ValidationError):
        Wes(max_response_size=size)


def test_state_properties() -> None:
    """States tell whether runs are finished or can be canceled."""
    assert State.COMPLETE.is_finished
    assert not State.RUNNING.is_finished
    assert State.RUNNING.is_cancelable
    assert not State.COMPLETE.is_cancelable
    assert "RUNNING" in State
    assert "OTHER" not in State


def test_state_preempted() -> None:
    """`PREEMPTED` (WES 1.1.0) is a finished state that can't be canceled."""
    assert State("PREEMPTED") is State.PREEMPTED
    assert State.PREEMPTED.is_finished
    assert not State.PREEMPTED.is_cancelable


# Final states, as `docs/api.md` (section "Run states") lists them
FINAL_STATES = {"COMPLETE", "EXECUTOR_ERROR", "SYSTEM_ERROR", "CANCELED", "PREEMPTED"}


@pytest.mark.parametrize("state", list(State), ids=lambda state: state.value)
def test_state_final(state: State) -> None:
    """The final states are those of the docs; final states can't be canceled.

    Args:
        state: Run state.
    """
    assert state.is_finished == (state.value in FINAL_STATES)
    if state.is_finished:
        assert not state.is_cancelable


def test_workflow_engine_version_not_null() -> None:
    """Engine versions are arrays (WES 1.1.0), not `null`; default empty."""
    with pytest.raises(ValidationError):
        WorkflowEngineVersion(workflow_engine_version=None)  # type: ignore[arg-type]
    assert WorkflowEngineVersion().workflow_engine_version == []
