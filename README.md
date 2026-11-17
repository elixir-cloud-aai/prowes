# proWES

[![license][badge-license]][badge-url-license]
[![chat][badge-chat]][badge-url-chat]
[![ci][badge-ci]][badge-url-ci]

## Synopsis

proWES is a robust and scalable [Global Alliance for Genomics and Health
(GA4GH)][res-ga4gh] [Workflow Execution Service (WES) API][res-ga4gh-wes] gateway
that may play a pivotal role in augmenting the capabilities of your GA4GH Cloud
ecosystem by offering flexible middleware injection for effective workflow
deployment across a range of federated workflow management system deployments.

## Description

proWES gateway may serve as a crucial component in federated compute networks
based on the GA4GH Cloud ecosystem. Its primary purpose is to provide
centralized features to a federated network of independently operated GA4GH WES
instances. As such, it can serve, for example, as a compatibility layer, a load
balancer workload distribution layer, a public entry point to an enclave of
independent compute nodes, or a means of collecting telemetry.

When WES requests are received, proWES applies one or more configurable
middlewares before forwarding the requests to appropriate WES instances in
the network. A plugin system makes it easy to write and inject middlewares
tailored to specific requirements, such as for access control, request/response
processing or validation, or the selection of suitable endpoints considering
data use restrictions and client preferences.

### Built-in middleware plugins

TBA

### Implementation notes

proWES is a [Flask][res-flask] microservice that supports
[OAuth2][res-oauth2]-based authorization out of the box (bearer authentication)
and stores information about incoming and outgoing tasks in a NoSQL database
([MongoDB][res-mongodb]). Based on our [FOCA][res-foca] microservice archetype,
it is highly configurable in a declarative (YAML-based!) manner. Forwarded tasks
are tracked asynchronously via a [RabbitMQ][res-rabbitmq] broker and
[Celery][res-celery] workers that can be easily scaled up. Both a
[Helm][res-helm] chart and a [Docker Compose][res-docker-compose] configuration
are provided for easy deployment in native cloud-based production and
development environments, respectively.

![proWES-overview][image-prowes-overview]

## Installation

For production-grade [Kubernetes][res-kubernetes]-based deployment, see
[separate instructions][docs-deploy]. For testing/development purposes, you can
use the instructions described below.

### Requirements

Ensure you have the following software installed:

* [Docker][res-docker]
* [docker-compose][res-docker-compose]
* [Git][res-git]

### Prerequisites

Create data directory and required subdirectories

```bash
export PROWES_DATA_DIR=/path/to/data/directory
mkdir -p $PROWES_DATA_DIR/{db,specs}
```

> **Note:** If the `PROWES_DATA_DIR` environment variable is not set, proWES
> will require the following default directories to be available:
>
> * `../data/pro_wes/db`
> * `../data/pro_wes/specs`

Clone repository

```bash
git clone https://github.com/elixir-europe/proWES.git
```

Traverse to app directory

```bash
cd proWES
```

### Configure (optional)

The following user-configurable files are available:

* [app configuration](pro_wes/config.yaml)
* [deployment configuration](docker-compose.yaml)

