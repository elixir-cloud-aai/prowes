"""Tests of the endpoints of workflow runs (`POST /runs`, `GET /runs`, ...).

Also the error paths of the controllers (`WorkflowRuns`): remote errors,
inconsistent stored runs, run identifiers and storage that aren't available,
access checks, and the paging of `GET /runs`.
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from connexion import App
from foca.testing import Client
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import pytest

from pro_wes.exceptions import EngineUnavailable, Forbidden
from pro_wes.ga4gh.wes import workflow_runs
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import (
    DbDocument,
    ErrorResponse,
    RunId,
    State,
    WesEndpoint,
)
from pro_wes.ga4gh.wes.workflow_runs import WorkflowRuns
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db import DbDocumentConnector
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import OWNER, TokenClient

BASE_PATH = "/ga4gh/wes/v1"
REMOTE_RUN_ID = "REMOTE1"
RUN_REQUEST: dict[str, str] = {
    "workflow_params": '{"input": "value"}',
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}
BOUNDARY = "prowes-test-boundary"


def _multipart(fields: dict[str, str]) -> bytes:
    """Encode form fields as a `multipart/form-data` body.

    Args:
        fields: Names and values of the form fields.

    Returns:
        Body, with the boundary `BOUNDARY`.
    """
    parts = [
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
        for name, value in fields.items()
    ]
    return ("".join(parts) + f"--{BOUNDARY}--\r\n").encode()


def _post_run(client: Client, fields: dict[str, str]) -> Any:
    """Send `POST /runs` with form fields.

    Args:
        client: Test client of the app.
        fields: Form fields of the run request.

    Returns:
        Response.
    """
    return client.post(
        f"{BASE_PATH}/runs",
        content=_multipart(fields),
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the tracking tasks of runs instead of queuing them.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Keyword arguments and options of the queued tasks, in order.
    """
    calls: list[dict[str, Any]] = []

    def _apply_async(args: Any, kwargs: dict[str, Any], **options: Any) -> None:
        """Record a queued task.

        Args:
            args: Positional arguments of the task.
            kwargs: Keyword arguments of the task.
            **options: Options of the task.
        """
        calls.append({"kwargs": kwargs, **options})

    monkeypatch.setattr(task__track_run_progress, "apply_async", _apply_async)
    return calls


@pytest.fixture
def remote_wes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Fake the remote WES: runs are accepted under `REMOTE_RUN_ID`.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Form data of the run requests sent to the remote WES, in order.
    """
    sent: list[dict[str, str]] = []

    def _post_run(self: WesClient, form_data: dict[str, str], **kwargs: Any) -> RunId:
        """Accept a run.

        Args:
            self: WES client.
            form_data: Form data of the run request.
            **kwargs: Further arguments; ignored.

        Returns:
            Identifier of the run.
        """
        sent.append(form_data)
        return RunId(run_id=REMOTE_RUN_ID)

    monkeypatch.setattr(WesClient, "post_run", _post_run)
    return sent


def test_post_run(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """A run is stored, sent to the remote WES and tracked in the background."""
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 200
    assert remote_wes[0]["workflow_type"] == "CWL"
    assert remote_wes[0]["tags"] == "{}"
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    assert stored["wes_endpoint"]["run_id"] == REMOTE_RUN_ID
    assert stored["run_log"]["request"]["workflow_url"] == RUN_REQUEST["workflow_url"]
    assert len(stored["run_log"]["run_id"]) == 6
    # the run's own identifier, by which it is found
    assert response.json() == {"run_id": stored["run_log"]["run_id"]}
    run = foca_client.get(f"{BASE_PATH}/runs/{response.json()['run_id']}")
    assert run.status_code == 200
    assert queued == [
        {
            "kwargs": {
                "jwt": None,
                "remote_host": stored["wes_endpoint"]["host"],
                "remote_base_path": "/ga4gh/wes/v1",
                "remote_run_id": REMOTE_RUN_ID,
            },
            "task_id": stored["task_id"],
        }
    ]


def test_post_run_unsupported_workflow_type(
    foca_client: Client,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs of workflow types the service info doesn't list are refused."""
    response = _post_run(foca_client, {**RUN_REQUEST, "workflow_type": "WDL"})
    assert response.status_code == 400
    assert response.json()["title"] == "Bad request"
    assert not remote_wes
    assert not queued


