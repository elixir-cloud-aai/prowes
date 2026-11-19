"""Contract tests of the WES specification that proWES serves (WES 1.1.0).

The app serves the vendored WES 1.1.0 specification (`pro_wes/api/`), and
its models follow the schemas of that specification. The vendored file
differs from the published one in one change only (`VENDORED_CHANGES`), and
the served specification (with `additions.openapi.yaml` merged over it) from
the vendored one only in the deviations of `ALLOWED_DEVIATIONS` (cf.
`docs/api.md`).
"""

from collections.abc import Callable, Iterator
from hashlib import sha256
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from foca.config.config_parser import ConfigParser
from foca.testing import Client
from pydantic import BaseModel
import pytest
import yaml

import pro_wes
from pro_wes.ga4gh.wes.models import Log, RunLog, ServiceInfoWesBase, State

API_DIR = Path(pro_wes.__file__).parent / "api"
SPEC_FILE = API_DIR / "20230914.tag_1_1_0.workflow_execution_service.openapi.yaml"
ADDITIONS_FILE = API_DIR / "additions.openapi.yaml"
CONFIG_FILE = Path(pro_wes.__file__).parent / "config.yaml"
BASE_PATH = "/ga4gh/wes/v1"

# SHA-256 of the published WES 1.1.0 specification (tag 1.1.0, 226f43b),
# `openapi/workflow_execution_service.openapi.yaml` of
# https://github.com/ga4gh/workflow-execution-service-schemas
UPSTREAM_SHA256 = "9cdbcdf5871a05dec05b90e2dc10e8d8cb60a84efda6e17d3531a21c1129e467"

# The only change of the vendored file, as (vendored text, published text):
# the service info schema is referenced in `additions.openapi.yaml` instead
# of on GitHub, from where it would be fetched on startup
VENDORED_CHANGES: list[tuple[str, str]] = [
    (
        "        # Deviation from upstream: referenced the schema in\n"
        "        # https://raw.githubusercontent.com/ga4gh-discovery/"
        "ga4gh-service-info/v1.0.0/service-info.yaml\n"
        "        # (fetched at startup); copied to `additions.openapi.yaml`\n"
        "        - $ref: '#/components/schemas/Service'\n",
        "        - $ref: 'https://raw.githubusercontent.com/ga4gh-discovery/"
        "ga4gh-service-info/v1.0.0/service-info.yaml#/components/schemas/Service'\n",
    ),
]

# Location of a value in the specification (keys of the nested mappings)
Location = tuple[Any, ...]

ERROR_SCHEMA_REF = "#/components/schemas/ProblemDetails"
TASK_PATHS = ("/runs/{run_id}/tasks", "/runs/{run_id}/tasks/{task_id}")
RUN_PATHS = (
    "/runs/{run_id}",
    "/runs/{run_id}/status",
    "/runs/{run_id}/cancel",
    *TASK_PATHS,
)


def _is_error_schema(location: Location, value: Any) -> bool:
    """Tell whether a deviation is the schema of an error response.

    Args:
        location: Location of the deviating value.
        value: Value in the served specification.

    Returns:
        Whether the value is the `$ref` to `ProblemDetails` of an error
        response (status 400 or higher) of an operation.
    """
    return (
        len(location) == 9
        and location[0] == "paths"
        and location[3] == "responses"
        and int(location[4]) >= 400
        and location[5:] == ("content", "application/json", "schema", "$ref")
        and value == ERROR_SCHEMA_REF
    )


def _is_task_501(location: Location, value: Any) -> bool:
    """Tell whether a deviation is the `501` response of a task endpoint.

    Args:
        location: Location of the deviating value.
        value: Value in the served specification; not checked.

    Returns:
        Whether the location is that of the `501` response of a task
        endpoint.
    """
    return (
        len(location) == 5
        and location[0] == "paths"
        and location[1] in TASK_PATHS
        and location[2:] == ("get", "responses", 501)
    )


