"""Tests that run logs (`GET /runs/{run_id}`) conform to the served specification.

The form of `POST /runs` has the JSON fields of the run request
(`workflow_params`, `tags`, `workflow_engine_parameters`) as JSON strings,
while the `RunRequest` of run logs has them as objects, and those of `tags`
and `workflow_engine_parameters` with string values (WES 1.1.0). These
tests validate the run logs of new runs, and of runs as earlier versions
stored them, against the schema `RunLog` of the served specification.
"""

from json import loads
from typing import Any

from foca.testing import Client
from jsonschema import Draft4Validator
from pymongo import MongoClient
import pytest

from pro_wes.ga4gh.wes.client_wes import WesClient
from pro_wes.ga4gh.wes.models import RunId
from pro_wes.tasks.track_run_progress import task__track_run_progress
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import OWNER

BASE_PATH = "/ga4gh/wes/v1"
BOUNDARY = "prowes-run-log-boundary"
RUN_REQUEST: dict[str, str] = {
    "workflow_params": '{"input": "value"}',
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
}
STORED_RUN_ID = "OLDRUN"


def _multipart(fields: dict[str, str]) -> bytes:
    """Encode form fields as `multipart/form-data`.

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


def _post_run(client: Client, fields: dict[str, str]) -> Any:
    """Send `POST /runs` with form fields.

    Args:
        client: Test client of the app.
        fields: Form fields of the run request.

    Returns:
        Response.
    """
    return client.post(
        f"{BASE_PATH}/runs",
        content=_multipart(fields),
        headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
    )


@pytest.fixture
def remote_wes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Fake the remote WES and the tracking of runs.

    Runs are accepted under a fixed remote run identifier, and their
    tracking tasks are not queued.

    Args:
        monkeypatch: Monkeypatch fixture of the test.

    Returns:
        Form data of the run requests sent to the remote WES, in order.
    """
    sent: list[dict[str, Any]] = []

    def _post(self: WesClient, form_data: dict[str, Any], **kwargs: Any) -> RunId:
        """Accept a run.

        Args:
            self: WES client.
            form_data: Form data of the run request.
            **kwargs: Further arguments; ignored.

        Returns:
            Identifier of the run.
        """
        sent.append(form_data)
        return RunId(run_id="REMOTE1")

    monkeypatch.setattr(WesClient, "post_run", _post)
    monkeypatch.setattr(
        task__track_run_progress, "apply_async", lambda *args, **kwargs: None
    )
    return sent


@pytest.fixture
def run_log_validator(foca_client: Client) -> Draft4Validator:
    """Get a validator of the schema `RunLog` of the served specification.

    Args:
        foca_client: Test client of the app.

    Returns:
        Validator; Draft 4 resolves `$ref` against the root, which has the
        components.
    """
    response = foca_client.get(f"{BASE_PATH}/openapi.json")
    assert response.status_code == 200
    spec: dict[str, Any] = response.json()
    return Draft4Validator(
        {"components": spec["components"], "$ref": "#/components/schemas/RunLog"}
    )


def _assert_conforms(validator: Draft4Validator, body: Any) -> None:
    """Check that a run log conforms to the schema `RunLog`.

    Args:
        validator: Validator of the schema `RunLog`.
        body: Run log.
    """
    errors = [
        f"{list(error.absolute_path)}: {error.message}"
        for error in validator.iter_errors(body)
    ]
    assert errors == []


@pytest.mark.usefixtures("remote_wes")
@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {},
            {
                "workflow_params": {"input": "value"},
                "tags": {},
                "workflow_engine_parameters": {},
            },
        ),
        (
            {
                "workflow_params": '{"inputs": {"n": 1, "list": [1, "a", null]}}',
                "tags": '{"project": "p1", "empty": ""}',
                "workflow_engine_parameters": '{"--debug": "true"}',
            },
            {
                "workflow_params": {"inputs": {"n": 1, "list": [1, "a", None]}},
                "tags": {"project": "p1", "empty": ""},
                "workflow_engine_parameters": {"--debug": "true"},
            },
        ),
        # empty optional fields are empty objects
        (
            {"tags": "", "workflow_engine_parameters": "null"},
            {
                "workflow_params": {"input": "value"},
                "tags": {},
                "workflow_engine_parameters": {},
            },
        ),
    ],
)
def test_new_run_log_conforms(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    run_log_validator: Draft4Validator,
    fields: dict[str, str],
    expected: dict[str, Any],
) -> None:
    """The run request of new runs is returned with objects, as specified.

    The database keeps the JSON fields as JSON text, as sent to the WES
    instance.
    """
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    assert response.status_code == 200
    stored = mongodb[RUN_DB][RUN_COLLECTION].find_one({})
    assert stored is not None
    stored_request = stored["run_log"]["request"]
    assert {field: loads(stored_request[field]) for field in expected} == expected
    run = foca_client.get(f"{BASE_PATH}/runs/{response.json()['run_id']}")
    assert run.status_code == 200
    _assert_conforms(run_log_validator, run.json())
    request = run.json()["request"]
    assert {field: request[field] for field in expected} == expected


