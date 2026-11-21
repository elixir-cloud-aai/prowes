"""WES client, sending requests with FOCA's guarded HTTP client.

Requests to WES instances go through FOCA's guarded HTTP client
(:py:class:`foca.utils.http.GuardedClient`):

- `https` only, unless the configured entry of the WES instance allows
  `http` (`url_schemes`); TLS certificates are always verified, with the
  public certificate authorities, or with those of the file or directory
  that the environment variable `REQUESTS_CA_BUNDLE` names
  (:py:data:`CA_BUNDLE_ENV`) instead;
- configured WES instances may have private addresses (operators trust
  them); discovered ones only public ones, unless their registry entry has
  `allow_private_addresses`; WES instances that the backend list doesn't
  yield (e.g., of runs stored before it changed) only public ones, over
  `https`;
- redirects are not followed: they fail the request;
- each request ends within its timeout as a whole (name resolution,
  connecting, sending and reading), and responses are read up to
  `custom.wes.max_response_size` bytes;
- proxy settings and `.netrc` files of the environment are ignored.

Failed requests raise :py:class:`pro_wes.exceptions.EngineUnavailable`:
:py:class:`pro_wes.exceptions.EngineRequestNotSent` if the WES instance
certainly didn't get the request, and
:py:class:`pro_wes.exceptions.EngineOutcomeUnknown` if it may have got (and
processed) it, e.g., after a read timeout, or after a redirect or a
response that is no JSON.

Requests are tried again as `custom.wes.retry` says
(:py:class:`pro_wes.config_models.Retry`), after a wait that doubles with
each try, as long as another try may succeed and can't make the WES instance
do anything twice:

- requests that the WES instance certainly didn't get
  (:py:class:`pro_wes.exceptions.EngineRequestNotSent`), whatever their
  method, unless another try can't change that (`NOT_RETRIED_REASONS`, e.g.,
  hosts without allowed addresses);
- reads (`SAFE_METHODS`) also after read timeouts and lost connections, and
  after answers of the statuses of `RETRIED_STATUSES` (e.g., `503`); the
  answer of the last try is returned.

Requests that change something (`POST`) are never tried again once the WES
instance may have got them: e.g., after a read timeout, a lost connection or
any answer, also `502`, `503` and `504`, which gateways in front of WES
instances send also after the WES instance got the request.
"""

from collections.abc import Callable
import logging
import os
import time
from typing import Any

from foca import get_custom_config
from foca.plugins.backends import RegistryEntry
from foca.utils.http import (
    GuardedClient,
    HttpResponse,
    RequestMaybeSent,
    RequestNotSent,
    ResponseTooLarge,
)
from pydantic import BaseModel, TypeAdapter, ValidationError
import requests
from urllib3 import encode_multipart_formdata

from pro_wes.config_models import CustomConfig, Retry, Timeout
from pro_wes.exceptions import (
    EngineOutcomeUnknown,
    EngineRequestNotSent,
    EngineUnavailable,
)
from pro_wes.ga4gh.wes.backends import get_backend_list
from pro_wes.ga4gh.wes.models import (
    ErrorResponse,
    RunId,
    RunListResponse,
    RunRequest,
    RunStatus,
    ServiceInfo,
)

logger = logging.getLogger(__name__)

CA_BUNDLE_ENV = "REQUESTS_CA_BUNDLE"
"""Environment variable with the path of the certificate authorities that
WES instances are verified with, instead of the public ones (a file or a
directory, as for requests)."""

_TIMEOUT: TypeAdapter[float] = TypeAdapter(Timeout)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
"""Methods of requests that change nothing (RFC 9110, section 9.2.1), which
are tried again also if the WES instance may have got them."""

RETRIED_STATUSES = frozenset({502, 503, 504})
"""Statuses of answers to reads after which they are tried again."""

RETRIED_MAYBE_SENT = frozenset({"read_timeout", "connection_lost"})
"""Reasons of failed reads that the WES instance may have got after which
they are tried again (not, e.g., `response_too_large` or `redirect`, which
another try wouldn't change)."""

