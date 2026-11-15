from __future__ import annotations

import os
from odte_spy_ibkr.providers.base import BaseProvider, MarketContext, MarketContextDelta, ProviderNotConfigured


def _env_enabled(env_var: str) -> bool:
    return os.environ.get(env_var, "").lower() in {"1", "true", "yes", "on"}


class StubProvider(BaseProvider):
    """Placeholder for providers that need paid keys or MCP auth outside this process."""

    def __init__(
        self,
        provider_id: str,
        *,
        enabled: bool,
        enable_env: str,
        key_envs: list[str],
    ) -> None:
        super().__init__(enabled=enabled and _env_enabled(enable_env))
        self.provider_id = provider_id
        self._key_envs = key_envs

    def is_configured(self) -> bool:
        return all(os.environ.get(k, "").strip() for k in self._key_envs)

    def _fetch(self, ctx: MarketContext | None) -> MarketContextDelta:
        raise ProviderNotConfigured(
            self.provider_id,
            f"{self.provider_id} stub: set {','.join(self._key_envs)} and enable env flag",
        )


STUB_SPECS: list[tuple[str, str, list[str]]] = [
    ("you_finance", "YOU_FINANCE_ENABLED", ["YOU_API_KEY"]),
    ("context", "CONTEXT_ENABLED", ["CONTEXT_API_KEY"]),
    ("sp_global", "SP_GLOBAL_ENABLED", ["SP_GLOBAL_API_KEY"]),
    ("daloopa", "DALOOPA_ENABLED", ["DALOOPA_API_KEY"]),
    ("perspective", "PERSPECTIVE_ENABLED", ["PERSPECTIVE_API_KEY"]),
]


def build_stub_providers(enabled_ids: set[str]) -> list[BaseProvider]:
    out: list[BaseProvider] = []
    for provider_id, enable_env, keys in STUB_SPECS:
        out.append(
            StubProvider(
                provider_id,
                enabled=provider_id in enabled_ids,
                enable_env=enable_env,
                key_envs=keys,
            )
        )
    return out