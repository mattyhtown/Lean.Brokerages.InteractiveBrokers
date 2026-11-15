from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import os
from typing import Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request

from multi_llm.mcp_templates import SUPABASE_MCP_URL, UNUSUAL_WHALES_MCP_URL
from multi_llm.providers import ProviderSpec, get_provider, resolve_api_key

TransportCallable = Callable[[str, dict[str, str], dict[str, object], int], dict[str, object]]


class LLMError(Exception):
    pass


@dataclass(frozen=True)
class ChatRequest:
    provider: str
    prompt: str
    model: str | None = None
    system: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.2
    attach_mcp: tuple[str, ...] = field(default_factory=tuple)
    timeout_seconds: int = 90


@dataclass(frozen=True)
class ChatResponse:
    provider: str
    model: str
    text: str
    raw: dict[str, object]


def _redact_secrets(message: str, secrets: list[str]) -> str:
    cleaned = message
    for secret in secrets:
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[REDACTED]")
            encoded = urllib.parse.quote(secret, safe="")
            if encoded != secret:
                cleaned = cleaned.replace(encoded, "[REDACTED]")
    return cleaned


def _default_transport(
    url: str,
    headers: dict[str, str],
    body: dict[str, object],
    timeout_seconds: int,
) -> dict[str, object]:
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("https", "http") or not parsed_url.netloc:
        raise LLMError(f"Refusing non-HTTP(S) provider URL: {url}")
    encoded = json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Accept": "application/json", **headers}
    request = urllib.request.Request(url, data=encoded, headers=req_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise LLMError("Expected JSON object response from provider")
    return parsed


def _resolve_remote_mcp_entries(
    names: tuple[str, ...],
    env: Mapping[str, str],
) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for raw in names:
        item = raw.strip().lower()
        if not item:
            continue
        if item in ("uw", "unusualwhales", "unusual_whales"):
            token = env.get("UW_API_KEY", "").strip()
            if not token:
                raise LLMError("UW_API_KEY is required when attaching 'uw' remote MCP")
            entries.append(("unusualwhales", UNUSUAL_WHALES_MCP_URL, token))
        elif item == "supabase":
            token = env.get("SUPABASE_ACCESS_TOKEN", "").strip()
            if not token:
                raise LLMError(
                    "SUPABASE_ACCESS_TOKEN is required when attaching 'supabase' remote MCP via API"
                )
            entries.append(("supabase", SUPABASE_MCP_URL, token))
        else:
            raise LLMError(f"Unsupported remote MCP attachment '{raw}' (use 'uw' or 'supabase')")
    return entries


def build_wire_request(
    req: ChatRequest,
    env: Mapping[str, str] | None = None,
) -> tuple[ProviderSpec, str, dict[str, str], dict[str, object], list[str]]:
    source = os.environ if env is None else env
    spec = get_provider(req.provider)
    model = req.model or spec.default_model
    api_key = resolve_api_key(spec, source)
    secrets: list[str] = [api_key] if api_key else []

    if not api_key and not spec.allow_unauthenticated:
        needed = " or ".join(spec.api_key_envs)
        raise LLMError(f"Missing API key for provider '{spec.name}' (set {needed})")

    mcp_entries = _resolve_remote_mcp_entries(req.attach_mcp, source)
    for _, _, mcp_token in mcp_entries:
        secrets.append(mcp_token)
    if mcp_entries and not spec.supports_remote_mcp:
        raise LLMError(
            f"Provider '{spec.name}' does not support server-side remote MCP attachments directly; "
            "use an MCP client host or 'anthropic' / 'openai'"
        )

    if spec.protocol == "anthropic_messages":
        headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
        body: dict[str, object] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if req.system:
            body["system"] = req.system
        if mcp_entries:
            headers["anthropic-beta"] = "mcp-client-2025-04-04"
            body["mcp_servers"] = [
                {
                    "type": "url",
                    "name": label,
                    "url": server_url,
                    "authorization_token": token,
                }
                for label, server_url, token in mcp_entries
            ]
        return spec, spec.base_url, headers, body, secrets

    if spec.protocol == "openai_responses":
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {
            "model": model,
            "input": req.prompt,
            "max_output_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.system:
            body["instructions"] = req.system
        if mcp_entries:
            body["tools"] = [
                {
                    "type": "mcp",
                    "server_label": label,
                    "server_url": server_url,
                    "headers": {"Authorization": f"Bearer {token}"},
                    "require_approval": "never",
                }
                for label, server_url, token in mcp_entries
            ]
        return spec, f"{spec.base_url.rstrip('/')}/responses", headers, body, secrets

    if spec.protocol == "google_gemini":
        headers = {"x-goog-api-key": api_key or ""}
        encoded_model = urllib.parse.quote(model, safe="-._")
        url = f"{spec.base_url.rstrip('/')}/models/{encoded_model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": req.prompt}]}],
            "generationConfig": {
                "maxOutputTokens": req.max_tokens,
                "temperature": req.temperature,
            },
        }
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        return spec, url, headers, body, secrets

    if spec.protocol == "cohere_v2":
        headers = {"Authorization": f"Bearer {api_key}"}
        messages: list[dict[str, str]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        return spec, spec.base_url, headers, body, secrets

    if spec.protocol == "azure_openai":
        endpoint = source.get(spec.endpoint_env or "", "").strip().rstrip("/")
        if not endpoint:
            raise LLMError("Missing AZURE_OPENAI_ENDPOINT for provider 'azure_openai'")
        deployment = req.model or source.get(spec.deployment_env or "", "").strip() or spec.default_model
        encoded_dep = urllib.parse.quote(deployment, safe="-._")
        url = f"{endpoint}/openai/deployments/{encoded_dep}/chat/completions?api-version=2024-10-21"
        headers = {"api-key": api_key or ""}
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        body = {
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        return spec, url, headers, body, secrets

    if spec.protocol == "aws_bedrock":
        region = source.get("AWS_REGION", "us-east-1").strip() or "us-east-1"
        encoded_model = urllib.parse.quote(model, safe="-._:")
        url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{encoded_model}/converse"
        headers = {"Authorization": f"Bearer {api_key}"}
        body = {
            "messages": [{"role": "user", "content": [{"text": req.prompt}]}],
            "inferenceConfig": {
                "maxTokens": req.max_tokens,
                "temperature": req.temperature,
            },
        }
        if req.system:
            body["system"] = [{"text": req.system}]
        return spec, url, headers, body, secrets

    base_url = spec.base_url
    if spec.endpoint_env:
        custom_base = source.get(spec.endpoint_env, "").strip()
        if custom_base:
            base_url = custom_base
    if spec.name == "openai_compatible":
        fallback = source.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
        if fallback:
            base_url = fallback
    elif spec.name == "ollama" and not source.get(spec.endpoint_env or "", "").strip():
        fallback = source.get("OPENAI_COMPATIBLE_BASE_URL", "").strip()
        if fallback:
            base_url = fallback
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    messages = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.append({"role": "user", "content": req.prompt})
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }
    return spec, url, headers, body, secrets


def parse_wire_response(spec: ProviderSpec, model: str, data: dict[str, object]) -> ChatResponse:
    if spec.protocol == "anthropic_messages":
        blocks = data.get("content")
        parts: list[str] = []
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        resolved_model = str(data.get("model") or model)
        return ChatResponse(spec.name, resolved_model, "\n".join(parts).strip(), data)

    if spec.protocol == "openai_responses":
        if isinstance(data.get("output_text"), str) and data["output_text"]:
            return ChatResponse(spec.name, str(data.get("model") or model), str(data["output_text"]), data)
        output_items = data.get("output")
        parts = []
        if isinstance(output_items, list):
            for item in output_items:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for chunk in content:
                        if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
                            parts.append(chunk["text"])
        return ChatResponse(spec.name, str(data.get("model") or model), "\n".join(parts).strip(), data)

    if spec.protocol == "google_gemini":
        candidates = data.get("candidates")
        parts = []
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                content = first.get("content")
                if isinstance(content, dict) and isinstance(content.get("parts"), list):
                    for part in content["parts"]:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
        return ChatResponse(spec.name, str(data.get("modelVersion") or model), "\n".join(parts).strip(), data)

    if spec.protocol == "cohere_v2":
        msg = data.get("message")
        parts = []
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for item in msg["content"]:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return ChatResponse(spec.name, model, "\n".join(parts).strip(), data)

    if spec.protocol == "aws_bedrock":
        output = data.get("output")
        parts = []
        if isinstance(output, dict):
            msg = output.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                for item in msg["content"]:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
        return ChatResponse(spec.name, model, "\n".join(parts).strip(), data)

    choices = data.get("choices")
    text = ""
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                text = msg["content"].strip()
    return ChatResponse(spec.name, str(data.get("model") or model), text, data)


def complete(
    req: ChatRequest,
    env: Mapping[str, str] | None = None,
    transport: TransportCallable | None = None,
) -> ChatResponse:
    spec, url, headers, body, secrets = build_wire_request(req, env=env)
    active_transport = _default_transport if transport is None else transport
    model = req.model or spec.default_model
    try:
        raw = active_transport(url, headers, body, req.timeout_seconds)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        safe_msg = _redact_secrets(f"HTTP {exc.code} from {spec.name} ({url}): {detail}", secrets)
        raise LLMError(safe_msg) from None
    except Exception as exc:
        if isinstance(exc, LLMError):
            raise LLMError(_redact_secrets(str(exc), secrets)) from None
        safe_msg = _redact_secrets(f"Request to {spec.name} failed: {exc}", secrets)
        raise LLMError(safe_msg) from None
    return parse_wire_response(spec, model, raw)


def fanout_complete(
    providers: list[str],
    prompt: str,
    system: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    env: Mapping[str, str] | None = None,
    transport: TransportCallable | None = None,
) -> list[dict[str, object]]:
    if not providers:
        raise ValueError("At least one provider must be specified for fanout")

    def _call_one(provider_name: str) -> dict[str, object]:
        try:
            resp = complete(
                ChatRequest(
                    provider=provider_name,
                    prompt=prompt,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                env=env,
                transport=transport,
            )
            return {
                "provider": resp.provider,
                "model": resp.model,
                "ok": True,
                "text": resp.text,
            }
        except Exception as exc:
            return {
                "provider": provider_name,
                "ok": False,
                "error": str(exc),
            }

    max_workers = min(8, len(providers))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_call_one, providers))
