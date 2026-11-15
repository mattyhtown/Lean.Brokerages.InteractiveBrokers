from __future__ import annotations

import json
import pytest

from multi_llm.cli import main as cli_main
from multi_llm.client import ChatRequest, LLMError, build_wire_request, complete, fanout_complete
from multi_llm.mcp_templates import (
    SUPPORTED_TARGETS,
    SUPABASE_MCP_URL,
    UNUSUAL_WHALES_MCP_URL,
    UNUSUAL_WHALES_NPX_PACKAGE,
    render_mcp_config,
)
from multi_llm.providers import ALL_PROVIDER_NAMES, PROVIDERS, get_provider, list_provider_statuses


def test_all_expected_providers_registered() -> None:
    assert len(PROVIDERS) == len(ALL_PROVIDER_NAMES) >= 45
    assert set(PROVIDERS.keys()) == set(ALL_PROVIDER_NAMES)
    required = {
        "anthropic",
        "openai",
        "gemini",
        "xai",
        "mistral",
        "cohere",
        "deepseek",
        "groq",
        "together",
        "fireworks",
        "perplexity",
        "cerebras",
        "openrouter",
        "huggingface",
        "moonshot",
        "dashscope",
        "zhipu",
        "nvidia_nim",
        "github_models",
        "azure_openai",
        "bedrock",
        "openai_compatible",
        "ollama",
    }
    assert required.issubset(set(PROVIDERS.keys()))
    assert get_provider("claude").name == "anthropic"
    assert get_provider("chatgpt").name == "openai"
    assert get_provider("grok").name == "xai"
    assert get_provider("google").name == "gemini"
    assert get_provider("vllm").name == "openai_compatible"
    assert get_provider("kimi").name == "moonshot"
    assert get_provider("qwen").name == "dashscope"


def test_provider_statuses_never_leak_secret_values() -> None:
    fake_secret = "sk-super-secret-value-99999"
    env = {"ANTHROPIC_API_KEY": fake_secret, "OPENAI_API_KEY": fake_secret}
    statuses = list_provider_statuses(env=env)
    serialized = json.dumps(statuses)
    assert fake_secret not in serialized
    by_name = {row["provider"]: row for row in statuses}
    assert by_name["anthropic"]["configured"] is True
    assert by_name["openai"]["configured"] is True
    assert by_name["gemini"]["configured"] is False


def test_anthropic_wire_request_with_remote_mcp_and_response() -> None:
    env = {
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "UW_API_KEY": "uw-token-123",
        "SUPABASE_ACCESS_TOKEN": "sbp-token-456",
    }
    req = ChatRequest(
        provider="anthropic",
        prompt="Check flow",
        system="Be concise",
        attach_mcp=("uw", "supabase"),
    )
    spec, url, headers, body, _ = build_wire_request(req, env=env)
    assert spec.name == "anthropic"
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-beta"] == "mcp-client-2025-11-20"
    assert body["system"] == "Be concise"
    mcp_servers = body["mcp_servers"]
    assert isinstance(mcp_servers, list) and len(mcp_servers) == 2
    assert mcp_servers[0]["url"] == UNUSUAL_WHALES_MCP_URL
    tools = body["tools"]
    assert isinstance(tools, list) and len(tools) == 2
    assert all(item.get("type") == "mcp_toolset" for item in tools)

    resp = complete(
        req,
        env=env,
        transport=lambda _u, _h, _b, _t: {
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "Anthropic answer"}],
        },
    )
    assert resp.text == "Anthropic answer"


def test_openai_responses_wire_request_with_remote_mcp() -> None:
    env = {"OPENAI_API_KEY": "sk-openai-test", "UW_API_KEY": "uw-key-abc"}
    req = ChatRequest(provider="openai", prompt="Analyze SPY", attach_mcp=("uw",))
    _, url, headers, body, _ = build_wire_request(req, env=env)
    assert url == "https://api.openai.com/v1/responses"
    assert headers["Authorization"] == "Bearer sk-openai-test"
    tools = body["tools"]
    assert isinstance(tools, list) and tools[0]["type"] == "mcp"
    assert tools[0]["server_url"] == UNUSUAL_WHALES_MCP_URL
    assert tools[0]["require_approval"] == "always"

    resp = complete(
        req,
        env=env,
        transport=lambda _u, _h, _b, _t: {
            "model": "gpt-4.1",
            "output": [{"content": [{"type": "output_text", "text": "OpenAI answer"}]}],
        },
    )
    assert resp.text == "OpenAI answer"


