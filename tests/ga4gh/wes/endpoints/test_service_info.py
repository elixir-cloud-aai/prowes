"""Tests of the service info (`GET /service-info`), served by FOCA.

The fields come from config section `service_info` and the system state
counts from :py:func:`pro_wes.ga4gh.wes.service_info.runtime_fields`.
"""

from collections.abc import Callable, Iterator
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from connexion import App
from foca import get_collection
from foca.testing import Client
from pydantic import ValidationError
import pytest
import yaml

import pro_wes
from pro_wes.app import init_app
from pro_wes.config_models import CustomConfig
from pro_wes.ga4gh.wes.models import State
from pro_wes.ga4gh.wes.service_info import runtime_fields, state_counts
from pro_wes.utils import config as config_utils
from pro_wes.utils.config import workflow_type_versions
from pro_wes.utils.db_names import RUN_COLLECTION, RUN_DB
from tests.conftest import SERVICE_INFO_ID

URL = "/ga4gh/wes/v1/service-info"
CONFIG_FILE = Path(pro_wes.__file__).parent / "config.yaml"


def _configured_service() -> dict[str, Any]:
    """Get the service info of `pro_wes/config.yaml` with the test overlay.

    Returns:
        Section `service_info.service`, with the identifier of the overlay.
    """
    config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    service: dict[str, Any] = config["service_info"]["service"]
    return {**service, "id": SERVICE_INFO_ID}


@pytest.fixture
def app_context(foca_app: App) -> Iterator[None]:
    """Enter the app context of the app.

    Args:
        foca_app: The app.

    Yields:
        Nothing; within the app context.
    """
    with foca_app.app.app_context():
        yield


def test_get_service_info(foca_client: Client) -> None:
    """`GET /service-info` serves the configured fields, version and counts."""
    response = foca_client.get(URL)
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["id"] == SERVICE_INFO_ID
    assert body["type"] == {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"}
    assert body["contactUrl"] == "mailto:support@example.org"
    assert body["workflow_type_versions"] == {
        "CWL": {"workflow_type_version": ["v1.0"]}
    }
    assert body["supported_wes_versions"] == ["1.1.0"]
    # WES 1.1.0: versions of each engine as objects
    assert body["workflow_engine_versions"] == {
        "cwl-engine": {"workflow_engine_version": ["1.2.3"]}
    }
    assert None not in body.values()
    assert body.pop("system_state_counts") == {state.value: 0 for state in State}
    assert body.pop("version") == version("pro-wes")
    assert body == _configured_service()


def test_get_service_info_counts_states(foca_app: App, foca_client: Client) -> None:
    """The system state counts reflect the stored runs, at each request."""
    assert foca_client.get(URL).json()["system_state_counts"]["RUNNING"] == 0
    with foca_app.app.app_context():
        get_collection(RUN_DB, RUN_COLLECTION).insert_many(
            [
                {"run_log": {"state": "RUNNING"}},
                {"run_log": {"state": "RUNNING"}},
                {"run_log": {"state": "COMPLETE"}},
            ]
        )
    counts = foca_client.get(URL).json()["system_state_counts"]
    assert counts["RUNNING"] == 2
    assert counts["COMPLETE"] == 1
    assert sum(counts.values()) == 3


def test_post_service_info_not_allowed(foca_client: Client) -> None:
    """`POST /service-info` is gone: the service info is configured."""
    response = foca_client.post(URL, json=_configured_service())
    assert response.status_code == 405


@pytest.mark.usefixtures("app_context")
def test_state_counts_unknown_state() -> None:
    """Runs with unknown states are not counted, without failing."""
    get_collection(RUN_DB, RUN_COLLECTION).insert_many(
        [{"run_log": {"state": "OTHER"}}, {"run_log": {"state": "RUNNING"}}, {}]
    )
    counts = state_counts()
    assert "OTHER" not in counts
    assert counts["RUNNING"] == 1
    assert sum(counts.values()) == 1
    assert runtime_fields() == {"system_state_counts": counts}


@pytest.mark.usefixtures("app_context")
def test_state_counts_preempted() -> None:
    """Preempted runs (WES 1.1.0) are counted."""
    get_collection(RUN_DB, RUN_COLLECTION).insert_one(
        {"run_log": {"state": "PREEMPTED"}}
    )
    assert state_counts()["PREEMPTED"] == 1


def test_state_counts_not_configurable(add_overlay: Callable[[str], None]) -> None:
    """Configured state counts are refused: proWES computes them."""
    add_overlay(
        "service_info:\n  service:\n    system_state_counts:\n      RUNNING: 5\n"
    )
    with pytest.raises(ValidationError, match="system_state_counts"):
        init_app()


@pytest.mark.parametrize(
    "overlay",
    [
        "service_info:\n  service:\n    workflow_type_versions: null\n",
        "service_info:\n  service:\n    auth_instructions_url: null\n",
        "service_info:\n  service:\n    tags: not a mapping\n",
        "service_info:\n  service:\n    contactUrl: support@example.org\n",
        # WES 1.0.1 form of the engine versions (strings)
        "service_info:\n  service:\n    workflow_engine_versions:\n"
        "      cwl-engine: 1.2.3\n",
    ],
)
def test_init_app_checks_service_info(
    add_overlay: Callable[[str], None], overlay: str
) -> None:
    """The app doesn't start with invalid or missing service info fields."""
    add_overlay(overlay)
    with pytest.raises(ValidationError):
        init_app()


def test_init_app_refuses_custom_service_info(
    add_overlay: Callable[[str], None],
) -> None:
    """The app doesn't start with the service info of earlier versions."""
    add_overlay("custom:\n  service_info:\n    id: org.example.prowes\n")
    with pytest.raises(ValueError, match="custom configuration") as exc_info:
        init_app()
    # FOCA tells what to correct in the cause
    assert "service_info.service" in str(exc_info.value.__cause__)


def test_custom_service_info_refused() -> None:
    """Service info in the custom section of earlier versions is refused."""
    with pytest.raises(ValidationError, match=r"service_info\.service"):
        CustomConfig.model_validate({"service_info": {"id": "org.example.prowes"}})


@pytest.mark.usefixtures("app_context")
def test_workflow_type_versions() -> None:
    """The workflow types and versions are those of the configuration."""
    assert workflow_type_versions() == {"CWL": {"workflow_type_version": ["v1.0"]}}


def test_workflow_type_versions_without_service_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without service info (which FOCA doesn't allow), no types are known."""
    monkeypatch.setattr(
        config_utils, "get_config", lambda: SimpleNamespace(service_info=None)
    )
    with pytest.raises(RuntimeError):
        workflow_type_versions()
