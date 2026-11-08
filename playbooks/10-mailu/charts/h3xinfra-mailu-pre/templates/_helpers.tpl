{{/*
Expand the name of the chart.
*/}}
{{- define "h3xinfra-mailu-pre.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "h3xinfra-mailu-pre.fullname" -}}
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
{{- define "h3xinfra-mailu-pre.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Hostnames that the web HTTPRoutes attach to the shared Gateway, as a JSON
list: gatewayRoute.hostnames, or certificate.dnsNames when it is empty.
When the OIDC gate is on, the OIDC host must be in the list. If it is not,
the OIDC callback does not go to the gated route. The OIDC host is
gatewayRoute.oidcHostname, or certificate.commonName when it is empty. This is
the same host that securitypolicy.yaml uses for the redirect URL.
*/}}
{{- define "h3xinfra-mailu-pre.webHostnames" -}}
{{- $hosts := .Values.gatewayRoute.hostnames | default .Values.certificate.dnsNames | default list -}}
{{- $oidcHost := .Values.gatewayRoute.oidcHostname | default .Values.certificate.commonName -}}
{{- if and .Values.gatewayRoute.oidc.enabled $oidcHost (not (has $oidcHost $hosts)) -}}
{{- fail (printf "OIDC callback host %q (gatewayRoute.oidcHostname or certificate.commonName) must be one of the web route hostnames %v" $oidcHost $hosts) -}}
{{- end -}}
{{- toJson $hosts -}}
{{- end }}

{{/*
Common labels
*/}}
{{- define "h3xinfra-mailu-pre.labels" -}}
helm.sh/chart: {{ include "h3xinfra-mailu-pre.chart" . }}
{{ include "h3xinfra-mailu-pre.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "h3xinfra-mailu-pre.selectorLabels" -}}
app.kubernetes.io/name: {{ include "h3xinfra-mailu-pre.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "h3xinfra-mailu-pre.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "h3xinfra-mailu-pre.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Pod selector of the Mailu front Deployment (upstream chart labels).
*/}}
{{- define "h3xinfra-mailu-pre.frontSelector" -}}
app.kubernetes.io/component: front
app.kubernetes.io/instance: {{ .Values.mailuInstanceName | default "mailu" }}
app.kubernetes.io/name: mailu
{{- end }}

{{/*
Source CIDRs for the mail client ports (IMAPS, POP3S, ManageSieve), as a
YAML list. The LoadBalancer loadBalancerSourceRanges (external-svc.yaml) and
the Cilium policy fromCIDRSet rules (network-policy.yaml) both use this list.
*/}}
{{- define "h3xinfra-mailu-pre.clientSourceRanges" -}}
- 10.0.0.0/8
- 172.16.0.0/12
- 192.168.0.0/16
- fc00::/7
{{- range .Values.networkPolicy.allowedIPv6Networks }}
- {{ . | quote }}
{{- end }}
{{- end }}
