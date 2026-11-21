# Getting started

To quickly run the service for development or testing, use [Docker
Compose](https://docs.docker.com/compose/install/), as described below; to
run it from the source code instead, e.g., to debug it, see [Run from
source](development.md#run-from-source). For deployments on Kubernetes, a
[Helm](https://helm.sh/) chart is in the
[`deployment/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/deployment)
directory (see [Deployment](deployment.md)). The [FOCA deployment
guide](https://foca.readthedocs.io/en/latest/deployment/) explains how FOCA
apps, such as this one, are configured with Docker Compose and Helm.

## Quick start

--8<-- "README.md:quick-start"

Only proWES is published, on the host's loopback interface
(`127.0.0.1:8090`); MongoDB and RabbitMQ are only reachable from the
containers. To publish proWES elsewhere, override `ports` in a
`docker-compose.override.yaml`. Docker Compose keeps the data of MongoDB,
RabbitMQ and the runs in the Compose project's named volumes `mongodb-data`,
`rabbitmq-data` and `prowes-data` (e.g., `prowes_mongodb-data`, see `docker
volume ls`); no data directories need to be created. The comments in
[`docker-compose.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/docker-compose.yaml)
describe the settings.

The base config lists no WES instance, so proWES refuses runs (`503 Service
Unavailable`) until you configure one (see [WES
instances](configuration.md#wes-instances)): put the list into a config
overlay, e.g., `wes.yaml` next to `docker-compose.yaml`,

```yaml
custom:
  wes:
    backends:
      - type: ga4gh-wes
        url: https://wes.example.org
```

and mount it into the app with a `docker-compose.override.yaml`:

```yaml
services:
  prowes:
    environment:
      FOCA_CONFIG_OVERLAY: /overlay/wes.yaml
    volumes:
      - ./wes.yaml:/overlay/wes.yaml:ro
```

Docker Compose reads `docker-compose.override.yaml` automatically (unless
files are given with `-f`). The Celery worker needs no overlay: it tracks
each run at the WES instance stored with it.

Requests other than `GET /service-info` need an access token of a trusted
identity provider (see [Access control](access-control.md)): add its issuer
and your client's audience to the overlay, e.g.,

```yaml
security:
  auth:
    issuers:
      - https://login.example.org/oidc/
    audience:
      - my-client-id
```

and send the token with each request (`-H "Authorization: Bearer
<token>"`).

The image reports version `0.0.0.dev0` unless you set
`SETUPTOOLS_SCM_PRETEND_VERSION` on the host (e.g.,
`SETUPTOOLS_SCM_PRETEND_VERSION=0.20.0 docker compose up -d --build --wait`).
RabbitMQ's credentials default to `prowes:prowes`; set `RABBITMQ_DEFAULT_USER`
and `RABBITMQ_DEFAULT_PASS` on the host to change them (on the first start, or
after `docker compose down --volumes`, which also removes the database and
the runs).

To also start [Flower](https://flower.readthedocs.io/), a web UI to monitor
the Celery worker, at `http://localhost:5565`, add the profile `monitoring`
and set its basic authentication:

```bash
FLOWER_BASIC_AUTH='<user>:<password>' docker compose --profile monitoring up -d --wait
```

Docker Compose is for local development and testing; for deployments, see
[Deployment](deployment.md).

### Other useful commands

To shut down the service, run:

```bash
docker compose down
```

Add `--volumes` to also delete the database, the broker's data and the runs.

To inspect the logs, run:

```bash
docker compose logs
```

## Usage

[Endpoints](api.md) can be probed with `curl`, as above, or tried in the
Swagger UI. See [Configuration](configuration.md) to change the
configuration, e.g., the [service info](configuration.md#service-info) that
describes your deployment: the defaults are placeholders.
