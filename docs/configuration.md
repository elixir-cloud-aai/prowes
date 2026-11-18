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
| `server.max_content_length` | `10485760` (10 MiB) | Maximum size of request bodies, see [Request size limit](#request-size-limit) |
| `exceptions` | `pro_wes.exceptions.exceptions`, `application/json` | Problem details of errors (`title`, `status`, `detail`) and their media type (see [Errors](api.md#errors)) |
| `custom.post_runs` | | Storage path of runs (`storage_path`), characters and length of run identifiers (`id_charset`, `id_length`; the characters are taken as is), timeout of forwarding runs and reading run logs (`timeout_post`, 30 seconds), polling of the remote WES (`polling_wait`, `polling_attempts`), limits of workflow attachments (`max_attachments`, `max_attachment_size`) and credentials in URLs (`url_credentials`), see [Run requests](#run-requests) |
| `custom.defaults.timeout` | `2` | Timeout of other requests to the remote WES (status, cancel), in seconds |

## Environment variables and overlays

Strings in the configuration may reference environment variables, as
`${VAR}` or, with a default, `${VAR:-default}`; an unset variable without
default stops the app. Write `$$` for a literal `$`.

Further configuration files can be merged over `pro_wes/config.yaml` with the
environment variable `FOCA_CONFIG_OVERLAY` (paths separated by `:`), e.g., to
set the service info of a deployment without changing the image.

## Security

The WES API is currently served without authentication; requests are
limited and sanitized as described below, as in proTES.

### Request size limit

Request bodies, with all workflow attachments, are limited to
`server.max_content_length` bytes (default: 10485760, i.e., 10 MiB), by
FOCA: larger requests, also chunked ones, are rejected with `413 Content Too
Large` before they are parsed. A reverse proxy or ingress controller in front
of proWES must allow bodies at least as large, or it rejects them first
(e.g., ingress-nginx allows 1 MiB by default; set its annotation
`nginx.ingress.kubernetes.io/proxy-body-size`, see FOCA's [Helm chart
guide](https://foca.readthedocs.io/en/latest/helm-chart/#exposing-the-app)).
Each form field other than attachments (e.g., `workflow_params`) is also
limited to 500 kB by Flask (`MAX_FORM_MEMORY_SIZE`, not configurable in
proWES), with the same `413`.

### Run requests

`custom.post_runs` limits the run requests of `POST /runs`:

- `max_attachments`: the maximum number of workflow attachments
  (`workflow_attachment`) of a run (default 100; `0` allows none); runs
  with more are rejected with `413 Content Too Large`;
- `max_attachment_size`: the maximum size of each attachment, in bytes
  (default 1048576, i.e., 1 MiB); runs with a larger one are rejected with
  `413 Content Too Large`. All attachments together are limited by the
  [request size limit](#request-size-limit);
- `url_credentials`: what happens to runs with credentials in URLs
  (`https://user:password@host/...`), in `workflow_url` and in the strings
  (keys and values) of `workflow_params`, `workflow_engine_parameters` and
  `tags`, at any depth: they are rejected with `400 Bad Request` (`reject`,
  the default), or the credentials are removed (`strip`). Credentials are
  (1) a user name or password in the authority of a string that is a URL
  (e.g., `https://token@host/...`), except user names of the schemes `ssh`,
  `git`, `git+ssh`, `ssh+git` and `docker` (e.g., `ssh://git@github.com/...`,
  `docker://ubuntu@sha256:...`), which are no secrets, and (2) a user name
  with password after a scheme and slashes or backslashes anywhere in a
  string, also in forms that lenient URL parsers accept (e.g.,
  `https:/user:password@host`) and in URLs inside strings (e.g.,
  `--url=https://user:password@host`); user names without password inside
  strings (e.g., `--url=https://token@host`) are not found. Tabs and
  newlines in URLs are ignored, as URL parsers remove them; keys that
  collide once stripped are rejected. Credentials found are not
  stored, forwarded or returned in run logs, and neither error responses
  nor logs echo them. Strings such as `user@example.org`,
  `mailto:user@example.org` and `git@github.com:org/repo` are kept. Pass
  credentials for inputs in other ways, e.g., signed URLs.

Attachments are written to the working directory of the run, in the storage
of the runs (`storage_path`), each under a safe file name: without
directories (separators become `_`, e.g., `dir/a.txt` becomes `dir_a.txt`;
`../` and leading dots are removed), only ASCII characters, and at most 255
bytes long. Runs with two attachments of the same safe file name, an
attachment with a longer one, or one without file name are rejected with
`400 Bad Request`. Files are created anew, never overwriting existing ones.

The values of `tags` and `workflow_engine_parameters` must be strings, as
in the WES specification, and no object in the JSON fields may have a key
twice, as decoding would keep only its last value (`400 Bad Request`
otherwise).

### Rate limits

proWES has no rate limits of its own (neither does FOCA). In production,
limit the rate of requests per client at the reverse proxy or ingress
controller in front of it, so that single clients can't overload the app,
its database, its storage or the WES instances (see FOCA's [Helm chart
guide](https://foca.readthedocs.io/en/latest/helm-chart/#exposing-the-app)
for the annotations of common ingress controllers).

### Responses

Responses are validated against the WES specification
(`api.specs[].connexion.validate_responses`), so that no unchecked data,
e.g., of the remote WES, reaches clients: an invalid response becomes a
`500 Internal server error`. Run logs (`GET /runs/{run_id}`) are built from
the stored run, without fields that have no value.

## Server

The app is served by Gunicorn with FOCA's settings for containers
([`pro_wes/gunicorn.py`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/gunicorn.py)):
port 8080, or the environment variable `PORT`; workers and threads from
`GUNICORN_PROCESSES` and `GUNICORN_THREADS`, default 1 and 4; other settings
via `GUNICORN_CMD_ARGS`. The Celery worker is started with `celery -A
pro_wes.celery_worker worker` (see [Operations](operations.md)). The `host`
and `port` of the section `server` apply to Flask's development server only
(`python -m pro_wes.app`).

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
