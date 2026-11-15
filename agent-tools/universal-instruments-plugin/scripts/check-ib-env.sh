#!/usr/bin/env bash
# Read-only IB environment check — does not authenticate to IB.
set -euo pipefail

IB_HOST="${IB_HOST:-127.0.0.1}"
IB_PORT="${IB_PORT:-7497}"
IB_CLIENT_ID="${IB_CLIENT_ID:-77}"

echo "IB_HOST=${IB_HOST}"
echo "IB_PORT=${IB_PORT}"
echo "IB_CLIENT_ID=${IB_CLIENT_ID}"
if [ "${IB_ALLOW_ORDERS:-}" = "1" ]; then
  echo "IB_ALLOW_ORDERS=1 (the bridge will send confirmed orders)"
else
  echo "IB_ALLOW_ORDERS is not 1 (the bridge will refuse to send or cancel orders)"
fi

port_open() {
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 "$1" "$2" 2>/dev/null
  elif command -v timeout >/dev/null 2>&1; then
    timeout 2 bash -c ': </dev/tcp/"$0"/"$1"' "$1" "$2" 2>/dev/null
  else
    return 2
  fi
}

status=0
port_open "${IB_HOST}" "${IB_PORT}" || status=$?
case "${status}" in
  0) echo "OK: TCP port ${IB_PORT} is open on ${IB_HOST} (Gateway/TWS may be running)." ;;
  2) echo "SKIP: neither 'nc' nor 'timeout' is installed; cannot probe port." ;;
  *) echo "WARN: Nothing listening on ${IB_HOST}:${IB_PORT}. Start IB Gateway or TWS with API enabled." ;;
esac

if command -v lean >/dev/null 2>&1; then
  echo "OK: lean CLI found: $(command -v lean)"
else
  echo "INFO: lean CLI not installed — see docs/IB_LEAN_SETUP.md for QuantConnect LEAN live workflow."
fi