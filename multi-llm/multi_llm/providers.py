from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


# Sentiment classifiers return label scores, not generated text.
CLASSIFICATION_PROTOCOL = "hf_text_classification"
WATSONX_PROTOCOL = "watsonx_chat"
WATSONX_PROJECT_ENV = "WATSONX_PROJECT_ID"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    protocol: str
    default_model: str
    base_url: str
    api_key_envs: tuple[str, ...]
    endpoint_env: str | None = None
    deployment_env: str | None = None
    supports_remote_mcp: bool = False
    allow_unauthenticated: bool = False


def _chat(
    name: str,
    display_name: str,
    default_model: str,
    base_url: str,
    *api_key_envs: str,
    endpoint_env: str | None = None,
    allow_unauthenticated: bool = False,
) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        display_name=display_name,
        protocol="openai_chat",
        default_model=default_model,
        base_url=base_url,
        api_key_envs=api_key_envs,
        endpoint_env=endpoint_env,
        allow_unauthenticated=allow_unauthenticated,
    )


PROVIDERS: dict[str, ProviderSpec] = {
    # --- Frontier (native APIs) ---
    "anthropic": ProviderSpec(
        name="anthropic",
        display_name="Anthropic Claude",
        protocol="anthropic_messages",
        default_model="claude-sonnet-4-5",
        base_url="https://api.anthropic.com/v1/messages",
        api_key_envs=("ANTHROPIC_API_KEY",),
        supports_remote_mcp=True,
    ),
    "openai": ProviderSpec(
        name="openai",
        display_name="OpenAI",
        protocol="openai_responses",
        default_model="gpt-4.1",
        base_url="https://api.openai.com/v1",
        api_key_envs=("OPENAI_API_KEY",),
        supports_remote_mcp=True,
    ),
    "gemini": ProviderSpec(
        name="gemini",
        display_name="Google Gemini (AI Studio)",
        protocol="google_gemini",
        default_model="gemini-2.5-pro",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ),
    "xai": ProviderSpec(
        name="xai",
        display_name="xAI Grok",
        protocol="openai_chat",
        default_model="grok-4",
        base_url="https://api.x.ai/v1",
        api_key_envs=("XAI_API_KEY",),
    ),
    "cohere": ProviderSpec(
        name="cohere",
        display_name="Cohere",
        protocol="cohere_v2",
        default_model="command-a-03-2025",
        base_url="https://api.cohere.com/v2/chat",
        api_key_envs=("COHERE_API_KEY",),
    ),
    # --- Major cloud & inference ---
    "mistral": _chat("mistral", "Mistral AI", "mistral-large-latest", "https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "deepseek": _chat("deepseek", "DeepSeek", "deepseek-chat", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "groq": _chat("groq", "Groq", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "together": _chat(
        "together",
        "Together AI",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "https://api.together.xyz/v1",
        "TOGETHER_API_KEY",
    ),
    "fireworks": _chat(
        "fireworks",
        "Fireworks AI",
        "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "https://api.fireworks.ai/inference/v1",
        "FIREWORKS_API_KEY",
    ),
    "perplexity": _chat("perplexity", "Perplexity Sonar", "sonar-pro", "https://api.perplexity.ai", "PERPLEXITY_API_KEY"),
    "cerebras": _chat("cerebras", "Cerebras", "llama-3.3-70b", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "openrouter": _chat(
        "openrouter",
        "OpenRouter (unified 300+ models)",
        "anthropic/claude-sonnet-4.5",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
    ),
    "huggingface": _chat(
        "huggingface",
        "Hugging Face Inference",
        "meta-llama/Llama-3.3-70B-Instruct",
        "https://router.huggingface.co/v1",
        "HF_TOKEN",
        "HUGGINGFACE_API_KEY",
    ),
    "nvidia_nim": _chat(
        "nvidia_nim",
        "NVIDIA NIM",
        "meta/llama-3.1-70b-instruct",
        "https://integrate.api.nvidia.com/v1",
        "NVIDIA_API_KEY",
    ),
    "sambanova": _chat(
        "sambanova",
        "SambaNova",
        "Meta-Llama-3.3-70B-Instruct",
        "https://api.sambanova.ai/v1",
        "SAMBANOVA_API_KEY",
    ),
    "deepinfra": _chat(
        "deepinfra",
        "DeepInfra",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "https://api.deepinfra.com/v1/openai",
        "DEEPINFRA_API_KEY",
    ),
    "anyscale": _chat(
        "anyscale",
        "Anyscale Endpoints",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "https://api.endpoints.anyscale.com/v1",
        "ANYSCALE_API_KEY",
    ),
    "lambda": _chat(
        "lambda",
        "Lambda Labs",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "https://api.lambdalabs.com/v1",
        "LAMBDA_API_KEY",
    ),
    "friendli": _chat(
        "friendli",
        "FriendliAI",
        "meta-llama-3.1-70b-instruct",
        "https://api.friendli.ai/v1",
        "FRIENDLI_API_KEY",
    ),
    "nebius": _chat(
        "nebius",
        "Nebius Token Factory",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "https://api.studio.nebius.com/v1",
        "NEBIUS_API_KEY",
    ),
    "hyperbolic": _chat(
        "hyperbolic",
        "Hyperbolic",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "https://api.hyperbolic.xyz/v1",
        "HYPERBOLIC_API_KEY",
    ),
    "novita": _chat(
        "novita",
        "Novita AI",
        "meta-llama/llama-3.3-70b-instruct",
        "https://api.novita.ai/v3/openai",
        "NOVITA_API_KEY",
    ),
    "writer": _chat("writer", "Writer", "palmyra-x-004", "https://api.writer.com/v1", "WRITER_API_KEY"),
    "reka": _chat("reka", "Reka", "reka-flash", "https://api.reka.ai/v1", "REKA_API_KEY"),
    "ai21": _chat("ai21", "AI21 Labs", "jamba-large", "https://api.ai21.com/studio/v1", "AI21_API_KEY"),
    # --- China / Asia-Pacific APIs ---
    "moonshot": _chat(
        "moonshot",
        "Moonshot (Kimi)",
        "moonshot-v1-128k",
        "https://api.moonshot.cn/v1",
        "MOONSHOT_API_KEY",
    ),
    "dashscope": _chat(
        "dashscope",
        "Alibaba DashScope (Qwen)",
        "qwen-max",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
    ),
    "zhipu": _chat(
        "zhipu",
        "Zhipu GLM",
        "glm-4-plus",
        "https://open.bigmodel.cn/api/paas/v4",
        "ZHIPU_API_KEY",
    ),
    "minimax": _chat(
        "minimax",
        "MiniMax",
        "abab6.5s-chat",
        "https://api.minimax.chat/v1",
        "MINIMAX_API_KEY",
    ),
    "yi": _chat(
        "yi",
        "01.AI (Yi)",
        "yi-large",
        "https://api.lingyiwanwu.com/v1",
        "YI_API_KEY",
    ),
    "baichuan": _chat(
        "baichuan",
        "Baichuan",
        "Baichuan4",
        "https://api.baichuan-ai.com/v1",
        "BAICHUAN_API_KEY",
    ),
    "siliconflow": _chat(
        "siliconflow",
        "SiliconFlow",
        "deepseek-ai/DeepSeek-V3",
        "https://api.siliconflow.cn/v1",
        "SILICONFLOW_API_KEY",
    ),
    "tencent_hunyuan": _chat(
        "tencent_hunyuan",
        "Tencent Hunyuan",
        "hunyuan-pro",
        "https://api.hunyuan.cloud.tencent.com/v1",
        "TENCENT_HUNYUAN_API_KEY",
    ),
    # --- Enterprise & cloud-managed ---
    "azure_openai": ProviderSpec(
        name="azure_openai",
        display_name="Azure OpenAI",
        protocol="azure_openai",
        default_model="gpt-4.1",
        base_url="",
        api_key_envs=("AZURE_OPENAI_API_KEY",),
        endpoint_env="AZURE_OPENAI_ENDPOINT",
        deployment_env="AZURE_OPENAI_DEPLOYMENT",
    ),
    "bedrock": ProviderSpec(
        name="bedrock",
        display_name="AWS Bedrock Converse",
        protocol="aws_bedrock",
        default_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        base_url="",
        api_key_envs=("AWS_BEARER_TOKEN_BEDROCK",),
        endpoint_env="AWS_REGION",
    ),
    "watsonx": ProviderSpec(
        name="watsonx",
        display_name="IBM watsonx.ai",
        protocol=WATSONX_PROTOCOL,
        default_model="ibm/granite-3-8b-instruct",
        base_url="https://us-south.ml.cloud.ibm.com",
        api_key_envs=("WATSONX_ACCESS_TOKEN",),
        endpoint_env="WATSONX_URL",
    ),
    "vertex_openai": _chat(
        "vertex_openai",
        "Google Vertex AI (OpenAI-compat)",
        "google/gemini-2.5-pro",
        "",
        "GOOGLE_CLOUD_ACCESS_TOKEN",
        "GCP_ACCESS_TOKEN",
        endpoint_env="VERTEX_OPENAI_BASE_URL",
    ),
    "databricks": _chat(
        "databricks",
        "Databricks Model Serving",
        "databricks-meta-llama-3-3-70b-instruct",
        "",
        "DATABRICKS_TOKEN",
        endpoint_env="DATABRICKS_BASE_URL",
    ),
    "snowflake_cortex": _chat(
        "snowflake_cortex",
        "Snowflake Cortex",
        "mistral-large2",
        "",
        "SNOWFLAKE_TOKEN",
        endpoint_env="SNOWFLAKE_CORTEX_BASE_URL",
    ),
    # --- Gateways & hosting platforms ---
    "vercel_ai_gateway": _chat(
        "vercel_ai_gateway",
        "Vercel AI Gateway",
        "openai/gpt-4.1",
        "https://ai-gateway.vercel.sh/v1",
        "VERCEL_AI_GATEWAY_API_KEY",
        "AI_GATEWAY_API_KEY",
        endpoint_env="VERCEL_AI_GATEWAY_BASE_URL",
    ),
    "cloudflare_ai": _chat(
        "cloudflare_ai",
        "Cloudflare Workers AI (OpenAI-compat)",
        "@cf/meta/llama-3.3-70b-instruct",
        "",
        "CLOUDFLARE_API_TOKEN",
        endpoint_env="CLOUDFLARE_AI_BASE_URL",
    ),
    "baseten": _chat(
        "baseten",
        "Baseten",
        "llama-3.3-70b",
        "",
        "BASETEN_API_KEY",
        endpoint_env="BASETEN_BASE_URL",
    ),
    "lepton": _chat(
        "lepton",
        "Lepton AI",
        "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "",
        "LEPTON_API_KEY",
        endpoint_env="LEPTON_BASE_URL",
    ),
    # --- Classifiers (not chat models) ---
    "finbert": ProviderSpec(
        name="finbert",
        display_name="FinBERT financial sentiment (Hugging Face Inference)",
        protocol=CLASSIFICATION_PROTOCOL,
        default_model="ProsusAI/finbert",
        base_url="https://router.huggingface.co/hf-inference/models",
        api_key_envs=("HF_TOKEN", "HUGGINGFACE_API_KEY"),
    ),
    # --- Local & catch-all ---
    "ollama": ProviderSpec(
        name="ollama",
        display_name="Ollama",
        protocol="openai_chat",
        default_model="llama3.2",
        base_url="http://127.0.0.1:11434/v1",
        api_key_envs=("OPENAI_COMPATIBLE_API_KEY",),
        endpoint_env="OLLAMA_BASE_URL",
        allow_unauthenticated=True,
    ),
    "openai_compatible": ProviderSpec(
        name="openai_compatible",
        display_name="Any OpenAI-compatible server (vLLM, LM Studio, SGLang, TGI, …)",
        protocol="openai_chat",
        default_model="default",
        base_url="http://127.0.0.1:8000/v1",
        api_key_envs=("OPENAI_COMPATIBLE_API_KEY",),
        endpoint_env="OPENAI_COMPATIBLE_BASE_URL",
        allow_unauthenticated=True,
    ),
}

# All registered provider ids (single source of truth for tests/docs).
ALL_PROVIDER_NAMES: tuple[str, ...] = tuple(sorted(PROVIDERS.keys()))

PROVIDER_ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "chatgpt": "openai",
    "google": "gemini",
    "grok": "xai",
    "x": "xai",
    "azure": "azure_openai",
    "aws": "bedrock",
    "local": "openai_compatible",
    "vllm": "openai_compatible",
    "lmstudio": "openai_compatible",
    "lm_studio": "openai_compatible",
    "sglang": "openai_compatible",
    "tgi": "openai_compatible",
    "kimi": "moonshot",
    "qwen": "dashscope",
    "glm": "zhipu",
    "hf": "huggingface",
    "hugging_face": "huggingface",
    "nvidia": "nvidia_nim",
    "nim": "nvidia_nim",
    "vercel": "vercel_ai_gateway",
    "cloudflare": "cloudflare_ai",
    "watson": "watsonx",
    "vertex": "vertex_openai",
    "snowflake": "snowflake_cortex",
}