NOT_RETRIED_REASONS = frozenset(
    {"not_public", "invalid_url", "invalid_request", "invalid_header"}
)
"""Reasons of requests not sent that another try wouldn't change (the host
has no allowed address, the URL or the request is invalid)."""

_sleep = time.sleep
"""Function that waits between two tries (replaced in tests)."""


def _retried(method: str, error: EngineRequestNotSent | EngineOutcomeUnknown) -> bool:
    """Tell whether a failed request may be tried again.

    Args:
        method: HTTP method of the request.
        error: Error of the request.

    Returns:
        Whether another try may succeed and can't make the WES instance do
        anything twice: for requests that the WES instance certainly didn't
        get, unless their reason is one of `NOT_RETRIED_REASONS`; for reads
        (`SAFE_METHODS`) also after the reasons of `RETRIED_MAYBE_SENT`.
    """
    if isinstance(error, EngineRequestNotSent):
        return error.reason not in NOT_RETRIED_REASONS
    return method in SAFE_METHODS and error.reason in RETRIED_MAYBE_SENT


class _GuardedHttp(GuardedClient):
    """Guarded HTTP client that may verify with other certificate authorities.

    FOCA's guarded client verifies TLS certificates with the public
    certificate authorities only (it ignores the environment, also
    `REQUESTS_CA_BUNDLE`); with a CA bundle, its `https` requests are
    verified with that one instead (never without verification).

    Attributes:
        ca_bundle: Path of the file or directory of the certificate
            authorities, if any.
    """

    def __init__(self, *, ca_bundle: str | None = None, **kwargs: Any) -> None:
        """Create the client.

        Args:
            ca_bundle: Path of the file or directory of the certificate
                authorities; `None` for the public ones.
            **kwargs: Keyword arguments of
                :py:class:`foca.utils.http.GuardedClient`.
        """
        super().__init__(**kwargs)
        self.ca_bundle = ca_bundle

    @property
    def session(self) -> requests.Session:
        """Get the session of the client, created on first use.

        Returns:
            FOCA's guarded session, whose `https` requests are verified with
            the CA bundle, if any.
        """
        session = super().session
        if self.ca_bundle is not None:
            # FOCA's guarded adapter, mounted for both schemes (`verify` only
            # matters for `https`); FOCA 0.22 has no option for other
            # certificate authorities and passes `verify=True` to `send()`
            # (`test_tls_with_ca_bundle` fails should that change); wrapped
            # once per session (FOCA creates a new one after `close()`)
            for adapter in session.adapters.values():
                if not getattr(adapter, "_prowes_ca_bundle", False):
                    adapter.send = _with_ca_bundle(  # type: ignore[method-assign]
                        adapter.send, self.ca_bundle
                    )
                    adapter._prowes_ca_bundle = True  # type: ignore[attr-defined]
        return session


def _with_ca_bundle(send: Callable[..., Any], ca_bundle: str) -> Callable[..., Any]:
    """Wrap the `send()` method of a transport adapter to use a CA bundle.

    Args:
        send: Method of the adapter.
        ca_bundle: Path of the file or directory of the certificate
            authorities.

    Returns:
        Method that verifies with the CA bundle where `send()` would verify
        with the public certificate authorities (`verify=True`, as FOCA's
        guarded client always sends); never without verification.
    """

    def _send(request: requests.PreparedRequest, **kwargs: Any) -> Any:
        """Send a request, verified with the CA bundle.

        Args:
            request: Prepared request.
            **kwargs: Other arguments of `send()`.

        Returns:
            Response.
        """
        if kwargs.get("verify", True) is True:
            kwargs["verify"] = ca_bundle
        return send(request, **kwargs)

    return _send


