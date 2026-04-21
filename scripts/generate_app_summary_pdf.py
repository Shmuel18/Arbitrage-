from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "trinity_app_summary.pdf"


def bullet(text: str) -> Paragraph:
    return Paragraph(f'&bull; {text}', styles["bullet"])


styles = getSampleStyleSheet()
styles.add(
    ParagraphStyle(
        name="TitleTight",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=21,
        textColor=colors.HexColor("#12263A"),
        spaceAfter=4,
    )
)
styles.add(
    ParagraphStyle(
        name="Meta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=10,
        textColor=colors.HexColor("#5C6773"),
        spaceAfter=6,
    )
)
styles.add(
    ParagraphStyle(
        name="Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=12,
        textColor=colors.HexColor("#0B5C8E"),
        spaceBefore=4,
        spaceAfter=2,
    )
)
styles.add(
    ParagraphStyle(
        name="BodyTight",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.7,
        leading=10.2,
        textColor=colors.black,
        spaceAfter=3,
    )
)
styles.add(
    ParagraphStyle(
        name="bullet",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.4,
        leading=9.7,
        leftIndent=9,
        firstLineIndent=-5,
        bulletIndent=0,
        spaceAfter=1.3,
    )
)


def build_story() -> list:
    story: list = []
    story.append(Paragraph("Trinity App Summary", styles["TitleTight"]))
    story.append(
        Paragraph(
            "Repo-based one-page summary generated from code, scripts, and bundled docs.",
            styles["Meta"],
        )
    )

    story.append(Paragraph("What It Is", styles["Section"]))
    story.append(
        Paragraph(
            "Trinity is a crypto funding arbitrage application centered on a Python trading bot with a FastAPI backend and a React frontend. Repo evidence describes it as a delta-neutral arbitrage engine, and the current codebase also exposes monitoring, analytics, alerts, and operator controls through HTTP and WebSocket interfaces.",
            styles["BodyTight"],
        )
    )

    story.append(Paragraph("Who It’s For", styles["Section"]))
    story.append(
        Paragraph(
            "Primary persona: an operator or trader running and supervising an automated arbitrage bot locally, with access to exchange credentials, Redis, and the web dashboard.",
            styles["BodyTight"],
        )
    )

    story.append(Paragraph("What It Does", styles["Section"]))
    features = [
        "Scans connected exchanges for opportunities and passes candidates to the execution controller (`src/discovery/scanner.py`, `main.py`).",
        "Executes and monitors positions through an `ExecutionController` plus sizing, entry, exit, and close mixins (`src/execution/`).",
        "Runs an independent `RiskGuard` alongside the controller to supervise live activity (`src/risk/guard.py`, `main.py`).",
        "Publishes status, balances, positions, opportunities, logs, trades, PnL, and alerts through Redis-backed API endpoints and WS updates (`api/main.py`, `api/broadcast_service.py`).",
        "Provides dashboard views for status, positions, trades, analytics, logs, alerts, and exchange balances in the React frontend (`frontend/src/components/`).",
        "Accepts authenticated control actions such as start, stop, pause, resume, config updates, and emergency stop (`api/routes/controls.py`, `frontend/src/services/api.ts`).",
    ]
    story.extend(bullet(item) for item in features)

    story.append(Paragraph("How It Works", styles["Section"]))
    arch = [
        "Bot runtime: `main.py` initializes config, connects Redis, registers and verifies exchanges, warms market/funding/trading settings, then starts `Scanner`, `ExecutionController`, `RiskGuard`, and `StatusPublisher`.",
        "State layer: `RedisClient` stores runtime keys such as `trinity:status`, `trinity:positions`, `trinity:balances`, `trinity:opportunities`, logs, alerts, and trade history (`src/storage/redis_client.py`, `api/main.py`, `api/broadcast_service.py`).",
        "API layer: FastAPI serves REST routes under `/api/*`, reads shared Redis state, and can be embedded inside the bot process on port 8000 (`api/main.py`, `run_api.ps1`).",
        "Realtime layer: `BroadcastService` reads Redis every 2 seconds and pushes a `full_update` payload to connected WebSocket clients on `/ws` (`api/broadcast_service.py`).",
        "Frontend: Vite/React uses relative `/api` requests via Axios and subscribes to `/ws`; components render dashboard, tables, analytics, risk, and control panels (`frontend/src/services/api.ts`, `frontend/src/services/websocket.ts`).",
        "Not found in repo: a relational database service actively used by the current runtime path. Docker Compose includes Redis only, and the current code path inspected centers on Redis.",
    ]
    story.extend(bullet(item) for item in arch)

    story.append(Paragraph("How To Run", styles["Section"]))
    steps = [
        "Install Python dependencies: `pip install -r api/requirements.txt` and project requirements as needed; install frontend packages with `./setup_frontend.ps1` or `cd frontend && npm install` (`setup_api.ps1`, `setup_frontend.ps1`).",
        "Set required tokens/env vars. `ADMIN_TOKEN` is required for WebSocket access; frontend uses `VITE_WS_TOKEN` and can use scoped tokens for API actions (`QUICK_START_WEB.md`, `api/main.py`, `frontend/README.md`).",
        "Start Redis. Repo docs and `docker-compose.yml` show Redis on port 6379.",
        "Start the bot plus embedded API: `./run.ps1` (or `./run_api.ps1`, which currently delegates to `main.py`).",
        "Start the frontend in a second terminal: `./run_frontend.ps1`, then open `http://localhost:3000`.",
        "Not found in repo: a single canonical production deployment procedure beyond the local scripts and quick-start docs above.",
    ]
    story.extend(bullet(item) for item in steps)

    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Evidence base: `README.md`, `main.py`, `api/main.py`, `api/broadcast_service.py`, `api/routes/controls.py`, `frontend/src/services/api.ts`, `frontend/src/services/websocket.ts`, startup/setup scripts, and `docker-compose.yml`.",
            styles["Meta"],
        )
    )
    return story


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=11 * mm,
        bottomMargin=11 * mm,
        title="Trinity App Summary",
        author="OpenAI Codex",
    )
    doc.build(build_story())
    print(OUTPUT)


if __name__ == "__main__":
    main()
