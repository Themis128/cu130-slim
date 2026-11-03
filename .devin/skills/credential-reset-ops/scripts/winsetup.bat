@echo off
copy /Y "C:\Users\tbaltzakis\cu130-slim\.devin\skills\win-bridge-ops\scripts\win_agent.py" "%USERPROFILE%\win_agent.py"
powershell -Command "Register-ScheduledTask -TaskName 'win-agent' -Action (New-ScheduledTaskAction -Execute 'python' -Argument '%USERPROFILE%\win_agent.py') -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Force"
start "" /min python "%USERPROFILE%\win_agent.py"
powershell -ExecutionPolicy Bypass -File "C:\Users\tbaltzakis\cu130-slim\.devin\skills\consent-root-ops\scripts\install-chromedebug-task.ps1"
