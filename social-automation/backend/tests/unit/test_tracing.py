"""Tests for app.core.tracing — OTel instrumentation toggle."""

import importlib.util
import sys

import pytest
from fastapi import FastAPI

from app.core import tracing


def test_instrument_app_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    app = FastAPI()
    tracing.instrument_app(app)  # must not raise, must not instrument


def test_instrument_app_warns_when_packages_missing(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_ENABLED", "true")
    for mod in list(sys.modules):
        if mod.startswith("opentelemetry"):
            monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    with caplog.at_level("WARNING", logger="app.core.tracing"):
        tracing.instrument_app(FastAPI())
    assert "not installed" in caplog.text


@pytest.mark.skipif(
    importlib.util.find_spec("opentelemetry.sdk") is None,
    reason="opentelemetry-sdk not installed in this environment",
)
def test_instrument_app_registers_provider(monkeypatch):
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "social-api-test")
    from opentelemetry import trace

    tracing.instrument_app(FastAPI())
    assert isinstance(trace.get_tracer_provider(), trace.TracerProvider)
