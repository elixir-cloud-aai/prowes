"""Tests of retries of requests to WES instances, and of failover.

The WES client (:py:class:`pro_wes.ga4gh.wes.client_wes.WesClient`) tries
requests again, as `custom.wes.retry` says: reads after any transient
failure, requests that change something (`POST`) only if the WES instance
certainly didn't get them. `POST /runs` forwards a run to the next WES
instance that can run it only if the current one certainly didn't get it
(`custom.post_runs.forward_timeout`). The waits between tries are recorded
instead of slept.
"""

from collections.abc import Callable
import json
import logging
from types import SimpleNamespace
from typing import Any

from foca.testing import Client
from foca.utils.http import (
    AddressNotAllowed,
    HttpResponse,
    InvalidRequestURL,
    RequestMaybeSent,
    RequestNotSent,
    ResponseTooLarge,
)
from pydantic import ValidationError
from pymongo import MongoClient
import pytest

from pro_wes.config_models import CustomConfig, PostRuns, Retry
from pro_wes.exceptions import (
    EngineOutcomeUnknown,
    EngineRequestNotSent,
)
from pro_wes.ga4gh.wes import client_wes, routing, workflow_runs
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import ErrorResponse, RunId, RunStatus
from pro_wes.ga4gh.wes.routing import backend_versions
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import BACKEND_URL, TokenClient

HOST = "https://wes.example.org"
URL = f"{HOST}/ga4gh/wes/v1"
RUN_ID = "RUN001"
FORM_DATA = {
    "workflow_params": "{}",
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}
STATUS = {"run_id": RUN_ID, "state": "RUNNING"}
BOUNDARY = "prowes-test-boundary"
SECOND_URL = "https://second.example.org"
THIRD_URL = "https://third.example.org"


class Answer:
    """Answer of a fake request: an error to raise, or a response.

    Attributes:
        error: Error raised by the request, if any.
        status: Status of the response.
        body: Body of the response (JSON-encoded).
    """

    def __init__(
        self, error: Exception | None = None, status: int = 200, body: Any = None
    ) -> None:
        """Create the answer.

        Args:
            error: Error raised by the request, if any.
            status: Status of the response.
            body: Body of the response.
        """
        self.error = error
        self.status = status
        self.body = body


