# Multi-LLM & MCP Setup (Cursor, Anthropic, OpenAI, and All Major LLMs)

Account-level MCP and LLM provider reference for this workspace. **Do not commit API keys, connection strings, or service role tokens.**

## Quick checklist

| Server | Endpoint / Package | Auth | Smoke test |
|--------|-----------------------------|------|------------|
| **Supabase** | `https://mcp.supabase.com/mcp` | OAuth (interactive) or Bearer PAT (`SUPABASE_ACCESS_TOKEN`) | `list_projects` -> `list_tables` |
| **MongoDB** | `mongodb-mcp-server` (official plugin / package) | `MDB_MCP_*` env (see [`mcp-env.example`](mcp-env.example)) | `list-connections` -> `connect` -> `list-databases` |
| **Unusual Whales** | `https://api.unusualwhales.com/api/mcp` or `@unusualwhales/mcp` | Bearer API key (`UW_API_KEY`) or web OAuth (`mcp.unusualwhales.com` on ChatGPT/Claude/Grok web) | Flow / market read tool |
| **Theta Data** | `http://127.0.0.1:25503/mcp/sse`, served by Theta Terminal v3 on your machine | None in the client; the Terminal holds your Theta Data login | `/mcp` shows it connected, then an option Greeks request |
| **Databento** | `dbn-go-mcp-meta` binary from [NimbleMarkets/dbn-go](https://github.com/NimbleMarkets/dbn-go) (community, not published by Databento) | `DATABENTO_API_KEY` or `DATABENTO_API_KEY_FILE` | `list_datasets` -> `get_cost` |

**TrendSpider** is not listed because there is nothing to connect to: as of November 2026 it publishes no MCP server and no public data API.

For programmatic multi-LLM routing across **every major API** (45+ registered providers — Anthropic, OpenAI, Gemini, Grok, Cohere, Mistral, DeepSeek, Groq, Together, Fireworks, Perplexity, Cerebras, OpenRouter, Hugging Face, NVIDIA NIM, Moonshot/Kimi, Qwen/DashScope, GLM/Zhipu, Azure, Bedrock, watsonx, Vertex, Databricks, Snowflake Cortex, Vercel/Cloudflare gateways, and generic **vLLM / LM Studio / Ollama**), use the Poetry package in [`../multi-llm/`](../multi-llm/) (`poetry run multi-llm providers`) and key-free env template [`llm-env.example`](llm-env.example).

---

## 1. Cursor

1. **Supabase**: Enable the **Supabase** plugin under **Settings -> Tools & MCP** and click **Connect** (OAuth 2.1).
2. **MongoDB**: Enable **MongoDB (Self-Managed MCP)** and configure `MDB_MCP_*` in its server environment (see [`MONGODB_SETUP.md`](MONGODB_SETUP.md)).
3. **Unusual Whales**: Merge [`mcp.json.example`](mcp.json.example) into `~/.cursor/mcp.json`, replace `YOUR_API_KEY` via Cursor's secret UI or private config, and fully restart Cursor.
4. **Theta Data** and **Databento**: the `thetadata` and `databento` entries are in the same [`mcp.json.example`](mcp.json.example). See [Market data servers](#market-data-servers-theta-data-and-databento) for what each needs running first.

---

## 2. Anthropic (Claude Desktop, Claude Code, Claude.ai, Messages API)

### Claude Code CLI
Register the official remote and local MCP servers without committing secrets:

```bash
claude mcp add --transport http supabase https://mcp.supabase.com/mcp
claude mcp add --transport http unusualwhales https://api.unusualwhales.com/api/mcp \
  --header "Authorization: Bearer ${UW_API_KEY}"
claude mcp add mongodb -e MDB_MCP_API_CLIENT_ID="${MDB_MCP_API_CLIENT_ID}" \
  -e MDB_MCP_API_CLIENT_SECRET="${MDB_MCP_API_CLIENT_SECRET}" \
  -e MDB_MCP_READ_ONLY=true -- npx -y mongodb-mcp-server@3.3.0
claude mcp add databento -e DATABENTO_API_KEY="${DATABENTO_API_KEY}" -- dbn-go-mcp-meta
claude mcp add --transport sse thetadata http://127.0.0.1:25503/mcp/sse
```

### Claude Desktop
Merge [`clients/claude_desktop_config.json.example`](clients/claude_desktop_config.json.example) into:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

For Supabase and Unusual Whales in **Claude.ai Web / Desktop Integrations**, you can also connect via OAuth using `https://mcp.supabase.com/mcp` and `https://mcp.unusualwhales.com` ([Unusual Whales AI Apps](https://unusualwhales.com/ai-apps)).

### Anthropic Messages API (Remote MCP Connector)
Pass `anthropic-beta: mcp-client-2025-11-20` with matching `mcp_servers` and `mcp_toolset` entries pointing to `https://api.unusualwhales.com/api/mcp` (`authorization_token: UW_API_KEY`) and `https://mcp.supabase.com/mcp` (`authorization_token: SUPABASE_ACCESS_TOKEN`). The `multi-llm` package builds this payload automatically (`cd multi-llm && poetry run multi-llm chat --provider anthropic --attach-mcp uw,supabase ...`).

---

## 3. OpenAI (Codex, ChatGPT Connectors, Responses API & Agents SDK)

### OpenAI Codex CLI & Desktop
Codex reads `~/.codex/config.toml` (macOS/Linux, `chmod 600`) or `%USERPROFILE%\.codex\config.toml` (Windows). Copy from [`clients/codex_config.toml.example`](clients/codex_config.toml.example).

### ChatGPT Web / Desktop (Developer Mode / Connectors)
In ChatGPT **Settings -> Connectors**, connect via OAuth:
- **Supabase**: `https://mcp.supabase.com/mcp`
- **Unusual Whales**: `https://mcp.unusualwhales.com` (web OAuth flow)

### OpenAI Responses API & Agents SDK
Attach hosted MCP servers directly in `tools` (`type: "mcp"`, `server_url: "https://api.unusualwhales.com/api/mcp"`). Supported natively by `poetry run multi-llm chat --provider openai --attach-mcp uw,supabase ...`.

---

## 4. Every Other Major LLM Client, IDE & Provider Ecosystem

| Ecosystem / Client | Config / Integration Path | Template / Command |
|--------------------|---------------------------|--------------------|
| **Google Gemini CLI & SDK** | `~/.gemini/settings.json` (`httpUrl` + stdio `mcpServers`) | [`clients/gemini_settings.json.example`](clients/gemini_settings.json.example) |
| **xAI Grok** | Grok Web OAuth (`mcp.unusualwhales.com`) or xAI API (`XAI_API_KEY`) | `cd multi-llm && poetry run multi-llm chat --provider xai ...` |
| **VS Code / GitHub Copilot** | `.vscode/mcp.json` using encrypted `${input:...}` secret prompts | [`clients/vscode_mcp.json.example`](clients/vscode_mcp.json.example) |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` (`serverUrl` / stdio) | `cd multi-llm && poetry run multi-llm mcp-config --target windsurf` |
| **Zed** | `~/.config/zed/settings.json` (`context_servers`) | `cd multi-llm && poetry run multi-llm mcp-config --target zed` |
| **Mistral / Cohere / DeepSeek** | Native HTTPS chat APIs + MCP tool bridge | `MISTRAL_API_KEY`, `COHERE_API_KEY`, `DEEPSEEK_API_KEY` |
| **Groq / Together / Fireworks / Cerebras / Perplexity** | High-throughput OpenAI-compatible inference APIs | `GROQ_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `CEREBRAS_API_KEY`, `PERPLEXITY_API_KEY` |
| **OpenRouter (300+ LLMs: Llama, Qwen, Nova, etc.)** | Unified `openrouter.ai/api/v1` gateway | `OPENROUTER_API_KEY` (`cd multi-llm && poetry run multi-llm chat --provider openrouter`) |
| **Azure OpenAI & AWS Bedrock** | Enterprise cloud deployments (`api-key` / Converse API) | `AZURE_OPENAI_*`, `AWS_BEARER_TOKEN_BEDROCK` |
| **FinBERT (financial sentiment)** | Hugging Face Inference text classification, `ProsusAI/finbert` | `HF_TOKEN` (`cd multi-llm && poetry run multi-llm chat --provider finbert --prompt "..."`) |
| **Ollama / vLLM / LM Studio / SGLang** | Local open-weight models (`http://127.0.0.1:11434/v1`) | `OLLAMA_BASE_URL` / `OPENAI_COMPATIBLE_BASE_URL` |

---

## Market data servers: Theta Data and Databento

### Theta Data
The MCP server is part of Theta Terminal v3 ([Theta Data docs](https://www.thetadata.net/docs/Mcp/Getting-Started.html)). Start the Terminal (`java -jar ThetaTerminalv3.jar`, Java 21+, active subscription) before opening the client; the endpoint only exists while it runs.

It speaks SSE on loopback only, which decides where it can be used:

| Client | Theta Data entry |
|--------|------------------|
| Cursor | `"url"` |
| Claude Code | `claude mcp add --transport sse` |
| Gemini CLI | `"url"` (not `httpUrl`, which is for streamable HTTP) |
| VS Code | `"type": "sse"` |
| Windsurf | `"serverUrl"` |
| Claude Desktop, Codex, Zed | Not available: no SSE support in their config files |
| Anthropic / OpenAI APIs (`--attach-mcp`) | Not available: the provider cannot reach `127.0.0.1` on your machine |

### Databento
Databento does not publish an MCP server. The templates use `dbn-go-mcp-meta` from the community [dbn-go](https://github.com/NimbleMarkets/dbn-go) project (Apache-2.0). Install it yourself so the client can find it on `PATH`:

```bash
brew install NimbleMarkets/homebrew-tap/dbn-go
```

or take the binary for your platform from the [releases page](https://github.com/NimbleMarkets/dbn-go/releases) and check it against `checksums.txt`.

`dbn-go-mcp-meta` only reads metadata (datasets, schemas, symbology, cost estimates), so no tool call is billed. To let the model download market data, swap the command for `dbn-go-mcp-data` and cap spend per query:

```json
"databento": {
  "command": "dbn-go-mcp-data",
  "args": ["--max-cost", "1.00"],
  "env": { "DATABENTO_API_KEY": "YOUR_DATABENTO_API_KEY" }
}
```

Its `fetch_range` tool is charged to your Databento account. `--max-cost` limits each query, not the total.

---

## Universal instruments (repo plugin)

Multi-asset routing for equities, options, forex, bonds, crypto, and **prediction markets** (Kalshi + Polymarket read-only):

1. Build prediction MCP once: `cd agent-tools/universal-instruments-plugin/mcp/prediction-markets && npm install && npm run build`
2. Optional IB bridge: `pip install -r agent-tools/universal-instruments-plugin/mcp/ib-lean-bridge/requirements.txt` (requires IB Gateway/TWS).
3. Merge **`prediction-markets`** and **`ib-lean-bridge`** from [`mcp.json.example`](mcp.json.example) into your user MCP config, or install the plugin from [`agent-tools/universal-instruments-plugin/README.md`](../agent-tools/universal-instruments-plugin/README.md).
4. Enable skill **instrument-router** / rule **instrument-routing** when asking cross-asset questions.
5. MCP health and IB setup: [`docs/MCP_HEALTH_CHECKLIST.md`](../agent-tools/universal-instruments-plugin/docs/MCP_HEALTH_CHECKLIST.md), [`docs/IB_LEAN_SETUP.md`](../agent-tools/universal-instruments-plugin/docs/IB_LEAN_SETUP.md).

---

## Governance & Troubleshooting

- **Vendor servers first**: Supabase (`mcp.supabase.com`), MongoDB (`mongodb-mcp-server`), Unusual Whales (`api.unusualwhales.com/api/mcp` or `@unusualwhales/mcp`), and Theta Data (Theta Terminal v3) are published by the vendor. Databento is the one exception, because Databento has no server of its own; review [dbn-go](https://github.com/NimbleMarkets/dbn-go) before giving it your API key. `prediction-markets` and `ib-lean-bridge` are not vendor servers either: they run from source in this repository (`agent-tools/universal-instruments-plugin/mcp/`). Do not invoke unvetted shadow `npx @modelcontextprotocol/server-*` wrappers.
- **Endpoint disambiguation**: Developer API-key setups use `https://api.unusualwhales.com/api/mcp` (not the documentation URL `.../public-api/mcp`). Interactive web OAuth apps (ChatGPT, Claude.ai, Grok web) use `https://mcp.unusualwhales.com`.
- **Client restarts**: Fully quit and reopen desktop clients (Cursor, Claude Desktop, Codex, Windsurf) after editing MCP JSON/TOML or environment variables.
