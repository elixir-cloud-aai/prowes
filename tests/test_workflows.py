"""Tests of the GitHub Actions workflows (`.github/workflows/`).

They check what actionlint can't: the names of the workflows and jobs, that
the gate (`CI passed`, the only required check) needs every other job and
allows only the intended skips, and that the image jobs push, scan and
publish the image as described in `docs/operations.md` ("Verifying images"),
with the Docker Hub secrets in those jobs only.
"""

from pathlib import Path
import re
from typing import Any

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
GATE = "passed"
# Pushes to this repository, or to a fork that sets `DOCKERHUB_ORG` (fork
# dry runs); `env.PUBLISH` and the `if:` of `push` and `publish`
PUBLISH = (
    "github.event_name == 'push' && (github.repository_owner == "
    "'elixir-cloud-aai' || vars.DOCKERHUB_ORG != '')"
)
# The jobs that may read secrets, and the secrets they read
SECRETS = {
    "push": {"DOCKERHUB_LOGIN", "DOCKERHUB_TOKEN"},
    "publish": {"DOCKERHUB_LOGIN", "DOCKERHUB_TOKEN"},
    "test": {"CODECOV_TOKEN"},
}


def _load(path: Path) -> dict[str, Any]:
    """Load a workflow file.

    Args:
        path: Path of the workflow file.

    Returns:
        The workflow.
    """
    workflow: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return workflow


def _jobs() -> dict[str, dict[str, Any]]:
    """Get the jobs of `ci.yml`.

    Returns:
        The jobs, by job ID.
    """
    jobs: dict[str, dict[str, Any]] = _load(WORKFLOWS / "ci.yml")["jobs"]
    return jobs


def _expression(value: str) -> str:
    """Normalize an expression for comparison.

    Args:
        value: Expression, with or without `${{ }}`.

    Returns:
        The expression without `${{ }}` and with single spaces.
    """
    value = " ".join(value.split())
    if value.startswith("${{") and value.endswith("}}"):
        value = value[3:-2].strip()
    return value


def test_workflow_files() -> None:
    """The only workflow is `ci.yml` (`CI`), until the release workflows."""
    names = {path.name: _load(path)["name"] for path in WORKFLOWS.iterdir()}
    assert names == {"ci.yml": "CI"}


def test_job_names_unique() -> None:
    """Job names, which name the checks, are unique."""
    names = [job["name"] for job in _jobs().values()]
    assert len(names) == len(set(names))


def test_gate_needs_all_jobs() -> None:
    """The gate needs every other job and runs whatever their results."""
    jobs = _jobs()
    gate = jobs[GATE]
    assert gate["name"] == "CI passed"
    assert gate["if"] == "always()"
    assert sorted(gate["needs"]) == sorted(set(jobs) - {GATE})


def test_gate_allowed_skips() -> None:
    """The gate allows skips of the image jobs only where none publishes."""
    workflow = _load(WORKFLOWS / "ci.yml")
    assert _expression(workflow["env"]["PUBLISH"]) == PUBLISH
    step = workflow["jobs"][GATE]["steps"][0]
    assert _expression(step["with"]["allow-skipped"]) == (
        "env.PUBLISH == 'false' && 'title push publish' || 'title'"
    )


def test_publishing_jobs_condition() -> None:
    """`push` and `publish` run exactly where `PUBLISH` is "true".

    Jobs' `if:` can't read `env`, so they repeat its expression.
    """
    jobs = _jobs()
    for job_id in ("push", "publish"):
        assert _expression(jobs[job_id]["if"]) == PUBLISH, job_id


def test_image_name() -> None:
    """The image is named after the Docker Hub organization of the run.

    Forks set the repository variable `DOCKERHUB_ORG` for dry runs, so that
    they never publish to the organization of the upstream repository.
    """
    image = _load(WORKFLOWS / "ci.yml")["env"]["IMAGE_NAME"]
    assert image == "docker.io/${{ vars.DOCKERHUB_ORG || 'elixircloud' }}/prowes"


def test_scan_follows_push() -> None:
    """The scan scans the pushed digest, or a local build where none is pushed."""
    scan = _jobs()["scan"]
    assert scan["needs"] == "push"
    assert _expression(scan["if"]) == (
        '!cancelled() && contains(fromJSON(\'["success", "skipped"]\'), '
        "needs.push.result)"
    )
    assert scan["permissions"] == {
        "contents": "read",
        "security-events": "write",
    }
    uses = [step.get("uses", "") for step in scan["steps"]]
    assert any(
        use.startswith("elixir-cloud-aai/.github/actions/scan-image@") for use in uses
    )