class ScriptedHttp:
    """Guarded HTTP client that answers requests in turn, as scripted.

    Attributes:
        answers: Answers of the requests, in order; the last one repeats.
        timeout: Timeout of the client, as set by the WES client.
        calls: Methods and URLs of the requests, in order.
    """

    def __init__(self, *answers: Answer) -> None:
        """Create the client.

        Args:
            *answers: Answers of the requests, in order.
        """
        self.answers = list(answers)
        self.timeout = 0.0
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **_kwargs: Any) -> HttpResponse:
        """Record a request and answer it.

        Args:
            method: HTTP method.
            url: Request URL.
            **_kwargs: Headers and body; ignored.

        Returns:
            The scripted response.

        Raises:
            Exception: The scripted error.
        """
        self.calls.append((method, url))
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if answer.error is not None:
            raise answer.error
        return HttpResponse(
            status=answer.status, content=json.dumps(answer.body).encode()
        )


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the waits of the client between tries instead of sleeping.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Seconds of the waits, in order.
    """
    recorded: list[float] = []
    monkeypatch.setattr(client_wes, "_sleep", recorded.append)
    return recorded


def _client(http: ScriptedHttp, retry: Retry | None = None) -> WesClient:
    """Get a WES client with a scripted guarded HTTP client.

    Args:
        http: Scripted guarded HTTP client.
        retry: Retries of the client; by default those of the config.

    Returns:
        WES client.
    """
    client = WesClient(host=HOST, retry=retry)
    client.http = http  # type: ignore[assignment]
    return client


RETRY = Retry(attempts=3, backoff=1, max_backoff=10)

NOT_SENT = RequestNotSent("refused")
MAYBE_SENT = RequestMaybeSent("timed out", reason="read_timeout")


# config


def test_retry_defaults() -> None:
    """Requests are tried 3 times, after 1 and 2 seconds; runs for 30 s."""
    config = CustomConfig()
    assert config.wes.retry == Retry(attempts=3, backoff=1, max_backoff=10)
    assert config.post_runs.forward_timeout == 30


@pytest.mark.parametrize(
    "values",
    [
        {"attempts": 0},
        {"attempts": 101},
        {"backoff": -1},
        {"backoff": float("inf")},
        {"max_backoff": -1},
        {"max_backoff": 3601},
        {"other": 1},
    ],
    ids=[
        "no attempts",
        "many attempts",
        "negative",
        "infinite",
        "negative max",
        "large max",
        "extra",
    ],
)
def test_retry_invalid(values: dict[str, Any]) -> None:
    """Invalid retry settings stop the app."""
    with pytest.raises(ValidationError):
        Retry(**values)


@pytest.mark.parametrize("value", [0, -1, float("inf")])
def test_forward_timeout_invalid(value: float) -> None:
    """The time for forwarding a run is positive and finite."""
    with pytest.raises(ValidationError):
        PostRuns(forward_timeout=value)


@pytest.mark.usefixtures("foca_app")
def test_retry_from_config() -> None:
    """Clients retry as `custom.wes.retry` says, unless told otherwise."""
    assert WesClient(host=HOST).retry == CustomConfig().wes.retry.model_copy(
        update={"backoff": 0}
    )
    assert WesClient(host=HOST, retry=RETRY).retry == RETRY


# reads


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize(
    "first",
    [
        Answer(error=NOT_SENT),
        Answer(error=RequestNotSent("no address", reason="dns_error")),
        Answer(error=RequestNotSent("TLS", reason="tls_error")),
        Answer(error=MAYBE_SENT),
        Answer(error=RequestMaybeSent("lost")),
        Answer(error=OSError("CA bundle unreadable")),
        Answer(status=502, body={"msg": "bad gateway", "status_code": 502}),
        Answer(status=503, body={"msg": "unavailable", "status_code": 503}),
        Answer(status=504, body={"msg": "gateway timeout", "status_code": 504}),
    ],
    ids=[
        "refused",
        "dns",
        "tls",
        "read timeout",
        "lost",
        "os error",
        "502",
        "503",
        "504",
    ],
)
def test_read_retried(waits: list[float], first: Answer) -> None:
    """Reads are tried again after transient failures, after a wait."""
    http = ScriptedHttp(first, Answer(body=STATUS))
    response = _client(http, RETRY).get_run_status(run_id=RUN_ID, timeout=1)
    assert isinstance(response, RunStatus)
    assert http.calls == [("GET", f"{URL}/runs/{RUN_ID}/status")] * 2
    assert waits == [1]


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize(
    "answer",
    [
        Answer(error=AddressNotAllowed("private")),
        Answer(error=InvalidRequestURL("scheme")),
        Answer(error=RequestNotSent("bad header", reason="invalid_header")),
        Answer(error=ResponseTooLarge("too large")),
        Answer(error=RequestMaybeSent("lost", reason="redirect")),
        Answer(status=301, body={}),
        Answer(status=500, body={"msg": "error", "status_code": 500}),
        Answer(status=404, body={"msg": "not found", "status_code": 404}),
    ],
    ids=[
        "not public",
        "invalid url",
        "invalid header",
        "too large",
        "redirect error",
        "redirect",
        "500",
        "404",
    ],
)
def test_read_not_retried(waits: list[float], answer: Answer) -> None:
    """Failures that another try wouldn't change are not tried again."""
    http = ScriptedHttp(answer, Answer(body=STATUS))
    try:
        response = _client(http, RETRY).get_run_status(run_id=RUN_ID, timeout=1)
    except (EngineOutcomeUnknown, EngineRequestNotSent):
        pass
    else:
        assert isinstance(response, ErrorResponse)
    assert len(http.calls) == 1
    assert not waits


