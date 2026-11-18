# Contributing

The general rules for contributing to proWES (getting started, issues,
branches and pull requests, commit messages and pull request titles with
their types, pull requests prepared with AI agents) are in the [contributing
guide of ELIXIR
On-Cloud](https://github.com/elixir-cloud-aai/.github/blob/main/CONTRIBUTING.md);
please mind the [code of
conduct](https://github.com/elixir-cloud-aai/.github/blob/main/CODE_OF_CONDUCT.md)
for all interactions with the community. This page covers what is specific
to this repository, an app built with [FOCA](https://foca.readthedocs.io/).

Changes of the API itself belong in the [GA4GH WES
specification](https://github.com/ga4gh/workflow-execution-service-schemas),
not here.

## Commit messages and pull request titles

CI checks the title of a pull request (job `PR title`) and rechecks it when
you edit it.

Use `feat`, `fix`, `perf`, `refactor` and `style` only for changes of what is
deployed: the app (`pro_wes/`) and its Helm chart (`deployment/`).

## Citation metadata

proWES is cited with the metadata in
[`CITATION.cff`](https://github.com/elixir-cloud-aai/prowes/blob/dev/CITATION.cff)
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

## Python guidelines

The app targets one Python version, that of the base image in the
`Dockerfile` (3.13): it is the minimum of `requires-python` in
`pyproject.toml`, the target of ruff and mypy, and the version of CI and Read
the Docs. Code, docstring and comment style follow the [Google Python Style
Guide](https://google.github.io/styleguide/pyguide.html). Most of the
following is checked by pre-commit and CI (see
[Development](../development.md) for how to run the checks locally); the
rest is checked in reviews.

- **Packaging:** the package, its dependencies and the configuration of the
  tools are in `pyproject.toml`; runtime dependencies in
  `project.dependencies`, those for development and the documentation in the
  extras `dev` and `docs`. `uv.lock` locks them all, at exact versions and
  with hashes, and CI's checks run on the locked versions. After changing
  dependencies, run `uv lock` and commit `uv.lock` with the change (see
  [Dependencies](../development.md#dependencies)). Don't add `setup.py` or
  requirements files.
- **Formatting and linting:** [ruff](https://docs.astral.sh/ruff/) formats
  the code (`ruff format`, compatible with Black, 88 characters per line) and
  lints it (`ruff check`) with the rule set shared by FOCA and FOCA apps
  (`pyproject.toml`, where each ignored rule says why). Prefer explicit
  checks to suppressions; justify the rare exceptions in a comment (see
  [Static analysis rules](../development.md#static-analysis-rules)).
- **Docstrings:** Google-style docstrings at all levels: packages, modules,
  classes, methods and functions, also private ones and tests, with a
  one-line summary and, where applicable, the sections `Args`, `Returns` (or
  `Yields`), `Raises` and, for classes, `Attributes`. The [API
  reference](../reference.md) is generated from them.
- **Type hints:** complete type hints for all functions and methods (also of
  the tests), and generic types with their parameters (e.g.,
  `dict[str, Any]`, not `dict`); [mypy](https://mypy.readthedocs.io/) checks
  them (`disallow_untyped_defs`, `disallow_any_generics`).
- **Tests:** [pytest](https://docs.pytest.org/), with coverage measured by
  [coverage](https://coverage.readthedocs.io/): `pro_wes` is covered
  completely, and `coverage report` fails below 100% (`fail_under` of
  `pyproject.toml`). Add tests that fail without your change.

## Documentation

Update the documentation with every change that affects it: the pages in
`docs/` (built with MkDocs, see
[Documentation](../development.md#documentation)) and the docstrings.
Describe user-visible changes in the pull request, and changes that affect
existing deployments in [Upgrading](../upgrading.md).
