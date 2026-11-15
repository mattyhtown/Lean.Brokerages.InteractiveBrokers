from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import ipaddress
import json
import os
import re
from typing import Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request

from multi_llm.mcp_templates import SUPABASE_MCP_URL, UNUSUAL_WHALES_MCP_URL
from multi_llm.providers import (
    CLASSIFICATION_PROTOCOL,
    WATSONX_PROJECT_ENV,
    WATSONX_PROTOCOL,
    ProviderSpec,
    get_provider,
    resolve_api_key,
)

TransportCallable = Callable[[str, dict[str, str], dict[str, object], int], object]


class LLMError(Exception):
    pass


# AWS region ids used only as a DNS label inside bedrock-runtime.<region>.amazonaws.com
_AWS_REGION_RE = re.compile(
    r"^(?:[a-z]{2}-(?:gov-)?[a-z0-9]+-\d+|cn-(?:north|northwest)-\d+)$"
)
_BEDROCK_HOST_SUFFIX = ".amazonaws.com"


def _validate_aws_region(region: str) -> str:
    cleaned = region.strip().lower()
    if not cleaned or not _AWS_REGION_RE.fullmatch(cleaned):
        raise LLMError(
            f"Invalid AWS_REGION for Bedrock: {region!r} "
            "(expected a value like us-east-1 or eu-west-1)"
        )
    return cleaned


def _bedrock_runtime_url(region: str, encoded_model: str) -> str:
    safe_region = _validate_aws_region(region)
    host = f"bedrock-runtime.{safe_region}{_BEDROCK_HOST_SUFFIX}"
    return f"https://{host}/model/{encoded_model}/converse"


_AZURE_OPENAI_ENDPOINT_RE = re.compile(
    r"^https://[a-z0-9][\w.-]*\.(openai\.azure\.com|cognitiveservices\.azure\.com)(/|$)",
    re.IGNORECASE,
)
_ANTHROPIC_MCP_BETA = "mcp-client-2025-11-20"
# watsonx.ai requires a dated version query parameter on every request.
_WATSONX_API_VERSION = "2024-10-08"
# Each segment must start with an alphanumeric, which rules out "." and ".." path segments.
_HF_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?$")


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    lowered = hostname.lower().strip("[]")
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return lowered == "localhost" or lowered.endswith(".localhost")


def _validate_base_url(url: str, *, allow_http_loopback: bool) -> str:
    cleaned = url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise LLMError(f"Invalid provider URL: {url!r}")
    if parsed.scheme == "http" and not (allow_http_loopback and _is_loopback_host(parsed.hostname)):
        raise LLMError(f"Refusing HTTP provider URL (use HTTPS): {url!r}")
    return cleaned


def _validate_azure_openai_endpoint(endpoint: str) -> str:
    cleaned = endpoint.strip().rstrip("/")
    if not _AZURE_OPENAI_ENDPOINT_RE.match(cleaned):
        raise LLMError(
            f"Invalid AZURE_OPENAI_ENDPOINT: {endpoint!r} "
            "(expected https://<resource>.openai.azure.com)"
        )
    return cleaned


