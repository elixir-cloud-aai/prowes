# Development

## Setup

proWES is packaged with
[`pyproject.toml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pyproject.toml);
all dependencies are locked in
[`uv.lock`](https://github.com/elixir-cloud-aai/prowes/blob/dev/uv.lock). You
need [uv](https://docs.astral.sh/uv/) and [Git](https://git-scm.com/); uv also
provides the Python version that proWES targets (see `requires-python` in
`pyproject.toml`, currently Python 3.13, the version of the base image in the
[`Dockerfile`](https://github.com/elixir-cloud-aai/prowes/blob/dev/Dockerfile)).

Clone the repository and install the app with its development tools (the
`dev` extra) from the lock into a virtual environment (`.venv`):

```bash
git clone https://github.com/elixir-cloud-aai/prowes.git
cd prowes
uv sync --locked --extra dev
```

Install the [pre-commit](https://pre-commit.com/) hooks, so that the checks
run on every commit, and run them on all files once:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

The hooks
([`.pre-commit-config.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/.pre-commit-config.yaml))
format and lint the code with [ruff](https://docs.astral.sh/ruff/) (Google
style docstrings), check types with [mypy](https://mypy-lang.org/), check the
project's semgrep (security) and ast-grep (anti-patterns) rules (see [Static
analysis rules](#static-analysis-rules)), lint the GitHub Actions workflows
with actionlint, scan for hardcoded secrets with gitleaks and check that
`uv.lock` matches `pyproject.toml`. CI runs the same hooks.

Run the tests:

```bash
uv run pytest
```

The version of the package comes from Git tags (`vX.Y.Z`) via
[setuptools-scm](https://setuptools-scm.readthedocs.io/); there is no version
to edit in the code.

## Static analysis rules

Besides ruff's security rules (`S`, from bandit), pre-commit runs
project-specific rules, each with tests:

- [semgrep](https://semgrep.dev/), security rules in
  [`.semgrep/pro-wes.yml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/.semgrep/pro-wes.yml),
  shared with FOCA and the other FOCA apps (keep their patterns, messages and
  severities identical):
    - `flask-debug-enabled`: debug mode in the app config or the Helm chart
    - `jwt-decode-without-verification`: JWTs decoded without verifying the
      signature
    - `jwt-decode-unsafe-algorithms`: JWTs verified with the algorithm `none`
      or the one the token's header names
    - `yaml-unsafe-load`: YAML loaded with PyYAML's full or unsafe loader
    - `tls-verification-disabled`: certificate verification turned off
- [ast-grep](https://ast-grep.github.io/), anti-patterns in
  [`.ast-grep/rules/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/.ast-grep/rules)
  (configured in
  [`sgconfig.yml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/sgconfig.yml));
  all but `pydantic-v1-dict`, `root-logger-call` and `string-status-code`
  check the package (`pro_wes/`) only:
    - `foca-config-attribute`: untyped access to the FOCA config
      (`app.config.foca`)
    - `framework-free-tasks`: Flask, Werkzeug, Connexion or Starlette
      imported outside the app, the controllers and the exceptions
    - `generic-exception-raised`: `Exception` or `BaseException` raised
    - `hard-coded-url`: string literals that start with a URL (not
      docstrings or other strings that stand alone as a statement); backends
      and other services come from the config
    - `print-call`: output with `print()` instead of a logger
    - `pydantic-v1-dict`: Pydantic v1's `.dict()` instead of `.model_dump()`
    - `root-logger-call`: messages logged with the root logger
    - `string-status-code`: HTTP status codes returned as strings

pre-commit runs the rule tests when the rules change; run it with `uv run
pre-commit ...`, as the ast-grep hooks use the ast-grep of the dev
dependencies. To run the rule tests by hand: `uv run ast-grep test
--skip-snapshot-tests`, and `semgrep --test .` in `.semgrep/` (semgrep is not
a dev dependency: pre-commit installs its own, so install it separately for
this).

Add a rule together with its test cases: for semgrep in
`.semgrep/pro-wes.py` (Python rules) or `.semgrep/pro-wes.test.yaml` (YAML
rules), marked with `# ruleid: <rule-id>` and `# ok: <rule-id>`; for
ast-grep in `.ast-grep/rule-tests/<rule-id>-test.yml`, with `valid` and
`invalid` snippets. The tests can't check which files a rule applies to
(`files` and `ignores` of ast-grep rules, `paths` of semgrep rules): check
the scoping by hand, e.g., with a finding planted in `pro_wes/`.

Suppress a finding only if it is a false positive or can't be avoided yet,
with a suppression comment on its line: `# nosemgrep: <rule-id>`,
`# ast-grep-ignore: <rule-id>`, `# noqa: <code>` (ruff),
`# type: ignore[<code>]` (mypy) or `# gitleaks:allow`. Give the reason in a
comment on the line before, as most suppression comments can't hold it.

## Continuous integration

CI
([`.github/workflows/ci.yml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/.github/workflows/ci.yml),
workflow `CI`) runs on pull requests and on pushes to `dev`. Its jobs, and
how to run them locally:

| Job | Name | Locally |
| --- | --- | --- |
| `title` | PR title | Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/), at most 50 characters |
| `lint` | Lint | `uv run pre-commit run --all-files`, `pipx run --spec cffconvert==2.0.0 cffconvert --validate -i CITATION.cff`, `uv build` |
| `docs` | Docs | `uv run --extra docs mkdocs build --strict` (see [Documentation](#documentation)) |
| `test` | Test | `uv run coverage run -m pytest && uv run coverage report` |
| `smoke` | Smoke test | `tests/smoke/check.sh` (see [Smoke test](#smoke-test)) |
| `push` | Push image | Pushes to `dev` in this repository only |
| `passed` | CI passed | Passes if all other jobs passed or were skipped as intended |

`CI passed` is the only required check. Add every new job to its `needs`.

## Smoke test

The smoke test
([`tests/smoke/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/tests/smoke))
deploys proWES with Docker Compose, as `docker-compose.yaml` does (app,
Celery worker, MongoDB, RabbitMQ), against a fake WES (`fake_wes.py`)
instead of a real one. It creates runs, which proWES forwards to the fake
WES, waits until the Celery worker has tracked them to their final state,
and checks the run list, the run logs, the run status, the cancellation of
a run and that the fake WES got the runs as sent. It needs Docker Compose,
curl and jq. Run it from the repository root:

```bash
tests/smoke/check.sh
```

It builds the image as `prowes:smoke` and starts the services in the
Compose project `prowes-smoke` (set `COMPOSE_PROJECT_NAME` to change it),
so that the image and data of other local deployments are left alone;
proWES is published on `127.0.0.1:8090`, as by `docker-compose.yaml`, so
stop other deployments that publish that port first. On failure, it shows
the logs of the services. The deployment keeps running after the test, for
inspection, and the test can run again on it; remove it with:

```bash
COMPOSE_FILE=docker-compose.yaml:tests/smoke/compose.yaml \
  COMPOSE_PROJECT_NAME=prowes-smoke docker compose down --volumes
```

Until WES backends are configurable
([#118](https://github.com/elixir-cloud-aai/prowes/issues/118)), proWES
sends all runs to a hard-coded WES; the fake WES takes its host name in the
Compose network, with a certificate of its own certificate authority, which
proWES trusts instead of the public ones (`REQUESTS_CA_BUNDLE`), so that no
run reaches the real WES.

## Dependencies

After changing dependencies in `pyproject.toml`, update the lock with `uv
lock` and commit both files.

Dependabot
([`.github/dependabot.yml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/.github/dependabot.yml))
opens pull requests labelled `type: dependencies` once a month, to `dev`,
grouped per ecosystem: Python dependencies (`pyproject.toml` and `uv.lock`),
GitHub Actions, the images of the `Dockerfile`, the service images of Docker
Compose (MongoDB, RabbitMQ, Flower) and the pre-commit hook revisions. Some
updates are made by hand instead:

- FOCA, in `pyproject.toml` and `uv.lock` together with the FOCA base image
  (the image build fails unless the base image has the locked FOCA); run
  `uv lock` after changing `pyproject.toml`.
- connexion 3, which needs a FOCA upgrade.
- MongoDB minor and major versions (patches still come).
- uv's pin in `.readthedocs.yaml`, which must be the version that `uv.lock`
  locks (`tests/test_readthedocs.py` fails otherwise).

## Documentation

This documentation is built with [MkDocs](https://www.mkdocs.org/) and
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/), from
`docs/` and
[`mkdocs.yml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/mkdocs.yml),
by Read the Docs (configured in
[`.readthedocs.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/.readthedocs.yaml))
and in CI (job `docs`), both with the versions of `uv.lock` (the `docs`
extra); warnings, e.g., of broken links, fail the build. The [API
reference](reference.md) is generated from the docstrings with
[mkdocstrings](https://mkdocstrings.github.io/). To build it locally, or serve
it with live reloading, in the dev environment set up as above, with the
docs dependencies too (`uv sync` removes the extras that it isn't given, so
`dev` stays in the command):

```bash
uv sync --locked --extra dev --extra docs
uv run mkdocs build --strict  # into `site/`, ignored by Git
uv run mkdocs serve
```

The introduction, the quick start and the citation of the home and getting
started pages are sections of `README.md` (between `--8<-- [start:<name>]`
and `--8<-- [end:<name>]` comments), so that they are written only once.
Links in these sections must be absolute, as they must also work on GitHub
and PyPI.
