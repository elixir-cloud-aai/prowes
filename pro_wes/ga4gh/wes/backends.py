"""WES backends of proWES: configured and discovered in service registries.

The backend list `custom.wes.backends` is a typed backend list of FOCA
(:py:mod:`foca.plugins.backends`), with entries for WES instances
(:py:class:`pro_wes.config_models.WesServiceEntry`) and service registries
(:py:class:`pro_wes.config_models.WesRegistryEntry`). FOCA 0.22 knows the
backends of TES gateways only, so the API type of WES is registered here
(`API_TYPES`, unless FOCA has one), and :py:class:`WesBackendList` takes the
entries of proWES; expanding them, discovery (`GET /services` and `GET
/verifications` of each registry, with FOCA's guarded HTTP client) and its
caches are FOCA's.

Without plugins (#121), runs go to the first backend of the list
(:py:func:`select_backend`).
"""

from collections.abc import Sequence

from foca import get_config, get_custom_config
from foca.plugins import Backend
from foca.plugins.backends import API_TYPES, ApiType, BackendList

from pro_wes.config_models import (
    DEFAULT_API_PATH,
    CustomConfig,
    WesRegistryEntry,
    WesServiceEntry,
)
from pro_wes.exceptions import NoBackendAvailable

WES_API = "wes"
"""API of the backends (cf. `foca.plugins.backends.API_TYPES`)."""

API_TYPES.setdefault(
    WES_API, ApiType("org.ga4gh", "wes", DEFAULT_API_PATH, "ga4gh-wes")
)


class WesBackendList(BackendList):
    """Backends of proWES, from its backend list (`custom.wes.backends`).

    FOCA's backend list (:py:class:`foca.plugins.backends.BackendList`),
    whose validation knows the entries of TES instances only (FOCA 0.22),
    with the entries of proWES, validated by its config model
    (:py:class:`pro_wes.config_models.Wes`).

    Attributes:
        entries: Entries, in order.
        api: API of the backends (`wes`).
        public_url: Public URL of proWES, normalized, if known: backends at
            or below it are never candidates.
    """

    def __init__(
        self,
        entries: Sequence[WesServiceEntry | WesRegistryEntry],
        public_url: str | None = None,
    ) -> None:
        """Create the list.

        Args:
            entries: Entries, in order, validated.
            public_url: Public URL of proWES (e.g., `server.public_url`).
        """
        super().__init__([], api=WES_API, public_url=public_url)
        self.entries = list(entries)


def get_backend_list() -> WesBackendList:
    """Get the backend list of proWES.

    Must be called in an app context.

    Returns:
        The backends of `custom.wes.backends`, without those at or below the
        public URL of proWES (`server.public_url`).
    """
    return WesBackendList(
        get_custom_config(CustomConfig).wes.backends,
        public_url=get_config().server.public_url,
    )


def check_backends() -> None:
    """Check the configured WES instances, on startup.

    Must be called in an app context. Reads no registry.

    Raises:
        ValueError: A configured WES instance is at or below the public URL
            of proWES (`server.public_url`), so that runs would be forwarded
            to proWES itself.
    """
    backend_list = get_backend_list()
    configured = [
        entry for entry in backend_list.entries if isinstance(entry, WesServiceEntry)
    ]
    kept = {
        backend.url
        for backend in WesBackendList(
            configured, public_url=backend_list.public_url
        ).expand()
    }
    for entry in configured:
        if entry.url not in kept:
            raise ValueError(
                f"WES instance {entry.id!r} is at or below the public URL of"
                " proWES (`server.public_url`)"
            )


def api_path(backend: Backend, backend_list: WesBackendList) -> str:
    """Get the path of the WES API of a backend.

    Args:
        backend: Candidate of the list.
        backend_list: List that it comes from.

    Returns:
        API path of its entry, for configured WES instances; that of the WES
        specification, for discovered ones (registries list the URLs of
        services with it, cf. `API_TYPES`).
    """
    entry = backend_list.entry_for(backend)
    if isinstance(entry, WesServiceEntry):
        return entry.api_path
    return DEFAULT_API_PATH


def select_backend() -> tuple[str, str]:
    """Choose the WES instance that a new run is forwarded to.

    The first backend of the list (configured WES instances, and those
    discovered in registries, in the order of `custom.wes.backends`). Must
    be called in an app context.

    Returns:
        Base URL of the WES instance and the path of its WES API.

    Raises:
        pro_wes.exceptions.NoBackendAvailable: The list has no backend, e.g.,
            none is configured, or the registries list none (yet).
    """
    backend_list = get_backend_list()
    backends = backend_list.expand()
    if not backends:
        raise NoBackendAvailable(
            "No WES instance available; configure `custom.wes.backends`"
        )
    backend = backends[0]
    return backend.url, api_path(backend, backend_list)
