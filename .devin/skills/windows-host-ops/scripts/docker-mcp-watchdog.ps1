# DockerMcpGateway-Watchdog
# Ensures the docker-mcp gateway's OAuth notification monitor stays connected.
# The monitor connects once at startup to \\.\pipe\dockerBackendApiServer and
# never retries — if Docker Desktop's backend restarted after the gateway, the
# monitor is permanently dead for that process lifetime. Restart the gateway
# whenever the backend is newer than the gateway process.
# Runs every 10 min via scheduled task "DockerMcpGateway-Watchdog".

$ErrorActionPreference = 'SilentlyContinue'
$log = "$env:USERPROFILE\logs\docker-mcp-watchdog.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Log($m) { "$(Get-Date -Format o) $m" | Out-File -Append -Encoding utf8 $log }

$gw = Get-Process docker-mcp -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $gw) {
    Log "gateway not running -> starting DockerMcpGateway task"
    Start-ScheduledTask -TaskName 'DockerMcpGateway'
    exit 0
}

# newest com.docker.backend start time = last backend (re)start
$backendNewest = Get-Process | Where-Object { $_.Name -like 'com.docker.backend*' } |
    Sort-Object StartTime -Descending | Select-Object -First 1
if (-not $backendNewest) { Log "no com.docker.backend process; skipping"; exit 0 }

if ($backendNewest.StartTime -gt $gw.StartTime) {
    # verify the pipe actually accepts connections before restarting
    $pipeOk = $false
    try {
        $pipe = New-Object System.IO.Pipes.NamedPipeClientStream('.', 'dockerBackendApiServer', [System.IO.Pipes.PipeDirection]::InOut)
        $pipe.Connect(3000)
        $pipeOk = $true
        $pipe.Dispose()
    } catch { $pipeOk = $false }

    if ($pipeOk) {
        Log "backend restarted (backend $($backendNewest.StartTime) > gateway $($gw.StartTime)) and pipe healthy -> restarting gateway"
        Stop-Process -Id $gw.Id -Force
        Start-Sleep -Seconds 3
        Start-ScheduledTask -TaskName 'DockerMcpGateway'
    } else {
        Log "backend newer than gateway but pipe not connectable yet; will retry next run"
    }
} else {
    Log "ok: gateway $($gw.StartTime) newer than backend $($backendNewest.StartTime)"
}
