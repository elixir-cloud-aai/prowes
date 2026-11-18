# API

proWES serves the [GA4GH WES
API](https://github.com/ga4gh/workflow-execution-service-schemas), version
1.0.1, at the base path `/ga4gh/wes/v1`. The specification is served at
`/ga4gh/wes/v1/openapi.json` and a Swagger UI at `/ga4gh/wes/v1/ui/`. The
specifications are in
[`pro_wes/api/`](https://github.com/elixir-cloud-aai/prowes/tree/dev/pro_wes/api):
the WES specification as published, and `additions.openapi.yaml`, which
completes it (e.g., with the schemas of the GA4GH service info).

| Endpoint | Description |
| --- | --- |
| `GET /service-info` | The service info, served by FOCA from the configuration (see [Service info](configuration.md#service-info)) |
| `POST /runs` | Start a run: forwards it to a WES instance (see [Runs](#runs)) |
| `GET /runs` | List runs, newest first, with `page_size` and `page_token` |
| `GET /runs/{run_id}` | The run log of a run |
| `GET /runs/{run_id}/status` | The state of a run |
| `POST /runs/{run_id}/cancel` | Cancel a run |

## Runs

For `POST /runs`, proWES validates the request (a form, as the WES API
defines it), stores the run in its database, writes its workflow
attachments to the storage of the runs (`custom.post_runs.storage_path`, see
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
state `SYSTEM_ERROR`.
