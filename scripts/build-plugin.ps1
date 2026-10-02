<#
.SYNOPSIS
  Build the MineCorp plugin with Maven, copy the jar into server/plugins, and create the
  plugin config (with the shared secret from .env) if it does not exist yet.
  Restart the server to load a new jar. Avoid /reload.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent

if (-not (Get-Command mvn -ErrorAction SilentlyContinue)) { throw "Maven (mvn) not found on PATH." }
if (-not (Get-Command javac -ErrorAction SilentlyContinue)) { throw "A JDK 25 (javac) is required on PATH. Run scripts/install-java.ps1." }

Push-Location (Join-Path $root "plugin")
try {
    mvn -B -q package
    if ($LASTEXITCODE -ne 0) { throw "Maven build failed." }
} finally { Pop-Location }

$plugins = Join-Path $root "server\plugins"
New-Item -ItemType Directory -Force $plugins | Out-Null
Get-ChildItem (Join-Path $root "plugin\target") -Filter "MineCorp-*.jar" | ForEach-Object {
    Copy-Item $_.FullName $plugins -Force
    Write-Host "Copied $($_.Name) to $plugins"
}

# Plugin config: written once from .env so the secret matches the agent service.
$configDir = Join-Path $plugins "MineCorp"
$config = Join-Path $configDir "config.yml"
if (-not (Test-Path $config)) {
    $envFile = Join-Path $root ".env"
    $secret = ""
    $port = "8080"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*AGENT_SHARED_SECRET\s*=\s*(.+?)\s*$') { $secret = $Matches[1].Trim('"', "'") }
            if ($line -match '^\s*AGENT_PORT\s*=\s*(\d+)') { $port = $Matches[1] }
        }
    }
    if (-not $secret) { Write-Warning "AGENT_SHARED_SECRET not found in .env; set shared-secret in $config yourself." }
    New-Item -ItemType Directory -Force $configDir | Out-Null
    @"
agent-url: "http://127.0.0.1:$port"
shared-secret: "$secret"
timeout-seconds: 30
"@ | Set-Content $config -Encoding utf8
    Write-Host "Created $config"
} else {
    Write-Host "Kept existing $config (delete it to regenerate from .env)"
}
