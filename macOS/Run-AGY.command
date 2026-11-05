#!/bin/bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11+ is required: https://www.python.org/downloads/" >&2
  exit 1
fi
exec python3 "$ROOT/app/acp_terminal.py"  "$@"
