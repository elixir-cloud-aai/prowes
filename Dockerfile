##### TOOLS #####
## uv, to export the locked requirements (`uv.lock`) for the image build, and
## to install them; mounted into the steps that use it only, so that the
## image doesn't contain it. Pinned by digest;
## Dependabot updates the tag and digest together
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

##### BASE IMAGE #####
## FOCA's image (Python 3.13, as in proTES; `requires-python` in
## `pyproject.toml`), with the FOCA release that `uv.lock` locks
## (`scripts/image_requirements.py` checks it). Pinned by digest, so that
## rebuilds (e.g., of a release) use the same base image; Dependabot updates
## the tag and digest together (`.github/dependabot.yml`). It runs as the
## non-root user 1000, with `/app` as working directory
FROM elixircloud/foca:0.22.0-py3.13@sha256:bf02db1195d60940dd3b29e53ce4757854155e258f35199e28d778cbf6128f9a AS base

##### BUILD #####
## Build the app's wheel, in a stage of its own, so that the image doesn't
## contain the build requirements. They (`build-system.requires` of
## `pyproject.toml`) are locked in `uv.lock`, as the dependency group
## `build-system`, and installed from there, as the runtime dependencies are
## below: at the exact versions of the lock, checked against its hashes, as
## wheels only, into a virtual environment of their own (created by uv for
## the Python of the base image, without pip). The wheel is then built with
## them by uv, without an isolated build environment (which would be filled
## with downloads that aren't hash-checked), offline and without network, so
## that it downloads nothing
FROM base AS build
USER root
COPY pyproject.toml uv.lock /tmp/src/
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
  export UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  && uv export --frozen --only-group build-system --no-emit-project \
    --project /tmp/src --output-file /tmp/build-requirements.txt \
  && uv venv --python /usr/local/bin/python /tmp/build-env \
  && uv pip install --python /tmp/build-env --require-hashes \
    --only-binary :all: --no-deps -r /tmp/build-requirements.txt
## (`README.md` is the package's long description; the license text is
## installed with the package metadata, e.g.,
## `site-packages/pro_wes-<version>.dist-info/licenses/LICENSE`)
## The build context has no Git metadata, so the version is passed as a build
## argument (e.g., `--build-arg SETUPTOOLS_SCM_PRETEND_VERSION=1.2.3`);
## without it, the app reports version `0.0.0.dev0`, unless the configuration
## sets `service_info.service.version`
COPY README.md LICENSE /tmp/src/
COPY pro_wes /tmp/src/pro_wes
ARG SETUPTOOLS_SCM_PRETEND_VERSION
RUN --network=none --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
  export UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  && uv build --wheel --offline --no-build-isolation \
    --python /tmp/build-env/bin/python --out-dir /tmp/dist /tmp/src

##### IMAGE #####
FROM base

##### METADATA #####
## OCI annotations; images built from the FOCA image inherit its own, so all
## static ones are set here. For the images that CI pushes,
## `docker/metadata-action` adds the version (derived from Git), revision and
## creation date and overrides title, description, URLs and licenses with the
## repository's metadata; local builds carry the labels below only
LABEL org.opencontainers.image.title="proWES"
LABEL org.opencontainers.image.description="Proxy for GA4GH Workflow Execution Service (WES) instances by ELIXIR On-Cloud"
LABEL org.opencontainers.image.url="https://github.com/elixir-cloud-aai/prowes"
LABEL org.opencontainers.image.source="https://github.com/elixir-cloud-aai/prowes"
LABEL org.opencontainers.image.documentation="https://github.com/elixir-cloud-aai/prowes#readme"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.authors="ELIXIR On-Cloud <cloud-service@elixir-europe.org>"
LABEL org.opencontainers.image.vendor="ELIXIR On-Cloud"

## Install dependencies first, so that code changes do not reinstall them.
## Only the locked ones are installed: the runtime dependencies
## (`project.dependencies` of `pyproject.toml`; the export leaves out the
## extras, and `--no-dev` a `dev` dependency group) and all their own
## dependencies, at the exact versions of `uv.lock`, checked against its
## hashes, as wheels only, and without resolving anything else (`--no-deps`).
## `--frozen` takes the lock as is, unchecked against `pyproject.toml` (CI's
## `uv lock --check` checks it). The build fails unless the base image has
## the locked FOCA release (`scripts/image_requirements.py`). A direct
## reference to a FOCA commit (temporary) has no hash and is left out, as the
## image has no Git: the build then fails unless the base image has FOCA of
## that commit. uv installs them into the Python of the base image
## (`--system`). Packages that the base image already has at the locked
## version are kept as they are (trusted through its digest), not checked
## against the hashes again
USER root
COPY pyproject.toml uv.lock scripts/image_requirements.py /tmp/src/
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
  export UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  && uv export --frozen --no-dev --no-emit-project \
    --project /tmp/src --output-file /tmp/src/locked.txt \
  && python /tmp/src/image_requirements.py /tmp/src/locked.txt > /tmp/src/requirements.txt \
  && uv pip install --system --require-hashes --only-binary :all: --no-deps \
    -r /tmp/src/requirements.txt \
  && rm -f /tmp/uv-*.lock

## Install the app from the wheel of the build stage (non-editable, owned by
## root and read-only for the app; the base config is installed with the
## package, and deployments override it via `FOCA_CONFIG_OVERLAY`), without
## its dependencies, installed above, and without an index and without
## network, so that nothing is downloaded; then uv checks that the installed
## packages (with those of the base image) meet each other's requirements, as
## nothing was resolved. FOCA 0.22 keeps the modified API specifications in
## memory, so the app needs no writable package directory
RUN --network=none --mount=from=build,source=/tmp/dist,target=/tmp/dist \
  --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
  export UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  && uv pip install --system --offline --no-index --no-deps /tmp/dist/*.whl \
  && uv pip check --system \
  && rm -rf /tmp/src /tmp/uv-*.lock

## Storage of the runs (`custom.post_runs.storage_path`, `/data`), the only
## directory the app and the Celery worker write to: owned by the user of the
## image and, for platforms that run images with an arbitrary user ID in
## group 0 (e.g., OpenShift), writable by group 0. Mount a volume here to
## keep the runs; a new named volume (Docker) takes over this directory's
## ownership, bind mounts and other volumes must be writable for the user
RUN install -d -o 1000 -g 0 -m 2775 /data
USER 1000

## Probe the Swagger UI, which needs neither the database nor the message
## broker; set `HEALTHCHECK_URL` if Gunicorn's `--bind` changes the port, or
## the config changes the API base path or the Swagger UI setting. Containers
## of the Celery worker (same image, other command) need a health check of
## their own (see `docker-compose.yaml`)
ENV HEALTHCHECK_URL=http://localhost:8080/ga4gh/wes/v1/ui/
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(os.environ['HEALTHCHECK_URL'], timeout=4)"]

## Serve with Gunicorn (proWES's settings, `pro_wes/gunicorn.py`, based on
## FOCA's settings for containers; override them via `GUNICORN_CMD_ARGS`);
## each worker creates the app (`pro_wes/wsgi.py`, no preloading). The Celery
## worker runs `celery -A pro_wes.celery_worker worker`
CMD ["gunicorn", "--config", "python:pro_wes.gunicorn", "pro_wes.wsgi:app"]
