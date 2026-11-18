"""Unit tests of the WES client (`pro_wes.ga4gh.wes.client_wes`).

The client's session is replaced by a fake that records the requests and
returns canned responses, so no request leaves the test. The app is created
for every test, because the validation of run requests reads its config (the
limits of `custom.post_runs`).
"""

from collections.abc import Callable
from typing import Any

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from pro_wes.exceptions import EngineUnavailable
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import (
    ErrorResponse,
    RunId,
    RunListResponse,
    RunStatus,
    ServiceInfo,
    State,
)

pytestmark = pytest.mark.usefixtures("foca_app")

HOST = "https://wes.example.org/api/"
URL = "https://wes.example.org/api/ga4gh/wes/v1"
RUN_ID = "RUN001"
TOKEN = "fake-token"  # noqa: S105 (a fake bearer token, no credential)
FORM_DATA = {
    "workflow_params": "{}",
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}
SERVICE_INFO: dict[str, Any] = {
    "id": "org.example.wes",
    "name": "WES",
    "type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.0.1"},
    "organization": {"name": "Example", "url": "https://example.org"},
    "version": "1.0.0",
    "workflow_type_versions": {"CWL": {"workflow_type_version": ["v1.0"]}},
    "workflow_engine_versions": {"cwltool": "3.1"},
    "auth_instructions_url": "https://example.org/auth",
    "tags": {},
    "system_state_counts": {"RUNNING": 1},
}
ERROR: dict[str, Any] = {"msg": "not found", "status_code": 404}
# neither a valid response of any operation nor an error response
INVALID: dict[str, Any] = {"runs": "none", "run_id": 1, "state": "NONE"}


