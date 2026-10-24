#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"
command -v python3 >/dev/null || { echo "ERROR: python3 is required." >&2; exit 2; }
exec python3 scripts/simulate_incident.py "$@"