def test_gemini_uses_header_not_query_param() -> None:
    env = {"GEMINI_API_KEY": "AIza-secret-test"}
    req = ChatRequest(provider="gemini", prompt="Hello Gemini", system="Act as quant")
    _, url, headers, body, _ = build_wire_request(req, env=env)
    assert "AIza-secret-test" not in url
    assert headers["x-goog-api-key"] == "AIza-secret-test"
    assert "systemInstruction" in body

    resp = complete(
        req,
        env=env,
        transport=lambda _u, _h, _b, _t: {
            "modelVersion": "gemini-2.5-pro",
            "candidates": [{"content": {"parts": [{"text": "Gemini reply"}]}}],
        },
    )
    assert resp.text == "Gemini reply"


@pytest.mark.parametrize(
    ("provider", "env_var", "expected_host"),
    [
        ("xai", "XAI_API_KEY", "https://api.x.ai/v1/chat/completions"),
        ("mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1/chat/completions"),
        ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com/chat/completions"),
        ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions"),
        ("together", "TOGETHER_API_KEY", "https://api.together.xyz/v1/chat/completions"),
        ("fireworks", "FIREWORKS_API_KEY", "https://api.fireworks.ai/inference/v1/chat/completions"),
        ("perplexity", "PERPLEXITY_API_KEY", "https://api.perplexity.ai/chat/completions"),
        ("cerebras", "CEREBRAS_API_KEY", "https://api.cerebras.ai/v1/chat/completions"),
        ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions"),
    ],
)
def test_openai_compatible_cloud_providers(provider: str, env_var: str, expected_host: str) -> None:
    env = {env_var: "token-xyz"}
    req = ChatRequest(provider=provider, prompt="Ping")
    _, url, headers, _, _ = build_wire_request(req, env=env)
    assert url == expected_host
    assert headers["Authorization"] == "Bearer token-xyz"

    resp = complete(
        req,
        env=env,
        transport=lambda _u, _h, _b, _t: {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": f"{provider} pong"}}],
        },
    )
    assert resp.text == f"{provider} pong"


def test_cohere_azure_bedrock_and_ollama() -> None:
    cohere_resp = complete(
        ChatRequest(provider="cohere", prompt="Hi"),
        env={"COHERE_API_KEY": "coh-key"},
        transport=lambda _u, _h, _b, _t: {"message": {"content": [{"text": "Cohere ok"}]}},
    )
    assert cohere_resp.text == "Cohere ok"

    azure_env = {
        "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
        "AZURE_OPENAI_API_KEY": "az-key",
        "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-prod",
    }
    _, az_url, az_headers, _, _ = build_wire_request(
        ChatRequest(provider="azure_openai", prompt="Hi"), env=azure_env
    )
    assert "deployments/gpt-4.1-prod/chat/completions" in az_url
    assert az_headers["api-key"] == "az-key"

    bedrock_resp = complete(
        ChatRequest(provider="bedrock", prompt="Hi"),
        env={"AWS_BEARER_TOKEN_BEDROCK": "brk-token", "AWS_REGION": "us-west-2"},
        transport=lambda u, _h, _b, _t: {
            "output": {"message": {"content": [{"text": f"Bedrock from {u}"}]}}
        },
    )
    assert "https://bedrock-runtime.us-west-2.amazonaws.com/" in bedrock_resp.text

    with pytest.raises(LLMError, match="Invalid AWS_REGION"):
        build_wire_request(
            ChatRequest(provider="bedrock", prompt="Hi"),
            env={
                "AWS_BEARER_TOKEN_BEDROCK": "brk-token",
                "AWS_REGION": "evil.com",
            },
        )

    captured_url: dict[str, str] = {}

    def ollama_transport(u: str, _h: dict[str, str], _b: dict[str, object], _t: int) -> dict[str, object]:
        captured_url["url"] = u
        return {
            "model": "llama3.2",
            "choices": [{"message": {"content": "Local llama ok"}}],
        }

    ollama_resp = complete(
        ChatRequest(provider="ollama", prompt="Local"),
        env={"OLLAMA_BASE_URL": "http://127.0.0.1:22434/v1"},
        transport=ollama_transport,
    )
    assert ollama_resp.text == "Local llama ok"
    assert captured_url["url"] == "http://127.0.0.1:22434/v1/chat/completions"

    with pytest.raises(LLMError, match="AZURE_OPENAI_DEPLOYMENT"):
        build_wire_request(
            ChatRequest(provider="azure_openai", prompt="Hi"),
            env={
                "AZURE_OPENAI_ENDPOINT": "https://my-resource.openai.azure.com",
                "AZURE_OPENAI_API_KEY": "az-key",
            },
        )

    with pytest.raises(LLMError, match="Invalid AZURE_OPENAI_ENDPOINT"):
        build_wire_request(
            ChatRequest(provider="azure_openai", prompt="Hi"),
            env={
                "AZURE_OPENAI_ENDPOINT": "https://evil.com",
                "AZURE_OPENAI_API_KEY": "az-key",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-prod",
            },
        )


