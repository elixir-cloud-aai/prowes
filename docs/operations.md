# Operations

## Components

A deployment of proWES consists of:

- **the app**, served by Gunicorn (`gunicorn --config python:pro_wes.gunicorn
  pro_wes.wsgi:app`, see [Server](configuration.md#server)), which serves the
  [API](api.md) and forwards runs to WES instances;
- **the Celery worker** (`celery -A pro_wes.celery_worker worker`), from the
  same image, which tracks forwarded runs (see [Run
  tracking](#run-tracking));
- **MongoDB** (4.4 or later; Docker Compose runs 7.0), which stores the runs;
- **RabbitMQ**, the broker between the app and the worker;
- **a volume for the storage of the runs** (`/data`), shared by the app and
  the worker, which holds the workflow attachments of each run.

[Flower](https://flower.readthedocs.io/) can be added to monitor the worker
(the profile `monitoring` of the Docker Compose setup, see [Getting
started](getting-started.md#quick-start)).

## Run tracking

For each forwarded run, the app starts a Celery task that polls the state of
the run at the WES instance every `custom.post_runs.polling_wait` seconds
(default: 3). If the WES instance doesn't answer with a valid state
`custom.post_runs.polling_attempts` times in a row (default: 100), the run
gets the state `SYSTEM_ERROR`. Once the run has a final state (see [Run
states](api.md#run-states)), the worker stores the outputs, the engine log
and the task logs of the remote run log, each if it is valid (invalid ones,
and run logs that can't be read, are logged as warnings); the run keeps its
own identifier and state. Tasks run without a time limit unless
`custom.post_runs.timeout_job` is set (in seconds); runs whose tracking
takes longer get the state `SYSTEM_ERROR`, too, unless they have finished
already (e.g., the limit is hit while the final run log is read). Runs
that proWES sets to `SYSTEM_ERROR` this way may still run at the WES
instance and can still be canceled (see [Run states](api.md#run-states)).

## Failed requests to WES instances

Requests to WES instances that fail answer `500 Internal Server Error`
("Could not reach remote WES service"; `POST /runs` also sets the run to
`SYSTEM_ERROR`), and the worker counts them as failed polls. Besides WES
instances that are down, causes are the limits of the guarded HTTP client
(see [WES instances](configuration.md#wes-instances)): redirects, responses
larger than `custom.wes.max_response_size` (logged as errors), timeouts,
`http` URLs and private addresses where they aren't allowed, and
certificates that `REQUESTS_CA_BUNDLE` or the public certificate
authorities don't verify. The worker logs failed polls as warnings, with
the error: `EngineOutcomeUnknown` if the WES instance may have got the
request, `EngineRequestNotSent` if it certainly didn't, each caused by the
error of the guarded client that names the cause. After
`EngineOutcomeUnknown` on `POST /runs` (e.g., a timeout, a redirect or an
answer that is no JSON), the run is `SYSTEM_ERROR` in proWES, but the WES
instance may still have created and run it.

## Database indexes

The app creates the indexes of the collection `runs` at start if they are
missing (`db.dbs.runStore.collections.runs.indexes` in
[`pro_wes/config.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/config.yaml)):

| Index | Keys | Purpose |
| --- | --- | --- |
| `run_log.run_id_1` | `run_log.run_id` (unique, not sparse) | Run identifiers are unique, also across instances of the app that share the database but not the storage: `POST /runs` tries another identifier if one is taken |
| `user_id_1__id_-1` | `user_id`, `_id` descending | `GET /runs` with authentication finds the runs of the caller, newest first, without reading the runs of other users |

Existing indexes are kept, also ones that are no longer configured (see
[Upgrading](upgrading.md#database-indexes)). The app doesn't start if a
configured index can't be created: e.g., if stored runs share an identifier
(as the index is not sparse, runs without `run_log.run_id` count as runs of
the same identifier), or the database user may not create indexes. On large
collections, the index builds make the first start take longer.

With `db.create_indexes_on_startup: false` (FOCA; default: `true`), the app
creates no indexes: create them by hand before the first start, e.g., in
`mongosh`, connected to the database of proWES (default: `runStore`):

```javascript
db.runs.createIndex({ "run_log.run_id": 1 }, { unique: true })
db.runs.createIndex({ user_id: 1, _id: -1 })
```

Without the unique index, run identifiers are unique only by the working
directory of each run in the storage.

## Logs

The app and the worker log to standard error, in the format of the section
`log` of the configuration (`[<time>: <level>] <message> [<logger>]`). With
Docker Compose, `docker compose logs` shows them.

## Image

The
[`Dockerfile`](https://github.com/elixir-cloud-aai/prowes/blob/dev/Dockerfile)
builds the image on FOCA's image (`elixircloud/foca`, with the FOCA release
that `uv.lock` locks), pinned by digest:

- **Locked installs**: the runtime dependencies and the build requirements
  (dependency group `build-system` of `pyproject.toml`) are installed at the
  versions of `uv.lock`, checked against their hashes, as wheels only; the app
  is built as a wheel in a separate stage, offline, and installed without its
  build tools, pip or uv.
- **User**: the image runs as the non-root user `1000` (group `0`); the app
  files are read-only for it. The only writable directory is `/data`, the
  storage of the runs (`custom.post_runs.storage_path`), owned by the user and
  writable for group `0`, for platforms that run images with an arbitrary user
  ID in group `0` (e.g., OpenShift). Volumes mounted at `/data` must be
  writable for the user (see [Deployment](deployment.md)); the app refuses to
  start if it can't write to the storage of the runs.
- **Command**: `gunicorn --config python:pro_wes.gunicorn pro_wes.wsgi:app`;
  the Celery worker runs `celery -A pro_wes.celery_worker worker` with the
  same image.
- **Health check**: probes the Swagger UI
  (`http://localhost:8080/ga4gh/wes/v1/ui/`); set `HEALTHCHECK_URL` if the
  port, the base path or the Swagger UI setting change. Containers of the
  Celery worker need their own health check (see
  [`docker-compose.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/docker-compose.yaml)).
- **Version**: the build argument `SETUPTOOLS_SCM_PRETEND_VERSION` sets the
  version of the package, which the service info reports unless
  `service_info.service.version` is set; without it, `0.0.0.dev0`.
- **Labels**: OCI annotations (`org.opencontainers.image.*`).

## Secrets

Secrets (e.g., passwords, tokens, private keys) are never part of the image:
pass them only at runtime, as environment variables or mounted secrets (e.g.,
Kubernetes Secrets), never as build arguments, which are recorded in the image
history. The image build receives only the files it needs (an allowlist in
[`.dockerignore`](https://github.com/elixir-cloud-aai/prowes/blob/dev/.dockerignore)).
As a safety net, common secret files are left out even within `pro_wes/`:
`.env` files (except `.env.example`), key, certificate and keystore files
(e.g., `*.pem`, `*.key`, `*.p12`, `*.jks`), SSH keys, `.netrc`, `.pgpass`,
`.aws/` and kubeconfig files. Don't keep other secrets in the working tree
either.
