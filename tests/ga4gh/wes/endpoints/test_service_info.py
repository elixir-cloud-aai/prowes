"""Tests of the service info (`GET /service-info`), served by FOCA.

The fields come from config section `service_info` and the system state
counts from :py:func:`pro_wes.ga4gh.wes.service_info.runtime_fields`.
"""

from collections.abc import Iterator
from importlib.metadata import version
import os
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
from pro_wes.config_models import CustomConfig
from pro_wes.ga4gh.wes import service_info
from pro_wes.ga4gh.wes.service_info import (
    runtime_fields,
    state_counts,
    workflow_type_versions,
)
from pro_wes.ga4gh.wes.states import States
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


def _add_overlay(monkeypatch: pytest.MonkeyPatch, path: Path, text: str) -> None:
    """Merge a further config overlay over the test overlay.

    Args:
        monkeypatch: Monkeypatch fixture of the test.
        path: Path of the overlay to write.
        text: Content of the overlay.
    """
    path.write_text(text, encoding="utf-8")
    overlays = os.environ["FOCA_CONFIG_OVERLAY"]
    monkeypatch.setenv("FOCA_CONFIG_OVERLAY", f"{overlays}:{path}")


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
    assert body.pop("system_state_counts") == dict.fromkeys(States.ALL, 0)
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
        [{"run_log": {"state": "PREEMPTED"}}, {"run_log": {"state": "RUNNING"}}, {}]
    )
    counts = state_counts()
    assert "PREEMPTED" not in counts
    assert counts["RUNNING"] == 1
    assert sum(counts.values()) == 1
    assert runtime_fields() == {"system_state_counts": counts}


def test_state_counts_not_configurable(
    app_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Configured state counts are refused: proWES computes them."""
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    _add_overlay(
        monkeypatch,
        tmp_path / "counts.yaml",
        "service_info:\n  service:\n    system_state_counts:\n      RUNNING: 5\n",
    )
    with pytest.raises(ValidationError, match="system_state_counts"):
        init_app()


@pytest.mark.parametrize(
    "overlay",
    [
        "service_info:\n  service:\n    workflow_type_versions: null\n",
        "service_info:\n  service:\n    auth_instructions_url: null\n",
        "service_info:\n  service:\n    tags: not a mapping\n",
    ],
)
def test_init_app_checks_wes_fields(
    app_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overlay: str
) -> None:
    """The app doesn't start if the service info lacks valid WES fields."""
    from pro_wes.app import init_app  # noqa: PLC0415 (after the client patch)

    _add_overlay(monkeypatch, tmp_path / "wes.yaml", overlay)
    with pytest.raises(ValidationError):
        init_app()


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
    """Without service info, no workflow types are supported."""
    monkeypatch.setattr(
        service_info, "get_config", lambda: SimpleNamespace(service_info=None)
    )
    assert workflow_type_versions() == {}
