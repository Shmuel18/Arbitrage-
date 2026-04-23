"""Backtest reports API — list and serve the HTML/JSON files emitted by
``python -m src.backtest.runner --save``.

The directory is repo-relative (``data/backtest-reports/``), root-owned and
bind-mounted into the bot container via docker-compose. All endpoints are
guarded by ``require_read_token`` so the dashboard's existing auth covers
them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from ..auth import require_read_token

logger = logging.getLogger("trinity.api.backtest")

router = APIRouter(redirect_slashes=False)

# Repo-relative: api/routes/backtest.py → repo root is three parents up.
REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "backtest-reports"

# Reports are auto-named with ASCII alnum + _ + - + . only.
# A user trying to hit ``../../etc/passwd`` will fail this regex and 404.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _read_sidecar(html_path: Path) -> Optional[dict]:
    """Load the ``.json`` sibling of an HTML report if it exists."""
    json_path = html_path.with_suffix(".json")
    if not json_path.exists():
        return None
    try:
        with json_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read sidecar %s: %s", json_path, exc)
        return None


@router.get("/reports", dependencies=[Depends(require_read_token)])
def list_reports() -> dict:
    """Return metadata for every HTML report in the reports directory."""
    if not REPORTS_DIR.exists():
        return {"reports": []}

    items: list[dict] = []
    for html in sorted(REPORTS_DIR.glob("*.html")):
        stat = html.stat()
        sidecar = _read_sidecar(html) or {}
        cfg = sidecar.get("config", {}) or {}
        metrics = sidecar.get("metrics", {}) or {}
        items.append(
            {
                "name": html.name,
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
                "size_bytes": stat.st_size,
                "has_json": html.with_suffix(".json").exists(),
                "symbol": cfg.get("symbol"),
                "exchange_a": cfg.get("exchange_a"),
                "exchange_b": cfg.get("exchange_b"),
                "notional_usd": cfg.get("notional_usd"),
                "min_funding_spread_pct": cfg.get("min_funding_spread_pct"),
                "trade_count": metrics.get("trade_count"),
                "win_rate": metrics.get("win_rate"),
                "total_pnl_usd": metrics.get("total_pnl_usd"),
                "sharpe_ratio_annualized": metrics.get("sharpe_ratio_annualized"),
            }
        )

    # Newest first — matches what the user expects when browsing.
    items.sort(key=lambda r: r["created_at"], reverse=True)
    return {"reports": items}


def _resolve_report(name: str) -> Path:
    if not _SAFE_NAME.match(name):
        raise HTTPException(status_code=400, detail="invalid report name")
    path = REPORTS_DIR / name
    try:
        # resolve() + relative_to() jails the final path under REPORTS_DIR.
        path.resolve().relative_to(REPORTS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid report path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report not found")
    return path


@router.get("/reports/{name}", dependencies=[Depends(require_read_token)])
def get_report(name: str):
    """Serve a backtest report file. HTML renders inline; JSON returns JSON."""
    path = _resolve_report(name)
    suffix = path.suffix.lower()
    if suffix == ".html":
        return HTMLResponse(content=path.read_text(encoding="utf-8"))
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as fh:
            return JSONResponse(content=json.load(fh))
    raise HTTPException(status_code=400, detail="unsupported report type")
