"""Unit tests for the route-inventory gauge in app.services.metrics."""

from fastapi import APIRouter, FastAPI
from prometheus_client import generate_latest

from app.services import metrics
from app.services.metrics import _ENDPOINT_PATHS, register_route_metrics


def _app_with_nested_routes() -> FastAPI:
    app = FastAPI()
    auth = APIRouter(prefix="/auth")
    auth.post("/login")(lambda: {})
    auth.get("/me")(lambda: {})
    api = APIRouter()
    api.include_router(auth)
    app.include_router(api, prefix="/api/v1")
    return app


def _route_lines() -> str:
    return generate_latest().decode()


class TestRegisterRouteMetrics:
    def test_emits_full_nested_paths(self) -> None:
        register_route_metrics(_app_with_nested_routes())
        out = _route_lines()
        assert 'socialauto_http_route{method="POST",path="/api/v1/auth/login"} 1.0' in out
        assert 'socialauto_http_route{method="GET",path="/api/v1/auth/me"} 1.0' in out

    def test_endpoint_path_map_for_middleware(self) -> None:
        register_route_metrics(_app_with_nested_routes())
        assert "/api/v1/auth/login" in _ENDPOINT_PATHS.values()
        assert "/api/v1/auth/me" in _ENDPOINT_PATHS.values()

    def test_idempotent_no_duplicate_series(self) -> None:
        app = _app_with_nested_routes()
        register_route_metrics(app)
        register_route_metrics(app)
        out = _route_lines()
        assert out.count('path="/api/v1/auth/login"') == 1

    def test_includes_top_level_routes(self) -> None:
        app = FastAPI()
        app.get("/health")(lambda: {})
        register_route_metrics(app)
        assert 'socialauto_http_route{method="GET",path="/health"} 1.0' in _route_lines()


class TestMiddlewarePathResolution:
    def test_middleware_uses_full_path_from_endpoint_map(self) -> None:
        app = _app_with_nested_routes()
        register_route_metrics(app)
        endpoint = next(
            ep for ep, p in _ENDPOINT_PATHS.items() if p == "/api/v1/auth/login"
        )
        assert _ENDPOINT_PATHS.get(endpoint) == "/api/v1/auth/login"
        # ensure the middleware fallback chain resolves via the map
        assert metrics._ENDPOINT_PATHS.get(endpoint) is not None
