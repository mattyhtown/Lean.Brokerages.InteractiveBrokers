"""Unified multi-LLM provider registry, HTTP adapters, and MCP template generator."""

from multi_llm.client import ChatRequest, ChatResponse, complete, fanout_complete
from multi_llm.mcp_templates import SUPPORTED_TARGETS, render_mcp_config
from multi_llm.providers import PROVIDERS, ProviderSpec, get_provider, list_provider_statuses

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "PROVIDERS",
    "ProviderSpec",
    "SUPPORTED_TARGETS",
    "complete",
    "fanout_complete",
    "get_provider",
    "list_provider_statuses",
    "render_mcp_config",
]

__version__ = "0.1.0"
