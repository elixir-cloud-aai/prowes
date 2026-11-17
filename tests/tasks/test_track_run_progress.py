"""Tests of the task tracking workflow runs (`task__track_run_progress`)."""

from collections.abc import Iterator
from typing import Any

from connexion import App
from foca import get_collection
import pytest

from pro_wes.exceptions import EngineProblem, EngineUnavailable
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import ErrorResponse, RunStatus, State
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.tasks.utils import set_run_state
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

TASK_ID = "task-1"
RUN_ID = "RUN001"
REMOTE_RUN_ID = "REMOTE1"
TASK_KWARGS: dict[str, Any] = {
    "remote_host": "https://wes.example.org",
    "remote_base_path": "/ga4gh/wes/v1",
    "remote_run_id": REMOTE_RUN_ID,
    "jwt": None,
}


@pytest.fixture
def run(foca_app: App) -> Iterator[None]:
    """Store a run that was sent to the remote WES, within the app context.

    Args:
        foca_app: The app.

    Yields:
        Nothing; within the app context.
    """
    with foca_app.app.app_context():
        get_collection(RUN_DB, RUN_COLLECTION).insert_one(
            {
                "task_id": TASK_ID,
                "run_log": {"run_id": RUN_ID, "state": "UNKNOWN"},
                "wes_endpoint": {
                    "host": TASK_KWARGS["remote_host"],
                    "base_path": TASK_KWARGS["remote_base_path"],
                    "run_id": REMOTE_RUN_ID,
                },
            }
        )
        yield


def _track() -> str:
    """Run the task in the current app context, as the worker would.

    Returns:
        Task identifier.
    """
    task__track_run_progress.push_request(id=TASK_ID)
    try:
        return task__track_run_progress.run(**TASK_KWARGS)
    finally:
        task__track_run_progress.pop_request()


def _stored() -> dict[str, Any]:
    """Get the stored run.

    Returns:
        Run document.
    """
    document = get_collection(RUN_DB, RUN_COLLECTION).find_one(
        {"task_id": TASK_ID}, {"_id": False}
    )
    assert document is not None
    return document


def _fake_remote(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[RunStatus | ErrorResponse | Exception],
) -> list[str]:
    """Fake the remote WES.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
        statuses: Responses to status requests, in order; exceptions are
            raised.

    Returns:
        Run identifiers of the requests for run logs, in order.
    """
    logs: list[str] = []

    def _get_run(self: WesClient, run_id: str, **kwargs: Any) -> dict[str, Any]:
        """Return the run log, with its run request.

        Args:
            self: WES client.
            run_id: Run identifier.
            **kwargs: Further arguments; ignored.

        Returns:
            Run log.
        """
        logs.append(run_id)
        return {"outputs": {"out": len(logs)}, "request": {"workflow_url": "x"}}

    def _get_run_status(
        self: WesClient, run_id: str, **kwargs: Any
    ) -> RunStatus | ErrorResponse:
        """Return the next status.

        Args:
            self: WES client.
            run_id: Run identifier.
            **kwargs: Further arguments; ignored.

        Returns:
            Next status.

        Raises:
            Exception: The next status is an exception.
        """
        status = statuses.pop(0)
        if isinstance(status, Exception):
            raise status
        return status

    monkeypatch.setattr(WesClient, "get_run", _get_run)
    monkeypatch.setattr(WesClient, "get_run_status", _get_run_status)
    return logs


@pytest.mark.usefixtures("run")
def test_track_run_until_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs are polled until they finish; states and logs are stored."""
    logs = _fake_remote(
        monkeypatch,
        [
            RunStatus(run_id=REMOTE_RUN_ID, state=State.RUNNING),
            ErrorResponse(msg="busy", status_code=503),
            EngineUnavailable(),
            RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE),
        ],
    )
    assert _track() == TASK_ID
    assert logs == [REMOTE_RUN_ID, REMOTE_RUN_ID]
    document = _stored()
    assert document["run_log"]["state"] == "COMPLETE"
    # the final run log, without the run request of the remote WES
    assert document["run_log"]["outputs"] == {"out": 2}
    assert "request" not in document["run_log"]


@pytest.mark.usefixtures("run")
def test_track_run_too_many_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose status can't be read in a row fail with `SYSTEM_ERROR`."""
    _fake_remote(
        monkeypatch,
        [ErrorResponse(msg="busy", status_code=503) for _ in range(3)],
    )
    with pytest.raises(EngineProblem):
        _track()
    assert _stored()["run_log"]["state"] == "SYSTEM_ERROR"


@pytest.mark.usefixtures("run")
def test_track_run_remote_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose remote WES is unavailable fail with `SYSTEM_ERROR`."""
    _fake_remote(monkeypatch, [EngineUnavailable() for _ in range(3)])
    with pytest.raises(EngineUnavailable):
        _track()
    assert _stored()["run_log"]["state"] == "SYSTEM_ERROR"


@pytest.mark.usefixtures("run")
def test_track_run_log_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose log can't be read fail with `SYSTEM_ERROR`."""

    def _unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Fail as an unavailable WES.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Raises:
            EngineUnavailable: Always.
        """
        raise EngineUnavailable

    monkeypatch.setattr(WesClient, "get_run", _unavailable)
    with pytest.raises(EngineUnavailable):
        _track()
    assert _stored()["run_log"]["state"] == "SYSTEM_ERROR"


@pytest.mark.usefixtures("run")
def test_track_run_without_task_id() -> None:
    """Tasks without identifier fail."""
    task__track_run_progress.push_request(id=None)
    try:
        with pytest.raises(ValueError, match="no identifier"):
            task__track_run_progress.run(**TASK_KWARGS)
    finally:
        task__track_run_progress.pop_request()


@pytest.mark.usefixtures("run")
@pytest.mark.parametrize("task_id", [TASK_ID, None])
def test_set_run_state(task_id: str | None) -> None:
    """The state of a run is set, by task identifier or run identifier."""
    set_run_state(
        collection=get_collection(RUN_DB, RUN_COLLECTION),
        run_id=RUN_ID,
        task_id=task_id,
        state="RUNNING",
    )
    assert _stored()["run_log"]["state"] == "RUNNING"


@pytest.mark.usefixtures("run")
def test_set_run_state_unknown_run() -> None:
    """Setting the state of an unknown run fails."""
    with pytest.raises(ValueError, match="not found"):
        set_run_state(
            collection=get_collection(RUN_DB, RUN_COLLECTION),
            run_id="UNKNOWN",
            state="RUNNING",
        )
