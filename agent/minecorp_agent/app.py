"""HTTP interface for the plugin: POST /ask, GET /health."""
from __future__ import annotations

import hmac
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings
from .book import AUTHOR, TITLE
from .core import answer_with_sources, close_memory, explain as explain_book, get_memory
from .models import Question

log = logging.getLogger("minecorp.agent")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_memory()        # a stopping service lets agent memory finish its writes


app = FastAPI(title="MineCorp agent", docs_url=None, redoc_url=None, lifespan=lifespan)
get_memory()          # decide the memory mode now, so the start-up log says which it is (no connection is made yet)


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
    graph: list[str] | None = None            # the path through the graph the tool walked, as pattern lines


class AskResponse(BaseModel):
    """Reply returned to the plugin."""
    requestId: str
    answer: str
    sources: list[Source] = []


class ExplainRequest(BaseModel):
    """Body sent by the plugin when a player asks how their answers were made."""
    playerUuid: str = Field(min_length=1, max_length=64)


class BookLine(BaseModel):
    """One line of a page: a link when it has a url, a jump to another page when it has a goto (counted from 1)."""
    text: str
    url: str | None = None
    goto: int | None = None


class BookPage(BaseModel):
    lines: list[BookLine]


class Book(BaseModel):
    """The book the plugin opens for the player (see book.py); no pages means there is nothing to explain yet."""
    title: str
    author: str
    pages: list[BookPage] = []


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


@app.post("/explain", response_model=Book, response_model_exclude_none=True, dependencies=[Depends(require_key)])
def explain(body: ExplainRequest) -> Book:
    """The book of a player's last answers, their questions and sources. Requires the shared secret in X-MineCorp-Key."""
    log.info("explain player=%s", body.playerUuid)
    book = explain_book(body.playerUuid)
    return Book(**book) if book else Book(title=TITLE, author=AUTHOR, pages=[])