def test_post_run_remote_error(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    queued: list[dict[str, Any]],
) -> None:
    """Runs that the remote WES refuses fail, with the state `SYSTEM_ERROR`."""
    monkeypatch.setattr(
        WesClient,
        "post_run",
        lambda *_args, **_kwargs: ErrorResponse(msg="no", status_code=403),
    )
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 403
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    assert stored["run_log"]["state"] == "SYSTEM_ERROR"
    assert stored["system_error_by_prowes"] is True
    assert not queued


def test_list_runs(
    foca_client: Client,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Runs are listed with identifier and state, newest first."""
    for _ in range(2):
        assert _post_run(foca_client, RUN_REQUEST).status_code == 200
    response = foca_client.get(f"{BASE_PATH}/runs")
    assert response.status_code == 200
    body = response.json()
    assert len(body["runs"]) == 2
    assert {run["state"] for run in body["runs"]} == {"UNKNOWN"}
    assert body["next_page_token"] == ""
    page = foca_client.get(f"{BASE_PATH}/runs", params={"page_size": 1})
    assert len(page.json()["runs"]) == 1
    assert page.json()["runs"][0] == body["runs"][0]


def test_get_run_log_and_status(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """The log and status of a stored run are returned."""
    assert _post_run(foca_client, RUN_REQUEST).status_code == 200
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    run_id = stored["run_log"]["run_id"]
    response = foca_client.get(f"{BASE_PATH}/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    response = foca_client.get(f"{BASE_PATH}/runs/{run_id}/status")
    assert response.status_code == 200
    assert response.json() == {"run_id": run_id, "state": "UNKNOWN"}


@pytest.mark.parametrize("path", ["/runs/ABC123/tasks", "/runs/ABC123/tasks/task1"])
def test_task_endpoints_not_implemented(foca_client: Client, path: str) -> None:
    """The task endpoints of WES 1.1.0 aren't served yet (#124): `501`."""
    response = foca_client.get(f"{BASE_PATH}{path}")
    assert response.status_code == 501
    assert response.json()["status"] == 501
    assert "tasks" in response.json()["detail"]


@pytest.mark.parametrize(
    "fields",
    [
        {"workflow_engine": "cwltool"},
        {"workflow_engine_version": "3.1"},
        {"workflow_engine": "cwltool", "workflow_engine_version": "3.1"},
        # only empty values choose no engine
        {"workflow_engine": " "},
    ],
)
def test_post_run_workflow_engine_refused(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
    fields: dict[str, str],
) -> None:
    """Runs that choose a workflow engine are refused until it is forwarded.

    Neither stored nor forwarded (#66), instead of losing the engine choice.
    """
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    assert response.status_code == 400
    assert "workflow_engine" in response.json()["detail"]
    assert mongodb[RUN_DB][RUN_COLLECTION].count_documents({}) == 0
    assert not remote_wes
    assert not queued


def test_post_run_empty_workflow_engine(
    foca_client: Client,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """Empty workflow engine fields (no choice) are accepted, not forwarded."""
    fields = {"workflow_engine": "", "workflow_engine_version": ""}
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    assert response.status_code == 200
    assert "workflow_engine" not in remote_wes[0]
    assert "workflow_engine_version" not in remote_wes[0]


def test_post_run_workflow_engine_as_file_refused(
    foca_client: Client,
    queued: list[dict[str, Any]],
    remote_wes: list[dict[str, str]],
) -> None:
    """A workflow engine sent as a file part is refused, too."""
    fields = _multipart(RUN_REQUEST)[: -len(f"--{BOUNDARY}--\r\n")]
    part = (
        f"--{BOUNDARY}\r\nContent-Disposition: form-data;"
        ' name="workflow_engine"; filename="engine.txt"\r\n\r\n'
        f"cwltool\r\n--{BOUNDARY}--\r\n"
    )
    response = foca_client.post(
        f"{BASE_PATH}/runs",
        content=fields + part.encode(),
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    assert response.status_code == 400
    assert not remote_wes


def _stored(mongodb: MongoClient[dict[str, Any]]) -> dict[str, Any]:
    """Get the only stored run.

    Args:
        mongodb: Database of the test.

    Returns:
        The stored run document.
    """
    document = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert document is not None
    return document


@pytest.fixture
def app_context(foca_app: App) -> Iterator[None]:
    """Enter the app context of the app.

    Args:
        foca_app: The app.

    Yields:
        Nothing; within the app context.
    """
    with foca_app.app.app_context():
        yield


@pytest.mark.usefixtures("queued")
def test_post_run_remote_unavailable(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Runs that can't be sent to the remote WES fail with `SYSTEM_ERROR`."""

    def _unavailable(*_args: Any, **_kwargs: Any) -> None:
        """Fail like an unreachable WES.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Raises:
            EngineUnavailable: Always.
        """
        raise EngineUnavailable

    monkeypatch.setattr(WesClient, "post_run", _unavailable)
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 500
    assert _stored(mongodb)["run_log"]["state"] == "SYSTEM_ERROR"
    assert _stored(mongodb)["system_error_by_prowes"] is True


@pytest.mark.usefixtures("queued")
@pytest.mark.parametrize(("remote", "expected"), [(400, 400), (401, 401), (502, 500)])
def test_post_run_remote_error_status(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    remote: int,
    expected: int,
) -> None:
    """Errors of the remote WES become errors of proWES, the run fails."""
    monkeypatch.setattr(
        WesClient,
        "post_run",
        lambda *_args, **_kwargs: ErrorResponse(msg="no", status_code=remote),
    )
    assert _post_run(foca_client, RUN_REQUEST).status_code == expected
    assert _stored(mongodb)["run_log"]["state"] == "SYSTEM_ERROR"
    assert _stored(mongodb)["system_error_by_prowes"] is True


@pytest.mark.usefixtures("queued")
@pytest.mark.parametrize(
    ("update", "detail"),
    [
        ({"wes_endpoint": None}, "An unexpected error occurred."),
        (
            {"wes_endpoint": WesEndpoint(host="https://h", base_path=None)},
            "An unexpected error occurred.",
        ),
        ({"task_id": None}, "No/few unique run identifiers available."),
    ],
    ids=["no endpoint", "no base path", "no task"],
)
def test_post_run_incomplete_document(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    remote_wes: list[dict[str, str]],
    update: dict[str, Any],
    detail: str,
) -> None:
    """Runs stored without endpoint, base path or task fail with `500`."""
    create = WorkflowRuns._create_run_environment

    def _create(self: WorkflowRuns, **kwargs: Any) -> DbDocument:
        """Store a run, then drop fields of the returned document.

        Args:
            self: Controllers.
            **kwargs: Arguments of `_create_run_environment()`.

        Returns:
            The stored document, without the fields of `update`.
        """
        return create(self, **kwargs).model_copy(update=update)

    monkeypatch.setattr(WorkflowRuns, "_create_run_environment", _create)
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 500
    assert response.json()["detail"] == detail
    # refused before forwarding
    assert not remote_wes


@pytest.mark.usefixtures("queued", "remote_wes")
@pytest.mark.parametrize(
    ("update", "detail"),
    [
        (
            lambda doc: doc.model_copy(update={"wes_endpoint": None}),
            "An unexpected error occurred.",
        ),
        (
            lambda doc: doc.model_copy(
                update={"wes_endpoint": WesEndpoint(host="https://h", base_path=None)}
            ),
            "An unexpected error occurred.",
        ),
        (
            lambda doc: doc.model_copy(
                update={"wes_endpoint": WesEndpoint(host="https://h", run_id=None)}
            ),
            "No/few requirements provided for WES endpoint.",
        ),
        (
            lambda doc: doc.model_copy(
                update={"run_log": doc.run_log.model_copy(update={"run_id": None})}
            ),
            "No/few unique run identifiers available.",
        ),
    ],
    ids=["no endpoint", "no base path", "no remote run id", "no run id"],
)
def test_post_run_incomplete_update(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    update: Callable[[DbDocument], DbDocument],
    detail: str,
) -> None:
    """Runs without endpoint or identifiers after forwarding fail with `500`."""
    upsert = DbDocumentConnector.upsert_fields_in_root_object

    def _upsert(self: DbDocumentConnector, **kwargs: Any) -> DbDocument:
        """Store the remote identifier, then drop fields of the result.

        Args:
            self: Database connector.
            **kwargs: Arguments of `upsert_fields_in_root_object()`.

        Returns:
            The updated document, changed by `update`.
        """
        return update(upsert(self, **kwargs))

    monkeypatch.setattr(DbDocumentConnector, "upsert_fields_in_root_object", _upsert)
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 500
    assert response.json()["detail"] == detail


@pytest.mark.usefixtures("remote_wes")
def test_post_run_timeout_job(
    add_overlay: Callable[[str], None],
    queued: list[dict[str, Any]],
    token: str,
) -> None:
    """With `timeout_job`, the tracking task gets a soft time limit."""
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    add_overlay("custom:\n  post_runs:\n    timeout_job: 60\n")
    with TokenClient(init_app(), token) as client:
        assert _post_run(client, RUN_REQUEST).status_code == 200
    assert queued[0]["soft_time_limit"] == 60


@pytest.mark.usefixtures("queued", "remote_wes")
def test_post_run_id_taken(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    storage_path: Path,
) -> None:
    """Identifiers whose working directory exists are skipped."""
    ids = iter(["TAKEN1", "FREE01"])
    monkeypatch.setattr(workflow_runs, "generate_id", lambda **_kwargs: next(ids))
    (storage_path / "TAKEN1").mkdir(parents=True)
    assert _post_run(foca_client, RUN_REQUEST).json() == {"run_id": "FREE01"}
    assert _stored(mongodb)["run_log"]["run_id"] == "FREE01"


@pytest.mark.usefixtures("queued", "remote_wes")
def test_post_run_id_stored(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    storage_path: Path,
) -> None:
    """Identifiers of stored runs are skipped, also without a working directory.

    E.g., runs stored by another instance that shares the database, but not
    the storage. The unique index on `run_log.run_id` rejects the duplicate;
    its working directory is removed again.

    Args:
        foca_client: Test client of the app.
        monkeypatch: Monkeypatch fixture of the test.
        mongodb: Database of the test (FOCA's fixture).
        storage_path: Storage directory of the runs.
    """
    collection = mongodb[RUN_DB][RUN_COLLECTION]
    collection.insert_one({"run_log": {"run_id": "TAKEN1", "state": "QUEUED"}})
    ids = iter(["TAKEN1", "FREE01"])
    monkeypatch.setattr(workflow_runs, "generate_id", lambda **_kwargs: next(ids))
    assert _post_run(foca_client, RUN_REQUEST).json() == {"run_id": "FREE01"}
    assert collection.count_documents({"run_log.run_id": "TAKEN1"}) == 1
    assert [path.name for path in storage_path.iterdir()] == ["FREE01"]


def test_runs_indexes(foca_app: App, mongodb: MongoClient[dict[str, Any]]) -> None:
    """The app creates the indexes of the runs collection at start.

    Run identifiers are unique; the runs of a user are found by user and
    newest first (`GET /runs` with authentication). The names are those of
    the docs (operations, upgrading); the previous index on `run_id` and
    `task_id` is no longer configured.

    Args:
        foca_app: The app, which created the indexes.
        mongodb: Database of the test (FOCA's fixture).
    """
    indexes = mongodb[RUN_DB][RUN_COLLECTION].index_information()
    keys = {
        name: [(key, int(direction)) for key, direction in index["key"]]
        for name, index in indexes.items()
    }
    assert keys == {
        "_id_": [("_id", 1)],
        "run_log.run_id_1": [("run_log.run_id", 1)],
        "user_id_1__id_-1": [("user_id", 1), ("_id", -1)],
    }
    assert indexes["run_log.run_id_1"].get("unique") is True
    assert not indexes["run_log.run_id_1"].get("sparse")


@pytest.mark.usefixtures("queued", "remote_wes")
def test_post_run_storage_missing(foca_client: Client, storage_path: Path) -> None:
    """Runs fail with `500` if the storage directory is gone."""
    storage_path.rmdir()
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 500
    assert response.json()["detail"] == "Storage is not accessible."


@pytest.mark.usefixtures("queued", "remote_wes")
def test_post_run_ids_unavailable(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    storage_path: Path,
) -> None:
    """Runs fail with `500` if no identifier can be stored; no directories stay."""

    def _duplicate(*_args: Any, **_kwargs: Any) -> None:
        """Fail like an identifier that is taken.

        Args:
            *_args: Positional arguments; ignored.
            **_kwargs: Keyword arguments; ignored.

        Raises:
            DuplicateKeyError: Always.
        """
        raise DuplicateKeyError("taken")

    monkeypatch.setattr(
        mongodb[RUN_DB][RUN_COLLECTION].__class__, "insert_one", _duplicate
    )
    response = _post_run(foca_client, RUN_REQUEST)
    assert response.status_code == 500
    assert response.json()["detail"] == "No/few unique run identifiers available."
    assert not list(storage_path.iterdir())


@pytest.mark.usefixtures("queued", "remote_wes")
def test_list_runs_pages(foca_client: Client) -> None:
    """The next page token gives the older runs, until they are exhausted."""
    for _ in range(2):
        assert _post_run(foca_client, RUN_REQUEST).status_code == 200
    first = foca_client.get(f"{BASE_PATH}/runs", params={"page_size": 1}).json()
    second = foca_client.get(
        f"{BASE_PATH}/runs",
        params={"page_size": 1, "page_token": first["next_page_token"]},
    ).json()
    assert len(second["runs"]) == 1
    assert second["runs"][0]["run_id"] != first["runs"][0]["run_id"]
    assert second["next_page_token"] == ""


@pytest.mark.parametrize("page_size", [-2, 0, 1, 2, 3, 10])
@pytest.mark.usefixtures("app_context")
def test_list_runs_last_page(
    mongodb: MongoClient[dict[str, Any]], page_size: int
) -> None:
    """The last page has no next page token, also if it is full.

    Args:
        mongodb: Database of the test (FOCA's fixture).
        page_size: Number of runs per page.
    """
    mongodb[RUN_DB][RUN_COLLECTION].insert_many(
        [{"run_log": {"run_id": f"RUN{i:03}", "state": "QUEUED"}} for i in range(3)]
    )
    runs: list[dict[str, Any]] = []
    page = WorkflowRuns().list_runs(page_size=page_size)
    runs += page["runs"]
    while page["next_page_token"]:
        page = WorkflowRuns().list_runs(
            page_size=page_size, page_token=page["next_page_token"]
        )
        assert page["runs"]
        runs += page["runs"]
    assert [run["run_id"] for run in runs] == ["RUN002", "RUN001", "RUN000"]


@pytest.mark.usefixtures("app_context")
def test_list_runs_last_page_of_user(mongodb: MongoClient[dict[str, Any]]) -> None:
    """Older runs of other users give no next page token.

    Args:
        mongodb: Database of the test (FOCA's fixture).
    """
    mongodb[RUN_DB][RUN_COLLECTION].insert_many(
        [
            {"user_id": "bob", "run_log": {"run_id": "RUNB01", "state": "QUEUED"}},
            {"user_id": "alice", "run_log": {"run_id": "RUNA01", "state": "QUEUED"}},
            {"user_id": "alice", "run_log": {"run_id": "RUNA02", "state": "QUEUED"}},
        ]
    )
    first = WorkflowRuns().list_runs(user_id="alice", page_size=1)
    assert first["next_page_token"]
    second = WorkflowRuns().list_runs(
        user_id="alice", page_size=1, page_token=first["next_page_token"]
    )
    assert second["runs"] == [{"run_id": "RUNA01", "state": "QUEUED"}]
    assert second["next_page_token"] == ""


@pytest.mark.usefixtures("app_context")
def test_list_runs_empty() -> None:
    """Without runs, the list and the next page token are empty."""
    assert WorkflowRuns().list_runs() == {"next_page_token": "", "runs": []}


@pytest.mark.usefixtures("app_context")
def test_list_runs_of_user(mongodb: MongoClient[dict[str, Any]]) -> None:
    """With a user, only the runs of that user are listed."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_many(
        [
            {"user_id": "alice", "run_log": {"run_id": "RUNA01", "state": "QUEUED"}},
            {"user_id": "bob", "run_log": {"run_id": "RUNB01", "state": "QUEUED"}},
        ]
    )
    runs = WorkflowRuns().list_runs(user_id="alice")["runs"]
    assert runs == [{"run_id": "RUNA01", "state": "QUEUED"}]


def test_get_run_status_not_found(foca_client: Client) -> None:
    """The status of unknown runs is not found."""
    assert foca_client.get(f"{BASE_PATH}/runs/UNKNWN/status").status_code == 404


def test_get_run_log_without_request(
    foca_client: Client, mongodb: MongoClient[dict[str, Any]]
) -> None:
    """Run logs without run request are returned as stored."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {"user_id": OWNER, "run_log": {"run_id": "NOREQ1", "state": "QUEUED"}}
    )
    response = foca_client.get(f"{BASE_PATH}/runs/NOREQ1")
    assert response.status_code == 200
    assert response.json() == {"run_id": "NOREQ1", "state": "QUEUED", "task_logs": []}


@pytest.mark.parametrize(
    ("owner", "requester"), [("alice", None), (None, None), ("alice", "alice")]
)
def test_access_allowed(owner: str | None, requester: str | None) -> None:
    """The owner, and requests without requester, have access."""
    WorkflowRuns._check_access_permission("RUN001", owner, requester)


@pytest.mark.parametrize(("owner", "requester"), [("alice", "bob"), (None, "bob")])
def test_access_forbidden(owner: str | None, requester: str | None) -> None:
    """Requesters other than the owner are refused."""
    with pytest.raises(Forbidden):
        WorkflowRuns._check_access_permission("RUN001", owner, requester)


@pytest.mark.usefixtures("app_context")
@pytest.mark.parametrize("operation", ["get_run_log", "get_run_status", "cancel_run"])
def test_run_of_other_user_forbidden(
    mongodb: MongoClient[dict[str, Any]], operation: str
) -> None:
    """Runs of other users can't be read or canceled."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {"user_id": "alice", "run_log": {"run_id": "RUNA01", "state": "QUEUED"}}
    )
    with pytest.raises(Forbidden):
        getattr(WorkflowRuns(), operation)(run_id="RUNA01", user_id="bob")


def test_cancel_run_forwarded(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Cancellations are forwarded with the remote run identifier."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {
            "user_id": OWNER,
            "run_log": {"run_id": "CANCEL", "state": "RUNNING"},
            "wes_endpoint": {
                "host": "https://wes.example.org",
                "base_path": "/ga4gh/wes/v1",
                "run_id": REMOTE_RUN_ID,
            },
        }
    )
    canceled: list[str] = []
    monkeypatch.setattr(
        WesClient,
        "cancel_run",
        lambda _self, run_id, **_kwargs: canceled.append(run_id),
    )
    response = foca_client.post(f"{BASE_PATH}/runs/CANCEL/cancel")
    assert response.status_code == 200
    assert response.json() == {"run_id": "CANCEL"}
    assert canceled == [REMOTE_RUN_ID]


def _fail_cancel(_self: WesClient, run_id: str, **_kwargs: Any) -> None:
    """Fail on any attempt to forward a cancellation.

    Args:
        _self: WES client; ignored.
        run_id: Identifier of the remote run.
        **_kwargs: Keyword arguments; ignored.

    Raises:
        AssertionError: Always.
    """
    raise AssertionError(f"cancellation of {run_id} forwarded")


# Stored endpoint of a run that has been forwarded (a remote run)
REMOTE_ENDPOINT: dict[str, str] = {
    "host": "https://wes.example.org",
    "base_path": "/ga4gh/wes/v1",
    "run_id": REMOTE_RUN_ID,
}
# Stored run logs of unknown state: without, `null` or an invalid state
UNKNOWN_RUN_LOGS: dict[str, dict[str, Any]] = {
    "UNKNOWN": {"state": "UNKNOWN"},
    "no state": {},
    "null state": {"state": None},
    "invalid state": {"state": "INVALID"},
    "unhashable state": {"state": ["RUNNING"]},
}


@pytest.mark.parametrize(
    "state",
    [
        state
        for state in State
        if not state.is_cancelable and state is not State.UNKNOWN
    ],
)
def test_cancel_run_not_cancelable(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    state: State,
) -> None:
    """Finished runs and runs being canceled get `409`, without contacting the WES."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {
            "user_id": OWNER,
            "run_log": {"run_id": "DONE01", "state": state.value},
            "wes_endpoint": REMOTE_ENDPOINT,
        }
    )
    monkeypatch.setattr(WesClient, "cancel_run", _fail_cancel)
    response = foca_client.post(f"{BASE_PATH}/runs/DONE01/cancel")
    assert response.status_code == 409
    assert response.json() == {
        "title": "Conflict",
        "status": 409,
        "detail": "The run can't be canceled in its current state.",
    }


@pytest.mark.parametrize(
    "run_log", UNKNOWN_RUN_LOGS.values(), ids=UNKNOWN_RUN_LOGS.keys()
)
def test_cancel_run_unknown_without_remote_run(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    run_log: dict[str, Any],
) -> None:
    """Runs of unknown state without remote run (not forwarded yet) get `409`."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {"user_id": OWNER, "run_log": {"run_id": "NOSTAT", **run_log}}
    )
    monkeypatch.setattr(WesClient, "cancel_run", _fail_cancel)
    response = foca_client.post(f"{BASE_PATH}/runs/NOSTAT/cancel")
    assert response.status_code == 409


@pytest.mark.parametrize(
    "run_log",
    [
        *({"state": state.value} for state in State if state.is_cancelable),
        *UNKNOWN_RUN_LOGS.values(),
    ],
    ids=[
        *(state.value for state in State if state.is_cancelable),
        *(f"{name} with remote run" for name in UNKNOWN_RUN_LOGS),
    ],
)
def test_cancel_run_cancelable(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    run_log: dict[str, Any],
) -> None:
    """Cancellations of cancelable runs are forwarded.

    Runs in a cancelable state, and runs of unknown state that have a remote
    run (e.g., not tracked yet, or reported as `UNKNOWN` by the WES).
    """
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {
            "user_id": OWNER,
            "run_log": {"run_id": "ACTIVE", **run_log},
            "wes_endpoint": REMOTE_ENDPOINT,
        }
    )
    canceled: list[str] = []
    monkeypatch.setattr(
        WesClient,
        "cancel_run",
        lambda _self, run_id, **_kwargs: canceled.append(run_id),
    )
    response = foca_client.post(f"{BASE_PATH}/runs/ACTIVE/cancel")
    assert response.status_code == 200
    assert canceled == [REMOTE_RUN_ID]


def test_cancel_run_system_error_by_prowes(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Runs that proWES set to `SYSTEM_ERROR` (lost track of) can be canceled.

    The cancellation is forwarded; the stored state is kept, as proWES doesn't
    track the run any longer.
    """
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {
            "run_log": {"run_id": "LOST01", "state": "SYSTEM_ERROR"},
            "system_error_by_prowes": True,
            "wes_endpoint": REMOTE_ENDPOINT,
        }
    )
    canceled: list[str] = []
    monkeypatch.setattr(
        WesClient,
        "cancel_run",
        lambda _self, run_id, **_kwargs: canceled.append(run_id),
    )
    response = foca_client.post(f"{BASE_PATH}/runs/LOST01/cancel")
    assert response.status_code == 200
    assert response.json() == {"run_id": "LOST01"}
    assert canceled == [REMOTE_RUN_ID]
    assert _stored(mongodb)["run_log"]["state"] == "SYSTEM_ERROR"


