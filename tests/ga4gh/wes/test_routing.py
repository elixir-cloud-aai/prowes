"""Tests of the routing of new runs by WES version (`pro_wes.ga4gh.wes.routing`).

The WES versions of WES instances (labels, service infos, the cache), the
eligibility of WES instances for a run request, and the choice of the WES
instance of a new run; service infos are faked.
"""

from collections.abc import Callable, Iterator
import logging
from typing import Any

from connexion import App
from foca.errors import InvalidFieldsProblem
from foca.plugins import Backend
from foca.plugins.backends import clear_caches
from pydantic import ValidationError
import pytest

from pro_wes.config_models import Wes
from pro_wes.exceptions import EngineRequestNotSent, NoBackendAvailable
from pro_wes.ga4gh.wes import routing
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.routing import (
    PROBE_RETRY,
    BackendVersions,
    backend_versions,
    eligible_backends,
    select_backend,
)
from pro_wes.ga4gh.wes.versions import WesVersion
from tests.conftest import BACKEND_URL

V10 = WesVersion(1, 0)
V11 = WesVersion(1, 1)
URL_10 = "https://wes10.example.org"
URL_11 = "https://wes11.example.org"
REQUEST: dict[str, str] = {
    "workflow_params": "{}",
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}
# chooses the workflow engine, which WES 1.0 can't
ENGINE_REQUEST: dict[str, str] = {
    **REQUEST,
    "workflow_engine": "cwltool",
    "workflow_engine_version": "3.1",
}


def _service_info(version: str) -> dict[str, Any]:
    """Get the service info of a WES instance (only the type).

    Args:
        version: Its WES version.

    Returns:
        The service info.
    """
    return {"type": {"group": "org.ga4gh", "artifact": "wes", "version": version}}


class FakeServiceInfos:
    """Fake service infos of WES instances, by URL.

    Attributes:
        answers: Status and body of the service info of each URL, or the
            exception that reading it raises; URLs without one fail.
        reads: URLs read, in order.
        timeouts: Timeouts of the reads, in order.
    """

    def __init__(self) -> None:
        """Create the fake, without answers."""
        self.answers: dict[str, tuple[int, Any] | Exception] = {}
        self.reads: list[str] = []
        self.timeouts: list[float] = []

    def __call__(
        self, backend: Backend, timeout: float = routing.PROBE_TIMEOUT
    ) -> tuple[int, Any]:
        """Read the service info of a WES instance.

        Args:
            backend: Backend.
            timeout: Timeout of the read, in seconds.

        Returns:
            Status and body.

        Raises:
            Exception: Reading it fails.
        """
        self.reads.append(backend.url)
        self.timeouts.append(timeout)
        answer = self.answers.get(
            backend.url, EngineRequestNotSent("not reachable", reason="connect_error")
        )
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def _no_caches() -> Iterator[None]:
    """Start and end each test without versions read earlier.

    Yields:
        Nothing.
    """
    backend_versions.clear()
    clear_caches()
    yield
    backend_versions.clear()
    clear_caches()


