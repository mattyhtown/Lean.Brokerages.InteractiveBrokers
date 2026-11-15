from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


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


PROVIDERS: dict[str, ProviderSpec] = {
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
        display_name="Google Gemini",
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
    "mistral": ProviderSpec(
        name="mistral",
        display_name="Mistral AI",
        protocol="openai_chat",
        default_model="mistral-large-latest",
        base_url="https://api.mistral.ai/v1",
        api_key_envs=("MISTRAL_API_KEY",),
    ),
    "cohere": ProviderSpec(
        name="cohere",
        display_name="Cohere",
        protocol="cohere_v2",
        default_model="command-a-03-2025",
        base_url="https://api.cohere.com/v2/chat",
        api_key_envs=("COHERE_API_KEY",),
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        display_name="DeepSeek",
        protocol="openai_chat",
        default_model="deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key_envs=("DEEPSEEK_API_KEY",),
    ),
    "groq": ProviderSpec(
        name="groq",
        display_name="Groq",
        protocol="openai_chat",
        default_model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key_envs=("GROQ_API_KEY",),
    ),
    "together": ProviderSpec(
        name="together",
        display_name="Together AI",
        protocol="openai_chat",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
        api_key_envs=("TOGETHER_API_KEY",),
    ),
    "fireworks": ProviderSpec(
        name="fireworks",
        display_name="Fireworks AI",
        protocol="openai_chat",
        default_model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_envs=("FIREWORKS_API_KEY",),
    ),
    "perplexity": ProviderSpec(
        name="perplexity",
        display_name="Perplexity Sonar",
        protocol="openai_chat",
        default_model="sonar-pro",
        base_url="https://api.perplexity.ai",
        api_key_envs=("PERPLEXITY_API_KEY",),
    ),
    "cerebras": ProviderSpec(
        name="cerebras",
        display_name="Cerebras",
        protocol="openai_chat",
        default_model="llama-3.3-70b",
        base_url="https://api.cerebras.ai/v1",
        api_key_envs=("CEREBRAS_API_KEY",),
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        display_name="OpenRouter (300+ Models)",
        protocol="openai_chat",
        default_model="anthropic/claude-sonnet-4.5",
        base_url="https://openrouter.ai/api/v1",
        api_key_envs=("OPENROUTER_API_KEY",),
    ),
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
    "ollama": ProviderSpec(
        name="ollama",
        display_name="Ollama / Local OpenAI-Compatible",
        protocol="openai_chat",
        default_model="llama3.2",
        base_url="http://127.0.0.1:11434/v1",
        api_key_envs=("OPENAI_COMPATIBLE_API_KEY",),
        endpoint_env="OLLAMA_BASE_URL",
        allow_unauthenticated=True,
    ),
}

PROVIDER_ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "chatgpt": "openai",
    "google": "gemini",
    "grok": "xai",
    "x": "xai",
    "azure": "azure_openai",
    "aws": "bedrock",
    "local": "ollama",
    "vllm": "ollama",
    "lmstudio": "ollama",
}


def get_provider(name: str) -> ProviderSpec:
    normalized = name.strip().lower().replace("-", "_")
    canonical = PROVIDER_ALIASES.get(normalized, normalized)
    if canonical not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider '{name}'. Supported providers: {known}")
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
        return bool(endpoint and resolve_api_key(spec, source))
    if spec.allow_unauthenticated:
        return bool(
            source.get("OLLAMA_BASE_URL", "").strip()
            or source.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
            or resolve_api_key(spec, source)
        )
    return resolve_api_key(spec, source) is not None


def list_provider_statuses(env: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    source = os.environ if env is None else env
    rows: list[dict[str, object]] = []
    for spec in PROVIDERS.values():
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
