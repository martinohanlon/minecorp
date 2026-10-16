"""Agent memory on neo4j-agent-memory: what each player asked, kept in Neo4j, and how each answer was reached (docs/agent-memory.md).

Optional and off by default (`AGENT_MEMORY=local`). Two layers work together:

- `History` (history.py) stays the always-on, in-process cache of each player's last five exchanges. The explain book is built from it.
- `AgentMemory` (here, `AGENT_MEMORY=neo4j`) writes each exchange behind it: the question and the answer as short-term memory (a
  `Conversation` per player, the answer's sources in its metadata), and a reasoning trace of the tools called. After a restart, or when a
  player has fallen out of the cache, the book is rebuilt from it.

Memory never gets in the way of an answer. Writes go to a background thread and are not waited for; a database that is down or a bad
setting is logged, and the agent carries on with the cache alone. The library is async only, so it runs on its own event loop in that thread.
Long-term memory (entities from conversations) and embeddings are separate switches, both off by default.

By default the memory is written into the same database as the game graph, so that long-term memory can be linked to the nodes already
there. `AGENT_MEMORY_DATABASE` puts it in a database of its own instead; the price is that it can no longer link to the game graph.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timezone
from types import SimpleNamespace

from .history import MAX_EXCHANGES, Exchange
from .models import Question, Result

log = logging.getLogger("minecorp.memory")

BACKENDS = ("local", "neo4j")
RETRY_SECONDS = 60.0          # after a failed connection, do not try again for this long
READ_TIMEOUT = 8.0            # a book read waits this long for Neo4j before showing nothing
MAX_PENDING = 100             # writes waiting for Neo4j; more than this are dropped (and logged) rather than piling up
_DB_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9.-]{0,62}$")
_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


class MemoryConfigError(ValueError):
    """A memory setting is wrong; the message says which and how to fix it."""


@dataclass(frozen=True)
class MemoryOptions:
    """The memory settings. `backend` 'local' means no neo4j-agent-memory at all."""
    backend: str = "local"
    database: str = "neo4j"         # the same database as the game graph unless AGENT_MEMORY_DATABASE says otherwise
    embed: bool = False             # embed each message and trace (OpenAI) so memory can be searched by meaning
    extract: bool = False           # long-term memory: an LLM pulls entities and preferences out of what players say
    traces: bool = True             # record a reasoning trace (the tools called) for every run
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = ""
    openai_api_key: str = ""
    llm_model: str = "gpt-5.4-mini"

    @property
    def enabled(self) -> bool:
        return self.backend == "neo4j"


def _flag(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise MemoryConfigError(f"{key} must be true or false, not {env[key]!r}")


def options_from_env(env: Mapping[str, str]) -> MemoryOptions:
    """Read the memory settings; a wrong value raises MemoryConfigError. Nothing is needed while the backend is local."""
    backend = env.get("AGENT_MEMORY", "").strip().lower() or "local"
    if backend not in BACKENDS:
        raise MemoryConfigError(f"AGENT_MEMORY must be one of {', '.join(BACKENDS)}, not {env.get('AGENT_MEMORY')!r}")
    database = env.get("AGENT_MEMORY_DATABASE", "").strip() or env.get("NEO4J_DATABASE", "").strip() or "neo4j"     # the game graph's own, by default
    if not _DB_NAME.match(database):
        raise MemoryConfigError(f"AGENT_MEMORY_DATABASE {database!r} is not a usable Neo4j database name (letters, digits, dots, dashes)")
    options = MemoryOptions(
        backend=backend, database=database,
        embed=_flag(env, "AGENT_MEMORY_EMBED", False), extract=_flag(env, "AGENT_MEMORY_EXTRACT", False),
        traces=_flag(env, "AGENT_MEMORY_TRACES", True),
        uri=env.get("NEO4J_URI") or "bolt://localhost:7687", user=env.get("NEO4J_USER") or "neo4j", password=env.get("NEO4J_PASSWORD", ""),
        openai_api_key=env.get("OPENAI_API_KEY", ""), llm_model=env.get("OPENAI_MODEL") or "gpt-5.4-mini")
    if options.enabled:
        if not options.password:
            raise MemoryConfigError("AGENT_MEMORY=neo4j needs NEO4J_PASSWORD")
        for key, on in (("AGENT_MEMORY_EMBED", options.embed), ("AGENT_MEMORY_EXTRACT", options.extract)):
            if on and not options.openai_api_key:
                raise MemoryConfigError(f"{key}=true needs OPENAI_API_KEY")
    return options


# --------------------------------------------------------------------------- what is stored, and how it is read back (no Neo4j here)

def messages_for(q: Question, exchange: Exchange, result: Result) -> list[tuple[str, str, dict]]:
    """The two short-term messages of an exchange as (role, content, metadata). Metadata values are plain, as Neo4j properties must be."""
    return [("user", exchange.question, {"player": q.player_name}),
            ("assistant", "\n".join(exchange.lines),
             {"sources": json.dumps(list(exchange.sources), ensure_ascii=False), "model": result.model, "ms": result.ms, "status": result.status})]


def exchanges_from(messages: list, limit: int = MAX_EXCHANGES) -> list[Exchange]:
    """The last `limit` exchanges from a conversation, oldest first: each user message with the assistant message that follows it.

    A question with no answer after it is left out (it was cut off, or the answer is still being written).
    """
    found: list[Exchange] = []
    pending = None
    for m in messages:
        role = getattr(m.role, "value", m.role)
        if role == "user":
            pending = m
        elif role == "assistant" and pending is not None:
            found.append(Exchange(pending.content, tuple(re.split(r"\r\n|\r|\n", m.content.rstrip())), _sources_of(m.metadata or {}),
                                  at=getattr(m, "at", 0.0) or 0.0))
            pending = None
    return found[-limit:]


def _sources_of(metadata: Mapping) -> tuple[dict, ...]:
    try:
        parsed = json.loads(metadata.get("sources") or "[]")
    except (TypeError, ValueError):
        return ()
    return tuple(s for s in parsed if isinstance(s, dict)) if isinstance(parsed, list) else ()


LATEST_MESSAGES = """
MATCH (:Conversation {session_id: $session_id})-[:HAS_MESSAGE]->(m:Message)
RETURN m
ORDER BY m.timestamp DESC, m.id DESC
LIMIT $limit
"""


def _epoch(stamp) -> float:
    """Seconds since the epoch for a time the library stored (a Neo4j DateTime, or a datetime); 0 when there is none. A time with no zone is UTC."""
    if stamp is None:
        return 0.0
    try:
        moment = stamp.to_native() if hasattr(stamp, "to_native") else stamp
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0.0


def _stored_metadata(raw) -> dict:
    """The library stores a message's metadata as a JSON string."""
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_result(step) -> dict:
    """What the trace keeps of a tool result: how big it was and what it gave the answer (not the whole result)."""
    return {"chars": step.result_chars, "sources": [s.get("label", "") for s in step.sources]}


