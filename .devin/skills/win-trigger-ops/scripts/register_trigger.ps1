# register_trigger.ps1 — auto-fires chrome-debug-launch on user logon so :9222
# binds without a manual double-click. Run once on Windows:
#   powershell -NoProfile -ExecutionPolicy Bypass -File register_trigger.ps1
$action = New-ScheduledTaskAction -Execute "C:\Users\tbaltzakis\chrome-debug-launch.bat"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "CloudlessDebugChrome" -Action $action -Trigger $trigger -Force
Write-Output "registered — chrome-debug-launch fires on next logon"
