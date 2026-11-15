# MongoDB MCP Credentials (Cursor, Anthropic, OpenAI Codex, and Other Clients)

The official MongoDB MCP server (`mongodb-mcp-server`) supports three authentication modes across all MCP clients. Credentials belong only in your private client config or `~/.mcp-env` (`chmod 600`) — never in git.

## Auth Options

1. **Option A — Connection String**: Set `MDB_MCP_CONNECTION_STRING` (`mongodb://` or `mongodb+srv://`) for direct single-cluster access.
2. **Option B — Atlas Service Account (Recommended for Atlas)**: Create an API service account in [MongoDB Atlas](https://cloud.mongodb.com), allowlist your IP if required, and set `MDB_MCP_API_CLIENT_ID` + `MDB_MCP_API_CLIENT_SECRET`.
3. **Option C — Atlas Local**: Run local MongoDB Atlas via Docker with zero cloud credentials.

Optionally set `MDB_MCP_READ_ONLY=true` to enforce read-only operations. See [`mcp-env.example`](mcp-env.example).

## Where to Set Credentials by LLM Client

- **Cursor**: **Settings -> Tools & MCP -> mongodb -> Environment**, then restart Cursor.
- **Anthropic Claude Code**: `claude mcp add mongodb -e MDB_MCP_API_CLIENT_ID=... -e MDB_MCP_API_CLIENT_SECRET=... -- npx -y mongodb-mcp-server@latest`
- **Anthropic Claude Desktop**: Add under `mcpServers.mongodb.env` in `claude_desktop_config.json` (see [`clients/claude_desktop_config.json.example`](clients/claude_desktop_config.json.example)).
- **OpenAI Codex**: Add under `[mcp_servers.mongodb.env]` in `~/.codex/config.toml` (see [`clients/codex_config.toml.example`](clients/codex_config.toml.example)).
- **Google Gemini CLI**: Add under `mcpServers.mongodb.env` in `~/.gemini/settings.json` (see [`clients/gemini_settings.json.example`](clients/gemini_settings.json.example)).
- **VS Code / GitHub Copilot**: Use `${input:...}` secret prompts in `.vscode/mcp.json` (see [`clients/vscode_mcp.json.example`](clients/vscode_mcp.json.example)).

## Verify

Ask the agent in your client to run:
1. `list-connections` (or `connect` when supplying a connection string),
2. `list-databases` with the active `connectionId`.

Reference: [mongodb-mcp-setup skill](https://github.com/mongodb/agent-skills/blob/main/skills/mongodb-mcp-setup/SKILL.md).
