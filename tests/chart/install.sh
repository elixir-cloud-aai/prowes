#!/usr/bin/env bash
# Installs the Helm chart (`deployment/`) on the current Kubernetes cluster
# (a kind cluster, `KIND_CLUSTER`, into which the image is loaded) with the
# image of the working tree and the values of CI (`deployment/ci/`), and
# checks it:
#
# - the app, the worker, MongoDB and the broker become ready (`--wait`);
# - the service info is that of the config overlay (`config`);
# - the app can write to the storage of the runs (`/data`, the chart's
#   claim), as the image's user;
# - the worker connects to the broker (it logs that it is ready);
# - neither the app nor the worker log errors;
# - MongoDB's claim is kept on uninstallation and adopted by a new release,
#   with its data;
# - upgrades of releases with resources of the legacy chart fail.
#
# Run by the `chart-install` job of `.github/workflows/ci.yml`; locally,
# with a kind cluster of that name and `KUBECONFIG` set (see "Testing the
# chart" in `docs/deployment.md`). Needs docker, kind, helm and kubectl.

set -euo pipefail

CLUSTER="${KIND_CLUSTER:-chart-testing}"
NAMESPACE="${NAMESPACE:-prowes-ci}"
RELEASE="${RELEASE:-prowes}"
IMAGE="${IMAGE:-prowes:ci}"
SERVICE_ID="org.example.wes.ci"
CHART="$(cd "$(dirname "$0")/../../deployment" && pwd)"

echo "Building image ${IMAGE}"
docker build --tag "${IMAGE}" --build-arg SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+chart \
  "${CHART}/.."
kind load docker-image "${IMAGE}" --name "${CLUSTER}"

helm dependency build "${CHART}"
(cd "${CHART}" && sha256sum -c charts.sha256)

# Secrets with the MongoDB credentials and the broker settings of
# `ci/test-values.yaml`, with random passwords; the broker URL has the host
# of the release's broker Service
# A namespace of its own, new on each run (also after a failed one), so that
# the Secrets' random passwords match the new MongoDB and broker data
kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait
kubectl create namespace "${NAMESPACE}"
kubectl create secret generic mongodb -n "${NAMESPACE}" \
  --from-literal=root-username=root \
  --from-literal=root-password="$(openssl rand -hex 16)" \
  --from-literal=username=prowes \
  --from-literal=password="$(openssl rand -hex 16)"
broker_password="$(openssl rand -hex 16)"
kubectl create secret generic celery -n "${NAMESPACE}" \
  --from-literal=username=prowes \
  --from-literal=password="${broker_password}" \
  --from-literal=broker-url="pyamqp://prowes:${broker_password}@${RELEASE}-broker:5672//"

install() {
  helm install "${RELEASE}" "${CHART}" -n "${NAMESPACE}" \
    -f "${CHART}/ci/test-values.yaml" \
    --set prowes.image="${IMAGE%:*}" --set prowes.tag="${IMAGE##*:}" \
    --set prowes.imagePullPolicy=Never \
    --set config.service_info.service.id="${SERVICE_ID}" \
    --wait --timeout 600s
}

# Runs a Python script in the app's container (which has no curl)
app_python() {
  kubectl exec -n "${NAMESPACE}" "deployment/${RELEASE}" -c prowes -- python -c "$1"
}

echo "Installing release ${RELEASE}"
install

echo "Checking the service info"
service_id="$(app_python "
import json, urllib.request
with urllib.request.urlopen('http://localhost:8080/ga4gh/wes/v1/service-info', timeout=10) as r:
    print(json.load(r)['id'])
")"
echo "Service info id: ${service_id}"
if [[ "${service_id}" != "${SERVICE_ID}" ]]; then
  echo "::error::The service info is not that of the config overlay"
  exit 1
fi

echo "Checking the storage of the runs"
app_python "
import os, pathlib
assert os.getuid() == 1000, os.getuid()
probe = pathlib.Path('/data/.probe')
probe.write_text('ok')
probe.unlink()
"
claim="$(kubectl get deployment "${RELEASE}" -n "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.volumes[?(@.name=="data")].persistentVolumeClaim.claimName}')"
if [[ "${claim}" != "${RELEASE}-data" ]]; then
  echo "::error::The app does not mount the claim ${RELEASE}-data at /data (${claim})"
  exit 1
