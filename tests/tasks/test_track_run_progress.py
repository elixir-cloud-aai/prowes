"""Tests of the task tracking workflow runs (`task__track_run_progress`)."""

from collections.abc import Iterator
from typing import Any, cast

from celery.exceptions import SoftTimeLimitExceeded
from connexion import App
from foca import get_collection
import pytest

from pro_wes.exceptions import EngineProblem, EngineUnavailable
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import ErrorResponse, RunStatus, State
from pro_wes.ga4gh.wes.versions import WesVersion
from pro_wes.tasks import track_run_progress
from pro_wes.tasks.track_run_progress import (
    _stored_state_is_finished,
    task__track_run_progress,
)
from pro_wes.tasks.utils import set_run_state
from pro_wes.utils.db import DbDocumentConnector
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
        # a complete run log, as a remote WES sends it
        return {
            "run_id": run_id,
            "state": "PREEMPTED",
            "outputs": {"out": len(logs)},
            "request": {"workflow_url": "x"},
            "run_log": {"name": "engine", "exit_code": 0},
            "task_logs": [{"name": "step", "cmd": ["echo"]}],
        }

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
    # the final run log; state and run identifier are not taken from it, nor
    # the run request of the remote WES
    assert document["run_log"]["outputs"] == {"out": 2}
    assert document["run_log"]["run_log"]["exit_code"] == 0
    assert document["run_log"]["task_logs"][0]["cmd"] == ["echo"]
    assert document["run_log"]["run_id"] == RUN_ID
    assert "request" not in document["run_log"]


@pytest.mark.usefixtures("run")
@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.0", "1.0"), ("1.1", "1.1"), (None, "1.0"), ("9.9", "1.0")],
)
def test_track_run_upgrades_run_logs(
    monkeypatch: pytest.MonkeyPatch, version: str | None, expected: str
) -> None:
    """Run logs are converted from the WES version of the remote WES.

    Tasks without a known version (e.g., queued by earlier versions) take
    the oldest one.
    """
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    upgraded: list[str] = []

    def _upgrade(run_log: dict[str, Any], wes_version: WesVersion) -> dict[str, Any]:
        """Record the conversion of a run log.

        Args:
            run_log: Run log of the remote WES.
            wes_version: Its WES version.

        Returns:
            The run log, with other outputs.
        """
        upgraded.append(str(wes_version))
        return {**run_log, "outputs": {"upgraded": True}}

    monkeypatch.setattr(track_run_progress, "upgrade_run_log", _upgrade)
    task__track_run_progress.push_request(id=TASK_ID)
    try:
        task__track_run_progress.run(**TASK_KWARGS, remote_wes_version=version)
    finally:
        task__track_run_progress.pop_request()
    assert upgraded == [expected, expected]
    assert _stored()["run_log"]["outputs"] == {"upgraded": True}


@pytest.mark.usefixtures("run")
def test_track_run_remote_system_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `SYSTEM_ERROR` reported by the remote WES is not marked as proWES's."""
    _fake_remote(
        monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.SYSTEM_ERROR)]
    )
    assert _track() == TASK_ID
    document = _stored()
    assert document["run_log"]["state"] == "SYSTEM_ERROR"
    assert document["system_error_by_prowes"] is False


@pytest.mark.usefixtures("run")
def test_track_run_ignores_client_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks queued with a client token (earlier versions) don't pass it on."""
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    tokens: list[str | None] = []
    original = WesClient.set_token

    def _set_token(self: WesClient, token: str | None = None) -> None:
        """Record the token of a client.

        Args:
            self: WES client.
            token: Token of the client.
        """
        tokens.append(token)
        original(self, token)

    monkeypatch.setattr(WesClient, "set_token", _set_token)
    task__track_run_progress.push_request(id=TASK_ID)
    try:
        task__track_run_progress.run(**{**TASK_KWARGS, "jwt": "client-token"})
    finally:
        task__track_run_progress.pop_request()
    assert tokens == [None]


