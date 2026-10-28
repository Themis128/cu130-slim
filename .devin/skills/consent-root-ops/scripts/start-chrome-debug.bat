@echo off
REM One-time: launch Chrome with remote debugging so root_approve.py can
REM drive the user's real logged-in session (X OAuth approve, Qualtrics, etc).
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\Users\tbaltzakis\chrome-debug-profile" "about:blank"
echo Chrome debug session started on :9222
