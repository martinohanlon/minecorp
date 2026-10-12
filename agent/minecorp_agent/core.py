"""The agent core function, independent of how it is called (HTTP now, MCP possible later)."""
from __future__ import annotations

import os
import re
import threading

from .agent import Agent
from .book import build_book
from .config import get_settings
from .graph import Graph
from .history import Exchange, History
from .models import Question

_agent: Agent | None = None
_lock = threading.Lock()
history = History()                       # what each player asked recently, for the explain book (in memory for now)


def get_agent() -> Agent:
    """Build the agent on first use (graph connection and model client), then reuse it."""
    global _agent
    with _lock:
        if _agent is None:
            settings = get_settings()
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY is not set")
            graph = Graph(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password,
                          settings.neo4j_database)
            graph.verify()
            _agent = Agent(settings, graph)
        return _agent


def answer_with_sources(q: Question, request_id: str) -> tuple[str, list[dict]]:
    """Return the answer as chat-ready text, and the sources the player may ask to see. A good answer is remembered for the player."""
    try:
        result = get_agent().run(q, request_id)
    except Exception as exc:  # start-up problems: missing key, graph down
        return f"The MineCorp agent is not available right now ({type(exc).__name__}). Please tell an admin.", []
    if result.status == "ok":                 # a failure or a run that hit its turn limit is not something to explain
        lines = tuple(re.split(r"\r\n|\r|\n", result.answer.rstrip()))
        history.add(q.player_uuid, Exchange(q.text, lines, tuple(result.sources)))
    return result.answer, result.sources


def explain(player_uuid: str) -> dict | None:
    """The explain book for a player's last answers (see book.py), or None when they have asked nothing yet."""
    return build_book(history.recent(player_uuid))


def answer(q: Question, request_id: str) -> str:
    """Return the answer to a question as chat-ready text."""
    return answer_with_sources(q, request_id)[0]
