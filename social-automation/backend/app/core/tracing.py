"""OpenTelemetry tracing — exports OTLP/HTTP to self-hosted Tempo on omv.

Enabled by OTEL_ENABLED=true. No-op otherwise. Cloudflare forwards its
trace context (W3C traceparent) to the origin when zone tracing has
forward_context on, so these spans continue the edge trace and land in
the same Tempo trace for end-to-end visibility.
"""

import logging
import os

from fastapi import FastAPI

logger = logging.getLogger(__name__)

_ENABLED_VALUES = {"1", "true", "yes", "on"}


def instrument_app(app: FastAPI) -> None:
    if os.environ.get("OTEL_ENABLED", "").lower() not in _ENABLED_VALUES:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL_ENABLED set but opentelemetry packages are not installed")
        return

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "social-api"),
            "deployment.environment": os.environ.get("APP_ENV", "production"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        app, excluded_urls="metrics|favicon|health|ready|live"
    )
    HTTPXClientInstrumentor().instrument()
    logger.info(
        "OpenTelemetry tracing enabled, exporting to %s",
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
    )
