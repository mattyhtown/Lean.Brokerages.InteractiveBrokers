from __future__ import annotations

import json
from pathlib import Path
import re
import threading

import pytest

from multi_llm.cli import main as cli_main
from multi_llm.client import ChatRequest, LLMError, build_wire_request, complete, fanout_complete
from multi_llm.mcp_templates import (
    DATABENTO_MCP_COMMAND,
    SUPPORTED_TARGETS,
    THETADATA_MCP_SSE_URL,
    SUPABASE_MCP_URL,
    SUPABASE_NPX_PACKAGE,
    UNUSUAL_WHALES_MCP_URL,
    UNUSUAL_WHALES_NPX_PACKAGE,
    render_mcp_config,
)
from multi_llm.providers import (
    ALL_PROVIDER_NAMES,
    PROVIDERS,
    get_provider,
    list_configured_chat_providers,
    list_provider_statuses,
)


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
    assert SUPABASE_MCP_URL in rendered or SUPABASE_NPX_PACKAGE in rendered


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
    with pytest.raises(LLMError, match="BASETEN_BASE_URL"):
        build_wire_request(
            ChatRequest(provider="baseten", prompt="Hi"),
            env={"BASETEN_API_KEY": "bt-test"},
        )
    with pytest.raises(LLMError, match="LEPTON_BASE_URL"):
        build_wire_request(
            ChatRequest(provider="lepton", prompt="Hi"),
            env={"LEPTON_API_KEY": "lp-test"},
        )


def test_no_provider_default_url_contains_placeholders() -> None:
    for spec in PROVIDERS.values():
        assert not any(token in spec.base_url for token in ("{", "<", "example."))


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.attacker.example/v1",
        "http://127.0.0.1.attacker.example/v1",
        "http://localhost.attacker.example/v1",
        "http://10.0.0.5:8000/v1",
    ],
)
def test_http_is_refused_for_hosts_that_only_look_like_loopback(base_url: str) -> None:
    with pytest.raises(LLMError, match="Refusing HTTP provider URL"):
        build_wire_request(
            ChatRequest(provider="openai_compatible", prompt="Hi"),
            env={"OPENAI_COMPATIBLE_BASE_URL": base_url, "OPENAI_COMPATIBLE_API_KEY": "secret"},
        )


@pytest.mark.parametrize("base_url", ["http://127.0.0.2:8000/v1", "http://[::1]:8000/v1", "http://localhost:8000/v1"])
def test_http_is_allowed_for_real_loopback(base_url: str) -> None:
    _, url, _, _, _ = build_wire_request(
        ChatRequest(provider="openai_compatible", prompt="Hi"),
        env={"OPENAI_COMPATIBLE_BASE_URL": base_url},
    )
    assert url == f"{base_url}/chat/completions"


def test_ollama_ignores_openai_compatible_base_url() -> None:
    env = {"OPENAI_COMPATIBLE_BASE_URL": "https://paid.example.test/v1"}
    _, url, _, _, _ = build_wire_request(ChatRequest(provider="ollama", prompt="Hi"), env=env)
    assert url == "http://127.0.0.1:11434/v1/chat/completions"


def test_minimax_group_id_is_not_an_api_key() -> None:
    env = {"MINIMAX_GROUP_ID": "group-123"}
    by_name = {row["provider"]: row for row in list_provider_statuses(env=env)}
    assert by_name["minimax"]["configured"] is False
    with pytest.raises(LLMError, match="MINIMAX_API_KEY"):
        build_wire_request(ChatRequest(provider="minimax", prompt="Hi"), env=env)


def test_openai_refusal_content_part_is_returned() -> None:
    def reply(content: list[object]) -> str:
        return complete(
            ChatRequest(provider="openai", prompt="Hi"),
            env={"OPENAI_API_KEY": "sk-test"},
            transport=lambda _u, _h, _b, _t: {
                "model": "gpt-4.1",
                "status": "completed",
                "output": [{"type": "message", "status": "completed", "role": "assistant", "content": content}],
            },
        ).text

    assert reply([{"type": "refusal", "refusal": " I can't help with that. "}]) == "[refusal: I can't help with that.]"
    assert reply(
        [{"type": "output_text", "text": "Here is part."}, {"type": "refusal", "refusal": "Not the rest."}]
    ) == "Here is part.\n[refusal: Not the rest.]"
    assert reply([{"type": "refusal", "refusal": ""}, {"type": "output_text", "text": "Fine."}]) == "Fine."


