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
7. Issue the pull request.

Don't forget to add your name and GitHub profile URL to the
[list of contributors](contributors.md).

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

### Continuous integration

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml), workflow `CI`)
runs on pull requests and on pushes to `dev`. Its jobs, and how to run them
locally:

| Job | Name | Locally |
| --- | --- | --- |
| `title` | PR title | Pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/), at most 50 characters |
| `lint` | Lint | `uv run pre-commit run --all-files`, `pipx run --spec cffconvert==2.0.0 cffconvert --validate -i CITATION.cff`, `uv build` |
| `test` | Test | `uv run coverage run -m pytest && uv run coverage report` |
| `smoke` | Smoke test | `docker compose up -d --build`, then `curl localhost:8090/ga4gh/wes/v1/service-info` |
| `push` | Push image | Pushes to `dev` in this repository only |
| `passed` | CI passed | Passes if all other jobs passed or were skipped as intended |

`CI passed` is the only required check. Add every new job to its `needs`.

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
