"""Tests of the WES versions and adapters (`pro_wes.ga4gh.wes.versions`)."""

from typing import Any

import pytest

from pro_wes.ga4gh.wes.versions import (
    ADAPTERS,
    CANONICAL,
    OLDEST,
    SUPPORTED,
    Incompatibility,
    Wes11To10,
    WesVersion,
    chain,
    downgrade_run_request,
    incompatibilities,
    reported_version,
    upgrade_run_log,
)

V10 = WesVersion(1, 0)
V11 = WesVersion(1, 1)
REQUEST: dict[str, Any] = {
    "workflow_params": "{}",
    "workflow_type": "CWL",
    "workflow_type_version": "v1.0",
    "workflow_url": "https://example.org/workflow.cwl",
    "tags": "{}",
    "workflow_engine_parameters": "{}",
}
# a run log of WES 1.0.1, with a task log as `Log`
RUN_LOG_10: dict[str, Any] = {
    "run_id": "abc",
    "state": "COMPLETE",
    "run_log": {"name": "run", "exit_code": 0},
    "task_logs": [{"name": "step", "cmd": ["echo"], "exit_code": 0}],
    "outputs": {"out": "https://example.org/out"},
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.1.0", V11),
        ("v1.0.1", V10),
        ("V1.1", V11),
        (" 1.0 ", V10),
        ("1.1.0-rc1", V11),
        (1.0, V10),
        ("1", None),
        ("", None),
        ("one", None),
        (None, None),
        (True, None),
        (1, None),
    ],
)
def test_parse(value: object, expected: WesVersion | None) -> None:
    """Versions are read leniently, as WES instances report them."""
    assert WesVersion.parse(value) == expected


def test_version_str_and_order() -> None:
    """Versions are shown as `<major>.<minor>` and ordered."""
    assert str(V10) == "1.0"
    assert V10 < V11


def test_registry() -> None:
    """Runs are served in WES 1.1, and forwarded in WES 1.0 and 1.1."""
    assert CANONICAL == V11
    assert OLDEST == V10
    assert {V10, V11} == SUPPORTED
    assert [type(adapter) for adapter in ADAPTERS] == [Wes11To10]


def test_chain() -> None:
    """Chains lead from the canonical version down to a version."""
    assert chain(V11) == []
    assert chain(V10) == list(ADAPTERS)


def test_chain_unsupported() -> None:
    """Unsupported versions have no chain."""
    with pytest.raises(ValueError, match=r"2\.0"):
        chain(WesVersion(2, 0))


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, []),
        ({"workflow_engine": "", "workflow_engine_version": ""}, []),
        ({"workflow_engine": None}, []),
        ({"workflow_engine": "cwltool"}, ["workflow_engine"]),
        (
            {"workflow_engine": "cwltool", "workflow_engine_version": "3.1"},
            ["workflow_engine", "workflow_engine_version"],
        ),
        ({"workflow_engine_version": "3.1"}, ["workflow_engine_version"]),
    ],
)
def test_incompatibilities_10(fields: dict[str, Any], expected: list[str]) -> None:
    """WES 1.0 can't choose the workflow engine; empty fields choose none."""
    request = {**REQUEST, **fields}
    found = incompatibilities(request, V10)
    assert [item.field for item in found] == expected
    assert request == {**REQUEST, **fields}


def test_incompatibilities_default_oldest() -> None:
    """Without a version, the oldest one counts."""
    assert incompatibilities({**REQUEST, "workflow_engine": "cwltool"}) != []


def test_incompatibilities_11() -> None:
    """WES 1.1 can run all requests of WES 1.1."""
    assert incompatibilities({**REQUEST, "workflow_engine": "cwltool"}, V11) == []


def test_incompatibility_str() -> None:
    """Incompatibilities name the field and the reason."""
    assert str(Incompatibility("workflow_engine", "why")) == "workflow_engine: why"


def test_downgrade_10() -> None:
    """Requests for WES 1.0 lose the (empty) engine fields, and keep the rest."""
    request = {**REQUEST, "workflow_engine": "", "workflow_engine_version": ""}
    assert downgrade_run_request(request, V10) == REQUEST
    assert "workflow_engine" in request


def test_downgrade_11() -> None:
    """Requests for WES 1.1 stay as they are."""
    request = {**REQUEST, "workflow_engine": "cwltool"}
    assert downgrade_run_request(request, V11) is request


@pytest.mark.parametrize("version", [V10, V11])
def test_upgrade_run_log(version: WesVersion) -> None:
    """Run logs of WES 1.0 and 1.1 are valid in WES 1.1."""
    assert upgrade_run_log(RUN_LOG_10, version) == RUN_LOG_10


@pytest.mark.parametrize(
    ("service_info", "expected"),
    [
        ({"type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.1.0"}}, V11),
        ({"type": {"group": "org.ga4gh", "artifact": "wes", "version": "1.0.1"}}, V10),
        ({"type": {"group": "ORG.GA4GH", "artifact": "WES", "version": "v1.1"}}, V11),
        ({"type": {"group": "org.ga4gh", "artifact": "wes", "version": "2.0.0"}}, None),
        ({"type": {"group": "org.ga4gh", "artifact": "wes", "version": ""}}, None),
        ({"type": {"group": "org.ga4gh", "artifact": "tes", "version": "1.1.0"}}, None),
        ({"type": {"group": 1, "artifact": "wes", "version": "1.1.0"}}, None),
        ({"type": "wes"}, None),
        ({}, None),
        ([], None),
        (None, None),
    ],
)
def test_reported_version(service_info: Any, expected: WesVersion | None) -> None:
    """Service infos report WES versions of type `org.ga4gh` `wes` only."""
    assert reported_version(service_info) == expected
