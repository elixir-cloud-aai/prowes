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
| `custom.wes.backends` | none | WES instances that runs are forwarded to, see [WES instances](#wes-instances); without any, `POST /runs` is refused |
| `service_info.service` | example values | Service info (`GET /service-info`), see [Service info](#service-info) |
| `server.debug` | `False` | Flask's debug mode, which allows executing arbitrary code: only for local development |
| `server.max_content_length` | `10485760` (10 MiB) | Maximum size of request bodies, see [Request size limit](#request-size-limit) |
| `exceptions` | `pro_wes.exceptions.exceptions`, `application/json` | Problem details of errors (`title`, `status`, `detail`) and their media type (see [Errors](api.md#errors)) |
| `custom.post_runs` | | Storage path of runs (`storage_path`), characters and length of run identifiers (`id_charset`, `id_length`; the characters are taken as is), timeout of forwarding runs and reading run logs (`timeout_post`, 30 seconds for each request as a whole; required, a positive number), polling of the remote WES (`polling_wait`, `polling_attempts`), limits of workflow attachments (`max_attachments`, `max_attachment_size`) and of the size of tags (`max_tags_size`, 16 KiB), and credentials in URLs (`url_credentials`), see [Run requests](#run-requests) |
| `custom.list_runs.default_page_size`, `custom.list_runs.max_page_size` | `5`, `100` | Runs per page of `GET /runs` without `page_size`, and at most, see [Run lists](#run-lists) |
| `custom.defaults.timeout` | `2` | Timeout of other requests to the remote WES (status, cancel), in seconds, for each request as a whole; required, a positive number |
| `custom.wes.max_response_size` | `16777216` (16 MiB) | Maximum size of each response of a WES instance, in bytes; larger responses fail the request, see [WES instances](#wes-instances) |

## WES instances

Runs are forwarded to the WES instances of the backend list,
`custom.wes.backends`, a typed backend list of FOCA (see FOCA's [backend
lists](https://foca.readthedocs.io/en/latest/plugins/#backend-lists); as
`custom.tes.backends` of proTES). Each entry is either a WES instance
(`type: ga4gh-wes`), with its base URL (`url`, without the API path
`/ga4gh/wes/v1`), or a service registry, whose verified WES instances are
used in its place (`type: ga4gh-service-registry`, see [Discovery in a
service registry](#discovery-in-a-service-registry)). The base config lists
none, and proWES refuses `POST /runs` with `503 Service Unavailable`
(nothing is stored) until a deployment sets its own list, e.g.:

```yaml
custom:
  wes:
    backends:
      - type: ga4gh-wes
        url: https://wes.example.org
        # Labels and location, e.g., for routing by plugins
        labels: {region: eu-north}
        location: {latitude: 60.17, longitude: 24.94}
      # A WES instance that serves the WES API at another path
      - type: ga4gh-wes
        url: https://wes.example.com
        api_path: /api/wes/v1
```

New runs go to the first WES instance of the list: the first configured one,
or the first verified WES instance of a registry entry that comes first.
Until FOCA's gateway plugins choose and order the WES instances
([#121](https://github.com/elixir-cloud-aai/prowes/issues/121)), later
entries are used only if the ones before them yield none (e.g., a registry
that lists none, or can't be read yet). proWES stores the WES instance with
each run, and tracks and cancels the run there, also after the list
changed.

Settings of a WES instance:

| Key | Default | Description |
| --- | --- | --- |
| `url` | (required) | Base URL, without the API path and without user info |
| `api_path` | `/ga4gh/wes/v1` | Path of the WES API, relative to `url`; `/` if it is at `url` itself |
| `id` | the normalized URL | Identifier; entries with the same identifier are rejected |
| `url_schemes` | `[https]` | Allowed schemes of `url`; add `http` for local tests only |
| `labels`, `location` | none | Labels and location, for plugins ([#121](https://github.com/elixir-cloud-aai/prowes/issues/121)) |
| `auth` | `none` | Credentials for the WES instance: only `none` for now; credentials per WES instance come with authentication ([#120](https://github.com/elixir-cloud-aai/prowes/issues/120)) |

URLs are normalized (e.g., lowercase host, without default port and trailing
slashes). A URL listed twice is used once, at its first place. Unknown keys
and invalid entries (e.g., `type: ga4gh-tes`, `auth: basic`) stop the app on
startup.

WES instances at or below the public URL of proWES (`server.public_url`: its
root URL, without the API path, e.g., `https://prowes.example.org`) are
never used, so that proWES never forwards runs to itself: proWES refuses to
start if a configured one is, or if its WES API (`url` and `api_path`) is.
Backend lists with registry entries need `server.public_url` (the app
doesn't start without it), as a registry may list proWES itself. All URLs
of the host and port of the public URL below its path count as proWES: for
WES instances on the same host and port, set the path prefix of proWES in
`server.public_url` (e.g., `https://example.org/prowes`).

Requests to WES instances go through FOCA's guarded HTTP client:

- They use `https` only, unless the entry of a configured WES instance
  allows `http` (`url_schemes`), and always verify TLS certificates: with
  the public certificate authorities, or with those of the file or directory
  that the environment variable `REQUESTS_CA_BUNDLE` names instead (e.g., a
  private certificate authority; then only these are trusted, so add the
  public ones to the file if other WES instances need them).
- Configured WES instances may have private addresses (e.g., in the same
  cluster); discovered ones need public addresses, unless their registry
  entry has `allow_private_addresses`. Runs whose WES instance the backend
  list no longer yields (e.g., after it was removed, or while its registry
  yields none, after `max_stale`) are tracked and canceled over `https` at
  public addresses only; the worker decides this when it starts tracking a
  run.
- They don't follow redirects (a redirect fails the request, as if the WES
  instance were unavailable): set the URL that the WES API is served at.
- Each request ends within its timeout as a whole (resolving, connecting,
  sending and reading; connecting within at most 10 seconds):
  `custom.post_runs.timeout_post` for forwarding runs and reading run logs,
  `custom.defaults.timeout` for status requests and cancellation (each a
  positive number of seconds, at most 86400). See [Failed requests to WES
  instances](operations.md#failed-requests-to-wes-instances).
- Responses larger than `custom.wes.max_response_size` (16 MiB) fail the
  request, with an error in the logs.
- Proxy settings (`HTTPS_PROXY` and the like) and `.netrc` files of the
  environment are ignored.

Run requests are sent as `multipart/form-data`, as the WES API specifies.
proWES sends no credentials to WES instances yet (neither its own nor the
client's), so they must accept runs without them.

### Discovery in a service registry

A registry entry (`type: ga4gh-service-registry`) uses the WES instances
that a GA4GH service registry, e.g., the ELIXIR [Cloud
Registry](https://github.com/elixir-cloud-aai/cloud-registry), lists as
verified, at the API path of the WES specification:

```yaml
custom:
  wes:
    backends:
      - type: ga4gh-service-registry
        url: https://registry.example.org/ga4gh/registry/v1
        # WES versions, as prefixes (default: 1.0 and 1.1)
        versions: ["1.1"]
        # Environments of the services (default: any)
        environments: [prod]
```

proWES reads the registry's `GET /verifications` and `GET /services` with
FOCA's guarded HTTP client (without access token, `https` only, at public
addresses only, without following redirects, within the entry's `timeout`)
and uses the services of type `org.ga4gh:wes` whose URL and type are
verified, of an accepted version and environment, at hosts with public
addresses (unless `allow_private_addresses`). Each process reads the
registry for the first run, then every `refresh_interval` seconds (default
300); while the registry can't be read, it keeps the last good list for
`max_stale` seconds (default 3600), and before the first good read and after
`max_stale`, the entry yields no WES instance. Set `id` for registries at
internal URLs (they also need `registry_allow_private_addresses`). The
settings are those of FOCA's registry entries; `auth` must be `none`, as for
WES instances.

## Environment variables and overlays

Strings in the configuration may reference environment variables, as
`${VAR}` or, with a default, `${VAR:-default}`; an unset variable without
default stops the app. Write `$$` for a literal `$`.

Further configuration files can be merged over `pro_wes/config.yaml` with the
environment variable `FOCA_CONFIG_OVERLAY` (paths separated by `:`), e.g., to
set the service info of a deployment without changing the image.

## Security

The WES API requires access tokens of the trusted issuers
(`security.auth`, none by default: set them for each deployment), except for
the service info; each run belongs to the caller that created it (see
[Access control](access-control.md)). Requests are limited and sanitized as
described below, as in proTES.

### Request size limit

Request bodies, with all workflow attachments, are limited to
`server.max_content_length` bytes (default: 10485760, i.e., 10 MiB), by
FOCA: larger requests, also chunked ones, are rejected with `413 Content Too
Large` before they are parsed. A reverse proxy or ingress controller in front
of proWES must allow bodies at least as large, or it rejects them first
(e.g., ingress-nginx allows 1 MiB by default; the Helm chart sets its
annotation `nginx.ingress.kubernetes.io/proxy-body-size` to 10 MiB, see
[Body size and rate limits](deployment.md#body-size-and-rate-limits); for
other proxies, see FOCA's [Helm chart
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
- `max_tags_size`: the maximum size of the tags (`tags`) of a run, in
  bytes (default 16384, i.e., 16 KiB): of their JSON text as stored, in
  UTF-8, after credentials are removed (`url_credentials: strip`); runs
  with larger tags are rejected with `400 Bad Request`, naming `tags` in
  `errors`. Tags are listed with every run (see [Run lists](#run-lists)),
  so the limit bounds the size of run lists. Stored runs with larger tags
  (e.g., stored before the limit, or under a larger one) are listed with
  `{}` as tags, with a warning logged; their run logs (`GET
  /runs/{run_id}`) keep the tags;
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
  strings (e.g., `--url=https://token@host`), and user information with
  spaces inside strings, are not found: detection is a safety net. Tabs and
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

### Run lists

`custom.list_runs` bounds the pages of `GET /runs`, so that no request
makes a worker load, decode and serialise all runs of a caller at once:

- `max_page_size`: the largest number of runs per page (default 100, at
  least 1); requests with a larger `page_size`, or one below 1, are
  rejected with `400 Bad Request`, naming `page_size` in `errors`;
- `default_page_size`: the number of runs per page of requests without
  `page_size` (default 5); from 1 to `max_page_size`, or the app doesn't
  start.

Clients get the other runs page by page, with `next_page_token` (see [Run
lists](api.md#run-lists)). With the size of tags (`max_tags_size`, see
[Run requests](#run-requests)), the limits bound the size of each list: by
default, 100 runs with at most 16 KiB of tags each, as stored. Responses
escape characters other than ASCII (e.g., `é` as `\u00e9`), so tags may
take up to about three times their stored size in a list.

Setting only `max_page_size` below 5 needs `default_page_size` too (at most
`max_page_size`), or the app doesn't start.

### Rate limits

proWES has no rate limits of its own (neither does FOCA). In production,
limit the rate of requests per client at the reverse proxy or ingress
controller in front of it, so that single clients can't overload the app,
its database, its storage or the WES instances (see [Body size and rate
limits](deployment.md#body-size-and-rate-limits) for the values of the Helm
chart, and FOCA's [Helm chart
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
(`python -m pro_wes.app`, see [Run from
source](development.md#run-from-source)): `127.0.0.1` and `8080`, i.e., the
loopback interface only.

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

The WES fields follow WES 1.1.0: `workflow_engine_versions` maps each engine
to an object with its versions (WES 1.0.1: a string), and `type.version` and
`supported_wes_versions` say `1.1.0`:

```yaml
service_info:
  service:
    type:
      group: "org.ga4gh"
      artifact: "wes"
      version: "1.1.0"
    supported_wes_versions:
      - 1.1.0
    workflow_engine_versions:
      cwltool:
        workflow_engine_version:
          - "3.1"
```
