#!/usr/bin/env bash
# Integration test of proWES against Sapporo, a real WES implementation,
# started with the Compose override of this directory (`compose.yaml`, on
# top of the smoke test's override): runs of the CWL workflows of
# `workflows/` that complete, fail and are canceled, with their states, run
# logs and the run list as clients see them, and the states that Sapporo
# reports for them. Requests carry an access token of the smoke test's fake
# identity provider. It also reports the WES version of Sapporo's service
# info (to the job summary in CI), and checks that Sapporo still requires
# the workflow engine that `engine_proxy.py` adds (#66).
#
# Run it from the repository root, with the base URL of proWES's WES API as
# argument (default `http://localhost:8090/ga4gh/wes/v1`), after starting
# the services (see `compose.yaml`); needs curl, jq and Docker Compose, with
# `COMPOSE_FILE` and `COMPOSE_PROJECT_NAME` as for `up` (the defaults
# below). Sapporo pulls the image of cwltool for the first run, so the waits
# are long: each polls up to `FINAL_WAIT` times (default 300), about once a
# second. Every request has a limit of 60 seconds (for the whole Compose call
# only where `timeout` of GNU coreutils exists). Tokens go to curl on its
# standard input, so that they don't show up in the process list.
set -euo pipefail

api="${1:-http://localhost:8090/ga4gh/wes/v1}"
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yaml:tests/smoke/compose.yaml:tests/integration/compose.yaml}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-prowes-integration}"
final_wait="${FINAL_WAIT:-300}"
# The workflows, as the engine containers fetch them (service `workflows`)
workflows=http://workflows:8000
body="$(mktemp)"
trap 'rm -f "${body}"' EXIT
token=""

# Run a command with a limit of 60 seconds where `timeout` exists
limited() {
  if command -v timeout > /dev/null; then
    timeout 60 "$@"
  else
    "$@"
  fi
}

# Request a path of Sapporo from the Compose network (it isn't published),
# with an optional URL-encoded form (`<key>=<value>` arguments); prints the
# status and the JSON body as `{"status": ..., "body": ...}`
sapporo() {
  limited docker compose exec -T fake-wes python -c '
import json, sys, urllib.error, urllib.parse, urllib.request
method, path, fields = sys.argv[1], sys.argv[2], sys.argv[3:]
data = urllib.parse.urlencode([tuple(f.split("=", 1)) for f in fields]).encode() if fields else None
request = urllib.request.Request("http://sapporo:1122" + path, data=data, method=method)
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        status, body = response.status, response.read()
except urllib.error.HTTPError as error:
    status, body = error.code, error.read()
print(json.dumps({"status": status, "body": json.loads(body)}))
' "$@"
}

# Request a path of proWES, with an optional form (curl `--form-string`
# arguments); check the status and that the JSON response matches a jq
# filter
check() {
  local method="$1" path="$2" status="$3" filter="$4" actual field
  shift 4
  local args=(--silent --show-error --max-time 60 --output "${body}"
    --write-out '%{http_code}' -X "${method}"
    --header 'Accept: application/json' --header @-)
  for field in "$@"; do
    args+=(--form-string "${field}")
  done
  actual="$(printf 'Authorization: Bearer %s\n' "${token}" \
    | curl "${args[@]}" "${api}${path}")"
  if [ "${actual}" != "${status}" ] \
      || ! jq -e "${filter}" "${body}" > /dev/null; then
    echo "::error::${method} ${path}: status ${actual}, expected ${status} and ${filter}"
    cat "${body}"
    return 1
  fi
}

# Wait until a run has a state
await_state() {
  local state=""
  for _ in $(seq "${final_wait}"); do
    check GET "/runs/$1/status" 200 '.state != null'
    state="$(jq -r .state "${body}")"
    [ "${state}" = "$2" ] && return
    sleep 1
  done
  echo "::error::run $1 is ${state}, not $2"
  check GET "/runs/$1" 200 'true'
  cat "${body}"
  return 1
}

# The identifier of a run of proWES at Sapporo, from proWES's database
backend_id() {
  limited docker compose exec -T mongodb mongosh --quiet runStore --eval \
    "print(db.runs.findOne({'run_log.run_id': '$1'}).wes_endpoint.run_id)"
}

# The state of a run of proWES as Sapporo reports it
backend_state() {
  sapporo GET "/runs/$(backend_id "$1")/status" | jq -r .body.state
}

