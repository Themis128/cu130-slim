#!/bin/sh
# Deploy the EspoCRM metrics exporter on the omv k3s monitoring stack.
# Run on omv:  ./apply.sh   (requires kubectl access)
#
# Provisions, idempotently:
#   - a dedicated read-only MariaDB user (metrics_reader, SELECT only) when
#     the espocrm-secrets/mariadb-root-password key is available — the
#     exporter never needs the app's write-capable credentials
#   - espocrm-exporter-db secret (ns: espocrm) — mysql-user/mysql-password +
#     metrics-token used for Bearer auth on /metrics
#   - espocrm-exporter-metrics-token secret (ns: monitoring) — same token for
#     the ServiceMonitor's authorization.credentials
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

rand() { LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c "${1:-32}"; }

# --- DB credentials -------------------------------------------------------
MYSQL_USER="espocrm"
MYSQL_PASS="$(kubectl -n espocrm get secret espocrm-secrets \
  -o jsonpath='{.data.mariadb-password}' | base64 -d)"

ROOT_PW="$(kubectl -n espocrm get secret espocrm-secrets \
  -o jsonpath='{.data.mariadb-root-password}' 2>/dev/null | base64 -d)"
MEXPASS="$(rand 32)"
MYPOD="$(kubectl -n espocrm get pods -o name | grep -m1 mariadb || true)"

if [ -n "$ROOT_PW" ] && [ -n "$MYPOD" ]; then
  if kubectl -n espocrm exec -i "$MYPOD" -- mariadb -uroot -p"$ROOT_PW" -e \
    "CREATE USER IF NOT EXISTS 'metrics_reader'@'%' IDENTIFIED BY '$MEXPASS';
     ALTER USER 'metrics_reader'@'%' IDENTIFIED BY '$MEXPASS';
     GRANT SELECT ON espocrm.* TO 'metrics_reader'@'%';
     FLUSH PRIVILEGES;"; then
    MYSQL_USER="metrics_reader"
    MYSQL_PASS="$MEXPASS"
    echo "espocrm-exporter: using read-only user metrics_reader"
  else
    echo "WARN: could not create metrics_reader — falling back to app credentials" >&2
  fi
else
  echo "WARN: no mariadb-root-password in espocrm-secrets — using app credentials" >&2
fi

TOKEN="$(rand 48)"
kubectl -n espocrm create secret generic espocrm-exporter-db \
  --from-literal=mysql-user="$MYSQL_USER" \
  --from-literal=mysql-password="$MYSQL_PASS" \
  --from-literal=metrics-token="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring create secret generic espocrm-exporter-metrics-token \
  --from-literal=token="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

# --- Exporter + scrape ----------------------------------------------------
kubectl create configmap espocrm-exporter -n espocrm \
  --from-file=exporter.py="$DIR/exporter.py" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$DIR/deployment.yaml" -f "$DIR/service.yaml" -f "$DIR/servicemonitor.yaml"
kubectl rollout status deployment/espocrm-exporter -n espocrm --timeout=120s

# --- Dashboard ------------------------------------------------------------
kubectl create configmap grafana-dashboard-espocrm-vitals -n monitoring \
  --from-file=espocrm-vitals.json="$DIR/dashboard-espocrm-vitals.json" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n monitoring label configmap grafana-dashboard-espocrm-vitals \
  grafana_dashboard=1 --overwrite