@pytest.fixture
def service_infos(monkeypatch: pytest.MonkeyPatch) -> FakeServiceInfos:
    """Fake the service infos of the WES instances.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        The fake: WES 1.0.1 at `URL_10`, WES 1.1.0 at `URL_11`.
    """
    fake = FakeServiceInfos()
    fake.answers[URL_10] = (200, _service_info("1.0.1"))
    fake.answers[URL_11] = (200, _service_info("1.1.0"))
    monkeypatch.setattr(routing, "read_service_info", fake)
    return fake


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Fake the monotonic clock of the routing.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        The current time, as a list of one value that tests change.
    """
    now = [1000.0]
    monkeypatch.setattr(routing.time, "monotonic", lambda: now[0])
    return now


def _backend(url: str, version: str | None = None) -> Backend:
    """Get a backend.

    Args:
        url: Its URL.
        version: Its label `wes.version`, if any.

    Returns:
        The backend.
    """
    labels = {} if version is None else {"wes.version": version}
    return Backend(id=url, url=url, labels=labels)


def _overlay(*entries: tuple[str, str | None]) -> str:
    """Get a config overlay with WES instances.

    Args:
        *entries: URL and label `wes.version` (if any) of each WES instance.

    Returns:
        YAML text of the overlay.
    """
    if not entries:
        return "custom:\n  wes:\n    backends: []\n"
    text = "custom:\n  wes:\n    backends:\n"
    for url, version in entries:
        text += f"      - type: ga4gh-wes\n        url: {url}\n"
        if version is not None:
            text += f'        labels: {{wes.version: "{version}"}}\n'
    return text


@pytest.fixture
def app_with(add_overlay: Callable[[str], None]) -> Callable[..., App]:
    """Get a function that creates the app with WES instances.

    Args:
        add_overlay: Function that merges config over the test overlay.

    Returns:
        Function taking the URL and label of each WES instance (cf.
        `_overlay()`), returning the app.
    """

    def _app(*entries: tuple[str, str | None]) -> App:
        """Create the app.

        Args:
            *entries: URL and label of each WES instance.

        Returns:
            The app.
        """
        from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

        add_overlay(_overlay(*entries))
        return init_app()

    return _app


@pytest.mark.parametrize(("label", "expected"), [("1.0", V10), ("1.1.0", V11)])
def test_label_wins(
    foca_app: App, service_infos: FakeServiceInfos, label: str, expected: WesVersion
) -> None:
    """The label `wes.version` states the version; no service info is read."""
    with foca_app.app.app_context():
        assert backend_versions.get(_backend(URL_11, label)) == expected
        assert backend_versions.confirmed(_backend(URL_11, label))
    assert service_infos.reads == []


def test_read_and_cached(
    foca_app: App, service_infos: FakeServiceInfos, clock: list[float]
) -> None:
    """Versions are read from service infos, and cached for the TTL."""
    versions = BackendVersions()
    backend = _backend(URL_10)
    with foca_app.app.app_context():
        assert not versions.was_read(backend)
        assert not versions.confirmed(backend)
        assert versions.get(backend) == V10
        assert versions.get(_backend(URL_11)) == V11
        assert versions.get(backend) == V10
        assert service_infos.reads == [URL_10, URL_11]
        assert versions.was_read(backend)
        assert versions.confirmed(backend)
        # `custom.wes.version_cache_ttl`: 3600 seconds
        clock[0] += 3599
        versions.get(backend)
        assert service_infos.reads == [URL_10, URL_11]
        clock[0] += 1
        versions.get(backend)
        assert service_infos.reads == [URL_10, URL_11, URL_10]
        versions.clear()
        assert not versions.was_read(backend)


def test_no_probe(foca_app: App, service_infos: FakeServiceInfos) -> None:
    """Without reading, unknown WES instances count as the oldest version."""
    with foca_app.app.app_context():
        assert backend_versions.get(_backend(URL_11), probe=False) == V10
    assert service_infos.reads == []


def test_unavailable(
    foca_app: App,
    service_infos: FakeServiceInfos,
    clock: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unavailable WES instances count as the oldest version, unconfirmed.

    They keep the version read before, and are read again after a minute.
    """
    versions = BackendVersions()
    backend = _backend(URL_11)
    with foca_app.app.app_context():
        service_infos.answers[URL_11] = EngineRequestNotSent("down")
        assert versions.get(backend) == V10
        assert not versions.confirmed(backend)
        assert "not available: EngineRequestNotSent" in caplog.text
        clock[0] += PROBE_RETRY
        service_infos.answers[URL_11] = (200, _service_info("1.1.0"))
        assert versions.get(backend) == V11
        clock[0] += 3600
        service_infos.answers[URL_11] = EngineRequestNotSent("down")
        assert versions.get(backend) == V11
        assert versions.confirmed(backend)
    assert service_infos.reads == [URL_11] * 3


@pytest.mark.parametrize(
    ("answer", "hint"),
    [
        ((200, _service_info("2.0.0")), ""),
        ((200, {"name": "not a WES"}), ""),
        ((404, {"msg": "not found"}), ""),
        ((404, None), ""),
        ((401, None), " (status 401 without credentials)"),
        ((403, _service_info("1.1.0")), " (status 403 without credentials)"),
    ],
)
def test_unknown_version(
    foca_app: App,
    service_infos: FakeServiceInfos,
    clock: list[float],
    caplog: pytest.LogCaptureFixture,
    answer: tuple[int, Any],
    hint: str,
) -> None:
    """WES instances without a supported version count as the oldest one.

    They answered, so the version is confirmed; it is read again after a
    minute.
    """
    service_infos.answers[URL_11] = answer
    backend = _backend(URL_11)
    with (
        foca_app.app.app_context(),
        caplog.at_level(logging.WARNING, logger=routing.__name__),
    ):
        assert backend_versions.get(backend) == V10
        assert backend_versions.confirmed(backend)
        clock[0] += PROBE_RETRY
        backend_versions.get(backend)
    assert f"WES instance {URL_11} unknown{hint}: taken as 1.0" in caplog.text
    assert service_infos.reads == [URL_11, URL_11]


