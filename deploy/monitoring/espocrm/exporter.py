#!/usr/bin/env python3
"""EspoCRM -> Prometheus exporter.

EspoCRM (espocrm/espocrm:9, PHP + MariaDB) exposes no /metrics surface, so
this collector queries the MariaDB schema directly for the entity and job-queue
vitals a CRM dashboard needs. Runs inside the `espocrm` namespace where the
ClusterIP `espocrm-mariadb` service is reachable.

Each /metrics request builds a FRESH CollectorRegistry: on any failure the
response contains only `espocrm_up 0` (+scrape duration) so dashboards never
display stale business numbers beside a failed scrape.

Access control: when METRICS_TOKEN is set, /metrics requires
`Authorization: Bearer <token>` (the ServiceMonitor carries the same token).

Metrics:
  espocrm_up                            1 when the scrape succeeded
  espocrm_scrape_duration_seconds       time spent querying MariaDB
  espocrm_leads_total{status}           leads by status
  espocrm_leads_by_source{source}       leads by lead source
  espocrm_leads_created_24h             leads created in the last 24h
  espocrm_target_list_members{list}     leads enrolled per target list
  espocrm_campaigns_total{status}       campaigns by status
  espocrm_notes_24h{type}               stream notes by type, last 24h
  espocrm_jobs{status}                  scheduled-job queue rows by status
  espocrm_job_failures_24h              jobs with status Error in last 24h
  espocrm_emails_24h                    email rows stored in last 24h
  espocrm_users_total                   active CRM users
  espocrm_db_size_bytes                 espocrm schema size (data+index)

Env:
  MYSQL_HOST        (default espocrm-mariadb)
  MYSQL_PORT        (default 3306)
  MYSQL_USER        (default espocrm — apply.sh provisions metrics_reader)
  MYSQL_PASSWORD    (required)
  MYSQL_DATABASE    (default espocrm)
  METRICS_TOKEN     (optional — require Bearer auth on /metrics when set)
  LISTEN_PORT       (default 8080)
"""

import logging
import os
import sys
import time

import pymysql
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
METRICS_TOKEN = os.environ.get("METRICS_TOKEN", "")


