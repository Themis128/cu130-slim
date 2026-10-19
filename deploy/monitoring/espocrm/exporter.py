#!/usr/bin/env python3
"""EspoCRM -> Prometheus exporter.

EspoCRM (espocrm/espocrm:9, PHP + MariaDB) exposes no /metrics surface, so
this collector queries the MariaDB schema directly for the entity and job-queue
vitals a CRM dashboard needs. Runs inside the `espocrm` namespace where the
ClusterIP `espocrm-mariadb` service is reachable; the DB password comes from
the existing `espocrm-secrets` secret (key: mariadb-password) via env.

Metrics (all rebuilt per scrape; vanished label sets do not go stale):
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
  MYSQL_USER        (default espocrm)
  MYSQL_PASSWORD    (required)
  MYSQL_DATABASE    (default espocrm)
  LISTEN_PORT       (default 8080)
"""

import logging
import os
import sys
import time

import pymysql
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest
from prometheus_client.exposition import make_wsgi_app

LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))

REG = CollectorRegistry()
UP = Gauge("espocrm_up", "1 when the EspoCRM scrape succeeded", registry=REG)
SCRAPE_DURATION = Gauge(
    "espocrm_scrape_duration_seconds", "MariaDB query time per scrape", registry=REG
)
LEADS = Gauge("espocrm_leads_total", "Leads by status", ["status"], registry=REG)
LEADS_SOURCE = Gauge(
    "espocrm_leads_by_source", "Leads by lead source", ["source"], registry=REG
)
LEADS_24H = Gauge(
    "espocrm_leads_created_24h", "Leads created in the last 24h", registry=REG
)
LIST_MEMBERS = Gauge(
    "espocrm_target_list_members",
    "Leads enrolled per target list",
    ["list"],
    registry=REG,
)
CAMPAIGNS = Gauge(
    "espocrm_campaigns_total", "Campaigns by status", ["status"], registry=REG
)
NOTES_24H = Gauge(
    "espocrm_notes_24h", "Stream notes in the last 24h", ["type"], registry=REG
)
JOBS = Gauge(
    "espocrm_jobs", "Scheduled-job queue rows by status", ["status"], registry=REG
)
JOB_FAIL_24H = Gauge(
    "espocrm_job_failures_24h", "Jobs that errored in the last 24h", registry=REG
)
EMAILS_24H = Gauge(
    "espocrm_emails_24h", "Email rows stored in the last 24h", registry=REG
)
USERS = Gauge("espocrm_users_total", "Active CRM users", registry=REG)
DB_SIZE = Gauge(
    "espocrm_db_size_bytes", "espocrm schema size (data + index)", registry=REG
)

QUERIES = [
    (
        "SELECT status, COUNT(*) FROM lead WHERE deleted = 0 GROUP BY status",
        lambda rows: [LEADS.labels(status=s or "unknown").set(n) for s, n in rows],
        [LEADS],
    ),
    (
        "SELECT source, COUNT(*) FROM lead WHERE deleted = 0 GROUP BY source",
        lambda rows: [LEADS_SOURCE.labels(source=s or "none").set(n) for s, n in rows],
        [LEADS_SOURCE],
    ),
    (
        "SELECT COUNT(*) FROM lead WHERE deleted = 0 "
        "AND created_at > NOW() - INTERVAL 1 DAY",
        lambda rows: LEADS_24H.set(int(rows[0][0])),
        [],
    ),
    (
        "SELECT tl.name, COUNT(*) FROM lead_target_list lt "
        "JOIN target_list tl ON tl.id = lt.target_list_id "
        "WHERE lt.deleted = 0 AND tl.deleted = 0 GROUP BY tl.name",
        lambda rows: [LIST_MEMBERS.labels(list=n).set(c) for n, c in rows],
        [LIST_MEMBERS],
    ),
    (
        "SELECT status, COUNT(*) FROM campaign WHERE deleted = 0 GROUP BY status",
        lambda rows: [CAMPAIGNS.labels(status=s or "unknown").set(n) for s, n in rows],
        [CAMPAIGNS],
    ),
    (
        "SELECT type, COUNT(*) FROM note WHERE deleted = 0 "
        "AND created_at > NOW() - INTERVAL 1 DAY GROUP BY type",
        lambda rows: [NOTES_24H.labels(type=t or "other").set(n) for t, n in rows],
        [NOTES_24H],
    ),
    (
        "SELECT status, COUNT(*) FROM job GROUP BY status",
        lambda rows: [JOBS.labels(status=s).set(n) for s, n in rows],
        [JOBS],
    ),
    (
        "SELECT COUNT(*) FROM job WHERE status = 'Error' "
        "AND execute_time > NOW() - INTERVAL 1 DAY",
        lambda rows: JOB_FAIL_24H.set(int(rows[0][0])),
        [],
    ),
    (
        "SELECT COUNT(*) FROM email WHERE deleted = 0 "
        "AND created_at > NOW() - INTERVAL 1 DAY",
        lambda rows: EMAILS_24H.set(int(rows[0][0])),
        [],
    ),
    (
        "SELECT COUNT(*) FROM user WHERE deleted = 0 AND is_active = 1",
        lambda rows: USERS.set(int(rows[0][0])),
        [],
    ),
    (
        "SELECT SUM(data_length + index_length) FROM information_schema.tables "
        "WHERE table_schema = %s",
        lambda rows: DB_SIZE.set(float(rows[0][0] or 0)),
        [],
    ),
]


def scrape() -> None:
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
            for sql, apply, gauges in QUERIES:
                for g in gauges:
                    g.clear()
                cur.execute(sql, (os.environ.get("MYSQL_DATABASE", "espocrm"),)
                            if "%s" in sql else ())
                apply(cur.fetchall())
    finally:
        conn.close()
    UP.set(1)
    SCRAPE_DURATION.set(time.monotonic() - start)


def metrics_app(environ, start_response):
    if environ.get("PATH_INFO") == "/metrics":
        try:
            scrape()
        except Exception as exc:  # noqa: BLE001 — never 500 the scrape
            UP.set(0)
            logging.warning("espocrm scrape failed: %s", exc)
    return make_wsgi_app(REG)(environ, start_response)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if "MYSQL_PASSWORD" not in os.environ:
        sys.exit("MYSQL_PASSWORD is required (espocrm-secrets/mariadb-password)")
    from wsgiref.simple_server import make_server

    server = make_server("0.0.0.0", LISTEN_PORT, metrics_app)
    logging.info("espocrm exporter listening on :%d", LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