The app configuration is a [FOCA][res-foca] configuration (FOCA 0.22; see
[FOCA's configuration reference][res-foca-config]); the comments in the file
describe each section. The settings that deployments usually change:

| Setting | Default | Description |
| --- | --- | --- |
| `db.host`, `db.port` | `mongodb`, `27017` | MongoDB; overridden by the environment variables `MONGO_HOST` and `MONGO_PORT`, with credentials from `MONGO_USERNAME` and `MONGO_PASSWORD`, the database name from `MONGO_DBNAME` (default: `runStore`), or the whole connection string from `MONGO_URI` |
| `jobs.host`, `jobs.port` | `${RABBIT_HOST:-rabbitmq}`, `${RABBIT_PORT:-5672}` | RabbitMQ broker of the Celery worker; the environment variable `CELERY_BROKER_URL` takes precedence (e.g., for brokers with credentials) |
| `server.debug` | `False` | Flask's debug mode, which allows executing arbitrary code: only for local development |
| `exceptions` | `pro_wes.exceptions.exceptions`, `application/json` | Problem details of errors (`title`, `status`, `detail`) and their media type |
| `custom.post_runs` | | Storage path of runs (`storage_path`), characters and length of run identifiers (`id_charset`, `id_length`; the characters are taken as is), timeout of forwarding runs (`timeout_post`, 30 seconds), polling of the remote WES (`polling_wait`, `polling_attempts`) |
| `custom.defaults.timeout` | `2` | Timeout of other requests to the remote WES (status, run log, cancel), in seconds |
| `custom.service_info` | | Service info, stored in the database on the first start |

Strings in the configuration may reference environment variables, as
`${VAR}` or, with a default, `${VAR:-default}`; an unset variable without
default stops the app. Write `$$` for a literal `$`. Further configuration
files can be merged over `pro_wes/config.yaml` with the environment variable
`FOCA_CONFIG_OVERLAY` (paths separated by `:`).

The app is served by Gunicorn with FOCA's settings for containers
([`pro_wes/gunicorn.py`](pro_wes/gunicorn.py): port 8080, or the environment
variable `PORT`; workers and threads from `GUNICORN_PROCESSES` and
`GUNICORN_THREADS`, default 1 and 4; other settings via `GUNICORN_CMD_ARGS`),
and the Celery worker is started with `celery -A pro_wes.celery_worker worker`.

### Deploy

Build/pull and run services

```bash
docker-compose up -d --build
```

Visit Swagger UI

```bash
firefox http://localhost:8090/ga4gh/wes/v1/ui
```

> **Note:** Host and port may differ if you have changed the configuration or
> use an HTTP server to reroute calls to a different host.

### Secrets

Secrets (e.g., passwords, tokens, private keys) are never part of the image:
pass them only at runtime, as environment variables or mounted secrets (e.g.,
Kubernetes Secrets), never as build arguments, which are recorded in the image
history. The image build receives only the files it needs (an allowlist in
[`.dockerignore`](.dockerignore)). As a safety net, common secret files are
left out even within `pro_wes/`: `.env` files (except `.env.example`), key,
certificate and keystore files (e.g., `*.pem`, `*.key`, `*.p12`, `*.jks`), SSH
keys, `.netrc`, `.pgpass`, `.aws/` and kubeconfig files. Don't keep other
secrets in the working tree either.

## Upgrading

### To 0.20.0 (FOCA 0.22)

proWES 0.20.0 upgrades FOCA from 0.12 to 0.22. Operators need to check the
following:

* **MongoDB 4.4 or later**: current PyMongo releases refuse to connect to
  older servers; Docker Compose now runs MongoDB 7.0 (before: 3.2). The data
  files of MongoDB 3.2 can't be opened by MongoDB 7.0: either start with an
  empty database (`$PROWES_DATA_DIR/db`; the runs stored so far are lost), or
  upgrade the database one major version at a time (3.2 → 3.4 → 3.6 → 4.0 →
  4.2 → 4.4 → 5.0 → 6.0 → 7.0, with `setFeatureCompatibilityVersion` after
  each step), or export the runs with `mongodump` before and import them with
  `mongorestore` after the upgrade.
* **Configuration**: configuration files of earlier versions fail
  validation, naming the keys that FOCA removed. Changed sections:
  `server` (no `environment` and `use_reloader`; `debug` is `False`),
  `security.auth` (no `validation_methods` and `validation_checks`),
  `exceptions` (`mapping` instead of `exceptions`, `required_members` and
  `status_member`), and `custom.post_runs.id_charset`, whose characters are
  now taken as is (before: a Python expression such as
  `string.ascii_uppercase + string.digits`). Start from
  [`pro_wes/config.yaml`](pro_wes/config.yaml).
* **Errors**: error responses are problem details (`title`, `status` and
  `detail`, as `application/json`) instead of `message` and `code`. Clients
  that read `message` or `code` need to read `detail` and `status` instead.
* **Commands**: the app is served with
  `gunicorn --config python:pro_wes.gunicorn pro_wes.wsgi:app` and the worker
  started with `celery -A pro_wes.celery_worker worker`, both from `/app`
  (before: `gunicorn -c gunicorn.py wsgi:app` and `celery -A celery_worker
  worker` from `/app/pro_wes`), as in [`docker-compose.yaml`](docker-compose.yaml)
  and the Helm chart (2.0.2 or later; deployments that set their own
  `command` or `args` need the same change). The app is created once per Gunicorn worker (no longer
  also by the Gunicorn master), and the Celery worker replaces its MongoDB
  client after forking (no more `MongoClient opened before fork` warnings).
  Gunicorn now runs one worker with 4 threads by default
  (`GUNICORN_THREADS`; before: 1).
* **Broker**: `RABBIT_HOST` and `RABBIT_PORT` are read by the app
  configuration (`jobs`); `CELERY_BROKER_URL` takes precedence.
* **Logs**: the Celery worker logs in the format of the app (section `log`)
  instead of Celery's own.
* **Service info**: the service info in the database is kept; a new database
  gets that of `custom.service_info`, with timestamps as RFC 3339 strings and
  URLs normalized (e.g., `https://example.org` becomes
  `https://example.org/`). Runs with states that proWES doesn't know are left
  out of `system_state_counts`. Startup no longer fetches the GA4GH service
  info schema from GitHub.
* **Timeouts**: Gunicorn's threaded workers don't stop requests that hang,
  so requests to the remote WES are limited: forwarding runs by
  `custom.post_runs.timeout_post` (now 30 seconds; before: none), canceling
  runs and reading run logs by `custom.defaults.timeout`.
* **Run logs**: the Celery worker stores the outputs, the engine log and the
  task logs of the remote run log, after validation; the run keeps its own
  identifier and state (before: the task failed before storing any).

## Development

proWES is packaged with [`pyproject.toml`](pyproject.toml); all dependencies
are locked in [`uv.lock`](uv.lock). You need [uv][res-uv] and
[Git][res-git]; uv also provides the Python version that proWES targets (see
`requires-python` in `pyproject.toml`, currently Python 3.13, the version of
the base image in the [`Dockerfile`](Dockerfile)).

Clone the repository and install the app with its development tools (the
`dev` extra) from the lock into a virtual environment (`.venv`):

```bash
git clone https://github.com/elixir-cloud-aai/prowes.git
cd prowes
uv sync --locked --extra dev
```

Install the [pre-commit][res-pre-commit] hooks, so that the checks run on
every commit, and run them on all files once:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

The hooks ([`.pre-commit-config.yaml`](.pre-commit-config.yaml)) format and
lint the code with [ruff][res-ruff] (Google style docstrings), check types with
[mypy][res-mypy], lint the GitHub Actions workflows with actionlint, scan
for hardcoded secrets with gitleaks and check that `uv.lock` matches
`pyproject.toml`. CI runs the same hooks.

Run the tests:

```bash
uv run pytest
```

After changing dependencies in `pyproject.toml`, update the lock with
`uv lock` and commit both files. The version of the package comes from Git
tags (`vX.Y.Z`) via [setuptools-scm][res-setuptools-scm]; there is no version
to edit in the code.

## Contributing

This project is a community effort and lives off your contributions, be it in
the form of bug reports, feature requests, discussions, ideas, fixes, or other
code changes. Please read [these guidelines][docs-contributing] if you want to
contribute. And please mind the [code of conduct][docs-coc] for all
interactions with the community.

## Versioning

The project adopts the [semantic versioning][res-sem-ver] scheme for versioning.
Currently the service is in beta stage, so the API may change and even break
without further notice. However, once we deem the service stable and "feature
complete", the major, minor and patch version will shadow the supported WES
version, with the build version representing proWES-internal updates.

## License

This project is covered by the [Apache License 2.0][badge-url-license] also
[shipped with this repository][docs-license].

## Contact

proWES is part of [ELIXIR Cloud & AAI][res-elixir-cloud-aai], a multinational
effort at establishing and implementing FAIR data sharing and promoting
reproducible data analyses and responsible data handling in the life sciences.

If you have suggestions for or find issue with this app, please use the
[issue tracker][contact-issue-tracker]. If you would like to reach out to us
for anything else, you can join our [Slack board][badge-url-chat], start a
thread in our [Q&A forum][contact-qa], or send us an [email][contact-email].

[![GA4GH logo](images/logo-ga4gh.png)](https://www.ga4gh.org/)
[![ELIXIR logo](images/logo-elixir.png)](https://www.elixir-europe.org/)
[![ELIXIR Cloud & AAI logo](images/logo-elixir-cloud.png)](https://elixir-europe.github.io/cloud/)

[badge-chat]: <https://img.shields.io/static/v1?label=chat&message=Slack&color=ff6994>
[badge-ci]: <https://github.com/elixir-cloud-aai/proWES/actions/workflows/ci.yml/badge.svg>
[badge-license]: <https://img.shields.io/badge/license-Apache%202.0-blue.svg>
[badge-url-chat]: <https://join.slack.com/t/elixir-cloud/shared_invite/zt-1uvebyx1e-NcC0Hof2guT9df~haBdQaw>
[badge-url-ci]: <https://github.com/elixir-cloud-aai/proWES/actions/workflows/ci.yml>
[badge-url-license]: <http://www.apache.org/licenses/LICENSE-2.0>
[contact-email]: <mailto:cloud-service@elixir-europe.org>
[contact-issue-tracker]: <https://github.com/elixir-cloud-aai/landing-page/issues>
[contact-qa]: <https://github.com/elixir-cloud-aai/elixir-cloud-aai/discussions>
[docs-coc]: <https://github.com/elixir-cloud-aai/elixir-cloud-aai/blob/dev/CODE_OF_CONDUCT.md>
[docs-contributing]: <https://elixir-cloud-aai.github.io/guides/guide-contributor/>
[docs-deploy]: deployment/README.md
[docs-license]: LICENSE
[image-prowes-overview]: <images/overview.svg>
[res-celery]: <http://www.celeryproject.org/>
[res-docker]: <https://www.docker.com/>
[res-docker-compose]: <https://docs.docker.com/compose/>
[res-elixir-cloud-aai]: <https://elixir-cloud.dcc.sib.swiss/>
[res-flask]: <http://flask.pocoo.org/>
[res-foca]: <https://github.com/elixir-cloud-aai/foca>
[res-foca-config]: <https://foca.readthedocs.io/en/latest/configuration/>
[res-ga4gh]: <https://www.ga4gh.org/>
[res-ga4gh-wes]: <https://github.com/ga4gh/workflow-execution-service-schemas>
[res-git]: <https://git-scm.com/>
[res-helm]: <https://helm.sh/>
[res-kubernetes]: <https://kubernetes.io/>
[res-mongodb]: <https://www.mongodb.com/>
[res-mypy]: <https://mypy-lang.org/>
[res-oauth2]: <https://oauth.net/2/>
[res-pre-commit]: <https://pre-commit.com/>
[res-rabbitmq]: <https://www.rabbitmq.com/>
[res-ruff]: <https://docs.astral.sh/ruff/>
[res-sem-ver]: <https://semver.org/>
[res-setuptools-scm]: <https://setuptools-scm.readthedocs.io/>
[res-uv]: <https://docs.astral.sh/uv/>