@pytest.mark.usefixtures("foca_app")
def test_read_attempts_exhausted(
    waits: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    """After the last try, its error is raised; each retry is logged."""
    http = ScriptedHttp(Answer(error=NOT_SENT))
    with caplog.at_level(logging.WARNING), pytest.raises(EngineRequestNotSent):
        _client(http, RETRY).get_run(run_id=RUN_ID, timeout=1)
    assert len(http.calls) == 3
    assert waits == [1, 2]
    retries = [r for r in caplog.records if "trying again" in r.getMessage()]
    assert len(retries) == 2
    assert "connect_error" in retries[0].getMessage()


@pytest.mark.usefixtures("foca_app")
def test_read_last_status_returned(waits: list[float]) -> None:
    """After the last try, a response of a retried status is returned."""
    http = ScriptedHttp(Answer(status=503, body={"msg": "no", "status_code": 503}))
    response = _client(http, RETRY).get_run_status(run_id=RUN_ID, timeout=1)
    assert isinstance(response, ErrorResponse)
    assert response.status_code == 503
    assert len(http.calls) == 3


@pytest.mark.usefixtures("foca_app")
def test_backoff_capped(waits: list[float]) -> None:
    """Waits double from `backoff`, up to `max_backoff` seconds."""
    http = ScriptedHttp(Answer(error=NOT_SENT))
    retry = Retry(attempts=6, backoff=1, max_backoff=5)
    with pytest.raises(EngineRequestNotSent):
        _client(http, retry).get_runs(timeout=1)
    assert waits == [1, 2, 4, 5, 5]


@pytest.mark.usefixtures("foca_app")
def test_single_attempt(waits: list[float]) -> None:
    """With `attempts: 1`, requests are not tried again."""
    http = ScriptedHttp(Answer(error=NOT_SENT))
    with pytest.raises(EngineRequestNotSent):
        _client(http, Retry(attempts=1)).get_service_info(timeout=1)
    assert len(http.calls) == 1
    assert not waits


# requests that change something


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.post_run(form_data=FORM_DATA, timeout=1),
        lambda client: client.cancel_run(run_id=RUN_ID, timeout=1),
    ],
    ids=["post_run", "cancel_run"],
)
def test_post_retried_if_not_sent(
    waits: list[float], call: Callable[[WesClient], Any]
) -> None:
    """`POST` requests are tried again if they certainly weren't received."""
    http = ScriptedHttp(Answer(error=NOT_SENT), Answer(body={"run_id": RUN_ID}))
    assert call(_client(http, RETRY)) == RunId(run_id=RUN_ID)
    assert len(http.calls) == 2
    assert waits == [1]


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.post_run(form_data=FORM_DATA, timeout=1),
        lambda client: client.cancel_run(run_id=RUN_ID, timeout=1),
    ],
    ids=["post_run", "cancel_run"],
)
@pytest.mark.parametrize(
    "answer",
    [
        Answer(error=MAYBE_SENT),
        Answer(error=RequestMaybeSent("lost")),
        Answer(status=502, body={"msg": "bad gateway", "status_code": 502}),
        Answer(status=503, body={"msg": "unavailable", "status_code": 503}),
    ],
    ids=["read timeout", "lost", "502", "503"],
)
def test_post_not_retried_if_maybe_sent(
    waits: list[float], call: Callable[[WesClient], Any], answer: Answer
) -> None:
    """`POST` requests that the WES instance may have got are never repeated.

    Also not after server errors: a gateway in front of the WES instance may
    answer them after the WES instance got the request.
    """
    http = ScriptedHttp(answer, Answer(body={"run_id": RUN_ID}))
    try:
        response = call(_client(http, RETRY))
    except EngineOutcomeUnknown:
        pass
    else:
        assert isinstance(response, ErrorResponse)
    assert len(http.calls) == 1
    assert not waits


def _fake_clock(monkeypatch: pytest.MonkeyPatch, module: Any, *times: float) -> None:
    """Replace `time.monotonic()` of a module with a scripted clock.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
        module: Module whose `time` is replaced.
        *times: Values returned in turn; the last one repeats.
    """
    values = list(times)

    def _monotonic() -> float:
        """Get the next scripted time.

        Returns:
            The time.
        """
        return values.pop(0) if len(values) > 1 else values[0]

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=_monotonic))


@pytest.mark.usefixtures("foca_app")
@pytest.mark.parametrize(
    ("deadline", "calls", "expected_waits"),
    [(0, 1, []), (1, 1, []), (1.5, 2, [1]), (None, 3, [1, 2])],
    ids=["passed", "wait ends at deadline", "wait ends before", "no deadline"],
)
def test_no_try_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
    waits: list[float],
    deadline: float | None,
    calls: int,
    expected_waits: list[float],
) -> None:
    """No retry starts whose wait would end at or after the deadline.

    The first try is always made. The clock stands still at 0.
    """
    _fake_clock(monkeypatch, client_wes, 0)
    http = ScriptedHttp(Answer(error=NOT_SENT))
    with pytest.raises(EngineRequestNotSent):
        _client(http, RETRY).post_run(form_data=FORM_DATA, timeout=1, deadline=deadline)
    assert len(http.calls) == calls
    assert waits == expected_waits


@pytest.mark.usefixtures("foca_app")
def test_service_info_probe_not_retried(
    monkeypatch: pytest.MonkeyPatch, waits: list[float]
) -> None:
    """Probes of the WES version are tried once: their failures are cached."""
    attempts: list[int] = []
    original = WesClient.__init__

    def _init(self: WesClient, *args: Any, **kwargs: Any) -> None:
        """Record the attempts of the client.

        Args:
            self: WES client.
            *args: Positional arguments of the client.
            **kwargs: Keyword arguments of the client.
        """
        original(self, *args, **kwargs)
        attempts.append(self.retry.attempts)
        self.http = ScriptedHttp(Answer(error=NOT_SENT))  # type: ignore[assignment]

    monkeypatch.setattr(WesClient, "__init__", _init)
    backend = routing.get_backend_list().expand()[0]
    with pytest.raises(EngineRequestNotSent):
        routing.read_service_info(backend, timeout=1)
    assert attempts == [1]
    assert not waits


