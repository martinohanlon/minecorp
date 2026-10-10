"""HTTP interface for the plugin: POST /ask, GET /health."""
from __future__ import annotations

import hmac
import logging
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .core import answer_with_sources
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


class Source(BaseModel):
    """Where a fact in the answer came from: game data, a wiki section (with its link) or a plan."""
    kind: str
    label: str
    url: str | None = None


class AskResponse(BaseModel):
    """Reply returned to the plugin."""
    requestId: str
    answer: str
    sources: list[Source] = []


@app.get("/health")
def health() -> dict:
    """Liveness check; needs no secret and reveals nothing."""
    return {"status": "ok"}


def require_key(x_minecorp_key: str = Header(default="")) -> None:
    """Reject the request unless it carries the shared secret. Runs before the body is validated, so a caller
    without the secret learns nothing about the expected fields."""
    if not hmac.compare_digest(x_minecorp_key.encode(), settings.shared_secret.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/ask", response_model=AskResponse, response_model_exclude_none=True, dependencies=[Depends(require_key)])
def ask(body: AskRequest) -> AskResponse:
    """Answer a player's question. Requires the shared secret in X-MineCorp-Key."""
    request_id = uuid.uuid4().hex[:12]
    log.info("ask %s player=%s question=%r", request_id, body.playerName, body.question)
    text, sources = answer_with_sources(Question(body.playerUuid, body.playerName, body.sessionId, body.question), request_id)
    return AskResponse(requestId=request_id, answer=text, sources=[Source(**s) for s in sources])
