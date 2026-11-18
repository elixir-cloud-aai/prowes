"""Contract tests of the WES specification that proWES serves (WES 1.1.0).

The app serves the vendored WES 1.1.0 specification (`pro_wes/api/`), and
its models follow the schemas of that specification.
"""

from importlib.util import find_spec
from pathlib import Path
from typing import Any

from foca.testing import Client
from pydantic import BaseModel
import pytest
import yaml

import pro_wes
from pro_wes.ga4gh.wes.models import Log, RunLog, ServiceInfoWesBase, State

API_DIR = Path(pro_wes.__file__).parent / "api"
SPEC_FILE = API_DIR / "20230914.tag_1_1_0.workflow_execution_service.openapi.yaml"
CONFIG_FILE = Path(pro_wes.__file__).parent / "config.yaml"
BASE_PATH = "/ga4gh/wes/v1"

# Fields of the specification that proWES doesn't model yet, with the issues
# that add them; the contract tests fail when one of them is added, so that
# this list is kept up to date
NOT_YET_MODELED: dict[str, set[str]] = {
    # `PREEMPTED` (#123)
    "State": {"PREEMPTED"},
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
    assert values == set(schemas["State"]["enum"]) - NOT_YET_MODELED["State"]


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
