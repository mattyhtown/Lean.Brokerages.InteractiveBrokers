from __future__ import annotations

import json

SUPABASE_MCP_URL = "https://mcp.supabase.com/mcp"
UNUSUAL_WHALES_MCP_URL = "https://api.unusualwhales.com/api/mcp"
MONGODB_NPX_PACKAGE = "mongodb-mcp-server@3.3.0"
UNUSUAL_WHALES_NPX_PACKAGE = "@unusualwhales/mcp@1.2.0"
SUPABASE_NPX_PACKAGE = "@supabase/mcp-server-supabase@0.17.0"
# Databento publishes no MCP server. This is the metadata-only server from the
# community NimbleMarkets/dbn-go project; none of its tools incur Databento
# billing. It is a binary the user installs, not an npx package.
DATABENTO_MCP_COMMAND = "dbn-go-mcp-meta"
# Served by Theta Terminal v3 on the user's machine, over SSE only. Clients
# without SSE support (Claude Desktop, Codex, Zed) and hosted APIs that cannot
# reach loopback (anthropic-api, openai-responses) therefore omit it.
THETADATA_MCP_SSE_URL = "http://127.0.0.1:25503/mcp/sse"

SUPPORTED_TARGETS: tuple[str, ...] = (
    "cursor",
    "claude-desktop",
    "claude-code",
    "anthropic-api",
    "codex",
    "openai-responses",
    "gemini-cli",
    "vscode",
    "windsurf",
    "zed",
)


def _databento_stdio(api_key: str = "YOUR_DATABENTO_API_KEY") -> dict[str, object]:
    return {
        "command": DATABENTO_MCP_COMMAND,
        "args": [],
        "env": {"DATABENTO_API_KEY": api_key},
    }


