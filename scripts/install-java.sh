#!/usr/bin/env bash
# Install Eclipse Temurin JDK 25 (required by Paper 26.x and the plugin build) with the system's package manager.
# Needs sudo on Linux. On Windows use scripts/install-java.ps1 (winget).
set -euo pipefail

if command -v brew >/dev/null 2>&1; then
    brew install --cask temurin@25
elif command -v apt-get >/dev/null 2>&1; then
    # Adoptium's apt repository: https://adoptium.net/installation/linux/
    sudo apt-get install -y wget apt-transport-https gpg
    wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/adoptium.gpg >/dev/null
    . /etc/os-release
    echo "deb https://packages.adoptium.net/artifactory/deb ${VERSION_CODENAME} main" | sudo tee /etc/apt/sources.list.d/adoptium.list
    sudo apt-get update
    sudo apt-get install -y temurin-25-jdk
else
    echo "No brew or apt-get found. Install a Temurin 25 JDK from https://adoptium.net/ and put it on PATH." >&2
    exit 1
fi
java -version
