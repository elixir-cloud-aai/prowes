{{/*
Settings of the app for the templates of the library chart foca-common (see
`foca-common.checkSettings` and "Helm library chart" in FOCA's docs), as
YAML; the templates are called with a dict of the root context (`root`) and
these settings (`settings`). They follow the base config in the image
(`pro_wes/config.yaml`): its size limit of request bodies
(`server.max_content_length`, 10 MiB), which the library checks; it serves
the API without authentication, so no audience is required; and its database is `runStore` (`db.dbs`), without
access control. Every template includes the settings, so the app's checks
(`prowes.validate`) run first, before those of the library and whichever
template Helm renders first.
*/}}
{{- define "prowes.settings" -}}
{{- include "prowes.validate" . -}}
valuesKey: prowes
maxContentLength: 10485760
requireAudience: false
mongodb:
  databases: [runStore]
config: {}
{{- end -}}

{{/*
Checks of the values and the release specific to this app; renders nothing.
Included by the settings (`prowes.settings`), so in every template, before
the library's checks (`foca-common.validate`); the checks of removed values
come first, so that they are reported instead of their consequences (e.g., a
missing Secret for `mongodb.secret`).
*/}}
{{- define "prowes.validate" -}}
{{- include "prowes.checkLegacyValues" . -}}
{{- include "prowes.checkStorage" . -}}
{{- include "prowes.checkExtraEnv" . -}}
{{- include "prowes.checkFlower" . -}}
{{- end -}}

{{/*
Fails on values of the legacy chart (version 2, before the library chart),
which the chart would otherwise ignore; renders nothing.
*/}}
{{- define "prowes.checkLegacyValues" -}}
{{- $hint := "(see docs/deployment.md, From the legacy chart)" -}}
{{- $removed := dict
  "applicationDomain" "set hostName to the full public host name of the app instead (it was <prowes.appName>.<applicationDomain>)"
  "clusterType" "an OpenShift Route is created if the cluster serves route.openshift.io/v1 and neither an Ingress nor an HTTPRoute is enabled, and security contexts follow the cluster"
  "storageAccessMode" "set prowes.storage.accessModes, mongodb.persistence.accessModes and broker.persistence.accessModes instead"
  "celeryWorker" "the Celery worker is configured in worker (its image is that of prowes)"
  "rabbitmq" "the broker is configured in broker, with its credentials in broker.existingSecret"
-}}
{{- range $key := keys $removed | sortAlpha -}}
{{- if hasKey $.Values $key -}}
{{- fail (printf "%s was removed with the chart on foca-common: %s %s" $key (get $removed $key) $hint) -}}
{{- end -}}
{{- end -}}
{{- range $key := list "prowes" "mongodb" "flower" -}}
{{- $values := index $.Values $key -}}
{{- if and (kindIs "map" $values) (hasKey $values "appName") -}}
{{- fail (printf "%s.appName was removed with the chart on foca-common: resource names are derived from the release name; set fullnameOverride to choose them at installation %s" $key $hint) -}}
{{- end -}}
{{- end -}}
{{- if and (kindIs "map" .Values.prowes) (hasKey .Values.prowes "initResources") -}}
{{- fail (printf "prowes.initResources was removed with the chart on foca-common: the pods have no init containers, as fsGroup (prowes.podSecurityContext) makes the storage of the runs writable %s" $hint) -}}
{{- end -}}
{{- if and (kindIs "map" .Values.mongodb) (hasKey .Values.mongodb "secret") -}}
{{- fail (printf "mongodb.secret was removed with the chart on foca-common: the chart creates no Secrets; create one with the MongoDB credentials and set mongodb.auth.existingSecret %s" $hint) -}}
{{- end -}}
{{- if and (kindIs "map" .Values.mongodb) (hasKey .Values.mongodb "volumeSize") -}}
{{- fail (printf "mongodb.volumeSize was removed with the chart on foca-common: set mongodb.persistence.size instead %s" $hint) -}}
{{- end -}}
{{- if and (kindIs "map" .Values.flower) (hasKey .Values.flower "basicAuth") -}}
{{- fail (printf "flower.basicAuth was removed with the chart on foca-common: create a Secret with the key basic-auth (<user>:<password>) and set flower.existingSecret %s" $hint) -}}
{{- end -}}
{{- end -}}

{{/*
Name of the PersistentVolumeClaim with the storage of the runs
(`prowes.storage`), if enabled: `existingClaim`, or the chart's own
(`<app name>-data`); empty if disabled.
*/}}
{{- define "prowes.storageClaim" -}}
{{- include "foca-common.claimName" (dict
  "persistence" .Values.prowes.storage
  "name" (include "prowes.storageFullname" .)
  "enabledByDefault" true
) -}}
{{- end -}}

