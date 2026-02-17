"""
Structured logging — one logger, JSON format, no fluff.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
import platform
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
            delay=True,  # Don't open file until first write (avoids Windows lock issues)
        )
        fh.setFormatter(fmt)
        # Windows: override namer/rotator to handle locked files gracefully
        if platform.system() == "Windows":
            import time as _wtime, shutil as _shutil
            def _win_rotator(source, dest):
                """Rotate with retry for Windows file-locking."""
                for attempt in range(5):
                    try:
                        if Path(dest).exists():
                            Path(dest).unlink()
                        _shutil.move(source, dest)
                        return
                    except PermissionError:
                        _wtime.sleep(0.1 * (attempt + 1))
                # Last resort: just truncate the source
                try:
                    with open(source, 'w'):
                        pass
                except Exception:
                    pass
            fh.rotator = _win_rotator
        logger.addHandler(fh)

    return logger
