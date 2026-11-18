# Deployment

proWES comes with a [Helm](https://helm.sh/) chart for Kubernetes and
OpenShift, in
[`deployment/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/deployment):
it deploys the app, the Celery worker, MongoDB and RabbitMQ, with a shared
volume for the storage of the runs.

!!! note "Being updated"
    The chart and its instructions are being updated
    ([#38](https://github.com/elixir-cloud-aai/prowes/issues/38),
    [#39](https://github.com/elixir-cloud-aai/prowes/issues/39)); this page
    will then describe the deployment in full. Until then, see the chart's
    [`README.md`](https://github.com/elixir-cloud-aai/prowes/blob/dev/deployment/README.md)
    and
    [`values.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/deployment/values.yaml),
    and check them against the [upgrade notes](upgrading.md).

The chart can't pass a configuration overlay yet (`FOCA_CONFIG_OVERLAY`, see
[Configuration](configuration.md#environment-variables-and-overlays)), so
deployments with the chart serve the service info of `pro_wes/config.yaml`
in the image until it can
([#38](https://github.com/elixir-cloud-aai/prowes/issues/38)).

The image runs as the non-root user `1000` and writes the runs to `/data`
(see [Image](operations.md#image)). On Kubernetes (`clusterType:
kubernetes`), the chart sets `fsGroup: 1000` and its init containers give
`/data` to user and group `1000` where they can, so that the volume is
writable for the app; on OpenShift (`clusterType: openshift`), OpenShift
assigns user and group itself. If `/data` still isn't writable, the app
refuses to start (`PermissionError` in its log).

For local development and testing, use Docker Compose instead (see [Getting
started](getting-started.md)).