{{/*
Name of the chart's own PersistentVolumeClaim with the storage of the runs;
truncated like the library's names of the other components (e.g.,
`foca-common.workerFullname`).
*/}}
{{- define "prowes.storageFullname" -}}
{{- printf "%s-data" (include "foca-common.fullname" . | trunc 58 | trimSuffix "-") -}}
{{- end -}}

{{/*
"true" if the app's pods may share the storage of the runs across nodes
(`ReadWriteMany` in `prowes.storage.accessModes`), or keep it in an
`emptyDir` of their own (storage disabled); empty otherwise.
*/}}
{{- define "prowes.storageShared" -}}
{{- $storage := .Values.prowes.storage | default dict -}}
{{- if or (not (include "prowes.storageClaim" .)) (has "ReadWriteMany" ($storage.accessModes | default (list "ReadWriteOnce"))) -}}
true
{{- end -}}
{{- end -}}

{{/*
Fails unless `prowes.storage` is a map, and on several replicas of the app
with a claim that is not `ReadWriteMany` (the pods would need it on several
nodes, or not see each other's runs), and on a name of the claim that
collides with the app's; renders nothing.
*/}}
{{- define "prowes.checkStorage" -}}
{{- if not (kindIs "map" (dig "storage" nil (.Values.prowes | default dict))) -}}
{{- fail "prowes.storage must be a map with the settings of the storage of the runs (enabled, size, accessModes, storageClass, existingClaim, resourcePolicy)" -}}
{{- end -}}
{{- $replicas := .Values.prowes.replicas -}}
{{- if and (not (kindIs "invalid" $replicas)) (gt (int $replicas) 1) (include "prowes.storageClaim" .) (not (include "prowes.storageShared" .)) -}}
{{- fail "prowes.replicas must be 1 unless prowes.storage.accessModes includes ReadWriteMany: the app's pods write the runs to one volume, which they may need on different nodes" -}}
{{- end -}}
{{- if eq (include "foca-common.fullname" .) (include "prowes.storageFullname" .) -}}
{{- fail (printf "the app and the storage of the runs would both be named %s: choose a fullnameOverride that does not end in -data" (include "foca-common.fullname" .)) -}}
{{- end -}}
{{- end -}}

{{/*
Environment variables that the chart sets in the app's and the worker's
containers, which `prowes.extraEnv` must not set again, and `MONGO_DBNAME`,
which would move the app to a database that the chart does not provision.
*/}}
{{- define "prowes.reservedEnv" -}}
{{- list "FOCA_CONFIG_OVERLAY" "GUNICORN_CMD_ARGS" "MONGO_USERNAME" "MONGO_PASSWORD" "CELERY_BROKER_URL" "CELERY_RESULT_BACKEND" "MONGO_DBNAME" | toJson -}}
{{- end -}}

{{/*
Fails unless `prowes.extraEnv` is a list of environment variables (each with
a `name`, set once) that the chart does not set itself; renders nothing.
*/}}
{{- define "prowes.checkExtraEnv" -}}
{{- $env := dig "extraEnv" nil (.Values.prowes | default dict) -}}
{{- if not (kindIs "invalid" $env) -}}
{{- if not (kindIs "slice" $env) -}}
{{- fail "prowes.extraEnv must be a list of environment variables (name and value or valueFrom)" -}}
{{- end -}}
{{- $reserved := include "prowes.reservedEnv" . | fromJsonArray -}}
{{- $names := list -}}
{{- range $var := $env -}}
{{- $name := and (kindIs "map" $var) (get $var "name") -}}
{{- if not (and (kindIs "string" $name) $name) -}}
{{- fail "prowes.extraEnv must be a list of environment variables, each with a name" -}}
{{- end -}}
{{- if has $name $reserved -}}
{{- fail (printf "prowes.extraEnv must not set %s, which the chart sets (%s)" $name (join ", " $reserved)) -}}
{{- end -}}
{{- if has $name $names -}}
{{- fail (printf "prowes.extraEnv sets %s more than once" $name) -}}
{{- end -}}
{{- $names = append $names $name -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
A Deployment rendered by the library (`manifest`, YAML; empty if disabled),
with the environment variables of `prowes.extraEnv` added to its containers
and, for the app (`storage`), the storage of the runs mounted at `/data`
(`custom.post_runs.storage_path` of the base config): the claim of
`prowes.storageClaim`, or an `emptyDir` of `prowes.storage.size` if storage
is disabled. Pods of an app whose claim is not `ReadWriteMany` are replaced
instead of rolled (`Recreate`), so that the old pod has released the claim
before the new one, maybe on another node, mounts it. Call with a dict of the
root context (`root`), the manifest and `storage` (true for the app).
*/}}
{{- define "prowes.withPodExtras" -}}
{{- $root := .root -}}
{{- $env := dig "extraEnv" nil ($root.Values.prowes | default dict) | default list -}}
{{- if and (trim .manifest) (or $env .storage) -}}
{{- $manifest := .manifest | fromYaml -}}
{{- if hasKey $manifest "Error" -}}
{{- fail (printf "prowes: could not add prowes.extraEnv or the storage of the runs to a Deployment of the library chart: %s" $manifest.Error) -}}
{{- end -}}
{{- $pod := $manifest.spec.template.spec -}}
{{- range $container := $pod.containers -}}
{{- $_ := set $container "env" (concat ($container.env | default list) $env) -}}
{{- if $.storage -}}
{{- $_ := set $container "volumeMounts" (append ($container.volumeMounts | default list) (dict "mountPath" "/data" "name" "data")) -}}
{{- end -}}
{{- end -}}
{{- if .storage -}}
{{- $claim := include "prowes.storageClaim" $root -}}
{{- $volume := dict "name" "data" -}}
{{- if $claim -}}
{{- $_ := set $volume "persistentVolumeClaim" (dict "claimName" $claim) -}}
{{- else -}}
{{- $_ := set $volume "emptyDir" (include "foca-common.emptyDir" ($root.Values.prowes.storage.size | default nil) | fromYaml).emptyDir -}}
{{- end -}}
{{- $_ := set $pod "volumes" (append ($pod.volumes | default list) $volume) -}}
{{- if not (include "prowes.storageShared" $root) -}}
{{- $_ := set $manifest.spec "strategy" (dict "type" "Recreate") -}}
{{- end -}}
{{- end -}}
{{- toYaml $manifest -}}
{{- else -}}
{{- .manifest -}}
{{- end -}}
{{- end -}}

