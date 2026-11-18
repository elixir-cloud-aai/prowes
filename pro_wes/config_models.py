"""Custom app config models."""

from pathlib import Path
import re
from typing import Annotated, Any, Literal, Self

from foca.plugins.backends import RegistryEntry, ServiceEntry
from foca.utils.http import MAX_RESPONSE_SIZE
from foca.utils.misc import generate_id
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)

DEFAULT_API_PATH = "/ga4gh/wes/v1"
"""Path of the WES API, relative to the URL of a backend, as the WES
specification has it; that of the API of proWES too."""

# Absolute paths of unreserved characters (RFC 3986, section 2.3)
_API_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)+")


class WesServiceEntry(ServiceEntry):
    """WES instance of `custom.wes.backends` (`type: ga4gh-wes`).

    The backend entry of FOCA (:py:class:`foca.plugins.backends.ServiceEntry`),
    whose types are those of TES instances only (FOCA 0.22), for WES
    instances, with the API path. proWES doesn't authenticate at WES
    instances yet (#120): `auth` must be `none`.

    Args:
        type: Type of the entry (`ga4gh-wes`).
        auth: Credentials for the WES instance: none (`none`).
        api_path: Path of the WES API, relative to `url`, for WES instances
            that serve it elsewhere than the WES specification says; `/` if
            it is at `url` itself.
        **data: Other fields of :py:class:`foca.plugins.backends.ServiceEntry`
            (`url`, `url_schemes`, `id`, `labels`, `location`).

    Attributes:
        type: Type of the entry.
        auth: Credentials for the WES instance.
        api_path: Path of the WES API, relative to `url`, without trailing
            slash (empty if it is at `url` itself).
    """

    # Narrower than the fields of FOCA's entry (TES instances, credentials);
    # the type is that of the API type of `pro_wes.ga4gh.wes.backends.WES_API`
    type: Literal["ga4gh-wes"]  # type: ignore[assignment]
    auth: Literal["none"] = "none"
    api_path: str = DEFAULT_API_PATH

    @field_validator("api_path")
    @classmethod
    def check_api_path(cls, value: str) -> str:
        """Check the API path, and remove trailing slashes.

        Args:
            value: API path, as given.

        Returns:
            The API path.

        Raises:
            ValueError: The path is not absolute, has characters other than
                unreserved ones (e.g., a query), or segments `.` or `..`.
        """
        if not value.startswith("/"):
            raise ValueError("API path must be absolute")
        path = value.removesuffix("/")
        if path and (
            not _API_PATH.fullmatch(path) or {".", ".."} & set(path.split("/"))
        ):
            raise ValueError(
                "API path must have unreserved characters only, and no segments"
                " `.` or `..`"
            )
        return path


class WesRegistryEntry(RegistryEntry):
    """Service registry of `custom.wes.backends` (`type: ga4gh-service-registry`).

    The registry entry of FOCA (:py:class:`foca.plugins.backends.RegistryEntry`),
    whose verified WES instances are used, at the API path of the WES
    specification. proWES doesn't authenticate at WES instances yet (#120):
    `auth` must be `none`.

    Args:
        auth: Credentials for the discovered WES instances: none (`none`).
        **data: Other fields of
            :py:class:`foca.plugins.backends.RegistryEntry`.

    Attributes:
        auth: Credentials for the discovered WES instances.
    """

    auth: Literal["none"] = "none"


WesBackendEntry = Annotated[
    WesServiceEntry | WesRegistryEntry, Field(discriminator="type")
]
"""Entry of `custom.wes.backends`."""


