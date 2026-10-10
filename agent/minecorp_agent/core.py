"""The agent core function, independent of how it is called (HTTP now, MCP possible later)."""
from __future__ import annotations

import os
import threading

from .agent import Agent
from .config import get_settings
from .graph import Graph
from .models import Question

_agent: Agent | None = None
_lock = threading.Lock()


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
    """Return the answer as chat-ready text, and the sources the player may ask to see."""
    try:
        result = get_agent().run(q, request_id)
        return result.answer, result.sources
    except Exception as exc:  # start-up problems: missing key, graph down
        return f"The MineCorp agent is not available right now ({type(exc).__name__}). Please tell an admin.", []


def answer(q: Question, request_id: str) -> str:
    """Return the answer to a question as chat-ready text."""
    return answer_with_sources(q, request_id)[0]