def _assert_credential_transport_safe(url: str, headers: dict[str, str]) -> None:
    if not headers.get("Authorization") and not headers.get("api-key"):
        return
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _is_loopback_host(parsed.hostname):
        return
    raise LLMError(f"Refusing to send API credentials over non-HTTPS URL: {url}")


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
) -> object:
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("https", "http") or not parsed_url.netloc:
        raise LLMError(f"Refusing non-HTTP(S) provider URL: {url}")
    _assert_credential_transport_safe(url, headers)
    encoded = json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Accept": "application/json", **headers}
    request = urllib.request.Request(url, data=encoded, headers=req_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


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
            headers["anthropic-beta"] = _ANTHROPIC_MCP_BETA
            body["mcp_servers"] = [
                {
                    "type": "url",
                    "name": label,
                    "url": server_url,
                    "authorization_token": token,
                }
                for label, server_url, token in mcp_entries
            ]
            body["tools"] = [
                {"type": "mcp_toolset", "mcp_server_name": label}
                for label, _, _ in mcp_entries
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
                    "require_approval": "always",
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

    if spec.protocol == CLASSIFICATION_PROTOCOL:
        if not _HF_MODEL_ID_RE.fullmatch(model):
            raise LLMError(f"Invalid Hugging Face model id for provider '{spec.name}': {model!r}")
        headers = {"Authorization": f"Bearer {api_key}"}
        return spec, f"{spec.base_url}/{model}", headers, {"inputs": req.prompt}, secrets

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
        endpoint_raw = source.get(spec.endpoint_env or "", "").strip()
        if not endpoint_raw:
            raise LLMError("Missing AZURE_OPENAI_ENDPOINT for provider 'azure_openai'")
        endpoint = _validate_azure_openai_endpoint(endpoint_raw)
        deployment = req.model or source.get(spec.deployment_env or "", "").strip()
        if not deployment:
            raise LLMError(
                "Missing AZURE_OPENAI_DEPLOYMENT for provider 'azure_openai' "
                "(set AZURE_OPENAI_DEPLOYMENT or pass --model)"
            )
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
        url = _bedrock_runtime_url(region, encoded_model)
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

    if spec.protocol == WATSONX_PROTOCOL:
        project_id = source.get(WATSONX_PROJECT_ENV, "").strip()
        if not project_id:
            raise LLMError(f"Missing {WATSONX_PROJECT_ENV} for provider '{spec.name}'")
        custom_host = source.get(spec.endpoint_env or "", "").strip()
        host = _validate_base_url(custom_host or spec.base_url, allow_http_loopback=False)
        # The API path is appended here, so accept a WATSONX_URL that already ends in it.
        host = host.removesuffix("/ml/v1")
        url = f"{host}/ml/v1/text/chat?version={_WATSONX_API_VERSION}"
        headers = {"Authorization": f"Bearer {api_key}"}
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})
        body = {
            "model_id": model,
            "project_id": project_id,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
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
    if not base_url.strip():
        if spec.endpoint_env:
            raise LLMError(f"Missing {spec.endpoint_env} for provider '{spec.name}'")
        raise LLMError(f"No base URL configured for provider '{spec.name}'")
    allow_http_loopback = spec.name in ("ollama", "openai_compatible")
    base_url = _validate_base_url(base_url, allow_http_loopback=allow_http_loopback)
    url = f"{base_url}/chat/completions"
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


def _mcp_approval_notice(item: dict[str, object]) -> str:
    return (
        "[mcp approval required: "
        f"{item.get('server_label')}.{item.get('name')} "
        f"arguments={item.get('arguments')}]"
    )


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
        output_items = data.get("output")
        if isinstance(data.get("output_text"), str) and data["output_text"]:
            # output_text aggregates message text only, so a pending approval would otherwise be hidden.
            approvals = [
                _mcp_approval_notice(item)
                for item in (output_items if isinstance(output_items, list) else [])
                if isinstance(item, dict) and item.get("type") == "mcp_approval_request"
            ]
            text = "\n".join([str(data["output_text"]), *approvals])
            return ChatResponse(spec.name, str(data.get("model") or model), text, data)
        status = str(data.get("status", "")).lower()
        if status in ("failed", "cancelled", "incomplete"):
            detail = data.get("incomplete_details") or data.get("error")
            if isinstance(detail, dict):
                message = detail.get("message") or detail.get("reason")
                if isinstance(message, str) and message.strip():
                    return ChatResponse(
                        spec.name,
                        str(data.get("model") or model),
                        f"[{status}: {message.strip()}]",
                        data,
                    )
        parts: list[str] = []
        if isinstance(output_items, list):
            for item in output_items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "mcp_approval_request":
                    parts.append(_mcp_approval_notice(item))
                content = item.get("content")
                if isinstance(content, list):
                    for chunk in content:
                        if not isinstance(chunk, dict):
                            continue
                        refusal = chunk.get("refusal")
                        if chunk.get("type") == "refusal" and isinstance(refusal, str) and refusal.strip():
                            parts.append(f"[refusal: {refusal.strip()}]")
                        elif isinstance(chunk.get("text"), str):
                            parts.append(chunk["text"])
        return ChatResponse(spec.name, str(data.get("model") or model), "\n".join(parts).strip(), data)

    if spec.protocol == "google_gemini":
        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict):
            block = feedback.get("blockReason")
            if isinstance(block, str) and block.strip():
                return ChatResponse(
                    spec.name,
                    str(data.get("modelVersion") or model),
                    f"[blocked: {block.strip()}]",
                    data,
                )
        candidates = data.get("candidates")
        parts = []
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                finish = first.get("finishReason")
                if isinstance(finish, str) and finish.upper() in ("SAFETY", "RECITATION", "BLOCKLIST"):
                    return ChatResponse(
                        spec.name,
                        str(data.get("modelVersion") or model),
                        f"[blocked: {finish}]",
                        data,
                    )
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
            if isinstance(msg, dict):
                refusal = msg.get("refusal")
                if isinstance(refusal, str) and refusal.strip():
                    text = f"[refusal: {refusal.strip()}]"
                elif isinstance(msg.get("content"), str):
                    text = msg["content"].strip()
    return ChatResponse(spec.name, str(data.get("model") or model), text, data)


def parse_classification_response(spec: ProviderSpec, model: str, data: object) -> ChatResponse:
    rows = data
    # Hugging Face returns one list of label scores per input.
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        rows = rows[0]
    scored: list[tuple[str, float]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            label, score = row.get("label"), row.get("score")
            if isinstance(label, str) and isinstance(score, (int, float)) and not isinstance(score, bool):
                scored.append((label, float(score)))
    if not scored:
        detail = data.get("error") if isinstance(data, dict) else None
        suffix = f": {detail}" if isinstance(detail, str) and detail.strip() else ""
        raise LLMError(f"Unexpected classification response from {spec.name}{suffix}")
    scored.sort(key=lambda item: item[1], reverse=True)
    text = ", ".join(f"{label} {score:.3f}" for label, score in scored)
    labels = [{"label": label, "score": score} for label, score in scored]
    return ChatResponse(spec.name, model, text, {"labels": labels})


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
    if spec.protocol == CLASSIFICATION_PROTOCOL:
        return parse_classification_response(spec, model, raw)
    if not isinstance(raw, dict):
        raise LLMError("Expected JSON object response from provider")
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
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futures = [pool.submit(_call_one, name) for name in providers]
    try:
        results = [future.result() for future in futures]
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return results
