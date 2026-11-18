"""Tests of the task tracking workflow runs without a remote run identifier.

Kept apart from `test_track_run_progress.py`, which is rewritten for WES
1.1.0 (#70), to avoid conflicts; to be merged into it afterwards.
"""

from typing import Any

from connexion import App
from foca import get_collection
import pytest

from pro_wes.exceptions import WesEndpointProblem
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

TASK_ID = "task-1"
REMOTE_RUN_ID = "REMOTE1"


@pytest.mark.parametrize(
    "wes_endpoint",
    [None, {"host": "https://wes.example.org", "base_path": "/ga4gh/wes/v1"}],
    ids=["no endpoint", "no remote run identifier"],
)
def test_track_run_without_remote_run_id(
    foca_app: App,
    monkeypatch: pytest.MonkeyPatch,
    wes_endpoint: dict[str, Any] | None,
) -> None:
    """Runs whose document has no remote run identifier are not polled.

    The run log is fetched once, before polling starts; no status is
    requested.
    """
    calls: list[str] = []

    def _record(name: str) -> Any:
        """Get a fake client method that records its calls.

        Args:
            name: Name of the method.

        Returns:
            Fake method, returning an empty response.
        """

        def _method(self: WesClient, run_id: str, **kwargs: Any) -> dict[str, Any]:
            """Record the call.

            Args:
                self: WES client.
                run_id: Run identifier.
                **kwargs: Further arguments; ignored.

            Returns:
                Empty response.
            """
            calls.append(f"{name} {run_id}")
            return {}

        return _method

    monkeypatch.setattr(WesClient, "get_run", _record("get_run"))
    monkeypatch.setattr(WesClient, "get_run_status", _record("get_run_status"))
    with foca_app.app.app_context():
        get_collection(RUN_DB, RUN_COLLECTION).insert_one(
            {
                "task_id": TASK_ID,
                "run_log": {"run_id": "RUN001", "state": "UNKNOWN"},
                "wes_endpoint": wes_endpoint,
            }
        )
        task__track_run_progress.push_request(id=TASK_ID)
        try:
            with pytest.raises(WesEndpointProblem):
                task__track_run_progress.run(
                    remote_host="https://wes.example.org",
                    remote_base_path="/ga4gh/wes/v1",
                    remote_run_id=REMOTE_RUN_ID,
                    jwt=None,
                )
        finally:
            task__track_run_progress.pop_request()
    assert calls == [f"get_run {REMOTE_RUN_ID}"]
