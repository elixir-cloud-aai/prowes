# Helm chart

The chart deploys proWES on Kubernetes and OpenShift: the app, its Celery
worker, which tracks the runs at the WES instances, a MongoDB database and a
RabbitMQ broker, and optionally Flower, the monitoring UI of the worker. It
is a thin chart over FOCA's library chart `foca-common` (the version in
`Chart.yaml`), which renders most resources from the values in
[`values.yaml`](values.yaml).

Quick start, with the Secrets of the MongoDB credentials and the broker URL
created first:

```bash
helm dependency build deployment
(cd deployment && sha256sum -c charts.sha256)
helm install prowes deployment -n prowes \
  --set hostName=wes.example.org \
  --set mongodb.auth.existingSecret=prowes-mongodb \
  --set broker.existingSecret=prowes-broker
```

proWES serves the WES API without authentication for now: limit access to
it in front of the app.

The Ingress lets ingress-nginx allow request bodies of the app's size limit
(`nginx.ingress.kubernetes.io/proxy-body-size: 10m` in
`prowes.ingress.annotations`; the chart fails to render if it is below
`config.server.max_content_length`). Rate limits are off by default: set
them with the annotations of the ingress controller
(`prowes.ingress.annotations`) or the OpenShift router
(`prowes.route.annotations`), see [Body size and rate
limits](https://prowes.readthedocs.io/en/latest/deployment/#body-size-and-rate-limits).

The [deployment guide](https://prowes.readthedocs.io/en/latest/deployment/)
(source: [`docs/deployment.md`](../docs/deployment.md)) describes the
installation, the Secrets, the configuration (including the service info of
the deployment), the storage of the runs, the ways of exposing the app, and
the upgrade from the legacy chart.
