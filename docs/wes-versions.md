# WES versions

proWES serves [WES](https://github.com/ga4gh/workflow-execution-service-schemas)
1.1.0 to its clients, and forwards runs to WES instances of WES 1.0 (1.0.0,
1.0.1) and 1.1 (1.1.0), so clients of WES 1.1.0 can use WES instances that
implement only WES 1.0. How proWES finds out the version of a WES instance is
described in [WES versions](configuration.md#wes-versions). Versions are
compared by major and minor version only, as patch versions don't change the
API: WES 1.0.0 and 1.0.1 are both `1.0`.

proWES stores runs in WES 1.1.0, as clients sent them. Each run is forwarded
in the version of its WES instance, which is stored with the run
(`wes_endpoint.wes_version`, not served), and the run logs of that WES
instance are converted to WES 1.1.0.

## Routing

New runs go to the first WES instance of `custom.wes.backends` that can run
them unchanged (see [WES instances](configuration.md#wes-instances)). Once
FOCA's gateway plugins choose and order the WES instances
([#121](https://github.com/elixir-cloud-aai/prowes/issues/121)), plugins of the
`route` phase will see only these WES instances, so that routing needs no
knowledge of versions.

Runs that choose the workflow engine (`workflow_engine`,
`workflow_engine_version`, not empty) can run on WES 1.1 only, and go to
the first WES instance of WES 1.1 of the list; all other runs go to the
first WES instance of the list. The service info of WES instances without
the label `wes.version` is read as needed, for their versions (see [WES
versions](configuration.md#wes-versions)): label WES instances whose
service info reports no version of the WES specification (they count as
WES 1.0), so that runs that choose the engine can go to them.

Runs for which no WES instance remains are rejected with `400 Bad
Request`, naming the fields (`errors`, with a `pointer` to each field),
and are not stored. If a WES instance didn't answer so far and no other can
run the run, the answer is `503 Service Unavailable` instead, as retrying
may help. A WES instance whose version was read once keeps that version if
it fails to answer later. A run that chooses the engine is forwarded in the
version that made its WES instance eligible: its service info isn't read
again in between, so that the run never goes out without what it chose. If
that version changed meanwhile (e.g., read by a concurrent request), the
next eligible WES instance is chosen, or, if none is left, the answer is
`503 Service Unavailable`.

A WES instance of WES 1.1.0 may still reject a run (e.g., an engine that it
doesn't support); the run then fails as for any rejected run (see
[Errors](api.md#errors)).

## Runs sent to WES 1.0

| Field (WES 1.1.0) | Sent to WES 1.0 |
| --- | --- |
| `workflow_engine`, `workflow_engine_version`, not empty | Not eligible: the WES instance would run the workflow with an engine of its own choice. Such runs go to WES instances of WES 1.1 only. |
| `workflow_engine`, `workflow_engine_version`, empty | Not sent: they choose nothing (and are not stored). |
| Other fields (`workflow_params`, `workflow_type`, `workflow_type_version`, `workflow_url`, `tags`, `workflow_engine_parameters`, `workflow_attachment`) | Sent as they are: WES 1.0 has them, too. |

## Responses of WES 1.0

| Response (WES 1.0) | Served in WES 1.1.0 |
| --- | --- |
| Run log (`GET /runs/{run_id}`): `outputs`, `run_log`, `task_logs` | As stored: valid in WES 1.1.0, which allows `Log` objects in `task_logs`. |
| States (`GET /runs/{run_id}/status`) | As they are: WES 1.1.0 only adds `PREEMPTED`. |
| Run lists | Not read: proWES lists runs from its database, as `RunSummary` (see [Run lists](api.md#run-lists)). |
| Tasks (`GET /runs/{run_id}/tasks`) | WES 1.0 has no task endpoints; proWES doesn't serve them yet ([#124](https://github.com/elixir-cloud-aai/prowes/issues/124)). They will be answered from the stored `task_logs`, for all WES instances. |
| Service info | Read only for the version (`type`), as it is: WES 1.0 lists the versions of each engine as strings. |

## Adding a WES version

1. Vendor its specification (see [API](api.md)), and update the models
   (`pro_wes/ga4gh/wes/models.py`) to it.
2. Add one adapter between it and the version before it to `ADAPTERS` in
   `pro_wes/ga4gh/wes/versions.py`, a subclass of `WesAdapter`, with its tests:
   `incompatibilities` lists the fields of run requests that the older version
   can't express without changing what a run does, `downgrade_run_request`
   drops the rest, and `upgrade_run_log` converts run logs that WES instances
   of the older version serve.

Chains of adapters then cover all older versions.
