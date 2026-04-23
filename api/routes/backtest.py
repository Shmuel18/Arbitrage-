"""Backtest API — reports listing + job management.

Reports are HTML/JSON pairs emitted by ``python -m src.backtest.runner --save``
under ``data/backtest-reports/``. Jobs are subprocess supervisors that spawn
either the fetch script or the runner and record status in Redis so the UI
can poll.

All endpoints are guarded by ``require_read_token``. That token grants enough
privilege to DoS the host via repeated job triggers, so tighten to a stricter
token (add ``require_command_token``) before exposing this service externally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

from ..auth import require_read_token
from ..deps import require_redis_client

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


# ── Jobs (fetch + run) ────────────────────────────────────────────────────

# Only these exchanges are valid inputs. Mirrors the live bot's exchange
# registry; extend when a new adapter is onboarded.
_SUPPORTED_EXCHANGES = {"binance", "bybit", "kucoin", "gateio", "bitget"}

# ccxt perp symbol: ``BASE/QUOTE:SETTLE`` (e.g. ``BTC/USDT:USDT``). Whitelist
# alnum + ``/:_-`` only — never interpolate user input into shell commands
# (we use argv form so shell metacharacters aren't a risk either way, but
# defensive validation keeps the API contract predictable).
_SYMBOL_RE = re.compile(r"^[A-Z0-9_-]+/[A-Z0-9_-]+:[A-Z0-9_-]+$")

_JOB_TTL_SECONDS = 86_400        # 24 h history is plenty
_MAX_RECENT_JOBS = 50
_JOB_TIMEOUT_SECONDS = 15 * 60   # a full-year fetch on 5 exchanges sits well below this

# Only one job at a time — each subprocess can peg the network or a CPU for
# minutes, and we don't want to stack them on a 2-vCPU VPS.
_job_lock = asyncio.Lock()


def _job_key(job_id: str) -> str:
    return f"bt:job:{job_id}"


_RECENT_KEY = "bt:jobs:recent"


class FetchRequest(BaseModel):
    exchange: str
    symbol: str
    days: int = Field(..., ge=1, le=365)
    kind: Literal["funding", "ohlcv-1d", "both"] = "both"

    @field_validator("exchange")
    @classmethod
    def _exchange_supported(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _SUPPORTED_EXCHANGES:
            raise ValueError(f"exchange must be one of {sorted(_SUPPORTED_EXCHANGES)}")
        return v

    @field_validator("symbol")
    @classmethod
    def _symbol_shape(cls, v: str) -> str:
        v = v.strip().upper()
        if not _SYMBOL_RE.match(v):
            raise ValueError("symbol must look like 'BTC/USDT:USDT'")
        return v


class RunRequest(BaseModel):
    symbol: str
    exchange_a: str
    exchange_b: str
    notional_usd: float = Field(100.0, gt=0, le=1_000_000)
    min_funding_spread: float = Field(0.003, ge=0, le=1)
    max_hold_hours: int = Field(72, ge=1, le=24 * 30)
    max_collections: int = Field(6, ge=1, le=200)
    slippage_bps: float = Field(5.0, ge=0, le=500)
    funding_interval_hours: int = Field(8, ge=1, le=48)

    @field_validator("symbol")
    @classmethod
    def _symbol_shape(cls, v: str) -> str:
        v = v.strip().upper()
        if not _SYMBOL_RE.match(v):
            raise ValueError("symbol must look like 'BTC/USDT:USDT'")
        return v

    @field_validator("exchange_a", "exchange_b")
    @classmethod
    def _exchange_supported(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _SUPPORTED_EXCHANGES:
            raise ValueError(f"exchange must be one of {sorted(_SUPPORTED_EXCHANGES)}")
        return v


async def _write_job(redis: "RedisClient", job: dict[str, Any]) -> None:
    job_id = job["id"]
    # Store as a single JSON blob — tiny and easy to inspect via redis-cli.
    await redis.set(
        _job_key(job_id),
        json.dumps(job, default=str),
        ex=_JOB_TTL_SECONDS,
    )


async def _load_job(redis: "RedisClient", job_id: str) -> Optional[dict[str, Any]]:
    raw = await redis.get(_job_key(job_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _detect_report_from_stdout(stdout: str) -> Optional[str]:
    """The runner prints ``HTML report → <path>`` — pull the basename out."""
    for line in stdout.splitlines():
        m = re.search(r"HTML report\s*→\s*(\S+\.html)", line)
        if m:
            return Path(m.group(1)).name
    return None


async def _spawn_subprocess(
    job_id: str, cmd: list[str], redis: "RedisClient",
) -> None:
    """Run ``cmd`` under a global lock, streaming terminal state to Redis.

    This is fired-and-forgotten by the POST handlers via ``create_task`` —
    the HTTP response returns as soon as the job record is written.
    """
    async with _job_lock:
        job = await _load_job(redis, job_id)
        if job is None:
            return  # cancelled or evicted before we could run

        job["status"] = "running"
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        await _write_job(redis, job)

        stdout_b = b""
        stderr_b = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd="/app",
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=_JOB_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                proc.kill()
                stdout_b, stderr_b = await proc.communicate()
                job["status"] = "failed"
                job["exit_code"] = -1
                job["error"] = f"timeout after {_JOB_TIMEOUT_SECONDS}s"
            else:
                job["exit_code"] = proc.returncode
                job["status"] = "succeeded" if proc.returncode == 0 else "failed"
        except Exception as exc:  # noqa: BLE001 — record whatever killed us
            logger.exception("backtest job %s crashed", job_id)
            job["status"] = "failed"
            job["error"] = str(exc)

        job["stdout_tail"] = stdout_b.decode(errors="replace")[-4000:]
        job["stderr_tail"] = stderr_b.decode(errors="replace")[-4000:]
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        # For run jobs, surface the report filename so the UI can deep-link.
        if job.get("kind") == "run" and job["status"] == "succeeded":
            job["report_name"] = _detect_report_from_stdout(job["stdout_tail"])
        await _write_job(redis, job)


async def _register_new_job(
    redis: "RedisClient",
    kind: Literal["fetch", "run"],
    params: dict[str, Any],
    cmd: list[str],
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "params": params,
        "cmd": cmd,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "report_name": None,
    }
    await _write_job(redis, job)
    await redis.lpush(_RECENT_KEY, job_id)
    await redis.ltrim(_RECENT_KEY, 0, _MAX_RECENT_JOBS - 1)
    return job


@router.post("/fetch", dependencies=[Depends(require_read_token)])
async def start_fetch(
    body: FetchRequest,
    redis: "RedisClient" = Depends(require_redis_client),
) -> dict[str, str]:
    cmd = [
        sys.executable,
        "scripts/fetch_historical_data.py",
        "--exchange", body.exchange,
        "--symbol", body.symbol,
        "--days", str(body.days),
        "--kind", body.kind,
    ]
    job = await _register_new_job(redis, "fetch", body.model_dump(), cmd)
    asyncio.create_task(
        _spawn_subprocess(job["id"], cmd, redis), name=f"bt-fetch-{job['id']}",
    )
    return {"job_id": job["id"]}


@router.post("/run", dependencies=[Depends(require_read_token)])
async def start_run(
    body: RunRequest,
    redis: "RedisClient" = Depends(require_redis_client),
) -> dict[str, str]:
    cmd = [
        sys.executable, "-m", "src.backtest.runner",
        "--symbol", body.symbol,
        "--pair", f"{body.exchange_a},{body.exchange_b}",
        "--notional", str(body.notional_usd),
        "--min-spread", str(body.min_funding_spread),
        "--max-hold-hours", str(body.max_hold_hours),
        "--max-collections", str(body.max_collections),
        "--slippage-bps", str(body.slippage_bps),
        "--funding-interval", str(body.funding_interval_hours),
        "--save",
    ]
    job = await _register_new_job(redis, "run", body.model_dump(), cmd)
    asyncio.create_task(
        _spawn_subprocess(job["id"], cmd, redis), name=f"bt-run-{job['id']}",
    )
    return {"job_id": job["id"]}


@router.get("/jobs", dependencies=[Depends(require_read_token)])
async def list_jobs(
    redis: "RedisClient" = Depends(require_redis_client),
) -> dict:
    ids = await redis.lrange(_RECENT_KEY, 0, _MAX_RECENT_JOBS - 1)
    jobs: list[dict[str, Any]] = []
    for jid in ids:
        # ``lrange`` on redis-py returns bytes by default; normalise to str.
        key = jid.decode() if isinstance(jid, (bytes, bytearray)) else str(jid)
        job = await _load_job(redis, key)
        if job:
            # Strip heavy fields from the list view; fetched per-job on click.
            jobs.append({k: v for k, v in job.items() if k not in {"stdout_tail", "stderr_tail", "cmd"}})
    return {"jobs": jobs}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_read_token)])
async def get_job(
    job_id: str,
    redis: "RedisClient" = Depends(require_redis_client),
) -> dict:
    if not re.match(r"^[a-f0-9]{12}$", job_id):
        raise HTTPException(status_code=400, detail="invalid job id")
    job = await _load_job(redis, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
