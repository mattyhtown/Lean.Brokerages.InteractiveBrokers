# `qc-multi-llm` (Poetry)

Poetry-managed, stdlib-only CLI and library that registers **every major commercial LLM API** used with MCP-style agent workflows: Anthropic, OpenAI, Google Gemini, xAI Grok, Cohere, plus **40+ additional** cloud, regional, enterprise, gateway, and local OpenAI-compatible providers.

Run `poetry run multi-llm providers` (or `--json`) for the live registry, default models, env var names, and configured/missing-key status.

## Provider coverage (summary)

| Category | Provider IDs (sample) |
|----------|----------------------|
| **Frontier** | `anthropic`, `openai`, `gemini`, `xai`, `cohere` |
| **US / global inference** | `mistral`, `deepseek`, `groq`, `together`, `fireworks`, `perplexity`, `cerebras`, `openrouter`, `huggingface`, `nvidia_nim`, `sambanova`, `deepinfra`, `anyscale`, `lambda`, `friendli`, `nebius`, `hyperbolic`, `novita`, `writer`, `reka`, `ai21`, `github_models` |
| **Asia-Pacific** | `moonshot`, `dashscope`, `zhipu`, `minimax`, `yi`, `baichuan`, `siliconflow`, `tencent_hunyuan` |
| **Enterprise cloud** | `azure_openai`, `bedrock`, `watsonx`, `vertex_openai`, `databricks`, `snowflake_cortex` |
| **Gateways & hosting** | `vercel_ai_gateway`, `cloudflare_ai`, `baseten`, `lepton` |
| **Local / catch-all** | `ollama`, `openai_compatible` (vLLM, LM Studio, SGLang, TGI, custom) |

**Native remote MCP** on API calls: `anthropic` (`mcp_servers`) and `openai` (Responses `mcp` tools). All other providers use MCP through the host client (Cursor, Claude, Codex, Gemini CLI, etc.).

Environment templates: [`../.cursor/llm-env.example`](../.cursor/llm-env.example).

## Install & Test (Poetry)

```bash
cd multi-llm
poetry install
poetry run pytest
```

## CLI Usage

```bash
poetry run multi-llm providers
poetry run multi-llm providers --json

poetry run multi-llm mcp-config --target cursor
poetry run multi-llm mcp-config --target claude-code

poetry run multi-llm chat --provider anthropic --attach-mcp uw --prompt "Summarize SPY options flow"
poetry run multi-llm fanout --providers anthropic,openai,gemini,xai,moonshot,dashscope --prompt "Compare macro risk"
poetry run multi-llm fanout --all-configured --prompt "Quick checklist for 0DTE entry gates"
```

Aliases: `claude`, `gpt`, `chatgpt`, `grok`, `kimi`, `qwen`, `glm`, `hf`, `vllm`, `lmstudio`, `vertex`, `snowflake`, etc. (`poetry run multi-llm chat --provider kimi` → Moonshot).