def collect(reg: CollectorRegistry) -> None:
    """Create every business gauge on `reg` and fill it from MariaDB."""
    up = Gauge("espocrm_up", "1 when the EspoCRM scrape succeeded", registry=reg)
    duration = Gauge(
        "espocrm_scrape_duration_seconds",
        "MariaDB query time per scrape",
        registry=reg,
    )
    leads = Gauge("espocrm_leads_total", "Leads by status", ["status"], registry=reg)
    leads_source = Gauge(
        "espocrm_leads_by_source", "Leads by lead source", ["source"], registry=reg
    )
    leads_24h = Gauge(
        "espocrm_leads_created_24h", "Leads created in the last 24h", registry=reg
    )
    list_members = Gauge(
        "espocrm_target_list_members",
        "Leads enrolled per target list",
        ["list"],
        registry=reg,
    )
    campaigns = Gauge(
        "espocrm_campaigns_total", "Campaigns by status", ["status"], registry=reg
    )
    notes_24h = Gauge(
        "espocrm_notes_24h", "Stream notes in the last 24h", ["type"], registry=reg
    )
    jobs = Gauge(
        "espocrm_jobs", "Scheduled-job queue rows by status", ["status"], registry=reg
    )
    job_fail_24h = Gauge(
        "espocrm_job_failures_24h", "Jobs that errored in the last 24h", registry=reg
    )
    emails_24h = Gauge(
        "espocrm_emails_24h", "Email rows stored in the last 24h", registry=reg
    )
    users = Gauge("espocrm_users_total", "Active CRM users", registry=reg)
    db_size = Gauge(
        "espocrm_db_size_bytes", "espocrm schema size (data + index)", registry=reg
    )

    queries = [
        (
            "SELECT status, COUNT(*) FROM lead WHERE deleted = 0 GROUP BY status",
            lambda rows: [leads.labels(status=s or "unknown").set(n) for s, n in rows],
        ),
        (
            "SELECT source, COUNT(*) FROM lead WHERE deleted = 0 GROUP BY source",
            lambda rows: [
                leads_source.labels(source=s or "none").set(n) for s, n in rows
            ],
        ),
        (
            "SELECT COUNT(*) FROM lead WHERE deleted = 0 "
            "AND created_at > NOW() - INTERVAL 1 DAY",
            lambda rows: leads_24h.set(int(rows[0][0])),
        ),
        (
            "SELECT tl.name, COUNT(*) FROM lead_target_list lt "
            "JOIN target_list tl ON tl.id = lt.target_list_id "
            "WHERE lt.deleted = 0 AND tl.deleted = 0 GROUP BY tl.name",
            lambda rows: [list_members.labels(list=n).set(c) for n, c in rows],
        ),
        (
            "SELECT status, COUNT(*) FROM campaign WHERE deleted = 0 GROUP BY status",
            lambda rows: [
                campaigns.labels(status=s or "unknown").set(n) for s, n in rows
            ],
        ),
        (
            "SELECT type, COUNT(*) FROM note WHERE deleted = 0 "
            "AND created_at > NOW() - INTERVAL 1 DAY GROUP BY type",
            lambda rows: [notes_24h.labels(type=t or "other").set(n) for t, n in rows],
        ),
        (
            "SELECT status, COUNT(*) FROM job GROUP BY status",
            lambda rows: [jobs.labels(status=s).set(n) for s, n in rows],
        ),
        (
            "SELECT COUNT(*) FROM job WHERE status = 'Error' "
            "AND execute_time > NOW() - INTERVAL 1 DAY",
            lambda rows: job_fail_24h.set(int(rows[0][0])),
        ),
        (
            "SELECT COUNT(*) FROM email WHERE deleted = 0 "
            "AND created_at > NOW() - INTERVAL 1 DAY",
            lambda rows: emails_24h.set(int(rows[0][0])),
        ),
        (
            "SELECT COUNT(*) FROM user WHERE deleted = 0 AND is_active = 1",
            lambda rows: users.set(int(rows[0][0])),
        ),
        (
            "SELECT SUM(data_length + index_length) FROM information_schema.tables "
            "WHERE table_schema = %s",
            lambda rows: db_size.set(float(rows[0][0] or 0)),
        ),
    ]

    start = time.monotonic()
    conn = pymysql.connect(
        host=os.environ.get("MYSQL_HOST", "espocrm-mariadb"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "espocrm"),
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ.get("MYSQL_DATABASE", "espocrm"),
        connect_timeout=5,
        read_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            for sql, apply in queries:
                cur.execute(
                    sql,
                    (os.environ.get("MYSQL_DATABASE", "espocrm"),)
                    if "%s" in sql
                    else (),
                )
                apply(cur.fetchall())
    finally:
        conn.close()
    up.set(1)
    duration.set(time.monotonic() - start)


def metrics_app(environ, start_response):
    # /healthz is unauthenticated on purpose — kubelet probes cannot send
    # auth headers; it only proves the process serves, no CRM data.
    if environ.get("PATH_INFO") == "/healthz":
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    if environ.get("PATH_INFO") != "/metrics":
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]

    if METRICS_TOKEN:
        auth = environ.get("HTTP_AUTHORIZATION", "")
        if auth != f"Bearer {METRICS_TOKEN}":
            start_response("401 Unauthorized", [("Content-Type", "text/plain")])
            return [b"missing or invalid bearer token"]

    reg = CollectorRegistry()
    try:
        collect(reg)
    except Exception as exc:  # noqa: BLE001 — never 500 the scrape
        # Fresh registry: only espocrm_up=0, so no stale business metrics
        # leak into dashboards beside a failed scrape.
        logging.warning("espocrm scrape failed: %s", exc)
        reg = CollectorRegistry()
        Gauge("espocrm_up", "1 when the EspoCRM scrape succeeded", registry=reg).set(0)

    body = generate_latest(reg)
    start_response(
        "200 OK",
        [("Content-Type", CONTENT_TYPE_LATEST), ("Content-Length", str(len(body)))],
    )
    return [body]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if "MYSQL_PASSWORD" not in os.environ:
        sys.exit("MYSQL_PASSWORD is required (espocrm-exporter-db/mysql-password)")
    from wsgiref.simple_server import make_server

    server = make_server("0.0.0.0", LISTEN_PORT, metrics_app)
    logging.info("espocrm exporter listening on :%d", LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