def test_openai_mcp_approval_request_is_surfaced() -> None:
    def fake_transport(_u: str, _h: dict[str, str], _b: dict[str, object], _t: int) -> dict[str, object]:
        return {
            "model": "gpt-4.1",
            "output": [
                {
                    "type": "mcp_approval_request",
                    "server_label": "unusualwhales",
                    "name": "get_flow",
                    "arguments": '{"ticker":"SPY"}',
                }
            ],
        }

    resp = complete(
        ChatRequest(provider="openai", prompt="Flow?", attach_mcp=("uw",)),
        env={"OPENAI_API_KEY": "sk-test", "UW_API_KEY": "uw-test"},
        transport=fake_transport,
    )
    assert "mcp approval required: unusualwhales.get_flow" in resp.text


def test_openai_mcp_approval_request_is_surfaced_alongside_output_text() -> None:
    def fake_transport(_u: str, _h: dict[str, str], _b: dict[str, object], _t: int) -> dict[str, object]:
        return {
            "model": "gpt-4.1",
            "output_text": "Let me pull the flow.",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Let me pull the flow."}]},
                {
                    "type": "mcp_approval_request",
                    "server_label": "unusualwhales",
                    "name": "get_flow",
                    "arguments": '{"ticker":"SPY"}',
                },
            ],
        }

    resp = complete(
        ChatRequest(provider="openai", prompt="Flow?", attach_mcp=("uw",)),
        env={"OPENAI_API_KEY": "sk-test", "UW_API_KEY": "uw-test"},
        transport=fake_transport,
    )
    assert resp.text == (
        "Let me pull the flow.\n"
        '[mcp approval required: unusualwhales.get_flow arguments={"ticker":"SPY"}]'
    )


def test_watsonx_uses_native_chat_endpoint_with_project_id() -> None:
    env = {"WATSONX_ACCESS_TOKEN": "iam-token-test", "WATSONX_PROJECT_ID": "proj-123"}
    req = ChatRequest(provider="watson", prompt="Hello", system="Be brief")
    spec, url, headers, body, _ = build_wire_request(req, env=env)
    assert spec.name == "watsonx"
    assert url == "https://us-south.ml.cloud.ibm.com/ml/v1/text/chat?version=2024-10-08"
    assert headers == {"Authorization": "Bearer iam-token-test"}
    assert body["model_id"] == "ibm/granite-3-8b-instruct"
    assert body["project_id"] == "proj-123"
    assert "model" not in body
    assert body["messages"] == [
        {"role": "system", "content": "Be brief"},
        {"role": "user", "content": "Hello"},
    ]

    resp = complete(
        req,
        env=env,
        transport=lambda _u, _h, _b, _t: {
            "model_id": "ibm/granite-3-8b-instruct",
            "choices": [{"message": {"role": "assistant", "content": "watsonx answer"}}],
        },
    )
    assert resp.text == "watsonx answer"
    assert resp.model == "ibm/granite-3-8b-instruct"


@pytest.mark.parametrize(
    "custom_url",
    ["https://eu-de.ml.cloud.ibm.com", "https://eu-de.ml.cloud.ibm.com/ml/v1/"],
)
def test_watsonx_region_url_with_or_without_api_path(custom_url: str) -> None:
    env = {
        "WATSONX_ACCESS_TOKEN": "iam-token-test",
        "WATSONX_PROJECT_ID": "proj-123",
        "WATSONX_URL": custom_url,
    }
    _, url, _, _, _ = build_wire_request(ChatRequest(provider="watsonx", prompt="Hi"), env=env)
    assert url == "https://eu-de.ml.cloud.ibm.com/ml/v1/text/chat?version=2024-10-08"


def test_watsonx_requires_token_and_project_id() -> None:
    with pytest.raises(LLMError, match="WATSONX_ACCESS_TOKEN"):
        build_wire_request(
            ChatRequest(provider="watsonx", prompt="Hi"),
            env={"WATSONX_API_KEY": "ibm-cloud-api-key", "WATSONX_PROJECT_ID": "proj-123"},
        )
    with pytest.raises(LLMError, match="WATSONX_PROJECT_ID"):
        build_wire_request(
            ChatRequest(provider="watsonx", prompt="Hi"),
            env={"WATSONX_ACCESS_TOKEN": "iam-token-test"},
        )

    by_name = {
        row["provider"]: row
        for row in list_provider_statuses(env={"WATSONX_ACCESS_TOKEN": "iam-token-test"})
    }
    assert by_name["watsonx"]["configured"] is False
    assert "watsonx" in list_configured_chat_providers(
        env={"WATSONX_ACCESS_TOKEN": "iam-token-test", "WATSONX_PROJECT_ID": "proj-123"}
    )


