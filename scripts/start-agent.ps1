<#
.SYNOPSIS
  Start the MineCorp agent service (uses the project's .venv; settings come from .env).
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run: python -m venv .venv" }

& $py -m pip install -q -r (Join-Path $root "agent\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

Push-Location (Join-Path $root "agent")
try { & $py -m minecorp_agent } finally { Pop-Location }