# Create a run of a workflow of `workflows/` with parameters; the identifier
# is in `${id}`. The tag is unique to this test run, so that its runs are
# told from those of earlier test runs on the same deployment
tag="{\"integration\": \"$(date +%s)-${RANDOM}\"}"
create() {
  check POST /runs 200 '.run_id | length > 0' \
    "workflow_params=$2" 'workflow_type=CWL' 'workflow_type_version=v1.0' \
    "workflow_url=${workflows}/$1" "tags=${tag}"
  id="$(jq -r .run_id "${body}")"
}

echo "Sapporo: service info"
info="$(sapporo GET /service-info)"
jq -e '.status == 200 and .body.type.artifact == "wes"' <<< "${info}" > /dev/null
summary="$(jq -r '.body | "Sapporo \(.version) reports WES version \(.type.version) (`type.version`), supported WES versions \(.supported_wes_versions | join(", ")) (`supported_wes_versions`)"' <<< "${info}")"
echo "${summary}"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "${summary}" >> "${GITHUB_STEP_SUMMARY}"
fi

# Sapporo rejects runs without workflow engine, which proWES doesn't forward
# yet (#66); `engine_proxy.py` adds it. Once this fails, drop the proxy
echo "Sapporo: runs without workflow engine are rejected"
rejected="$(sapporo POST /runs 'workflow_params={}' 'workflow_type=CWL' \
  'workflow_type_version=v1.0' "workflow_url=${workflows}/hello.cwl")"
if ! jq -e '.status == 400 and (.body.msg | test("workflow_engine"))' \
    <<< "${rejected}" > /dev/null; then
  echo "::error::Sapporo doesn't reject runs without workflow engine as expected: drop engine_proxy.py if it accepts them"
  echo "${rejected}"
  exit 1
fi

token="$(limited docker compose exec -T fake-wes python /smoke/fake_wes.py token alice)"

# The output file is in the engine's standard output (cwltool's output
# object, with the size of the message and its newline). Sapporo's outputs
# are a list of files, not an object as WES has them, so proWES doesn't
# store them (an invalid field of the run log)
echo "proWES: a run that completes"
message="integration-$(date +%s)-${RANDOM}"
create hello.cwl "{\"message\": \"${message}\"}"
done_id="${id}"
await_state "${done_id}" COMPLETE
check GET "/runs/${done_id}" 200 "
  .run_id == \"${done_id}\"
  and .state == \"COMPLETE\"
  and .request.workflow_url == \"${workflows}/hello.cwl\"
  and .run_log.exit_code == 0
  and (.run_log.stdout | fromjson | .greeting.basename == \"greeting.txt\"
    and .greeting.size == $(( ${#message} + 1 )))
  and (has(\"outputs\") | not)"
[ "$(backend_state "${done_id}")" = COMPLETE ] \
  || { echo "::error::run ${done_id} isn't complete at Sapporo"; exit 1; }
check GET '/runs?page_size=100' 200 \
  "any(.runs[]; .run_id == \"${done_id}\" and .state == \"COMPLETE\")"
# Finished runs aren't cancelable (not forwarded to Sapporo)
check POST "/runs/${done_id}/cancel" 409 '.status == 409'

echo "proWES: a run that fails"
create fail.cwl '{}'
failed="${id}"
await_state "${failed}" EXECUTOR_ERROR
check GET "/runs/${failed}" 200 '.state == "EXECUTOR_ERROR" and .run_log.exit_code != 0'

echo "proWES: a run that is canceled while it runs"
create sleep.cwl '{"seconds": 300}'
running="${id}"
await_state "${running}" RUNNING
check POST "/runs/${running}/cancel" 200 ".run_id == \"${running}\""
await_state "${running}" CANCELED
[ "$(backend_state "${running}")" = CANCELED ] \
  || { echo "::error::run ${running} isn't canceled at Sapporo"; exit 1; }
# Sapporo stopped the engine's container (the only containers in the
# Compose network that Compose didn't start)
engines="$(docker ps --filter network=prowes-integration \
  --format '{{.Label "com.docker.compose.service"}} {{.Names}}' \
  | awk 'NF == 1')"
if [ -n "${engines}" ]; then
  echo "::error::engine containers still running: ${engines}"
  exit 1
fi
check GET '/runs?page_size=100' 200 \
  "any(.runs[]; .run_id == \"${running}\" and .state == \"CANCELED\")"

echo "Integration test passed"
