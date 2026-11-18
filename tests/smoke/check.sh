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
# On failure, it shows the logs of the services. The deployment is left
# running (Compose project `COMPOSE_PROJECT_NAME`, default `prowes-smoke`,
# so that it doesn't touch the data of other deployments); remove it with
# `docker compose down --volumes` with the same `COMPOSE_FILE` and
# `COMPOSE_PROJECT_NAME`.
set -euo pipefail

api="${1:-http://localhost:8090/ga4gh/wes/v1}"
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yaml:tests/smoke/compose.yaml}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-prowes-smoke}"
body="$(mktemp)"
on_exit() {
  local status=$?
  rm -f "${body}"
  if [ "${status}" -ne 0 ]; then
    echo "Smoke test failed; logs of the services:"
    docker compose logs --tail 100 || true
  fi
  exit "${status}"
}
trap on_exit EXIT

# Builds and starts the services; waits until the containers are healthy
# (see their health checks) or the certificates are created
docker compose up -d --build --wait --wait-timeout 300

# Request a path, with an optional form (curl `--form-string` arguments);
# check the status and that the JSON response matches a jq filter
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

# Wait until the response to `GET` of a path (the first argument) matches a
# jq filter (the second one), e.g., for a state or a field of the final run
# log, which the Celery worker stores after the final state
await() {
  for _ in $(seq 60); do
    check GET "$1" 200 '.'
    jq -e "$2" "${body}" > /dev/null && return
    sleep 1
  done
  echo "::error::GET $1 doesn't match $2"
  cat "${body}"
  return 1
}

# The fields of a run request, with the tags as the last argument; the
# workflow parameters are unique to this test run, so that the runs it
# creates are told from those of earlier test runs on the same deployment
params="{\"input\": \"smoke-$(date +%s)-${RANDOM}\"}"
request=(
  "workflow_params=${params}"
  'workflow_type=CWL'
  'workflow_type_version=v1.0'
  'workflow_url=https://example.org/smoke.cwl'
  'workflow_engine_parameters={}'
)

check GET /service-info 200 '.type.artifact == "wes"'
# A run is forwarded to the WES, tracked to its end, and the final run log
# of the WES (exit code, outputs, standard output) is stored; the request
# is its own
check POST /runs 200 '.run_id | length > 0' "${request[@]}" 'tags={"smoke": "true"}'
id="$(jq -r .run_id "${body}")"
await "/runs/${id}/status" ".run_id == \"${id}\" and .state == \"COMPLETE\""
await "/runs/${id}" '.run_log.stdout == "fake complete"'
check GET "/runs/${id}" 200 \
  ".run_id == \"${id}\" and .state == \"COMPLETE\" and .request.workflow_url == \"https://example.org/smoke.cwl\" and .outputs.workflow_url == \"https://example.org/smoke.cwl\" and .run_log.exit_code == 0"
check GET /runs 200 "any(.runs[]; .run_id == \"${id}\" and .state == \"COMPLETE\")"
# A run that the WES holds is running until it is canceled, then canceling
# until the WES reports it canceled, and its final run log is stored
check POST /runs 200 '.run_id | length > 0' "${request[@]}" 'tags={"fake.hold": "true"}'
held="$(jq -r .run_id "${body}")"
await "/runs/${held}/status" '.state == "RUNNING"'
check POST "/runs/${held}/cancel" 200 ".run_id == \"${held}\""
await "/runs/${held}/status" '.state == "CANCELED"'
await "/runs/${held}" '.run_log.stdout == "fake canceled"'
check GET "/runs/${held}" 200 '.state == "CANCELED" and .run_log.exit_code == null'
check GET /runs 200 "any(.runs[]; .run_id == \"${held}\" and .state == \"CANCELED\")"
# Unknown runs aren't found
check GET /runs/UNKNWN 404 '.status == 404'
check GET /runs/UNKNWN/status 404 '.status == 404'
# The fake WES got both runs, with the fields as sent to proWES, and both
# are finished there
docker compose exec -T fake-wes python /smoke/fake_wes.py verify 2 \
  "workflow_params=${params}" workflow_type=CWL workflow_type_version=v1.0
echo "Smoke test passed"
