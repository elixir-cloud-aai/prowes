#!/usr/bin/env bash
# Smoke test of proWES deployed with Docker Compose against a fake WES
# (`compose.yaml` and `fake_wes.py` of this directory): builds and starts
# the app, the Celery worker, MongoDB, RabbitMQ and the fake WES, waits
# until their health checks pass, then creates runs, which proWES forwards
# to the fake WES, waits until the Celery worker has tracked them (via
# RabbitMQ) to their final state, and checks the other endpoints and the
# cancellation of a run. Run it from the repository root, with the base URL
# of the WES API as argument (default
# `http://localhost:8090/ga4gh/wes/v1`); needs curl, jq and Docker Compose.
# The deployment is left running (Compose project `COMPOSE_PROJECT_NAME`,
# default `prowes-smoke`, so that it doesn't touch the data of other
# deployments); remove it with `docker compose down --volumes` with the
# same `COMPOSE_FILE` and `COMPOSE_PROJECT_NAME`.
set -euo pipefail

api="${1:-http://localhost:8090/ga4gh/wes/v1}"
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yaml:tests/smoke/compose.yaml}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-prowes-smoke}"
body="$(mktemp)"
trap 'rm -f "${body}"' EXIT

# Builds and starts the services; waits until the containers are healthy
# (see their health checks) or the certificates are created
docker compose up -d --build --wait --wait-timeout 300

# Request a path, with an optional form (curl `--form` arguments); check
# the status and that the JSON response matches a jq filter
check() {
  local method="$1" path="$2" status="$3" filter="$4" actual
  shift 4
  local args=(--silent --show-error --output "${body}"
    --write-out '%{http_code}' -X "${method}"
    --header 'Accept: application/json')
  local field
  for field in "$@"; do
    args+=(--form-string "${field}")
  done
  actual="$(curl "${args[@]}" "${api}${path}")"
  if [ "${actual}" != "${status}" ] \
      || ! jq -e "${filter}" "${body}" > /dev/null; then
    echo "::error::${method} ${path}: status ${actual}, expected ${status} and ${filter}"
    cat "${body}"
    return 1
  fi
}

# Wait until a run (the first argument) has a state (the second one)
await_state() {
  local state=""
  for _ in $(seq 60); do
    check GET "/runs/$1/status" 200 ".run_id == \"$1\" and .state != null"
    state="$(jq -r .state "${body}")"
    [ "${state}" = "$2" ] && return
    sleep 1
  done
  echo "::error::Run $1 not $2, but ${state}"
  return 1
}

# The fields of a run request, with the tags as the last argument
request=(
  'workflow_params={"input": "smoke"}'
  'workflow_type=CWL'
  'workflow_type_version=v1.0'
  'workflow_url=https://example.org/smoke.cwl'
  'workflow_engine_parameters={}'
)

check GET /service-info 200 '.type.artifact == "wes"'
# A run is forwarded to the WES, tracked to its end, and the final run log
# of the WES (exit code, outputs) is stored; the request is its own
check POST /runs 200 '.run_id | length > 0' "${request[@]}" 'tags={"smoke": "true"}'
id="$(jq -r .run_id "${body}")"
await_state "${id}" COMPLETE
check GET "/runs/${id}" 200 \
  ".run_id == \"${id}\" and .state == \"COMPLETE\" and .request.workflow_url == \"https://example.org/smoke.cwl\" and .outputs.workflow_url == \"https://example.org/smoke.cwl\" and .run_log.stdout == \"fake stdout\" and .run_log.exit_code == 0"
check GET /runs 200 "any(.runs[]; .run_id == \"${id}\" and .state == \"COMPLETE\")"
# A run that the WES holds is running until it is canceled, then canceling
# until the WES reports it canceled
check POST /runs 200 '.run_id | length > 0' "${request[@]}" 'tags={"fake.hold": "true"}'
held="$(jq -r .run_id "${body}")"
await_state "${held}" RUNNING
check POST "/runs/${held}/cancel" 200 ".run_id == \"${held}\""
await_state "${held}" CANCELED
check GET "/runs/${held}" 200 '.state == "CANCELED" and .run_log.exit_code == null'
check GET /runs 200 "any(.runs[]; .run_id == \"${held}\" and .state == \"CANCELED\")"
# Unknown runs aren't found
check GET /runs/UNKNWN 404 '.status == 404'
check GET /runs/UNKNWN/status 404 '.status == 404'
# The fake WES got the runs (and those of earlier runs of this test, if it
# wasn't restarted), as sent to proWES, and they are all finished
docker compose exec -T fake-wes python -c \
  "import json, urllib.request; \
  base = 'http://localhost:8080/ga4gh/wes/v1/runs'; \
  runs = json.load(urllib.request.urlopen(base))['runs']; \
  assert runs and all(run['state'] in ('COMPLETE', 'CANCELED') for run in runs), runs; \
  requests = [json.load(urllib.request.urlopen(f'{base}/{run[\"run_id\"]}'))['request'] for run in runs]; \
  assert any(request.get('workflow_params') == '{\"input\": \"smoke\"}' and request.get('workflow_type') == 'CWL' for request in requests), requests"
echo "Smoke test passed"
