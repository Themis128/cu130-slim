#!/usr/bin/env python3
"""
Airbyte MCP Server — HTTP wrapper.

Wraps the official airbyte-mcp (stdio-only) with a streamable-http transport
so it can run as a Docker Compose service. Uses FastMCP's built-in HTTP support.

Also provides a simple /health endpoint for Docker health checks.
"""

import os
import sys
import asyncio
from fastmcp import FastMCP

# ── Health check HTTP server (separate from MCP) ────────────────────────────

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"airbyte-mcp-server"}')
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args):
        pass  # suppress health check logs

def start_health_server(port: int):
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# ── Main ────────────────────────────────────────────────────────────────────

async def main():
    port = int(os.environ.get("AIRBYTE_MCP_PORT", "9228"))
    health_port = int(os.environ.get("AIRBYTE_MCP_HEALTH_PORT", "9229"))
    host = os.environ.get("AIRBYTE_MCP_HOST", "0.0.0.0")

    # Start health check server in a background thread on a separate port
    health_thread = threading.Thread(
        target=start_health_server, args=(health_port,), daemon=True
    )
    health_thread.start()

    # Try to import the official airbyte MCP server
    try:
        from airbyte.mcp.server import create_mcp_server
        mcp = create_mcp_server()
        print(f"Airbyte MCP server created successfully", flush=True)
    except ImportError as e:
        print(f"Could not import airbyte.mcp.server: {e}", flush=True)
        print("Falling back to minimal FastMCP server...", flush=True)
        mcp = FastMCP("airbyte-mcp")

        @mcp.tool()
        def list_connectors() -> str:
            """List all available Airbyte connectors."""
            try:
                from airbyte.registry import get_connector_metadata
                connectors = get_connector_metadata()
                return f"Found {len(connectors)} connectors"
            except Exception as e:
                return f"Error listing connectors: {e}"

        @mcp.tool()
        def list_env_vars() -> str:
            """List available environment variable names (not values)."""
            env_vars = [k for k in os.environ if not k.startswith("_")]
            safe_vars = [v for v in env_vars if "SECRET" not in v.upper()
                        and "PASSWORD" not in v.upper()
                        and "TOKEN" not in v.upper()
                        and "KEY" not in v.upper()]
            return f"Available env vars: {', '.join(sorted(safe_vars))}"

    # Run with streamable-http transport
    print(f"Starting Airbyte MCP server on http://{host}:{port}/mcp", flush=True)
    await mcp.run_http_async(
        host=host,
        port=port,
        path="/mcp",
    )

if __name__ == "__main__":
    asyncio.run(main())
