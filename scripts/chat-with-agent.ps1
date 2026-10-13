<#
.SYNOPSIS
  Try the MineCorp agent from a terminal, without Minecraft (a stand-in for the plugin).

.EXAMPLE
  ./scripts/chat-with-agent.ps1                              # a chat; the agent service must be running (./scripts/start-agent.ps1)
  ./scripts/chat-with-agent.ps1 how do I make a bed?         # ask once
  ./scripts/chat-with-agent.ps1 -Trace -Direct               # run the agent in this process, and show what it did
#>
param(
    [switch]$Direct,
    [switch]$Trace,
    [string]$Name = "Steve",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Question
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "No .venv found. Run: python -m venv .venv" }

$arguments = @((Join-Path $PSScriptRoot "chat.py"), "--name", $Name)
if ($Direct) { $arguments += "--direct" }
if ($Trace) { $arguments += "--trace" }
if ($Question) { $arguments += $Question }
$env:PYTHONIOENCODING = "utf-8"
& $py @arguments
exit $LASTEXITCODE
