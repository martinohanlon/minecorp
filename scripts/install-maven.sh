#!/usr/bin/env bash
# Install Apache Maven (needed to build the plugin and run its tests) with the system's package manager, or by downloading it from Apache.
# On Windows use scripts/install-maven.ps1. Usage: install-maven.sh [version]   (the version is only used for the download; default 3.9.16)
set -euo pipefail

version="${1:-3.9.16}"

if command -v mvn >/dev/null 2>&1; then
    echo "Maven is already installed:"
    mvn -version
    exit 0
fi

if command -v brew >/dev/null 2>&1; then
    brew install maven
elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y maven            # may be older than 3.9; use the download below if the plugin build needs a newer one
else
    # No package manager: unpack Apache's release into ~/.local and link mvn into ~/.local/bin (put that on PATH).
    [[ "$version" =~ ^3\.[0-9]+\.[0-9]+$ ]] || { echo "Version must look like 3.9.16, not '$version'." >&2; exit 1; }
    url="https://archive.apache.org/dist/maven/maven-3/$version/binaries/apache-maven-$version-bin.tar.gz"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    echo "Downloading Maven $version..."
    curl -fsSL "$url" -o "$tmp/maven.tar.gz"
    expected="$(curl -fsSL "$url.sha512" | awk '{print $1}')"
    actual="$( (sha512sum "$tmp/maven.tar.gz" 2>/dev/null || shasum -a 512 "$tmp/maven.tar.gz") | awk '{print $1}')"
    [ "$expected" = "$actual" ] || { echo "The download does not match its SHA-512. Nothing was installed." >&2; exit 1; }
    mkdir -p "$HOME/.local/bin"
    rm -rf "$HOME/.local/apache-maven-$version"
    tar -xzf "$tmp/maven.tar.gz" -C "$HOME/.local"
    ln -sf "$HOME/.local/apache-maven-$version/bin/mvn" "$HOME/.local/bin/mvn"
    echo "Installed to $HOME/.local/apache-maven-$version. Make sure $HOME/.local/bin is on PATH."
    export PATH="$HOME/.local/bin:$PATH"
fi
mvn -version
