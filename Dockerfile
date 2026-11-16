##### BASE IMAGE #####
FROM elixircloud/foca:20230818-py3.10

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
WORKDIR /app
COPY setup.py pyproject.toml requirements.txt README.md LICENSE ./
COPY pro_wes ./pro_wes
RUN pip install -e .

## Add permissions for storing updated API specification
## (required by FOCA)
RUN chmod -R a+rwx /app/pro_wes/api
