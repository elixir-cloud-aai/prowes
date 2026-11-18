"""Tests of the controller of `POST /runs/{run_id}/cancel` (`CancelRun`).

The controller only hands the request to
:py:meth:`pro_wes.ga4gh.wes.workflow_runs.WorkflowRuns.cancel_run`, which is
replaced by a fake here; its behaviour is tested with the other operations
on workflow runs.
"""

from typing import Any

from foca.testing import Client
import pytest

from pro_wes.ga4gh.wes.workflow_runs import WorkflowRuns

URL = "/ga4gh/wes/v1/runs/{run_id}/cancel"
RUN_ID = "RUN001"
# response of the fake, distinct from the request, so that it is passed on
RESPONSE = {"run_id": "RUN001-CANCELED"}


def test_cancel_run(foca_client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests are handed on with their run identifier; responses passed on."""
    calls: list[str] = []

    def _cancel_run(self: WorkflowRuns, run_id: str, **kwargs: Any) -> dict[str, str]:
        """Record the run identifier and accept the cancellation.

        Args:
            self: Operations on workflow runs.
            run_id: Run identifier.
            **kwargs: Further arguments of the request; ignored.

        Returns:
            Canned response.
        """
        calls.append(run_id)
        return RESPONSE

    monkeypatch.setattr(WorkflowRuns, "cancel_run", _cancel_run)
    response = foca_client.post(URL.format(run_id=RUN_ID))
    assert response.status_code == 200
    assert response.json() == RESPONSE
    assert calls == [RUN_ID]
