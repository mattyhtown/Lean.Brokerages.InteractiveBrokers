from __future__ import annotations

import os
from datetime import time
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class UWMode(str, Enum):
    ALERTS_ONLY = "alerts_only"
    GATE = "gate"


class CreditSide(str, Enum):
    PUT = "put"
    CALL = "call"
    AUTO = "auto"


class MacroFlowConflict(str, Enum):
    BLOCK = "block"
    DOWNSIZE = "downsize"
    IGNORE = "ignore"


class UWBacktestMode(str, Enum):
    SKIP = "skip"
    NEUTRAL = "neutral"
    BLOCK = "block"


class MacroBacktestMode(str, Enum):
    PERMISSIVE = "permissive"
    STRICT = "strict"


def _parse_time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Expected HH:MM, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    return time(hour=hour, minute=minute)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # IB connectivity
    ib_host: str = Field(default="127.0.0.1", alias="IB_HOST")
    ib_port: int = Field(default=7497, alias="IB_PORT")
    ib_client_id: int = Field(default=7, alias="IB_CLIENT_ID")
    ib_account: str = Field(default="", alias="IB_ACCOUNT")
    paper: bool = Field(default=True, alias="PAPER")

    live_trading: bool = Field(default=False, alias="LIVE_TRADING")
    read_only: bool = Field(default=True, alias="READ_ONLY")
    enable_orders: bool = Field(default=False, alias="ENABLE_ORDERS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    config_yaml: str = Field(default="", alias="CONFIG_YAML")

    # Strategy
    underlying: str = Field(default="SPY", alias="UNDERLYING")
    credit_side: CreditSide = Field(default=CreditSide.PUT, alias="CREDIT_SIDE")
    min_minutes_after_open: int = Field(default=30, alias="MIN_MINUTES_AFTER_OPEN")
    min_credit: float = Field(default=0.15, alias="MIN_CREDIT")
    spread_width: float = Field(default=1.0, alias="SPREAD_WIDTH")
    short_delta_min: float = Field(default=0.10, alias="SHORT_DELTA_MIN")
    short_delta_max: float = Field(default=0.20, alias="SHORT_DELTA_MAX")
    otm_distance: float | None = Field(default=None, alias="OTM_DISTANCE")
    max_contracts: int = Field(default=1, alias="MAX_CONTRACTS")
    max_daily_loss: float = Field(default=500.0, alias="MAX_DAILY_LOSS")
    entry_cutoff: time = Field(default=time(15, 0), alias="ENTRY_CUTOFF")
    flatten_time: time = Field(default=time(15, 45), alias="FLATTEN_TIME")
    poll_interval_sec: float = Field(default=30.0, alias="POLL_INTERVAL_SEC")
    max_net_call_risk: float = Field(default=50_000.0, alias="MAX_NET_CALL_RISK")
    strong_bullish_skew: float = Field(default=0.55, alias="STRONG_BULLISH_SKEW")
    stop_loss_credit_multiple: float = Field(
        default=0.0,
        alias="STOP_LOSS_CREDIT_MULTIPLE",
        description="Stop when the spread mark reaches this multiple of the entry credit; 0 disables.",
    )

    # Unusual Whales
    unusual_whales_api_key: str = Field(default="", alias="UNUSUAL_WHALES_API_KEY")
    uw_base_url: str = Field(default="https://api.unusualwhales.com", alias="UW_BASE_URL")
    uw_required: bool = Field(default=False, alias="UW_REQUIRED")
    uw_min_strength: float = Field(default=0.35, alias="UW_MIN_STRENGTH")
    uw_mode: UWMode = Field(default=UWMode.ALERTS_ONLY, alias="UW_MODE")
    uw_poll_interval_sec: float = Field(default=120.0, alias="UW_POLL_INTERVAL_SEC")
    uw_cache_ttl_sec: float = Field(default=90.0, alias="UW_CACHE_TTL_SEC")
    uw_stale_block_sec: float = Field(default=600.0, alias="UW_STALE_BLOCK_SEC")
    uw_flow_required: bool = Field(default=False, alias="UW_FLOW_REQUIRED")

    # Multi-provider market data
    providers_enabled: str = Field(
        default="ibkr,unusual_whales,alpaca",
        alias="PROVIDERS_ENABLED",
    )

    # Alpaca (read-only data cross-check)
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")
    alpaca_data_url: str = Field(default="https://data.alpaca.markets", alias="ALPACA_DATA_URL")

    # Coinbase macro overlay (public stats; keys optional for future private endpoints)
    coinbase_api_key: str = Field(default="", alias="COINBASE_API_KEY")
    coinbase_api_secret: str = Field(default="", alias="COINBASE_API_SECRET")
    coinbase_risk_off_threshold_pct: float = Field(default=3.0, alias="COINBASE_RISK_OFF_THRESHOLD_PCT")
    coinbase_products: str = Field(default="BTC-USD,ETH-USD", alias="COINBASE_PRODUCTS")

    # Macro news
    macro_enabled: bool = Field(default=True, alias="MACRO_ENABLED")
    macro_tier1_blackout_before_min: int = Field(default=30, alias="MACRO_BLACKOUT_BEFORE_MIN")
    macro_tier1_blackout_after_min: int = Field(default=45, alias="MACRO_BLACKOUT_AFTER_MIN")
    macro_headline_lookback_min: int = Field(default=60, alias="MACRO_HEADLINE_LOOKBACK_MIN")
    macro_headline_intensity_block: float = Field(default=0.75, alias="MACRO_HEADLINE_INTENSITY_BLOCK")
    macro_size_scale_under_stress: float = Field(default=0.5, alias="MACRO_SIZE_SCALE")
    macro_flow_conflict: MacroFlowConflict = Field(default=MacroFlowConflict.DOWNSIZE, alias="MACRO_FLOW_CONFLICT")
    macro_backtest_mode: MacroBacktestMode = Field(default=MacroBacktestMode.PERMISSIVE, alias="MACRO_BACKTEST_MODE")
    macro_calendar_path: str = Field(default="config/macro_events.yaml", alias="MACRO_CALENDAR_PATH")
    macro_headline_refresh_sec: float = Field(default=120.0, alias="MACRO_HEADLINE_REFRESH_SEC")
    context_api_key: str = Field(default="", alias="CONTEXT_API_KEY")

    # Sharpe gate (live)
    sharpe_gate_enabled: bool = Field(default=False, alias="SHARPE_GATE_ENABLED")
    sharpe_min: float = Field(default=1.5, alias="SHARPE_MIN")
    sharpe_window_days: int = Field(default=60, alias="SHARPE_WINDOW_DAYS")
    sharpe_min_obs: int = Field(default=60, alias="SHARPE_MIN_OBS")
    journal_path: str = Field(default="data/journal/daily_pnl.jsonl", alias="JOURNAL_PATH")

    # Backtest
    backtest_equity_base: float = Field(default=100_000.0, alias="BACKTEST_EQUITY_BASE")
    backtest_rolling_window_days: int = Field(default=60, alias="BACKTEST_ROLLING_WINDOW_DAYS")
    backtest_sharpe_target: float = Field(default=1.5, alias="BACKTEST_SHARPE_TARGET")
    backtest_slippage_ticks: float = Field(default=1.0, alias="BACKTEST_SLIPPAGE_TICKS")
    backtest_commission_per_contract: float = Field(default=0.65, alias="BACKTEST_COMMISSION_PER_CONTRACT")
    backtest_cache_dir: str = Field(default="data/cache", alias="BACKTEST_CACHE_DIR")
    uw_backtest_cache_dir: str = Field(default="data/cache/uw", alias="UW_BACKTEST_CACHE_DIR")
    uw_backtest_mode: UWBacktestMode = Field(default=UWBacktestMode.NEUTRAL, alias="UW_BACKTEST_MODE")
    uw_session_jsonl_dir: str = Field(default="data/cache/uw/sessions", alias="UW_SESSION_JSONL_DIR")
    backtest_walk_forward_train_days: int = Field(default=60, alias="BACKTEST_WALK_FORWARD_TRAIN_DAYS")
    backtest_walk_forward_test_days: int = Field(default=20, alias="BACKTEST_WALK_FORWARD_TEST_DAYS")
    backtest_require_sharpe_pass: bool = Field(default=False, alias="BACKTEST_REQUIRE_SHARPE_PASS")
    backtest_decision_interval_min: int = Field(default=5, alias="BACKTEST_DECISION_INTERVAL_MIN")
    backtest_strike_range: int = Field(default=15, alias="BACKTEST_STRIKE_RANGE")
    backtest_snapshot_time: time = Field(default=time(11, 0), alias="BACKTEST_SNAPSHOT_TIME")
    backtest_mark_max_age_min: int = Field(default=5, alias="BACKTEST_MARK_MAX_AGE_MIN")
    backtest_quote_half_spread: float = Field(default=0.01, alias="BACKTEST_QUOTE_HALF_SPREAD")
    backtest_stock_feed: str = Field(default="sip", alias="BACKTEST_STOCK_FEED")
    providers_backtest_enabled: str = Field(
        default="alpaca,unusual_whales,macro_news",
        alias="PROVIDERS_BACKTEST_ENABLED",
    )

    # Improvement engine
    improvement_auto_promote_paper: bool = Field(default=True, alias="IMPROVEMENT_AUTO_PROMOTE_PAPER")
    improvement_auto_promote_live: bool = Field(default=False, alias="IMPROVEMENT_AUTO_PROMOTE_LIVE")
    improvement_sharpe_margin: float = Field(default=0.1, alias="IMPROVEMENT_SHARPE_MARGIN")
    improvement_max_dd_penalty: float = Field(default=0.25, alias="IMPROVEMENT_MAX_DD_PENALTY")
    params_active_path: str = Field(default="params/active.yaml", alias="PARAMS_ACTIVE_PATH")
    params_history_dir: str = Field(default="params/history", alias="PARAMS_HISTORY_DIR")

    @property
    def providers_enabled_list(self) -> list[str]:
        return [p.strip().lower() for p in self.providers_enabled.split(",") if p.strip()]

    @property
    def providers_enabled_set(self) -> set[str]:
        return set(self.providers_enabled_list)

    @property
    def providers_backtest_enabled_set(self) -> set[str]:
        return {p.strip().lower() for p in self.providers_backtest_enabled.split(",") if p.strip()}

    @property
    def coinbase_products_list(self) -> list[str]:
        return [p.strip().upper() for p in self.coinbase_products.split(",") if p.strip()]

    @field_validator("entry_cutoff", "flatten_time", mode="before")
    @classmethod
    def _time_fields(cls, v: Any) -> time:
        if v is None:
            return time(15, 0)
        return _parse_time(v)

    @field_validator(
        "credit_side",
        "uw_mode",
        "macro_flow_conflict",
        "uw_backtest_mode",
        "macro_backtest_mode",
        mode="before",
    )
    @classmethod
    def _enum_fields(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, str):
            return v.lower().strip()
        return v

    @field_validator("backtest_snapshot_time", mode="before")
    @classmethod
    def _snapshot_time_field(cls, v: Any) -> time:
        return _parse_time(v)

    def apply_yaml(self, path: str | Path) -> None:
        p = Path(path)
        if not p.is_file():
            return
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        mapping = {
            "ib": {
                "host": "ib_host",
                "port": "ib_port",
                "client_id": "ib_client_id",
                "account": "ib_account",
                "paper": "paper",
            },
            "safety": {
                "live_trading": "live_trading",
                "read_only": "read_only",
                "enable_orders": "enable_orders",
            },
            "strategy": {
                "underlying": "underlying",
                "credit_side": "credit_side",
                "min_minutes_after_open": "min_minutes_after_open",
                "min_credit": "min_credit",
                "spread_width": "spread_width",
                "short_delta_min": "short_delta_min",
                "short_delta_max": "short_delta_max",
                "otm_distance": "otm_distance",
                "max_contracts": "max_contracts",
                "max_daily_loss": "max_daily_loss",
                "entry_cutoff": "entry_cutoff",
                "flatten_time": "flatten_time",
                "poll_interval_sec": "poll_interval_sec",
                "stop_loss_credit_multiple": "stop_loss_credit_multiple",
            },
            "unusual_whales": {
                "base_url": "uw_base_url",
                "required": "uw_required",
                "min_strength": "uw_min_strength",
                "mode": "uw_mode",
                "poll_interval_sec": "uw_poll_interval_sec",
                "cache_ttl_sec": "uw_cache_ttl_sec",
                "stale_block_sec": "uw_stale_block_sec",
                "backtest_cache_dir": "uw_backtest_cache_dir",
                "backtest_mode": "uw_backtest_mode",
            },
            "providers": {
                "enabled": "providers_enabled",
                "backtest_enabled": "providers_backtest_enabled",
            },
            "alpaca": {
                "api_key": "alpaca_api_key",
                "api_secret": "alpaca_api_secret",
                "data_url": "alpaca_data_url",
            },
            "coinbase": {
                "risk_off_threshold_pct": "coinbase_risk_off_threshold_pct",
                "products": "coinbase_products",
            },
            "macro": {
                "enabled": "macro_enabled",
                "tier1_blackout_before_min": "macro_tier1_blackout_before_min",
                "tier1_blackout_after_min": "macro_tier1_blackout_after_min",
                "headline_lookback_min": "macro_headline_lookback_min",
                "headline_intensity_block": "macro_headline_intensity_block",
                "size_scale_under_macro_stress": "macro_size_scale_under_stress",
                "flow_conflict": "macro_flow_conflict",
                "backtest_mode": "macro_backtest_mode",
                "calendar_path": "macro_calendar_path",
            },
            "backtest": {
                "equity_base": "backtest_equity_base",
                "rolling_window_days": "backtest_rolling_window_days",
                "sharpe_target": "backtest_sharpe_target",
                "slippage_ticks": "backtest_slippage_ticks",
                "commission_per_contract": "backtest_commission_per_contract",
                "cache_dir": "backtest_cache_dir",
                "require_sharpe_pass": "backtest_require_sharpe_pass",
                "decision_interval_min": "backtest_decision_interval_min",
                "walk_forward_train_days": "backtest_walk_forward_train_days",
                "walk_forward_test_days": "backtest_walk_forward_test_days",
            },
            "risk": {
                "sharpe_gate_enabled": "sharpe_gate_enabled",
                "sharpe_min": "sharpe_min",
                "sharpe_window_days": "sharpe_window_days",
                "sharpe_min_obs": "sharpe_min_obs",
                "journal_path": "journal_path",
            },
        }
        for section, keys in mapping.items():
            block = data.get(section) or {}
            for yaml_key, attr in keys.items():
                if yaml_key in block and block[yaml_key] is not None:
                    # Validate like env input so "15:00" becomes a time and "put" an enum.
                    self.__pydantic_validator__.validate_assignment(self, attr, block[yaml_key])

    @property
    def orders_allowed(self) -> bool:
        return self.enable_orders and not self.read_only and (
            self.paper or self.live_trading
        )


def load_settings(config_path: str | Path | None = None) -> Settings:
    settings = Settings()
    yaml_path = str(config_path or "") or settings.config_yaml or os.environ.get("CONFIG_YAML", "")
    if not yaml_path:
        candidate = Path("config.yaml")
        if candidate.is_file():
            yaml_path = str(candidate)
    if yaml_path:
        settings.apply_yaml(yaml_path)
    return settings