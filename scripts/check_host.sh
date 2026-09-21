#!/usr/bin/env bash
set -euo pipefail
echo "CPU: $(nproc) threads"
free -h
df -h /
python3 --version
docker --version
