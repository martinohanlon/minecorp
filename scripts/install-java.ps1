<#
.SYNOPSIS
  Install Eclipse Temurin JDK 25 (required by Paper 26.x) using winget.
  The JDK is used rather than the JRE because the plugin build will need it later.
#>
[CmdletBinding()]
param(
    [string]$Package = "EclipseAdoptium.Temurin.25.JDK"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget not found. Install 'App Installer' from the Microsoft Store, or install Temurin 25 manually from https://adoptium.net."
}

winget install --id $Package --exact --source winget --accept-package-agreements --accept-source-agreements
if ($LASTEXITCODE -ne 0) { throw "winget install failed (exit code $LASTEXITCODE)." }

# PATH changes only apply to new shells; refresh this one so the check below works.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

Write-Host "`nInstalled. Verifying:"
cmd /c "java -version 2>&1"
Write-Host "`nOpen a new terminal before running start.ps1 so PATH is picked up."