# Deviations of the merged specification (vendored file and additions, as
# FOCA merges them before serving) from the vendored one, by name, as listed
# in `docs/api.md`: functions of the location and the merged value of a
# deviating value that tell whether the deviation is one of them. That all
# error responses are problem details is checked on the served specification
# (`tests/ga4gh/wes/test_error_responses.py`)
ALLOWED_DEVIATIONS: dict[str, Callable[[Location, Any], bool]] = {
    "OpenAPI version": lambda loc, _: loc == ("openapi",),
    "info of proWES": lambda loc, _: loc[:1] == ("info",) and loc[1:2] != ("version",),
    "servers of proWES": lambda loc, _: loc == ("servers",),
    "service info served by FOCA": lambda loc, _: (
        loc == ("paths", "/service-info", "get", "x-openapi-router-controller")
    ),
    "service info schemas": lambda loc, _: (
        loc[:3]
        in {
            ("components", "schemas", "Service"),
            ("components", "schemas", "ServiceType"),
        }
    ),
    "problem details schema": lambda loc, _: (
        loc[:3] == ("components", "schemas", "ProblemDetails")
    ),
    "problem details of error responses": _is_error_schema,
    "413, 415 and 503 of POST /runs": lambda loc, _: (
        loc
        in {
            ("paths", "/runs", "post", "responses", status)
            for status in (413, 415, 503)
        }
    ),
    "409 of POST /runs/{run_id}/cancel": lambda loc, _: (
        loc == ("paths", "/runs/{run_id}/cancel", "post", "responses", 409)
    ),
    "descriptions of 401, 403 and 500 of POST /runs/{run_id}/cancel": lambda loc, _: (
        loc
        in {
            (
                "paths",
                "/runs/{run_id}/cancel",
                "post",
                "responses",
                status,
                "description",
            )
            for status in (401, 403, 500)
        }
    ),
    "501 of the task endpoints": _is_task_501,
    "access tokens (bearer tokens), but for the service info": lambda loc, _: (
        loc in {("security",), ("paths", "/service-info", "get", "security")}
        or loc[:2] == ("components", "securitySchemes")
    ),
    "400 of the endpoints of a run": lambda loc, _: (
        len(loc) == 5
        and loc[0] == "paths"
        and loc[1] in RUN_PATHS
        and loc[3:] == ("responses", 400)
    ),
}

# Fields of the specification that proWES doesn't model yet, with the issues
# that add them; the contract tests fail when one of them is added, so that
# this list is kept up to date
NOT_YET_MODELED: dict[str, set[str]] = {
    # `task_logs_url` (#124)
    "RunLog": {"task_logs_url"},
    # fields of task logs (`TaskLog`) beyond those of `Log` (#124)
    "TaskLog": {"id", "tes_uri"},
}


@pytest.fixture(scope="module")
def schemas() -> dict[str, Any]:
    """Get the schemas of the vendored WES specification.

    Returns:
        Section `components.schemas` of the specification.
    """
    spec = yaml.safe_load(SPEC_FILE.read_text(encoding="utf-8"))
    result: dict[str, Any] = spec["components"]["schemas"]
    return result


def _fields(model: type[BaseModel]) -> set[str]:
    """Get the field names of a model.

    Args:
        model: Model class.

    Returns:
        Names of its fields.
    """
    return set(model.model_fields)


def test_only_wes_1_1_0_spec_vendored() -> None:
    """The WES 1.1.0 specification is the only one vendored and served."""
    specs = sorted(path.name for path in API_DIR.glob("*workflow_execution_service*"))
    assert specs == [SPEC_FILE.name]
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    paths = config["api"]["specs"][0]["path"]
    assert paths == [f"api/{SPEC_FILE.name}", "api/additions.openapi.yaml"]


