"""Tests of the WES client (`pro_wes.ga4gh.wes.client_wes`).

Most tests replace the client's guarded HTTP client by a fake that records
the requests and returns canned responses, so no request leaves the test.
Others send requests to HTTP servers of the test on the loopback interface
(redirects, response sizes, timeouts, errors before and after sending, TLS
with the certificate authority of the smoke test's fake WES). The app is
created for every test, because the client reads its config (timeouts,
response sizes and the backend list) and so does the validation of run
requests (the limits of `custom.post_runs`).
"""

from collections.abc import Callable, Iterator
import email.parser
import email.policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import socket
import ssl
import threading
import time
from typing import Any

from foca.plugins.backends import clear_caches, registry
from foca.utils.http import (
    AddressNotAllowed,
    HttpResponse,
    RequestMaybeSent,
    RequestNotSent,
    ResponseTooLarge,
)
import pytest

from pro_wes.app import init_app
from pro_wes.exceptions import (
    EngineOutcomeUnknown,
    EngineRequestNotSent,
    EngineUnavailable,
)
from pro_wes.ga4gh.wes import client_wes
from pro_wes.ga4gh.wes.client_wes import CA_BUNDLE_ENV, WesClient
from pro_wes.ga4gh.wes.models import (
    ErrorResponse,
    RunId,
    RunListResponse,
    RunStatus,
    ServiceInfo,
    State,
)
from tests.conftest import BACKEND_URL
from tests.smoke import fake_wes

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
    "type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"},
    "organization": {"name": "Example", "url": "https://example.org"},
    "version": "1.0.0",
    "workflow_type_versions": {"CWL": {"workflow_type_version": ["v1.0"]}},
    "workflow_engine_versions": {"cwltool": {"workflow_engine_version": ["3.1"]}},
    "auth_instructions_url": "https://example.org/auth",
    "tags": {},
    "system_state_counts": {"RUNNING": 1},
}
ERROR: dict[str, Any] = {"msg": "not found", "status_code": 404}
# neither a valid response of any operation nor an error response
INVALID: dict[str, Any] = {"runs": "none", "run_id": 1, "state": "NONE"}
REGISTRY_URL = "https://registry.example.org/ga4gh/registry/v1"
DISCOVERED_URL = "https://found.example.org"


@pytest.fixture(autouse=True)
def _no_registry_caches() -> Iterator[None]:
    """Start and end each test without backends discovered earlier.

    Yields:
        Nothing.
    """
    clear_caches()
    yield
    clear_caches()


