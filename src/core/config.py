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
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings


# ── Sub-configs ──────────────────────────────────────────────────

class RiskLimits(BaseModel):
    max_margin_usage: Decimal = Decimal("0.70")
    max_position_size_usd: Decimal = Decimal("10000")
    delta_threshold_pct: Decimal = Decimal("5.0")
    position_size_pct: Decimal = Decimal("0.70")


class TradingParams(BaseModel):
    min_funding_spread: Decimal = Decimal("0.3")  # min spread of next imminent payment (%) — no 8h normalization
    slippage_buffer_pct: Decimal = Decimal("0.015")  # Estimated slippage on entry/exit
    safety_buffer_pct: Decimal = Decimal("0.02")     # General safety margin
    max_market_data_age_ms: int = 2000  # Require bid/ask data newer than this before qualifying an entry
    cooldown_after_orphan_hours: int = 2
    cooldown_after_close_seconds: int = 120  # Block re-entry into same symbol after any close
    max_sane_funding_rate: Decimal = Decimal("0.10")  # max abs funding rate before filtering
    entry_offset_seconds: int = 900
    exit_offset_seconds: int = 900
    min_entry_secs_before_funding: int = 120  # Reject entry if next funding is ≤ N seconds away (too close to hedge properly)
    max_entry_window_minutes: int = 60  # Only enter if closest funding is within N minutes
    narrow_entry_window_minutes: int = 15  # For MEDIUM/BAD tiers, only enter if funding is within N minutes
    upgrade_spread_delta: Decimal = Decimal("0.5")  # Switch to new opp if spread is +N% better
    upgrade_cooldown_seconds: int = 300  # Block re-entry of upgraded symbol for N seconds
    min_upgrade_hold_seconds: int = 180  # Minimum hold time (s) before a trade is eligible for upgrade (prevents rapid churn)
    upgrade_funding_lock_secs: int = 180  # Lock upgrades when funding is within N seconds
    execute_only_best_opportunity: bool = True
    # Tier-based entry strategy
    weak_min_funding_excess: Decimal = Decimal("0.5")  # WEAK tier: funding must exceed adverse spread by this %
    # Exit strategy
    profit_target_pct: Decimal = Decimal("0.7")  # Exit at 0.7% profit on notional
    exit_slippage_buffer_pct: Decimal = Decimal("0.3")  # Extra margin deducted from PnL before profit target check
    basis_recovery_timeout_minutes: Decimal = Decimal("30")  # After funding, wait up to 30min for basis recovery
    basis_recovery_tolerance_pct: Decimal = Decimal("0.10")  # Tolerance (%) for basis recovery — exit if within this of entry
    liquidation_safety_pct: Decimal = Decimal("5.0")  # Exit when equity/margin < this % (5 → exit at 95% loss, near liquidation)


class ExecutionConfig(BaseModel):
    concurrent_opportunities: int = 3
    order_timeout_ms: int = 10000
    scan_parallelism: int = 10
    entry_refetch_attempts: int = 1
    entry_refetch_interval_ms: int = 250


class RiskGuardConfig(BaseModel):
    fast_loop_interval_sec: int = 5
    deep_loop_interval_sec: int = 60
    enable_panic_close: bool = True
    scanner_interval_sec: int = 10


class ExchangeConfig(BaseModel):
    name: str
    ccxt_id: str
    default_type: str
    rate_limit_ms: int
    max_leverage: int
    api_key: Optional[SecretStr] = None
    api_secret: Optional[SecretStr] = None
    api_passphrase: Optional[SecretStr] = None
    testnet: bool = False
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
    margin_mode: Optional[str] = None
    position_mode: Optional[str] = None

    def to_adapter_dict(self) -> dict:
        """Return a plain dict for the ExchangeAdapter, unwrapping SecretStr
        fields at the only boundary where raw credential values are needed.

        Never call model_dump() on ExchangeConfig directly — it would expose
        masked fields as '**********' strings rather than the real values.
        """
        data = self.model_dump(exclude={"api_key", "api_secret", "api_passphrase"})
        data["api_key"] = self.api_key.get_secret_value() if self.api_key else None
        data["api_secret"] = self.api_secret.get_secret_value() if self.api_secret else None
        data["api_passphrase"] = (
            self.api_passphrase.get_secret_value() if self.api_passphrase else None
        )
        return data


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    password: Optional[SecretStr] = None  # never embedded in URL — passed as separate kwarg
    db: int = 0
    tls: bool = False  # Enable TLS (rediss://) — required for remote/cloud Redis (e.g. Redis Cloud, Upstash)
    key_prefix: str = "trinity:"
    lock_timeout_sec: int = 10

    @property
    def url(self) -> str:
        """Safe URL with no credentials — suitable for logging.

        Uses ``rediss://`` scheme when tls=True, signalling TLS to aioredis
        and making the URL self-describing in logs.
        """
        scheme = "rediss" if self.tls else "redis"
        return f"{scheme}://{self.host}:{self.port}/{self.db}"

    @property
    def password_plaintext(self) -> Optional[str]:
        """Unwrap the password only at the connection boundary."""
        return self.password.get_secret_value() if self.password else None


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    console_output: bool = True
    file_output: bool = True
    log_dir: str = "logs"
    max_file_size_mb: int = 100
    backup_count: int = 10
    log_balances_on_startup: bool = True
    log_balances_after_trade: bool = True


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
            "bitget":   ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_PASSPHRASE", "BITGET_TESTNET"),
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
