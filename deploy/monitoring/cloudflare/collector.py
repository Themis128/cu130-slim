#!/usr/bin/env python3
"""Cloudflare GraphQL Analytics -> Prometheus exporter (free-plan datasets).

The lablabs/cloudflare-exporter builds its zone query around
``httpRequests1mGroups`` which is a paid-plan dataset; on Free zones the whole
zone query fails with an authz error and no zone metrics are emitted. This
collector only queries datasets available on the Free plan:

Zone scope:
  - httpRequestsAdaptiveGroups   (counts, status, cache, host, latency quantiles)
  - firewallEventsAdaptiveGroups (WAF/security actions)
  - httpRequests1dGroups         (daily request/bandwidth/threat totals)
Account scope:
  - workersInvocationsAdaptive   (requests, errors, cpu/duration quantiles)
  - r2OperationsAdaptiveGroups   (per-bucket API op counts)
  - r2StorageAdaptiveGroups      (per-bucket objects + payload bytes)

Env:
  CF_API_TOKEN    (required) read-only token — Analytics Read + Zone Read
  CF_ACCOUNT_ID   (required)
  CF_ZONE_ID      (required)
  POLL_SECONDS    (default 300) — also the GraphQL look-back window
  LISTEN_PORT     (default 8080)
"""

import os
import sys
import time
import logging
import urllib.request
import urllib.error
import json
import datetime

from prometheus_client import start_http_server, Gauge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cf-metrics")

TOKEN = os.environ.get("CF_API_TOKEN", "")
ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "")
ZONE = os.environ.get("CF_ZONE_ID", "")
POLL = int(os.environ.get("POLL_SECONDS", "300"))
PORT = int(os.environ.get("LISTEN_PORT", "8080"))
GQL = "https://api.cloudflare.com/client/v4/graphql"

# ---- metric handles -------------------------------------------------------

def _g(name, doc, labels):
    return Gauge(name, doc, labels)

m_reqs        = _g("cloudflare_zone_requests_total",   "Edge requests in window", ["zone", "host", "status"])
m_origin      = _g("cloudflare_zone_origin_requests_total", "Requests reaching origin (cache miss)", ["zone", "host", "origin_status"])
m_cache       = _g("cloudflare_zone_cache_requests_total",  "Edge requests by cache status", ["zone", "cache_status"])
m_country     = _g("cloudflare_zone_requests_country_total","Edge requests by client country", ["zone", "country"])
m_origin_ms   = _g("cloudflare_zone_origin_duration_ms","Origin response duration quantiles (ms)", ["zone", "quantile"])
m_fw          = _g("cloudflare_zone_firewall_events_total", "Firewall/WAF events by action", ["zone", "action", "source"])
m_day_reqs    = _g("cloudflare_zone_daily_requests",   "Daily request totals", ["zone", "date"])
m_day_bytes   = _g("cloudflare_zone_daily_bytes",      "Daily bandwidth bytes", ["zone", "date"])
m_day_threats = _g("cloudflare_zone_daily_threats",    "Daily threat totals", ["zone", "date"])
m_day_pv      = _g("cloudflare_zone_daily_pageviews",  "Daily page views", ["zone", "date"])
m_wk_reqs     = _g("cloudflare_worker_requests_total", "Worker invocations", ["script", "status"])
m_wk_errs     = _g("cloudflare_worker_errors_total",   "Worker errors", ["script", "status"])
m_wk_dur      = _g("cloudflare_worker_duration_seconds_total", "Worker wall time sum (s)", ["script", "status"])
m_wk_cpu      = _g("cloudflare_worker_cpu_time_us",    "Worker CPU time quantiles (us)", ["script", "quantile"])
m_r2_ops      = _g("cloudflare_r2_operations_total",   "R2 API operations", ["bucket", "action"])
m_r2_objs     = _g("cloudflare_r2_objects",            "R2 object count", ["bucket"])
m_r2_bytes    = _g("cloudflare_r2_payload_bytes",      "R2 payload bytes", ["bucket"])
m_scrape_ok   = _g("cloudflare_exporter_up",           "1 when last GraphQL poll succeeded", [])