{{/*
Name of Flower's resources; truncated like the library's names of the other
components (e.g., `foca-common.workerFullname`).
*/}}
{{- define "prowes.flowerFullname" -}}
{{- printf "%s-flower" (include "foca-common.fullname" . | trunc 56 | trimSuffix "-") -}}
{{- end -}}

{{/*
Fails unless Flower, if enabled, has its credentials (`flower.existingSecret`)
and the broker's (`broker.existingSecret`), and a name of its own; renders
nothing.
*/}}
{{- define "prowes.checkFlower" -}}
{{- if dig "enabled" false (.Values.flower | default dict) -}}
{{- if not .Values.flower.existingSecret -}}
{{- fail "flower.existingSecret must be set if flower.enabled: a Secret with the key basic-auth (<user>:<password>), as Flower shows the arguments of tasks" -}}
{{- end -}}
{{- if not (dig "existingSecret" "" (.Values.broker | default dict)) -}}
{{- fail "broker.existingSecret must be set if flower.enabled: Flower connects to the broker with its URL (broker-url)" -}}
{{- end -}}
{{- if eq (include "foca-common.fullname" .) (include "prowes.flowerFullname" .) -}}
{{- fail (printf "the app and Flower resources would both be named %s: choose a fullnameOverride that does not end in -flower" (include "foca-common.fullname" .)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Fails on upgrades of releases of the legacy chart (version 2, before the
library chart), which would delete their volumes with the MongoDB data, as
their names changed; renders nothing. Releases are recognized by their
Deployments and claims of volumes (`prowes.failIfLegacy`; the legacy chart
does not keep its claims on uninstallation). Included once (in the app's
ConfigMap template), as it looks up resources in the cluster. `lookup` finds
nothing in `helm template` and client-side dry runs.
*/}}
{{- define "prowes.checkUpgrade" -}}
{{- if .Release.IsUpgrade -}}
{{- range $old := list (list "apps/v1" "Deployment") (list "v1" "PersistentVolumeClaim") -}}
{{- $resources := lookup (index $old 0) (index $old 1) $.Release.Namespace "" | default dict -}}
{{- range $resource := (get $resources "items" | default list) -}}
{{- include "prowes.failIfLegacy" (dict "root" $ "kind" (index $old 1) "resource" $resource) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Fails if a resource (looked up in the cluster) belongs to this release, but
was created by the legacy chart (version 2): without the label
`app.kubernetes.io/instance`. Call with a dict of the root context (`root`),
the resource's kind (`kind`) and the resource (`resource`).
*/}}
{{- define "prowes.failIfLegacy" -}}
{{- $owner := dig "metadata" "annotations" "meta.helm.sh/release-name" "" .resource -}}
{{- $labels := dig "metadata" "labels" dict .resource -}}
{{- if and (eq $owner .root.Release.Name) (not (hasKey $labels "app.kubernetes.io/instance")) -}}
{{- fail (printf "release %s was installed with the legacy chart (version 2), and upgrading it would delete its volume with the MongoDB data (%s %s): uninstall it and install the current chart instead, keeping the data as described in docs/deployment.md (From the legacy chart)" .root.Release.Name .kind (dig "metadata" "name" "" .resource)) -}}
{{- end -}}
{{- end -}}
