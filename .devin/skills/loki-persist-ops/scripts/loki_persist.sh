#!/bin/bash
# loki_persist.sh — upgrade the omv k3s Loki to R2/S3-backed storage.
# Replaces the test-schema+filesystem single-binary with a real
# schema_config + S3 object store (datalake pattern).
set -e
HOST="tbaltzakis@192.168.1.200"
KEY="$HOME/.ssh/id_rsa_win"
BUCKET="${LOKI_BUCKET:-datalake-bucket}"
ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

# helm upgrade — same release, R2 backend + real schema
# env vars go over the wire — heredoc would expand them remotely (empty)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" bash -s <<REMOTE
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
export R2_ACCOUNT_ID='$R2_ACCOUNT_ID'
export R2_ACCESS_KEY_ID='$R2_ACCESS_KEY_ID'
export R2_SECRET_ACCESS_KEY='$R2_SECRET_ACCESS_KEY'
export ENDPOINT='$ENDPOINT'
export BUCKET='$BUCKET' 
sudo -n -E helm upgrade loki grafana/loki -n monitoring \
  --set deploymentMode=SingleBinary \
  --set 'singleBinary.replicas=1' \
  --set 'write.replicas=0' --set 'read.replicas=0' --set 'backend.replicas=0' \
  --set 'loki.auth_enabled=false' \
  --set 'loki.storage.type=s3' \
  --set "loki.storage.s3.endpoint=$ENDPOINT" \
  --set "loki.storage.s3.secretAccessKey=$R2_SECRET_ACCESS_KEY" \
  --set "loki.storage.s3.accessKeyId=$R2_ACCESS_KEY_ID" \
  --set "loki.storage.s3.s3ForcePathStyle=true" \
  --set "loki.storage.bucketNames.chunks=$BUCKET" \
  --set "loki.storage.bucketNames.ruler=$BUCKET" \
  --set "loki.storage.bucketNames.admin=$BUCKET" \
  --set "loki.schemaConfig.configs[0].from=2026-10-28" \
  --set "loki.schemaConfig.configs[0].store=tsdb" \
  --set "loki.schemaConfig.configs[0].object_store=s3" \
  --set "loki.schemaConfig.configs[0].schema=v13" \
  --set "loki.schemaConfig.configs[0].index.prefix=index_" \
  --set "loki.schemaConfig.configs[0].index.period=24h" \
  --set 'minio.enabled=false' --set 'gateway.enabled=false'
REMOTE
echo "upgraded — verify: ssh omv kubectl -n monitoring get pods | grep loki"