# ---- GraphQL ---------------------------------------------------------------

def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GQL, data=body, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    if d.get("errors"):
        raise RuntimeError(str(d["errors"])[:400])
    return d["data"]["viewer"]


def window():
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return (now - datetime.timedelta(seconds=POLL)).strftime("%Y-%m-%dT%H:%M:%SZ"), \
           now.strftime("%Y-%m-%dT%H:%M:%SZ")


Q_ZONE = """
query ($zone: String!, $mintime: Time!, $maxtime: Time!, $today: Date!, $limit: Int!) {
  viewer { zones(filter: { zoneTag: $zone }) {
    edge: httpRequestsAdaptiveGroups(limit: $limit, filter: {
        datetime_geq: $mintime, datetime_lt: $maxtime,
        requestSource_in: ["eyeball"] }) {
      count
      dimensions { clientRequestHTTPHost edgeResponseStatus cacheStatus clientCountryName }
      quantiles { originResponseDurationMsP50 originResponseDurationMsP95 originResponseDurationMsP99 }
    }
    misses: httpRequestsAdaptiveGroups(limit: $limit, filter: {
        datetime_geq: $mintime, datetime_lt: $maxtime, cacheStatus_notin: ["hit","stale","revalidated"] }) {
      count
      dimensions { clientRequestHTTPHost originResponseStatus }
    }
    daily: httpRequests1dGroups(limit: 3, orderBy: [date_DESC], filter: {date_geq: $today}) {
      sum { requests bytes threats pageViews }
      dimensions { date }
    }
  } }
}"""

# Optional — paid plans only; skipped silently on Free.
Q_FIREWALL = """
query ($zone: String!, $mintime: Time!, $maxtime: Time!, $limit: Int!) {
  viewer { zones(filter: { zoneTag: $zone }) {
    fw: firewallEventsAdaptiveGroups(limit: $limit, filter: {
        datetime_geq: $mintime, datetime_lt: $maxtime }) {
      count
      dimensions { action source }
    }
  } }
}"""

Q_ACCOUNT = """
query ($acct: String!, $mintime: Time!, $maxtime: Time!, $storagetime: Time!, $limit: Int!) {
  viewer { accounts(filter: { accountTag: $acct }) {
    workersInvocationsAdaptive(limit: $limit, filter: {
        datetime_geq: $mintime, datetime_lt: $maxtime }) {
      sum { requests errors duration }
      dimensions { scriptName status }
      quantiles { cpuTimeP50 cpuTimeP99 durationP50 durationP99 }
    }
    r2OperationsAdaptiveGroups(limit: $limit, filter: {
        datetime_geq: $mintime, datetime_lt: $maxtime }) {
      sum { requests }
      dimensions { actionType bucketName }
    }
    r2StorageAdaptiveGroups(limit: 1000, filter: {
        datetime_geq: $storagetime, datetime_lt: $maxtime }) {
      max { objectCount payloadSize }
      dimensions { bucketName }
    }
  } }
}"""


