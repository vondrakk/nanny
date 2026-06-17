{{/* Standard name/label helpers. */}}
{{- define "nanny.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "nanny.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "nanny.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "nanny.labels" -}}
helm.sh/chart: {{ include "nanny.chart" . }}
{{ include "nanny.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: nanny
{{- end -}}

{{- define "nanny.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nanny.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "nanny.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "nanny.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* CNPG cluster name + the app secret it generates ("<cluster>-app"). */}}
{{- define "nanny.dbCluster" -}}
{{- printf "%s-db" (include "nanny.fullname" .) -}}
{{- end -}}

{{- define "nanny.dbSecretName" -}}
{{- if .Values.databaseUrlSecret.name -}}
{{- .Values.databaseUrlSecret.name -}}
{{- else -}}
{{- printf "%s-app" (include "nanny.dbCluster" .) -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret nanny's env is loaded from (templated or pre-existing). */}}
{{- define "nanny.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "nanny.fullname" .) -}}
{{- end -}}
{{- end -}}
