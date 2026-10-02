"""HTTP interface for the plugin: POST /ask, GET /health."""
from __future__ import annotations

import hmac
import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .core import answer
from .models import Question

log = logging.getLogger("minecorp.agent")
settings = get_settings()
app = FastAPI(title="MineCorp agent", docs_url=None, redoc_url=None)


class AskRequest(BaseModel):
    """Body sent by the plugin."""
    playerUuid: str = Field(min_length=1, max_length=64)
    playerName: str = Field(min_length=1, max_length=64)
    sessionId: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=500)


class AskResponse(BaseModel):
    """Reply returned to the plugin."""
    requestId: str
    answer: str


@app.get("/health")
def health() -> dict:
    """Liveness check; needs no secret and reveals nothing."""
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, x_minecorp_key: str = Header(default="")) -> AskResponse:
    """Answer a player's question. Requires the shared secret in X-MineCorp-Key."""
    if not hmac.compare_digest(x_minecorp_key.encode(), settings.shared_secret.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")
    request_id = uuid.uuid4().hex[:12]
    log.info("ask %s player=%s question=%r", request_id, body.playerName, body.question)
    text = answer(Question(body.playerUuid, body.playerName, body.sessionId, body.question), request_id)
    return AskResponse(requestId=request_id, answer=text)