def _access(host: str) -> tuple[bool, bool]:
    """Get what requests to a WES instance may do.

    Must be called in an app context.

    Args:
        host: Base URL of the WES instance.

    Returns:
        Whether `http` and whether private addresses are allowed: for
        configured WES instances, `http` if their entry allows it
        (`url_schemes`), and private addresses always; for discovered ones,
        `https` only, and private addresses if their registry entry allows
        them; for others (e.g., no longer listed), neither. Hosts missing
        from the registry snapshot of the process are looked up again in the
        registries (at most once per registry every 10 seconds, FOCA's
        `MIN_REFRESH_INTERVAL`): another process (e.g., the app, for the
        Celery worker) may have chosen a WES instance that this process's
        snapshot doesn't list yet.
    """
    backend_list = get_backend_list()
    backend = backend_list.find(host)
    if backend is None:
        return False, False
    entry = backend_list.entry_for(backend)
    if isinstance(entry, RegistryEntry):
        return False, entry.allow_private_addresses
    return "http" in entry.url_schemes, True


def _validate[T: BaseModel](body: Any, model: type[T]) -> ErrorResponse | T:
    """Validate a response body as the model of an operation or as an error.

    Args:
        body: Parsed JSON body.
        model: Model of the operation's response.

    Returns:
        The body as the model, or as an error response.

    Raises:
        ValueError: The body is neither.
    """
    if not isinstance(body, dict):
        raise ValueError(f"invalid response: {body}")
    try:
        return model(**body)
    except ValidationError:
        try:
            return ErrorResponse(**body)
        except ValidationError as exc:
            raise ValueError(f"invalid response: {body}") from exc


