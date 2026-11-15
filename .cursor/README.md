# Cursor MCP setup (Supabase + MongoDB + Unusual Whales)

Account-level configuration for this workspace. **Do not commit API keys, connection strings, or service role tokens.**

## Quick checklist

| Server | Install | Auth | Smoke test |
|--------|---------|------|------------|
| **Supabase** | Supabase plugin | OAuth (**Connect** in Settings → Tools & MCP) | `list_projects` → `list_tables` |
| **MongoDB** | MongoDB (Self-Managed MCP) plugin | `MDB_MCP_*` on the **mongodb** server (see [`mcp-env.example`](mcp-env.example)) | `list-connections` → `connect` → `list-databases` |
| **Unusual Whales** | Merge [`mcp.json.example`](mcp.json.example) into `~/.cursor/mcp.json` or Cursor MCP UI | Bearer API key | Flow / market read tool after restart |

**After any MCP JSON or env change:** fully quit and reopen Cursor.

## Supabase (plugin + OAuth)

1. Install the **Supabase** plugin; confirm **https://mcp.supabase.com/mcp** under Tools & MCP.
2. Reachability check: `curl -so /dev/null -w "%{http_code}" https://mcp.supabase.com/mcp` → **401** is expected without auth.
3. In Cursor: **Connect** next to Supabase or run Supabase **`mcp_auth`** when prompted.
4. Verify: agent calls **`list_projects`**, then **`list_tables`** with a `project_id`.

Docs: [Supabase MCP guide](https://supabase.com/docs/guides/getting-started/mcp).

## MongoDB (plugin + env)

Walkthrough: [mongodb-mcp-setup](https://github.com/mongodb/agent-skills/blob/main/skills/mongodb-mcp-setup/SKILL.md).

1. Install **MongoDB (Self-Managed MCP)** plugin.
2. Choose one auth path (templates in [`mcp-env.example`](mcp-env.example)):
   - **A** `MDB_MCP_CONNECTION_STRING`
   - **B** `MDB_MCP_API_CLIENT_ID` + `MDB_MCP_API_CLIENT_SECRET` (+ Atlas API access list IP)
   - **C** Atlas Local (Docker), no env
3. Set variables in **Cursor → Settings → Tools & MCP → mongodb → Environment**, or in `~/.mcp-env` (chmod 600). The agent does not handle secret values.
4. Restart Cursor.
5. Verify: **`list-connections`** (or **`connect`** with connection string) then **`list-databases`**.

Governance: use the official MongoDB plugin only; avoid ad-hoc `npx @modelcontextprotocol/server-*` wrappers.

## Unusual Whales (hosted MCP)

**Use the API MCP endpoint**, not the docs page or ChatGPT OAuth host:

- Correct: `https://api.unusualwhales.com/api/mcp`
- Not for Cursor API-key setup: `https://mcp.unusualwhales.com/...` (OAuth for other AI apps)

1. API key: [unusualwhales.com/settings/api-dashboard](https://unusualwhales.com/settings/api-dashboard).
2. Merge **`mcp.json.example`** into user config; prefer Cursor **secrets UI** over plaintext JSON in git.
3. Use **either** hosted `unusualwhales` **or** local `unusualwhales-local` (`npx -y @unusualwhales/mcp` + `UW_API_KEY`), not both unless intentional.
4. Restart Cursor; confirm green/connected under Tools & MCP.
5. Smoke prompts: unusual NVDA options flow; market state / sector sentiment.

Optional local plugin on this machine: `~/.cursor/plugins/local/unusual-whales-mcp/`.

## Universal instruments (repo plugin)

Multi-asset routing for equities, options, forex, bonds, crypto, and **prediction markets** (Kalshi + Polymarket read-only):

1. Build prediction MCP once: `cd agent-tools/universal-instruments-plugin/mcp/prediction-markets && npm install && npm run build`
2. Optional IB bridge: `pip install -r agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge/requirements.txt` (requires IB Gateway/TWS).
3. Merge **`prediction-markets`** and **`ib-lean-bridge`** from [`mcp.json.example`](mcp.json.example) into your user MCP config, or install the plugin from [`agent-tools/universal-instruments-plugin/README.md`](../agent-tools/universal-instruments-plugin/README.md).
4. Enable skill **instrument-router** / rule **instrument-routing** when asking cross-asset questions.
5. MCP health and IB setup: [`docs/MCP_HEALTH_CHECKLIST.md`](../agent-tools/universal-instruments-plugin/docs/MCP_HEALTH_CHECKLIST.md), [`docs/IB_LEAN_SETUP.md`](../agent-tools/universal-instruments-plugin/docs/IB_LEAN_SETUP.md).

## Single user `mcp.json` merge

Combine Unusual Whales with any other URL servers you already have. Supabase and MongoDB are normally provided by **plugins**, not duplicated in `mcp.json`.

```json
{
  "mcpServers": {
    "unusualwhales": {
      "type": "url",
      "url": "https://api.unusualwhales.com/api/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Supabase tools missing | Re-auth via Connect / `mcp_auth` |
| MongoDB “no active connections” | Set `MDB_MCP_*` on mongodb server; restart |
| UW 401 | Regenerate key; check subscription |
| UW tools missing | Fix JSON; full restart; URL must be `api.unusualwhales.com/api/mcp` |

## Repo scope

These files are **examples** for QuantConnect IB development. They do not change brokerage runtime code. Cloud Agents use the **user’s** Cursor MCP settings on their machine.