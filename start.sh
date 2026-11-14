#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# Every start, so an existing environment picks up a requirement added since it was made.
.venv/bin/python -m pip install -q -r requirements.txt
printf 'Earth Data Explorer: http://127.0.0.1:8000\nKeep this terminal open. Press Ctrl+C to stop.\n'
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
