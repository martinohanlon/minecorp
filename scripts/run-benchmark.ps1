<#
.SYNOPSIS
  Run the benchmark: does a knowledge layer let a small model match a large one? (docs/benchmark.md)
  Checks the gold answers against the graph, asks before spending money, runs every question on four arms, then prints the report.

.EXAMPLE
  ./scripts/run-benchmark.ps1                       # the full run: 720 runs, about 6 dollars; asks first
  ./scripts/run-benchmark.ps1 -Yes                  # do not ask
  ./scripts/run-benchmark.ps1 -Repeats 1 -Yes       # a third of the cost: one repeat
  ./scripts/run-benchmark.ps1 s1 s2 -Repeats 1      # only questions whose id contains s1 or s2
  ./scripts/run-benchmark.ps1 -Arms bare -Models gpt-6-luna -Repeats 1   # the model alone, the small model
  ./scripts/run-benchmark.ps1 -VerifyOnly           # free: check the gold answers against the graph
  ./scripts/run-benchmark.ps1 -ReportOnly           # free: report on the newest run (-Run <id> for another)
  ./scripts/run-benchmark.ps1 -DryRun -Repeats 1 s1  # free: only print the command it would run

  Needs the graph (the Neo4j in .env) and OPENAI_API_KEY. Results go to agent/bench/results/. The cost cap is -MaxCost (default 10 dollars).
#>
[CmdletBinding(PositionalBinding = $false)]     # a bare word is a question id, not a value for the first parameter
param(
    [switch]$Yes,
    [switch]$VerifyOnly,
    [switch]$ReportOnly,
    [string]$Run,
    [int]$Repeats,
    [double]$MaxCost,
    [string]$Arms,
    [string]$Models,
    [string]$Effort,
    [int]$Seed,
    [switch]$NoShuffle,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Filter
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run: python -m venv .venv ; .venv\Scripts\pip install -r requirements-dev.txt" }

$arguments = @((Join-Path $PSScriptRoot "run_benchmark.py"))
if ($Yes) { $arguments += "--yes" }
if ($VerifyOnly) { $arguments += "--verify-only" }
if ($ReportOnly) { $arguments += "--report-only" }
if ($Run) { $arguments += @("--run", $Run) }
if ($PSBoundParameters.ContainsKey("Repeats")) { $arguments += @("--repeats", $Repeats) }
if ($PSBoundParameters.ContainsKey("MaxCost")) { $arguments += @("--max-cost", $MaxCost) }
if ($Arms) { $arguments += @("--arms", $Arms) }
if ($Models) { $arguments += @("--models", $Models) }
if ($Effort) { $arguments += @("--effort", $Effort) }
if ($PSBoundParameters.ContainsKey("Seed")) { $arguments += @("--seed", $Seed) }
if ($NoShuffle) { $arguments += "--no-shuffle" }
if ($Filter) { $arguments += $Filter }
if ($DryRun) { Write-Host "$py $($arguments -join ' ')"; exit 0 }
$env:PYTHONIOENCODING = "utf-8"
& $py @arguments
exit $LASTEXITCODE
