# MongoDB MCP credentials (Cursor)

The MongoDB MCP server in this agent session is **installed** (`connect`, `list-databases`, etc.). Credentials are **not** in the cloud VM shell until you configure them in **your** Cursor client.

## Recommended: Option B (Atlas service account)

1. In [Atlas](https://cloud.mongodb.com), create an **API service account** with appropriate access.
2. Add your IP to the **API Access List** if required.
3. In Cursor: **Settings → Tools & MCP → mongodb → Environment** set:
   - `MDB_MCP_API_CLIENT_ID`
   - `MDB_MCP_API_CLIENT_SECRET`
4. Optionally `MDB_MCP_READ_ONLY=true`
5. **Restart Cursor** fully.

See [`mcp-env.example`](mcp-env.example) for Option A (connection string) and Option C (Atlas Local).

## Verify

After restart, ask the agent to run:

1. `list-connections` — should show an active connection if the server auto-connected, **or**
2. `connect` with `connectionString` (if using ad-hoc string in chat — prefer env-based setup),
3. `list-databases` with the returned `connectionId`.

Full skill: [mongodb-mcp-setup](https://github.com/mongodb/agent-skills/blob/main/skills/mongodb-mcp-setup/SKILL.md).