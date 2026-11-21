"""WES versions, and adapters of runs between them.

proWES stores and serves runs in the newest WES version it supports
(:py:data:`CANONICAL`), and forwards them to WES instances that may implement
an older one. Adapters (:py:class:`WesAdapter`) convert between two adjacent
versions, in both directions; they are registered in :py:data:`ADAPTERS` and
applied in chains, so that supporting a new version needs its specification
and one adapter (see `docs/wes-versions.md`). They work on run requests (form
fields) and run logs as plain JSON objects, without the database, the
tracking of runs or HTTP clients.

Going down, an adapter declares what the older version can't express without
changing what the run does (:py:meth:`WesAdapter.incompatibilities`): such
runs are forwarded only to WES instances that can run them. Everything else
that the older version doesn't know is dropped.

Versions are compared by major and minor version only, as patch versions
don't change the API (e.g., WES 1.0.0 and 1.0.1 are both `1.0`).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from typing import Any, Self

# at most 9 digits per component, far below the limit of `int()`
_VERSION = re.compile(r"v?(\d{1,9})\.(\d{1,9})(?:[.\-+].*)?", re.IGNORECASE)

ENGINE_FIELDS = ("workflow_engine", "workflow_engine_version")
"""Fields of run requests of WES 1.1 that choose the workflow engine."""

WES_VERSION_LABEL = "wes.version"
"""Label of backend entries that states the WES version of WES instances."""


@dataclass(frozen=True, order=True)
class WesVersion:
    """Version of the WES API; patch versions don't change the API.

    Attributes:
        major: Major version.
        minor: Minor version.
    """

    major: int
    minor: int

    @classmethod
    def parse(cls, value: object) -> Self | None:
        """Parse a version leniently, as WES instances report them.

        E.g., `1.1.0`, `v1.0.1` and `1.0` (also as a number).

        Args:
            value: Version.

        Returns:
            The version, or `None` if it is none.
        """
        if isinstance(value, bool) or not isinstance(value, str | float):
            return None
        match = _VERSION.fullmatch(str(value).strip())
        if match is None:
            return None
        return cls(major=int(match[1]), minor=int(match[2]))

    def __str__(self) -> str:
        """Get the version as `<major>.<minor>`.

        Returns:
            The version.
        """
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True)
class Incompatibility:
    """Field of a run request that a version can't express.

    Attributes:
        field: Name of the field (form field of `POST /runs`).
        reason: Why dropping the field would change what the run does.
    """

    field: str
    reason: str

    def __str__(self) -> str:
        """Get the incompatibility, for messages to clients.

        Returns:
            The field and the reason.
        """
        return f"{self.field}: {self.reason}"


class WesAdapter(ABC):
    """Adapter of runs between two adjacent WES versions.

    Attributes:
        newer: The newer version.
        older: The older version.
    """

    newer: WesVersion
    older: WesVersion

    @abstractmethod
    def incompatibilities(self, request: dict[str, Any]) -> list[Incompatibility]:
        """Get what the older version can't express without changing the run.

        Args:
            request: Run request of the newer version (form fields); not
                changed.

        Returns:
            The fields of the request that the older version can't express.
        """

    @abstractmethod
    def downgrade_run_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Convert a run request to the older version.

        Args:
            request: Run request of the newer version (form fields); not
                changed.

        Returns:
            The run request, without what the older version doesn't know.
        """

    @abstractmethod
    def upgrade_run_log(self, run_log: dict[str, Any]) -> dict[str, Any]:
        """Convert a run log that a WES instance serves to the newer version.

        Args:
            run_log: Run log of the older version; not changed.

        Returns:
            The run log.
        """


