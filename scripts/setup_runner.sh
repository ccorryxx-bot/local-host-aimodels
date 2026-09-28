#!/usr/bin/env bash
# Free disk on the GitHub-hosted runner before downloading models.
set -euo pipefail

echo "Disk before:"
df -h / | tail -n 1

for d in /usr/share/dotnet /usr/local/lib/android /opt/ghc /opt/hostedtoolcache/CodeQL /usr/local/share/boost; do
  sudo rm -rf "$d" &
done
wait
sudo apt-get clean

echo "Disk after:"
df -h / | tail -n 1
echo "CPU cores: $(nproc)"
free -h | sed -n 2p
