<#
.SYNOPSIS
  Audit (and optionally repair) Windows scheduled tasks that can flash a
  visible console window. Companion to windows-host-ops skill.

.DESCRIPTION
  Every console-subsystem executable launched directly by Task Scheduler
  allocates a console -> a visible conhost/cmd flash. '-WindowStyle Hidden'
  does NOT help: the console is still allocated before the flag applies.
  The only reliable fix is wrapping the action in:

      C:\Windows\System32\conhost.exe --headless <original exe> <args>

  GUI-subsystem exes (notepad, chrome, wscript->hidden vbs is still risky for
  its children, pythonw) and already-wrapped conhost actions are reported OK.

.PARAMETER Fix
  Re-register offending actions wrapped in conhost --headless. Tasks whose
  RunLevel=Highest or that live under \DevOptimizer\ may need an elevated
  shell; access-denied tasks are reported, not failed. If the current shell
  is not elevated, use -FixElevated instead.

.PARAMETER FixElevated
  Run the fix through a pre-registered HIGHEST-runlevel helper task
  (PopupTaskAudit-Fix). schtasks /run needs no UAC — this is the standard
  Windows pattern for unattended elevation. Register the helper ONCE from an
  elevated shell:  popup_task_audit.ps1 -RegisterElevatedHelper
  Helper output lands in %LOCALAPPDATA%\PopupTaskAudit\fix.log.

.PARAMETER RegisterElevatedHelper
  (Re)create the PopupTaskAudit-Fix scheduled task with RunLevel=Highest.
  MUST be run from an elevated PowerShell once — registering a Highest task
  from a medium-IL process is denied by Task Scheduler.

.PARAMETER Json
  Emit the audit as JSON instead of a table.

.PARAMETER Path
  Restrict the audit to a task path (default: all tasks).

.PARAMETER Log
  Transcript path - all output is captured here (used by the elevated helper).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1 -FixElevated
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$FixElevated,
    [switch]$RegisterElevatedHelper,
    [switch]$Json,
    [string]$Path = '\',
    [string]$Log = '',
    [switch]$IncludeVendor
)

if ($Log) {
    $logDir = Split-Path $Log -Parent
    if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    Start-Transcript -Path $Log -Force | Out-Null
}

$ErrorActionPreference = 'Continue'

$HelperTaskName = 'PopupTaskAudit-Fix'
$AuditLogDir   = Join-Path $env:LOCALAPPDATA 'PopupTaskAudit'
$FixLogPath    = Join-Path $AuditLogDir 'fix.log'
# Prefer the stable Windows-side copy for the scheduled action; fall back to
# this script's own path (works from the repo checkout too).
$HostScript    = 'D:\DevOptimizer\scripts\popup_task_audit.ps1'
if (-not (Test-Path $HostScript)) { $HostScript = $PSCommandPath }

if ($RegisterElevatedHelper) {
    # conhost --headless -> powershell -File <script> -Fix -Log <fix.log>
    $argLine = ('--headless "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" ' +
                '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Fix -Log "{1}"' -f $HostScript, $FixLogPath)
    $action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\conhost.exe' -Argument $argLine
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
    try {
        Register-ScheduledTask -TaskName $HelperTaskName -Action $action -Principal $principal -Force -ErrorAction Stop | Out-Null
        Write-Host "Registered $HelperTaskName (RunLevel=Highest)." -ForegroundColor Green
        Write-Host "Now any medium-IL shell can run:  popup_task_audit.ps1 -FixElevated"
    } catch {
        Write-Host "FAILED to register helper (needs an ELEVATED shell): $($_.Exception.Message)" -ForegroundColor Red
        exit 2
    }
    exit 0
}

if ($FixElevated) {
    $helper = Get-ScheduledTask -TaskName $HelperTaskName -ErrorAction SilentlyContinue
    if (-not $helper) {
        Write-Host "Helper task '$HelperTaskName' not found. Register it once from an elevated shell:" -ForegroundColor Yellow
        Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -RegisterElevatedHelper"
        exit 2
    }
    if (-not (Test-Path $AuditLogDir)) { New-Item -ItemType Directory -Path $AuditLogDir -Force | Out-Null }
    Remove-Item $FixLogPath -ErrorAction SilentlyContinue
    schtasks /run /tn $HelperTaskName | Out-Null
    # wait for the helper to leave Running state (cap 180s)
    $deadline = (Get-Date).AddSeconds(180)
    do {
        Start-Sleep -Seconds 3
        $st = (Get-ScheduledTask -TaskName $HelperTaskName -ErrorAction SilentlyContinue).State
    } while ($st -eq 'Running' -and (Get-Date) -lt $deadline)
    Write-Host "Helper state: $st"
    if (Test-Path $FixLogPath) {
        Write-Host "`n--- fix.log ---"
        Get-Content $FixLogPath -Tail 40
    } else {
        Write-Host "No fix.log produced - helper may have failed before logging." -ForegroundColor Yellow
    }
    exit 0
}

# Exes that DO allocate a console when run directly.
$consoleRisk = '(?i)\b(powershell|pwsh|cmd|wsl|bash|sh|python|node|npm|npx|docker|docker-compose|kubectl|ssh|scp|ftp|telnet|curl|bitsadmin|certutil|mshta|cscript|wscript|conhost|bash\.exe)\b'

