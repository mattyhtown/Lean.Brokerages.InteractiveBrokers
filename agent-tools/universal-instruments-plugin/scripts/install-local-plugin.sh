#!/usr/bin/env bash
# Build and wire the universal-instruments Cursor plugin on a local machine.
# Safe and idempotent: never overwrites ~/.cursor/mcp.json automatically.
set -euo pipefail

DRY_RUN=0
DO_SYMLINK=1

usage() {
  cat <<'EOF'
Usage: install-local-plugin.sh [--dry-run] [--no-symlink]

Options:
  --dry-run     Print planned actions and the mcpServers snippet without modifying anything
  --no-symlink  Skip symlinking into ~/.cursor/plugins/local/universal-instruments
  -h, --help    Show this help message
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-symlink) DO_SYMLINK=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MD_DIR="${PLUGIN_DIR}/mcp/market-data"
PM_DIR="${PLUGIN_DIR}/mcp/prediction-markets"
IB_DIR="${PLUGIN_DIR}/mcp/ib-lean-bridge"
VENV_DIR="${IB_DIR}/.venv"
TARGET_LINK="${HOME}/.cursor/plugins/local/universal-instruments"

run() {
  if [ "${DRY_RUN}" -eq 1 ]; then
    printf '[dry-run]'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

echo "Plugin directory: ${PLUGIN_DIR}"

echo ""
echo "1. Building market-data and prediction-markets MCP servers..."
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is required to build market-data and prediction-markets." >&2
  exit 1
fi
run npm --prefix "${MD_DIR}" ci
run npm --prefix "${MD_DIR}" run build
run npm --prefix "${PM_DIR}" ci
run npm --prefix "${PM_DIR}" run build

echo ""
echo "2. Setting up Python virtual environment for ib-lean-bridge..."
IB_PYTHON="${VENV_DIR}/bin/python"
if ! command -v python3 >/dev/null 2>&1; then
  echo "WARN: python3 not found; skipping ib-lean-bridge setup." >&2
  IB_PYTHON="python3"
elif [ "${DRY_RUN}" -eq 1 ]; then
  run python3 -m venv "${VENV_DIR}"
  run "${VENV_DIR}/bin/pip" install -r "${IB_DIR}/requirements.txt"
else
  if [ ! -x "${VENV_DIR}/bin/pip" ]; then
    rm -rf "${VENV_DIR}"
    if ! python3 -m venv "${VENV_DIR}" 2>/dev/null || [ ! -x "${VENV_DIR}/bin/pip" ]; then
      rm -rf "${VENV_DIR}"
      echo "WARN: Could not create Python venv (on Debian/Ubuntu, install python3-venv)." >&2
      echo "      Fallback: pip3 install --user -r \"${IB_DIR}/requirements.txt\"" >&2
      IB_PYTHON="python3"
    fi
  fi
  if [ -x "${VENV_DIR}/bin/pip" ]; then
    "${VENV_DIR}/bin/pip" install -q -r "${IB_DIR}/requirements.txt"
    echo "OK: Installed ib-lean-bridge dependencies into ${VENV_DIR}"
  fi
fi

echo ""
echo "3. Linking plugin into ~/.cursor/plugins/local/universal-instruments..."
if [ "${DO_SYMLINK}" -eq 0 ]; then
  echo "SKIP: --no-symlink specified."
elif [ -L "${TARGET_LINK}" ] && [ "$(readlink "${TARGET_LINK}")" = "${PLUGIN_DIR}" ]; then
  echo "OK: Symlink already points to ${PLUGIN_DIR}"
elif [ -e "${TARGET_LINK}" ] && [ ! -L "${TARGET_LINK}" ]; then
  echo "WARN: ${TARGET_LINK} exists and is not a symlink; leaving it untouched." >&2
else
  run mkdir -p "$(dirname "${TARGET_LINK}")"
  run ln -sfn "${PLUGIN_DIR}" "${TARGET_LINK}"
  if [ "${DRY_RUN}" -eq 0 ]; then
    echo "OK: Linked ${TARGET_LINK} -> ${PLUGIN_DIR}"
  fi
fi

echo ""
echo "4. Ready-to-paste ~/.cursor/mcp.json entry (not written automatically):"
echo ""
python3 - "${MD_DIR}/dist/index.js" "${PM_DIR}/dist/index.js" "${IB_PYTHON}" "${IB_DIR}/server.py" <<'EOF'
import json
import sys

md_entry, pm_entry, ib_python, ib_server = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
snippet = {
    "mcpServers": {
        "market-data": {
            "command": "node",
            "args": [md_entry],
            "env": {
                "SEC_USER_AGENT": "",
            },
        },
        "prediction-markets": {
            "command": "node",
            "args": [pm_entry],
        },
        "ib-lean-bridge": {
            "command": ib_python,
            "args": [ib_server],
            "env": {
                "IB_HOST": "127.0.0.1",
                "IB_PORT": "7497",
                "IB_CLIENT_ID": "77",
                "IB_ACCOUNT": "",
                "IB_ALLOW_ORDERS": "0",
            },
        },
    }
}
print(json.dumps(snippet, indent=2))
EOF
echo ""
echo "Next steps:"
echo "  - Restart Cursor after updating ~/.cursor/mcp.json."
echo "  - Optional: run market-data as a standalone Streamable HTTP MCP server on 127.0.0.1:3100:"
echo "      node \"${MD_DIR}/dist/index.js\" --http --port 3100"
echo "  - Start local TWS (paper port 7497) or IB Gateway (paper port 4002), then run:"
echo "      \"${IB_PYTHON}\" \"${PLUGIN_DIR}/scripts/smoke-test-ib.py\" --port 7497"
