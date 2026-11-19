"""Tests of the authentication of clients and the owners of runs.

The app trusts the local identity provider of FOCA's fixture `issuer` (cf.
`tests/conftest.py`). All operations but `GET /service-info` require its
access tokens; runs belong to the namespaced subject of the token that
created them.
"""

from typing import Any

from connexion import App
from foca.testing import Client, request_context
from foca.testing.client import Response
from foca.testing.issuer import Issuer
from pymongo import MongoClient
import pytest

from pro_wes.ga4gh.wes import controllers
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import RunId
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import AUDIENCE, OWNER, USER, TokenClient

BASE_PATH = "/ga4gh/wes/v1"
RUN_REQUEST: dict[str, str] = {
    "workflow_params": '{"input": "value"}',
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}
# Operations on runs, with a run that doesn't exist
OPERATIONS = [
    ("get", "/runs"),
    ("post", "/runs"),
    ("get", "/runs/NONE01"),
    ("get", "/runs/NONE01/status"),
    ("post", "/runs/NONE01/cancel"),
    ("get", "/runs/NONE01/tasks"),
    ("get", "/runs/NONE01/tasks/TASK01"),
]


@pytest.fixture
def forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept runs at the WES instance and don't track them.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
    """
    monkeypatch.setattr(
        WesClient, "post_run", lambda *_args, **_kwargs: RunId(run_id="REMOTE1")
    )
    monkeypatch.setattr(
        task__track_run_progress, "apply_async", lambda *_args, **_kwargs: None
    )


def _assert_unauthorized(response: Response) -> None:
    """Check a rejection for lack of a valid token.

    Args:
        response: Response.
    """
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].lower().startswith("bearer")
    assert response.json()["status"] == 401


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_no_token(foca_client: Client, method: str, path: str) -> None:
    """Operations on runs without token get `401`, with a Bearer challenge."""
    response = foca_client.request(
        method, f"{BASE_PATH}{path}", headers={"Authorization": ""}
    )
    _assert_unauthorized(response)


@pytest.mark.parametrize(
    "token_args",
    [
        pytest.param({"expires_in": -60}, id="expired"),
        pytest.param({"aud": "other-app"}, id="other audience"),
        pytest.param({"typ": "JWT"}, id="no access token"),
        pytest.param({"iss": "https://untrusted.example.org/"}, id="other issuer"),
    ],
)
def test_invalid_token(
    foca_client: Client, issuer: Issuer, token_args: dict[str, Any]
) -> None:
    """Expired, foreign and non-access tokens get `401`."""
    token = issuer.token(**{"sub": USER, "aud": AUDIENCE, **token_args})
    response = foca_client.get(
        f"{BASE_PATH}/runs", headers={"Authorization": f"Bearer {token}"}
    )
    _assert_unauthorized(response)
    assert 'error="invalid_token"' in response.headers["WWW-Authenticate"]


def test_malformed_token(foca_client: Client) -> None:
    """Tokens that aren't JWTs get `401`."""
    response = foca_client.get(
        f"{BASE_PATH}/runs", headers={"Authorization": "Bearer not-a-token"}
    )
    _assert_unauthorized(response)


@pytest.mark.parametrize(
    "path", ["/service-info", "/openapi.json", "/ui/"], ids=["info", "spec", "ui"]
)
def test_public(foca_client: Client, path: str) -> None:
    """The service info, the specification and the Swagger UI are public."""
    response = foca_client.get(f"{BASE_PATH}{path}", headers={"Authorization": ""})
    assert response.status_code == 200


def test_valid_token(foca_client: Client) -> None:
    """Valid access tokens are accepted."""
    response = foca_client.get(f"{BASE_PATH}/runs")
    assert response.status_code == 200
    assert response.json()["runs"] == []


@pytest.mark.usefixtures("forwarded")
def test_runs_of_owner_only(
    foca_app: App,
    foca_client: Client,
    issuer: Issuer,
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Runs belong to their creator, which alone lists, reads and cancels them."""
    response = foca_client.post(f"{BASE_PATH}/runs", data=RUN_REQUEST)
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    assert stored["user_id"] == OWNER
    listed = foca_client.get(f"{BASE_PATH}/runs").json()["runs"]
    assert [run["run_id"] for run in listed] == [run_id]
    assert foca_client.get(f"{BASE_PATH}/runs/{run_id}/status").status_code == 200

    # another user of the same issuer
    with TokenClient(foca_app, issuer.token(sub="bob", aud=AUDIENCE)) as other:
        assert other.get(f"{BASE_PATH}/runs").json()["runs"] == []
        for method, path in [
            ("get", f"/runs/{run_id}"),
            ("get", f"/runs/{run_id}/status"),
            ("post", f"/runs/{run_id}/cancel"),
        ]:
            response = other.request(method, f"{BASE_PATH}{path}")
            assert response.status_code == 403, path


@pytest.mark.usefixtures("forwarded")
def test_user_id_of_request_refused(
    foca_client: Client, mongodb: MongoClient[dict[str, Any]]
) -> None:
    """Requests can't name the owner: a `user_id` is refused (`400`)."""
    response = foca_client.get(f"{BASE_PATH}/runs", params={"user_id": "bob"})
    assert response.status_code == 400
    response = foca_client.post(
        f"{BASE_PATH}/runs", data={**RUN_REQUEST, "user_id": "bob"}
    )
    assert response.status_code == 400
    assert mongodb[RUN_DB][RUN_COLLECTION].count_documents({}) == 0


@pytest.mark.parametrize("token_info", [{"claims": {"sub": USER}}, None])
def test_set_owner_replaces_user_id(
    foca_app: App, issuer: Issuer, token_info: dict[str, Any] | None
) -> None:
    """The owner of the caller replaces any `user_id`, or removes it."""
    if token_info is not None:
        token_info["claims"]["iss"] = issuer.url
    kwargs: dict[str, Any] = {"user_id": "bob", "page_size": 1}
    with request_context(token_info=token_info, app=foca_app):
        controllers._set_owner(kwargs)
    expected: dict[str, Any] = {"page_size": 1}
    if token_info is not None:
        expected["user_id"] = OWNER
    assert kwargs == expected
