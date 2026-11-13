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
  shell; access-denied tasks are reported, not failed.

.PARAMETER Json
  Emit the audit as JSON instead of a table.

.PARAMETER Path
  Restrict the audit to a task path (default: all tasks).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File popup_task_audit.ps1 -Fix
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$Json,
    [string]$Path = '\'
)

$ErrorActionPreference = 'Continue'

# Exes that never allocate a console (GUI subsystem or already headless).
$guiSafe = '(?i)^(notepad|chrome|msedge|firefox|opera|brave|docker desktop|com\.docker.*|wscript|mshta|rundll32|regsvr32|wmic|mmc|control|msinfo32|taskmgr|devenv|code|windsurf|devin)'
# Exes that DO allocate a console when run directly.
$consoleRisk = '(?i)\b(powershell|pwsh|cmd|wsl|bash|sh|python|node|npm|npx|docker|docker-compose|kubectl|ssh|scp|ftp|telnet|curl|bitsadmin|certutil|mshta|cscript|wscript|conhost|bash\.exe)'

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

$tasks = Get-ScheduledTask -TaskPath $Path -ErrorAction SilentlyContinue
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
