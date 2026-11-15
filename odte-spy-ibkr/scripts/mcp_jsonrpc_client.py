#!/usr/bin/env python3
"""Minimal MCP JSON-RPC client for tool calls via ALPACA_MCP_HTTP_URL or MCP_HTTP_URL."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: mcp_jsonrpc_client.py <namespace> <tool> <arguments-json>")
    namespace, tool, arg_json = sys.argv[1], sys.argv[2], sys.argv[3]
    url = os.environ.get("ALPACA_MCP_HTTP_URL") or os.environ.get("MCP_HTTP_URL")
    if not url:
        raise SystemExit("set ALPACA_MCP_HTTP_URL or MCP_HTTP_URL for non-interactive MCP fetch")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": json.loads(arg_json)},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-MCP-Namespace": namespace},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(exc.read().decode() or str(exc)) from exc
    if "error" in raw:
        raise SystemExit(json.dumps(raw["error"]))
    result = raw.get("result", raw)
    if isinstance(result, dict) and "content" in result:
        for block in result["content"]:
            if block.get("type") == "text":
                print(block["text"])
                return
    print(json.dumps(result))


if __name__ == "__main__":
    main()