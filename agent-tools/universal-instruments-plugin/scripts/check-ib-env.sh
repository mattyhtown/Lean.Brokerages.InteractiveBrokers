#!/usr/bin/env bash
# Read-only IB environment check — does not authenticate to IB.
set -euo pipefail

IB_HOST="${IB_HOST:-127.0.0.1}"
IB_PORT="${IB_PORT:-7497}"
IB_CLIENT_ID="${IB_CLIENT_ID:-1}"

echo "IB_HOST=${IB_HOST}"
echo "IB_PORT=${IB_PORT}"
echo "IB_CLIENT_ID=${IB_CLIENT_ID}"

if command -v nc >/dev/null 2>&1; then
  if nc -z -w 2 "${IB_HOST}" "${IB_PORT}" 2>/dev/null; then
    echo "OK: TCP port ${IB_PORT} is open on ${IB_HOST} (Gateway/TWS may be running)."
  else
    echo "WARN: Nothing listening on ${IB_HOST}:${IB_PORT}. Start IB Gateway or TWS with API enabled."
  fi
else
  echo "SKIP: 'nc' not installed; cannot probe port."
fi

if command -v lean >/dev/null 2>&1; then
  echo "OK: lean CLI found: $(command -v lean)"
else
  echo "INFO: lean CLI not installed — see docs/IB_LEAN_SETUP.md for QuantConnect LEAN live workflow."
fi