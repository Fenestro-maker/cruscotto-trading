#!/bin/bash
# Installa le dipendenze di backend e frontend all'avvio delle sessioni Claude Code nel cloud.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

if [ ! -x backend/venv/bin/python ]; then
  python3 -m venv backend/venv
fi
backend/venv/bin/pip install --quiet --disable-pip-version-check -r backend/requirements.txt mongomock-motor

(cd frontend && npm install --no-save --no-audit --no-fund --loglevel=error)
