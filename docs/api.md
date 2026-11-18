# API

proWES serves the [GA4GH WES
API](https://github.com/ga4gh/workflow-execution-service-schemas), version
[1.1.0](https://github.com/ga4gh/workflow-execution-service-schemas/releases/tag/1.1.0),
at the base path `/ga4gh/wes/v1`. The specification is served at
`/ga4gh/wes/v1/openapi.json` and a Swagger UI at `/ga4gh/wes/v1/ui/`. The
specifications are in
[`pro_wes/api/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/pro_wes/api):
the WES specification as published (one deviation: it references the
schema of the GA4GH service info in `additions.openapi.yaml` instead of
fetching it from GitHub on startup), and `additions.openapi.yaml`, which
completes it (the schemas of the GA4GH service info).

| Endpoint | Description |
| --- | --- |
| `GET /service-info` | The service info, served by FOCA from the configuration (see [Service info](configuration.md#service-info)) |
| `POST /runs` | Start a run: forwards it to a WES instance (see [Runs](#runs)) |
| `GET /runs` | List runs, newest first, with `page_size` and `page_token` |
| `GET /runs/{run_id}` | The run log of a run |
| `GET /runs/{run_id}/status` | The state of a run |
| `POST /runs/{run_id}/cancel` | Cancel a run |
| `GET /runs/{run_id}/tasks` | The tasks of a run (WES 1.1.0): not served yet, `501 Not Implemented` ([#124](https://github.com/elixir-cloud-aai/prowes/issues/124)) |
| `GET /runs/{run_id}/tasks/{task_id}` | A task of a run (WES 1.1.0): not served yet, `501 Not Implemented` ([#124](https://github.com/elixir-cloud-aai/prowes/issues/124)) |

### WES 1.1.0

What proWES does with the additions of WES 1.1.0 so far:

| Addition | proWES |
| --- | --- |
| `workflow_engine` and `workflow_engine_version` of `POST /runs` | Refused (`400 Bad Request`) unless empty, until they are forwarded ([#66](https://github.com/elixir-cloud-aai/prowes/issues/66)), so that a run never loses its choice of engine |
| Task endpoints, `task_logs_url` of run logs | Not served yet (see above); run logs have `task_logs` (deprecated in WES 1.1.0), as `Log` objects: the `id` and `tes_uri` of `TaskLog` are not kept yet |
| `system_logs` of logs | Stored from the run logs of the WES instance and returned in `run_log` and `task_logs` |
| State `PREEMPTED` | Tracked as a final state and returned as is (see [Run states](#run-states)) |
| `RunSummary` in `GET /runs` | Runs are listed as `RunStatus` (identifier and state), which WES 1.1.0 still allows ([#125](https://github.com/elixir-cloud-aai/prowes/issues/125)) |
| `workflow_engine_versions` of the service info | Versions of each engine as objects (`{"cwltool": {"workflow_engine_version": ["3.1"]}}`), see [Service info](configuration.md#service-info) |

The WES instances that runs are forwarded to may serve WES 1.0.1 or 1.1.0;
proWES sends them only fields of both versions. Routing by the WES version
of each instance follows with
[#122](https://github.com/elixir-cloud-aai/prowes/issues/122).

## Runs

For `POST /runs`, proWES validates the request (a form, as the WES API
defines it), checks its limits and the credentials in its URLs (see [Run
requests](configuration.md#run-requests)), stores the run in its database,
writes its workflow attachments to the storage of the runs
(`custom.post_runs.storage_path`, see
[Configuration](configuration.md#settings)) and forwards the request to a
WES instance. Currently, that is a single, built-in WES instance; WES
instances become configurable with
[#118](https://github.com/elixir-cloud-aai/prowes/issues/118).

`POST /runs` returns the run's own identifier, by which `GET
/runs/{run_id}` finds it; the identifier at the WES instance is kept in the
database. A Celery worker then tracks the run at the WES instance: it polls
its state and, once the run has finished, stores its outputs, its engine log
and its task logs (see [Run tracking](operations.md#run-tracking)). `GET
/runs/{run_id}` and `GET /runs/{run_id}/status` answer from the database;
`POST /runs/{run_id}/cancel` forwards the cancellation to the WES instance.

`GET /runs/{run_id}` returns the run request with `workflow_params`, `tags`
and `workflow_engine_parameters` as JSON objects, as the WES specification
defines `RunLog` (the form of `POST /runs` has them as JSON-encoded
strings), and leaves out fields without value (e.g., an `exit_code` that the
WES instance didn't report). All responses are validated against the
specification (see [Responses](configuration.md#responses)).

### Run states

Runs have the states of WES 1.1.0. proWES stores the state that the WES
instance reports, as is, and returns it in `GET /runs`, `GET
/runs/{run_id}` and `GET /runs/{run_id}/status`; the service info counts
the runs in each state (`system_state_counts`).

| State | Final | proWES |
| --- | --- | --- |
| `UNKNOWN` | No | Stored as reported; tracking goes on |
| `QUEUED` | No | Stored as reported; tracking goes on |
| `INITIALIZING` | No | Set when the worker starts tracking a run, or stored as reported; tracking goes on |
| `RUNNING` | No | Stored as reported; tracking goes on |
| `PAUSED` | No | Stored as reported; tracking goes on |
| `CANCELING` | No | Stored as reported; tracking goes on |
| `COMPLETE` | Yes | Stored; tracking ends |
| `EXECUTOR_ERROR` | Yes | Stored; tracking ends |
| `SYSTEM_ERROR` | Yes | Stored; tracking ends. Also set by proWES for runs it fails to forward or track (see [Run tracking](operations.md#run-tracking)) |
| `CANCELED` | Yes | Stored; tracking ends |
| `PREEMPTED` | Yes | Stored; tracking ends (WES 1.1.0) |

Once a run has a final state, the Celery worker stops polling it and
stores its run log. `PREEMPTED` means that the WES instance stopped the
run, e.g., because the resources it ran on were reclaimed. proWES treats it
like the other final states: it doesn't resubmit the run, to the same or to
another WES instance, and returns `PREEMPTED` to clients, which can submit
the run again.

## Errors

Errors are sent as problem details ([RFC
9457](https://www.rfc-editor.org/rfc/rfc9457)), with the members `title`,
`status` and `detail`, as `application/json` (section `exceptions` of the
configuration, see the [API reference](reference.md#errors)). For example,
a run that doesn't exist:

```json
{
  "title": "Not found",
  "status": 404,
  "detail": "The requested run wasn't found."
}
```

Errors of the WES instance when a run is forwarded become `400`, `401`,
`403` or, for all others, `500` responses of proWES, and the run gets the
state `SYSTEM_ERROR`. So does a run whose workflow attachments can't be
written to the storage of the runs (`500`, "Storage is not accessible."); it
is not forwarded.

Error responses of `POST /runs` for requests that proWES rejects (neither
stored nor forwarded):

| Status | Cause |
| --- | --- |
| `400 Bad Request` | The form is invalid (e.g., a missing field, a workflow engine or its version is chosen (`workflow_engine`, `workflow_engine_version`; not supported yet), `workflow_params` that is no JSON object or has an object with duplicate keys, `tags` or `workflow_engine_parameters` with values other than strings), the workflow type or version isn't supported, a URL has credentials (`custom.post_runs.url_credentials: reject`) or is malformed, or a workflow attachment has no file name, a safe file name longer than 255 bytes, or the same safe file name as another one (see [Run requests](configuration.md#run-requests)) |
| `413 Content Too Large` | The request body is larger than `server.max_content_length` (see [Request size limit](configuration.md#request-size-limit)), the run has more workflow attachments than `custom.post_runs.max_attachments`, or an attachment is larger than `custom.post_runs.max_attachment_size` |

The `detail` of each names the cause; credentials of URLs are never echoed.
Responses that don't conform to the specification (e.g., a
stored run log that is invalid) become `500 Internal server error`.
