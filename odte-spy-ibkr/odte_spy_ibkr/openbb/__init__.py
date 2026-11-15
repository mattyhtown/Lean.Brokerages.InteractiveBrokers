"""OpenBB Platform integration (optional `openbb` package)."""

from odte_spy_ibkr.openbb.client import (
    OPENBB_HISTORICAL_NOTES,
    configure_openbb_session,
    fetch_option_chains,
    openbb_available,
    require_openbb,
)

__all__ = [
    "OPENBB_HISTORICAL_NOTES",
    "configure_openbb_session",
    "fetch_option_chains",
    "openbb_available",
    "require_openbb",
]