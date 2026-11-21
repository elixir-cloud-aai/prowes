"""Routing of new runs to WES instances, by their WES versions.

The WES version of a WES instance (:py:func:`backend_version`) is the label
`wes.version` of its entry; otherwise, what its service info reports (read
without credentials, leniently, and cached per process for
`custom.wes.version_cache_ttl` seconds). WES instances of unknown versions
count as the oldest supported version (:py:data:`pro_wes.ga4gh.wes.versions.OLDEST`).

New runs go to the first WES instance of the backend list that can run them
unchanged (:py:func:`eligible_backends`, :py:func:`select_backend`), in the
version of that WES instance. Until FOCA's gateway plugins choose and order
the WES instances (#121), these are the candidates of the `route` phase.
"""

from collections.abc import Collection
from dataclasses import dataclass
from http import HTTPStatus
import logging
import time
from typing import Any, NamedTuple

from foca import get_custom_config
from foca.errors import FieldError, InvalidFieldsProblem, json_pointer
from foca.plugins import Backend

from pro_wes.config_models import CustomConfig, Retry
from pro_wes.exceptions import NoBackendAvailable
from pro_wes.ga4gh.wes.backends import api_path, get_backend_list
from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.versions import (
    OLDEST,
    WES_VERSION_LABEL,
    WesVersion,
    incompatibilities,
    parse_supported,
    reported_version,
)

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 5.0
"""Seconds that reading the service info of a WES instance may take, and that
all reads for one run may take before the cached versions count."""

PROBE_RETRY = 60.0
"""Seconds after which a failed read, or one without a supported version, is
tried again."""

PROBE_REFUSED = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})
"""Statuses of service infos that need credentials (logged with a hint)."""

PROBE_NOT_FOUND = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.GONE})
"""Statuses of WES instances without a service info, which count as answers."""


def label_version(backend: Backend) -> WesVersion | None:
    """Get the WES version that the label `wes.version` of a backend states.

    Args:
        backend: Backend.

    Returns:
        The version, if the label is set to a supported one (others stop the
        app on startup, cf. :py:class:`pro_wes.config_models.Wes`).
    """
    return parse_supported(backend.labels.get(WES_VERSION_LABEL))


class _Probe(NamedTuple):
    """Result of reading the service info of a WES instance.

    Attributes:
        version: The WES version; `None` if the WES instance never answered.
        expires_at: When to read it again (`time.monotonic()`).
    """

    version: WesVersion | None
    expires_at: float