def get_provider(name: str) -> ProviderSpec:
    normalized = name.strip().lower().replace("-", "_")
    canonical = PROVIDER_ALIASES.get(normalized, normalized)
    if canonical not in PROVIDERS:
        known = ", ".join(ALL_PROVIDER_NAMES)
        raise ValueError(f"Unknown provider '{name}'. Supported providers ({len(PROVIDERS)}): {known}")
    return PROVIDERS[canonical]


def resolve_api_key(spec: ProviderSpec, env: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if env is None else env
    for var_name in spec.api_key_envs:
        val = source.get(var_name, "").strip()
        if val:
            return val
    return None


def is_provider_configured(spec: ProviderSpec, env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    if spec.protocol == "azure_openai":
        endpoint = source.get(spec.endpoint_env or "", "").strip()
        deployment = source.get(spec.deployment_env or "", "").strip()
        return bool(endpoint and deployment and resolve_api_key(spec, source))
    if spec.name == "ollama":
        return True
    if spec.protocol == WATSONX_PROTOCOL:
        return bool(source.get(WATSONX_PROJECT_ENV, "").strip() and resolve_api_key(spec, source))
    if spec.name in (
        "databricks",
        "snowflake_cortex",
        "baseten",
        "lepton",
        "vertex_openai",
        "cloudflare_ai",
    ):
        custom_endpoint = source.get(spec.endpoint_env or "", "").strip()
        return bool(custom_endpoint and resolve_api_key(spec, source))
    if spec.name == "openai_compatible":
        return bool(source.get("OPENAI_COMPATIBLE_BASE_URL", "").strip() or resolve_api_key(spec, source))
    if spec.allow_unauthenticated:
        return bool(source.get(spec.endpoint_env or "", "").strip() or resolve_api_key(spec, source))
    return resolve_api_key(spec, source) is not None


def list_configured_chat_providers(env: Mapping[str, str] | None = None) -> list[str]:
    """Configured providers that answer a prompt; classifiers are left for explicit selection."""
    source = os.environ if env is None else env
    return [
        name
        for name in ALL_PROVIDER_NAMES
        if PROVIDERS[name].protocol != CLASSIFICATION_PROTOCOL and is_provider_configured(PROVIDERS[name], source)
    ]


def list_provider_statuses(env: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    source = os.environ if env is None else env
    rows: list[dict[str, object]] = []
    for name in ALL_PROVIDER_NAMES:
        spec = PROVIDERS[name]
        rows.append(
            {
                "provider": spec.name,
                "display_name": spec.display_name,
                "default_model": spec.default_model,
                "protocol": spec.protocol,
                "api_key_envs": list(spec.api_key_envs),
                "supports_remote_mcp": spec.supports_remote_mcp,
                "configured": is_provider_configured(spec, source),
            }
        )
    return rows