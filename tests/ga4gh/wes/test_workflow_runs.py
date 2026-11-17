"""Tests of the endpoints of workflow runs (`POST /runs`, `GET /runs`)."""

from typing import Any

from foca.testing import Client
from pymongo import MongoClient
import pytest

from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import ErrorResponse, RunId
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

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
    assert body["next_page_token"]
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
