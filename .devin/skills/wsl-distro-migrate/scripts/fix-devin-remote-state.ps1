# fix-devin-remote-state.ps1 — repoint VS Code-family IDE state (Devin/Cursor/Code)
# from one WSL distro name to another. App-managed files get flushed from memory,
# so the durable fix needs the IDE closed: this script waits for the exit gap.
# Usage: fix-devin-remote-state.ps1 -OldDistro Ubuntu-26.04 -NewDistro Ubuntu-24.04 [-WaitMinutes 30]
param(
    [Parameter(Mandatory)][string]$OldDistro,
    [Parameter(Mandatory)][string]$NewDistro,
    [string]$IdeExe = 'Devin',
    [int]$WaitMinutes = 30,
    [int]$PollSeconds = 15
)
$ErrorActionPreference = 'Continue'
$log = "$env:USERPROFILE\devin-queue\fix-remote-state.log"
function Log($m) { Add-Content $log "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" }

$deadline = (Get-Date).AddMinutes($WaitMinutes)
while (Get-Process $IdeExe -ErrorAction SilentlyContinue) {
    if ((Get-Date) -gt $deadline) { Log "SKIP: $IdeExe still running after ${WaitMinutes}m"; exit 2 }
    Start-Sleep $PollSeconds
}

$oldLow = $OldDistro.ToLower(); $newLow = $NewDistro.ToLower()
$fixes = 0
$roots = @("$env:APPDATA\devin", "$env:APPDATA\Cursor", "$env:APPDATA\Code")
foreach ($root in $roots) {
    if (-not (Test-Path $root)) { continue }
    $jsons = @("$root\User\globalStorage\storage.json")
    $jsons += Get-ChildItem "$root\User\workspaceStorage\*\workspace.json" -ErrorAction SilentlyContinue | ForEach-Object FullName
    foreach ($f in $jsons) {
        if (-not (Test-Path $f)) { continue }
        $raw = Get-Content $f -Raw
        if ($raw -match [regex]::Escape($OldDistro) -or $raw -match [regex]::Escape($oldLow)) {
            Copy-Item $f "$f.bak-distromig" -Force
            ($raw -replace [regex]::Escape($OldDistro), $NewDistro -replace [regex]::Escape($oldLow), $newLow) |
                Set-Content $f -NoNewline -Encoding UTF8
            $fixes++; Log "patched $f"
        }
    }
    # sqlite state stores (global + per-workspace)
    $dbs = @("$root\User\globalStorage\state.vscdb")
    $dbs += Get-ChildItem "$root\User\workspaceStorage\*\state.vscdb" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $sqlite = (Get-Command sqlite3 -ErrorAction SilentlyContinue).Source
    if (-not $sqlite) { $sqlite = "$env:USERPROFILE\Android\Sdk\platform-tools\sqlite3.exe" }
    foreach ($db in $dbs) {
        if (-not (Test-Path $db) -or -not (Test-Path $sqlite)) { continue }
        $stale = & $sqlite $db "SELECT count(*) FROM ItemTable WHERE key LIKE '%$OldDistro%' OR key LIKE '%$oldLow%' OR value LIKE '%$OldDistro%' OR value LIKE '%$oldLow%';" 2>$null
        if ($stale -and [int]$stale -gt 0) {
            Copy-Item $db "$db.bak-distromig" -Force
            & $sqlite $db "UPDATE ItemTable SET value = replace(replace(value,'$OldDistro','$NewDistro'),'$oldLow','$newLow') WHERE value LIKE '%$OldDistro%' OR value LIKE '%$oldLow%';" 2>$null
            & $sqlite $db "UPDATE ItemTable SET key = replace(replace(key,'$OldDistro','$NewDistro'),'$oldLow','$newLow') WHERE key LIKE '%$OldDistro%' OR key LIKE '%$oldLow%';" 2>$null
            $fixes++; Log "patched $db ($stale rows)"
        }
    }
}
Log "DONE: $fixes file(s) patched ($OldDistro -> $NewDistro)"
exit 0