class FakeResponse:
    """Response of the fake session.

    Attributes:
        body: Body returned by `json()`; raised if it is an exception.
    """

    def __init__(self, body: Any) -> None:
        """Class constructor.

        Args:
            body: Body returned by `json()`; raised if it is an exception.
        """
        self.body = body

    def json(self) -> Any:
        """Get the decoded body.

        Returns:
            Body.

        Raises:
            Exception: The body is an exception.
        """
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeSession:
    """Session that records requests and returns a canned response.

    Attributes:
        body: Body of every response; raised by `json()` if it is an
            exception other than a `RequestException`.
        error: Exception raised by every request instead, if any.
        calls: Method, URL and keyword arguments of the requests, in order.
        headers: Session headers.
    """

    def __init__(self, body: Any = None, error: Exception | None = None) -> None:
        """Class constructor.

        Args:
            body: Body of every response.
            error: Exception raised by every request instead, if any.
        """
        self.body = body
        self.error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}

    def _request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Record a request and answer it.

        Args:
            method: HTTP method.
            url: Request URL.
            **kwargs: Further request arguments.

        Returns:
            Canned response.

        Raises:
            Exception: The session is set to raise an exception.
        """
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.body)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Send a GET request.

        Args:
            url: Request URL.
            **kwargs: Further request arguments.

        Returns:
            Canned response.
        """
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Send a POST request.

        Args:
            url: Request URL.
            **kwargs: Further request arguments.

        Returns:
            Canned response.
        """
        return self._request("POST", url, **kwargs)


def _client(session: FakeSession, token: str | None = TOKEN) -> WesClient:
    """Get a client with a fake session.

    Args:
        session: Fake session.
        token: Bearer token of the client.

    Returns:
        WES client.
    """
    client = WesClient(host=HOST, token=token)
    client.session = session  # type: ignore[assignment]
    return client


# Operations: call, expected method and URL, valid response body and the type
# it is validated as
Call = Callable[[WesClient], Any]
OPERATIONS: list[tuple[str, Call, str, str, dict[str, Any], type[Any]]] = [
    (
        "get_service_info",
        lambda client: client.get_service_info(timeout=1),
        "GET",
        f"{URL}/service-info",
        SERVICE_INFO,
        ServiceInfo,
    ),
    (
        "post_run",
        lambda client: client.post_run(form_data=FORM_DATA, timeout=1),
        "POST",
        f"{URL}/runs",
        {"run_id": RUN_ID},
        RunId,
    ),
    (
        "get_runs",
        lambda client: client.get_runs(timeout=1),
        "GET",
        f"{URL}/runs",
        {"runs": [{"run_id": RUN_ID, "state": "RUNNING"}]},
        RunListResponse,
    ),
    (
        "get_run_status",
        lambda client: client.get_run_status(run_id=RUN_ID, timeout=1),
        "GET",
        f"{URL}/runs/{RUN_ID}/status",
        {"run_id": RUN_ID, "state": "RUNNING"},
        RunStatus,
    ),
    (
        "cancel_run",
        lambda client: client.cancel_run(run_id=RUN_ID, timeout=1),
        "POST",
        f"{URL}/runs/{RUN_ID}/cancel",
        {"run_id": RUN_ID},
        RunId,
    ),
]
VALIDATED = pytest.mark.parametrize(
    ("call", "method", "url", "body", "model"),
    [operation[1:] for operation in OPERATIONS],
    ids=[operation[0] for operation in OPERATIONS],
)
ALL_OPERATIONS = pytest.mark.parametrize(
    "call",
    [operation[1] for operation in OPERATIONS]
    + [lambda client: client.get_run(run_id=RUN_ID, timeout=1)],
    ids=[operation[0] for operation in OPERATIONS] + ["get_run"],
)


@pytest.mark.parametrize(
    ("host", "base_path", "url"),
    [
        (HOST, "/ga4gh/wes/v1", URL),
        (
            "https://wes.example.org",
            "ga4gh/wes/v1/",
            "https://wes.example.org/ga4gh/wes/v1",
        ),
        ("https://wes.example.org/", "/wes", "https://wes.example.org/wes"),
    ],
)
def test_url(host: str, base_path: str, url: str) -> None:
    """The URL joins host and base path with exactly one slash."""
    assert WesClient(host=host, base_path=base_path).url == url


def test_set_token() -> None:
    """Tokens can be set after the client was created."""
    client = WesClient(host=HOST)
    assert client.token is None
    client.set_token(TOKEN)
    assert client.token == TOKEN


def test_set_headers() -> None:
    """Headers carry the accepted type, the token and the content type."""
    session = FakeSession()
    client = _client(session)
    client.set_headers()
    assert session.headers == {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }
    client.set_headers(content_accept="text/plain", content_type="text/csv")
    assert session.headers == {
        "Accept": "text/plain",
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "text/csv",
    }


@VALIDATED
def test_valid_response(
    call: Call, method: str, url: str, body: dict[str, Any], model: type[Any]
) -> None:
    """Valid responses are returned as models; arguments are passed on."""
    session = FakeSession(body=body)
    response = call(_client(session))
    assert isinstance(response, model)
    assert len(session.calls) == 1
    sent_method, sent_url, kwargs = session.calls[0]
    assert (sent_method, sent_url) == (method, url)
    assert kwargs["timeout"] == 1
    assert session.headers["Authorization"] == f"Bearer {TOKEN}"


@VALIDATED
def test_error_response(
    call: Call, method: str, url: str, body: dict[str, Any], model: type[Any]
) -> None:
    """Error responses are returned as such."""
    # invalid for the operation, valid as an error response
    error = {**INVALID, **ERROR}
    response = call(_client(FakeSession(body=error)))
    assert isinstance(response, ErrorResponse)
    assert response.status_code == 404


@VALIDATED
def test_invalid_response(
    call: Call, method: str, url: str, body: dict[str, Any], model: type[Any]
) -> None:
    """Responses that are neither valid nor errors raise `ValueError`."""
    with pytest.raises(ValueError, match="invalid response"):
        call(_client(FakeSession(body=INVALID)))


@ALL_OPERATIONS
def test_service_unavailable(call: Call) -> None:
    """Failed requests raise `EngineUnavailable`."""
    session = FakeSession(error=RequestsConnectionError("refused"))
    with pytest.raises(EngineUnavailable):
        call(_client(session))


@ALL_OPERATIONS
def test_no_json(call: Call) -> None:
    """Responses that are no JSON raise `EngineUnavailable`."""
    with pytest.raises(EngineUnavailable):
        call(_client(FakeSession(body=ValueError("no JSON"))))


def test_post_run_form_data_and_files() -> None:
    """Form data and files are sent; files default to none."""
    session = FakeSession(body={"run_id": RUN_ID})
    client = _client(session)
    files = {"workflow_attachment": ("a.cwl", b"cwlVersion: v1.0")}
    client.post_run(form_data=FORM_DATA, files=files)
    client.post_run(form_data=FORM_DATA)
    assert [call[2] for call in session.calls] == [
        {"data": FORM_DATA, "files": files},
        {"data": FORM_DATA, "files": {}},
    ]


def test_post_run_invalid_form_data() -> None:
    """Invalid form data raise `ValueError` without sending a request."""
    session = FakeSession(body={"run_id": RUN_ID})
    form_data = {key: FORM_DATA[key] for key in FORM_DATA if key != "workflow_url"}
    with pytest.raises(ValueError, match="invalid form data"):
        _client(session).post_run(form_data=form_data)
    assert session.calls == []


def test_get_run() -> None:
    """Run logs are returned as sent, without validation."""
    # not a valid `RunLog` (e.g., as cwl-WES sends them)
    body = {"run_id": RUN_ID, "state": "RUNNING", "request": {"workflow_url": 1}}
    session = FakeSession(body=body)
    assert _client(session).get_run(run_id=RUN_ID, timeout=1) == body
    assert session.calls == [("GET", f"{URL}/runs/{RUN_ID}", {"timeout": 1})]


@pytest.mark.parametrize("body", [[{"run_id": RUN_ID}], "RUNNING", None])
def test_get_run_no_object(body: Any) -> None:
    """Run logs that are no JSON object raise `EngineUnavailable`."""
    with pytest.raises(EngineUnavailable, match="no JSON object"):
        _client(FakeSession(body=body)).get_run(run_id=RUN_ID)


def test_get_run_status_state() -> None:
    """Run states are parsed."""
    body = {"run_id": RUN_ID, "state": "COMPLETE"}
    response = _client(FakeSession(body=body)).get_run_status(run_id=RUN_ID)
    assert isinstance(response, RunStatus)
    assert response.state == State.COMPLETE