class BackendVersions:
    """WES versions of WES instances, from their service info, cached.

    Thread-safe (a lost update only reads a service info once more, and
    failures keep what other threads have read meanwhile); each process (e.g.,
    each Gunicorn worker) has its own cache.
    """

    def __init__(self) -> None:
        """Create an empty cache."""
        self._cache: dict[str, _Probe] = {}

    def clear(self) -> None:
        """Remove all cached versions."""
        self._cache.clear()

    def get(
        self, backend: Backend, probe: bool = True, timeout: float = PROBE_TIMEOUT
    ) -> WesVersion:
        """Get the WES version of a WES instance.

        Never raises: runs can be forwarded to WES instances of unknown
        versions.

        Args:
            backend: Backend.
            probe: Whether to read the service info of the WES instance if no
                version is cached, or the cached one expired.
            timeout: Time that reading the service info may take, in seconds.

        Returns:
            The version of the label `wes.version` of the backend, else that
            of its service info, else (e.g., if the WES instance is
            unavailable) the oldest supported version.
        """
        version = label_version(backend)
        if version is not None:
            return version
        cached = self._cache.get(backend.url)
        if probe and (cached is None or time.monotonic() >= cached.expires_at):
            cached = self._probe(backend, timeout=timeout)
            self._cache[backend.url] = cached
        return (cached.version if cached is not None else None) or OLDEST

    def was_read(self, backend: Backend) -> bool:
        """Tell whether reading the service info of a WES instance was tried.

        Args:
            backend: Backend.

        Returns:
            Whether this process has tried to read it (and still knows).
        """
        return backend.url in self._cache

    def confirmed(self, backend: Backend) -> bool:
        """Tell whether a WES instance stated its WES version or answered at all.

        Doesn't read its service info.

        Args:
            backend: Backend.

        Returns:
            Whether the version comes from the label of the backend or an
            answer of the WES instance, not from having no answer so far.
        """
        if label_version(backend) is not None:
            return True
        cached = self._cache.get(backend.url)
        return cached is not None and cached.version is not None

    def _probe(self, backend: Backend, timeout: float = PROBE_TIMEOUT) -> _Probe:
        """Read the WES version of a WES instance from its service info.

        The service info is read without credentials (it is public in the WES
        specification), within `PROBE_TIMEOUT` seconds. A failure (also an
        answer that is no service info: neither a JSON object of a
        successful status, nor `401`, `403`, `404` or `410`, e.g., a server
        error of a proxy) doesn't change what is known: a version that was
        read before stays, so that one failed request doesn't make a WES
        instance count as another version. An answer without a supported
        version does.

        Args:
            backend: Backend.
            timeout: Time that reading the service info may take, in seconds.

        Returns:
            What the service info says, and when to read it again.
        """
        try:
            status, service_info = read_service_info(backend, timeout=timeout)
            if not (
                (200 <= status < 300 and isinstance(service_info, dict))
                or status in PROBE_REFUSED
                or status in PROBE_NOT_FOUND
            ):
                # e.g., a proxy in front of the WES instance in maintenance
                raise ValueError(f"status {status}, no service info")
        except Exception as exc:
            logger.warning(
                "Service info of WES instance %s not available: %s",
                backend.url,
                exc if isinstance(exc, ValueError) else type(exc).__name__,
            )
            # as it is now: other threads may have read it meanwhile
            previous = self._cache.get(backend.url)
            return _Probe(
                previous.version if previous is not None else None,
                time.monotonic() + PROBE_RETRY,
            )
        version = reported_version(service_info) if 200 <= status < 300 else None
        if version is None:
            hint = (
                f" (status {status} without credentials)"
                if status in PROBE_REFUSED
                else ""
            )
            logger.warning(
                "WES version of WES instance %s unknown%s: taken as %s; set its"
                " label `%s` if it is another one",
                backend.url,
                hint,
                OLDEST,
                WES_VERSION_LABEL,
            )
            return _Probe(OLDEST, time.monotonic() + PROBE_RETRY)
        logger.info("WES version of WES instance %s: %s", backend.url, version)
        return _Probe(
            version,
            time.monotonic() + get_custom_config(CustomConfig).wes.version_cache_ttl,
        )


backend_versions = BackendVersions()


def read_service_info(
    backend: Backend, timeout: float = PROBE_TIMEOUT
) -> tuple[int, Any]:
    """Read the service info of a WES instance, without credentials.

    Tried once, not as `custom.wes.retry` says. Must be called in an app
    context.

    Args:
        backend: Backend.
        timeout: Time that reading the service info may take, in seconds.

    Returns:
        Status and JSON body of the response (`None` if it is no JSON).

    Raises:
        pro_wes.exceptions.EngineUnavailable: The request failed.
    """
    # tried once: failures are cached and read again after `PROBE_RETRY`
    # seconds, and reads for a run are limited to `PROBE_TIMEOUT` seconds
    client = WesClient(
        host=backend.url,
        base_path=api_path(backend, get_backend_list()),
        retry=Retry(attempts=1),
    )
    return client.read_service_info(timeout=timeout)


def backend_version(
    backend: Backend, probe: bool = True, timeout: float = PROBE_TIMEOUT
) -> WesVersion:
    """Get the WES version of a WES instance (cf. :py:class:`BackendVersions`).

    Args:
        backend: Backend.
        probe: Whether its service info may be read now.
        timeout: Time that reading the service info may take, in seconds.

    Returns:
        The version; the oldest supported one if it is unknown.
    """
    return backend_versions.get(backend, probe=probe, timeout=timeout)


