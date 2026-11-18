# Contributing

Your contributions are welcome and necessary. Please use the
[GitHub issue tracker](https://guides.github.com/features/issues/) to:

- Report bugs
- Propose or implement features
- Submit code changes / fixes
- Discuss code

See here for a [short tutorial for GitHub's issue tracking
system](https://guides.github.com/features/issues/).

Please adhere to the [Code of Conduct](CODE_OF_CONDUCT.md).

Please *do not* ask usage questions, installation problems (unless they appear
to be bugs) etc. via the GitHub issue tracker. We will set up a dedicated Q&A
channel soon.

## Reporting bugs

Please use the project's
[issue tracker](https://github.com/elixir-europe/proWES/issues) to report
bugs. If you have no experience in filing bug reports, see e.g.,
[these recommendations by the Mozilla Developer Network](https://developer.mozilla.org/en-US/docs/Mozilla/QA/Bug_writing_guidelines)
first. Briefly, it is important that bug reports contain enough detail,
background and, if applicable, _minimal_ reproducible sample code. Tell us
what you expect to happen, and what actually does happen.

## Implementing features and submitting fixes

Kindly use pull requests to submit changes to the code base. But please note
that this project is driven by a community that likes to act on consensus. So
in your own best interest, before just firing off a pull request after a lot of
work, please [open an issue](https://github.com/elixir-europe/proWES/issues)
to **discuss your proposed changes first**. Afterwards, please stick to the
following simple rules to make sure your pull request will indeed be merged:

1. Do not change the API. The official [GA4GH
   repo](https://github.com/ga4gh/workflow-execution-service-schemas) is the
   right place for that!
2. Fork the repo and create a [_feature
   branch_](https://datasift.github.io/gitflow/IntroducingGitFlow.html) from
   branch `dev`
3. If you've added code that should be tested, add tests.
4. Ensure that all tests pass.
5. Document your code and update all relevant documentation.
6. Stick to the code and documentation style (see below).
7. Issue the pull request against `dev`. GitHub fills in the
   organisation's [pull request
   template](https://github.com/elixir-cloud-aai/.github/blob/main/.github/pull_request_template.md):
   give enough detail for a review, close the issue with `Closes #<issue>`
   and say how you verified the change.

Don't forget to add your name and GitHub profile URL to the
[list of contributors](contributors.md) once your pull request is merged.

Important: Note that all your contributions are understood to be covered by the
[same license](LICENSE.md) that covers the entire project.

## Citation metadata

proWES is cited with the metadata in [`CITATION.cff`](CITATION.cff)
([Citation File Format](https://citation-file-format.github.io/)), which
GitHub ("Cite this repository") and Zenodo use. Its authors follow the
[citation metadata rule of the contributing
guide](https://github.com/elixir-cloud-aai/.github/blob/main/CONTRIBUTING.md#citation-metadata)
of the organisation (not counting vendored files, such as the GA4GH
specifications in `pro_wes/api/`, the lock `uv.lock`, and the reformatting
commits in `.git-blame-ignore-revs`); open a pull request to add or correct
your entry. CI validates the file (`cffconvert --validate`, job `lint`).

The file has no `version` and `date-released` yet: before each release, the
release-prep pull request sets both to the release's version (`X.Y.Z`,
without the `v` of the tag) and date. Once a release is archived on Zenodo,
a follow-up pull request adds the concept DOI (`doi` and `identifiers`), which
always resolves to the latest version, and a DOI badge to the README.

## Code & documentation style

### Python

Use the Python version that `requires-python` in `pyproject.toml` names.
Python code, docstring and comment style follows the
[Google Python Style Guide](https://github.com/google/styleguide/blob/gh-pages/pyguide.md),
with Google style docstrings and complete
[type hints](https://docs.python.org/3/library/typing.html) for all
functions and methods.

The pre-commit hooks check this: [ruff](https://docs.astral.sh/ruff/) formats
and lints the code and [mypy](https://mypy-lang.org/) checks the types (see
the "Development" section of the [README](README.md) for the setup). Make
sure that `uv run pre-commit run --all-files` and `uv run pytest` pass before
you open a pull request; CI runs the same checks.

### Static analysis rules

Besides ruff's security rules (`S`, from bandit), pre-commit runs
project-specific rules, each with tests:

- [semgrep](https://semgrep.dev/), security rules in
  [`.semgrep/pro-wes.yml`](.semgrep/pro-wes.yml), shared with FOCA and the
  other FOCA apps (keep their patterns, messages and severities identical):
  - `flask-debug-enabled`: debug mode in the app config or the Helm chart
  - `jwt-decode-without-verification`: JWTs decoded without verifying the
    signature
  - `jwt-decode-unsafe-algorithms`: JWTs verified with the algorithm `none`
    or the one the token's header names
  - `yaml-unsafe-load`: YAML loaded with PyYAML's full or unsafe loader
  - `tls-verification-disabled`: certificate verification turned off
- [ast-grep](https://ast-grep.github.io/), anti-patterns in
  [`.ast-grep/rules/`](.ast-grep/rules) (configured in
  [`sgconfig.yml`](sgconfig.yml)); all but `pydantic-v1-dict`,
  `root-logger-call` and `string-status-code` check the package
  (`pro_wes/`) only:
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

pre-commit runs the rule tests when the rules change; run it with
`uv run pre-commit ...`, as the ast-grep hooks use the ast-grep of the dev
dependencies. To run the rule tests by
hand: `uv run ast-grep test --skip-snapshot-tests`, and `semgrep --test .`
in `.semgrep/` (semgrep is not a dev dependency: pre-commit installs its
own, so install it separately for this).

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

### Continuous integration

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml), workflow `CI`)
runs on pull requests and on pushes to `dev`. Its jobs, and how to run them
locally:

| Job | Name | Locally |
| --- | --- | --- |
| `title` | PR title | Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/), at most 50 characters |
| `lint` | Lint | `uv run pre-commit run --all-files`, `pipx run --spec cffconvert==2.0.0 cffconvert --validate -i CITATION.cff`, `uv build` |
| `test` | Test | `uv run coverage run -m pytest && uv run coverage report` |
| `smoke` | Smoke test | `tests/smoke/check.sh` (see [Smoke test](#smoke-test)) |
| `push` | Push image | Pushes to `dev` in this repository only |
| `passed` | CI passed | Passes if all other jobs passed or were skipped as intended |

`CI passed` is the only required check. Add every new job to its `needs`.

### Smoke test

The smoke test ([`tests/smoke/`](tests/smoke/)) deploys proWES with Docker
Compose, as [`docker-compose.yaml`](docker-compose.yaml) does (app, Celery
worker, MongoDB, RabbitMQ), against a fake WES
([`tests/smoke/fake_wes.py`](tests/smoke/fake_wes.py)) instead of a real
one. It creates runs, which proWES forwards to the fake WES, waits until
the Celery worker has tracked them to their final state, and checks the
run list, the run logs, the run status, the cancellation of a run and that
the fake WES got the runs as sent. It needs Docker Compose, curl and jq.
Run it from the repository root:

```bash
tests/smoke/check.sh
```

It builds the image and starts the services in the Compose project
`prowes-smoke` (set `COMPOSE_PROJECT_NAME` to change it), so that the data
of other local deployments is left alone; proWES is published on
`127.0.0.1:8090`, as by `docker-compose.yaml`, so stop other deployments
that publish that port first. The deployment keeps running after the test,
for inspection; remove it with:

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

### Dependency updates

Dependabot ([`.github/dependabot.yml`](.github/dependabot.yml)) opens
pull requests labelled `type: dependencies` once a month, to `dev`, grouped
per ecosystem: Python dependencies (`pyproject.toml` and `uv.lock`), GitHub
Actions, the images of the `Dockerfile`, the service images of Docker Compose
(MongoDB, RabbitMQ, Flower) and the pre-commit hook revisions. Some updates
are made by hand instead:

- FOCA, in `pyproject.toml` and `uv.lock` together with the FOCA base image
  (the image build fails unless the base image has the locked FOCA); run
  `uv lock` after changing `pyproject.toml`.
- connexion 3, which needs a FOCA upgrade.
- MongoDB minor and major versions (patches still come).
