<#
.SYNOPSIS
  Install Apache Maven (needed to build the plugin and run its tests) and put it on your PATH. No administrator rights needed.
  Maven is not in winget's official sources, so this downloads it from Apache, checks the SHA-512, unpacks it to
  %LOCALAPPDATA%\Programs and adds its bin folder to your user PATH.

.PARAMETER Version
  The Maven 3.9 release to install (see https://maven.apache.org/download.cgi). Default 3.9.16.

.PARAMETER Force
  Install again even when this version is already unpacked.
#>
[CmdletBinding()]
param(
    [string]$Version = "3.9.16",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"      # Invoke-WebRequest is very slow with the progress bar

if ($Version -notmatch '^3\.\d+\.\d+$') { throw "Version must look like 3.9.16, not '$Version'." }
if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    Write-Warning "No java on PATH. Maven needs a JDK: run ./scripts/install-java.ps1 first."
}

$base = "https://archive.apache.org/dist/maven/maven-3/$Version/binaries/apache-maven-$Version-bin.zip"
$root = Join-Path $env:LOCALAPPDATA "Programs"
$home_ = Join-Path $root "apache-maven-$Version"
$bin = Join-Path $home_ "bin"

if ((Test-Path (Join-Path $bin "mvn.cmd")) -and -not $Force) {
    Write-Host "Maven $Version is already unpacked in $home_."
} else {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("maven-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "maven.zip"
        Write-Host "Downloading Maven $Version..."
        Invoke-WebRequest -Uri $base -OutFile $zip -UseBasicParsing
        $expected = ((Invoke-WebRequest -Uri "$base.sha512" -UseBasicParsing).Content -split '\s+')[0].Trim().ToLowerInvariant()
        $actual = (Get-FileHash -Algorithm SHA512 -Path $zip).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "The download does not match its SHA-512 (expected $expected, got $actual). Nothing was installed." }
        Write-Host "SHA-512 checked."

        $unpacked = Join-Path $tmp "unpacked"
        Expand-Archive -Path $zip -DestinationPath $unpacked
        New-Item -ItemType Directory -Force -Path $root | Out-Null
        if (Test-Path $home_) { Remove-Item -Recurse -Force $home_ }
        Move-Item -Path (Join-Path $unpacked "apache-maven-$Version") -Destination $home_
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

# User PATH: add this bin folder, and drop the bin folder of any other Maven this script installed earlier.
$user = [Environment]::GetEnvironmentVariable("Path", "User")
$parts = @($user -split ';' | Where-Object { $_ -and ($_ -notlike (Join-Path $root "apache-maven-*") -or $_ -eq $bin) })
if ($parts -notcontains $bin) { $parts += $bin }
[Environment]::SetEnvironmentVariable("Path", ($parts -join ';'), "User")

# This shell only: PATH changes reach new terminals, so refresh this one for the check below.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

Write-Host "`nInstalled. Verifying:"
cmd /c "mvn -version 2>&1"
Write-Host "`nOpen a new terminal (and restart VS Code) so PATH is picked up everywhere. mvn -version should report Java 25."
