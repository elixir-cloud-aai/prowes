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
gets the state `SYSTEM_ERROR`. Once the run has finished, the worker stores
the outputs, the engine log and the task logs of the remote run log, each if
it is valid (invalid ones, and run logs that can't be read, are logged as
warnings); the run keeps its own identifier and state. Tasks run without a
time limit unless `custom.post_runs.timeout_job` is set (in seconds).

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
