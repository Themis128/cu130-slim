#Requires -Version 5.1
<#
.SYNOPSIS
  Diagnose the new Outlook for Windows (olk / Microsoft.OutlookForWindows) on this host.
  Read-only. Run from WSL: powershell.exe -NoProfile -ExecutionPolicy Bypass -File outlook_diag.ps1

  What it checks (all verified useful during the 2026-11-13 MS365 outage triage):
    - AppX package state/version (query packages ONE name at a time — Get-AppxPackage -Name
      rejects an array; that was the earlier diagnostic bug)
    - olk.exe process state + WebView2 child tree (user-data-dir proves they belong to Outlook)
    - Application crash events (1000/1001) + AppModel lifecycle events
    - Package file activity under %LOCALAPPDATA%\Packages\... and %LOCALAPPDATA%\Microsoft\Olk
    - WebView2 runtime version
    - Latest olk_*.log tail hints (OOBE funnel events, GetToken results, deactivation flapping)
#>
$ErrorActionPreference = 'SilentlyContinue'
$pkgName = 'Microsoft.OutlookForWindows'

Write-Output '=== AppX package ==='
$pkg = Get-AppxPackage -Name $pkgName
if ($pkg) {
    $pkg | Select-Object Name, Version, Status, PackageFullName, InstallLocation | Format-List
} else { Write-Output 'NOT INSTALLED' }

Write-Output '=== WebView2 runtime ==='
Get-AppxPackage -Name 'MicrosoftCorporationII.MicrosoftEdgeWebView2Runtime' -ErrorAction SilentlyContinue |
    Select-Object Name, Version | Format-Table -AutoSize

Write-Output '=== olk process ==='
$olk = Get-Process olk
if ($olk) {
    $olk | Select-Object Id, @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB)}},
        Responding, MainWindowTitle, StartTime | Format-List
    $wv = Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" |
        Where-Object { $_.CommandLine -match 'Microsoft.Olk' }
    Write-Output ("webview2 children for Olk: " + @($wv).Count)
} else { Write-Output 'olk not running' }

Write-Output '=== Crash events (48h) ==='
Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddDays(-2);
    ProviderName='Application Error','Windows Error Reporting','.NET Runtime'} |
    Where-Object { $_.Message -match 'olk|Outlook' } |
    Select-Object -First 5 TimeCreated, Id, @{n='Msg';e={$_.Message.Substring(0,[Math]::Min(140,$_.Message.Length))}} |
    Format-List

Write-Output '=== Package data footprint ==='
$pkgRoot = "$env:LOCALAPPDATA\Packages\$($pkg.PackageFamilyName)"
$olkRoot  = "$env:LOCALAPPDATA\Microsoft\Olk"
foreach ($d in @($pkgRoot, $olkRoot)) {
    if (Test-Path $d) {
        $mb = [math]::Round(((Get-ChildItem $d -Recurse -File | Measure-Object Length -Sum).Sum)/1MB,1)
        Write-Output ("{0} = {1} MB" -f $d, $mb)
    }
}

Write-Output '=== Recent files touched (2h) ==='
Get-ChildItem $olkRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt (Get-Date).AddHours(-2) } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 12 |
    ForEach-Object { "{0}  {1} KB" -f $_.LastWriteTime.ToString('HH:mm:ss'), [math]::Round($_.Length/1KB) }

Write-Output '=== Latest olk log: funnel/auth/deactivation lines ==='
$log = Get-ChildItem "$olkRoot\logs\olk_*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($log) {
    Write-Output ("log: " + $log.Name + "  " + [math]::Round($log.Length/1KB) + "KB  write=" + $log.LastWriteTime)
    Select-String -Path $log.FullName -Pattern 'oobeFunnelEvent|GetToken\t|DeactivatedApp|Application exiting' |
        Select-Object -Last 12 | ForEach-Object {
            $_.Line.Substring(0, [Math]::Min(190, $_.Line.Length)) }
}

Write-Output '=== Network reachability (MS365 degradation signatures) ==='
foreach ($u in @('https://outlook.office.com/mail/','https://arc.msn.com/v4/','https://outlook.office365.com/owa/')) {
    try {
        $r = Invoke-WebRequest -Uri $u -TimeoutSec 15 -UseBasicParsing -MaximumRedirection 0
        Write-Output ("{0} -> {1}" -f $u, $r.StatusCode)
    } catch {
        $resp = $_.Exception.Response
        if ($resp) { Write-Output ("{0} -> HTTP {1}" -f $u, [int]$resp.StatusCode) }
        else      { Write-Output ("{0} -> ERR {1}" -f $u, $_.Exception.Message.Substring(0,[Math]::Min(70,$_.Exception.Message.Length))) }
    }
}