fi

# The worker logs that it is ready once it has connected to the broker; it
# has no probes, so its logs are polled (FOCA's log format appends the
# logger, `[celery.apps.worker]`). Errors in Gunicorn's (`[ERROR]`),
# FOCA's (`: ERROR   ]`) and Celery's (`ERROR/`) log formats, except the
# connection retries while the broker starts
echo "Checking the worker"
logs=""
for _ in $(seq 1 30); do
  logs="$(kubectl logs -n "${NAMESPACE}" --all-containers --prefix \
    -l app.kubernetes.io/component=worker --tail=-1 2>&1)" || true
  if grep -Eq ' ready\.( |$)' <<< "${logs}"; then
    break
  fi
  sleep 5
done
printf '%s\n' "${logs}"
if ! grep -Eq ' ready\.( |$)' <<< "${logs}"; then
  echo "::error::The worker is not ready"
  exit 1
fi

echo "Checking the logs for errors"
logs="$(kubectl logs -n "${NAMESPACE}" --all-containers --prefix \
  -l 'app.kubernetes.io/component in (app,worker)' --tail=-1)"
if grep -v 'consumer: Cannot connect to' <<< "${logs}" \
    | grep -E '\[ERROR\]|: ERROR +\]|ERROR/'; then
  echo "::error::The app or the worker logged errors"
  exit 1
fi

# MongoDB's claim is kept when the release is uninstalled
# (`helm.sh/resource-policy: keep`), and a new release of the same name and
# namespace adopts it with its data
echo "Checking that MongoDB's data survives a reinstallation"
mongo_eval() {
  kubectl exec -n "${NAMESPACE}" "deployment/${RELEASE}-mongodb" -- sh -c \
    'mongosh --quiet -u "${MONGO_INITDB_ROOT_USERNAME}" -p "${MONGO_INITDB_ROOT_PASSWORD}" --eval "$1"' \
    sh "$1" | tail -n 1
}
claim_uid() {
  kubectl get pvc "${RELEASE}-mongodb" -n "${NAMESPACE}" \
    -o jsonpath='{.metadata.uid}' --ignore-not-found
}
mongo_eval "db.getSiblingDB('runStore').ci.insertOne({kept: true})" > /dev/null
before="$(claim_uid)"
helm uninstall "${RELEASE}" -n "${NAMESPACE}" --wait --timeout 300s
if [[ -z "${before}" || "$(claim_uid)" != "${before}" ]]; then
  echo "::error::MongoDB's claim was not kept on uninstallation"
  exit 1
fi
install
if [[ "$(claim_uid)" != "${before}" ]]; then
  echo "::error::The new release did not adopt MongoDB's claim"
  exit 1
fi
kept="$(mongo_eval "db.getSiblingDB('runStore').ci.countDocuments({kept: true})")"
if [[ "${kept}" != "1" ]]; then
  echo "::error::MongoDB's data did not survive the reinstallation (${kept})"
  exit 1
fi

# A Deployment as the legacy chart created them (annotated with the release,
# without the label `app.kubernetes.io/instance`; no pods) makes upgrades of
# the release fail (`prowes.checkUpgrade`), as they would delete the legacy
# volumes
echo "Checking that upgrades of legacy releases fail"
kubectl create deployment legacy-mongodb -n "${NAMESPACE}" --image "${IMAGE}" \
  --replicas 0
kubectl annotate deployment legacy-mongodb -n "${NAMESPACE}" \
  meta.helm.sh/release-name="${RELEASE}"
if output="$(helm upgrade "${RELEASE}" "${CHART}" -n "${NAMESPACE}" \
    --reuse-values 2>&1)"; then
  echo "::error::The upgrade of a release with legacy resources did not fail"
  exit 1
fi
printf '%s\n' "${output}"
if ! grep -q 'was installed with the legacy chart' <<< "${output}"; then
  echo "::error::The upgrade failed for another reason than the legacy resources"
  exit 1
fi
kubectl delete deployment legacy-mongodb -n "${NAMESPACE}"
echo "The chart works"