def eligible_backends(request: Any, backends: list[Backend]) -> list[Backend]:
    """Get the WES instances that can run a run request unchanged.

    Runs that every supported version can express can run anywhere, and need
    no service info. For other runs, no further service info is read once
    `PROBE_TIMEOUT` seconds have passed, so that unavailable WES instances
    don't keep the request waiting; the remaining ones count with what is
    cached (cf. :py:meth:`BackendVersions.confirmed`). WES instances never
    read come first, so that an unavailable one can't keep them from being
    read.

    Args:
        request: Run request of the canonical WES version (form fields).
        backends: Backends, in order.

    Returns:
        The backends that can run the request, in order.
    """
    if not isinstance(request, dict) or not incompatibilities(request):
        return list(backends)
    deadline = time.monotonic() + PROBE_TIMEOUT
    for backend in sorted(backends, key=backend_versions.was_read):
        # each read within the time left
        remaining = deadline - time.monotonic()
        backend_version(backend, probe=remaining > 0, timeout=remaining)
    return [
        backend
        for backend in backends
        if not incompatibilities(request, backend_version(backend, probe=False))
    ]


@dataclass(frozen=True)
class SelectedBackend:
    """WES instance that a new run is forwarded to.

    Attributes:
        url: Base URL of the WES instance.
        api_path: Path of its WES API.
        version: Its WES version, which the run is forwarded in.
    """

    url: str
    api_path: str
    version: WesVersion


def select_backend(
    request: dict[str, Any], exclude: Collection[str] = ()
) -> SelectedBackend:
    """Choose the WES instance that a new run is forwarded to.

    The first backend of the list (configured WES instances, and those
    discovered in registries, in the order of `custom.wes.backends`) that can
    run the request unchanged. Must be called in an app context.

    Args:
        request: Run request of the canonical WES version (form fields).
        exclude: Base URLs of WES instances that are not chosen (e.g., those
            that a run could not be sent to).

    Returns:
        The WES instance, with its API path and WES version.

    Raises:
        foca.errors.InvalidFieldsProblem: No WES instance can run the request
            unchanged, and all of them stated or reported their versions
            (`400`), naming the fields.
        pro_wes.exceptions.NoBackendAvailable: The list has no backend
            (e.g., none is configured, the registries list none yet, or
            all are excluded), or
            none that can run the request, but some didn't answer so far,
            or the versions of all eligible ones changed since they were
            found eligible (`503`, as retrying may help).
    """
    backend_list = get_backend_list()
    backends = [
        backend for backend in backend_list.expand() if backend.url not in exclude
    ]
    if not backends:
        raise NoBackendAvailable(
            "No WES instance available; configure `custom.wes.backends`"
        )
    eligible = eligible_backends(request, backends)
    if not eligible:
        if all(backend_versions.confirmed(backend) for backend in backends):
            raise InvalidFieldsProblem(
                [
                    FieldError(
                        detail=(
                            "No WES instance can run this run unchanged:"
                            f" {found.reason}."
                        ),
                        code="not-supported",
                        pointer=json_pointer(found.field),
                    )
                    for found in incompatibilities(request)
                ]
            )
        raise NoBackendAvailable("No available WES instance can run this run unchanged")
    if not incompatibilities(request):
        backend = eligible[0]
        # read for the WES instance that the run goes to, unless cached
        return SelectedBackend(
            url=backend.url,
            api_path=api_path(backend, backend_list),
            version=backend_version(backend),
        )
    # for runs that not all versions can express, the version that made the
    # WES instance eligible (not read again), so that the run isn't forwarded
    # in a version that drops what it chose; one whose version changed
    # meanwhile (e.g., read by another request) is skipped
    for backend in eligible:
        version = backend_version(backend, probe=False)
        if not incompatibilities(request, version):
            return SelectedBackend(
                url=backend.url,
                api_path=api_path(backend, backend_list),
                version=version,
            )
    raise NoBackendAvailable("No available WES instance can run this run unchanged")
