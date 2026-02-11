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


def get_logger(
    name: str,
    level: str = "INFO",
    log_dir: str = "logs",
    console: bool = True,
    file_output: bool = True,
    max_mb: int = 100,
    backup_count: int = 10,
) -> logging.Logger:
    """Return a configured logger, creating it only once per name."""

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger                    # already set up

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    fmt = JsonFormatter()

    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

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
        logger.addHandler(fh)

    return logger