@pytest.mark.parametrize(
    "answer",
    [(503, None), (502, {"msg": "bad gateway"}), (429, None), (200, None), (200, [])],
)
def test_no_answer_keeps_version(
    foca_app: App,
    service_infos: FakeServiceInfos,
    clock: list[float],
    caplog: pytest.LogCaptureFixture,
    answer: tuple[int, Any],
) -> None:
    """Server errors and answers that aren't JSON objects count as failures.

    A version read before stays; without one, the WES instance counts as the
    oldest version, unconfirmed (e.g., a proxy in front of it in
    maintenance), and is read again after a minute.
    """
    backend = _backend(URL_11)
    with foca_app.app.app_context():
        service_infos.answers[URL_11] = answer
        assert backend_versions.get(backend) == V10
        assert not backend_versions.confirmed(backend)
        clock[0] += PROBE_RETRY
        service_infos.answers[URL_11] = (200, _service_info("1.1.0"))
        assert backend_versions.get(backend) == V11
        clock[0] += 3600
        service_infos.answers[URL_11] = answer
        assert backend_versions.get(backend) == V11
        assert backend_versions.confirmed(backend)
    assert f"Service info of WES instance {URL_11} not available" in caplog.text
    assert service_infos.reads == [URL_11] * 3


def test_read_service_info(
    app_with: Callable[..., App], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service infos are read at the API path of the WES instance, briefly."""
    calls: list[tuple[str, float | None]] = []

    def _read(self: WesClient, timeout: float | None = None) -> tuple[int, Any]:
        """Record the read.

        Args:
            self: WES client.
            timeout: Timeout of the request.

        Returns:
            A service info of WES 1.1.0.
        """
        calls.append((self.url, timeout))
        return 200, _service_info("1.1.0")

    monkeypatch.setattr(WesClient, "read_service_info", _read)
    app = app_with((URL_11, None))
    with app.app.app_context():
        assert routing.read_service_info(_backend(URL_11)) == (
            200,
            _service_info("1.1.0"),
        )
    assert calls == [(f"{URL_11}/ga4gh/wes/v1", routing.PROBE_TIMEOUT)]


def test_eligible_compatible(foca_app: App, service_infos: FakeServiceInfos) -> None:
    """Runs that every version can express can run anywhere, unread."""
    backends = [_backend(URL_10), _backend(URL_11)]
    with foca_app.app.app_context():
        assert eligible_backends(REQUEST, backends) == backends
        assert eligible_backends(None, backends) == backends
    assert service_infos.reads == []


def test_eligible_by_version(foca_app: App, service_infos: FakeServiceInfos) -> None:
    """Runs that choose the engine are eligible for WES 1.1 only."""
    backends = [_backend(URL_10), _backend(URL_11), _backend(BACKEND_URL, "1.0")]
    with foca_app.app.app_context():
        assert eligible_backends(ENGINE_REQUEST, backends) == [backends[1]]
        # empty fields choose nothing
        empty = {**REQUEST, "workflow_engine": ""}
        assert eligible_backends(empty, backends) == backends
    assert service_infos.reads == [URL_10, URL_11]


def test_eligible_deadline(
    foca_app: App,
    service_infos: FakeServiceInfos,
    clock: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After `PROBE_TIMEOUT` seconds, no further service info is read.

    WES instances never read are read first, so that one that was read
    before can't keep them from being read.
    """
    first, second = _backend(URL_10), _backend(URL_11)
    reads = service_infos.reads

    def _slow(backend: Backend, timeout: float) -> tuple[int, Any]:
        """Read a service info, taking the whole time.

        Args:
            backend: Backend.
            timeout: Timeout of the read, in seconds.

        Returns:
            Its status and service info.
        """
        clock[0] += timeout
        return service_infos(backend, timeout)

    with foca_app.app.app_context():
        # `first` was read once, and its version has expired since
        backend_versions.get(first)
        clock[0] += 3600
        monkeypatch.setattr(routing, "read_service_info", _slow)
        assert eligible_backends(ENGINE_REQUEST, [first, second]) == [second]
    # `second` first; then the deadline has passed for `first`
    assert reads == [URL_10, URL_11]


def test_eligible_remaining_time(
    foca_app: App,
    service_infos: FakeServiceInfos,
    clock: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each read gets the time left of `PROBE_TIMEOUT` for all of them."""

    def _three_seconds(backend: Backend, timeout: float) -> tuple[int, Any]:
        """Read a service info in three seconds.

        Args:
            backend: Backend.
            timeout: Timeout of the read, in seconds.

        Returns:
            Its status and service info.
        """
        clock[0] += 3
        return service_infos(backend, timeout)

    monkeypatch.setattr(routing, "read_service_info", _three_seconds)
    backends = [_backend(URL_10), _backend(URL_11)]
    with foca_app.app.app_context():
        assert eligible_backends(ENGINE_REQUEST, backends) == [backends[1]]
    assert service_infos.timeouts == [routing.PROBE_TIMEOUT, routing.PROBE_TIMEOUT - 3]


def test_select_first_eligible(
    app_with: Callable[..., App], service_infos: FakeServiceInfos
) -> None:
    """New runs go to the first WES instance that can run them, in its version."""
    app = app_with((URL_10, None), (URL_11, None))
    with app.app.app_context():
        selected = select_backend(REQUEST)
        assert (selected.url, selected.api_path, selected.version) == (
            URL_10,
            "/ga4gh/wes/v1",
            V10,
        )
        assert select_backend(ENGINE_REQUEST).url == URL_11


def test_select_none_eligible(
    app_with: Callable[..., App], service_infos: FakeServiceInfos
) -> None:
    """Runs that no WES instance can run are refused, naming the fields."""
    app = app_with((URL_10, None), (BACKEND_URL, "1.0"))
    with app.app.app_context(), pytest.raises(InvalidFieldsProblem) as info:
        select_backend(ENGINE_REQUEST)
    assert [error.to_dict() for error in info.value.field_errors] == [
        {
            "detail": "No WES instance can run this run unchanged: choosing the"
            " workflow engine is not supported.",
            "pointer": f"#/{field}",
            "code": "not-supported",
        }
        for field in ("workflow_engine", "workflow_engine_version")
    ]


def test_select_none_answered(
    app_with: Callable[..., App], service_infos: FakeServiceInfos
) -> None:
    """If a WES instance hasn't answered, retrying may help: `503`."""
    app = app_with((URL_10, None), ("https://down.example.org", None))
    with app.app.app_context(), pytest.raises(NoBackendAvailable):
        select_backend(ENGINE_REQUEST)


def test_select_no_backend(app_with: Callable[..., App]) -> None:
    """Without WES instances, no run is forwarded: `503`."""
    app = app_with()
    with app.app.app_context(), pytest.raises(NoBackendAvailable):
        select_backend(REQUEST)


def _wes(*entries: dict[str, Any]) -> Wes:
    """Validate the WES backend configuration.

    Args:
        *entries: Backend entries.

    Returns:
        The configuration.
    """
    return Wes.model_validate({"backends": list(entries)})


@pytest.mark.parametrize("label", ["1.0", "1.0.1", "1.1", "v1.1.0"])
def test_config_label(label: str) -> None:
    """Labels `wes.version` of supported versions are accepted."""
    _wes({"type": "ga4gh-wes", "url": URL_11, "labels": {"wes.version": label}})


@pytest.mark.parametrize("label", ["2.0", "1", "latest", ""])
def test_config_label_unknown(label: str) -> None:
    """Labels `wes.version` of other versions stop the app on startup."""
    with pytest.raises(ValidationError, match="supported WES versions"):
        _wes({"type": "ga4gh-wes", "url": URL_11, "labels": {"wes.version": label}})


def test_config_label_registry() -> None:
    """Labels of registry entries are checked, too."""
    with pytest.raises(ValidationError, match="supported WES versions"):
        _wes(
            {
                "type": "ga4gh-service-registry",
                "url": "https://registry.example.org",
                "labels": {"wes.version": "0.3"},
            }
        )


def test_config_cache_ttl() -> None:
    """The version cache's TTL is positive (default: an hour)."""
    assert Wes().version_cache_ttl == 3600
    with pytest.raises(ValidationError):
        Wes.model_validate({"version_cache_ttl": 0})
