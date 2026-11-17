"""Tests of the models of the WES API (`pro_wes.ga4gh.wes.models`)."""

from collections.abc import Iterator
from typing import Any

from connexion import App
from pydantic import ValidationError
import pytest

from pro_wes.exceptions import NoSuitableEngine
from pro_wes.ga4gh.service_info.models import Service
from pro_wes.ga4gh.wes.models import (
    DbDocument,
    DefaultWorkflowEngineParameter,
    ErrorResponse,
    RunListResponse,
    RunLog,
    RunRequest,
    State,
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


def test_state_properties() -> None:
    """States tell whether runs are finished or can be canceled."""
    assert State.COMPLETE.is_finished
    assert not State.RUNNING.is_finished
    assert State.RUNNING.is_cancelable
    assert not State.COMPLETE.is_cancelable
    assert "RUNNING" in State
    assert "OTHER" not in State