def test_served_spec_is_wes_1_1_0(foca_client: Client) -> None:
    """The app serves WES 1.1.0, with the task endpoints."""
    response = foca_client.get(f"{BASE_PATH}/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["version"] == "1.1.0"
    assert "/runs/{run_id}/tasks" in spec["paths"]
    assert "/runs/{run_id}/tasks/{task_id}" in spec["paths"]
    # the service info schema is local (not fetched from GitHub on startup)
    service_info = spec["components"]["schemas"]["ServiceInfo"]
    assert service_info["allOf"][0] == {"$ref": "#/components/schemas/Service"}


def test_states_module_replaced() -> None:
    """The run states are those of the `State` model (no `states` module)."""
    assert find_spec("pro_wes.ga4gh.wes.states") is None


def test_state_matches_spec(schemas: dict[str, Any]) -> None:
    """The states are those of the specification."""
    values = {state.value for state in State}
    assert values == set(schemas["State"]["enum"])


def test_log_matches_spec(schemas: dict[str, Any]) -> None:
    """The fields of logs are those of the specification."""
    assert _fields(Log) == set(schemas["Log"]["properties"])


def test_run_log_matches_spec(schemas: dict[str, Any]) -> None:
    """The fields of run logs are those of the specification."""
    expected = set(schemas["RunLog"]["properties"]) - NOT_YET_MODELED["RunLog"]
    assert _fields(RunLog) == expected


def test_service_info_matches_spec(schemas: dict[str, Any]) -> None:
    """The WES fields of the service info are those of the specification."""
    wes_fields = schemas["ServiceInfo"]["allOf"][1]["properties"]
    # the state counts are computed (`ServiceInfo`), not configured
    assert _fields(ServiceInfoWesBase) == set(wes_fields) - {"system_state_counts"}


def test_task_log_matches_spec(schemas: dict[str, Any]) -> None:
    """Task logs are stored as logs, without the fields of `TaskLog` (#124)."""
    task_fields = set(schemas["TaskLog"]["allOf"][1]["properties"])
    assert task_fields - _fields(Log) == NOT_YET_MODELED["TaskLog"]


def _deviations(base: Any, served: Any, location: Location = ()) -> Iterator[Location]:
    """Get the locations of the values of a specification that differ.

    Args:
        base: Value of the vendored specification at the location.
        served: Value of the served specification at the location.
        location: Location of the values.

    Yields:
        Locations of the values that the served specification adds, removes
        or changes, as deep as both values are mappings.
    """
    if isinstance(base, dict) and isinstance(served, dict):
        for key in base.keys() | served.keys():
            if key in base and key in served:
                yield from _deviations(base[key], served[key], (*location, key))
            else:
                yield (*location, key)
    elif base != served:
        yield location


def _get(spec: dict[str, Any], location: Location) -> Any:
    """Get the value at a location of a specification.

    Args:
        spec: Specification.
        location: Location of the value.

    Returns:
        The value, or `None` if there is none.
    """
    value: Any = spec
    for key in location:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def test_vendored_spec_changes_only_service_info_ref() -> None:
    """The vendored file is the published one, but for `VENDORED_CHANGES`."""
    text = SPEC_FILE.read_text(encoding="utf-8")
    for vendored, published in VENDORED_CHANGES:
        assert text.count(vendored) == 1
        text = text.replace(vendored, published)
    assert sha256(text.encode("utf-8")).hexdigest() == UPSTREAM_SHA256


def test_merged_spec_deviates_only_as_allowed() -> None:
    """The additions deviate from WES 1.1.0 only in `ALLOWED_DEVIATIONS`.

    The specification is merged as FOCA merges it before serving. Each
    allowed deviation is used, so that the list is kept up to date.
    """
    vendored = ConfigParser.merge_yaml(SPEC_FILE)
    served = ConfigParser.merge_yaml(SPEC_FILE, ADDITIONS_FILE)
    used: set[str] = set()
    unexpected: list[Location] = []
    for location in _deviations(vendored, served):
        value = _get(served, location)
        names = {
            name
            for name, allowed in ALLOWED_DEVIATIONS.items()
            if allowed(location, value)
        }
        if not names:
            unexpected.append(location)
        used |= names
    assert unexpected == []
    assert used == set(ALLOWED_DEVIATIONS)
