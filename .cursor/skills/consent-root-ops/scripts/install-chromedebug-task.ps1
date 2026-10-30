# install-chromedebug-task.ps1 — registers "ChromeDebug" as a CURRENT-USER
# logon task (no admin needed). Run once from Windows:
#   powershell -ExecutionPolicy Bypass -File install-chromedebug-task.ps1
$bat = "C:\Users\tbaltzakis\cu130-slim\.devin\skills\consent-root-ops\scripts\start-chrome-debug.bat"
$action = New-ScheduledTaskAction -Execute $bat
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "ChromeDebug" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "ChromeDebug task registered for $env:USERNAME"
Start-ScheduledTask -TaskName "ChromeDebug"
Write-Host "fired once now - :9222 should bind in ~10s"