# failover of new runs


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


def _post_run(client: Client) -> Any:
    """Send `POST /runs` with a run request.

    Args:
        client: Test client of the app.

    Returns:
        Response.
    """
    return client.post(
        "/ga4gh/wes/v1/runs",
        content=_multipart(FORM_DATA),
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the tracking tasks of runs instead of queuing them.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Keyword arguments of the queued tasks, in order.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        task__track_run_progress,
        "apply_async",
        lambda _args, kwargs, **_options: calls.append(kwargs),
    )
    return calls


Outcome = Exception | ErrorResponse | None


@pytest.fixture
def remotes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake the WES instances: each answers `POST /runs` as scripted.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Outcomes by base URL (`outcomes`; an error to raise, an error
        response, or `None` to accept the run), and the base URLs, form
        data and deadlines of the requests, in order (`sent`).
    """
    state: dict[str, Any] = {"outcomes": {}, "sent": []}

    def _post_run(
        self: WesClient,
        form_data: dict[str, str],
        deadline: float | None = None,
        **_kwargs: Any,
    ) -> ErrorResponse | RunId:
        """Answer a run request as scripted for the WES instance.

        Args:
            self: WES client.
            form_data: Form data of the run request.
            deadline: Deadline of the tries.
            **_kwargs: Further arguments; ignored.

        Returns:
            Identifier of the run, or the scripted error response.

        Raises:
            Exception: The scripted error.
        """
        host = self.url.removesuffix("/ga4gh/wes/v1")
        state["sent"].append((host, form_data, deadline))
        outcome = state["outcomes"].get(host)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, ErrorResponse):
            return outcome
        return RunId(run_id=f"REMOTE-{host}")

    monkeypatch.setattr(WesClient, "post_run", _post_run)
    return state


@pytest.fixture
def three_backends(
    add_overlay: Callable[[str], None], token: str
) -> Callable[[str], TokenClient]:
    """Get a function creating a client of an app with three WES instances.

    The first two are of WES 1.1, the third of WES 1.0.

    Args:
        add_overlay: Function merging further config over the test overlay.
        token: Access token of the test user.

    Returns:
        Function taking further YAML config of `custom`, and returning a
        client of the app (to be closed by the caller).
    """
    backend_versions.clear()

    def _create(extra: str = "") -> TokenClient:
        """Create the app and its client.

        Args:
            extra: Further YAML config, indented as keys of `custom`.

        Returns:
            Client of the app.
        """
        from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

        add_overlay(
            "custom:\n  wes:\n    backends:\n"
            f"      - type: ga4gh-wes\n        url: {BACKEND_URL}\n"
            '        labels: {wes.version: "1.1"}\n'
            f"      - type: ga4gh-wes\n        url: {SECOND_URL}\n"
            '        labels: {wes.version: "1.1"}\n'
            f"      - type: ga4gh-wes\n        url: {THIRD_URL}\n"
            '        labels: {wes.version: "1.0"}\n' + extra
        )
        return TokenClient(init_app(), token)

    return _create


def _stored(mongodb: MongoClient[dict[str, Any]]) -> dict[str, Any]:
    """Get the only stored run.

    Args:
        mongodb: Database of the test.

    Returns:
        Its document.
    """
    documents = list(mongodb[RUN_DB][RUN_COLLECTION].find({}))
    assert len(documents) == 1
    return documents[0]


def test_failover_if_not_sent(
    monkeypatch: pytest.MonkeyPatch,
    three_backends: Callable[[str], TokenClient],
    remotes: dict[str, Any],
    queued: list[dict[str, Any]],
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Runs go to the next WES instance if one certainly didn't get them.

    Each WES instance gets the run in its WES version (marked by a fake
    downgrade). The run is stored with the WES instance that accepted it, in
    its WES version, and tracked there.
    """
    monkeypatch.setattr(
        workflow_runs,
        "downgrade_run_request",
        lambda fields, version: {**fields, "version": str(version)},
    )
    remotes["outcomes"][BACKEND_URL] = EngineRequestNotSent("refused")
    remotes["outcomes"][SECOND_URL] = EngineRequestNotSent("refused")
    with three_backends("") as client:
        response = _post_run(client)
    assert response.status_code == 200
    assert [host for host, _, _ in remotes["sent"]] == [
        BACKEND_URL,
        SECOND_URL,
        THIRD_URL,
    ]
    stored = _stored(mongodb)
    assert stored["wes_endpoint"]["host"] == THIRD_URL
    assert stored["wes_endpoint"]["wes_version"] == "1.0"
    assert stored["wes_endpoint"]["run_id"] == f"REMOTE-{THIRD_URL}"
    assert stored["run_log"]["state"] != "SYSTEM_ERROR"
    assert queued[0]["remote_host"] == THIRD_URL
    assert [form["version"] for _, form, _ in remotes["sent"]] == [
        "1.1",
        "1.1",
        "1.0",
    ]
    deadlines = {deadline for _, _, deadline in remotes["sent"]}
    assert len(deadlines) == 1
    assert None not in deadlines


