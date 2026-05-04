{{/*
Expand the name of the chart.
*/}}
{{- define "h3xinfra-gateway-pre.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "h3xinfra-gateway-pre.fullname" -}}
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
{{- define "h3xinfra-gateway-pre.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "h3xinfra-gateway-pre.labels" -}}
helm.sh/chart: {{ include "h3xinfra-gateway-pre.chart" . }}
{{ include "h3xinfra-gateway-pre.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "h3xinfra-gateway-pre.selectorLabels" -}}
app.kubernetes.io/name: {{ include "h3xinfra-gateway-pre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Name of the wildcard certificate Secret produced by cert-manager.
*/}}
{{- define "h3xinfra-gateway-pre.wildcardSecretName" -}}
{{- printf "%s-wildcard-certificate" (include "h3xinfra-gateway-pre.fullname" .) }}
{{- end }}

{{/*
Name of the shared Gateway resource.
*/}}
{{- define "h3xinfra-gateway-pre.gatewayName" -}}
{{- printf "%s-shared" (include "h3xinfra-gateway-pre.fullname" .) }}
{{- end }}
