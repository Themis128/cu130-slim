# push-via-windows.ps1 -Repo <wsl-path> [-Remote origin] [-Branch <b>]
# Pushes a WSL repo via Windows git.exe — bypasses flaky WSL/vsock networking.
param([Parameter(Mandatory)][string]$Repo,[string]$Remote='origin',[string]$Branch='')
$dst="\wsl.localhost\Ubuntu-24.04$($Repo -replace '/','\')"
$work="$env:TEMP\wslgit-$(Get-Random)"
robocopy $dst $work /E /MT:8 /NFL /NDL /NJH /NJS | Out-Null
Set-Location $work
gh auth setup-git 2>$null
if (-not $Branch) { $Branch = git branch --show-current }
git push -u $Remote $Branch
$local = git rev-parse HEAD
$rem = (git ls-remote $Remote $Branch) -split '\s' | Select-Object -First 1
if ($local -eq $rem) { Write-Output "PUSHED $Branch@$local" } else { Write-Output "FAILED $Branch" }