@pytest.mark.parametrize(
    "outcome",
    [
        EngineOutcomeUnknown("timed out", reason="read_timeout"),
        ErrorResponse(msg="unavailable", status_code=503),
        ErrorResponse(msg="bad request", status_code=400),
    ],
    ids=["outcome unknown", "503", "400"],
)
def test_no_failover_if_maybe_accepted(
    three_backends: Callable[[str], TokenClient],
    remotes: dict[str, Any],
    queued: list[dict[str, Any]],
    mongodb: MongoClient[dict[str, Any]],
    outcome: Outcome,
) -> None:
    """Runs that a WES instance may have got, or answered, go nowhere else."""
    remotes["outcomes"][BACKEND_URL] = outcome
    with three_backends("") as client:
        response = _post_run(client)
    assert response.status_code in (400, 500)
    assert [host for host, _, _ in remotes["sent"]] == [BACKEND_URL]
    stored = _stored(mongodb)
    assert stored["wes_endpoint"]["host"] == BACKEND_URL
    assert stored["run_log"]["state"] == "SYSTEM_ERROR"
    assert not queued


def test_no_backend_accepts(
    three_backends: Callable[[str], TokenClient],
    remotes: dict[str, Any],
    queued: list[dict[str, Any]],
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Runs that no WES instance got fail with `SYSTEM_ERROR`."""
    for url in (BACKEND_URL, SECOND_URL, THIRD_URL):
        remotes["outcomes"][url] = EngineRequestNotSent("refused")
    with three_backends("") as client:
        response = _post_run(client)
    assert response.status_code == 500
    assert len(remotes["sent"]) == 3
    assert _stored(mongodb)["run_log"]["state"] == "SYSTEM_ERROR"
    assert not queued


def test_failover_only_to_eligible(
    three_backends: Callable[[str], TokenClient],
    remotes: dict[str, Any],
    queued: list[dict[str, Any]],
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Runs go only to WES instances that can run them unchanged."""
    remotes["outcomes"][BACKEND_URL] = EngineRequestNotSent("refused")
    remotes["outcomes"][SECOND_URL] = EngineRequestNotSent("refused")
    with three_backends("") as client:
        response = client.post(
            "/ga4gh/wes/v1/runs",
            content=_multipart({**FORM_DATA, "workflow_engine": "cwltool"}),
            headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
        )
    assert response.status_code == 500
    assert [host for host, _, _ in remotes["sent"]] == [BACKEND_URL, SECOND_URL]
    assert _stored(mongodb)["run_log"]["state"] == "SYSTEM_ERROR"
    assert not queued


@pytest.mark.parametrize(
    ("elapsed", "hosts"),
    [(29.9, [BACKEND_URL, SECOND_URL]), (30, [BACKEND_URL])],
    ids=["before", "passed"],
)
def test_failover_within_forward_timeout(
    monkeypatch: pytest.MonkeyPatch,
    three_backends: Callable[[str], TokenClient],
    remotes: dict[str, Any],
    mongodb: MongoClient[dict[str, Any]],
    elapsed: float,
    hosts: list[str],
) -> None:
    """No further WES instance is tried once the forward timeout passed.

    The run starts at 0 and fails at the first WES instance after
    `elapsed` seconds (`forward_timeout: 30`).
    """
    remotes["outcomes"][BACKEND_URL] = EngineRequestNotSent("refused")
    remotes["outcomes"][SECOND_URL] = EngineRequestNotSent("refused")
    remotes["outcomes"][THIRD_URL] = EngineRequestNotSent("refused")
    _fake_clock(monkeypatch, workflow_runs, 0, elapsed, 30)
    with three_backends("  post_runs:\n    forward_timeout: 30\n") as client:
        response = _post_run(client)
    assert response.status_code == 500
    assert [host for host, _, _ in remotes["sent"]] == hosts
    assert _stored(mongodb)["run_log"]["state"] == "SYSTEM_ERROR"
