# API

proWES serves the [GA4GH WES
API](https://github.com/ga4gh/workflow-execution-service-schemas), version
[1.1.0](https://github.com/ga4gh/workflow-execution-service-schemas/releases/tag/1.1.0),
at the base path `/ga4gh/wes/v1`. The specification is served at
`/ga4gh/wes/v1/openapi.json` and a Swagger UI at `/ga4gh/wes/v1/ui/`. The
specifications are in
[`pro_wes/api/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/pro_wes/api):
the WES specification as published (one change: it references the schema
of the GA4GH service info in `additions.openapi.yaml` instead of fetching it
from GitHub on startup), and `additions.openapi.yaml`, which is merged over
it. So the vendored file has that one change only, while the two files
merged deviate from WES 1.1.0 in the following only (when serving, FOCA
adds the controllers to all operations, `x-openapi-router-controller`, and
fills in OpenAPI defaults, e.g., `style` of parameters):

| Deviation | Why |
| --- | --- |
| `openapi`, `info` (except `info.version`) and `servers` | Those of proWES |
| `x-openapi-router-controller` of `GET /service-info` | The service info is served by FOCA |
| `security` (bearer tokens, scheme `bearerAuth`), `security: []` of `GET /service-info` | WES recommends bearer tokens, but declares no security scheme; all operations but the service info require access tokens (see [Access control](access-control.md#authentication)) |
| Schemas `Service` and `ServiceType` | The schemas of the GA4GH service info 1.0.0, which WES references on GitHub |
| Schema `ProblemDetails`, used by all error responses instead of `ErrorResponse` (`msg`, `status_code`) | Errors are problem details (see [Errors](#errors)); `ErrorResponse` stays in the specification, unused |
| `400` of the endpoints of a run (`/runs/{run_id}...`); `413`, `415` and `503` of `POST /runs`; `409` of `POST /runs/{run_id}/cancel`; `501` of `GET /runs/{run_id}/tasks` and `GET /runs/{run_id}/tasks/{task_id}` | proWES extensions: statuses that proWES answers with (`400` for unknown or invalid parameters, which all operations reject), but WES doesn't declare (clients generated from the published WES specification don't know them) |
| Descriptions of `401`, `403` and `500` of `POST /runs/{run_id}/cancel` (and of its `400`) | They may also be the answer of the WES instance to the cancellation (see [Run states](#run-states)) |

Statuses that any request can get, whatever its operation, are not declared
per operation: `404` for unknown paths, `405 Method Not Allowed`, and `413
Content Too Large` for bodies larger than `server.max_content_length` (on
operations other than `POST /runs`, which declares it). They are problem
details too.

The contract tests (`tests/ga4gh/wes/test_spec.py`) fail on any other
change of the vendored file or deviation of the merged specification;
`tests/ga4gh/wes/test_error_responses.py` checks that error responses are
those declared.

| Endpoint | Description |
| --- | --- |
| `GET /service-info` | The service info, served by FOCA from the configuration (see [Service info](configuration.md#service-info)) |
| `POST /runs` | Start a run: forwards it to a WES instance (see [Runs](#runs)) |
| `GET /runs` | List runs, newest first, as `RunSummary` (see [Run lists](#run-lists)), with `page_size` and `page_token`; `next_page_token` is empty on the last page. `page_size` defaults to `custom.list_runs.default_page_size` (5), and may be 1 to `custom.list_runs.max_page_size` (100); other sizes and malformed page tokens (not 24 hexadecimal characters, as the `next_page_token` of proWES) get `400 Bad Request` |
| `GET /runs/{run_id}` | The run log of a run |
| `GET /runs/{run_id}/status` | The state of a run |
| `POST /runs/{run_id}/cancel` | Cancel a run, if it is cancelable (see [Run states](#run-states)) |
| `GET /runs/{run_id}/tasks` | The tasks of a run (WES 1.1.0): not served yet, `501 Not Implemented` ([#124](https://github.com/elixir-cloud-aai/prowes/issues/124)) |
| `GET /runs/{run_id}/tasks/{task_id}` | A task of a run (WES 1.1.0): not served yet, `501 Not Implemented` ([#124](https://github.com/elixir-cloud-aai/prowes/issues/124)) |

### WES 1.1.0

What proWES does with the additions of WES 1.1.0 so far:

| Addition | proWES |
| --- | --- |
| `workflow_engine` and `workflow_engine_version` of `POST /runs` | Stored, returned in the run log (`request`) and forwarded to WES instances of WES 1.1, the only ones that can run them (see [WES versions](wes-versions.md#routing)); empty fields choose no engine and are left out. `workflow_engine_version` requires `workflow_engine`, as specified; both must be form fields, not files |
| Task endpoints, `task_logs_url` of run logs | Not served yet (see above); run logs have `task_logs` (deprecated in WES 1.1.0), as `Log` objects: the `id` and `tes_uri` of `TaskLog` are not kept yet |
| `system_logs` of logs | Stored from the run logs of the WES instance and returned in `run_log` and `task_logs` |
| State `PREEMPTED` | Tracked as a final state and returned as is (see [Run states](#run-states)) |
| `RunSummary` in `GET /runs` | Runs are listed as `RunSummary` (identifier, state, start and end time, tags), which WES 2.0.0 will require, as the description of `RunListResponse` in WES 1.1.0 announces (see [Run lists](#run-lists)) |
| `workflow_engine_versions` of the service info | Versions of each engine as objects (`{"cwltool": {"workflow_engine_version": ["3.1"]}}`), see [Service info](configuration.md#service-info) |

The WES instances that runs are forwarded to may serve WES 1.0 (1.0.0,
1.0.1) or 1.1.0: proWES forwards each run in the version of its WES
instance, to one that can run it unchanged, and serves their responses in
WES 1.1.0 (see [WES versions](wes-versions.md)).

## Runs

For `POST /runs`, proWES validates the request (a form, as the WES API
defines it), checks its limits and the credentials in its URLs (see [Run
requests](configuration.md#run-requests)), stores the run in its database,
writes its workflow attachments to the storage of the runs
(`custom.post_runs.storage_path`, see
[Configuration](configuration.md#settings)) and forwards the request to a
WES instance: the first of `custom.wes.backends` that can run it, in the
WES version of that WES instance (see [WES
instances](configuration.md#wes-instances) and [WES
versions](wes-versions.md)). Without any, it refuses the run with `503
Service Unavailable`, before anything is stored.

The workflow attachments (`workflow_attachment`) are forwarded with the
run, in their order, to each WES instance that the run is sent to. Their
file names are forwarded as sent, directories included, so that relative
paths in the run request (e.g., `workflow_url: main.cwl` or an input
`data/input.txt`) find them at the WES instance. Names that the WES API
doesn't allow, or that aren't relative paths, are forwarded under the safe
file name that proWES writes them under instead (see [Run
requests](configuration.md#run-requests)): absolute paths, names with
`..`, `.` or empty parts (e.g., `a//b`), backslashes, quotes, percent
signs, or characters that aren't printable, names that Unicode
normalization (NFKC) changes (e.g., the fullwidth `．．／`, which would
become `../`), names longer than 4096 bytes, or with a part longer than 255
bytes (e.g.,
`../input.txt` is forwarded as `input.txt`). Names that clash only at the
WES instance (e.g., `data` and `data/input.txt`) are forwarded as they are.

`POST /runs` returns the run's own identifier, by which `GET
/runs/{run_id}` finds it; the identifier at the WES instance is kept in the
database. A Celery worker then tracks the run at the WES instance: it polls
its state and, once the run has finished, stores its outputs, its engine log
and its task logs (see [Run tracking](operations.md#run-tracking)). `GET
/runs/{run_id}` and `GET /runs/{run_id}/status` answer from the database;
`POST /runs/{run_id}/cancel` forwards the cancellation to the WES instance
if the run is cancelable (see [Run states](#run-states)).

`GET /runs/{run_id}` returns the run request with `workflow_params`, `tags`
and `workflow_engine_parameters` as JSON objects, as the WES specification
defines `RunLog` (the form of `POST /runs` has them as JSON-encoded
strings), and leaves out fields without value (e.g., an `exit_code` that the
WES instance didn't report). Fields that are empty or missing in the form
(`tags`, `workflow_engine_parameters`) are empty objects; fields that aren't
JSON objects are refused with `400 Bad Request` before the run is stored
(see [Errors](#errors)). The database keeps the fields as JSON text, as they
were sent to the WES instance; runs that versions before 0.20.0 stored with
values other than strings in the objects `tags` or
`workflow_engine_parameters` have those values returned as their JSON text
(e.g., `{"threads": 4}` as `{"threads": "4"}`). All responses are
validated against the specification (see
[Responses](configuration.md#responses)).

### Run lists

`GET /runs` lists the runs of the caller (see [Run
ownership](access-control.md#run-ownership)) as `RunSummary` objects, from
the database:

| Field | proWES |
| --- | --- |
| `run_id` | Identifier of the run at proWES |
| `state` | State of the run (see [Run states](#run-states)) |
| `start_time` | `start_time` of the engine log (`run_log`) that the WES instance reported, as is; left out until it is reported |
| `end_time` | `end_time` of the engine log, as is; left out until it is reported (e.g., while the run is running) |
| `tags` | `tags` of the run request, as an object (`{}` without tags) |

```json
{
  "runs": [
    {
      "run_id": "Q7HXBD",
      "state": "COMPLETE",
      "start_time": "2026-11-19T10:00:00Z",
      "end_time": "2026-11-19T11:00:00Z",
      "tags": {"project": "demo"}
    }
  ],
  "next_page_token": ""
}
```

The tags of runs stored by earlier versions are returned as WES defines
them, objects of strings: other values (numbers, booleans, `null`, objects
and arrays) as JSON (e.g., `"1"`, `"true"`), and URLs without credentials.
Tags that can't be read (e.g., with malformed URLs) are returned as `{}`,
and an error is logged, so that one run doesn't fail the whole list.

Lists are bounded: pages have at most `custom.list_runs.max_page_size` runs
(default 100), and tags at most `custom.post_runs.max_tags_size` bytes of
JSON text (default 16 KiB; see [Run lists](configuration.md#run-lists) and
[Run requests](configuration.md#run-requests)). WES calls `page_size` the
preferred number of runs, and has services return at most that many:
proWES refuses page sizes above the maximum with `400 Bad Request`, as
cloud-registry does with its page sizes, rather than returning fewer, so
that clients learn the limit; it refuses `0`, as a page of at most 0 runs
lists none, rather than listing all runs. The served specification keeps
the description of `page_size` of WES: the maximum is configured, so it
isn't declared there. Tags of stored runs larger than the limit (e.g.,
stored before it) are listed as `{}`, with a warning logged for each run
listed; run logs keep them.

### Run states

Runs have the states of WES 1.1.0. proWES stores the state that the WES
instance reports, as is, and returns it in `GET /runs`, `GET
/runs/{run_id}` and `GET /runs/{run_id}/status`; the service info counts
the runs in each state (`system_state_counts`).

| State | Final | Cancelable | proWES |
| --- | --- | --- | --- |
| `UNKNOWN` | No | Once forwarded | Set when a run is stored, until the worker starts tracking it, or stored as reported; tracking goes on |
| `QUEUED` | No | Yes | Stored as reported; tracking goes on |
| `INITIALIZING` | No | Yes | Set when the worker starts tracking a run, or stored as reported; tracking goes on |
| `RUNNING` | No | Yes | Stored as reported; tracking goes on |
| `PAUSED` | No | Yes | Stored as reported; tracking goes on |
| `CANCELING` | No | No | Stored as reported; tracking goes on |
| `COMPLETE` | Yes | No | Stored; tracking ends |
| `EXECUTOR_ERROR` | Yes | No | Stored; tracking ends |
| `SYSTEM_ERROR` | Yes | If set by proWES and forwarded | Stored; tracking ends. Also set by proWES for runs it fails to forward or track (see [Run tracking](operations.md#run-tracking)) |
| `CANCELED` | Yes | No | Stored; tracking ends |
| `PREEMPTED` | Yes | No | Stored; tracking ends (WES 1.1.0) |

Once a run has a final state, the Celery worker stops polling it and
stores its run log. `PREEMPTED` means that the WES instance stopped the
run, e.g., because the resources it ran on were reclaimed. proWES treats it
like the other final states: it doesn't resubmit the run, to the same or to
another WES instance, and returns `PREEMPTED` to clients, which can submit
the run again.

`POST /runs/{run_id}/cancel` forwards cancellations of cancelable runs
only, by the state that proWES has stored at the time of the request:
runs in the states `QUEUED`, `INITIALIZING`, `RUNNING` and `PAUSED`, and
runs whose state proWES doesn't know, once they have a run at the WES
instance: runs of state `UNKNOWN` (e.g., forwarded but not yet tracked by
the worker, or reported as `UNKNOWN` by the WES instance), and runs that
proWES itself set to `SYSTEM_ERROR` (see below). Other runs get `409
Conflict` (problem details, "The run can't be canceled in its current
state."), and the WES instance isn't contacted: finished runs (including
those that the WES instance reported as `SYSTEM_ERROR`), runs that are
being canceled already (`CANCELING`), and runs of state `UNKNOWN` that
proWES hasn't forwarded yet (try again once `POST /runs` has returned). A
run in a cancelable state without a run at the WES instance gets `500`
("No/few requirements provided for WES endpoint.").

proWES sets runs to `SYSTEM_ERROR` itself when it gives up tracking them
(see [Run tracking](operations.md#run-tracking)): the WES instance is
unreachable or answers with errors for too long, or tracking takes longer
than `custom.post_runs.timeout_job`. Such a run may still be running at the
WES instance, so proWES forwards its cancellation. The stored state stays
`SYSTEM_ERROR`, as proWES doesn't track the run any longer; check the
outcome at the WES instance. proWES records which `SYSTEM_ERROR`s it set
itself (`system_error_by_prowes` of the stored run); runs stored by earlier
versions don't have that record, and their cancellations are refused, as
are those of runs that failed before they were forwarded (e.g., the WES
instance refused the run request).

proWES answers `200` with the run identifier only once the WES instance has
accepted the cancellation. If it refuses it or can't be reached, proWES
answers with an error, and the stored state stays as it is. Errors are
told by the HTTP status of the response, whatever its body (e.g., an
`ErrorResponse` of WES, problem details, or an error page of a proxy):

| WES instance | proWES |
| --- | --- |
| `400`, `401` or `403` | The same status (as when a run is forwarded); proWES sends no credentials to WES instances yet, so `401` and `403` mean that the WES instance requires them |
| Another error status (e.g., `404`: it doesn't know the run, or `500`) | `500`, "The remote WES service answered with an error." |
| `2xx` without the run identifier (a JSON object `RunId`) | `500`, "The remote WES service sent an invalid response." |
| Not reached, or its response not read (e.g., connecting or the TLS handshake failed, the response timed out, see `custom.defaults.timeout`, a redirect, which proWES doesn't follow, see [Failed requests to WES instances](operations.md#failed-requests-to-wes-instances), or a `2xx` response is no JSON) | `500`, "Could not reach remote WES service." |

The WES instance may have canceled the run even if proWES couldn't read
its response; check the state of the run later (`GET /runs/{run_id}`).

## Errors

Errors are sent as problem details ([RFC
9457](https://www.rfc-editor.org/rfc/rfc9457)), with the members `title`,
`status` and `detail`, as `application/json` (section `exceptions` of the
configuration, see the [API reference](reference.md#errors)), as the
served specification declares them (schema `ProblemDetails`), so that
clients generated from it can read them. Requests with invalid fields also
get `type`, `code` (`invalid-request`) and the invalid fields (`errors`).
For example, a run that doesn't exist:

```json
{
  "title": "Not found",
  "status": 404,
  "detail": "The requested run wasn't found."
}
```

Requests without a valid access token get `401 Unauthorized`, with a
Bearer challenge (`WWW-Authenticate`); requests for runs of other users, and
with tokens whose subject can't own runs, get `403 Forbidden` (see [Access
control](access-control.md)). Only `GET /service-info` needs no token.
The token is checked first: run requests without a valid token get `401`
whatever their body, so `400` and `415` for bodies that aren't readable
forms are answered to authenticated clients only.

Errors of the WES instance when a run is forwarded become `400`, `401`,
`403` or, for all others, `500` responses of proWES, and the run gets the
state `SYSTEM_ERROR`. So does a run whose workflow attachments can't be
written to the storage of the runs (`500`, "Storage is not accessible."); it
is not forwarded.

Error responses of `POST /runs` for requests that proWES rejects (neither
stored nor forwarded):

| Status | Cause |
| --- | --- |
| `400 Bad Request` | The form is invalid (e.g., a `multipart/form-data` body without a boundary, a URL-encoded body with `workflow_attachment` (URL-encoded forms can't carry files), a missing field, `workflow_engine_version` without `workflow_engine`, `workflow_engine` or `workflow_engine_version` sent as a file, `workflow_params` that is no JSON object or has an object with duplicate keys, `tags` or `workflow_engine_parameters` with values other than strings, `tags` larger than `custom.post_runs.max_tags_size`), the workflow type or version isn't supported, no WES instance can run the run unchanged (e.g., it chooses the workflow engine, and all WES instances are of WES 1.0; see [WES versions](wes-versions.md#routing)), a URL has credentials (`custom.post_runs.url_credentials: reject`) or is malformed, or a workflow attachment has no file name, a safe file name longer than 255 bytes, or the same safe file name as another one (see [Run requests](configuration.md#run-requests)) |
| `413 Content Too Large` | The request body is larger than `server.max_content_length` (see [Request size limit](configuration.md#request-size-limit)), the run has more workflow attachments than `custom.post_runs.max_attachments`, or an attachment is larger than `custom.post_runs.max_attachment_size` |
| `415 Unsupported Media Type` | The body isn't a form: run requests are `multipart/form-data` (as in WES), or `application/x-www-form-urlencoded` if they have no workflow attachments; other media types (e.g., JSON) and bodies without a media type are refused |
| `503 Service Unavailable` | No WES instance is available: none is configured (`custom.wes.backends`), or its registries list none (yet) (see [WES instances](configuration.md#wes-instances)) |

The `detail` of each names the cause; credentials of URLs are never echoed.
`POST /runs/{run_id}/cancel` refuses runs that aren't cancelable with `409
Conflict` (see [Run states](#run-states)).
Responses that don't conform to the specification (e.g., a
stored run log that is invalid) become `500 Internal server error`.