class WesClient:
    """Client to communicate with GA4GH WES API.

    Must be created in an app context (it reads the config and the backend
    list, cf. :py:mod:`pro_wes.ga4gh.wes.client_wes`). A client is not meant
    to be shared between threads.

    Arguments:
        host: Host at which the WES API is served; note that this should
            include the path information but *not* the base path path defined
            in the WES API specification; e.g., specify https://my.wes.com/api
            if the actual API is hosted at https://my.wes.com/api/ga4gh/wes/v1.
        base_path: Override the default path suffix defined in the WES API
            specification.
        token: Bearer token to send along with any WES API requests. Set if
            required by WES instance. Alternatively, use `set_token()` method
            to set.
        retry: Retries of the requests; by default those of the config
            (`custom.wes.retry`).

    Attributes:
        url: URL to WES endpoints, built from `url` and `base_path`,
            e.g.,"https://wes.rahtiapp.fi/ga4gh/wes/v1".
        token: Bearer token for gaining access to WES endpoints.
        timeout: Timeout of requests without one of their own, in seconds
            (`custom.defaults.timeout`).
        retry: Retries of the requests.
        http: Guarded HTTP client of the requests.
    """

    def __init__(
        self,
        host: str,
        base_path: str = "/ga4gh/wes/v1",
        token: str | None = None,
        retry: Retry | None = None,
    ) -> None:
        """Class constructor."""
        # without the separator for an empty base path (the API at `host`)
        self.url = "/".join(
            part for part in (host.rstrip("/"), base_path.strip("/")) if part
        )
        self.set_token(token)
        config = get_custom_config(CustomConfig)
        self.timeout: float = config.defaults.timeout
        self.retry: Retry = config.wes.retry if retry is None else retry
        allow_http, allow_private_addresses = _access(host)
        self.http: GuardedClient = _GuardedHttp(
            ca_bundle=os.environ.get(CA_BUNDLE_ENV) or None,
            timeout=self.timeout,
            allow_http=allow_http,
            allow_private_addresses=allow_private_addresses,
            max_response_size=config.wes.max_response_size,
        )

    def get_service_info(
        self,
        timeout: float | None = None,
    ) -> ErrorResponse | ServiceInfo:
        """Retrieve information about the WES instance.

        Validated against WES 1.1.0 (`ServiceInfo`): the service info of WES
        1.0.x instances (e.g., `workflow_engine_versions` as strings) is
        invalid; their versions are read with :py:meth:`read_service_info`.

        Args:
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).

        Returns:
            Response object according to WES API schema `ServiceInfo`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.EngineUnavailable: The request failed.
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        body = self._request("GET", "/service-info", timeout=timeout)
        return _validate(body, ServiceInfo)

    def read_service_info(self, timeout: float | None = None) -> tuple[int, Any]:
        """Read the service info of the WES instance, as it is.

        Not validated, as WES instances of other WES versions serve other
        forms (cf. :py:mod:`pro_wes.ga4gh.wes.routing`).

        Args:
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).

        Returns:
            Status of the response (any but a redirect), and its parsed JSON
            body (`None` if it is no JSON).

        Raises:
            pro_wes.exceptions.EngineUnavailable: The request failed.
        """
        response = self._send("GET", "/service-info", timeout=timeout)
        try:
            body = response.json()
        except ValueError:
            body = None
        return response.status, body

    def post_run(
        self,
        form_data: dict[str, str],
        files: dict[str, tuple[str, bytes]] | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> ErrorResponse | RunId:
        """Send workflow run request.

        The run request is sent as `multipart/form-data`, as the WES API
        specifies.

        Args:
            form_data: Form data according to WES API schema `RunRequest`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
                The following form fields are required:
                * `workflow_params`
                * `workflow_type`
                * `workflow_type_version`
                * `workflow_url`
                The following form fields are optional:
                * `tags`
                * `workflow_engine_parameters`
                * `workflow_engine` and `workflow_engine_version` (WES
                  1.1.0; left out for WES 1.0)
            files: Files to be attached, by form field: file name and
                content.
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).
            deadline: `time.monotonic()` value after which the request is
                not tried again, if any (the first try is always made).

        Returns:
            WES API schema `RunId`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            ValueError: The form data does not conform to the expected schema.
            pro_wes.exceptions.EngineUnavailable: The request failed.
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        try:
            RunRequest(**form_data)
        except Exception as exc:
            raise ValueError(f"invalid form data: {form_data}") from exc
        fields: list[tuple[str, str | tuple[str, bytes]]] = list(form_data.items())
        fields.extend((files or {}).items())
        data, content_type = encode_multipart_formdata(fields)
        body = self._request(
            "POST",
            "/runs",
            timeout=timeout,
            data=data,
            content_type=content_type,
            deadline=deadline,
        )
        return _validate(body, RunId)

    def get_runs(
        self,
        timeout: float | None = None,
    ) -> ErrorResponse | RunListResponse:
        """Retrieve list of workflow runs.

        Args:
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).

        Returns:
            WES API schema `RunListResponse`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.EngineUnavailable: The request failed.
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        body = self._request("GET", "/runs", timeout=timeout)
        return _validate(body, RunListResponse)

    def get_run(
        self,
        run_id: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Retrieve detailed information about a workflow run.

        The response is not validated against the WES API schema `RunLog`
        (workaround for cwl-WES, whose responses don't conform to it).

        Args:
            run_id: Workflow run identifier.
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).

        Returns:
            Response object, as sent; according to WES API schema `RunLog` if
                the service conforms to it. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.EngineUnavailable: The request failed, or the
                response is no JSON object.
        """
        body = self._request("GET", f"/runs/{run_id}", timeout=timeout)
        if not isinstance(body, dict):
            raise EngineUnavailable("external workflow engine sent no JSON object")
        return body

    def get_run_status(
        self,
        run_id: str,
        timeout: float | None = None,
    ) -> ErrorResponse | RunStatus:
        """Retrieve status information about a workflow run.

        Args:
            run_id: Workflow run identifier.
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).

        Returns:
            WES API schema `RunStatus`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.EngineUnavailable: The request failed.
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        body = self._request("GET", f"/runs/{run_id}/status", timeout=timeout)
        return _validate(body, RunStatus)

    def cancel_run(
        self,
        run_id: str,
        timeout: float | None = None,
    ) -> ErrorResponse | RunId:
        """Cancel workflow run.

        Args:
            run_id: Workflow run identifier.
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's (`custom.defaults.timeout`).

        Returns:
            WES API schema `RunId` if the WES instance accepted the
            cancellation (a `2xx` status), else `ErrorResponse` with the
            HTTP status of the response, whatever its body (e.g., problem
            details, or no JSON). Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.EngineUnavailable: The request failed, or a
                response of `2xx` status is no JSON.
            ValueError: A response of `2xx` status is no `RunId`.
        """
        response = self._send("POST", f"/runs/{run_id}/cancel", timeout=timeout)
        if not 200 <= response.status < 300:
            return ErrorResponse(
                msg=f"WES instance answered with status {response.status}",
                status_code=response.status,
            )
        try:
            return RunId(**_json(response))
        except (TypeError, ValidationError):
            # the body isn't logged (it may be large)
            raise ValueError("invalid response: no run identifier") from None

    def set_token(
        self,
        token: str | None = None,
    ) -> None:
        """Set access token for session.

        Args:
            token: Bearer token to send along with any WES API requests. Set if
                required by WES instance.
        """
        self.token = token

    def headers(
        self,
        content_type: str | None = None,
    ) -> dict[str, str]:
        """Get the headers of a request.

        Each request gets its own headers, so that none sticks to later
        requests. Without a token (or with an empty one), no `Authorization`
        header is sent (no anonymous request carries `Authorization: Bearer
        None`).

        Args:
            content_type: Type of content sent with the request, if any.

        Returns:
            Headers: `Accept` (JSON), and `Content-Type` and `Authorization`
            if given.
        """
        headers = {"Accept": "application/json"}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        timeout: float | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        deadline: float | None = None,
    ) -> Any:
        """Send a request to the WES API and parse the response.

        Args:
            method: HTTP method.
            path: Path of the endpoint, relative to the WES API.
            timeout: Timeout of each try as a whole, in seconds; by default
                the client's.
            data: Body, if any.
            content_type: Type of the body, if any.
            deadline: `time.monotonic()` value after which the request is
                not tried again, if any.

        Returns:
            Parsed JSON body of the response, whatever its status.

        Raises:
            pro_wes.exceptions.EngineRequestNotSent: The WES instance
                certainly didn't get the request (cf. `_send()`).
            pro_wes.exceptions.EngineOutcomeUnknown: The WES instance may
                have got the request (cf. `_send()`), or its response is no
                JSON.
            pydantic.ValidationError: The timeout is invalid (cf. `_send()`).
        """
        return _json(self._send(method, path, timeout, data, content_type, deadline))

    def _send(
        self,
        method: str,
        path: str,
        timeout: float | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
        deadline: float | None = None,
    ) -> HttpResponse:
        """Send a request to the WES API, and try again as `retry` says.

        Each try has the timeout of its own. Failed tries are tried again if
        another try may succeed and can't make the WES instance do anything
        twice (cf. :py:mod:`pro_wes.ga4gh.wes.client_wes`), up to
        `retry.attempts` tries, unless the wait before the next try would end
        after the deadline.

        Args:
            method: HTTP method.
            path: Path of the endpoint, relative to the WES API.
            timeout: Timeout of each try as a whole, in seconds; by default
                the client's.
            data: Body, if any.
            content_type: Type of the body, if any.
            deadline: `time.monotonic()` value after which no try starts,
                if any (the first try is always made).

        Returns:
            Response of the last try, of any status but a redirect.

        Raises:
            pro_wes.exceptions.EngineRequestNotSent: The WES instance
                certainly didn't get the last try (cf. `_send_once()`).
            pro_wes.exceptions.EngineOutcomeUnknown: The WES instance may
                have got the last try (cf. `_send_once()`).
            pydantic.ValidationError: The timeout is invalid (cf.
                `_send_once()`).
        """
        attempt = 1
        while True:
            try:
                response = self._send_once(method, path, timeout, data, content_type)
            except (EngineRequestNotSent, EngineOutcomeUnknown) as exc:
                if not (
                    _retried(method, exc)
                    and self._wait(attempt, deadline, method, path, exc.reason)
                ):
                    raise
            else:
                if not (
                    method in SAFE_METHODS
                    and response.status in RETRIED_STATUSES
                    and self._wait(
                        attempt, deadline, method, path, f"status {response.status}"
                    )
                ):
                    return response
            attempt += 1

    def _wait(
        self,
        attempt: int,
        deadline: float | None,
        method: str,
        path: str,
        cause: str,
    ) -> bool:
        """Wait before the next try of a request, if there is one.

        Args:
            attempt: Number of the try that failed (from 1).
            deadline: `time.monotonic()` value after which no try starts,
                if any.
            method: HTTP method of the request, for the log.
            path: Path of the request, for the log.
            cause: Why the try failed, for the log.

        Returns:
            Whether there is a next try: tries are left (`retry.attempts`),
            and the wait (`retry.backoff`, doubled for each further try, up
            to `retry.max_backoff`) ends before the deadline.
        """
        if attempt >= self.retry.attempts:
            return False
        wait = min(self.retry.backoff * 2 ** (attempt - 1), self.retry.max_backoff)
        if deadline is not None and time.monotonic() + wait >= deadline:
            return False
        logger.warning(
            "%s %s%s failed (%s); trying again in %s s (try %d of %d)",
            method,
            self.url,
            path,
            cause,
            wait,
            attempt + 1,
            self.retry.attempts,
        )
        _sleep(wait)
        return True

    def _send_once(
        self,
        method: str,
        path: str,
        timeout: float | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> HttpResponse:
        """Send a request to the WES API, once.

        Args:
            method: HTTP method.
            path: Path of the endpoint, relative to the WES API.
            timeout: Timeout of the request as a whole, in seconds; by
                default the client's.
            data: Body, if any.
            content_type: Type of the body, if any.

        Returns:
            Response, of any status but a redirect.

        Raises:
            pro_wes.exceptions.EngineRequestNotSent: The WES instance
                certainly didn't get the request (e.g., its host has no
                allowed address, connecting or the TLS handshake failed, the
                timeout passed before sending, or the CA bundle can't be
                read).
            pro_wes.exceptions.EngineOutcomeUnknown: The WES instance may
                have got the request (e.g., the response timed out, or is
                larger than `custom.wes.max_response_size`, or a redirect).
            pydantic.ValidationError: The timeout is not positive, finite and
                at most `pro_wes.config_models.MAX_TIMEOUT` (a `ValueError`;
                nothing is sent).
        """
        url = f"{self.url}{path}"
        # FOCA's `request()` takes no timeout: set the client's per request
        # (clients aren't shared between threads), within the bounds of the
        # config, as larger ones overflow FOCA's deadlines
        self.http.timeout = _TIMEOUT.validate_python(
            self.timeout if timeout is None else timeout
        )
        try:
            response: HttpResponse = self.http.request(
                method, url, headers=self.headers(content_type), data=data
            )
        except ResponseTooLarge as exc:
            logger.error(
                "Response of WES instance larger than allowed"
                " (`custom.wes.max_response_size`): %s",
                exc,
            )
            raise EngineOutcomeUnknown(str(exc), reason=exc.reason) from exc
        except RequestMaybeSent as exc:
            raise EngineOutcomeUnknown(str(exc), reason=exc.reason) from exc
        except RequestNotSent as exc:
            raise EngineRequestNotSent(str(exc), reason=exc.reason) from exc
        except OSError as exc:
            # e.g., a CA bundle that doesn't exist (`cert_verify()` of
            # requests), before anything is sent
            raise EngineRequestNotSent(
                f"{method} {url} not sent: {type(exc).__name__}"
            ) from exc
        # the WES instance (or a proxy in front of it) got the request and may
        # have processed it, but sent no usable answer
        if 300 <= response.status < 400:
            raise EngineOutcomeUnknown(
                f"{method} {url} redirected (status {response.status}); redirects"
                " are not followed",
                reason="redirect",
            )
        return response


def _json(response: HttpResponse) -> Any:
    """Parse the body of a response of a WES instance as JSON.

    Args:
        response: Response of the WES instance.

    Returns:
        Parsed body.

    Raises:
        pro_wes.exceptions.EngineOutcomeUnknown: The body is no JSON.
    """
    try:
        return response.json()
    except ValueError as exc:
        raise EngineOutcomeUnknown(
            "external workflow engine sent no JSON", reason="invalid_json"
        ) from exc
