# Configuration

proWES is configured with a [FOCA](https://foca.readthedocs.io/)
configuration (FOCA 0.22; see [FOCA's configuration
reference](https://foca.readthedocs.io/en/latest/configuration/)):
[`pro_wes/config.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/config.yaml),
which is part of the image. The comments in the file describe each section;
the section `custom` holds the settings of proWES itself
([`pro_wes/config_models.py`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/config_models.py),
see the [API reference](reference.md#configuration)). The settings of the
Docker Compose setup are in
[`docker-compose.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/docker-compose.yaml)
(see [Getting started](getting-started.md)).

## Settings

The settings that deployments usually change:

| Setting | Default | Description |
| --- | --- | --- |
| `db.host`, `db.port` | `mongodb`, `27017` | MongoDB; overridden by the environment variables `MONGO_HOST` and `MONGO_PORT`, with credentials from `MONGO_USERNAME` and `MONGO_PASSWORD`, the database name from `MONGO_DBNAME` (default: `runStore`), or the whole connection string from `MONGO_URI` |
| `jobs.host`, `jobs.port` | `${RABBIT_HOST:-rabbitmq}`, `${RABBIT_PORT:-5672}` | RabbitMQ broker of the Celery worker; the environment variable `CELERY_BROKER_URL` takes precedence (e.g., for brokers with credentials) |
| `service_info.service` | example values | Service info (`GET /service-info`), see [Service info](#service-info) |
| `server.debug` | `False` | Flask's debug mode, which allows executing arbitrary code: only for local development |
| `exceptions` | `pro_wes.exceptions.exceptions`, `application/json` | Problem details of errors (`title`, `status`, `detail`) and their media type (see [Errors](api.md#errors)) |
| `custom.post_runs` | | Storage path of runs (`storage_path`), characters and length of run identifiers (`id_charset`, `id_length`; the characters are taken as is), timeout of forwarding runs and reading run logs (`timeout_post`, 30 seconds), polling of the remote WES (`polling_wait`, `polling_attempts`) |
| `custom.defaults.timeout` | `2` | Timeout of other requests to the remote WES (status, cancel), in seconds |

## Environment variables and overlays

Strings in the configuration may reference environment variables, as
`${VAR}` or, with a default, `${VAR:-default}`; an unset variable without
default stops the app. Write `$$` for a literal `$`.

Further configuration files can be merged over `pro_wes/config.yaml` with the
environment variable `FOCA_CONFIG_OVERLAY` (paths separated by `:`), e.g., to
set the service info of a deployment without changing the image.

## Server

The app is served by Gunicorn with FOCA's settings for containers
([`pro_wes/gunicorn.py`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/gunicorn.py)):
port 8080, or the environment variable `PORT`; workers and threads from
`GUNICORN_PROCESSES` and `GUNICORN_THREADS`, default 1 and 4; other settings
via `GUNICORN_CMD_ARGS`. The Celery worker is started with `celery -A
pro_wes.celery_worker worker` (see [Operations](operations.md)). The `host`
and `port` of the section `server` apply to Flask's development server only
(`python -m pro_wes.app`, see [Run from
source](development.md#run-from-source)): `127.0.0.1` and `8080`, i.e., the
loopback interface only, as the WES API is served without authentication.

## Service info

The service info (`GET /service-info`) is served by FOCA (see [FOCA's
service info
reference](https://foca.readthedocs.io/en/latest/configuration/service-info/))
from `service_info.service`: the fields of the GA4GH service info (`id`,
`name`, `type`, `organization`, `contactUrl`, ...) and the WES fields
(`workflow_type_versions`, `supported_wes_versions`,
`supported_filesystem_protocols`, `workflow_engine_versions`,
`default_workflow_engine_parameters`, `auth_instructions_url`, `tags`),
checked on startup. `version` defaults to the version of the installed
package (`service_info.package`); `system_state_counts` is computed for each
request and can't be set. Set `id`, `name`, `organization` and the URLs to
describe your deployment: the defaults are placeholders.
