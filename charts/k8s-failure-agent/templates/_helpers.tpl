{{/*
Shared snippets. Files starting with "_" produce no resources: they only define helpers used by the templates.
*/}}

{{/* The chart's short name. */}}
{{- define "fa.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
The release's full name, used as a prefix for every resource: "<release>-k8s-failure-agent", or just the release
name when it already contains the chart name. Kubernetes names are limited to 63 characters.
*/}}
{{- define "fa.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Labels on every resource: who made it, which release, which version (the standard Kubernetes labels). */}}
{{- define "fa.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "fa.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Labels that select one component's pods. Call with a dict: (dict "root" $ "component" "collector").
Selectors must never change after install, so they hold only stable values (no version).
*/}}
{{- define "fa.selectorLabels" -}}
app.kubernetes.io/name: {{ include "fa.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
The image reference: repository:tag, the tag defaulting to the chart's appVersion.
toString: `--set image.tag=4764173` (an all-digit commit id) arrives as a number, and printf "%s" would print
"%!s(int64=4764173)" into the image name (seen on 23 Nov: the migration hook could not start and the upgrade failed).
*/}}
{{- define "fa.image" -}}
{{- printf "%s:%s" (toString .Values.image.repository) (toString (default .Chart.AppVersion .Values.image.tag)) }}
{{- end }}

{{/* The database's name inside the cluster: its Service, StatefulSet and Secret share it. */}}
{{- define "fa.dbName" -}}
{{- printf "%s-db" (include "fa.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Environment for any container that talks to the database. The password comes from the Secret, and Kubernetes
expands $(DB_PASSWORD) inside the pod, so the password never appears in the chart, the values or the release.
*/}}
{{- define "fa.databaseEnv" -}}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef: {name: {{ include "fa.dbName" . }}, key: password}
- name: DATABASE_URL
  value: "postgresql://postgres:$(DB_PASSWORD)@{{ include "fa.dbName" . }}:5432/{{ .Values.database.name }}"
{{- end }}

{{/* Container security settings shared by the application's containers (image user 10001). */}}
{{- define "fa.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities: {drop: [ALL]}
seccompProfile: {type: RuntimeDefault}
{{- end }}

{{/* A component's name: "<fullname>-<component>". Call with (dict "root" $ "component" "collector"). */}}
{{- define "fa.componentName" -}}
{{- printf "%s-%s" (include "fa.fullname" .root) .component | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Pod-level security settings shared by the application's pods. */}}
{{- define "fa.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile: {type: RuntimeDefault}
{{- end }}

{{/* Environment shared by the application's containers: settings and the database connection. */}}
{{- define "fa.appEnv" -}}
envFrom:
  - configMapRef: {name: {{ include "fa.fullname" . }}-settings}
env:
  {{- include "fa.databaseEnv" . | nindent 2 }}
{{- end }}

{{/* "--namespace a --namespace b ..." for the components that take namespaces as arguments. */}}
{{- define "fa.namespaceArgs" -}}
{{- range .Values.settings.namespaces }}
- --namespace
- {{ . | quote }}
{{- end }}
{{- end }}
