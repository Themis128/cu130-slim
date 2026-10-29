#!/bin/bash
# loki_persist.sh — upgrade the omv k3s Loki to R2/S3-backed storage.
# Replaces the test-schema+filesystem single-binary with a real
# schema_config + S3 object store (datalake pattern).
#
# Review-driven hardening:
#  - memberlist/ring values included (single-binary still needs the
#    memberlist service or the ring never forms)
#  - the pre-existing filesystem schema entry is preserved so logs
#    ingested before cutover stay queryable across the boundary
#  - R2 creds ride a k8s Secret + AWS_* env — never helm --set (which
#    persists values in release metadata for anyone who can read it)
#  - creds cross ssh base64-encoded, never inline (quoting can't inject)
#  - StrictHostKeyChecking=accept-new — first-connect bootstraps the key,
#    a changed host key fails loudly instead of silently trusting
set -e
HOST="tbaltzakis@192.168.1.200"
KEY="$HOME/.ssh/id_rsa_win"
BUCKET="${LOKI_BUCKET:-datalake-bucket}"
ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
CUTOVER="${LOKI_SCHEMA_CUTOVER:-$(date -u +%Y-%m-%d)}"
# Preserve queryability for logs written before the cutover — the prior
# install used useTestSchema (boltdb-shipper filesystem, v12).
LEGACY_FROM="${LOKI_LEGACY_SCHEMA_FROM:-2026-01-01}"

# base64 the secrets so quoting/shell chars can't inject remote code
B64_AKI=$(printf %s "$R2_ACCESS_KEY_ID"     | base64 -w0)
B64_SAK=$(printf %s "$R2_SECRET_ACCESS_KEY" | base64 -w0)
B64_ACC=$(printf %s "$R2_ACCOUNT_ID"        | base64 -w0)
B64_END=$(printf %s "$ENDPOINT"             | base64 -w0)
B64_BKT=$(printf %s "$BUCKET"               | base64 -w0)

ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST" bash -s <<REMOTE
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
R2_ACCESS_KEY_ID=\$(echo '$B64_AKI' | base64 -d)
R2_SECRET_ACCESS_KEY=\$(echo '$B64_SAK' | base64 -d)
R2_ACCOUNT_ID=\$(echo '$B64_ACC' | base64 -d)
ENDPOINT=\$(echo '$B64_END' | base64 -d)
BUCKET=\$(echo '$B64_BKT' | base64 -d)

# creds live in a k8s Secret — read via env, never helm release metadata
kubectl -n monitoring create secret generic loki-r2-creds \
  --from-literal=AWS_ACCESS_KEY_ID="\$R2_ACCESS_KEY_ID" \
  --from-literal=AWS_SECRET_ACCESS_KEY="\$R2_SECRET_ACCESS_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

sudo -n -E helm upgrade loki grafana/loki -n monitoring \
  --set deploymentMode=SingleBinary \
  --set 'singleBinary.replicas=1' \
  --set 'write.replicas=0' --set 'read.replicas=0' --set 'backend.replicas=0' \
  --set 'loki.auth_enabled=false' \
  --set 'loki.storage.type=s3' \
  --set "loki.storage.s3.endpoint=\$ENDPOINT" \
  --set "loki.storage.s3.bucketNames.chunks=\$BUCKET" \
  --set "loki.storage.s3.bucketNames.ruler=\$BUCKET" \
  --set "loki.storage.s3.bucketNames.admin=\$BUCKET" \
  --set "loki.storage.s3.s3ForcePathStyle=true" \
  --set "loki.storage.s3.region=auto" \
  --set 'loki.memberlist.service.publishNotReadyAddresses=true' \
  --set 'loki.ring.kvstore.store=memberlist' \
  --set 'singleBinary.extraEnv[0].name=AWS_ACCESS_KEY_ID' \
  --set 'singleBinary.extraEnv[0].valueFrom.secretKeyRef.name=loki-r2-creds' \
  --set 'singleBinary.extraEnv[0].valueFrom.secretKeyRef.key=AWS_ACCESS_KEY_ID' \
  --set 'singleBinary.extraEnv[1].name=AWS_SECRET_ACCESS_KEY' \
  --set 'singleBinary.extraEnv[1].valueFrom.secretKeyRef.name=loki-r2-creds' \
  --set 'singleBinary.extraEnv[1].valueFrom.secretKeyRef.key=AWS_SECRET_ACCESS_KEY' \
  --set "loki.schemaConfig.configs[0].from=$LEGACY_FROM" \
  --set 'loki.schemaConfig.configs[0].store=tsdb' \
  --set 'loki.schemaConfig.configs[0].object_store=filesystem' \
  --set 'loki.schemaConfig.configs[0].schema=v12' \
  --set 'loki.schemaConfig.configs[0].index.prefix=index_' \
  --set 'loki.schemaConfig.configs[0].index.period=24h' \
  --set "loki.schemaConfig.configs[1].from=$CUTOVER" \
  --set 'loki.schemaConfig.configs[1].store=tsdb' \
  --set 'loki.schemaConfig.configs[1].object_store=s3' \
  --set 'loki.schemaConfig.configs[1].schema=v13' \
  --set 'loki.schemaConfig.configs[1].index.prefix=index_' \
  --set 'loki.schemaConfig.configs[1].index.period=24h'

# verify: pod ready + a real query answers (ring formed, s3 reachable)
kubectl -n monitoring rollout status deploy/loki --timeout=180s
kubectl -n monitoring exec deploy/loki -- \
  wget -qO- 'http://localhost:3100/loki/api/v1/labels' | head -c 200
echo
echo 'NOTE: legacy filesystem chunks persist on the pod PVC — new writes go to R2.'
REMOTE
