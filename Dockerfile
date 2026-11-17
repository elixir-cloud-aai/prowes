##### TOOLS #####
## uv, to install the locked requirements (`uv.lock`); mounted into the step
## that uses it only, so that the image doesn't contain it. Pinned by digest
## (the same as in proTES)
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

##### BASE IMAGE #####
## Python 3.13, as in proTES (`requires-python` in `pyproject.toml`); FOCA
## 0.12 has no image for it, so FOCA is installed with the app. Pinned by
## digest, so that rebuilds use the same base image. The image has no
## compiler, so pymongo and PyYAML, which have no wheels for Python 3.13 at
## the versions FOCA 0.12 pins, run without their C extensions (until the
## FOCA upgrade, #107)
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

##### METADATA #####
LABEL version="3.0"
LABEL software="proWES"
LABEL software.description="Flask microservice implementing the Global Alliance for Genomics and Health (GA4GH) Workflow Execution Service (WES) API specification as a proxy for middleware injection (e.g., workflow distribution logic)."
LABEL software.website="https://github.com/elixir-europe/proWES"
LABEL software.documentation="https://github.com/elixir-europe/proWES"
LABEL software.license="https://spdx.org/licenses/Apache-2.0"
LABEL maintainer="cloud-service@elixir-europe.org"
LABEL maintainer.organisation="ELIXIR Cloud & AAI"

# Python UserID workaround for OpenShift/K8S
ENV LOGNAME=ipython
ENV USER=ipython

## Copy only what the build needs, not the whole build context; secrets are
## never part of the image: they come in only at runtime (environment
## variables, mounted secrets), never as build arguments
## (`pyproject.toml` holds the package metadata and dependencies, `uv.lock`
## their locked versions, `README.md` is the package's long description)
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY pro_wes ./pro_wes

## Install the runtime dependencies at the exact versions of `uv.lock`,
## checked against its hashes and without resolving anything else
## (`--no-deps`; the export leaves out the `dev` extra), then the app itself
## (editable, as the services run from `/app/pro_wes`). Only the runtime
## packages are locked and hash-checked: the build backends of the app and of
## sdists (setuptools, setuptools-scm) come unpinned from the index, until the
## image moves to a FOCA base image (#111). The build context has
## no Git metadata, so the package version is `0.0.0.dev0` unless it is passed
## as a build argument (e.g.,
## `--build-arg SETUPTOOLS_SCM_PRETEND_VERSION=1.2.3`); the app doesn't use
## it (service info reports the version of `pro_wes/config.yaml`)
ARG SETUPTOOLS_SCM_PRETEND_VERSION
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
  export UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
  && uv export --frozen --no-dev --no-emit-project \
    --output-file /tmp/requirements.txt \
  && uv pip install --system --require-hashes --no-deps \
    -r /tmp/requirements.txt \
  && uv pip install --system --no-deps -e . \
  && rm -f /tmp/requirements.txt

## Add permissions for storing updated API specification
## (required by FOCA)
RUN chmod -R a+rwx /app/pro_wes/api