def _collect_zone(mintime, maxtime):
    try:
        today = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        data = gql(Q_ZONE, {"zone": ZONE, "mintime": mintime, "maxtime": maxtime,
                            "today": today, "limit": 10000})
        z = (data.get("zones") or [{}])[0]
        for g in z.get("edge") or []:
            d = g["dimensions"]
            lbl = {"zone": "cloudless.gr", "host": d.get("clientRequestHTTPHost") or "-"}
            m_reqs.labels(zone=lbl["zone"], host=lbl["host"], status=str(d.get("edgeResponseStatus") or "?")).set(g["count"])
            m_cache.labels(zone=lbl["zone"], cache_status=d.get("cacheStatus") or "-").set(g["count"])
            m_country.labels(zone=lbl["zone"], country=d.get("clientCountryName") or "-").set(g["count"])
            q = g.get("quantiles") or {}
            for k, out in [("originResponseDurationMsP50", "0.5"),
                           ("originResponseDurationMsP95", "0.95"),
                           ("originResponseDurationMsP99", "0.99")]:
                if q.get(k) is not None:
                    m_origin_ms.labels(zone=lbl["zone"], quantile=out).set(q[k])
        for g in z.get("misses") or []:
            d = g["dimensions"]
            m_origin.labels(zone="cloudless.gr", host=d.get("clientRequestHTTPHost") or "-",
                            origin_status=str(d.get("originResponseStatus") or "?")).set(g["count"])
        for g in z.get("daily") or []:
            s, d = g["sum"], g["dimensions"]["date"]
            m_day_reqs.labels(zone="cloudless.gr", date=d).set(s["requests"])
            m_day_bytes.labels(zone="cloudless.gr", date=d).set(s["bytes"])
            m_day_threats.labels(zone="cloudless.gr", date=d).set(s["threats"])
            m_day_pv.labels(zone="cloudless.gr", date=d).set(s["pageViews"])
        try:
            fw = gql(Q_FIREWALL, {"zone": ZONE, "mintime": mintime,
                                  "maxtime": maxtime, "limit": 10000})
            for g in ((fw.get("zones") or [{}])[0].get("fw") or []):
                d = g["dimensions"]
                m_fw.labels(zone="cloudless.gr", action=d.get("action") or "-",
                            source=d.get("source") or "-").set(g["count"])
        except Exception as e:
            log.debug("firewall dataset unavailable (free plan): %s", e)
        return True
    except Exception as e:
        log.error("zone poll failed: %s", e)
        return False


def _collect_account(mintime, maxtime):
    try:
        storagetime = (datetime.datetime.now(datetime.timezone.utc)
                       - datetime.timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = gql(Q_ACCOUNT, {"acct": ACCOUNT, "mintime": mintime, "maxtime": maxtime,
                               "storagetime": storagetime, "limit": 10000})
        a = (data.get("accounts") or [{}])[0]
        for g in a.get("workersInvocationsAdaptive") or []:
            d = g["dimensions"]
            m_wk_reqs.labels(script=d["scriptName"], status=d["status"]).set(g["sum"]["requests"])
            m_wk_errs.labels(script=d["scriptName"], status=d["status"]).set(g["sum"]["errors"])
            m_wk_dur.labels(script=d["scriptName"], status=d["status"]).set(g["sum"]["duration"])
            q = g.get("quantiles") or {}
            for k, out in [("cpuTimeP50", "0.5"), ("cpuTimeP99", "0.99")]:
                if q.get(k) is not None:
                    m_wk_cpu.labels(script=d["scriptName"], quantile=out).set(q[k])
        for g in a.get("r2OperationsAdaptiveGroups") or []:
            d = g["dimensions"]
            m_r2_ops.labels(bucket=d.get("bucketName") or "-", action=d["actionType"]).set(g["sum"]["requests"])
        objs, sizes = {}, {}
        for g in a.get("r2StorageAdaptiveGroups") or []:
            b = g["dimensions"]["bucketName"]
            objs[b] = max(objs.get(b, 0), g["max"]["objectCount"])
            sizes[b] = max(sizes.get(b, 0), g["max"]["payloadSize"])
        for b in objs:
            m_r2_objs.labels(bucket=b).set(objs[b])
            m_r2_bytes.labels(bucket=b).set(sizes[b])
        return True
    except Exception as e:
        log.error("account poll failed: %s", e)
        return False


def main():
    if not (TOKEN and ACCOUNT and ZONE):
        sys.exit("CF_API_TOKEN, CF_ACCOUNT_ID and CF_ZONE_ID are required")
    start_http_server(PORT)
    log.info("serving on :%d/metrics, poll=%ds", PORT, POLL)
    while True:
        mintime, maxtime = window()
        ok = _collect_zone(mintime, maxtime)
        ok = _collect_account(mintime, maxtime) and ok
        m_scrape_ok.set(1 if ok else 0)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
