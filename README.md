<!-- markdownlint-disable MD033 MD041 -- logo first; <picture> for dark mode -->
<a href="https://elixir-on.cloud/">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo_elixir_on_cloud_night.png">
    <img src="docs/images/logo_elixir_on_cloud.png" alt="ELIXIR On-Cloud">
  </picture>
</a>
<!-- markdownlint-enable MD033 MD041 -->

# proWES

[![License][badge-license]][badge-url-license]
[![Build_status][badge-build-status]][badge-url-build-status]
[![Docs][badge-docs]][badge-url-docs]
[![Coverage][badge-coverage]][badge-url-coverage]

<!-- --8<-- [start:synopsis] -->
A gateway for the [GA4GH Workflow Execution Service (WES)
API](https://github.com/ga4gh/workflow-execution-service-schemas) that
federates your WES instances, brought to you by [ELIXIR
On-Cloud](https://elixir-on.cloud/). Clients send their workflow runs to
proWES as to any WES instance; proWES forwards each run to a WES instance and
tracks its state. It is built with
[FOCA](https://github.com/elixir-cloud-aai/foca). Run it with Docker Compose
for development and testing; a Helm chart for Kubernetes is in
[`deployment/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/deployment).
<!-- --8<-- [end:synopsis] -->

**Read the [documentation][docs]** for the architecture, the configuration,
the API, the deployment, operations, upgrades and development.

## Quick start

<!-- --8<-- [start:quick-start] -->
With [Git](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git) and
[Docker](https://docs.docker.com/get-docker/) with the [Compose
plugin](https://docs.docker.com/compose/install/) (version 2.23.1 or later)
installed, clone the repository and start proWES with its MongoDB database,
RabbitMQ broker and Celery worker, waiting until their health checks pass:

```bash
git clone https://github.com/elixir-cloud-aai/prowes.git
cd prowes
docker compose up -d --build --wait
```

The WES API is then served at `http://localhost:8090/ga4gh/wes/v1/`, with a
Swagger UI at
[http://localhost:8090/ga4gh/wes/v1/ui/](http://localhost:8090/ga4gh/wes/v1/ui/):

```bash
curl -X GET "http://localhost:8090/ga4gh/wes/v1/service-info" -H "accept: application/json"
```
<!-- --8<-- [end:quick-start] -->

## Citation

<!-- --8<-- [start:citation] -->
If you use proWES, please cite it with the metadata in
[`CITATION.cff`](https://github.com/elixir-cloud-aai/prowes/blob/dev/CITATION.cff):
on GitHub, "Cite this repository" in the sidebar of the repository formats it
as APA or BibTeX. A DOI follows with the first release archived on
[Zenodo](https://zenodo.org/).
<!-- --8<-- [end:citation] -->

## Contributing

Contributions are welcome! See [Contributing][docs-contributing] in the
documentation and the organisation's [contributing guide][contributing], and
mind our [code of conduct][code-of-conduct].

[badge-build-status]: <https://github.com/elixir-cloud-aai/prowes/actions/workflows/ci.yml/badge.svg?branch=dev>
[badge-coverage]: <https://codecov.io/gh/elixir-cloud-aai/prowes/branch/dev/graph/badge.svg?branch=dev>
[badge-docs]: <https://img.shields.io/readthedocs/prowes>
[badge-license]: <https://img.shields.io/badge/license-Apache%202.0-blue.svg>
[badge-url-build-status]: <https://github.com/elixir-cloud-aai/prowes/actions/workflows/ci.yml?query=branch%3Adev>
[badge-url-coverage]: <https://codecov.io/gh/elixir-cloud-aai/prowes?branch=dev>
[badge-url-docs]: <https://prowes.readthedocs.io/en/latest/>
[badge-url-license]: <https://www.apache.org/licenses/LICENSE-2.0>
[code-of-conduct]: <https://github.com/elixir-cloud-aai/.github/blob/main/CODE_OF_CONDUCT.md>
[contributing]: <https://github.com/elixir-cloud-aai/.github/blob/main/CONTRIBUTING.md>
[docs]: <https://prowes.readthedocs.io/en/latest/>
[docs-contributing]: <https://prowes.readthedocs.io/en/latest/contributing/>
