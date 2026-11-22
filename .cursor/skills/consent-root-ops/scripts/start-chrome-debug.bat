@echo off
REM One-time: launch Chrome with remote debugging so root_approve.py can
REM drive the user's real logged-in session (X OAuth approve, Qualtrics, etc).
REM Port arg or %CDP_PORT%, default 9229 — 9222 is claimed by the Lenovo
REM Vantage WebView2 on this host.
set PORT=%1
if "%PORT%"=="" set PORT=%CDP_PORT%
if "%PORT%"=="" set PORT=9229
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=%PORT% --user-data-dir="C:\Users\tbaltzakis\chrome-debug-profile" "about:blank"
echo Chrome debug session started on :%PORT%
