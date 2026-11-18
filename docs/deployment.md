# Deployment

proWES comes with a [Helm](https://helm.sh/) chart for Kubernetes and
OpenShift, in
[`deployment/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/deployment).
It deploys the app, its Celery worker, which tracks the runs at the WES
instances, a MongoDB database and a RabbitMQ broker, and optionally Flower,
the monitoring UI of the worker. It is a thin chart over FOCA's library
chart `foca-common` (the version in `Chart.yaml`), like the charts of proTES
and cloud-registry: the library renders most resources from the values
described here and in
[`values.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/deployment/values.yaml)
(see the [library chart's
documentation](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.4.0/docs/helm-chart.md)).
The same chart runs on vanilla Kubernetes and on OpenShift: it detects
OpenShift by its APIs (see [Exposing the app](#exposing-the-app) and
[Security context](#security-context)).

For local development and testing, use Docker Compose instead (see [Getting
started](getting-started.md)).

!!! warning "No authentication"
    proWES serves the WES API without authentication for now
    ([#120](https://github.com/elixir-cloud-aai/prowes/issues/120)): anyone
    who can reach it can start, list and cancel runs. Limit access to it in
    front of the app, e.g., with an authenticating proxy, the ingress
    controller's access rules or a network that only trusted clients reach.

## Installation

The chart in the repository downloads the library chart with `helm
dependency build`, which also checks it against `Chart.lock`; check the
downloaded archive against the digest it was pinned with, too, as tags of
OCI registries can be pushed again:

```bash
git clone https://github.com/elixir-cloud-aai/prowes.git
cd prowes
helm dependency build deployment
(cd deployment && sha256sum -c charts.sha256)
```

Create the Secrets with the credentials (see [MongoDB](#mongodb) and
[Celery worker and broker](#celery-worker-and-broker)), e.g., with random
passwords, for a release `prowes` in the namespace `prowes`:

```bash
kubectl create namespace prowes
kubectl create secret generic prowes-mongodb -n prowes \
  --from-literal=root-username=root \
  --from-literal=root-password="$(openssl rand -hex 16)" \
  --from-literal=username=prowes \
  --from-literal=password="$(openssl rand -hex 16)"
password="$(openssl rand -hex 16)"
kubectl create secret generic prowes-broker -n prowes \
  --from-literal=username=prowes \
  --from-literal=password="${password}" \
  --from-literal=broker-url="pyamqp://prowes:${password}@prowes-broker:5672//"
```

Write the settings of the deployment into a values file, e.g.,
`my-values.yaml`:

```yaml
hostName: wes.example.org
mongodb:
  auth:
    existingSecret: prowes-mongodb
broker:
  existingSecret: prowes-broker
config:
  service_info:
    service:
      id: org.example.wes
      name: My WES gateway
      organization:
        name: My organization
        url: https://organization.example.org
      contactUrl: mailto:wes@example.org
      environment: prod
```

Then install the chart, and follow the notes it prints:

```bash
helm install prowes deployment -n prowes -f my-values.yaml
```

Upgrade with `helm upgrade prowes deployment -n prowes -f my-values.yaml`,
after `helm dependency build deployment` if the library chart's version
changed; uninstall with `helm uninstall prowes -n prowes` (see
[MongoDB](#mongodb) for the data, which is kept).

## Configuration

The image contains the app's complete base config
([`pro_wes/config.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/config.yaml),
see [Configuration](configuration.md)). The chart renders a config overlay
into the ConfigMap `<app>-config` (see [Names](#names)), which FOCA merges
into the base config on startup (via `FOCA_CONFIG_OVERLAY`), for the app
and the worker:

- `db.host` is set to the release's MongoDB Service (`<app>-mongodb`),
  unless MongoDB is external (see [External MongoDB](#external-mongodb));
- `config` overrides keys of the base config; it takes precedence over the
  generated values. Mappings are merged recursively, all other values
  (including lists) replace those of the base config. `server.host` and
  `server.port` have no effect, as they apply to the development server only
  (see [Server](#server)); the broker is set via its Secret, not `jobs` (see
  [Celery worker and broker](#celery-worker-and-broker)).

The ConfigMap is no place for secrets. Values that must stay secret go into
Secrets, passed to the app and the worker as environment variables
(`prowes.extraEnv`, Kubernetes `EnvVar`s, e.g., with
`valueFrom.secretKeyRef`), which the config can reference (`${VAR}`, see
[Configuration](configuration.md#environment-variables-and-overlays)).
Rendering fails for the variables that the chart sets itself, for
`MONGO_DBNAME`, `MONGO_URI`, `MONGO_HOST` and `MONGO_PORT`, as the chart
provisions the database `runStore` only (set `config.db` for an external
MongoDB), and for `PORT`, as the chart's port is 8080. Pods read the values
of Secrets when they start: after changing a Secret (e.g., rotating the
MongoDB or broker credentials), restart them, e.g., with `kubectl rollout
restart deployment/<app> deployment/<app>-worker -n <namespace>`.

### Service info

The base config describes an example deployment (`service_info`), which
accepts CWL v1.0 runs only: set the service info of the deployment in
`config.service_info`, at least `service.id`, `service.name`,
`service.organization`, `service.contactUrl`, `service.environment` and the
workflow types and versions that the WES instances behind proWES accept
(`service.workflow_type_versions`), as in the example of
[`values.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/deployment/values.yaml).
The pods are restarted when the config changes.

The service info reports the version of the app's package, unless
`service_info.service.version` is set. Images built without the build
argument `SETUPTOOLS_SCM_PRETEND_VERSION` report `0.0.0.dev0` (see
[Image](operations.md#image)): set `config.service_info.service.version`
for them.

### WES instances

The base config lists no WES instance, so a deployment refuses all runs
(`503 Service Unavailable`) until it sets its own in
`config.custom.wes.backends` (a list, which replaces that of the base
config): WES instances and service registries, as described in [WES
instances](configuration.md#wes-instances), e.g.:

```yaml
config:
  custom:
    wes:
      backends:
        - type: ga4gh-wes
          url: https://wes.example.org
```

The app reads the list on startup and refuses to start with invalid entries
(the pod then fails its startup, with the reason in its logs). WES
instances behind a private certificate authority need it in a file mounted
into the pods, with `REQUESTS_CA_BUNDLE` pointing to it (`prowes.extraEnv`;
it replaces the public certificate authorities, so add those to the file if
other WES instances need them; the image's system trust store isn't used);
TLS verification can't be switched off.
Set `config.server.public_url` to the root URL of proWES, without the API
path (e.g., `https://<hostName>`, plus the path prefix of a reverse proxy, if
any), so that proWES never forwards runs to itself, e.g., when a registry
lists it; with registry entries, the app doesn't start without it.

## Image

The app and the worker run the image `prowes.image` (default:
`elixircloud/prowes`) with the tag `prowes.tag`, by default the chart's
`appVersion`: `dev` for the chart in the repository, the image of the
latest commit on `dev`. Set `prowes.tag` to a released version, or include
a tag or digest in `prowes.image` (e.g., `elixircloud/prowes@sha256:...`),
for a deployment that changes only when you upgrade it. With the default
`imagePullPolicy: Always`, pods pull the tag again whenever they start.

## Names

Resource names are derived from the release name: `<release>-prowes` (or
`<release>` if it contains `prowes`), called `<app>` here, for the app's
Deployment, Service, Ingress, HTTPRoute or Route and ConfigMap
(`<app>-config`), and `<app>-mongodb`, `<app>-worker`, `<app>-broker`,
`<app>-flower` and `<app>-data` for MongoDB, the worker, the broker, Flower
and the storage of the runs. `fullnameOverride` replaces `<app>`;
`nameOverride` replaces the chart name. Set them at installation only, as
changing them later changes the pod selectors, which can't be upgraded.

## Storage of the runs

The app writes the workflow attachments of each run into a directory of its
own under `/data` (`custom.post_runs.storage_path`; the chart mounts the
storage at the path that `config` sets, if any). The chart mounts a
PersistentVolumeClaim there, in the app's pods only (the worker doesn't use
the storage): `<app>-data`, of `prowes.storage.size` (1 GiB), with
`prowes.storage.accessModes` and `prowes.storage.storageClass` (the
cluster's default class if empty, `-` for no dynamic provisioning), or the
existing claim `prowes.storage.existingClaim` (set
`prowes.storage.accessModes` to its access modes, which the chart reads
from the values only).

- **Access modes**: with `ReadWriteOnce` (the default), the volume can be
  mounted on one node at a time, so the app's pods are replaced
  (`Recreate`, a short downtime on each upgrade) instead of rolled, and
  `prowes.replicas` must be 1. For several replicas, or rolling upgrades,
  use a storage class with `ReadWriteMany` (e.g., NFS or CephFS) and set
  `prowes.storage.accessModes: [ReadWriteMany]`.
- **Ownership**: the image runs as user `1000`, and `fsGroup: 1000` with
  `fsGroupChangePolicy: OnRootMismatch` (`prowes.podSecurityContext`) makes
  the volume writable for it where Kubernetes manages the volume's ownership.
  On volumes whose ownership Kubernetes doesn't manage (e.g., some NFS
  provisioners), make the volume writable for user or group `1000` first.
  OpenShift assigns user and group itself. If `/data` isn't writable, the app
  refuses to start (`PermissionError` in its log).
- **Growth**: the app never deletes the directories of runs, so the
  volume fills up over time (then `POST /runs` fails): watch its usage and
  remove the directories of finished runs from time to time. The app
  refuses request bodies over `config.server.max_content_length` (10 MiB
  by default); the ingress controller, Gateway or router in front of it
  must allow at least as much (see [Body size and rate
  limits](#body-size-and-rate-limits)).
- **Retention**: Helm deletes the claim when the release is uninstalled,
  unless `prowes.storage.resourcePolicy` is `keep`.
- **Without persistence** (`prowes.storage.enabled: false`), the runs are
  kept in an `emptyDir` (limited to `size`; the pod is evicted when it is
  full) and lost when the pod is replaced, e.g., for tests; `prowes.replicas`
  must be 1, as each pod would have runs of its own.

## Celery worker and broker

The worker (`worker`) tracks the progress of forwarded runs (see [Run
tracking](operations.md#run-tracking)): a Deployment with the app's image,
config overlay, MongoDB credentials, `prowes.extraEnv` and security
contexts, running `worker.command`. Without it (`worker.enabled: false`),
runs are not tracked, unless a worker runs elsewhere, connected to the same
broker and MongoDB.

The broker URL comes from the Secret `broker.existingSecret` (required),
key `broker-url`, passed to the app, the worker and Flower as
`CELERY_BROKER_URL` (and the optional key `result-backend` as
`CELERY_RESULT_BACKEND`; by default `jobs.backend` of the config). With the
bundled RabbitMQ (`broker.enabled`, one pod), the Secret also holds the
user's `username` and `password`, which the URL must contain:
`pyamqp://<username>:<password>@<app>-broker:5672//` (URL-encode reserved
characters of the credentials). RabbitMQ keeps its queues in a
PersistentVolumeClaim (`broker.persistence`), and creates its user from the
Secret only when it starts with empty data: after changing the password in
the Secret, change the user's too (`rabbitmqctl change_password`). For an
external broker, set `broker.enabled: false` and its URL in the Secret.

## Flower

Flower (`flower.enabled`, off by default) shows the worker's tasks, with
their arguments, including the tokens that clients sent with their runs, so
it requires basic authentication: create a Secret with the key `basic-auth`
(`<user>:<password>`) and set `flower.existingSecret`. Flower is not exposed
outside the cluster (the legacy chart's Ingress for Flower is gone; for one of
your own, admit its controller in `flower.networkPolicy.ingressFrom`); reach it with:

```bash
kubectl port-forward svc/<app>-flower 5555:5555 -n <namespace>
```

## Exposing the app

The app is exposed for `hostName` via one of:

- an **Ingress** (`prowes.ingress`, the default), of the class
  `prowes.ingress.className` (the cluster's default if empty), with
  `prowes.ingress.annotations` for the ingress controller (e.g., the size
  limit of request bodies, which carry the workflow attachments, and rate
  limits, see [Body size and rate limits](#body-size-and-rate-limits)). With
  `prowes.ingress.https.enabled`, it requests a certificate from
  cert-manager (ClusterIssuer `prowes.ingress.https.issuer`) and serves
  HTTPS; otherwise, terminate TLS in front of the cluster;
- a Gateway API **HTTPRoute** (`prowes.httpRoute`, with
  `prowes.ingress.enabled: false`), attached to the Gateways in
  `prowes.httpRoute.parentRefs`, whose listener terminates TLS; optionally
  with a second route that redirects HTTP to HTTPS
  (`prowes.httpRoute.httpsRedirect`);
- an **OpenShift Route** (`prowes.route`), if neither is enabled and the
  cluster serves `route.openshift.io/v1`, with TLS at the router, which
  redirects HTTP to HTTPS.

Without any of them, the chart doesn't expose the app outside the cluster;
reach it with `kubectl port-forward svc/<app> 8080:8080 -n <namespace>`.

### Body size and rate limits

The app refuses request bodies over `config.server.max_content_length`
(10 MiB by default; see [Request size
limit](configuration.md#request-size-limit)) with its problem details. A
proxy in front of it with a lower limit rejects larger bodies first, with
a `413` page of its own:

- **Ingress**: ingress-nginx allows 1 MiB by default, so the chart sets its
  annotation `nginx.ingress.kubernetes.io/proxy-body-size: 10m` in
  `prowes.ingress.annotations` (other ingress controllers ignore it; set
  theirs in the same value). The chart fails to render if the annotation
  is below the app's limit: when raising `config.server.max_content_length`,
  raise the annotation too (sizes with at most 9 digits and no leading
  zeros, e.g., `20m`; `"0"` disables the limit of ingress-nginx; `null`
  removes the annotation, e.g., for another controller).
- **OpenShift Route**: the router has no size limit of request bodies.
- **HTTPRoute**: the Gateway API has no standard for it; set the limit of
  the Gateway implementation (e.g., via `prowes.httpRoute.filters`).

proWES has no rate limits of its own, and the chart sets none: in
production, limit the rate of requests per client at the proxy, with
limits that suit the clients of the deployment (e.g., workflow engines that
poll the state of many runs). Behind a load balancer, the proxy must see
the clients' addresses, e.g., via the PROXY protocol (`use-proxy-protocol`
of ingress-nginx) or `use-forwarded-headers`; with the latter, also set
`proxy-real-ip-cidr` to the load balancer's addresses, or clients can send
any `X-Forwarded-For` address and get a rate limit of their own for each.

- **Ingress** (ingress-nginx; excess requests get `503`):

    ```yaml
    prowes:
      ingress:
        annotations:
          nginx.ingress.kubernetes.io/limit-rps: "10"
          nginx.ingress.kubernetes.io/limit-burst-multiplier: "5"
          nginx.ingress.kubernetes.io/limit-connections: "20"
    ```

- **OpenShift Route** (HAProxy router):

    ```yaml
    prowes:
      route:
        annotations:
          haproxy.router.openshift.io/rate-limit-connections: "true"
          haproxy.router.openshift.io/rate-limit-connections.rate-http: "100"
    ```

For other controllers and Gateways, see FOCA's [Helm chart
guide](https://foca.readthedocs.io/en/latest/helm-chart/#exposing-the-app).

## Network policies

NetworkPolicies allow connections to MongoDB and the broker only from the
app's and the worker's pods of the release (`mongodb.networkPolicy`,
`broker.networkPolicy`, on by default), and to Flower only from
`flower.networkPolicy.ingressFrom` (none by default; port forwarding is not
affected). The app's own policy (`prowes.networkPolicy`) is off by default:
before enabling it, set `prowes.networkPolicy.ingressFrom` to the pods of the
ingress controller or Gateway (see the [library chart's
documentation](https://github.com/elixir-cloud-aai/foca/blob/chart-v0.4.0/docs/helm-chart.md#networkpolicy-peers)
for common ones); rendering fails without peers. Clusters without
NetworkPolicy support ignore them all.

## Security context

All pods run as non-root users, without privilege escalation and
capabilities, with a read-only root file system and the `RuntimeDefault`
seccomp profile; temporary files go to `emptyDir` volumes at `/tmp`
(`*.tmpSizeLimit`). The app and the worker run as the image's user `1000`;
MongoDB and RabbitMQ as user `999`, Flower as `1000`. On OpenShift (detected
by `security.openshift.io/v1`), the chart leaves out `runAsUser`,
`runAsGroup` and `fsGroup`, which OpenShift assigns from the namespace's
range. Clusters that refuse the `RuntimeDefault` seccomp profile need
`seccompProfile` removed from the `podSecurityContext` values.

## Server

The image serves the app with Gunicorn; the chart passes `prowes.gunicorn`
as `GUNICORN_CMD_ARGS`: `workers` and `threads` (one worker with 4 threads
by default), `timeout`, `gracefulTimeout` (the pod's termination grace
period is `preStopSleepSeconds` plus `gracefulTimeout` plus 10 seconds),
`keepalive` (keep it longer than the ingress controller's upstream
keep-alive timeout) and `extraArgs`. Rendering fails for `--bind`, as the
chart's ports are 8080, for `--preload` and for `--graceful-timeout`. The
app's probes request the Swagger UI (`prowes.probePath`); replace them
(`prowes.startupProbe`, `readinessProbe`, `livenessProbe`) if the config
disables it.

## MongoDB

The chart deploys MongoDB 7.0 (`mongodb.image`, pinned to a patch version;
8.0 refuses to start on Linux kernels 6.19 to 7.0.13,
[SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912)) with
authentication: the Secret `mongodb.auth.existingSecret` holds the root
user's `root-username` and `root-password` and the app user's `username` and
`password`. MongoDB creates both users when it starts with an empty data
directory, the app user with access to the database `runStore` only. For an
existing database without these users, create them first.

MongoDB keeps its data in a PersistentVolumeClaim (`<app>-mongodb`,
`mongodb.persistence`), which Helm keeps when the release is uninstalled
(`resourcePolicy: keep`), so that reinstalling the release with the same
name and namespace finds the data again. Delete the claim by hand to remove
the data.

### External MongoDB

With `mongodb.enabled: false`, set the host (and port) of an external
MongoDB in `config.db` and the app user's credentials in the Secret
`mongodb.auth.existingSecret` (keys `username` and `password`, with access
to the database `runStore`); rendering fails without `config.db.host`.

## Upgrades

### From the legacy chart

The chart was rebuilt on `foca-common` (before: version 2 of the chart, with
`clusterType`, `applicationDomain` and resource names from `appName`
values). Rendering fails on the values of the legacy chart, naming their
replacements:

| Legacy value | Replacement |
| --- | --- |
| `applicationDomain` | `hostName`, the full host name |
| `clusterType` | none: OpenShift is detected |
| `storageAccessMode` | `prowes.storage.accessModes`, `mongodb.persistence.accessModes`, `broker.persistence.accessModes` |
| `*.appName` | names from the release name; `fullnameOverride` |
| `celeryWorker` | `worker` |
| `rabbitmq` | `broker`, with `broker.existingSecret` |
| `mongodb.secret` | a Secret of your own, in `mongodb.auth.existingSecret` |
| `mongodb.volumeSize` | `mongodb.persistence.size` |
| `flower.basicAuth` | a Secret of your own, in `flower.existingSecret` |
| `prowes.initResources` | none: `fsGroup` makes `/data` writable |
| Flower's Ingress | none: `kubectl port-forward`, or an Ingress of your own with `flower.networkPolicy.ingressFrom` |

Releases of the legacy chart can't be upgraded in place: the names of their
volumes changed, so an upgrade would delete them with the data (the chart
refuses such upgrades if the release's resources carry Helm's annotation
`meta.helm.sh/release-name`, which Helm 3.2 and later add; check releases
installed with older Helm versions yourself). Uninstall the legacy release and install the chart
anew; to keep the runs, export the database first with `mongodump` (e.g.,
from the legacy MongoDB pod) and import it into the new release's MongoDB
with `mongorestore`, as the app user or root, into the database `runStore`.
The workflow attachments of earlier runs (`/data`) are not needed once the
runs have been forwarded. Uninstalling the legacy release also removes its
broker and the queued tracking tasks: migrate when no runs are active, as
the new worker doesn't resume tracking runs whose tasks were lost (their
states stay as they were).

## Testing the chart

The chart's tests render it with `helm template` (`tests/test_chart.py`,
skipped without Helm) and need the library chart:

```bash
helm dependency build deployment
pytest tests/test_chart.py
helm lint deployment --strict -f deployment/ci/test-values.yaml
```

The CI job `chart-install` installs the chart on a kind cluster with the
image of the commit and checks it (`tests/chart/install.sh`). To run it
locally, create a kind cluster with its kubeconfig in a file of its own,
outside the repository:

```bash
export KUBECONFIG="$(mktemp -d)/kubeconfig"
kind create cluster --name chart-testing
tests/chart/install.sh
kind delete cluster --name chart-testing
```
