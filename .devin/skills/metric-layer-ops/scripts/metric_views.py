#!/usr/bin/env python3
"""metric_views.py — governed metric definitions over SocialAuto Postgres.
The Databricks Metric Views adopt: one canonical SQL per KPI, materialized as
views, shared by Metabase/genie/alerts — kills metric drift."""
import subprocess
import sys

VIEWS = {
    "mv_publish_success": """
      CREATE OR REPLACE VIEW mv_publish_success AS
      SELECT date_trunc('day', scheduled_at) AS day,
             count(*) FILTER (WHERE status='published') AS published,
             count(*) FILTER (WHERE status='failed') AS failed,
             round(100.0*count(*) FILTER (WHERE status='published')
                   /nullif(count(*),0),1) AS success_pct
      FROM posts WHERE scheduled_at IS NOT NULL GROUP BY 1 ORDER BY 1 DESC""",
    "mv_engagement_rate": """
      CREATE OR REPLACE VIEW mv_engagement_rate AS
      SELECT date_trunc('day', posted_at) AS day,
             count(*) AS candidates,
             count(*) FILTER (WHERE scored IS NOT NULL) AS scored,
             count(*) FILTER (WHERE in_crm) AS in_crm,
             round(100.0*count(*) FILTER (WHERE in_crm)
                   /nullif(count(*),0),1) AS crm_pct
      FROM engagement_candidates GROUP BY 1 ORDER BY 1 DESC""",
    "mv_survey_response": """
      CREATE OR REPLACE VIEW mv_survey_response AS
      SELECT count(*) AS responses,
             count(*) FILTER (WHERE submitted_at IS NOT NULL) AS submitted,
             count(*) FILTER (WHERE espocrm_noted) AS in_crm,
             round(100.0*count(*) FILTER (WHERE espocrm_noted)
                   /nullif(count(*),0),1) AS crm_pct
      FROM survey_responses""",
}


def _psql(sql: str):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "social-postgres", "psql",
         "-U", "social_user", "-d", "social_automation", "-c", sql],
        capture_output=True, text=True, timeout=30)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "metrics"
    if cmd == "views":
        for n, ddl in VIEWS.items():
            print(n, _psql(ddl).stderr.strip() or "created")
    elif cmd == "query":
        print(_psql(f"SELECT * FROM {sys.argv[2]} LIMIT 10").stdout)
    else:
        print("\n".join(VIEWS))