def render_mcp_config(target: str) -> str:
    normalized = target.strip().lower()
    if normalized not in SUPPORTED_TARGETS:
        allowed = ", ".join(SUPPORTED_TARGETS)
        raise ValueError(f"Unsupported target '{target}'. Supported targets: {allowed}")

    if normalized == "cursor":
        payload = {
            "mcpServers": {
                "supabase": {
                    "type": "url",
                    "url": SUPABASE_MCP_URL,
                },
                "unusualwhales": {
                    "type": "url",
                    "url": UNUSUAL_WHALES_MCP_URL,
                    "headers": {"Authorization": "Bearer YOUR_UW_API_KEY"},
                },
                "mongodb": {
                    "command": "npx",
                    "args": ["-y", MONGODB_NPX_PACKAGE],
                    "env": {
                        "MDB_MCP_API_CLIENT_ID": "YOUR_ATLAS_CLIENT_ID",
                        "MDB_MCP_API_CLIENT_SECRET": "YOUR_ATLAS_CLIENT_SECRET",
                        "MDB_MCP_READ_ONLY": "true",
                    },
                },
                "databento": _databento_stdio(),
                "thetadata": {"url": THETADATA_MCP_SSE_URL},
            }
        }
        return json.dumps(payload, indent=2)

    if normalized == "claude-desktop":
        payload = {
            "mcpServers": {
                # claude_desktop_config.json only launches stdio servers; the hosted
                # URL is added separately under Settings -> Connectors.
                "supabase": {
                    "command": "npx",
                    "args": ["-y", SUPABASE_NPX_PACKAGE, "--read-only"],
                    "env": {"SUPABASE_ACCESS_TOKEN": "YOUR_SUPABASE_ACCESS_TOKEN"},
                },
                "unusualwhales": {
                    "command": "npx",
                    "args": ["-y", UNUSUAL_WHALES_NPX_PACKAGE],
                    "env": {"UW_API_KEY": "YOUR_UW_API_KEY"},
                },
                "mongodb": {
                    "command": "npx",
                    "args": ["-y", MONGODB_NPX_PACKAGE],
                    "env": {
                        "MDB_MCP_API_CLIENT_ID": "YOUR_ATLAS_CLIENT_ID",
                        "MDB_MCP_API_CLIENT_SECRET": "YOUR_ATLAS_CLIENT_SECRET",
                        "MDB_MCP_READ_ONLY": "true",
                    },
                },
                "databento": _databento_stdio(),
            }
        }
        return json.dumps(payload, indent=2)

    if normalized == "claude-code":
        return "\n".join(
            [
                f"claude mcp add --transport http supabase {SUPABASE_MCP_URL}",
                f"claude mcp add --transport http unusualwhales {UNUSUAL_WHALES_MCP_URL} \\",
                '  --header "Authorization: Bearer ${UW_API_KEY}"',
                "claude mcp add mongodb \\",
                '  -e MDB_MCP_API_CLIENT_ID="${MDB_MCP_API_CLIENT_ID}" \\',
                '  -e MDB_MCP_API_CLIENT_SECRET="${MDB_MCP_API_CLIENT_SECRET}" \\',
                f"  -e MDB_MCP_READ_ONLY=true -- npx -y {MONGODB_NPX_PACKAGE}",
                f'claude mcp add databento -e DATABENTO_API_KEY="${{DATABENTO_API_KEY}}" -- {DATABENTO_MCP_COMMAND}',
                f"claude mcp add --transport sse thetadata {THETADATA_MCP_SSE_URL}",
            ]
        )

    if normalized == "anthropic-api":
        payload = {
            "_note": "POST to https://api.anthropic.com/v1/messages; set these HTTP headers separately (not in the JSON body).",
            "http_headers": {
                "anthropic-beta": "mcp-client-2025-11-20",
                "anthropic-version": "2023-06-01",
                "x-api-key": "YOUR_ANTHROPIC_API_KEY",
            },
            "request_body": {
                "model": "claude-sonnet-4-5",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "Your prompt"}],
                "mcp_servers": [
                    {
                        "type": "url",
                        "name": "supabase",
                        "url": SUPABASE_MCP_URL,
                        "authorization_token": "YOUR_SUPABASE_ACCESS_TOKEN",
                    },
                    {
                        "type": "url",
                        "name": "unusualwhales",
                        "url": UNUSUAL_WHALES_MCP_URL,
                        "authorization_token": "YOUR_UW_API_KEY",
                    },
                ],
                "tools": [
                    {"type": "mcp_toolset", "mcp_server_name": "supabase"},
                    {"type": "mcp_toolset", "mcp_server_name": "unusualwhales"},
                ],
            },
        }
        return json.dumps(payload, indent=2)

    if normalized == "codex":
        return "\n".join(
            [
                "[mcp_servers.supabase]",
                f'url = "{SUPABASE_MCP_URL}"',
                "",
                "[mcp_servers.unusualwhales]",
                f'url = "{UNUSUAL_WHALES_MCP_URL}"',
                'bearer_token_env_var = "UW_API_KEY"',
                "",
                "[mcp_servers.mongodb]",
                'command = "npx"',
                f'args = ["-y", "{MONGODB_NPX_PACKAGE}"]',
                "",
                "[mcp_servers.mongodb.env]",
                'MDB_MCP_API_CLIENT_ID = "YOUR_ATLAS_CLIENT_ID"',
                'MDB_MCP_API_CLIENT_SECRET = "YOUR_ATLAS_CLIENT_SECRET"',
                'MDB_MCP_READ_ONLY = "true"',
                "",
                "[mcp_servers.databento]",
                f'command = "{DATABENTO_MCP_COMMAND}"',
                "",
                "[mcp_servers.databento.env]",
                'DATABENTO_API_KEY = "YOUR_DATABENTO_API_KEY"',
            ]
        )

    if normalized == "openai-responses":
        payload = {
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "supabase",
                    "server_url": SUPABASE_MCP_URL,
                    "headers": {"Authorization": "Bearer YOUR_SUPABASE_ACCESS_TOKEN"},
                    "require_approval": "always",
                },
                {
                    "type": "mcp",
                    "server_label": "unusualwhales",
                    "server_url": UNUSUAL_WHALES_MCP_URL,
                    "headers": {"Authorization": "Bearer YOUR_UW_API_KEY"},
                    "require_approval": "always",
                },
            ]
        }
        return json.dumps(payload, indent=2)

    if normalized == "gemini-cli":
        payload = {
            "mcpServers": {
                "supabase": {"httpUrl": SUPABASE_MCP_URL},
                "unusualwhales": {
                    "httpUrl": UNUSUAL_WHALES_MCP_URL,
                    "headers": {"Authorization": "Bearer YOUR_UW_API_KEY"},
                },
                "mongodb": {
                    "command": "npx",
                    "args": ["-y", MONGODB_NPX_PACKAGE],
                    "env": {
                        "MDB_MCP_API_CLIENT_ID": "YOUR_ATLAS_CLIENT_ID",
                        "MDB_MCP_API_CLIENT_SECRET": "YOUR_ATLAS_CLIENT_SECRET",
                        "MDB_MCP_READ_ONLY": "true",
                    },
                },
                "databento": _databento_stdio(),
                "thetadata": {"url": THETADATA_MCP_SSE_URL, "timeout": 30000},
            }
        }
        return json.dumps(payload, indent=2)

    if normalized == "vscode":
        payload = {
            "inputs": [
                {
                    "type": "promptString",
                    "id": "uw-api-key",
                    "description": "Unusual Whales API Key",
                    "password": True,
                },
                {
                    "type": "promptString",
                    "id": "mdb-client-id",
                    "description": "MongoDB Atlas API Client ID",
                    "password": False,
                },
                {
                    "type": "promptString",
                    "id": "mdb-client-secret",
                    "description": "MongoDB Atlas API Client Secret",
                    "password": True,
                },
                {
                    "type": "promptString",
                    "id": "databento-api-key",
                    "description": "Databento API Key",
                    "password": True,
                },
            ],
            "servers": {
                "supabase": {"type": "http", "url": SUPABASE_MCP_URL},
                "unusualwhales": {
                    "type": "http",
                    "url": UNUSUAL_WHALES_MCP_URL,
                    "headers": {"Authorization": "Bearer ${input:uw-api-key}"},
                },
                "mongodb": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", MONGODB_NPX_PACKAGE],
                    "env": {
                        "MDB_MCP_API_CLIENT_ID": "${input:mdb-client-id}",
                        "MDB_MCP_API_CLIENT_SECRET": "${input:mdb-client-secret}",
                        "MDB_MCP_READ_ONLY": "true",
                    },
                },
                "databento": {"type": "stdio", **_databento_stdio("${input:databento-api-key}")},
                "thetadata": {"type": "sse", "url": THETADATA_MCP_SSE_URL},
            },
        }
        return json.dumps(payload, indent=2)

    if normalized == "windsurf":
        payload = {
            "mcpServers": {
                "supabase": {"serverUrl": SUPABASE_MCP_URL},
                "unusualwhales": {
                    "serverUrl": UNUSUAL_WHALES_MCP_URL,
                    "headers": {"Authorization": "Bearer YOUR_UW_API_KEY"},
                },
                "mongodb": {
                    "command": "npx",
                    "args": ["-y", MONGODB_NPX_PACKAGE],
                    "env": {
                        "MDB_MCP_API_CLIENT_ID": "YOUR_ATLAS_CLIENT_ID",
                        "MDB_MCP_API_CLIENT_SECRET": "YOUR_ATLAS_CLIENT_SECRET",
                        "MDB_MCP_READ_ONLY": "true",
                    },
                },
                "databento": _databento_stdio(),
                "thetadata": {"serverUrl": THETADATA_MCP_SSE_URL},
            }
        }
        return json.dumps(payload, indent=2)

    payload = {
        "context_servers": {
            "supabase": {
                "url": SUPABASE_MCP_URL,
                "headers": {"Authorization": "Bearer YOUR_SUPABASE_ACCESS_TOKEN"},
            },
            "unusualwhales": {
                "command": "npx",
                "args": ["-y", UNUSUAL_WHALES_NPX_PACKAGE],
                "env": {"UW_API_KEY": "YOUR_UW_API_KEY"},
            },
            "mongodb": {
                "command": "npx",
                "args": ["-y", MONGODB_NPX_PACKAGE],
                "env": {
                    "MDB_MCP_API_CLIENT_ID": "YOUR_ATLAS_CLIENT_ID",
                    "MDB_MCP_API_CLIENT_SECRET": "YOUR_ATLAS_CLIENT_SECRET",
                    "MDB_MCP_READ_ONLY": "true",
                },
            },
            "databento": _databento_stdio(),
        }
    }
    return json.dumps(payload, indent=2)
