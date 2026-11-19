"""Tests that error responses are those that the served specification declares.

Error responses are problem details (`pro_wes.exceptions`). The served
specification declares them (`ProblemDetails`, `additions.openapi.yaml`)
for every error status of every operation; these tests send requests that
fail and validate the responses against the declared status, media type and
schema.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any

from foca.errors.fields import MAX_FIELD_ERRORS
from foca.testing import Client
from jsonschema import Draft4Validator
from pymongo import MongoClient
import pytest

from pro_wes.ga4gh.wes import controllers
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB

BASE_PATH = "/ga4gh/wes/v1"
BOUNDARY = "prowes-errors-boundary"
RUN_REQUEST: dict[str, str] = {
    "workflow_params": '{"input": "value"}',
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}


@pytest.fixture
def served_spec(foca_client: Client) -> dict[str, Any]:
    """Get the specification that the app serves.

    Args:
        foca_client: Test client of the app.

    Returns:
        The served specification (`openapi.json`).
    """
    response = foca_client.get(f"{BASE_PATH}/openapi.json")
    assert response.status_code == 200
    spec: dict[str, Any] = response.json()
    return spec


@pytest.fixture
def client_with(
    add_overlay: Callable[[str], None],
) -> Iterator[Callable[[str], Client]]:
    """Get a function that creates a test client of the app with an overlay.

    Args:
        add_overlay: Function that merges config over the test overlay.

    Yields:
        Function taking the YAML text of an overlay, returning a client of
        an app created with it; the clients are closed afterwards.
    """
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    with ExitStack() as stack:

        def _client(text: str) -> Client:
            """Create a client of an app with an overlay.

            Args:
                text: YAML text of the overlay.

            Returns:
                Client of the app.
            """
            add_overlay(text)
            return stack.enter_context(Client(init_app()))

        yield _client


def _multipart(fields: dict[str, str]) -> bytes:
    """Encode form fields as `multipart/form-data`.

    Args:
        fields: Names and values of the form fields.

    Returns:
        Body, with the boundary `BOUNDARY`.
    """
    parts = [
        (
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()
        for name, value in fields.items()
    ]
    return b"".join(parts) + f"--{BOUNDARY}--\r\n".encode()


def _post_run(
    client: Client, fields: dict[str, str], params: dict[str, str] | None = None
) -> Any:
    """Send `POST /runs` with form fields.

    Args:
        client: Test client of the app.
        fields: Form fields of the run request.
        params: Query parameters, if any.

    Returns:
        Response.
    """
    return client.post(
        f"{BASE_PATH}/runs",
        params=params,
        content=_multipart(fields),
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


def assert_declared_error(
    spec: dict[str, Any], method: str, path: str, response: Any, status: int
) -> None:
    """Check an error response against the operation that it answers.

    Args:
        spec: Served specification.
        method: HTTP method of the operation, in lower case.
        path: Path of the operation, as in the specification.
        response: Error response.
        status: Expected status code.

    Raises:
        AssertionError: The response doesn't have the expected status (as
            HTTP status and member `status`), or the status, the media type
            or the body isn't declared for the operation.
    """
    assert response.status_code == status
    declared = spec["paths"][path][method]["responses"]
    assert str(status) in declared, f"{status} not declared for {method} {path}"
    content = declared[str(status)]["content"]
    media_type = response.headers["Content-Type"].split(";")[0].strip()
    assert media_type in content, f"{media_type} not declared for {status}"
    schema = content[media_type]["schema"]
    assert schema == {"$ref": "#/components/schemas/ProblemDetails"}
    # Draft 4 resolves `$ref` against the root, which has the components
    validator = Draft4Validator({"components": spec["components"], **schema})
    body = response.json()
    errors = [error.message for error in validator.iter_errors(body)]
    assert errors == []
    assert body["status"] == status


def test_problem_details_declared(served_spec: dict[str, Any]) -> None:
    """All error responses of all operations are declared as problem details."""
    undeclared = [
        f"{method.upper()} {path} {status}"
        for path, path_item in served_spec["paths"].items()
        for method, operation in path_item.items()
        for status, response in operation["responses"].items()
        if int(status) >= 400
        and response.get("content")
        != {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetails"}
            }
        }
    ]
    assert undeclared == []


def test_problem_details_schema(served_spec: dict[str, Any]) -> None:
    """The schema has the members of problem details that proWES sends."""
    schema = served_spec["components"]["schemas"]["ProblemDetails"]
    assert schema["required"] == ["title", "status"]
    members = {"type", "title", "status", "detail", "code", "errors"}
    assert set(schema["properties"]) == members
    # FOCA lists at most 100 invalid fields (`MAX_FIELD_ERRORS`)
    assert schema["properties"]["errors"]["maxItems"] == MAX_FIELD_ERRORS


def _assert_unknown_parameter(response: Any) -> None:
    """Check that a response names the unknown parameter `unknown` only.

    Args:
        response: Error response.

    Raises:
        AssertionError: The invalid fields aren't the parameter `unknown`.
    """
    errors = response.json()["errors"]
    assert [error["parameter"] for error in errors] == ["unknown"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/service-info"),
        ("get", "/runs"),
        ("get", "/runs/{run_id}"),
        ("get", "/runs/{run_id}/status"),
        ("get", "/runs/{run_id}/tasks"),
        ("get", "/runs/{run_id}/tasks/{task_id}"),
        ("post", "/runs/{run_id}/cancel"),
    ],
)
def test_unknown_parameter(
    method: str, path: str, foca_client: Client, served_spec: dict[str, Any]
) -> None:
    """Unknown parameters get `400` on all operations, as declared.

    `POST /runs` is tested on its own (`test_run_request_unknown_parameter`).
    """
    url = path.format(run_id="RUN001", task_id="TASK001")
    response = getattr(foca_client, method)(
        f"{BASE_PATH}{url}", params={"unknown": "1"}
    )
    assert_declared_error(served_spec, method, path, response, 400)
    _assert_unknown_parameter(response)


def test_run_request_unknown_parameter(
    foca_client: Client, served_spec: dict[str, Any]
) -> None:
    """Run requests with unknown parameters get `400`, as declared."""
    response = _post_run(foca_client, RUN_REQUEST, params={"unknown": "1"})
    assert_declared_error(served_spec, "post", "/runs", response, 400)
    _assert_unknown_parameter(response)


@pytest.mark.parametrize(
    ("method", "path", "url"),
    [
        ("get", "/runs/{run_id}", "/runs/UNKNOWN"),
        ("get", "/runs/{run_id}/status", "/runs/UNKNOWN/status"),
        ("post", "/runs/{run_id}/cancel", "/runs/UNKNOWN/cancel"),
    ],
)
def test_run_not_found(
    method: str,
    path: str,
    url: str,
    foca_client: Client,
    served_spec: dict[str, Any],
) -> None:
    """Unknown runs get `404`, as declared."""
    response = getattr(foca_client, method)(f"{BASE_PATH}{url}")
    assert_declared_error(served_spec, method, path, response, 404)
    assert response.json()["detail"] == "The requested run wasn't found."


@pytest.mark.parametrize(
    ("path", "url"),
    [
        ("/runs/{run_id}/tasks", "/runs/RUN001/tasks"),
        ("/runs/{run_id}/tasks/{task_id}", "/runs/RUN001/tasks/TASK001"),
    ],
)
def test_tasks_not_implemented(
    path: str, url: str, foca_client: Client, served_spec: dict[str, Any]
) -> None:
    """The task endpoints get `501`, as declared."""
    response = foca_client.get(f"{BASE_PATH}{url}")
    assert_declared_error(served_spec, "get", path, response, 501)


def test_cancel_finished_run(
    foca_client: Client,
    served_spec: dict[str, Any],
    mongodb: MongoClient[dict[str, Any]],
) -> None:
    """Cancellations of finished runs get `409`, as declared."""
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {"run_log": {"run_id": "DONE01", "state": "COMPLETE"}}
    )
    response = foca_client.post(f"{BASE_PATH}/runs/DONE01/cancel")
    assert_declared_error(served_spec, "post", "/runs/{run_id}/cancel", response, 409)


def test_invalid_parameter(foca_client: Client, served_spec: dict[str, Any]) -> None:
    """Invalid parameters get `400` with the invalid fields, as declared."""
    response = foca_client.get(f"{BASE_PATH}/runs", params={"page_size": "many"})
    assert_declared_error(served_spec, "get", "/runs", response, 400)
    assert response.json()["errors"]


def test_invalid_run_request(foca_client: Client, served_spec: dict[str, Any]) -> None:
    """Run requests without required fields get `400`, as declared."""
    fields = {k: v for k, v in RUN_REQUEST.items() if k != "workflow_url"}
    response = _post_run(foca_client, fields)
    assert_declared_error(served_spec, "post", "/runs", response, 400)


@pytest.mark.parametrize(
    ("content", "headers"),
    [
        (b'{"workflow_url": "x"}', {"Content-Type": "application/json"}),
        (b"workflow_url=x", {"Content-Type": "text/plain"}),
        (b"workflow_url=x", {}),
        (b"", {}),
    ],
    ids=["json", "text", "no-media-type", "no-body"],
)
def test_run_request_unsupported_media_type(
    content: bytes,
    headers: dict[str, str],
    foca_client: Client,
    served_spec: dict[str, Any],
) -> None:
    """Run requests that aren't forms get `415`, as declared.

    Args:
        content: Body of the request.
        headers: Headers of the request.
        foca_client: Test client of the app.
        served_spec: Served specification.
    """
    response = foca_client.post(f"{BASE_PATH}/runs", content=content, headers=headers)
    assert_declared_error(served_spec, "post", "/runs", response, 415)
    assert response.json()["detail"] == (
        "Run requests must be sent as multipart/form-data."
    )


def test_run_request_without_boundary(
    foca_client: Client, served_spec: dict[str, Any]
) -> None:
    """Multipart run requests without a boundary get `400`, as declared."""
    response = foca_client.post(
        f"{BASE_PATH}/runs",
        content=_multipart(RUN_REQUEST),
        headers={"Content-Type": "multipart/form-data"},
    )
    assert_declared_error(served_spec, "post", "/runs", response, 400)
    assert response.json()["detail"] == (
        "The multipart/form-data body has no boundary."
    )


def test_run_request_wrong_boundary(
    foca_client: Client, served_spec: dict[str, Any]
) -> None:
    """Multipart run requests with another boundary get `400`, as declared."""
    response = foca_client.post(
        f"{BASE_PATH}/runs",
        content=_multipart(RUN_REQUEST),
        headers={"Content-Type": "multipart/form-data; boundary=other"},
    )
    assert_declared_error(served_spec, "post", "/runs", response, 400)


@pytest.mark.parametrize(
    ("params", "parameter"),
    [
        ({"page_token": "zzz"}, "page_token"),
        ({"page_token": "0" * 23}, "page_token"),
        ({"page_size": "-1"}, "page_size"),
    ],
    ids=["malformed-token", "short-token", "negative-size"],
)
def test_invalid_page(
    params: dict[str, str],
    parameter: str,
    foca_client: Client,
    served_spec: dict[str, Any],
) -> None:
    """Malformed page tokens and negative page sizes get `400`, as declared.

    Args:
        params: Query parameters.
        parameter: The invalid parameter.
        foca_client: Test client of the app.
        served_spec: Served specification.
    """
    response = foca_client.get(f"{BASE_PATH}/runs", params=params)
    assert_declared_error(served_spec, "get", "/runs", response, 400)
    errors = response.json()["errors"]
    assert [error["parameter"] for error in errors] == [parameter]
    assert params[parameter] not in response.json()["detail"]


def test_run_request_too_large(
    client_with: Callable[[str], Client], served_spec: dict[str, Any]
) -> None:
    """Run requests larger than the size limit get `413`, as declared."""
    client = client_with("server:\n  max_content_length: 2048\n")
    fields = {**RUN_REQUEST, "workflow_params": f'{{"input": "{"x" * 4096}"}}'}
    response = _post_run(client, fields)
    assert_declared_error(served_spec, "post", "/runs", response, 413)


def test_no_backend_available(
    client_with: Callable[[str], Client], served_spec: dict[str, Any]
) -> None:
    """Runs without a WES instance to forward them to get `503`, as declared."""
    client = client_with("custom:\n  wes:\n    backends: []\n")
    response = _post_run(client, RUN_REQUEST)
    assert_declared_error(served_spec, "post", "/runs", response, 503)


def test_unexpected_error(
    foca_client: Client,
    served_spec: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected errors get `500`, as declared."""

    def _fail(*args: Any, **kwargs: Any) -> None:
        """Fail like an unexpected error.

        Args:
            *args: Positional arguments; ignored.
            **kwargs: Keyword arguments; ignored.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("internal-reason")

    monkeypatch.setattr(controllers, "WorkflowRuns", _fail)
    response = foca_client.get(f"{BASE_PATH}/runs")
    assert_declared_error(served_spec, "get", "/runs", response, 500)
    assert "internal-reason" not in response.text