@pytest.mark.usefixtures("run")
def test_track_run_invalid_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid fields of remote run logs are left out; tracking goes on."""
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    monkeypatch.setattr(
        WesClient,
        "get_run",
        lambda *_args, **_kwargs: {
            "outputs": {"out": 1},
            "task_logs": [{"start_time": 1700000000}],
        },
    )
    assert _track() == TASK_ID
    document = _stored()
    assert document["run_log"]["state"] == "COMPLETE"
    assert document["run_log"]["outputs"] == {"out": 1}
    assert "task_logs" not in document["run_log"]


@pytest.mark.usefixtures("run")
def test_track_run_system_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """System logs of WES 1.1.0 run and task logs are stored."""
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    monkeypatch.setattr(
        WesClient,
        "get_run",
        lambda *_args, **_kwargs: {
            "run_log": {"exit_code": 0, "system_logs": ["disk ok"]},
            "task_logs": [{"name": "step", "system_logs": ["node 1"]}],
        },
    )
    assert _track() == TASK_ID
    document = _stored()
    assert document["run_log"]["run_log"]["system_logs"] == ["disk ok"]
    assert document["run_log"]["task_logs"][0]["system_logs"] == ["node 1"]


@pytest.mark.usefixtures("run")
def test_track_run_error_response_as_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run logs without stored fields (e.g., error responses) change nothing."""
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    monkeypatch.setattr(
        WesClient,
        "get_run",
        lambda *_args, **_kwargs: {"msg": "not found", "status_code": 404},
    )
    assert _track() == TASK_ID
    document = _stored()
    assert document["run_log"] == {"run_id": RUN_ID, "state": "COMPLETE"}


@pytest.mark.usefixtures("run")
def test_track_run_remote_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote `UNKNOWN` state is stored."""
    states: list[str] = []
    _fake_remote(
        monkeypatch,
        [
            RunStatus(run_id=REMOTE_RUN_ID, state=State.UNKNOWN),
            RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE),
        ],
    )
    update = DbDocumentConnector.update_run_state

    def _record(self: DbDocumentConnector, state: str = "UNKNOWN") -> None:
        """Record and store a state.

        Args:
            self: Database connector.
            state: New state.
        """
        states.append(state)
        update(self, state=state)

    monkeypatch.setattr(DbDocumentConnector, "update_run_state", _record)
    _track()
    assert states == ["INITIALIZING", "UNKNOWN", "COMPLETE"]


@pytest.mark.usefixtures("run")
def test_track_run_preempted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preempted runs (WES 1.1.0) are final: stored as is, with their log."""
    logs = _fake_remote(
        monkeypatch,
        [
            RunStatus(run_id=REMOTE_RUN_ID, state=State.RUNNING),
            RunStatus.model_validate({"run_id": REMOTE_RUN_ID, "state": "PREEMPTED"}),
            # never requested: tracking ends with `PREEMPTED`
            RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE),
        ],
    )
    assert _track() == TASK_ID
    assert logs == [REMOTE_RUN_ID, REMOTE_RUN_ID]
    document = _stored()
    assert document["run_log"]["state"] == "PREEMPTED"
    assert document["run_log"]["outputs"] == {"out": 2}


@pytest.mark.usefixtures("run")
def test_track_run_invalid_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid status responses are retried, then fail with `SYSTEM_ERROR`."""
    _fake_remote(monkeypatch, [ValueError("invalid response") for _ in range(3)])
    with pytest.raises(ValueError, match="invalid response"):
        _track()
    assert _stored()["run_log"]["state"] == "SYSTEM_ERROR"
    assert _stored()["system_error_by_prowes"] is True


@pytest.mark.usefixtures("run")
def test_track_run_soft_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose tracking takes too long fail with `SYSTEM_ERROR`."""
    _fake_remote(monkeypatch, [SoftTimeLimitExceeded()])
    with pytest.raises(SoftTimeLimitExceeded):
        _track()
    assert _stored()["run_log"]["state"] == "SYSTEM_ERROR"
    assert _stored()["system_error_by_prowes"] is True


