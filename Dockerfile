##### BASE IMAGE #####
## Python 3.13, as in proTES (`requires-python` in `pyproject.toml`); FOCA
## 0.12 has no image for it, so FOCA is installed with the app. Pinned by
## digest, so that rebuilds use the same base image
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
## (`pyproject.toml` holds the package metadata and dependencies, `README.md`
## is the package's long description). The build context has no Git metadata,
## so the version is passed as a build argument (e.g.,
## `--build-arg SETUPTOOLS_SCM_PRETEND_VERSION=1.2.3`); without it, the app
## reports version `0.0.0.dev0`
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY pro_wes ./pro_wes
ARG SETUPTOOLS_SCM_PRETEND_VERSION
RUN pip install -e .

## Add permissions for storing updated API specification
## (required by FOCA)
RUN chmod -R a+rwx /app/pro_wes/api
