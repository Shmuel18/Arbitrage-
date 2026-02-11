"""
Configuration Manager
Loads and validates system configuration
"""

import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class RiskLimits(BaseSettings):
    """Risk management limits"""
    max_margin_usage: Decimal = Decimal('0.30')
    max_position_size_usd: Decimal = Decimal('10000')
    max_orphan_time_ms: int = 500
    max_hedge_gap_ms: int = 1000
    max_ws_staleness_ms: int = 500
    delta_threshold_pct: Decimal = Decimal('5.0')
    min_liquidation_distance_pct: Decimal = Decimal('25.0')


class TradingParams(BaseSettings):
    """Trading strategy parameters"""
    min_net_bps: Decimal = Decimal('5.0')
    slippage_buffer_bps: Decimal = Decimal('2.0')
    safety_buffer_bps: Decimal = Decimal('3.0')
    basis_buffer_bps: Decimal = Decimal('1.0')
    max_chase_attempts: int = 3
    max_open_time_ms: int = 1200
    cooldown_after_orphan_hours: int = 2
    cooldown_after_outage_hours: int = 24


class ExecutionConfig(BaseSettings):
    """Execution engine configuration"""
    concurrent_opportunities: int = 3
    order_retry_attempts: int = 3
    order_timeout_ms: int = 5000
    cancel_timeout_ms: int = 2000
    position_poll_interval_ms: int = 200


class RiskGuardConfig(BaseSettings):
    """Risk guard settings"""
    fast_loop_interval_sec: int = 5
    deep_loop_interval_sec: int = 60
    reconciliation_interval_sec: int = 5
    enable_auto_rebalance: bool = True
    enable_panic_close: bool = True


class DataIngestionConfig(BaseSettings):
    """Data ingestion settings"""
    ws_reconnect_delay_sec: int = 5
    ws_max_reconnects: int = 10
    orderbook_depth: int = 10
    health_check_interval_sec: int = 1
    sequence_gap_tolerance: int = 0


class DiscoveryConfig(BaseSettings):
    """Discovery scanner settings"""
    scan_interval_ms: int = 200
    min_opportunity_duration_sec: int = 60
    min_orderbook_depth_usd: Decimal = Decimal('5000')
    max_spread_bps: Decimal = Decimal('10')


class ExchangeConfig(BaseSettings):
    """Single exchange configuration"""
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
    margin_mode: Optional[str] = None  # isolated | cross
    position_mode: Optional[str] = None  # hedged | oneway


class MonitoringConfig(BaseSettings):
    """Monitoring and alerts configuration"""
    metrics_port: int = 9090
    enable_prometheus: bool = True
    enable_telegram: bool = True
    enable_sentry: bool = True
    daily_summary_hour_utc: int = 0
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    sentry_dsn: Optional[str] = None
    
    class Config:
        extra = "allow"


class DatabaseConfig(BaseSettings):
    """Database connection settings"""
    host: str = "localhost"
    port: int = 5432
    database: str = "trinity_arbitrage"
    user: str = "trinity"
    password: str = ""
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout_sec: int = 30
    echo_sql: bool = False
    
    @property
    def dsn(self) -> str:
        """Get PostgreSQL connection string"""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class RedisConfig(BaseSettings):
    """Redis connection settings"""
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    db: int = 0
    key_prefix: str = "trinity:"
    default_ttl_sec: int = 3600
    lock_timeout_sec: int = 10
    retry_attempts: int = 3
    
    @property
    def url(self) -> str:
        """Get Redis connection URL"""
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class LoggingConfig(BaseSettings):
    """Logging configuration"""
    level: str = "INFO"
    format: str = "json"
    console_output: bool = True
    file_output: bool = True
    log_dir: str = "logs"
    max_file_size_mb: int = 100
    backup_count: int = 10
    audit_all_orders: bool = True
    audit_all_decisions: bool = True


