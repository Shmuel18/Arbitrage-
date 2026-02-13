"""
Structured logging — one logger, JSON format, no fluff.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        doc: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Merge extra fields added via logger.info("msg", extra={...})
        for key in ("exchange", "symbol", "trade_id", "action", "data"):
            val = getattr(record, key, None)
            if val is not None:
                doc[key] = val

        if record.exc_info and record.exc_info[1]:
            doc["exception"] = self.formatException(record.exc_info)

        return json.dumps(doc, default=str, ensure_ascii=False)


class StructlogAdapter(logging.LoggerAdapter):
    """Accept structlog-style keyword args and funnel them into extra."""

    def process(self, msg, kwargs):
        # Move any non-standard kwargs into extra dict
        extra = kwargs.pop("extra", {})
        # Pull out standard logging kwargs
        exc_info = kwargs.pop("exc_info", None)
        stack_info = kwargs.pop("stack_info", False)
        stacklevel = kwargs.pop("stacklevel", 1)
        # Everything else is a structlog-style kwarg → pack into extra
        extra.update(kwargs)
        kwargs.clear()
        result_kwargs: Dict[str, Any] = {"extra": extra}
        if exc_info is not None:
            result_kwargs["exc_info"] = exc_info
        if stack_info:
            result_kwargs["stack_info"] = stack_info
        result_kwargs["stacklevel"] = stacklevel
        return msg, result_kwargs


def get_logger(
    name: str,
    level: str = "INFO",
    log_dir: str = "logs",
    console: bool = True,
    file_output: bool = True,
    max_mb: int = 100,
    backup_count: int = 10,
) -> StructlogAdapter:
    """Return a configured logger, creating it only once per name."""

    base = logging.getLogger(name)
    if base.handlers:
        return StructlogAdapter(base, {})   # already set up

    base.setLevel(getattr(logging, level.upper(), logging.INFO))
    base.propagate = False
    fmt = JsonFormatter()

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        base.addHandler(sh)

    if file_output:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_path / f"{name}.log",
            maxBytes=max_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        base.addHandler(fh)

    return StructlogAdapter(base, {})
