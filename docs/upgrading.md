# Upgrading

Changes to existing deployments and data, per version. Upgrade one version
at a time and read all sections between your version and the target.

## To version 0.20.0 (FOCA 0.22)

proWES 0.20.0 upgrades FOCA from 0.12 to 0.22. Operators need to check the
following.

### MongoDB 4.4 or later

Current PyMongo releases refuse to connect to older servers; Docker Compose
now runs MongoDB 7.0 (before: 3.2). The data files of MongoDB 3.2 can't be
opened by MongoDB 7.0: either start with an empty database
(`$PROWES_DATA_DIR/db`; the runs stored so far are lost), or upgrade the
database one major version at a time (3.2 → 3.4 → 3.6 → 4.0 → 4.2 → 4.4 →
5.0 → 6.0 → 7.0, with `setFeatureCompatibilityVersion` after each step), or
export the runs with `mongodump` before and import them with `mongorestore`
after the upgrade.

### Configuration

Configuration files of earlier versions fail validation, naming the keys
that FOCA removed. Changed sections: `server` (no `environment` and
`use_reloader`; `debug` is `False`), `security.auth` (no
`validation_methods` and `validation_checks`), `exceptions` (`mapping`
instead of `exceptions`, `required_members` and `status_member`), and
`custom.post_runs.id_charset`, whose characters are now taken as is (before:
a Python expression such as `string.ascii_uppercase + string.digits`). Start
from
[`pro_wes/config.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/pro_wes/config.yaml).

### WES instances

Breaking: proWES no longer sends runs to a built-in WES instance
(`https://wes-na.cloud.e-infra.cz`): set the WES instances of the
deployment in `custom.wes.backends` (see [WES
instances](configuration.md#wes-instances); Helm chart:
`config.custom.wes.backends`, see [WES
instances](deployment.md#wes-instances)). Without any, `POST /runs` is
refused with `503 Service Unavailable`. To keep the previous behaviour, list
the former WES instance:

```yaml
custom:
  wes:
    backends:
      - type: ga4gh-wes
        url: https://wes-na.cloud.e-infra.cz
```

Runs stored before the upgrade are tracked and canceled at the WES instance
stored with them, as before.

### Run identifiers

`POST /runs` returns the run's own identifier, by which `GET
/runs/{run_id}` finds it (before: the identifier at the remote WES, which
proWES didn't find).

### Limits of run requests

Run requests are limited and sanitized (see
[Security](configuration.md#security)): request bodies to 10 MiB
(`server.max_content_length`; before: unlimited), workflow attachments to 100
per run of 1 MiB each (`custom.post_runs.max_attachments`,
`max_attachment_size`), all with `413 Content Too Large`. Runs with
credentials in URLs (`https://user:password@host/...`) are rejected with
`400 Bad Request` (`custom.post_runs.url_credentials: reject`; set `strip` to
remove the credentials instead), as are `tags` and
`workflow_engine_parameters` with values other than strings. Raise the limits
for clients that send larger runs, also at the reverse proxy in front of
proWES.

Workflow attachments are written with their contents, under safe file names
(before: runs with attachments failed with `500`); their contents are no
longer stored in the database. A run whose attachments can't be written
(e.g., a full volume) is stored with the state `SYSTEM_ERROR` and gets `500`. Upgrade
the app and the Celery worker together: workers of earlier versions can't
read the attachments of runs that this version stores.

### Run logs

`GET /runs/{run_id}` returns the run request with `workflow_params`, `tags`
and `workflow_engine_parameters` as JSON objects (before: JSON-encoded
strings), as the WES specification defines them, and leaves out fields
without value (before: `null`). Responses are now validated against the
specification (`validate_responses: True`); runs stored by earlier versions
with `tags` or `workflow_engine_parameters` whose values aren't strings get
`500` responses. Credentials in the URLs of runs stored by earlier versions
are removed from the run logs returned (not from the database).

### Errors

Error responses are problem details (`title`, `status` and `detail`, as
`application/json`) instead of `message` and `code`. Clients that read
`message` or `code` need to read `detail` and `status` instead.

### Commands

The app is served with `gunicorn --config python:pro_wes.gunicorn
pro_wes.wsgi:app` and the worker started with `celery -A
pro_wes.celery_worker worker`, both from `/app` (before: `gunicorn -c
gunicorn.py wsgi:app` and `celery -A celery_worker worker` from
`/app/pro_wes`), as in
[`docker-compose.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/docker-compose.yaml)
and the Helm chart (deployments that set their own `command` or `args` need
the same change). The app is created once per Gunicorn worker
(no longer also by the Gunicorn master), and the Celery worker replaces its
MongoDB client after forking (no more `MongoClient opened before fork`
warnings). Gunicorn now runs one worker with 4 threads by default
(`GUNICORN_THREADS`; before: 1).

### Image

The image is built on FOCA's image and runs as the non-root user `1000`
(group `0`; before: root); the app is installed from a wheel into
`site-packages` (before: editable, from `/app/pro_wes`, whose `api` directory
was world-writable). Only `/data` is writable: volumes mounted there must be
writable for the user (the Helm chart sets `fsGroup: 1000` on Kubernetes;
OpenShift assigns user and group itself; see [Storage of the
runs](deployment.md#storage-of-the-runs)). Runs stored before by root may not
be writable for the user.

**Replace releases of the legacy chart before their pods pull the new
image** (e.g., with `elixircloud/prowes:latest` and `imagePullPolicy:
Always`, on their next restart; see [Helm chart](#helm-chart-breaking)), or
make the volume writable for user `1000` first. The app now refuses to start if
it can't write to `custom.post_runs.storage_path` (before: only `POST /runs`
failed). Run directories that root created before stay root's; to make them
writable too, run `chown -R 1000:1000 /data` as root on the volume (e.g.,
from a debug pod). The image now has a health check (the Swagger UI) and a
default command (Gunicorn, see above).

### Docker Compose

[`docker-compose.yaml`](https://github.com/elixir-cloud-aai/prowes/blob/dev/docker-compose.yaml)
builds the image `prowes:local` (before: `prowes:latest`) and keeps its data
in named volumes instead of `PROWES_DATA_DIR` (default `../data/pro_wes`),
whose data isn't migrated. It runs MongoDB 7.0, RabbitMQ 4.3 with the
credentials `prowes:prowes` (configurable, see [Getting
started](getting-started.md#quick-start); before: `guest`) and Flower 2.0
only with the profile `monitoring` and basic authentication. proWES (port
8090) and Flower (port 5565) are published on the host's loopback interface
only; RabbitMQ (before: 5682) and MongoDB (before: 27027) are no longer
published.

### Broker

`RABBIT_HOST` and `RABBIT_PORT` are read by the app configuration (`jobs`);
`CELERY_BROKER_URL` takes precedence.

### Logs

The Celery worker logs in the format of the app (section `log`) instead of
Celery's own.

### Service info (breaking)

The service info is served by FOCA from the configuration, no longer from
the database:

- Move `custom.service_info` to `service_info.service` (the app refuses to
  start with `custom.service_info`). The WES fields
  (`workflow_type_versions`, ...) stay in it and are checked on startup;
  `contactUrl` must be a URI (e.g., `mailto:support@example.org`, not
  `support@example.org`).
- `POST /service-info` is gone (`405 Method Not Allowed`): change the
  service info in the configuration and restart the app. Service info
  posted to or changed in the database before is ignored; the collection
  `service_info` is no longer used and can be dropped
  (`db.service_info.drop()` in the database of the runs).
- `version` is the version of the installed package unless
  `service_info.service.version` is set (before: `0.18.0` from the
  example configuration). Images built without
  `--build-arg SETUPTOOLS_SCM_PRETEND_VERSION=<version>` report
  `0.0.0.dev0`.
- Fields that aren't set are left out instead of being served as `null`,
  and values are served as configured (e.g., URLs are no longer
  normalized). Runs with states that proWES doesn't know are left out of
  `system_state_counts`. Startup no longer fetches the GA4GH service info
  schema from GitHub.
- The Helm chart no longer creates the collection `service_info`; set the
  service info of the deployment in `config.service_info` of the chart's
  values, which the chart passes to the app as a configuration overlay
  (see [Service info](deployment.md#service-info) in Deployment).
- Runs that proWES accepted keep being tracked if their workflow type or
  version is later removed from `workflow_type_versions` (new runs are
  still checked). Workers of earlier versions still check stored runs
  against the service info in the database: stop them before the app of
  this version accepts runs (no rolling update across this upgrade).

### Helm chart (breaking)

The Helm chart was rebuilt on FOCA's library chart `foca-common`, like the
charts of proTES and cloud-registry, and runs on vanilla Kubernetes and
OpenShift alike: the values changed (rendering fails on those of the legacy
chart, naming their replacements), and resource names are derived from the
release name. Releases of the legacy chart can't be upgraded in place:
uninstall them and install the chart anew, as described in [From the legacy
chart](deployment.md#from-the-legacy-chart). The chart passes `config` to the
app and the worker as a configuration overlay, takes all credentials from
Secrets of your own, and deploys the image tag `dev` by default (set
`prowes.tag` for a fixed version).

### WES 1.1.0 (breaking)

proWES serves WES 1.1.0 instead of 1.0.1 (see [API](api.md#wes-110)):

- Service info: `workflow_engine_versions` maps each engine to an object
  (`cwltool: {workflow_engine_version: ["3.1"]}`; before: `cwltool:
  "3.1"`); the app refuses to start with the old form. Set
  `type.version` and `supported_wes_versions` to `1.1.0` if your
  configuration sets them (see [Service info](configuration.md#service-info)).
  The example of the Helm chart's `config` (`deployment/values.yaml`) has
  the new form.
- `GET /runs/{run_id}/tasks` and `GET /runs/{run_id}/tasks/{task_id}` are
  part of the specification and answer `501 Not Implemented` for now.
- `workflow_engine` and `workflow_engine_version` in `POST /runs` are
  refused with `400 Bad Request` unless empty (as with WES 1.0.1, whose
  specification didn't have them).
- Run and task logs keep the `system_logs` of the WES instance (runs
  tracked from now on).
- Clients that read the service info must accept the object form of
  `workflow_engine_versions` (and the version `1.1.0`). Otherwise, the
  endpoints and fields of WES 1.0.1 are unchanged: clients of the runs
  endpoints keep working.

### Timeouts

Gunicorn's threaded workers don't stop requests that hang, so requests to
the remote WES are limited: forwarding runs by
`custom.post_runs.timeout_post` (now 30 seconds; before: none), also for
reading run logs, canceling runs by `custom.defaults.timeout`.

### Run logs

The Celery worker stores the outputs, the engine log and the task logs of
the remote run log, each if it is valid (invalid ones and run logs that
can't be read are logged as warnings); the run keeps its own identifier and
state (before: the task failed before storing any). Other fields, e.g., of
WES 1.1, are left out.

### Tokens and failed runs

Runs submitted without a token are forwarded to the remote WES without an
`Authorization` header (before: `Authorization: Bearer None`, which WES
instances that check tokens rejected as an invalid token), and headers of
one request to the remote WES no longer stick to later ones (e.g., a
`Content-Type`).

Runs whose document has no identifier of the remote run are set to
`SYSTEM_ERROR` when the Celery worker fails to track them (before: they
stayed `INITIALIZING`).