# --------------------------------------------------------------------------- the database and the client

def ensure_database(options: MemoryOptions) -> None:
    """Create the memory database if it is missing (Neo4j Enterprise). Only matters for a separate one: the default `neo4j` is never created."""
    if options.database == "neo4j":
        return
    from neo4j import GraphDatabase
    with GraphDatabase.driver(options.uri, auth=(options.user, options.password)) as driver:
        try:
            driver.execute_query(f"CREATE DATABASE {options.database} IF NOT EXISTS WAIT", database_="system")   # name checked by options_from_env
        except Exception as exc:
            log.warning("could not create the memory database %s (%s). It must exist already, or use AGENT_MEMORY_DATABASE=neo4j "
                        "to share the main database (Neo4j Enterprise is needed for a second one).", options.database, exc)


def check_package() -> None:
    """Raise MemoryConfigError when the neo4j-agent-memory package is not installed."""
    try:
        import neo4j_agent_memory  # noqa: F401
    except ImportError as exc:
        raise MemoryConfigError("AGENT_MEMORY=neo4j needs the neo4j-agent-memory package: pip install -r agent/requirements.txt") from exc


def build_client(options: MemoryOptions):
    """The neo4j-agent-memory client for these options (not connected yet)."""
    check_package()
    from neo4j_agent_memory import EmbeddingConfig, ExtractionConfig, ExtractorType, MemoryClient, MemoryConfig, MemorySettings, Neo4jConfig
    from pydantic import SecretStr
    key = SecretStr(options.openai_api_key or "unused")          # only used when embeddings or extraction are on
    settings = MemorySettings(
        backend="bolt",
        neo4j=Neo4jConfig(uri=options.uri, username=options.user, password=SecretStr(options.password), database=options.database),
        embedding=EmbeddingConfig(api_key=key),
        llm=f"openai/{options.llm_model}" if options.extract else None,
        extraction=ExtractionConfig(extractor_type=ExtractorType.LLM if options.extract else ExtractorType.NONE, llm_model=options.llm_model),
        memory=MemoryConfig(message_embedding_enabled=options.embed, trace_embedding_enabled=options.embed))
    return MemoryClient(settings)


