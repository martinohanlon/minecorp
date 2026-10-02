<#
.SYNOPSIS
  Run the MineCorp tests: the Python tests for the loaders (pytest) and the plugin's JUnit tests (Maven).

.PARAMETER Integration
  Also run the tests that rebuild the whole graph in a throwaway Neo4j database (about 20 seconds).
  Needs Neo4j running (Enterprise Edition, for a second database) and the Paper jar.

.PARAMETER E2E
  Also run the end-to-end plugin test: a real Paper server with the real plugin and a stub agent
  (about 25 seconds; uses .test-server/ and a random port, so your own server can keep running).

.PARAMETER All
  Everything: -Integration and -E2E.

.PARAMETER PythonOnly
  Skip the plugin's Maven tests.

.EXAMPLE
  ./scripts/test.ps1              # fast: loader unit tests, tests against the Paper jar, plugin JUnit tests
  ./scripts/test.ps1 -All         # everything
#>
[CmdletBinding()]
param(
    [switch]$Integration,
    [switch]$E2E,
    [switch]$All,
    [switch]$PythonOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run: python -m venv .venv ; .venv\Scripts\pip install -r requirements-dev.txt" }
if ($All) { $Integration = $true; $E2E = $true }

# which pytest markers to include
$markers = if ($Integration -and $E2E) { "" }
           elseif ($Integration)       { "not e2e" }
           elseif ($E2E)               { "not integration" }
           else                        { "not integration and not e2e" }

$results = [ordered]@{}

Push-Location $root
try {
    Write-Host "`n== Python tests (pytest, markers: '$markers') ==" -ForegroundColor Cyan
    & $py -m pip install -q -r requirements-dev.txt 2>&1 | Out-Null
    & $py -m pytest -m $markers
    $results["Python (loaders)"] = ($LASTEXITCODE -eq 0)

    if (-not $PythonOnly) {
        Write-Host "`n== Plugin tests (JUnit via Maven) ==" -ForegroundColor Cyan
        if (-not (Get-Command mvn -ErrorAction SilentlyContinue)) { throw "Maven (mvn) not found on PATH." }
        if (-not (Get-Command javac -ErrorAction SilentlyContinue)) { throw "A JDK 25 (javac) is required on PATH. Run scripts/install-java.ps1." }
        Push-Location (Join-Path $root "plugin")
        try { mvn -B test; $results["Java (plugin)"] = ($LASTEXITCODE -eq 0) } finally { Pop-Location }
    }
} finally { Pop-Location }

Write-Host "`n== Summary ==" -ForegroundColor Cyan
$failed = $false
foreach ($k in $results.Keys) {
    $ok = $results[$k]
    Write-Host ("{0,-20} {1}" -f $k, $(if ($ok) { "PASS" } else { "FAIL" })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
    if (-not $ok) { $failed = $true }
}
if ($failed) { exit 1 }