class Config(BaseSettings):
    """
    Master configuration class
    Loads from environment and YAML
    """
    
    # System
    environment: str = Field(default="development")
    version: str = "2.1.0"
    timezone: str = "UTC"
    
    # Components
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    trading_params: TradingParams = Field(default_factory=TradingParams)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    risk_guard: RiskGuardConfig = Field(default_factory=RiskGuardConfig)
    data_ingestion: DataIngestionConfig = Field(default_factory=DataIngestionConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    # Exchanges
    enabled_exchanges: List[str] = Field(default_factory=lambda: ["binance", "bybit", "okx"])
    exchanges: Dict[str, ExchangeConfig] = Field(default_factory=dict)
    
    # Symbols
    watchlist: List[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    blacklist: List[str] = Field(default_factory=list)
    
    # Safety
    paper_trading: bool = True
    dry_run: bool = True
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "allow"  # Allow extra fields from YAML
    
    @classmethod
    def load_from_yaml(cls, yaml_path: str = "config.yaml") -> "Config":
        """
        Load configuration from YAML file and environment
        Environment variables override YAML
        """
        config_path = Path(yaml_path)
        
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_path}")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            yaml_data = yaml.safe_load(f)
        
        # Load environment variables from .env
        load_dotenv()
        
        # Load environment variables
        env_overrides = cls._load_env_overrides()
        
        # Merge configurations (env takes priority)
        merged = cls._deep_merge(yaml_data, env_overrides)
        
        # Load exchange credentials
        merged = cls._load_exchange_credentials(merged)
        
        # Handle exchanges structure
        if "exchanges" in merged and "enabled" in merged["exchanges"]:
            # Extract enabled list
            enabled_list = merged["exchanges"].pop("enabled")
            merged["enabled_exchanges"] = enabled_list
        
        # Handle symbols structure
        if "symbols" in merged:
            merged["watchlist"] = merged["symbols"].get("watchlist", [])
            merged["blacklist"] = merged["symbols"].get("blacklist", [])
            del merged["symbols"]
        
        # Load exchange credentials from env
        merged = cls._load_exchange_credentials(merged)
        
        return cls(**merged)
    
    @staticmethod
    def _load_env_overrides() -> Dict[str, Any]:
        """Load overrides from environment variables"""
        overrides = {}
        
        # System
        if env := os.getenv("ENVIRONMENT"):
            overrides["environment"] = env
        if paper := os.getenv("PAPER_TRADING"):
            overrides["paper_trading"] = paper.lower() == "true"
        if dry := os.getenv("DRY_RUN"):
            overrides["dry_run"] = dry.lower() == "true"
        
        # Database
        if os.getenv("POSTGRES_HOST"):
            overrides.setdefault("database", {})
            overrides["database"]["host"] = os.getenv("POSTGRES_HOST")
            overrides["database"]["port"] = int(os.getenv("POSTGRES_PORT", 5432))
            overrides["database"]["database"] = os.getenv("POSTGRES_DB", "trinity_arbitrage")
            overrides["database"]["user"] = os.getenv("POSTGRES_USER", "trinity")
            overrides["database"]["password"] = os.getenv("POSTGRES_PASSWORD", "")
        
        # Redis
        if os.getenv("REDIS_HOST"):
            overrides.setdefault("redis", {})
            overrides["redis"]["host"] = os.getenv("REDIS_HOST")
            overrides["redis"]["port"] = int(os.getenv("REDIS_PORT", 6379))
            overrides["redis"]["password"] = os.getenv("REDIS_PASSWORD")
            overrides["redis"]["db"] = int(os.getenv("REDIS_DB", 0))
        
        # Monitoring
        if os.getenv("TELEGRAM_BOT_TOKEN"):
            overrides.setdefault("monitoring", {})
            overrides["monitoring"]["telegram_bot_token"] = os.getenv("TELEGRAM_BOT_TOKEN")
            overrides["monitoring"]["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID")
        
        if sentry := os.getenv("SENTRY_DSN"):
            overrides.setdefault("monitoring", {})
            overrides["monitoring"]["sentry_dsn"] = sentry
        
        # Logging
        if log_level := os.getenv("LOG_LEVEL"):
            overrides.setdefault("logging", {})
            overrides["logging"]["level"] = log_level
        
        return overrides
    
    @staticmethod
    def _load_exchange_credentials(config: Dict[str, Any]) -> Dict[str, Any]:
        """Load exchange API credentials from environment"""
        exchanges = config.get("exchanges", {})
        
        # Binance
        if "binance" in exchanges:
            exchanges["binance"]["api_key"] = os.getenv("BINANCE_API_KEY")
            exchanges["binance"]["api_secret"] = os.getenv("BINANCE_API_SECRET")
            exchanges["binance"]["testnet"] = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        
        # Bybit
        if "bybit" in exchanges:
            exchanges["bybit"]["api_key"] = os.getenv("BYBIT_API_KEY")
            exchanges["bybit"]["api_secret"] = os.getenv("BYBIT_API_SECRET")
            exchanges["bybit"]["testnet"] = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        
        # OKX
        if "okx" in exchanges:
            exchanges["okx"]["api_key"] = os.getenv("OKX_API_KEY")
            exchanges["okx"]["api_secret"] = os.getenv("OKX_API_SECRET")
            exchanges["okx"]["api_passphrase"] = os.getenv("OKX_PASSPHRASE")
            exchanges["okx"]["testnet"] = os.getenv("OKX_TESTNET", "false").lower() == "true"
        
        # GateIO
        if "gateio" in exchanges:
            exchanges["gateio"]["api_key"] = os.getenv("GATEIO_API_KEY")
            exchanges["gateio"]["api_secret"] = os.getenv("GATEIO_API_SECRET")
            exchanges["gateio"]["testnet"] = os.getenv("GATEIO_TESTNET", "false").lower() == "true"
        
        config["exchanges"] = exchanges
        return config
    
    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """Deep merge two dictionaries"""
        result = base.copy()
        
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = Config._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def validate_safety(self):
        """Validate safety configuration before live trading"""
        errors = []
        
        if not self.paper_trading and not self.dry_run:
            # Production mode - extra validation
            
            # Check API keys
            for exchange_id in self.enabled_exchanges:
                exchange = self.exchanges.get(exchange_id)
                if not exchange:
                    errors.append(f"Exchange {exchange_id} not configured")
                    continue
                
                if not exchange.api_key or not exchange.api_secret:
                    errors.append(f"Missing API credentials for {exchange_id}")
            
            # Check monitoring
            if self.monitoring.enable_telegram:
                if not self.monitoring.telegram_bot_token or not self.monitoring.telegram_chat_id:
                    errors.append("Telegram alerts enabled but credentials missing")
            
            # Check database
            if not self.database.password:
                errors.append("Database password not set")
            
            # Validate risk limits
            if self.risk_limits.max_margin_usage > Decimal('0.5'):
                errors.append(f"Margin usage too high: {self.risk_limits.max_margin_usage}")
            
            if self.risk_limits.max_position_size_usd > Decimal('100000'):
                errors.append(f"Position size too large: {self.risk_limits.max_position_size_usd}")
        
        if errors:
            raise ValueError(f"Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors))
    
    def is_exchange_enabled(self, exchange_id: str) -> bool:
        """Check if exchange is enabled"""
        return exchange_id in self.enabled_exchanges
    
    def get_exchange_config(self, exchange_id: str) -> Optional[ExchangeConfig]:
        """Get configuration for specific exchange"""
        return self.exchanges.get(exchange_id)
    
    def is_symbol_allowed(self, symbol: str) -> bool:
        """Check if symbol is allowed for trading"""
        if symbol in self.blacklist:
            return False
        if self.watchlist and symbol not in self.watchlist:
            return False
        return True


# Singleton instance
_config_instance: Optional[Config] = None


def get_config(reload: bool = False) -> Config:
    """
    Get global configuration instance
    Lazy loads on first access
    """
    global _config_instance
    
    if _config_instance is None or reload:
        _config_instance = Config.load_from_yaml()
    
    return _config_instance


def init_config(config_path: str = "config.yaml"):
    """Initialize configuration from specific file"""
    global _config_instance
    _config_instance = Config.load_from_yaml(config_path)
    return _config_instance
