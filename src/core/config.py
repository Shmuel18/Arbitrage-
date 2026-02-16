"""
Configuration — single source of truth.

Loads YAML first, then overlays environment variables.
Exchange credentials always come from env for security.
"""

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


# ── Sub-configs ──────────────────────────────────────────────────

class RiskLimits(BaseSettings):
    max_margin_usage: Decimal = Decimal("0.70")
    max_position_size_usd: Decimal = Decimal("10000")
    delta_threshold_pct: Decimal = Decimal("5.0")
    position_size_pct: Decimal = Decimal("0.70")


class TradingParams(BaseSettings):
    min_funding_spread: Decimal = Decimal("0.5")
    min_immediate_spread: Decimal = Decimal("0.5")   # min IMMEDIATE spread (next payment)
    min_net_pct: Decimal = Decimal("0.5")  # ← Requires 0.5% net profit (not 0.01%) after all fees & slippage
    max_slippage_pct: Decimal = Decimal("0.10")
    slippage_buffer_pct: Decimal = Decimal("0.015")  # Estimated slippage on entry/exit
    safety_buffer_pct: Decimal = Decimal("0.02")     # General safety margin
    basis_buffer_pct: Decimal = Decimal("0.01")      # Basis risk penalty
    cooldown_after_orphan_hours: int = 2
    entry_offset_seconds: int = 900
    exit_offset_seconds: int = 900
    max_entry_window_minutes: int = 60  # Only enter if closest funding is within N minutes
    quick_cycle: bool = True             # Exit after first funding payment (zero dead time)
    hold_min_spread: Decimal = Decimal("0.5")   # Min spread % to HOLD after funding collection
    upgrade_spread_delta: Decimal = Decimal("0.5")  # Switch to new opp if spread is +N% better
    top_opportunities_display: int = 5
    execute_only_best_opportunity: bool = True


class ExecutionConfig(BaseSettings):
    concurrent_opportunities: int = 3
    order_timeout_ms: int = 5000
    batch_scan_concurrent: bool = True
    scan_parallelism: int = 10


class RiskGuardConfig(BaseSettings):
    fast_loop_interval_sec: int = 5
    deep_loop_interval_sec: int = 60
    enable_panic_close: bool = True
    scanner_interval_sec: int = 10


class ExchangeConfig(BaseSettings):
    name: str
    ccxt_id: str
    default_type: str
    rate_limit_ms: int
    max_leverage: int
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    api_passphrase: Optional[str] = None
    testnet: bool = False
    leverage: Optional[int] = None
    margin_mode: Optional[str] = None
    position_mode: Optional[str] = None


