#!/usr/bin/env bash
# Smoke test of proWES deployed with Docker Compose against a fake WES
# (`compose.yaml` and `fake_wes.py` of this directory): builds and starts
# the app, the Celery worker, MongoDB, RabbitMQ and the fake WES, waits
# until their health checks pass, then creates runs, which proWES forwards
# to the fake WES, waits until the Celery worker has tracked them (via
# RabbitMQ) to their final state, and checks the other endpoints, the
# cancellation of a run, and that run requests with credentials in URLs and
# larger than the size limit are rejected. All requests but those of the
# service info carry an access token of the fake identity provider (the
# fake WES); requests without a valid token are rejected, and users see only
# their own runs. Run it from the repository root,
# with the base URL of the WES API as argument (default
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
# The run that the fake WES holds, once created: canceled on failure (best
# effort), so that its Celery task doesn't keep tracking it
held=""
# Access tokens: of the requests of `check` and `await` (none if empty), and
# of the user that creates the runs
token=""
user_token=""
on_exit() {
  local status=$?
  rm -f "${body}"
  if [ "${status}" -ne 0 ]; then
    if [ -n "${held}" ]; then
      curl --silent --output /dev/null -X POST \
        --header "Authorization: Bearer ${user_token}" \
        "${api}/runs/${held}/cancel" || true
    fi
    echo "Smoke test failed; logs of the services:"
    docker compose logs --tail 100 || true
  fi
  exit "${status}"
}
trap on_exit EXIT

# Builds and starts the services; waits until the containers are healthy
# (see their health checks) or the certificates are created
docker compose up -d --build --wait --wait-timeout 300

# Access token of a user from the fake identity provider (the fake WES)
token_of() {
  docker compose exec -T fake-wes python /smoke/fake_wes.py token "$1"
}
user_token="$(token_of alice)"
token="${user_token}"

# Request a path, with an optional form (curl `--form-string` arguments);
# check the status and that the JSON response matches a jq filter
check() {
  local method="$1" path="$2" status="$3" filter="$4" actual
  shift 4
  local args=(--silent --show-error --max-time 10 --output "${body}"
    --write-out '%{http_code}' -X "${method}"
    --header 'Accept: application/json')
  if [ -n "${token}" ]; then
    args+=(--header "Authorization: Bearer ${token}")
  fi
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
# log, which the Celery worker stores after the final state; failed requests
# are retried, for up to about 2 minutes in all
await() {
  local deadline=$((SECONDS + 120)) status=""
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(curl --silent --max-time 10 --output "${body}" \
      --write-out '%{http_code}' --header 'Accept: application/json' \
      --header "Authorization: Bearer ${token}" "${api}$1" || true)"
    if [ "${status}" = 200 ] && jq -e "$2" "${body}" > /dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  echo "::error::GET $1 doesn't match $2 (last status: ${status})"
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

check GET /service-info 200 '.type.artifact == "wes" and .type.version == "1.1.0"'
# The service info is public; all other requests need a valid access token
token=""
check GET /service-info 200 '.type.artifact == "wes"'
check GET /runs 401 '.status == 401'
token="not-a-token"
check GET /runs 401 '.status == 401'
token="${user_token}"
# A run is forwarded to the WES, tracked to its end, and the final run log
# of the WES (exit code, outputs, standard output) is stored; the request
# is its own
check POST /runs 200 '.run_id | length > 0' "${request[@]}" 'tags={"smoke": "true"}'
id="$(jq -r .run_id "${body}")"
await "/runs/${id}/status" ".run_id == \"${id}\" and .state == \"COMPLETE\""
await "/runs/${id}" '.run_log.stdout == "fake complete"'
check GET "/runs/${id}" 200 \
  ".run_id == \"${id}\" and .state == \"COMPLETE\" and .request.workflow_url == \"https://example.org/smoke.cwl\" and .outputs.workflow_url == \"https://example.org/smoke.cwl\" and .run_log.exit_code == 0"
check GET '/runs?page_size=100' 200 "any(.runs[]; .run_id == \"${id}\" and .state == \"COMPLETE\")"
# Finished runs aren't cancelable (not forwarded to the WES)
check POST "/runs/${id}/cancel" 409 '.status == 409'
# A run that the WES holds is running until it is canceled, then canceling
# until the WES reports it canceled, and its final run log is stored
check POST /runs 200 '.run_id | length > 0' "${request[@]}" 'tags={"fake.hold": "true"}'
held="$(jq -r .run_id "${body}")"
await "/runs/${held}/status" '.state == "RUNNING"'
check POST "/runs/${held}/cancel" 200 ".run_id == \"${held}\""
await "/runs/${held}/status" '.state == "CANCELED"'
await "/runs/${held}" '.run_log.stdout == "fake canceled"'
check GET "/runs/${held}" 200 '.state == "CANCELED"'
check GET '/runs?page_size=100' 200 "any(.runs[]; .run_id == \"${held}\" and .state == \"CANCELED\")"
# Other users don't see the runs of a user, nor read or cancel them
token="$(token_of bob)"
check GET '/runs?page_size=100' 200 "all(.runs[]; .run_id != \"${id}\")"
check GET "/runs/${id}" 403 '.status == 403'
check POST "/runs/${id}/cancel" 403 '.status == 403'
token="${user_token}"
# Unknown runs aren't found (lowercase letters, which run identifiers don't
# have)
check GET /runs/unknwn 404 '.status == 404'
check GET /runs/unknwn/status 404 '.status == 404'
# The task endpoints of WES 1.1.0 aren't served yet (#124)
check GET "/runs/${id}/tasks" 501 '.status == 501'
# Runs with credentials in URLs are rejected (`url_credentials: reject`),
# without echoing them
check POST /runs 400 '.status == 400 and (.detail | test("credentials")) and (tostring | test("s3cr3t") | not)' \
  "workflow_params=${params}" 'workflow_type=CWL' 'workflow_type_version=v1.0' \
  'workflow_url=https://user:s3cr3t@example.org/smoke.cwl'
# Request bodies larger than `server.max_content_length` (10 MiB) are
# rejected before they are parsed: an attachment of 11 MiB. The app may
# answer before curl has sent the whole body and close the connection, so
# that curl fails after receiving the answer: only the status counts
large="$(mktemp)"
head -c $((11 * 1024 * 1024)) /dev/zero > "${large}"
status="$(curl --silent --max-time 30 --output "${body}" \
  --write-out '%{http_code}' -X POST --header 'Accept: application/json' \
  --header "Authorization: Bearer ${user_token}" \
  --form-string "workflow_params=${params}" --form-string 'workflow_type=CWL' \
  --form-string 'workflow_type_version=v1.0' \
  --form-string 'workflow_url=https://example.org/smoke.cwl' \
  --form "workflow_attachment=@${large};filename=large.bin" "${api}/runs" \
  || true)"
rm -f "${large}"
# (the size limit of bodies, not that of each attachment)
if [ "${status}" != 413 ] || ! jq -e '.status == 413 and (.detail | test("request body"))' "${body}" > /dev/null; then
  echo "::error::POST /runs with 11 MiB: status ${status}, expected 413"
  cat "${body}"
  exit 1
fi
# The fake WES got each run once, with all fields as sent to proWES (the
# held one with its tags, so it was held until canceled), and both are
# finished there
docker compose exec -T fake-wes python /smoke/fake_wes.py verify 1 \
  "${request[@]}" 'tags={"smoke": "true"}'
docker compose exec -T fake-wes python /smoke/fake_wes.py verify 1 \
  "${request[@]}" 'tags={"fake.hold": "true"}'
echo "Smoke test passed"
