@echo off
REM win-agent-setup.bat — one-time: open firewall + start win_agent
REM bound to all interfaces so WSL can reach it on :8765.
REM Run as Administrator (right-click → Run as administrator).

netsh advfirewall firewall add rule name="WSL win-agent 8765" dir=in action=allow protocol=TCP localport=8765 remoteip=any >nul 2>&1
echo firewall rule added (or already existed)

REM Start the agent bound to 0.0.0.0 — reachable from WSL at the
REM default-gateway IP.
start "" python "C:\Users\tbaltzakis\cu130-slim\.devin\skills\win-bridge-ops\scripts\win_agent.py"
echo win-agent started on :8765 — WSL reaches it at http://^<gateway-ip^>:8765