function Test-ConsoleRisk([string]$exe, [string]$argLine) {
    # basename without IO.Path (Execute can contain quotes/env vars that are
    # illegal path chars)
    $clean = $exe.Trim('"') -replace '^.*[\\/]', ''
    $name = [IO.Path]::GetFileNameWithoutExtension($clean)
    $exeN = $exe.Trim('"')
    if ($name -match '^(pythonw|wscript|mshta|rundll32|regsvr32|wmic|mmc|control|msinfo32|taskmgr|devenv|code|windsurf|devin|notepad|chrome|msedge|firefox|opera|brave)$') { return $false }
    if ($name -match '^conhost' -or $exeN -match '(?i)conhost(\.exe)?$') {
        # conhost without --headless still shows a window
        if ($argLine -match '--headless') { return $false }
        return $true
    }
    if ($name -match '^(powershell|pwsh|cmd|wsl|bash|sh|python|node|npm|npx|docker|docker-compose|kubectl|ssh|scp|ftp|telnet|curl|bitsadmin|certutil|cscript)$') { return $true }
    # Fallback: full path basename check for anything else suspicious
    if ($exeN -match $consoleRisk) { return $true }
    return $false
}

function Wrap-Action($action) {
    $exe = $action.Execute.Trim('"')
    $args = $action.Arguments
    # Build: conhost.exe --headless "<exe>" <args>
    $inner = '"{0}"' -f $exe
    if ($args) { $inner = $inner + ' ' + $args }
    return ,@('C:\Windows\System32\conhost.exe', ('--headless ' + $inner))
}

# -TaskPath '\' is NOT recursive: it returns root-level tasks only and misses
# \DevOptimizer\ etc. Bare Get-ScheduledTask returns every folder.
if ($Path -eq '\') {
    $tasks = Get-ScheduledTask -ErrorAction SilentlyContinue
} else {
    $tasks = Get-ScheduledTask -TaskPath $Path -ErrorAction SilentlyContinue
}
# Vendor tasks (\Microsoft\Office, \Microsoft\Windows\*) run as SYSTEM/session-0
# and manage their own console behavior - wrapping them is out of scope and
# risky. -IncludeVendor audits them anyway.
if (-not $IncludeVendor) {
    $tasks = @($tasks | Where-Object { $_.TaskPath -notlike '\Microsoft\*' })
}
$results = New-Object System.Collections.Generic.List[object]

foreach ($t in $tasks) {
    $state = $null
    try { $state = $t.State } catch {}
    $runLevel = ''
    try { $runLevel = $t.Principal.RunLevel } catch {}
    foreach ($a in $t.Actions) {
        $exe = "$($a.Execute)"
        $argLine = "$($a.Arguments)"
        $risk = Test-ConsoleRisk $exe $argLine
        $hiddenFlag = $argLine -match '(?i)-WindowStyle\s+Hidden|/MIN|start\s+/min'
        $verdict = 'OK'
        if ($risk) {
            if ($hiddenFlag) { $verdict = 'FLASH-RISK (Hidden flag insufficient)' }
            else { $verdict = 'FLASH-RISK' }
        }
        $results.Add([pscustomobject]@{
            Task      = $t.TaskName
            Path      = $t.TaskPath
            State     = "$state"
            RunLevel  = "$runLevel"
            Execute   = $exe
            Arguments = $(if ($argLine.Length -gt 110) { $argLine.Substring(0,110)+'...' } else { $argLine })
            Verdict   = $verdict
        })
    }
}

$bad = $results | Where-Object { $_.Verdict -ne 'OK' }

if ($Fix -and $bad) {
    $fixed = 0; $denied = 0
    foreach ($row in $bad) {
        $t = Get-ScheduledTask -TaskName $row.Task -TaskPath $row.Path -ErrorAction SilentlyContinue
        if (-not $t) { continue }
        $newActions = @()
        foreach ($a in $t.Actions) {
            $aExe = "$($a.Execute)".Trim('"')
            $aArgs = "$($a.Arguments)"
            $aName = ($aExe -replace '^.*[\\/]', '') -replace '\.exe$',''
            $isRisk = (Test-ConsoleRisk $aExe $aArgs) -and ($aArgs -notmatch '--headless')
            if (-not $isRisk) { $newActions += $a; continue }
            if ($aName -match '^conhost') {
                # already conhost, just missing --headless: prepend it to args
                $newActions += (New-ScheduledTaskAction -Execute $aExe -Argument ('--headless ' + $aArgs))
            } else {
                $wrapped = Wrap-Action $a
                $newActions += (New-ScheduledTaskAction -Execute $wrapped[0] -Argument $wrapped[1])
            }
        }
        try {
            Set-ScheduledTask -TaskName $row.Task -TaskPath $row.Path -Action $newActions -ErrorAction Stop | Out-Null
            $fixed++
            Write-Host ("FIXED   {0}{1} -> conhost --headless" -f $row.Path, $row.Task) -ForegroundColor Green
        } catch {
            $denied++
            Write-Host ("DENIED  {0}{1} ({2}) - rerun elevated" -f $row.Path, $row.Task, $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    Write-Host ("`nFix pass: {0} fixed, {1} need elevation" -f $fixed, $denied)
}

if ($Json) {
    $results | ConvertTo-Json -Depth 4
} else {
    $okCount = @($results | Where-Object { $_.Verdict -eq 'OK' }).Count
    Write-Host ("{0} task-action(s) audited: {1} OK, {2} flash-risk." -f `
        $results.Count, $okCount, @($bad).Count)
    if (@($bad).Count -gt 0) {
        Write-Host "`nFLASH-RISK actions:"
        $bad | Sort-Object TaskPath, TaskName | ForEach-Object {
            Write-Host ("  {0}{1}  [{2}/{3}]`n      {4} {5}`n      -> {6}" -f `
                $_.Path, $_.Task, $_.State, $_.RunLevel, $_.Execute, $_.Arguments, $_.Verdict)
        }
    }
}
