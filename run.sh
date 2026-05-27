#!/usr/bin/env bash
# Wrapper for launchd/cron: fetch the token *at runtime* (never store it in the
# plist or in this file), then run the summary. Edit the token line for your setup.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Pick ONE way to provide the token (do not hardcode it):
#   1Password CLI:  export GITHUB_TOKEN="$(op read 'op://Private/GitHub PAT/token')"
#   macOS Keychain: export GITHUB_TOKEN="$(security find-generic-password -s tldiff -w)"
export GITHUB_TOKEN="${GITHUB_TOKEN:-$(op read 'op://Private/GitHub PAT/token')}"

# launchd starts with a minimal PATH, so resolve uv explicitly.
UV="$(command -v uv || echo "${HOME}/.local/bin/uv")"

# Human-readable table -> monthly log; structured data -> daily.jsonl (one row
# per date, deduped). daily.jsonl is the chartable dataset that accumulates.
LOG_DIR="${HOME}/Projects/tldiff/logs"
mkdir -p "$LOG_DIR"
# `uv run` syncs the locked .venv (fast no-op if current) then runs the script.
exec "$UV" run --project "$HERE" python "$HERE/tldiff.py" \
    --append "$LOG_DIR/daily.jsonl" \
    >> "$LOG_DIR/$(date +%Y-%m).log" 2>&1