@pytest.fixture(autouse=True)
def _no_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify with the public certificate authorities, unless a test sets some.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
    """
    monkeypatch.delenv(CA_BUNDLE_ENV, raising=False)


class FakeHttp:
    """Guarded HTTP client that records requests and returns a canned response.

    Attributes:
        body: Body of every response, encoded as is if bytes, else as JSON.
        status: Status of every response.
        error: Exception raised by every request instead, if any.
        timeout: Timeout of the client, as set by the WES client.
        calls: Method, URL, headers, body and timeout of the requests, in
            order.
    """

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        error: Exception | None = None,
    ) -> None:
        """Class constructor.

        Args:
            body: Body of every response.
            status: Status of every response.
            error: Exception raised by every request instead, if any.
        """
        self.body = body
        self.status = status
        self.error = error
        self.timeout = 0.0
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> HttpResponse:
        """Record a request and answer it.

        Args:
            method: HTTP method.
            url: Request URL.
            headers: Headers.
            data: Body.

        Returns:
            Canned response.

        Raises:
            Exception: The client is set to raise an exception.
        """
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "data": data,
                "timeout": self.timeout,
            }
        )
        if self.error is not None:
            raise self.error
        content = (
            self.body
            if isinstance(self.body, bytes)
            else json.dumps(self.body).encode()
        )
        return HttpResponse(status=self.status, content=content)


def _client(http: FakeHttp, token: str | None = TOKEN) -> WesClient:
    """Get a client with a fake guarded HTTP client.

    Args:
        http: Fake guarded HTTP client.
        token: Bearer token of the client.

    Returns:
        WES client.
    """
    client = WesClient(host=HOST, token=token)
    client.http = http  # type: ignore[assignment]
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
VALIDATED_CALLS = pytest.mark.parametrize(
    "call",
    [operation[1] for operation in OPERATIONS],
    ids=[operation[0] for operation in OPERATIONS],
)
ALL_OPERATIONS = pytest.mark.parametrize(
    "call",
    [operation[1] for operation in OPERATIONS]
    + [lambda client: client.get_run(run_id=RUN_ID, timeout=1)],
    ids=[operation[0] for operation in OPERATIONS] + ["get_run"],
)


@pytest.mark.usefixtures("foca_app")
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
        # the API at the host itself (`api_path: /`)
        ("https://wes.example.org/", "", "https://wes.example.org"),
        ("https://wes.example.org/api", "/", "https://wes.example.org/api"),
    ],
)
def test_url(host: str, base_path: str, url: str) -> None:
    """The URL joins host and base path with exactly one slash."""
    assert WesClient(host=host, base_path=base_path).url == url


@pytest.mark.usefixtures("foca_app")
def test_set_token() -> None:
    """Tokens can be set after the client was created."""
    client = WesClient(host=HOST)
    assert client.token is None
    client.set_token(TOKEN)
    assert client.token == TOKEN


@pytest.mark.usefixtures("foca_app")
def test_headers() -> None:
    """Headers carry the accepted type, the token and the content type."""
    client = WesClient(host=HOST, token=TOKEN)
    assert client.headers() == {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }
    assert client.headers(content_type="text/csv") == {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "text/csv",
    }


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize("token", [None, ""], ids=["no token", "empty token"])
def test_headers_without_token(token: str | None) -> None:
    """Without a token, no `Authorization` header is sent (#166).

    Before, the literal header `Authorization: Bearer None` was sent, which
    WES instances that check tokens reject as an invalid token.

    Args:
        token: Missing or empty bearer token.
    """
    assert WesClient(host=HOST, token=token).headers() == {"Accept": "application/json"}


@pytest.mark.usefixtures("foca_app")
def test_headers_token_unset() -> None:
    """A token that was unset is no longer sent (#166)."""
    client = WesClient(host=HOST, token=TOKEN)
    client.set_token(None)
    assert client.headers() == {"Accept": "application/json"}


@pytest.mark.usefixtures("foca_app")
def test_headers_do_not_stick() -> None:
    """Headers of one request don't stick to later ones (#166)."""
    http = FakeHttp(body={"run_id": RUN_ID})
    client = _client(http)
    client.post_run(form_data=FORM_DATA)
    client.cancel_run(run_id=RUN_ID)
    assert "Content-Type" in http.calls[0]["headers"]
    assert http.calls[1]["headers"] == {
        "Accept": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }


@pytest.mark.usefixtures("foca_app")
@VALIDATED
def test_request_without_token(
    call: Call, method: str, url: str, body: dict[str, Any], model: type[Any]
) -> None:
    """Requests of a client without a token carry no `Authorization` (#166)."""
    http = FakeHttp(body=body)
    call(_client(http, token=None))
    assert len(http.calls) == 1
    assert "Authorization" not in http.calls[0]["headers"]


@pytest.mark.usefixtures("foca_app")
@VALIDATED
def test_valid_response(
    call: Call, method: str, url: str, body: dict[str, Any], model: type[Any]
) -> None:
    """Valid responses are returned as models; the timeout is passed on."""
    http = FakeHttp(body=body)
    response = call(_client(http))
    assert isinstance(response, model)
    assert response == model(**body)
    assert len(http.calls) == 1
    sent = http.calls[0]
    assert (sent["method"], sent["url"]) == (method, url)
    assert sent["timeout"] == 1
    assert sent["headers"]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.get_service_info(),
        lambda client: client.post_run(form_data=FORM_DATA),
        lambda client: client.get_runs(),
        lambda client: client.get_run(run_id=RUN_ID),
        lambda client: client.get_run_status(run_id=RUN_ID),
        lambda client: client.cancel_run(run_id=RUN_ID),
    ],
    ids=[
        "get_service_info",
        "post_run",
        "get_runs",
        "get_run",
        "get_run_status",
        "cancel_run",
    ],
)
def test_default_timeout(call: Call) -> None:
    """Requests without a timeout get the client's (`custom.defaults.timeout`)."""
    http = FakeHttp(body=ERROR, status=404)
    client = _client(http)
    client.timeout = 7
    call(client)
    assert http.calls[0]["timeout"] == 7