class Wes(BaseModel):
    """WES backend configuration.

    Args:
        backends: WES instances that runs may be forwarded to, in order:
            configured ones (`type: ga4gh-wes`) and service registries whose
            verified WES instances are used (`type: ga4gh-service-registry`;
            cf. :py:mod:`foca.plugins.backends`). None by default: runs are
            refused until a deployment configures them.
        max_response_size: Maximum size of each response of a WES instance,
            in bytes (by default FOCA's, 16 MiB); larger responses fail the
            request.

    Attributes:
        backends: WES instances that runs may be forwarded to, in order.
        max_response_size: Maximum size of each response of a WES instance,
            in bytes.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    backends: list[WesBackendEntry] = []
    max_response_size: PositiveInt = MAX_RESPONSE_SIZE

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        """Check that the identifiers of the entries are unique.

        Returns:
            The configuration.

        Raises:
            ValueError: Two entries have the same identifier.
        """
        seen: set[str] = set()
        for entry in self.backends:
            if entry.id in seen:
                raise ValueError(f"Backend entries with the same id: {entry.id}")
            seen.add(entry.id)
        return self


class Defaults(BaseModel):
    """Model for global default parameters.

    Args:
        timeout: Timeout of requests to WES instances (e.g., status and
            cancellation), in seconds, for each request as a whole. May be
            overridden by more specific parameters for each endpoint.

    Attributes:
        timeout: Timeout of requests to WES instances, in seconds.
    """

    timeout: PositiveFloat = 3


class PostRuns(BaseModel):
    """Model for controller config parameters for operation `POST /runs`.

    Attributes:
        storage_path: Location inside the app container where storage will be
            mounted.
        db_insert_attempts: How many times the controller will create new run
            identifiers for inserting a new run document in the database. If
            exceeded, a `500/InternalServerError` will be returned.
        id_charset: Characters of run identifiers, taken as is (expressions
            such as `string.digits` are not evaluated).
        id_length: Length of run identifiers.
        timeout_post: Timeout of sending a new workflow run request to a
            remote WES instance, and of reading run logs, in seconds, for
            each request as a whole.
        timeout_job: How long, in seconds, a background job tracking a
            workflow run is allowed to run. Set to `None` to disable timing
            out.
        polling_wait: How long, in seconds, the worker should wait in between
            status update calls.
        polling_attempts: How often the worker should attempt in a row to
            receive a valid run status response from the remote WES service
            until an error is raised and the job is set to state
            `SYSTEM_ERROR`. Once a valid response is obtained, the counter is
            reset to 1.
        max_attachments: Maximum number of workflow attachments of a run
            (`0` allows none); runs with more are rejected with `413 Content
            Too Large`.
        max_attachment_size: Maximum size of each workflow attachment, in
            bytes; runs with larger ones are rejected with `413 Content Too
            Large`. All attachments together are limited by the size of
            request bodies (`server.max_content_length`).
        url_credentials: What happens to runs with credentials in the URLs of
            their run requests (`https://user:password@host/...`, in
            `workflow_url` and in the strings of `workflow_params`,
            `workflow_engine_parameters` and `tags`; cf.
            `pro_wes.utils.urls.has_credentials`): they are rejected with
            `400 Bad Request` (`reject`), or the credentials are removed
            (`strip`), before runs are stored or forwarded.
    """

    storage_path: Path = Path("/data")
    db_insert_attempts: int = 10
    id_charset: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    id_length: int = 6
    timeout_post: PositiveFloat = 30
    timeout_job: int | None = None
    polling_wait: float = 3
    polling_attempts: int = 100
    max_attachments: NonNegativeInt = 100
    max_attachment_size: PositiveInt = 1048576
    url_credentials: Literal["reject", "strip"] = "reject"

    @field_validator("id_charset")
    @classmethod
    def id_charset_valid(cls, value: str) -> str:
        """Ensure that the characters of run identifiers can be used.

        Args:
            value: Characters of run identifiers.

        Returns:
            The characters.

        Raises:
            ValueError: The characters are empty or an expression (e.g.,
                `string.digits`), which FOCA no longer evaluates.
        """
        try:
            generate_id(charset=value, length=1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unusable characters of run identifiers: {exc}") from exc
        return value


class ListRuns(BaseModel):
    """Model for controller config parameters for operation `GET /runs`.

    Args:
        default_page_size: Default page size for response pagination.

    Attributes:
        default_page_size: Default page size for response pagination.
    """

    default_page_size: int = 5


class CustomConfig(BaseModel):
    """Custom app configuration.

    Args:
        defaults: Global configuration parameters.
        post_runs: Configuration parameters for operation `POST /runs`.
        list_runs: Configuration parameters for operation `GET /runs`.
        wes: WES backends that runs are forwarded to.

    Attributes:
        defaults: Global configuration parameters.
        post_runs: Configuration parameters for operation `POST /runs`.
        list_runs: Configuration parameters for operation `GET /runs`.
        wes: WES backends that runs are forwarded to.
    """

    defaults: Defaults = Defaults()
    post_runs: PostRuns = PostRuns()
    list_runs: ListRuns = ListRuns()
    wes: Wes = Wes()

    @model_validator(mode="before")
    @classmethod
    def no_service_info(cls, data: Any) -> Any:
        """Refuse the service info of earlier versions (`custom.service_info`).

        Without this check, it would be ignored, and the service info of the
        configuration served instead.

        Args:
            data: Values of the section.

        Returns:
            The values, unchanged.

        Raises:
            ValueError: The section has key `service_info`.
        """
        if isinstance(data, dict) and "service_info" in data:
            raise ValueError(
                "`custom.service_info` moved to `service_info.service` "
                '(served by FOCA); cf. "Upgrading" in the documentation'
            )
        return data