def test_publish_needs_all_checks() -> None:
    """The image is tagged only after all checks and the scan passed."""
    jobs = _jobs()
    publish = jobs["publish"]
    assert sorted(publish["needs"]) == sorted(set(jobs) - {GATE, "title", "publish"})
    assert publish["permissions"] == {
        "contents": "read",
        "id-token": "write",
        "attestations": "write",
    }


def test_secrets_only_where_needed() -> None:
    """Only the jobs that need secrets read them, from the environment.

    The Docker Hub secrets are read in the environment `dev-images`, which
    allows branch `dev` only.
    """
    for job_id, job in _jobs().items():
        found = set(re.findall(r"secrets\.(\w+)", yaml.safe_dump(job)))
        assert found == SECRETS.get(job_id, set()), job_id
        if "DOCKERHUB_TOKEN" in found:
            assert job["environment"] == "dev-images", job_id


def test_actions_pinned() -> None:
    """Actions are pinned to the full commit hash of a release."""
    for path in WORKFLOWS.glob("*.yml"):
        for job_id, job in _load(path)["jobs"].items():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(
                        r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]
                    ), f"{path.name}: {job_id}: {step['uses']}"


def test_version_uv_image() -> None:
    """`push` finds the uv image of the `Dockerfile`, pinned by digest.

    `push` derives the version with the build requirements, which it
    exports from `uv.lock` with that image; its `sed` pattern (an extended
    regular expression, which Python reads the same) must match the
    `Dockerfile`.
    """
    run = _steps("push")["Derive version"]["run"]
    match = re.search(r"sed -En 's/(.+)/\\1/p' Dockerfile", run)
    assert match
    pattern = match.group(1).replace("\\/", "/")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    images = [
        found.group(1)
        for line in dockerfile.splitlines()
        if (found := re.fullmatch(pattern, line))
    ]
    assert len(images) == 1
    assert re.fullmatch(r"ghcr\.io/astral-sh/uv:[^ @]+@sha256:[0-9a-f]{64}", images[0])


def _steps(job_id: str) -> dict[str, dict[str, Any]]:
    """Get the steps of a job of `ci.yml`.

    Args:
        job_id: ID of the job.

    Returns:
        The steps, by name, in order.
    """
    steps = _jobs()[job_id]["steps"]
    names = [step["name"] for step in steps]
    # Unique, so that no step hides behind another of the same name
    assert len(names) == len(set(names)), job_id
    return {step["name"]: step for step in steps}


def test_push_by_digest_only() -> None:
    """`push` pushes by digest only, untagged, and can't sign or attest."""
    assert _jobs()["push"]["permissions"] == {"contents": "read"}
    build = _steps("push")["Build and push image by digest"]["with"]
    options = set(build["outputs"].split(","))
    assert {"type=image", "push-by-digest=true", "push=true"} <= options
    assert "tags" not in build
    assert build["sbom"] is True
    assert build["provenance"] == "mode=max"


def test_scan_upload_upstream_only() -> None:
    """The scan scans the pushed digest; findings go to upstream only."""
    steps = _steps("scan")
    assert _expression(steps["Scan image"]["with"]["upload"]) == (
        "github.repository_owner == 'elixir-cloud-aai' && "
        "(github.event_name == 'push' || "
        "github.event.pull_request.head.repo.full_name == github.repository)"
    )
    # A local build only where nothing is pushed; otherwise the digest
    for name in ("Set up Docker Buildx", "Build image"):
        assert _expression(steps[name]["if"]) == "env.PUBLISH != 'true'", name
    run = steps["Determine image"]["run"]
    assert 'if [[ "${PUBLISH}" != "true" ]]; then' in run
    assert run.rstrip().endswith(
        'echo "image=${IMAGE_NAME}@${DIGEST}" >> "$GITHUB_OUTPUT"'
    )


def test_publish_signs_before_tagging() -> None:
    """`publish` attests and signs the digest before it tags it.

    Every step after the check of the branch head runs only if the commit
    is still the head, and the tags are `dev` and `sha-<commit>`, never
    `latest`.
    """
    publish = _jobs()["publish"]
    assert publish["environment"] == "dev-images"
    assert publish["concurrency"] == {
        "group": "${{ github.workflow }}-publish-${{ github.ref }}",
        "cancel-in-progress": False,
    }
    steps = _steps("publish")
    names = list(steps)
    assert names[0] == "Check that commit is still the branch head"
    for name in names[1:]:
        assert steps[name]["if"] == "steps.head.outputs.current == 'true'", name
    assert names[-3:] == ["Install cosign", "Sign image", "Tag image"]
    assert names.index("Attest build provenance") < names.index("Sign image")
    tag = steps["Tag image"]["run"]
    assert '"${IMAGE_NAME}:dev" "${IMAGE_NAME}:sha-${SHA}"' in tag
    assert "latest" not in tag