@pytest.mark.usefixtures("foca_app")
def test_timeout_from_config() -> None:
    """The default timeout is `custom.defaults.timeout` (2 in `config.yaml`)."""
    assert WesClient(host=HOST).timeout == 2


@pytest.mark.usefixtures("foca_app")
@VALIDATED_CALLS
def test_error_response(call: Call) -> None:
    """Error responses are returned as such."""
    # invalid for the operation, valid as an error response
    error = {**INVALID, **ERROR}
    response = call(_client(FakeHttp(body=error, status=404)))
    assert isinstance(response, ErrorResponse)
    assert response.status_code == 404


@pytest.mark.usefixtures("foca_app")
@VALIDATED_CALLS
@pytest.mark.parametrize("body", [INVALID, [INVALID], "RUNNING", None])
def test_invalid_response(call: Call, body: Any) -> None:
    """Responses that are neither valid nor errors raise `ValueError`."""
    with pytest.raises(ValueError, match="invalid response"):
        call(_client(FakeHttp(body=body)))


@pytest.mark.usefixtures("foca_app")
@ALL_OPERATIONS
@pytest.mark.parametrize(
    ("error", "expected", "reason"),
    [
        (RequestNotSent("refused"), EngineRequestNotSent, "connect_error"),
        (AddressNotAllowed("private"), EngineRequestNotSent, "not_public"),
        (
            RequestMaybeSent("timed out", reason="read_timeout"),
            EngineOutcomeUnknown,
            "read_timeout",
        ),
        (RequestMaybeSent("lost"), EngineOutcomeUnknown, "connection_lost"),
        (ResponseTooLarge("too large"), EngineOutcomeUnknown, "response_too_large"),
        (OSError("no CA bundle"), EngineRequestNotSent, "connect_error"),
    ],
    ids=["not sent", "address", "read timeout", "lost", "too large", "os error"],
)
def test_failed_request(
    call: Call,
    error: Exception,
    expected: type[EngineRequestNotSent] | type[EngineOutcomeUnknown],
    reason: str,
) -> None:
    """Failed requests tell whether the WES instance may have got them.

    Both are `EngineUnavailable`, as before.
    """
    with pytest.raises(expected) as info:
        call(_client(FakeHttp(error=error)))
    assert issubclass(expected, EngineUnavailable)
    assert isinstance(info.value, EngineRequestNotSent | EngineOutcomeUnknown)
    assert info.value.reason == reason


