<#
.SYNOPSIS
  Build the whole MineCorp graph from source (the jar, model/ and the wiki page list): recipes, actions, world, trades,
  model, routes, then the wiki and its links. WIPES the database named in .env first.

.EXAMPLE
  ./scripts/build-graph.ps1                # asks, then rebuilds everything
  ./scripts/build-graph.ps1 --list         # show the steps and change nothing
  ./scripts/build-graph.ps1 --no-wiki      # the game graph only (no OpenAI, no network)
  ./scripts/build-graph.ps1 --from model   # carry on from a step after a failure
  ./scripts/build-graph.ps1 --yes          # do not ask

  All arguments go to scripts/build_graph.py, which explains them (--help).
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run: python -m venv .venv ; .venv\Scripts\pip install -r loader\requirements.txt" }

& $py (Join-Path $PSScriptRoot "build_graph.py") @args
exit $LASTEXITCODE
