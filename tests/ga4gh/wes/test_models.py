"""Tests of the models of the WES API (`pro_wes.ga4gh.wes.models`)."""

from collections.abc import Iterator
from typing import Any

from connexion import App
from pydantic import ValidationError
import pytest

from pro_wes.config_models import Defaults, ListRuns, PostRuns, Wes
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
    RunStatus,
    RunSummary,
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
def test_run_request_workflow_engine() -> None:
    """The workflow engine (version) is optional; empty fields choose none."""
    request = RunRequest(**RUN_REQUEST)
    assert (request.workflow_engine, request.workflow_engine_version) == (None, None)
    request = RunRequest(**RUN_REQUEST, workflow_engine="", workflow_engine_version="")
    assert (request.workflow_engine, request.workflow_engine_version) == (None, None)
    request = RunRequest(
        **RUN_REQUEST, workflow_engine="cwltool", workflow_engine_version="3.1"
    )
    assert (request.workflow_engine, request.workflow_engine_version) == (
        "cwltool",
        "3.1",
    )
    assert "workflow_engine" not in RunRequest(**RUN_REQUEST).model_dump(
        exclude_none=True
    )


@pytest.mark.usefixtures("app_context")
@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_engine_version": "3.1"},
        {"workflow_engine": "", "workflow_engine_version": "3.1"},
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


def test_run_list_response_keeps_summaries() -> None:
    """Run lists keep `RunSummary` items, and accept `RunStatus` items.

    WES 1.1.0 allows both (`anyOf`); WES 1.0.1 instances list `RunStatus`.
    """
    response = RunListResponse.model_validate(
        {
            "runs": [
                {
                    "run_id": "DONE01",
                    "state": "COMPLETE",
                    "start_time": "2026-11-19T10:00:00Z",
                    "end_time": "2026-11-19T11:00:00Z",
                    "tags": {"project": "demo"},
                },
                {"run_id": "OLD001", "state": "RUNNING"},
            ],
            "next_page_token": "",
        }
    )
    assert response.runs is not None
    summary, status = response.runs
    assert isinstance(summary, RunSummary)
    assert summary.tags == {"project": "demo"}
    assert summary.start_time == "2026-11-19T10:00:00Z"
    assert summary.end_time == "2026-11-19T11:00:00Z"
    assert type(status) is RunStatus


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


@pytest.mark.parametrize("default_page_size", [-1, 0, 101])
def test_default_page_size_invalid(default_page_size: int) -> None:
    """Default page sizes below 1 or above the maximum stop the app on startup.

    `GET /runs` would refuse requests without `page_size` with `400`; `0`
    no longer lists all runs.

    Args:
        default_page_size: Invalid default page size (maximum: 100).
    """
    with pytest.raises(ValidationError):
        ListRuns(default_page_size=default_page_size)


def test_page_sizes_default() -> None:
    """By default, pages have 5 runs, and at most 100."""
    assert ListRuns().default_page_size == 5
    assert ListRuns().max_page_size == 100
    assert ListRuns(default_page_size=100).default_page_size == 100
    assert ListRuns(default_page_size=1, max_page_size=1).max_page_size == 1
    assert ListRuns(default_page_size=3, max_page_size=3).max_page_size == 3


def test_max_page_size_below_default() -> None:
    """A maximum page size below the default of 5 needs a default too."""
    with pytest.raises(ValidationError):
        ListRuns(max_page_size=3)


@pytest.mark.parametrize("max_page_size", [-1, 0, 2**62 + 1])
def test_max_page_size_invalid(max_page_size: int) -> None:
    """Maximum page sizes below 1 or beyond what MongoDB takes stop the app.

    Args:
        max_page_size: Invalid maximum page size.
    """
    with pytest.raises(ValidationError):
        ListRuns(default_page_size=1, max_page_size=max_page_size)


@pytest.mark.parametrize("max_tags_size", [-1, 0, 1])
def test_max_tags_size_invalid(max_tags_size: int) -> None:
    """Maximum sizes of tags below 2 (the size of `{}`) stop the app on startup.

    Args:
        max_tags_size: Invalid maximum size of tags.
    """
    with pytest.raises(ValidationError):
        PostRuns(max_tags_size=max_tags_size)
    assert PostRuns().max_tags_size == 16384


@pytest.mark.parametrize(
    "timeout", [None, 0, -1, "never", float("inf"), float("nan"), 1e10, 86401]
)
def test_timeouts_required(timeout: Any) -> None:
    """Timeouts of requests to WES instances: positive, finite, at most a day.

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