def _store_run(mongodb: MongoClient[dict[str, Any]], request: dict[str, Any]) -> None:
    """Store a run as earlier versions stored it.

    Args:
        mongodb: Database of the test.
        request: Run request, as stored.
    """
    mongodb[RUN_DB][RUN_COLLECTION].insert_one(
        {
            "user_id": OWNER,
            "run_log": {
                "run_id": STORED_RUN_ID,
                "state": "COMPLETE",
                "request": {
                    "workflow_type": "CWL",
                    "workflow_type_version": "v1.0",
                    "workflow_url": "https://example.org/workflow.cwl",
                    **request,
                },
            },
        }
    )


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # JSON strings, as all earlier versions stored them
        (
            {
                "workflow_params": '{"input": "value"}',
                "tags": '{"project": "p1"}',
                "workflow_engine_parameters": '{"--debug": "true"}',
            },
            {
                "workflow_params": {"input": "value"},
                "tags": {"project": "p1"},
                "workflow_engine_parameters": {"--debug": "true"},
            },
        ),
        # values other than strings, which versions before 0.20.0 accepted,
        # as JSON strings
        (
            {
                "workflow_params": '{"input": "value"}',
                "tags": '{"n": 1, "flag": true, "none": null, "obj": {"a": "b"}}',
                "workflow_engine_parameters": '{"threads": 4, "list": [1, "a"]}',
            },
            {
                "workflow_params": {"input": "value"},
                "tags": {
                    "n": "1",
                    "flag": "true",
                    "none": "null",
                    "obj": '{"a": "b"}',
                },
                "workflow_engine_parameters": {"threads": "4", "list": '[1, "a"]'},
            },
        ),
        # without the optional fields
        (
            {"workflow_params": '{"input": "value"}'},
            {
                "workflow_params": {"input": "value"},
                "tags": {},
                "workflow_engine_parameters": {},
            },
        ),
    ],
)
def test_stored_run_log_conforms(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    run_log_validator: Draft4Validator,
    stored: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    """The run request of stored runs is returned with objects, as specified."""
    _store_run(mongodb, stored)
    run = foca_client.get(f"{BASE_PATH}/runs/{STORED_RUN_ID}")
    assert run.status_code == 200
    _assert_conforms(run_log_validator, run.json())
    request = run.json()["request"]
    assert {field: request[field] for field in expected} == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"workflow_params": "not json"},
        {"workflow_params": "[1]"},
        {"tags": "{"},
        {"tags": '"text"'},
        {"workflow_engine_parameters": "1"},
    ],
    ids=["not-json", "array", "truncated", "string", "number"],
)
def test_fields_not_json_objects_refused(
    foca_client: Client,
    mongodb: MongoClient[dict[str, Any]],
    remote_wes: list[dict[str, Any]],
    fields: dict[str, str],
) -> None:
    """JSON fields that aren't JSON objects get `400`, before anything is stored."""
    response = _post_run(foca_client, {**RUN_REQUEST, **fields})
    assert response.status_code == 400
    assert mongodb[RUN_DB][RUN_COLLECTION].count_documents({}) == 0
    assert not remote_wes


def test_stored_nested_credentials_not_returned(
    foca_client: Client, mongodb: MongoClient[dict[str, Any]]
) -> None:
    """Credentials in legacy values other than strings are removed first.

    That is, before such values are returned as their JSON text, so that
    none hides in the text of a nested object or array.
    """
    _store_run(
        mongodb,
        {
            "workflow_params": '{"input": "value"}',
            "tags": '{"obj": {"u": "https://user:s3cr3t@example.org/x"}}',
            "workflow_engine_parameters": (
                '{"l": ["https://user:s3cr3t@example.org/y"]}'
            ),
        },
    )
    run = foca_client.get(f"{BASE_PATH}/runs/{STORED_RUN_ID}")
    assert run.status_code == 200
    assert "s3cr3t" not in run.text
    request = run.json()["request"]
    assert loads(request["tags"]["obj"]) == {"u": "https://example.org/x"}
    assert loads(request["workflow_engine_parameters"]["l"]) == [
        "https://example.org/y"
    ]
