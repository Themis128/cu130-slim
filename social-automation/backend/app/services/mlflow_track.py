"""MLflow tracking — local file store, same SDK surface Databricks uses.

Experiment `social-funnel` logs the funnel_score tiers per run; experiment
`content-eval` logs per-post quality scores. Swap MLFLOW_TRACKING_URI to a
Databricks workspace URI later — no code changes needed.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_URI = os.environ.get("MLFLOW_TRACKING_URI", "file:///app/mlruns")


def _mlflow():
    import mlflow
    mlflow.set_tracking_uri(_URI)
    return mlflow


@contextmanager
def run(experiment: str, run_name: str | None = None, tags: dict | None = None):
    """Start (or reuse) an MLflow run under a named experiment."""
    m = _mlflow()
    m.set_experiment(experiment)
    with m.start_run(run_name=run_name, tags=tags or {}) as r:
        yield m, r


def log_funnel_score(scored: dict[str, int], total: int) -> None:
    """Log one funnel_score run — tier distribution as metrics."""
    with run("social-funnel", tags={"source": "funnel_score"}) as (m, _):
        for tier, n in scored.items():
            m.log_metric(f"leads_{tier}", n)
        m.log_metric("leads_total", total)


def log_content_eval(post_id: str, total: float, model: str,
                     dims: dict[str, float] | None = None) -> None:
    """Log one content_eval judgement — total + per-dimension scores."""
    with run("content-eval", tags={"model": model}) as (m, _):
        m.log_param("post_id", post_id)
        m.log_metric("score_total", total)
        for k, v in (dims or {}).items():
            m.log_metric(f"score_{k}", v)


def log_dmr_usage(endpoint: str, model: str, latency_ms: float,
                  ok: bool) -> None:
    """Log one DMR-gateway call — latency + success per logical endpoint."""
    with run("dmr-gateway", tags={"endpoint": endpoint, "model": model}) as (m, _):
        m.log_metric("latency_ms", latency_ms)
        m.log_metric("ok", 1 if ok else 0)