class AgentMemory:
    """Writes each exchange to neo4j-agent-memory and reads a player's last exchanges back. Safe to call from any thread; never raises."""

    def __init__(self, options: MemoryOptions, *, client_factory: Callable = build_client,
                 database_creator: Callable = ensure_database, clock: Callable[[], float] = time.monotonic) -> None:
        self.options = options
        self._factory, self._create_database, self._clock = client_factory, database_creator, clock
        self._client = None
        self._retry_at = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._guard = threading.Lock()
        self._pending = 0

    # ---- the event loop thread
    def _event_loop(self) -> asyncio.AbstractEventLoop:
        with self._guard:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="agent-memory", daemon=True).start()
                self._loop = loop
                asyncio.run_coroutine_threadsafe(self._make_lock(), loop).result()
            return self._loop

    async def _make_lock(self) -> None:
        self._lock = asyncio.Lock()          # keeps one player's writes in order

    async def _connect(self):
        if self._client is not None:
            return self._client
        if self._clock() < self._retry_at:
            raise ConnectionError("agent memory is unavailable (waiting before trying again)")
        try:
            self._create_database(self.options)          # blocks this loop for a moment, once; a thread pool cannot start while Python is exiting
            client = self._factory(self.options)
            await client.connect()
        except Exception:
            self._retry_at = self._clock() + RETRY_SECONDS
            raise
        self._client = client
        log.info("agent memory connected (database %s)", self.options.database)
        return client

    # ---- writing
    def record(self, q: Question, exchange: Exchange | None, result: Result) -> None:
        """Remember an answered question and the run behind it, without waiting. `exchange` is None for a run that gave no good answer:
        its trace is still kept (as a failure), but nothing is added to the conversation."""
        with self._guard:
            if self._pending >= MAX_PENDING:
                log.warning("agent memory is behind (%d writes waiting); dropping one for %s", self._pending, q.player_uuid)
                return
            self._pending += 1
        try:
            future = asyncio.run_coroutine_threadsafe(self._write(q, exchange, result), self._event_loop())
        except Exception:
            self._done(None)
            raise
        future.add_done_callback(self._done)

    def _done(self, future) -> None:
        with self._guard:
            self._pending -= 1
        error = future.exception() if future is not None and not future.cancelled() else None
        if error is not None:
            log.warning("agent memory write failed: %s: %s", type(error).__name__, error)

    async def _write(self, q: Question, exchange: Exchange | None, result: Result) -> None:
        assert self._lock is not None
        async with self._lock:
            client = await self._connect()
            o, user_message = self.options, None
            if exchange is not None:
                for role, content, metadata in messages_for(q, exchange, result):
                    message = await client.short_term.add_message(
                        q.player_uuid, role, content, extract_entities=o.extract, extract_relations=o.extract,
                        generate_embedding=o.embed, metadata=metadata)
                    user_message = message if role == "user" else user_message
            if o.traces:
                await self._trace(client, q, result, user_message)

    async def _trace(self, client, q: Question, result: Result, user_message) -> None:
        from neo4j_agent_memory import ToolCallStatus
        reasoning, embed = client.reasoning, self.options.embed
        trace = await reasoning.start_trace(
            q.player_uuid, q.text, generate_embedding=embed, user_identifier=q.player_uuid,
            triggered_by_message_id=user_message.id if user_message is not None else None,
            metadata={"player": q.player_name, "model": result.model, "ms": result.ms, "status": result.status,
                      "context_turns": result.context_turns})
        for step in result.steps:
            made = await reasoning.add_step(trace.id, action=step.tool, observation=f"{step.result_chars} characters", generate_embedding=embed,
                                            metadata={"turn": step.turn})
            await reasoning.record_tool_call(made.id, step.tool, step.args, result=tool_result(step), duration_ms=step.ms,
                                             status=ToolCallStatus.ERROR if step.error else ToolCallStatus.SUCCESS, error=step.error)
        await reasoning.complete_trace(trace.id, outcome=result.status, success=result.status == "ok")

    # ---- reading
    def recent(self, player: str, limit: int = MAX_EXCHANGES) -> list[Exchange]:
        """The player's last exchanges from memory, oldest first. Empty when there are none, or memory cannot be reached (logged)."""
        try:
            return asyncio.run_coroutine_threadsafe(self._read(player, limit), self._event_loop()).result(READ_TIMEOUT)
        except Exception as exc:
            log.warning("agent memory read failed: %s: %s", type(exc).__name__, exc)
            return []

    async def _read(self, player: str, limit: int) -> list[Exchange]:
        # The library's get_conversation(limit=n) gives the OLDEST n messages (and at most 1000), and the book needs the latest, so this
        # is one query of our own through the library's connection. It relies on the library's storage (a Conversation with
        # HAS_MESSAGE to Message nodes with role, content, metadata and timestamp); test_memory_graph.py fails if that changes.
        client = await self._connect()
        rows = await client._client.execute_read(LATEST_MESSAGES, {"session_id": player, "limit": limit * 2 + 2})
        messages = []
        for row in reversed(rows):                                   # newest first from the query, oldest first for reading
            node = dict(row["m"])
            messages.append(SimpleNamespace(role=node.get("role"), content=node.get("content") or "", metadata=_stored_metadata(node.get("metadata")),
                                            at=_epoch(node.get("timestamp"))))
        return exchanges_from(messages, limit)

    def close(self) -> None:
        """Wait briefly for pending writes, close the client and stop the loop. Safe to call more than once."""
        loop = self._loop
        if loop is None:
            return
        deadline = time.monotonic() + 5.0
        while self._pending and time.monotonic() < deadline:
            time.sleep(0.05)
        client = self._client                 # read after the wait: a write that was pending may be what connected it
        if client is not None:
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(5.0)
            except Exception as exc:
                log.warning("agent memory close failed: %s", exc)
        loop.call_soon_threadsafe(loop.stop)
        self._loop = self._client = None
