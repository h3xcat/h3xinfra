{{/*
Expand the name of the chart.
*/}}
{{- define "h3xinfra-keycloak-pre.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "h3xinfra-keycloak-pre.fullname" -}}
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

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "h3xinfra-keycloak-pre.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "h3xinfra-keycloak-pre.labels" -}}
helm.sh/chart: {{ include "h3xinfra-keycloak-pre.chart" . }}
{{ include "h3xinfra-keycloak-pre.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "h3xinfra-keycloak-pre.selectorLabels" -}}
app.kubernetes.io/name: {{ include "h3xinfra-keycloak-pre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Name of the bootstrap secret (referenced by the main Keycloak chart).
*/}}
{{- define "h3xinfra-keycloak-pre.bootstrapSecretName" -}}
{{- printf "%s-bootstrap" (include "h3xinfra-keycloak-pre.fullname" .) }}
{{- end }}

{{/*
Name of the Postgres service / StatefulSet (used as DB hostname by Keycloak).
*/}}
{{- define "h3xinfra-keycloak-pre.postgresName" -}}
{{- printf "%s-postgres" (include "h3xinfra-keycloak-pre.fullname" .) }}
{{- end }}
