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

{{/* The image reference: repository:tag, the tag defaulting to the chart's appVersion. */}}
{{- define "fa.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{/* The database's name inside the cluster: its Service, StatefulSet and Secret share it. */}}
{{- define "fa.dbName" -}}
{{- printf "%s-db" (include "fa.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
