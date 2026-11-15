# `qc-multi-llm` (Poetry)

Poetry-managed, stdlib-only CLI and library that registers **every major commercial LLM API** used with MCP-style agent workflows: Anthropic, OpenAI, Google Gemini, xAI Grok, Cohere, plus **40+ additional** cloud, regional, enterprise, gateway, and local OpenAI-compatible providers.

Run `poetry run multi-llm providers` (or `--json`) for the live registry, default models, env var names, and configured/missing-key status.

## Provider coverage (summary)

| Category | Provider IDs (sample) |
|----------|----------------------|
| **Frontier** | `anthropic`, `openai`, `gemini`, `xai`, `cohere` |
| **US / global inference** | `mistral`, `deepseek`, `groq`, `together`, `fireworks`, `perplexity`, `cerebras`, `openrouter`, `huggingface`, `nvidia_nim`, `sambanova`, `deepinfra`, `friendli`, `nebius`, `hyperbolic`, `novita`, `writer`, `reka`, `ai21` |
| **Asia-Pacific** | `moonshot`, `dashscope`, `zhipu`, `minimax`, `yi`, `baichuan`, `siliconflow`, `tencent_hunyuan` |
| **Enterprise cloud** | `azure_openai`, `bedrock`, `watsonx`, `vertex_openai`, `databricks`, `snowflake_cortex` |
| **Gateways & hosting** | `vercel_ai_gateway`, `cloudflare_ai`, `baseten`, `lepton` |
| **Local / catch-all** | `ollama`, `openai_compatible` (vLLM, LM Studio, SGLang, TGI, custom) |
| **Sentiment classifier** | `finbert` ([ProsusAI/finbert](https://huggingface.co/ProsusAI/finbert) on Hugging Face Inference) |

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

### FinBERT sentiment

`finbert` is a classifier, not a chat model. The prompt is the text to score, and the reply is the label scores, highest first:

```bash
export HF_TOKEN=hf_...
poetry run multi-llm chat --provider finbert --prompt "Shares surged after the company raised full-year guidance."
# positive 0.948, neutral 0.036, negative 0.016   (illustrative scores)
```

- `--model` selects another Hugging Face text-classification model, for example `yiyanghkust/finbert-tone`.
- `--system`, `--max-tokens`, `--temperature` and `--attach-mcp` do not apply to it.
- `fanout --all-configured` skips it, so a question sent to every chat model is not also scored for sentiment. Name it in `--providers` to include it.

### IBM watsonx.ai

`watsonx` calls the native chat endpoint (`/ml/v1/text/chat`), which takes an IAM bearer token and a project id rather than the IBM Cloud API key itself. Exchange the API key for a token (valid for about an hour) and export both:

```bash
export WATSONX_ACCESS_TOKEN=$(curl -s https://iam.cloud.ibm.com/identity/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=urn:ibm:params:oauth:grant-type:apikey" \
  --data-urlencode "apikey=$IBM_CLOUD_API_KEY" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])")
export WATSONX_PROJECT_ID=...
poetry run multi-llm chat --provider watsonx --prompt "Summarize today's macro risk"
```

`WATSONX_URL` is optional and selects the region host (default `https://us-south.ml.cloud.ibm.com`).

### MiniMax

`minimax` calls the international host, `https://api.minimax.io/v1`, with `MiniMax-M3.1-Flash`. A key from the mainland China platform only works on that platform's host:

```bash
export MINIMAX_BASE_URL=https://api.minimax.cn/v1
```

Aliases: `claude`, `gpt`, `chatgpt`, `grok`, `kimi`, `qwen`, `glm`, `hf`, `vllm`, `lmstudio`, `vertex`, `snowflake`, etc. (`poetry run multi-llm chat --provider kimi` → Moonshot).