# `qc-multi-llm` (Poetry)

Poetry-managed, zero-runtime-dependency Python package and CLI that unifies **Anthropic**, **OpenAI**, **Google Gemini**, **xAI Grok**, and **12+ additional cloud, enterprise, and local LLM providers**, plus key-free **MCP configuration generation** for Supabase, MongoDB, and Unusual Whales.

## Supported LLM Providers

| Provider ID | Ecosystem | Default Model | Auth Env Var(s) | Native Remote MCP |
|-------------|-----------|---------------|-----------------|-------------------|
| `anthropic` | Anthropic Claude | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` | Yes (`mcp_servers`) |
| `openai` | OpenAI | `gpt-4.1` | `OPENAI_API_KEY` | Yes (Responses `mcp`) |
| `gemini` | Google Gemini | `gemini-2.5-pro` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Via Gemini CLI / host |
| `xai` | xAI Grok | `grok-4` | `XAI_API_KEY` | Via Grok web / host |
| `mistral` | Mistral AI | `mistral-large-latest` | `MISTRAL_API_KEY` | Via MCP host |
| `cohere` | Cohere | `command-a-03-2025` | `COHERE_API_KEY` | Via MCP host |
| `deepseek` | DeepSeek | `deepseek-chat` | `DEEPSEEK_API_KEY` | Via MCP host |
| `groq` | Groq | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | Via MCP host |
| `together` | Together AI | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | `TOGETHER_API_KEY` | Via MCP host |
| `fireworks` | Fireworks AI | `accounts/fireworks/models/llama-v3p3-70b-instruct` | `FIREWORKS_API_KEY` | Via MCP host |
| `perplexity` | Perplexity Sonar | `sonar-pro` | `PERPLEXITY_API_KEY` | Via MCP host |
| `cerebras` | Cerebras | `llama-3.3-70b` | `CEREBRAS_API_KEY` | Via MCP host |
| `openrouter` | OpenRouter (300+ models) | `anthropic/claude-sonnet-4.5` | `OPENROUTER_API_KEY` | Via MCP host |
| `azure_openai` | Azure OpenAI | `gpt-4.1` | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` | Via MCP host |
| `bedrock` | AWS Bedrock Converse | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | `AWS_BEARER_TOKEN_BEDROCK` (`AWS_REGION`) | Via MCP host |
| `ollama` | Local Ollama / vLLM / LM Studio | `llama3.2` | `OLLAMA_BASE_URL` / `OPENAI_COMPATIBLE_BASE_URL` | Via MCP host |

See [`../.cursor/llm-env.example`](../.cursor/llm-env.example) for key-free environment templates.

## Install & Test (Poetry)

```bash
cd multi-llm
poetry install
poetry run pytest
```

## CLI Usage

```bash
# Inspect registered providers and whether env keys are present (values are never printed)
poetry run multi-llm providers

# Generate key-free MCP configs (Supabase + MongoDB + Unusual Whales) for any client or API
poetry run multi-llm mcp-config --target cursor
poetry run multi-llm mcp-config --target claude-desktop
poetry run multi-llm mcp-config --target claude-code
poetry run multi-llm mcp-config --target anthropic-api
poetry run multi-llm mcp-config --target codex
poetry run multi-llm mcp-config --target openai-responses
poetry run multi-llm mcp-config --target gemini-cli
poetry run multi-llm mcp-config --target vscode

# Single-provider completion (with optional remote MCP attachment on Anthropic / OpenAI)
poetry run multi-llm chat --provider anthropic --attach-mcp uw --prompt "Summarize SPY options flow"
poetry run multi-llm chat --provider openai --attach-mcp uw,supabase --prompt "Check market state"

# Side-by-side fanout across Anthropic, OpenAI, Gemini, Grok, or all configured LLMs
poetry run multi-llm fanout --providers anthropic,openai,gemini,xai --prompt "Compare 0DTE skew regimes"
poetry run multi-llm fanout --all-configured --prompt "Give a concise macro risk checklist"
```