def test_fanout_shuts_down_its_thread_pool() -> None:
    before = {t.ident for t in threading.enumerate()}

    def fake_transport(_u: str, _h: dict[str, str], _b: dict[str, object], _t: int) -> dict[str, object]:
        return {"model": "grok-4", "choices": [{"message": {"content": "ok"}}]}

    fanout_complete(providers=["xai"], prompt="Hi", env={"XAI_API_KEY": "x"}, transport=fake_transport)
    assert {t.ident for t in threading.enumerate() if t.is_alive()} <= before


def test_claude_desktop_and_zed_templates_match_client_schemas() -> None:
    desktop = json.loads(render_mcp_config("claude-desktop"))["mcpServers"]
    for server in desktop.values():
        assert isinstance(server["command"], str) and server["command"]
        assert "url" not in server and "type" not in server

    zed = json.loads(render_mcp_config("zed"))["context_servers"]
    assert zed["supabase"]["url"] == SUPABASE_MCP_URL
    assert set(zed["supabase"]) == {"url", "headers"}
    for name in ("unusualwhales", "mongodb"):
        assert zed[name]["command"] == "npx"
        assert isinstance(zed[name]["args"], list)


def test_committed_examples_pin_npx_packages() -> None:
    cursor_dir = Path(__file__).resolve().parents[2] / ".cursor"
    npx_package = re.compile(r"""-y["',\s]+([@\w./-]+?)(@[\w.-]+)?["'\s\]]""")
    checked = 0
    offenders = []
    for path in cursor_dir.rglob("*"):
        if not path.is_file() or path.suffix not in (".example", ".md"):
            continue
        for line in path.read_text().splitlines():
            for match in npx_package.finditer(line + " "):
                checked += 1
                version = match.group(2) or ""
                if not re.fullmatch(r"@\d+\.\d+\.\d+", version):
                    offenders.append(f"{path.relative_to(cursor_dir)}: {line.strip()}")
    assert checked >= 8
    assert offenders == []


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


@pytest.mark.parametrize(
    "target", ["cursor", "claude-desktop", "claude-code", "codex", "gemini-cli", "vscode", "windsurf", "zed"]
)
def test_databento_is_a_local_stdio_server_on_every_client_target(target: str) -> None:
    rendered = render_mcp_config(target)
    assert DATABENTO_MCP_COMMAND in rendered
    assert "DATABENTO_API_KEY" in rendered
    assert not re.search(r"\bdb-[A-Za-z0-9]{29}\b", rendered)


@pytest.mark.parametrize(
    ("target", "servers_key", "expected"),
    [
        ("cursor", "mcpServers", {"url": THETADATA_MCP_SSE_URL}),
        ("gemini-cli", "mcpServers", {"url": THETADATA_MCP_SSE_URL, "timeout": 30000}),
        ("vscode", "servers", {"type": "sse", "url": THETADATA_MCP_SSE_URL}),
        ("windsurf", "mcpServers", {"serverUrl": THETADATA_MCP_SSE_URL}),
    ],
)
def test_thetadata_uses_each_clients_sse_field(target: str, servers_key: str, expected: dict[str, object]) -> None:
    assert json.loads(render_mcp_config(target))[servers_key]["thetadata"] == expected


def test_thetadata_claude_code_command_uses_sse_transport() -> None:
    assert f"claude mcp add --transport sse thetadata {THETADATA_MCP_SSE_URL}" in render_mcp_config("claude-code")


@pytest.mark.parametrize("target", ["claude-desktop", "codex", "zed", "anthropic-api", "openai-responses"])
def test_thetadata_is_omitted_where_loopback_sse_cannot_work(target: str) -> None:
    rendered = render_mcp_config(target)
    assert "thetadata" not in rendered
    assert THETADATA_MCP_SSE_URL not in rendered


@pytest.mark.parametrize("target", ["anthropic-api", "openai-responses"])
def test_hosted_api_templates_only_reference_https_servers(target: str) -> None:
    rendered = render_mcp_config(target)
    assert DATABENTO_MCP_COMMAND not in rendered
    assert "http://" not in rendered


FINBERT_URL = "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert"
FINBERT_SCORES = [
    {"label": "neutral", "score": 0.036},
    {"label": "positive", "score": 0.948},
    {"label": "negative", "score": 0.016},
]


