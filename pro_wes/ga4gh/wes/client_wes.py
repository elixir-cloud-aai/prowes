"""Basic WES client."""

from typing import Any

from pydantic import ValidationError
import requests
from requests.exceptions import RequestException

from pro_wes.exceptions import EngineUnavailable
from pro_wes.ga4gh.wes.models import (
    ErrorResponse,
    RunId,
    RunListResponse,
    RunRequest,
    RunStatus,
    ServiceInfo,
)


class WesClient:
    """Client to communicate with GA4GH WES API.

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

    Attributes:
        url: URL to WES endpoints, built from `url` and `base_path`,
            e.g.,"https://wes.rahtiapp.fi/ga4gh/wes/v1".
        token: Bearer token for gaining access to WES endpoints.
        session: Client server session.
    """

    def __init__(
        self,
        host: str,
        base_path: str = "/ga4gh/wes/v1",
        token: str | None = None,
    ) -> None:
        """Class constructor."""
        # without the separator for an empty base path (the API at `host`)
        self.url = "/".join(
            part for part in (host.rstrip("/"), base_path.strip("/")) if part
        )
        self.set_token(token)
        self.session = requests.Session()

    def get_service_info(
        self,
        **kwargs: Any,
    ) -> ErrorResponse | ServiceInfo:
        """Retrieve information about the WES instance.

        Validated against WES 1.1.0 (`ServiceInfo`): the service info of WES
        1.0.x instances (e.g., `workflow_engine_versions` as strings) is
        invalid until versions are read per instance (#122).

        Args:
            **kwargs: Additional keyword arguments passed along with request.

        Returns:
            Response object according to WES API schema `ServiceInfo`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`
        """
        self.set_headers()
        url = f"{self.url}/service-info"
        response: ErrorResponse | ServiceInfo
        try:
            response_unvalidated = self.session.get(url, **kwargs).json()
        except (RequestException, ValueError) as exc:
            raise EngineUnavailable("external workflow engine unavailable") from exc
        try:
            response = ServiceInfo(**response_unvalidated)
        except ValidationError:
            try:
                response = ErrorResponse(**response_unvalidated)
            except ValidationError as exc:
                raise ValueError(f"invalid response: {response_unvalidated}") from exc
        return response

    def post_run(
        self,
        form_data: dict[str, str],
        files: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ErrorResponse | RunId:
        """Send workflow run request.

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
            files: Dictionary of files to be attached. Cf.
                https://requests.readthedocs.io/en/latest/api/#requests.request
                for possible structures of dictionary.
            **kwargs: Additional keyword arguments passed along with request,
                e.g., `timeout`, the request timeout in seconds (`None`
                disables it).

        Returns:
            WES API schema `RunId`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            ValueError: The form data does not conform to the expected schema.
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        self.set_headers()
        url = f"{self.url}/runs"
        response: ErrorResponse | RunId
        if files is None:
            files = {}
        try:
            RunRequest(**form_data)
        except Exception as exc:
            raise ValueError(f"invalid form data: {form_data}") from exc
        try:
            response_unvalidated = self.session.post(
                url,
                data=form_data,
                files=files,
                **kwargs,
            ).json()
        except (RequestException, ValueError) as exc:
            raise EngineUnavailable("external workflow engine unavailable") from exc
        try:
            response = RunId(**response_unvalidated)
        except ValidationError:
            try:
                response = ErrorResponse(**response_unvalidated)
            except ValidationError as exc:
                raise ValueError(f"invalid response: {response_unvalidated}") from exc
        return response

    def get_runs(
        self,
        **kwargs: Any,
    ) -> ErrorResponse | RunListResponse:
        """Retrieve list of workflow runs.

        Args:
            **kwargs: Additional keyword arguments passed along with request,
                e.g., `timeout`, the request timeout in seconds (`None`
                disables it).

        Returns:
            WES API schema `RunListResponse`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        self.set_headers()
        url = f"{self.url}/runs"
        response: ErrorResponse | RunListResponse
        try:
            response_unvalidated = self.session.get(url, **kwargs).json()
        except (RequestException, ValueError) as exc:
            raise EngineUnavailable("external workflow engine unavailable") from exc
        try:
            response = RunListResponse(**response_unvalidated)
        except ValidationError:
            try:
                response = ErrorResponse(**response_unvalidated)
            except ValidationError as exc:
                raise ValueError(f"invalid response: {response_unvalidated}") from exc
        return response

    def get_run(
        self,
        run_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Retrieve detailed information about a workflow run.

        The response is not validated against the WES API schema `RunLog`
        (workaround for cwl-WES, whose responses don't conform to it).

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request,
                e.g., `timeout`, the request timeout in seconds (`None`
                disables it).

        Returns:
            Response object, as sent; according to WES API schema `RunLog` if
                the service conforms to it. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            pro_wes.exceptions.EngineUnavailable: The service is unavailable,
                or its response is no JSON object.
        """
        self.set_headers()
        url = f"{self.url}/runs/{run_id}"
        try:
            response_unvalidated = self.session.get(url, **kwargs).json()
        except (RequestException, ValueError) as exc:
            raise EngineUnavailable("external workflow engine unavailable") from exc
        if not isinstance(response_unvalidated, dict):
            raise EngineUnavailable("external workflow engine sent no JSON object")
        return response_unvalidated

    def get_run_status(
        self,
        run_id: str,
        **kwargs: Any,
    ) -> ErrorResponse | RunStatus:
        """Retrieve status information about a workflow run.

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request,
                e.g., `timeout`, the request timeout in seconds (`None`
                disables it).

        Returns:
            WES API schema `RunStatus`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        self.set_headers()
        url = f"{self.url}/runs/{run_id}/status"
        response: ErrorResponse | RunStatus
        try:
            response_unvalidated = self.session.get(url, **kwargs).json()
        except (RequestException, ValueError) as exc:
            raise EngineUnavailable("external workflow engine unavailable") from exc
        try:
            response = RunStatus(**response_unvalidated)
        except ValidationError:
            try:
                response = ErrorResponse(**response_unvalidated)
            except ValidationError as exc:
                raise ValueError(f"invalid response: {response_unvalidated}") from exc
        return response

    def cancel_run(
        self,
        run_id: str,
        **kwargs: Any,
    ) -> ErrorResponse | RunId:
        """Cancel workflow run.

        Args:
            run_id: Workflow run identifier.
            **kwargs: Additional keyword arguments passed along with request,
                e.g., `timeout`, the request timeout in seconds (`None`
                disables it).

        Returns:
            WES API schema `RunId`. Cf.
                `pro_wes/api/20230914.tag_1_1_0.workflow_execution_service.openapi.yaml`

        Raises:
            ValueError: The response does not conform to any of the expected
                schemas.
        """
        self.set_headers()
        url = f"{self.url}/runs/{run_id}/cancel"
        response: ErrorResponse | RunId
        try:
            response_unvalidated = self.session.post(url, **kwargs).json()
        except (RequestException, ValueError) as exc:
            raise EngineUnavailable("external workflow engine unavailable") from exc
        try:
            response = RunId(**response_unvalidated)
        except ValidationError:
            try:
                response = ErrorResponse(**response_unvalidated)
            except ValidationError as exc:
                raise ValueError(f"invalid response: {response_unvalidated}") from exc
        return response

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

    def set_headers(
        self,
        content_accept: str = "application/json",
        content_type: str | None = None,
    ) -> None:
        """Set session headers.

        Headers that aren't given are removed from the session, so that they
        don't stick to later requests: `Content-Type` if `content_type` is
        `None`, and `Authorization` if the client has no or an empty token
        (no anonymous request carries `Authorization: Bearer None`).

        Args:
            content_accept: Requested MIME/content type.
            content_type: Type of content sent with the request.
        """
        headers: dict[str, str | None] = {
            "Accept": content_accept,
            "Content-Type": content_type,
            "Authorization": f"Bearer {self.token}" if self.token else None,
        }
        for name, value in headers.items():
            if value is None:
                self.session.headers.pop(name, None)
            else:
                self.session.headers[name] = value
