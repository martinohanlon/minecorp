<#
.SYNOPSIS
  Download (if needed) and start a Paper server for MineCorp.

.PARAMETER Version
  Minecraft version, or "latest" for the newest version with a STABLE Paper build.
  The resolved version is pinned in paper-version.txt so later runs do not drift;
  use -Update to re-resolve.

.PARAMETER AcceptEula
  Writes eula=true. Only pass this once you have read https://aka.ms/MinecraftEULA.
#>
[CmdletBinding()]
param(
    [string]$Version = "latest",
    [string]$Memory = "4G",
    [switch]$Update,
    [switch]$AcceptEula
)

$ErrorActionPreference = "Stop"
$api = "https://fill.papermc.io/v3/projects/paper"
# Scripts live in scripts/; the server runs from ../server (git-ignored).
$serverDir = Join-Path (Split-Path $PSScriptRoot -Parent) "server"
New-Item -ItemType Directory -Force $serverDir | Out-Null
Set-Location $serverDir

if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    throw "Java not found on PATH. Install the JDK/JRE version required by this Paper release (see https://docs.papermc.io/paper/getting-started) and retry."
}

# --- resolve version -------------------------------------------------------
$pinFile = Join-Path $PSScriptRoot "paper-version.txt"
if ($Version -eq "latest" -and (Test-Path $pinFile) -and -not $Update) {
    $Version = (Get-Content $pinFile).Trim()
}

$build = $null
if ($Version -eq "latest") {
    $groups = (Invoke-RestMethod "$api").versions.PSObject.Properties.Value
    foreach ($v in $groups | ForEach-Object { $_ }) {
        if ($v -match "-") { continue }   # skip pre/rc
        $b = Invoke-RestMethod "$api/versions/$v/builds/latest"
        if ($b.channel -eq "STABLE") { $Version = $v; $build = $b; break }
    }
    if (-not $build) { throw "No STABLE Paper build found." }
} else {
    $build = Invoke-RestMethod "$api/versions/$Version/builds/latest"
}
Set-Content $pinFile $Version

# --- download + verify -----------------------------------------------------
$dl = $build.downloads."server:default"
$jar = Join-Path $serverDir $dl.name
if (-not (Test-Path $jar)) {
    Write-Host "Downloading $($dl.name) ..."
    Invoke-WebRequest $dl.url -OutFile $jar
    $hash = (Get-FileHash $jar -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $dl.checksums.sha256) {
        Remove-Item $jar
        throw "Checksum mismatch for $($dl.name)."
    }
}

# --- java version ----------------------------------------------------------
$minJava = (Invoke-RestMethod "$api/versions/$Version").version.java.version.minimum
# java -version writes to stderr; run via cmd so Windows PowerShell 5.1 doesn't treat it as an error.
$javaOut = (cmd /c "java -version 2>&1" | Select-Object -First 1).ToString()
if ($javaOut -match '"(\d+)') {
    if ([int]$Matches[1] -lt $minJava) { throw "Paper $Version needs Java $minJava+, found: $javaOut" }
}

# --- eula ------------------------------------------------------------------
$eula = Join-Path $serverDir "eula.txt"
if ($AcceptEula) { Set-Content $eula "eula=true" }
elseif (-not (Test-Path $eula) -or -not (Select-String -Path $eula -Pattern "eula=true" -Quiet)) {
    throw "Minecraft EULA not accepted. Read https://aka.ms/MinecraftEULA then rerun with -AcceptEula."
}

# --- run -------------------------------------------------------------------
Write-Host "Starting Paper $Version (build $($build.id)) with $Memory ..."
java "-Xms$Memory" "-Xmx$Memory" -jar $jar --nogui