def test_finbert_posts_the_prompt_to_hugging_face_text_classification() -> None:
    spec, url, headers, body, secrets = build_wire_request(
        ChatRequest(provider="finbert", prompt="Shares surged after earnings.", system="ignored"),
        env={"HF_TOKEN": "hf_test_token"},
    )
    assert spec.name == "finbert"
    assert url == FINBERT_URL
    assert headers == {"Authorization": "Bearer hf_test_token"}
    assert body == {"inputs": "Shares surged after earnings."}
    assert secrets == ["hf_test_token"]


def test_finbert_requires_a_hugging_face_token() -> None:
    with pytest.raises(LLMError, match="HF_TOKEN or HUGGINGFACE_API_KEY"):
        build_wire_request(ChatRequest(provider="finbert", prompt="Hi"), env={})


@pytest.mark.parametrize("payload", [[FINBERT_SCORES], FINBERT_SCORES])
def test_finbert_returns_labels_ordered_by_score(payload: list[object]) -> None:
    calls: list[str] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, object], timeout: int) -> object:
        calls.append(url)
        return payload

    resp = complete(ChatRequest(provider="finbert", prompt="Shares surged."), env={"HF_TOKEN": "hf_x"}, transport=transport)
    assert calls == [FINBERT_URL]
    assert resp.model == "ProsusAI/finbert"
    assert resp.text == "positive 0.948, neutral 0.036, negative 0.016"
    assert [row["label"] for row in resp.raw["labels"]] == ["positive", "neutral", "negative"]  # type: ignore[union-attr]


@pytest.mark.parametrize("payload", [[], [[]], {"error": "Model is loading"}, [{"label": "positive"}], "oops"])
def test_finbert_rejects_responses_without_label_scores(payload: object) -> None:
    with pytest.raises(LLMError, match="Unexpected classification response from finbert"):
        complete(
            ChatRequest(provider="finbert", prompt="Hi"),
            env={"HF_TOKEN": "hf_x"},
            transport=lambda url, headers, body, timeout: payload,
        )


def test_finbert_accepts_another_classifier_model_and_rejects_path_tricks() -> None:
    env = {"HF_TOKEN": "hf_x"}
    _, url, _, _, _ = build_wire_request(
        ChatRequest(provider="finbert", prompt="Hi", model="yiyanghkust/finbert-tone"), env=env
    )
    assert url == "https://router.huggingface.co/hf-inference/models/yiyanghkust/finbert-tone"
    for bad in ("../v1/chat/completions", "ProsusAI/../x", "a/b/c", "ProsusAI/finbert?x=1", " "):
        with pytest.raises(LLMError, match="Invalid Hugging Face model id"):
            build_wire_request(ChatRequest(provider="finbert", prompt="Hi", model=bad), env=env)


def test_finbert_cannot_take_remote_mcp_attachments() -> None:
    with pytest.raises(LLMError, match="does not support server-side remote MCP"):
        build_wire_request(
            ChatRequest(provider="finbert", prompt="Hi", attach_mcp=("uw",)),
            env={"HF_TOKEN": "hf_x", "UW_API_KEY": "uw_x"},
        )


def test_all_configured_fanout_skips_classifiers() -> None:
    env = {"HF_TOKEN": "hf_x", "ANTHROPIC_API_KEY": "sk-ant-x"}
    by_name = {row["provider"]: row for row in list_provider_statuses(env=env)}
    assert by_name["finbert"]["configured"] is True
    selected = list_configured_chat_providers(env=env)
    assert "finbert" not in selected
    assert {"anthropic", "huggingface", "ollama"} <= set(selected)


def test_finbert_can_be_named_explicitly_in_a_fanout() -> None:
    def transport(url: str, headers: dict[str, str], body: dict[str, object], timeout: int) -> object:
        if url == FINBERT_URL:
            return [FINBERT_SCORES]
        return {"choices": [{"message": {"content": "ok"}}]}

    results = fanout_complete(
        ["finbert", "groq"], "Shares surged.", env={"HF_TOKEN": "hf_x", "GROQ_API_KEY": "gsk_x"}, transport=transport
    )
    assert [(row["provider"], row["ok"], row["text"]) for row in results] == [
        ("finbert", True, "positive 0.948, neutral 0.036, negative 0.016"),
        ("groq", True, "ok"),
    ]


def test_chat_providers_still_reject_non_object_json() -> None:
    with pytest.raises(LLMError, match="Expected JSON object response from provider"):
        complete(
            ChatRequest(provider="groq", prompt="Hi"),
            env={"GROQ_API_KEY": "gsk_x"},
            transport=lambda url, headers, body, timeout: [{"label": "positive", "score": 0.9}],
        )