class Wes11To10(WesAdapter):
    """Adapter between WES 1.1 (1.1.0) and WES 1.0 (1.0.0, 1.0.1).

    WES 1.1.0 adds `workflow_engine` and `workflow_engine_version` to run
    requests; run logs, run states and run lists of WES 1.0 are valid in WES
    1.1.0, which only adds optional fields, the state `PREEMPTED`, the task
    endpoints and `RunSummary`.
    """

    newer = WesVersion(1, 1)
    older = WesVersion(1, 0)

    def incompatibilities(self, request: dict[str, Any]) -> list[Incompatibility]:
        """Get what WES 1.0 can't express without changing the run.

        Empty fields choose nothing, so they can be dropped.

        Args:
            request: Run request of WES 1.1 (form fields); not changed.

        Returns:
            The fields that choose the workflow engine or its version: the
            WES instance would run the workflow with an engine of its own
            choice.
        """
        return [
            Incompatibility(field, "choosing the workflow engine is not supported")
            for field in ENGINE_FIELDS
            if request.get(field) not in (None, "")
        ]

    def downgrade_run_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Convert a run request to WES 1.0.

        Args:
            request: Run request of WES 1.1, without incompatibilities; not
                changed.

        Returns:
            The run request, without the fields of WES 1.1, which WES
            instances of WES 1.0 may reject.
        """
        return {
            key: value for key, value in request.items() if key not in ENGINE_FIELDS
        }

    def upgrade_run_log(self, run_log: dict[str, Any]) -> dict[str, Any]:
        """Convert a run log that a WES instance serves to WES 1.1.

        Args:
            run_log: Run log of WES 1.0.

        Returns:
            The run log: all run logs of WES 1.0 are valid in WES 1.1 (`Log`
            objects in `task_logs` included).
        """
        return run_log


ADAPTERS: tuple[WesAdapter, ...] = (Wes11To10(),)
"""Registered adapters, each between two adjacent versions."""

CANONICAL: WesVersion = max(adapter.newer for adapter in ADAPTERS)
"""Version in which proWES stores and serves runs: the newest it supports."""

OLDEST: WesVersion = min(adapter.older for adapter in ADAPTERS)
"""Oldest supported version, assumed for WES instances of unknown versions."""

SUPPORTED: frozenset[WesVersion] = frozenset(
    version for adapter in ADAPTERS for version in (adapter.newer, adapter.older)
)
"""Versions that proWES can forward runs to."""


def parse_supported(value: object) -> WesVersion | None:
    """Parse a version leniently, if proWES supports it.

    Args:
        value: Version (e.g., a label, or stored with a run).

    Returns:
        The version, if it is one of `SUPPORTED`; otherwise `None`.
    """
    version = WesVersion.parse(value)
    return version if version in SUPPORTED else None


def chain(version: WesVersion) -> list[WesAdapter]:
    """Get the adapters between the canonical version and a version.

    Args:
        version: A supported version.

    Returns:
        The adapters, from the newest down to the version; none for the
        canonical version.

    Raises:
        ValueError: The version is not supported.
    """
    if version not in SUPPORTED:
        raise ValueError(f"WES version not supported: {version}")
    return sorted(
        (adapter for adapter in ADAPTERS if adapter.older >= version),
        key=lambda adapter: adapter.newer,
        reverse=True,
    )


def incompatibilities(
    request: dict[str, Any], version: WesVersion = OLDEST
) -> list[Incompatibility]:
    """Get what a version can't express of a run request.

    Each adapter sees the request as the adapters before it left it.

    Args:
        request: Run request of the canonical version (form fields); not
            changed.
        version: Version.

    Returns:
        The fields that keep the run from running unchanged in the version;
        none if it can.

    Raises:
        ValueError: The version is not supported.
    """
    found: list[Incompatibility] = []
    for adapter in chain(version):
        found.extend(adapter.incompatibilities(request))
        request = adapter.downgrade_run_request(request)
    return found


def downgrade_run_request(
    request: dict[str, Any], version: WesVersion
) -> dict[str, Any]:
    """Convert a run request of the canonical version to a version.

    Args:
        request: Run request of the canonical version (form fields), without
            incompatibilities; not changed.
        version: Version.

    Returns:
        The run request in the version (the same object for the canonical
        version).

    Raises:
        ValueError: The version is not supported.
    """
    for adapter in chain(version):
        request = adapter.downgrade_run_request(request)
    return request


def upgrade_run_log(run_log: dict[str, Any], version: WesVersion) -> dict[str, Any]:
    """Convert a run log that a WES instance serves to the canonical version.

    Args:
        run_log: Run log of the version; not changed.
        version: Version.

    Returns:
        The run log in the canonical version.

    Raises:
        ValueError: The version is not supported.
    """
    for adapter in reversed(chain(version)):
        run_log = adapter.upgrade_run_log(run_log)
    return run_log


def reported_version(service_info: Any) -> WesVersion | None:
    """Get the WES version that a service info reports, read leniently.

    Args:
        service_info: Service info, as served by a WES instance.

    Returns:
        The version of `type.version`, if `type.group` is `org.ga4gh` and
        `type.artifact` is `wes` (ignoring case); `None` if it reports none
        that proWES supports.
    """
    if not isinstance(service_info, dict):
        return None
    service_type = service_info.get("type")
    if not isinstance(service_type, dict):
        return None
    group, artifact = service_type.get("group"), service_type.get("artifact")
    if not (
        isinstance(group, str)
        and isinstance(artifact, str)
        and (group.lower(), artifact.lower()) == ("org.ga4gh", "wes")
    ):
        return None
    return parse_supported(service_type.get("version"))
