"""The agent core function, independent of how it is called (HTTP now, MCP possible later)."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import replace

from . import conversation, memory as agent_memory
from .agent import Agent
from .book import build_book
from .config import get_settings, load_dotenv
from .graph import Graph
from .history import Exchange, History
from .models import Question
from .sessions import Sessions

log = logging.getLogger("minecorp.agent")

_agent: Agent | None = None
_lock = threading.Lock()
history = History()                       # what each player asked recently, for the explain book (the in-process cache)
sessions = Sessions()                     # each player's current conversation (the id memory files their questions under)
_memory: agent_memory.AgentMemory | None = None
_memory_ready = False                     # decided once: either `_memory` is set, or memory is off or could not be built
_conversation: conversation.ConversationOptions | None = None     # decided once, like the memory


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


def get_memory() -> agent_memory.AgentMemory | None:
    """The neo4j-agent-memory layer when AGENT_MEMORY=neo4j, else None. A wrong setting or a missing package is logged once and means
    None: the agent keeps answering with the in-process history alone."""
    global _memory, _memory_ready
    with _lock:
        if not _memory_ready:
            _memory_ready = True
            load_dotenv()
            try:
                options = agent_memory.options_from_env(os.environ)
                if options.enabled:
                    agent_memory.check_package()
                    _memory = agent_memory.AgentMemory(options)
                    log.info("agent memory: neo4j-agent-memory, database %s (embeddings %s, long-term extraction %s, traces %s)",
                             options.database, "on" if options.embed else "off", "on" if options.extract else "off", "on" if options.traces else "off")
                else:
                    log.info("agent memory: local (history kept in this process only)")
            except agent_memory.MemoryConfigError as exc:
                log.error("agent memory is OFF because of a setting: %s", exc)
        return _memory


def close_memory() -> None:
    """Let agent memory finish its background writes and close. Call it before the process exits (the service does, when it stops): it cannot
    be left to atexit, because by then Python has shut down the thread pool that asyncio needs to reach the database."""
    global _memory, _memory_ready
    with _lock:
        memory, _memory, _memory_ready = _memory, None, False
    if memory is not None:
        memory.close()


def get_conversation_options() -> conversation.ConversationOptions:
    """How much of the conversation is sent with a question (AGENT_CONVERSATION_TURNS, AGENT_CONVERSATION_IDLE_MINUTES). A wrong value is
    logged once and the defaults are used."""
    global _conversation
    with _lock:
        if _conversation is None:
            load_dotenv()
            try:
                _conversation = conversation.options_from_env(os.environ)
            except conversation.ConversationConfigError as exc:
                log.error("conversation context uses its defaults because of a setting: %s", exc)
                _conversation = conversation.ConversationOptions()
            log.info("conversation context: the last %d exchange(s) of a conversation, which ends after %g minute(s) quiet",
                     _conversation.turns, _conversation.idle_minutes)
        return _conversation


def _session(player_uuid: str) -> str:
    """The id of the player's current conversation. After a restart (none known) it is their newest conversation in agent memory, so a
    player in the middle of a chat carries on; otherwise a new one. A new conversation only shows in Neo4j with its first question."""
    known = sessions.current(player_uuid)
    if known is not None:
        return known
    memory = get_memory()
    return sessions.adopt(player_uuid, memory.latest_session(player_uuid) if memory is not None else None)


def _recent(player_uuid: str) -> list[Exchange]:
    """The player's last exchanges: from the in-process history, or when that has nothing for them (the service was restarted, or they were
    quiet long enough to be forgotten) from agent memory, which also refills the history."""
    exchanges = history.recent(player_uuid)
    if not exchanges:
        memory = get_memory()
        if memory is not None:
            exchanges = memory.recent(_session(player_uuid))
            for e in exchanges:
                history.add(player_uuid, e)
    return exchanges


def clear(player_uuid: str) -> None:
    """The player starts a new chat (they joined, or used /agent-clear): a new conversation, so what they asked before is no longer sent with
    their questions or shown in their book. It stays in agent memory, as a conversation of its own."""
    history.forget(player_uuid)
    sessions.start(player_uuid)


def _context_for(q: Question) -> list[Exchange]:
    """The earlier part of this player's conversation, to send with the question. Never raises: no context is better than no answer."""
    try:
        options = get_conversation_options()
        if options.turns <= 0:
            return []
        return conversation.recent_context(_recent(q.player_uuid), now=time.time(), options=options)
    except Exception as exc:
        log.warning("could not get the conversation so far: %s: %s", type(exc).__name__, exc)
        return []


def answer_with_sources(q: Question, request_id: str) -> tuple[str, list[dict]]:
    """Return the answer as chat-ready text, and the sources the player may ask to see. A good answer is remembered for the player."""
    q = replace(q, session_id=_session(q.player_uuid))      # the service owns the conversation, not the client
    context = _context_for(q)                 # before this question is added to the history
    try:
        result = get_agent().run(q, request_id, context=context)
    except Exception as exc:  # start-up problems: missing key, graph down
        return f"The MineCorp agent is not available right now ({type(exc).__name__}). Please tell an admin.", []
    exchange = None
    if result.status == "ok":                 # a failure or a run that hit its turn limit is not something to explain
        lines = tuple(re.split(r"\r\n|\r|\n", result.answer.rstrip()))
        exchange = Exchange(q.text, lines, tuple(result.sources), at=time.time())
        history.add(q.player_uuid, exchange)
    memory = get_memory()
    if memory is not None:                    # the run is kept either way: a failed one is a failed trace
        try:
            memory.record(q, exchange, result)
        except Exception as exc:              # memory must never cost the player their answer
            log.warning("agent memory could not take the run: %s: %s", type(exc).__name__, exc)
    return result.answer, result.sources


def explain(player_uuid: str) -> dict | None:
    """The explain book for a player's last answers (see book.py), or None when they have asked nothing yet.

    Built from the in-process history, refilled from agent memory when that has nothing for the player (see `_recent`).
    """
    return build_book(_recent(player_uuid))


def answer(q: Question, request_id: str) -> str:
    """Return the answer to a question as chat-ready text."""
    return answer_with_sources(q, request_id)[0]