@pytest.mark.parametrize(
    "document",
    [
        {"system_error_by_prowes": False, "wes_endpoint": REMOTE_ENDPOINT},
        {"wes_endpoint": REMOTE_ENDPOINT},
        {"system_error_by_prowes": None, "wes_endpoint": REMOTE_ENDPOINT},
        {"system_error_by_prowes": True},
        {
            "system_error_by_prowes": True,
            "wes_endpoint": {**REMOTE_ENDPOINT, "run_id": None},
        },
    ],
    ids=[
        "reported by WES",
        "unmarked (earlier versions)",
        "null mark",
        "by proWES without endpoint",
        "by proWES without remote run",
    ],
)
def test_cancel_run_system_error_refused(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    document: dict[str, Any],
) -> None:
    """Other `SYSTEM_ERROR`s get `409`, without contacting the WES.

    Those reported by the WES instance, and those of proWES without a remote
    run (e.g., the run request failed).
    """
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {"run_log": {"run_id": "FAILED", "state": "SYSTEM_ERROR"}, **document}
    )
    monkeypatch.setattr(WesClient, "cancel_run", _fail_cancel)
    response = foca_client.post(f"{BASE_PATH}/runs/FAILED/cancel")
    assert response.status_code == 409


@pytest.mark.parametrize(
    "endpoint",
    [
        {},
        {"wes_endpoint": None},
        {
            "wes_endpoint": {
                "host": "https://wes.example.org",
                "base_path": "/ga4gh/wes/v1",
            }
        },
        {"wes_endpoint": {**REMOTE_ENDPOINT, "run_id": None}},
        {"wes_endpoint": {**REMOTE_ENDPOINT, "base_path": None}},
    ],
    ids=[
        "no endpoint",
        "null endpoint",
        "no remote run",
        "null remote run",
        "null base path",
    ],
)
def test_cancel_run_without_remote_run(
    foca_client: Client,
    monkeypatch: pytest.MonkeyPatch,
    mongodb: MongoClient[dict[str, Any]],
    endpoint: dict[str, Any],
) -> None:
    """Cancelable runs without remote run get `500`, without contacting the WES."""
    document: dict[str, Any] = {
        "user_id": OWNER,
        "run_log": {"run_id": "NOREMO", "state": "RUNNING"},
        **endpoint,
    }
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(document)
    monkeypatch.setattr(WesClient, "cancel_run", _fail_cancel)
    response = foca_client.post(f"{BASE_PATH}/runs/NOREMO/cancel")
    assert response.status_code == 500
    assert response.json()["detail"] == "No/few requirements provided for WES endpoint."


def test_cancel_run_not_found(foca_client: Client) -> None:
    """Unknown runs can't be canceled."""
    assert foca_client.post(f"{BASE_PATH}/runs/UNKNWN/cancel").status_code == 404
