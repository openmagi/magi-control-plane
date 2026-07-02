{{- /*
magi-cp helm helpers.

replicaGuard: refuse to render a multi-replica deployment that still points at
the default SQLite DSN. Two or more pods cannot share one SQLite file on a
single RWO PVC without corruption, and the in-process asyncio locks +
SELECT ... FOR UPDATE (Postgres only) give no cross-replica protection there.
Operators who want HA MUST set postgres.dsn.
*/ -}}
{{- define "magi-cp.replicaGuard" -}}
{{- if and (gt (int .Values.replicaCount) 1) (not .Values.postgres.dsn) -}}
{{- fail "magi-cp: replicaCount > 1 requires postgres.dsn to be set. SQLite cannot be shared across replicas; set postgres.dsn for HA or keep replicaCount: 1." -}}
{{- end -}}
{{- end -}}