@pytest.mark.usefixtures("foca_app")
def test_response_too_large_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Responses larger than allowed are logged as errors, naming the setting."""
    client = _client(FakeHttp(error=ResponseTooLarge("too large")))
    with (
        caplog.at_level(logging.ERROR, logger=client_wes.__name__),
        pytest.raises(EngineOutcomeUnknown),
    ):
        client.get_run(run_id=RUN_ID)
    assert "custom.wes.max_response_size" in caplog.text


@pytest.mark.usefixtures("foca_app")
@ALL_OPERATIONS
@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_fails(call: Call, status: int) -> None:
    """Redirects fail the request instead of being followed.

    The WES instance got the request, so it may have processed it (e.g., a
    `303` after creating a run).
    """
    with pytest.raises(EngineOutcomeUnknown, match="redirect") as info:
        call(_client(FakeHttp(body={"run_id": RUN_ID}, status=status)))
    assert info.value.reason == "redirect"


@pytest.mark.usefixtures("foca_app")
@ALL_OPERATIONS
def test_no_json(call: Call) -> None:
    """Responses that are no JSON: the WES instance may have processed them."""
    with pytest.raises(EngineOutcomeUnknown, match="no JSON") as info:
        call(_client(FakeHttp(body=b"<html>")))
    assert info.value.reason == "invalid_json"


def _form(data: bytes, content_type: str) -> dict[str, tuple[str | None, bytes]]:
    """Parse a `multipart/form-data` body.

    Args:
        data: Body.
        content_type: Value of its `Content-Type` header.

    Returns:
        File name (if any) and content of each part, by field name.
    """
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + data
    )
    parts: dict[str, tuple[str | None, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(name, str)
        assert isinstance(payload, bytes)
        parts[name] = (part.get_filename(), payload)
    return parts


@pytest.mark.usefixtures("foca_app")
def test_post_run_multipart() -> None:
    """Run requests are sent as `multipart/form-data`, with their files."""
    http = FakeHttp(body={"run_id": RUN_ID})
    client = _client(http)
    files = {"workflow_attachment": ("a.cwl", b"cwlVersion: v1.0")}
    client.post_run(form_data=FORM_DATA, files=files)
    client.post_run(form_data=FORM_DATA)
    with_files, without = http.calls
    content_type = with_files["headers"]["Content-Type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert _form(with_files["data"], content_type) == {
        **{key: (None, value.encode()) for key, value in FORM_DATA.items()},
        "workflow_attachment": ("a.cwl", b"cwlVersion: v1.0"),
    }
    assert _form(without["data"], without["headers"]["Content-Type"]) == {
        key: (None, value.encode()) for key, value in FORM_DATA.items()
    }


@pytest.mark.usefixtures("foca_app")
def test_post_run_invalid_form_data() -> None:
    """Invalid form data raise `ValueError` without sending a request."""
    http = FakeHttp(body={"run_id": RUN_ID})
    form_data = {key: FORM_DATA[key] for key in FORM_DATA if key != "workflow_url"}
    with pytest.raises(ValueError, match="invalid form data"):
        _client(http).post_run(form_data=form_data)
    assert http.calls == []


@pytest.mark.usefixtures("foca_app")
def test_get_run() -> None:
    """Run logs are returned as sent, without validation."""
    # not a valid `RunLog` (e.g., as cwl-WES sends them)
    body = {"run_id": RUN_ID, "state": "RUNNING", "request": {"workflow_url": 1}}
    http = FakeHttp(body=body)
    assert _client(http).get_run(run_id=RUN_ID, timeout=1) == body
    assert [(call["method"], call["url"]) for call in http.calls] == [
        ("GET", f"{URL}/runs/{RUN_ID}")
    ]


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize("body", [[{"run_id": RUN_ID}], "RUNNING", None])
def test_get_run_no_object(body: Any) -> None:
    """Run logs that are no JSON object raise `EngineUnavailable`."""
    with pytest.raises(EngineUnavailable, match="no JSON object"):
        _client(FakeHttp(body=body)).get_run(run_id=RUN_ID)


@pytest.mark.usefixtures("foca_app")
def test_get_run_status_state() -> None:
    """Run states are parsed."""
    body = {"run_id": RUN_ID, "state": "COMPLETE"}
    response = _client(FakeHttp(body=body)).get_run_status(run_id=RUN_ID)
    assert isinstance(response, RunStatus)
    assert response.state == State.COMPLETE


@pytest.mark.usefixtures("foca_app")
def test_get_run_status_preempted() -> None:
    """The state `PREEMPTED` of WES 1.1.0 is parsed."""
    body = {"run_id": RUN_ID, "state": "PREEMPTED"}
    response = _client(FakeHttp(body=body)).get_run_status(run_id=RUN_ID)
    assert isinstance(response, RunStatus)
    assert response.state == State.PREEMPTED


@pytest.mark.usefixtures("foca_app")
def test_service_info_wes_1_0() -> None:
    """Service info of WES 1.0.x (engine versions as strings) is invalid (#122)."""
    body = {**SERVICE_INFO, "workflow_engine_versions": {"cwltool": "3.1"}}
    with pytest.raises(ValueError, match="invalid response"):
        _client(FakeHttp(body=body)).get_service_info()


# What requests may do (`http`, private addresses), by backend list


def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake a service registry that lists one verified WES instance.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
    """
    wes_type = {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"}
    service = {"id": "wes", "url": f"{DISCOVERED_URL}/ga4gh/wes/v1", "type": wes_type}
    verification = {"serviceId": "wes", "url": service["url"], "type": wes_type}

    def _fetch_json(url: str, *_args: Any, **_kwargs: Any) -> Any:
        """Answer a read of the registry.

        Args:
            url: URL read.
            *_args: Further arguments; ignored.
            **_kwargs: Further arguments; ignored.

        Returns:
            The verifications or the services.
        """
        return [verification] if url.endswith("/verifications") else [service]

    monkeypatch.setattr(registry, "fetch_json", _fetch_json)


@pytest.mark.parametrize(
    ("overlay", "host", "allow_http", "allow_private"),
    [
        ("", BACKEND_URL, False, True),
        ("", f"{BACKEND_URL}/", False, True),
        (
            "        url_schemes: [https, http]\n",
            BACKEND_URL,
            True,
            True,
        ),
        ("", "https://other.example.org", False, False),
        ("", "https://wes.example.org/other", False, False),
    ],
    ids=["configured", "not normalized", "http allowed", "unknown", "other path"],
)
def test_access_configured(
    add_overlay: Callable[[str], None],
    overlay: str,
    host: str,
    allow_http: bool,
    allow_private: bool,
) -> None:
    """Configured WES instances may have private addresses, others not."""
    add_overlay(
        "custom:\n  wes:\n    backends:\n"
        f"      - type: ga4gh-wes\n        url: {BACKEND_URL}\n" + overlay
    )
    app = init_app()
    with app.app.app_context():
        http = WesClient(host=host).http
    assert http.allow_http is allow_http
    assert http.allow_private_addresses is allow_private


@pytest.mark.parametrize("allow_private", [True, False])
def test_access_discovered(
    add_overlay: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    allow_private: bool,
) -> None:
    """Discovered WES instances: `https`, private addresses as the entry says."""
    _registry(monkeypatch)
    add_overlay(
        "server:\n  public_url: https://prowes.example.org\n"
        "custom:\n  wes:\n    backends:\n"
        "      - type: ga4gh-service-registry\n"
        f"        url: {REGISTRY_URL}\n"
        f"        allow_private_addresses: {str(allow_private).lower()}\n"
    )
    app = init_app()
    with app.app.app_context():
        http = WesClient(host=DISCOVERED_URL).http
    assert http.allow_http is False
    assert http.allow_private_addresses is allow_private


@pytest.mark.usefixtures("foca_app")
def test_max_response_size_from_config() -> None:
    """Responses are limited to `custom.wes.max_response_size` (16 MiB)."""
    assert WesClient(host=HOST).http.max_response_size == 16 * 1024 * 1024


# Requests to servers of the test, on the loopback interface


class _Handler(BaseHTTPRequestHandler):
    """Handler of the test server: answers as the server's `answer` says."""

    server: "_Server"

    def log_message(self, format: str, *args: Any) -> None:
        """Log nothing.

        Args:
            format: Format of the message.
            *args: Arguments of the message.
        """

    def _answer(self) -> None:
        """Answer the request."""
        self.server.paths.append(self.path)
        status, headers, body, delay = self.server.answer
        time.sleep(delay)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not self.server.drip:
            self.wfile.write(body)
            return
        try:
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(self.server.drip)
        except OSError:
            pass  # the client gave up

    do_GET = _answer
    do_POST = _answer


class _Server(ThreadingHTTPServer):
    """Test server.

    Attributes:
        answer: Status, headers, body and delay (in seconds) of every response.
        drip: Seconds between the bytes of each body, if they are sent one
            by one (`0`: all at once).
        paths: Paths of the requests received, in order.
    """

    daemon_threads = True
    answer: tuple[int, dict[str, str], bytes, float]
    drip: float = 0.0
    paths: list[str]


@pytest.fixture
def server() -> Iterator[_Server]:
    """Get an HTTP server on the loopback interface, answering with JSON.

    Yields:
        Server; stopped afterwards.
    """
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.answer = (200, {"Content-Type": "application/json"}, b"{}", 0.0)
    httpd.paths = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _local_app(add_overlay: Callable[[str], None], url: str, extra: str = "") -> Any:
    """Create the app with a configured WES instance at a local URL.

    Args:
        add_overlay: Function to merge config over the test overlay.
        url: URL of the WES instance (`http` allowed).
        extra: Further YAML of `custom.wes`, indented by 4 spaces.

    Returns:
        The app.
    """
    add_overlay(
        "custom:\n  wes:\n"
        + extra
        + "    backends:\n"
        + f"      - type: ga4gh-wes\n        url: {url}\n"
        + "        url_schemes: [https, http]\n"
    )
    return init_app()


def _url(server: _Server, scheme: str = "http") -> str:
    """Get the base URL of a test server.

    Args:
        server: Server.
        scheme: Scheme.

    Returns:
        URL, at the loopback address.
    """
    return f"{scheme}://127.0.0.1:{server.server_address[1]}"


def test_local_request(add_overlay: Callable[[str], None], server: _Server) -> None:
    """Configured WES instances may have private addresses, also with `http`."""
    server.answer = (
        200,
        {"Content-Type": "application/json"},
        b'{"run_id": "RUN001", "state": "RUNNING"}',
        0.0,
    )
    app = _local_app(add_overlay, _url(server))
    with app.app.app_context():
        response = WesClient(host=_url(server)).get_run_status(run_id=RUN_ID)
    assert response == RunStatus(run_id=RUN_ID, state=State.RUNNING)
    assert server.paths == [f"/ga4gh/wes/v1/runs/{RUN_ID}/status"]


def test_local_redirect_not_followed(
    add_overlay: Callable[[str], None], server: _Server
) -> None:
    """Redirects are not followed, e.g., to an internal service."""
    server.answer = (307, {"Location": "http://127.0.0.1:1/internal"}, b"", 0.0)
    app = _local_app(add_overlay, _url(server))
    with app.app.app_context(), pytest.raises(EngineUnavailable, match="redirect"):
        WesClient(host=_url(server)).cancel_run(run_id=RUN_ID)
    assert server.paths == [f"/ga4gh/wes/v1/runs/{RUN_ID}/cancel"]


def test_local_response_too_large(
    add_overlay: Callable[[str], None], server: _Server
) -> None:
    """Responses larger than `custom.wes.max_response_size` fail."""
    server.answer = (200, {}, b'{"run_id": "' + b"x" * 100 + b'"}', 0.0)
    app = _local_app(add_overlay, _url(server), "    max_response_size: 50\n")
    with app.app.app_context(), pytest.raises(EngineOutcomeUnknown) as info:
        WesClient(host=_url(server)).get_run(run_id=RUN_ID)
    assert info.value.reason == "response_too_large"


def test_local_timeout(add_overlay: Callable[[str], None], server: _Server) -> None:
    """Requests end at their timeout as a whole; the WES may have got them."""
    server.answer = (200, {}, b"{}", 2.0)
    app = _local_app(add_overlay, _url(server))
    with app.app.app_context():
        client = WesClient(host=_url(server))
        start = time.monotonic()
        with pytest.raises(EngineOutcomeUnknown) as info:
            client.post_run(form_data=FORM_DATA, timeout=0.5)
    assert time.monotonic() - start < 1.5
    assert info.value.reason == "read_timeout"


def test_local_timeout_as_a_whole(
    add_overlay: Callable[[str], None], server: _Server
) -> None:
    """Servers that keep sending, slowly, are cut off at the timeout, too.

    Each wait for data is shorter than the timeout; before, the timeout
    limited each wait only.
    """
    server.answer = (200, {}, b'{"run_id": "RUN001", "state": "RUNNING"}', 0.0)
    server.drip = 0.1  # about 4 seconds for the whole body
    app = _local_app(add_overlay, _url(server))
    with app.app.app_context():
        client = WesClient(host=_url(server))
        start = time.monotonic()
        with pytest.raises(EngineOutcomeUnknown) as info:
            client.get_run_status(run_id=RUN_ID, timeout=1)
    assert time.monotonic() - start < 2.5
    assert info.value.reason == "read_timeout"


def test_local_proxy_ignored(
    add_overlay: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    server: _Server,
) -> None:
    """Proxy settings of the environment are ignored."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    server.answer = (200, {}, b'{"run_id": "RUN001"}', 0.0)
    app = _local_app(add_overlay, _url(server))
    with app.app.app_context():
        response = WesClient(host=_url(server)).cancel_run(run_id=RUN_ID)
    assert response == RunId(run_id=RUN_ID)
    assert server.paths == [f"/ga4gh/wes/v1/runs/{RUN_ID}/cancel"]


def test_local_http_not_allowed(
    add_overlay: Callable[[str], None], server: _Server
) -> None:
    """The `http` URL of a WES instance configured with `https` only is refused."""
    url = _url(server, scheme="https")
    add_overlay(
        f"custom:\n  wes:\n    backends:\n      - type: ga4gh-wes\n        url: {url}\n"
    )
    app = init_app()
    with app.app.app_context(), pytest.raises(EngineRequestNotSent) as info:
        WesClient(host=_url(server)).get_run(run_id=RUN_ID)
    assert info.value.reason == "invalid_url"
    assert server.paths == []


def test_local_not_sent(add_overlay: Callable[[str], None]) -> None:
    """Refused connections certainly didn't reach the WES instance."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    app = _local_app(add_overlay, url)
    with app.app.app_context(), pytest.raises(EngineRequestNotSent):
        WesClient(host=url).post_run(form_data=FORM_DATA)


@pytest.mark.usefixtures("foca_app")
def test_unknown_private_address_refused(server: _Server) -> None:
    """WES instances that the list doesn't yield get public addresses only."""
    url = _url(server, scheme="https")
    with pytest.raises(EngineRequestNotSent) as info:
        WesClient(host=url).get_run(run_id=RUN_ID)
    assert info.value.reason == "not_public"
    assert server.paths == []


@pytest.mark.usefixtures("foca_app")
def test_unknown_http_refused(server: _Server) -> None:
    """WES instances that the list doesn't yield get `https` only."""
    with pytest.raises(EngineRequestNotSent) as info:
        WesClient(host=_url(server)).get_run(run_id=RUN_ID)
    assert info.value.reason == "invalid_url"
    assert server.paths == []


def test_discovered_private_address_refused(
    add_overlay: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovered WES instances get public addresses only, by default."""
    _registry(monkeypatch)
    add_overlay(
        "server:\n  public_url: https://prowes.example.org\n"
        "custom:\n  wes:\n    backends:\n"
        "      - type: ga4gh-service-registry\n"
        f"        url: {REGISTRY_URL}\n"
    )

    def _getaddrinfo(*_args: Any, **_kwargs: Any) -> list[Any]:
        """Resolve every host to the loopback address.

        Args:
            *_args: Arguments of `socket.getaddrinfo()`; ignored.
            **_kwargs: Keyword arguments of it; ignored.

        Returns:
            The loopback address.
        """
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    app = init_app()
    with app.app.app_context(), pytest.raises(EngineRequestNotSent) as info:
        WesClient(host=DISCOVERED_URL).get_run(run_id=RUN_ID)
    assert info.value.reason == "not_public"


@pytest.fixture
def fake_wes_tls(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """Serve the smoke test's fake WES over HTTPS on the loopback interface.

    With a certificate of its own certificate authority, for `localhost`.

    Args:
        tmp_path: Temporary directory of the test.

    Yields:
        Base URL of the fake WES, and the file of its certificate authority;
        stopped afterwards.
    """
    fake_wes.create_certificates(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.pem", tmp_path / "server-key.pem")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fake_wes.Handler)
    httpd.daemon_threads = True
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"https://localhost:{httpd.server_address[1]}", tmp_path / "ca.pem"
    httpd.shutdown()
    httpd.server_close()


def test_tls_with_ca_bundle(
    add_overlay: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    fake_wes_tls: tuple[str, Path],
) -> None:
    """WES instances are verified with the CA bundle of `REQUESTS_CA_BUNDLE`.

    The smoke test's fake WES gets a run, as in the Compose smoke test.
    """
    url, ca_bundle = fake_wes_tls
    monkeypatch.setenv(CA_BUNDLE_ENV, str(ca_bundle))
    add_overlay(
        f"custom:\n  wes:\n    backends:\n      - type: ga4gh-wes\n        url: {url}\n"
    )
    app = init_app()
    with app.app.app_context():
        client = WesClient(host=url)
        response = client.post_run(form_data=FORM_DATA)
        assert isinstance(response, RunId)
        status = client.get_run_status(run_id=response.run_id)
    assert isinstance(status, RunStatus)
    assert (
        fake_wes.RUNS[response.run_id]["request"]["workflow_url"]
        == (FORM_DATA["workflow_url"])
    )


def test_tls_without_ca_bundle(
    add_overlay: Callable[[str], None], fake_wes_tls: tuple[str, Path]
) -> None:
    """Without the CA bundle, certificates of other authorities are refused."""
    url, _ = fake_wes_tls
    add_overlay(
        f"custom:\n  wes:\n    backends:\n      - type: ga4gh-wes\n        url: {url}\n"
    )
    app = init_app()
    with app.app.app_context(), pytest.raises(EngineRequestNotSent) as info:
        WesClient(host=url).get_service_info()
    assert info.value.reason == "tls_error"


def test_tls_ca_bundle_missing(
    add_overlay: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    fake_wes_tls: tuple[str, Path],
    tmp_path: Path,
) -> None:
    """A CA bundle that doesn't exist fails requests before they are sent."""
    url, _ = fake_wes_tls
    monkeypatch.setenv(CA_BUNDLE_ENV, str(tmp_path / "missing.pem"))
    add_overlay(
        f"custom:\n  wes:\n    backends:\n      - type: ga4gh-wes\n        url: {url}\n"
    )
    app = init_app()
    with app.app.app_context(), pytest.raises(EngineRequestNotSent):
        WesClient(host=url).get_service_info()


def test_tls_ca_bundle_reused(
    add_overlay: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
    fake_wes_tls: tuple[str, Path],
) -> None:
    """The CA bundle is applied once per session, also after `close()`."""
    url, ca_bundle = fake_wes_tls
    monkeypatch.setenv(CA_BUNDLE_ENV, str(ca_bundle))
    add_overlay(
        f"custom:\n  wes:\n    backends:\n      - type: ga4gh-wes\n        url: {url}\n"
    )
    app = init_app()
    with app.app.app_context():
        client = WesClient(host=url)
        session = client.http.session
        send = session.get_adapter(url).send
        assert client.http.session.get_adapter(url).send is send
        assert isinstance(client.get_runs(), RunListResponse)
        client.http.close()
        assert client.http.session is not session
        assert isinstance(client.get_runs(), RunListResponse)
