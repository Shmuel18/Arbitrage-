"""AI chat API — natural-language Q&A exposed to the dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

from ..auth import require_read_token
from ..deps import require_redis_client

logger = logging.getLogger("trinity.api.ai")

router = APIRouter(redirect_slashes=False)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    lang: Optional[str] = Field(None, description="'he', 'en', or None for auto")


class ChatResponse(BaseModel):
    answer: str
    provider: str


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_read_token)])
async def chat(
    body: ChatRequest,
    redis_client: "RedisClient" = Depends(require_redis_client),
):
    """Ask the AI assistant a natural-language question about the bot.

    Uses the same tools+logic as the Telegram bot's assistant. The answer
    is returned as pre-formatted Telegram-HTML (safe subset: <b> <i> <code>).
    """
    try:
        from src.notifications.ai_assistant import answer_question, _resolve_provider
    except ImportError as exc:
        logger.exception("AI assistant import failed")
        raise HTTPException(status_code=500, detail=f"AI module missing: {exc}")

    provider = _resolve_provider()
    if provider == "none":
        raise HTTPException(
            status_code=503,
            detail="AI not configured. Set GEMINI_API_KEY (free) or ANTHROPIC_API_KEY.",
        )

    try:
        answer = await answer_question(body.question, redis_client, lang=body.lang)
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI chat failed")
        raise HTTPException(status_code=502, detail=f"AI error: {exc}")

    return ChatResponse(answer=answer, provider=provider)