class RedisConfig(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    db: int = 0
    key_prefix: str = "trinity:"
    lock_timeout_sec: int = 10

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class LoggingConfig(BaseSettings):
    level: str = "INFO"
    format: str = "json"
    console_output: bool = True
    file_output: bool = True
    log_dir: str = "logs"
    max_file_size_mb: int = 100
    backup_count: int = 10
    log_balances_on_startup: bool = True
    log_balances_after_trade: bool = True
    log_top_opportunities: bool = True


# ── Master config ────────────────────────────────────────────────

class Config(BaseSettings):
    environment: str = "development"
    version: str = "3.0.0"

    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    trading_params: TradingParams = Field(default_factory=TradingParams)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    risk_guard: RiskGuardConfig = Field(default_factory=RiskGuardConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    enabled_exchanges: List[str] = Field(default_factory=lambda: ["binance", "bybit"])
    exchanges: Dict[str, ExchangeConfig] = Field(default_factory=dict)

    watchlist: List[str] = Field(default_factory=list)

    paper_trading: bool = True
    dry_run: bool = True

    model_config = ConfigDict(extra="allow")

    # ── Loading ──────────────────────────────────────────────────

    @classmethod
    def load_from_yaml(cls, yaml_path: str = "config.yaml") -> "Config":
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {yaml_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        load_dotenv()

        # Overlay env overrides
        env_overrides = cls._env_overrides()
        merged = cls._deep_merge(data, env_overrides)

        # Restructure exchange / symbol sections
        if "exchanges" in merged:
            if "enabled" in merged["exchanges"]:
                merged["enabled_exchanges"] = merged["exchanges"].pop("enabled")
        if "symbols" in merged:
            wl = merged["symbols"].get("watchlist", [])
            merged["watchlist"] = wl if isinstance(wl, list) else []
            del merged["symbols"]

        # Inject API credentials from env
        cls._inject_credentials(merged.get("exchanges", {}))

        return cls(**merged)

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _env_overrides() -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if v := os.getenv("ENVIRONMENT"):
            out["environment"] = v
        if v := os.getenv("PAPER_TRADING"):
            out["paper_trading"] = v.lower() == "true"
        if v := os.getenv("DRY_RUN"):
            out["dry_run"] = v.lower() == "true"
        if os.getenv("REDIS_HOST"):
            out["redis"] = {
                "host": os.getenv("REDIS_HOST"),
                "port": int(os.getenv("REDIS_PORT", 6379)),
                "password": os.getenv("REDIS_PASSWORD"),
            }
        if v := os.getenv("LOG_LEVEL"):
            out.setdefault("logging", {})["level"] = v
        return out

    @staticmethod
    def _inject_credentials(exchanges: Dict[str, Any]) -> None:
        """Inject API keys from environment variables into exchange dicts."""
        env_map = {
            "binance":  ("BINANCE_API_KEY", "BINANCE_API_SECRET", None, "BINANCE_TESTNET"),
            "bybit":    ("BYBIT_API_KEY", "BYBIT_API_SECRET", None, "BYBIT_TESTNET"),
            "okx":      ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE", "OKX_TESTNET"),
            "gateio":   ("GATEIO_API_KEY", "GATEIO_API_SECRET", None, "GATEIO_TESTNET"),
            "kucoin":   ("KUCOIN_API_KEY", "KUCOIN_API_SECRET", "KUCOIN_PASSPHRASE", "KUCOIN_TESTNET"),
            "kraken":   ("KRAKEN_API_KEY", "KRAKEN_API_SECRET", None, "KRAKEN_TESTNET"),
        }
        for eid, (key_env, secret_env, pass_env, test_env) in env_map.items():
            if eid not in exchanges:
                continue
            exchanges[eid]["api_key"] = os.getenv(key_env)
            exchanges[eid]["api_secret"] = os.getenv(secret_env)
            if pass_env:
                exchanges[eid]["api_passphrase"] = os.getenv(pass_env)
            exchanges[eid]["testnet"] = os.getenv(test_env, "false").lower() == "true"

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = Config._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    # ── Validation ───────────────────────────────────────────────

    def validate_safety(self) -> None:
        """Drop exchanges that lack credentials; raise if none remain."""
        if self.paper_trading or self.dry_run:
            return

        valid = []
        for eid in self.enabled_exchanges:
            exc = self.exchanges.get(eid)
            if not exc or not exc.api_key or not exc.api_secret:
                print(f"⚠️  Skipping {eid} — missing API credentials")
                continue
            valid.append(eid)
        self.enabled_exchanges = valid

        if not self.enabled_exchanges:
            raise ValueError("No exchanges with valid credentials!")

        if self.risk_limits.max_margin_usage > Decimal("0.95"):
            raise ValueError(f"Margin usage too high: {self.risk_limits.max_margin_usage}")


# ── Singleton ────────────────────────────────────────────────────

_instance: Optional["Config"] = None


def get_config() -> "Config":
    global _instance
    if _instance is None:
        _instance = Config.load_from_yaml()
    return _instance


def init_config(path: str = "config.yaml") -> "Config":
    global _instance
    _instance = Config.load_from_yaml(path)
    return _instance