@pytest.mark.usefixtures("run")
def test_track_run_soft_time_limit_final_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs that finish before tracking takes too long keep their final state.

    E.g., the time limit is hit while the final run log is read.
    """
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    calls: list[str] = []

    def _get_run(_self: WesClient, run_id: str, **_kwargs: Any) -> dict[str, Any]:
        """Return a run log, then hit the time limit.

        Args:
            _self: WES client; ignored.
            run_id: Run identifier.
            **_kwargs: Further arguments; ignored.

        Returns:
            Run log, on the first call.

        Raises:
            SoftTimeLimitExceeded: On later calls.
        """
        calls.append(run_id)
        if len(calls) > 1:
            raise SoftTimeLimitExceeded()
        return {"outputs": {"out": 1}}

    monkeypatch.setattr(WesClient, "get_run", _get_run)
    with pytest.raises(SoftTimeLimitExceeded):
        _track()
    document = _stored()
    assert document["run_log"]["state"] == "COMPLETE"
    assert document["system_error_by_prowes"] is False


@pytest.mark.usefixtures("run")
def test_track_run_soft_time_limit_first_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose tracking hits the time limit before polling fail, too."""
    _fake_remote(monkeypatch, [])

    def _get_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        """Hit the time limit.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Raises:
            SoftTimeLimitExceeded: Always.
        """
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(WesClient, "get_run", _get_run)
    with pytest.raises(SoftTimeLimitExceeded):
        _track()
    document = _stored()
    assert document["run_log"]["state"] == "SYSTEM_ERROR"
    assert document["system_error_by_prowes"] is True


@pytest.mark.usefixtures("run")
def test_track_run_soft_time_limit_after_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final states keep, if the limit is hit right after they are stored."""
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])
    update = DbDocumentConnector.update_run_state

    def _update(self: DbDocumentConnector, state: str = "UNKNOWN") -> None:
        """Store a state, then hit the time limit for final ones.

        Args:
            self: Database connector.
            state: New state.

        Raises:
            SoftTimeLimitExceeded: The state is final.
        """
        update(self, state=state)
        if State(state).is_finished:
            raise SoftTimeLimitExceeded()

    monkeypatch.setattr(DbDocumentConnector, "update_run_state", _update)
    with pytest.raises(SoftTimeLimitExceeded):
        _track()
    document = _stored()
    assert document["run_log"]["state"] == "COMPLETE"
    assert document["system_error_by_prowes"] is False


@pytest.mark.parametrize(
    "document",
    [None, {}, {"run_log": {}}, {"run_log": {"state": "INVALID"}}],
    ids=["no run", "no run log", "no state", "invalid state"],
)
def test_stored_state_is_finished_invalid(document: dict[str, Any] | None) -> None:
    """Missing or invalid stored states aren't final.

    Args:
        document: Stored run (projected), if any.
    """
    connector = DbDocumentConnector(
        collection=cast(Any, _FindOne(document)), task_id=TASK_ID
    )
    assert _stored_state_is_finished(db_client=connector) is False


class _FindOne:
    """Collection that returns one document for any query.

    Args:
        document: Document to return.
    """

    def __init__(self, document: dict[str, Any] | None) -> None:
        """Class constructor."""
        self.document = document

    def find_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        """Return the document.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Returns:
            The document.
        """
        return self.document


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
    assert _stored()["system_error_by_prowes"] is True


@pytest.mark.usefixtures("run")
def test_track_run_remote_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose remote WES is unavailable fail with `SYSTEM_ERROR`."""
    _fake_remote(monkeypatch, [EngineUnavailable() for _ in range(3)])
    with pytest.raises(EngineUnavailable):
        _track()
    assert _stored()["run_log"]["state"] == "SYSTEM_ERROR"
    assert _stored()["system_error_by_prowes"] is True


@pytest.mark.usefixtures("run")
def test_track_run_log_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs whose log can't be read are tracked; their state is kept."""
    _fake_remote(monkeypatch, [RunStatus(run_id=REMOTE_RUN_ID, state=State.COMPLETE)])

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
    assert _track() == TASK_ID
    assert _stored()["run_log"]["state"] == "COMPLETE"


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