def test_secret_redaction_on_transport_failure() -> None:
    secret = "sk-ant-sensitive-token-value!"
    env = {"ANTHROPIC_API_KEY": secret}

    def failing_transport(_u: str, _h: dict[str, str], _b: dict[str, object], _t: int) -> dict[str, object]:
        raise RuntimeError(f"Upstream leaked {secret} and {secret.replace('!', '%21')}")

    with pytest.raises(LLMError) as exc_info:
        complete(ChatRequest(provider="anthropic", prompt="test"), env=env, transport=failing_transport)
    assert secret not in str(exc_info.value)
    assert "sk-ant-sensitive" not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_fanout_complete_handles_mixed_success_and_missing_keys() -> None:
    env = {"ANTHROPIC_API_KEY": "sk-ant-1", "XAI_API_KEY": "xai-1"}

    def fake_transport(url: str, _h: dict[str, str], _b: dict[str, object], _t: int) -> dict[str, object]:
        if "anthropic" in url:
            return {"model": "claude-sonnet-4-5", "content": [{"text": "Claude ok"}]}
        return {"model": "grok-4", "choices": [{"message": {"content": "Grok ok"}}]}

    results = fanout_complete(
        providers=["anthropic", "openai", "xai"],
        prompt="Compare",
        env=env,
        transport=fake_transport,
    )
    assert results[0]["ok"] is True and results[0]["text"] == "Claude ok"
    assert results[1]["ok"] is False and "OPENAI_API_KEY" in str(results[1]["error"])
    assert results[2]["ok"] is True and results[2]["text"] == "Grok ok"


@pytest.mark.parametrize("target", SUPPORTED_TARGETS)
def test_mcp_templates_use_official_servers_and_no_shadow_mcps(target: str) -> None:
    rendered = render_mcp_config(target)
    assert "@modelcontextprotocol/server-" not in rendered
    assert "unusualwhales.com/public-api/mcp" not in rendered
    assert "unusualwhales" in rendered
    assert UNUSUAL_WHALES_MCP_URL in rendered or UNUSUAL_WHALES_NPX_PACKAGE in rendered
    assert SUPABASE_MCP_URL in rendered


def test_cli_providers_and_mcp_config(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["providers", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert len(out) == len(ALL_PROVIDER_NAMES)
    provider_ids = [row["provider"] for row in out]
    assert provider_ids == list(ALL_PROVIDER_NAMES)
    for row in out:
        assert isinstance(row["display_name"], str) and row["display_name"]
        assert isinstance(row["default_model"], str) and row["default_model"]
        assert isinstance(row["configured"], bool)

    cli_main(["mcp-config", "--target", "codex"])
    codex_toml = capsys.readouterr().out
    assert "[mcp_servers.unusualwhales]" in codex_toml
    assert UNUSUAL_WHALES_MCP_URL in codex_toml


def test_rejects_non_http_url_scheme() -> None:
    with pytest.raises(LLMError, match="Invalid provider URL"):
        complete(
            ChatRequest(provider="ollama", prompt="test"),
            env={"OLLAMA_BASE_URL": "file:///etc/passwd"},
        )


def test_enterprise_providers_require_custom_base_url() -> None:
    with pytest.raises(LLMError, match="DATABRICKS_BASE_URL"):
        build_wire_request(
            ChatRequest(provider="databricks", prompt="Hi"),
            env={"DATABRICKS_TOKEN": "dapi-test"},
        )
    with pytest.raises(LLMError, match="SNOWFLAKE_CORTEX_BASE_URL"):
        build_wire_request(
            ChatRequest(provider="snowflake_cortex", prompt="Hi"),
            env={"SNOWFLAKE_TOKEN": "sf-test"},
        )


def test_huggingface_and_openai_compatible_endpoints() -> None:
    hf_req = ChatRequest(provider="huggingface", prompt="Hi")
    _, hf_url, hf_headers, _, _ = build_wire_request(hf_req, env={"HF_TOKEN": "hf_test"})
    assert hf_url == "https://router.huggingface.co/v1/chat/completions"
    assert hf_headers["Authorization"] == "Bearer hf_test"

    compat_req = ChatRequest(provider="openai_compatible", prompt="Hi")
    _, compat_url, _, _, _ = build_wire_request(
        compat_req,
        env={"OPENAI_COMPATIBLE_BASE_URL": "http://127.0.0.1:8080/v1"},
    )
    assert compat_url == "http://127.0.0.1:8080/v1/chat/completions"

