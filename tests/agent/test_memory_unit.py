"""Unit tests for agent/minecorp_agent/memory.py: the settings, what is stored and read back, and the background writer.

No Neo4j and no OpenAI: the neo4j-agent-memory client is replaced by a fake that records the calls it gets. The real library against a real
database is tested in test_memory_graph.py."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from minecorp_agent import memory as mem
from minecorp_agent.history import Exchange
from minecorp_agent.models import Question, Result, Step

Q = Question("uuid-1", "Steve", "s1", "how do I make an eye of ender?")
SOURCES = ({"kind": "game data", "label": "Recipes for Ender Eye", "graph": ["(Item {Ender Eye})<-[CREATES]-(Recipe)"]},)
EX = Exchange(Q.text, ("- Crafting Table: 1 blaze powder + 1 ender pearl", "- That makes 1 eye of ender."), SOURCES)


def result(status="ok", steps=None):
    steps = [Step(1, "get_recipes", {"name": "eye of ender"}, 12, 900, sources=[{"kind": "game data", "label": "Recipes for Ender Eye"}]),
             Step(2, "find", {"query": "x"}, 5, 40, error="boom")] if steps is None else steps
    return Result(answer="a", status=status, model="m1", ms=321, steps=steps)


# --------------------------------------------------------------------------- a fake neo4j-agent-memory client

class FakeShort:
    def __init__(self, owner):
        self.owner = owner

    async def add_message(self, session_id, role, content, **kwargs):
        await self.owner.pause()
        self.owner.calls.append(("message", session_id, role, content, kwargs))
        return SimpleNamespace(id=f"m{len(self.owner.calls)}", role=role, content=content, metadata=kwargs.get("metadata"))

    async def get_conversation(self, session_id, **kwargs):
        self.owner.calls.append(("conversation", session_id, kwargs))
        return SimpleNamespace(messages=self.owner.stored.get(session_id, []))


class FakeReasoning:
    def __init__(self, owner):
        self.owner = owner

    async def start_trace(self, session_id, task, **kwargs):
        self.owner.calls.append(("trace", session_id, task, kwargs))
        return SimpleNamespace(id="t1")

    async def add_step(self, trace_id, **kwargs):
        self.owner.calls.append(("step", trace_id, kwargs))
        return SimpleNamespace(id=f"s{len(self.owner.calls)}")

    async def record_tool_call(self, step_id, tool_name, arguments, **kwargs):
        self.owner.calls.append(("tool", tool_name, arguments, kwargs))

    async def complete_trace(self, trace_id, **kwargs):
        self.owner.calls.append(("complete", trace_id, kwargs))


class FakeClient:
    def __init__(self, delay=0.0, connect_error=None):
        self.calls, self.stored, self.delay, self.connect_error = [], {}, delay, connect_error
        self.connected = self.closed = 0
        self.short_term, self.reasoning = FakeShort(self), FakeReasoning(self)
        self._client = SimpleNamespace(execute_read=self._execute_read, execute_write=self._execute_write)          # the library's own query runner

    async def _execute_read(self, query, params):
        """Gives what the real query does: the newest messages first, at most `limit`, metadata as a JSON string."""
        self.calls.append(("read", query, params))
        stored = self.stored.get(params["session_id"], [])
        return [{"m": {"role": m.role, "content": m.content, "metadata": json.dumps(m.metadata)}} for m in reversed(stored)][: params["limit"]]

    async def _execute_write(self, query, params):
        self.calls.append(("link", query, params))
        if getattr(self, "link_error", None):
            raise self.link_error

    async def pause(self):
        if self.delay:
            await asyncio.sleep(self.delay)

    async def connect(self):
        self.connected += 1
        if self.connect_error:
            raise self.connect_error

    async def close(self):
        self.closed += 1

    def kinds(self):
        return [c[0] for c in self.calls]


def options(**kw):
    return mem.MemoryOptions(backend="neo4j", password="pw", **kw)


def make(client=None, clock=time.monotonic, **kw):
    client = client or FakeClient()
    created = []
    memory = mem.AgentMemory(options(**kw), client_factory=lambda o: client, database_creator=created.append, clock=clock)
    memory.created = created
    return memory, client


def settle(memory, timeout=5.0):
    deadline = time.monotonic() + timeout
    while memory._pending and time.monotonic() < deadline:
        time.sleep(0.01)
    assert memory._pending == 0, "writes did not finish"


@pytest.fixture
def started():
    made = []

    def build(*a, **kw):
        memory, client = make(*a, **kw)
        made.append(memory)
        return memory, client
    yield build
    for m in made:
        m.close()


# --------------------------------------------------------------------------- settings

class TestOptions:
    def test_the_default_is_local_and_needs_nothing(self):
        o = mem.options_from_env({})
        assert (o.backend, o.enabled, o.database, o.embed, o.extract, o.traces) == ("local", False, "neo4j", False, False, True)

    def test_every_setting_is_read(self):
        o = mem.options_from_env({"AGENT_MEMORY": " NEO4J ", "AGENT_MEMORY_DATABASE": "mem.db-2", "AGENT_MEMORY_EMBED": "yes",
                                  "AGENT_MEMORY_EXTRACT": "1", "AGENT_MEMORY_TRACES": "off", "NEO4J_URI": "bolt://h:9", "NEO4J_USER": "u",
                                  "NEO4J_PASSWORD": "p", "OPENAI_API_KEY": "k", "OPENAI_MODEL": "gpt-x"})
        assert (o.enabled, o.database, o.embed, o.extract, o.traces) == (True, "mem.db-2", True, True, False)
        assert (o.uri, o.user, o.password, o.openai_api_key, o.llm_model) == ("bolt://h:9", "u", "p", "k", "gpt-x")

    def test_memory_shares_the_game_graphs_database_unless_told_otherwise(self):
        base = {"AGENT_MEMORY": "neo4j", "NEO4J_PASSWORD": "p"}
        assert mem.options_from_env(base).database == "neo4j"
        assert mem.options_from_env({**base, "NEO4J_DATABASE": "minecorp"}).database == "minecorp"
        assert mem.options_from_env({**base, "NEO4J_DATABASE": "minecorp", "AGENT_MEMORY_DATABASE": "memory"}).database == "memory"
        assert mem.options_from_env({**base, "NEO4J_DATABASE": "minecorp", "AGENT_MEMORY_DATABASE": "  "}).database == "minecorp"

    def test_connection_defaults(self):
        o = mem.options_from_env({"AGENT_MEMORY": "neo4j", "NEO4J_PASSWORD": "p"})
        assert (o.uri, o.user, o.llm_model) == ("bolt://localhost:7687", "neo4j", "gpt-5.4-mini")

    @pytest.mark.parametrize("env,words", [
        ({"AGENT_MEMORY": "redis"}, "AGENT_MEMORY must be one of local, neo4j"),
        ({"AGENT_MEMORY_EMBED": "maybe"}, "AGENT_MEMORY_EMBED must be true or false"),
        ({"AGENT_MEMORY_TRACES": "2"}, "AGENT_MEMORY_TRACES must be true or false"),
        ({"AGENT_MEMORY_DATABASE": "bad name"}, "not a usable Neo4j database name"),
        ({"AGENT_MEMORY_DATABASE": "1abc"}, "not a usable Neo4j database name"),
        ({"AGENT_MEMORY_DATABASE": "a;DROP"}, "not a usable Neo4j database name"),
        ({"AGENT_MEMORY": "neo4j"}, "needs NEO4J_PASSWORD"),
        ({"AGENT_MEMORY": "neo4j", "NEO4J_PASSWORD": "p", "AGENT_MEMORY_EMBED": "true"}, "AGENT_MEMORY_EMBED=true needs OPENAI_API_KEY"),
        ({"AGENT_MEMORY": "neo4j", "NEO4J_PASSWORD": "p", "AGENT_MEMORY_EXTRACT": "true"}, "AGENT_MEMORY_EXTRACT=true needs OPENAI_API_KEY")])
    def test_a_wrong_setting_says_which(self, env, words):
        with pytest.raises(mem.MemoryConfigError, match=words):
            mem.options_from_env(env)

    def test_the_other_needs_are_not_asked_for_while_memory_is_local(self):
        assert not mem.options_from_env({"AGENT_MEMORY": "local", "AGENT_MEMORY_EMBED": "true"}).enabled


# --------------------------------------------------------------------------- what is stored and read back

class TestStoredShape:
    def test_an_exchange_is_a_user_message_and_an_assistant_message(self):
        user, assistant = mem.messages_for(Q, EX, result())
        assert user == ("user", Q.text, {"player": "Steve"})
        assert assistant[0] == "assistant" and assistant[1] == "\n".join(EX.lines)
        assert {k: assistant[2][k] for k in ("model", "ms", "status")} == {"model": "m1", "ms": 321, "status": "ok"}

    def test_metadata_holds_only_plain_values_with_the_sources_as_json(self):
        for _role, _content, meta in mem.messages_for(Q, EX, result()):
            assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())
        assert json.loads(mem.messages_for(Q, EX, result())[1][2]["sources"]) == list(SOURCES)

    def test_what_is_stored_reads_back_as_the_same_exchange(self):
        stored = [SimpleNamespace(role=r, content=c, metadata=m) for r, c, m in mem.messages_for(Q, EX, result())]
        assert mem.exchanges_from(stored) == [EX]

    def test_text_that_is_not_ascii_survives(self):
        ex = Exchange("¿cómo?", ("línea ñ",), ({"kind": "wiki", "label": "Café"},))
        stored = [SimpleNamespace(role=r, content=c, metadata=m) for r, c, m in mem.messages_for(Q, ex, result())]
        assert mem.exchanges_from(stored) == [ex]

    def test_the_last_few_come_back_oldest_first(self):
        msgs = []
        for n in range(8):
            msgs += [SimpleNamespace(role="user", content=f"q{n}", metadata={}), SimpleNamespace(role="assistant", content=f"a{n}", metadata={})]
        got = mem.exchanges_from(msgs, 5)
        assert [e.question for e in got] == [f"q{n}" for n in range(3, 8)]
        assert got[0].lines == ("a3",)

    def test_an_enum_role_is_understood(self):
        role = lambda v: SimpleNamespace(value=v)       # noqa: E731  (the library gives a MessageRole enum)
        assert mem.exchanges_from([SimpleNamespace(role=role("user"), content="q", metadata={}),
                                   SimpleNamespace(role=role("assistant"), content="a", metadata={})])[0].question == "q"

    def test_a_question_with_no_answer_is_left_out_and_a_repeated_question_takes_the_next_answer(self):
        u = lambda c: SimpleNamespace(role="user", content=c, metadata={})            # noqa: E731
        a = lambda c: SimpleNamespace(role="assistant", content=c, metadata={})       # noqa: E731
        assert mem.exchanges_from([u("lonely")]) == []
        assert [e.question for e in mem.exchanges_from([u("first"), u("second"), a("answer")])] == ["second"]
        assert mem.exchanges_from([a("orphan answer"), u("q"), a("a")])[0].question == "q"

    @pytest.mark.parametrize("sources", [None, "", "not json", "{}", '"text"', "[1, 2]", "null"])
    def test_sources_that_cannot_be_read_become_none(self, sources):
        meta = {} if sources is None else {"sources": sources}
        got = mem.exchanges_from([SimpleNamespace(role="user", content="q", metadata={}), SimpleNamespace(role="assistant", content="a", metadata=meta)])
        assert got[0].sources == ()

    def test_a_message_without_metadata_is_fine(self):
        got = mem.exchanges_from([SimpleNamespace(role="user", content="q", metadata=None), SimpleNamespace(role="assistant", content="a", metadata=None)])
        assert got == [Exchange("q", ("a",), ())]

    def test_the_trace_keeps_the_size_and_source_labels_of_a_tool_result_not_the_result(self):
        assert mem.tool_result(result().steps[0]) == {"chars": 900, "sources": ["Recipes for Ender Eye"]}


# --------------------------------------------------------------------------- the writer

class TestWriting:
    def test_an_exchange_is_written_as_two_messages_under_the_players_session(self, started):
        memory, client = started()
        memory.record(Q, EX, result())
        settle(memory)
        messages = [c for c in client.calls if c[0] == "message"]
        assert [(m[1], m[2], m[3]) for m in messages] == [("s1", "user", Q.text), ("s1", "assistant", "\n".join(EX.lines))]
        assert all(m[4]["user_identifier"] == "uuid-1" for m in messages)          # the User is the player; the session is the conversation

    def test_embedding_and_extraction_are_off_unless_asked_for(self, started):
        memory, client = started()
        memory.record(Q, EX, result())
        settle(memory)
        kwargs = [c[4] for c in client.calls if c[0] == "message"]
        assert all(k["generate_embedding"] is False and k["extract_entities"] is False and k["extract_relations"] is False for k in kwargs)

    def test_embedding_and_extraction_follow_the_settings(self, started):
        memory, client = started(embed=True, extract=True, openai_api_key="k")
        memory.record(Q, EX, result())
        settle(memory)
        assert all(c[4]["generate_embedding"] and c[4]["extract_entities"] and c[4]["extract_relations"] for c in client.calls if c[0] == "message")
        trace = next(c for c in client.calls if c[0] == "trace")
        assert trace[3]["generate_embedding"] is True

    def test_the_run_is_a_trace_with_one_step_and_tool_call_per_tool(self, started):
        memory, client = started()
        memory.record(Q, EX, result())
        settle(memory)
        assert client.kinds() == ["message", "message", "trace", "step", "tool", "step", "tool", "complete", "link"]
        trace = next(c for c in client.calls if c[0] == "trace")
        assert trace[1:3] == ("s1", Q.text) and trace[3]["user_identifier"] == "uuid-1" and trace[3]["triggered_by_message_id"] == "m1"
        assert trace[3]["metadata"] == {"player": "Steve", "model": "m1", "ms": 321, "status": "ok", "context_turns": 0}
        tools = [c for c in client.calls if c[0] == "tool"]
        assert [(t[1], t[2]) for t in tools] == [("get_recipes", {"name": "eye of ender"}), ("find", {"query": "x"})]
        assert tools[0][3]["duration_ms"] == 12 and tools[0][3]["result"] == {"chars": 900, "sources": ["Recipes for Ender Eye"]}
        assert tools[0][3]["status"].value == "success" and tools[1][3]["status"].value == "error" and tools[1][3]["error"] == "boom"
        assert next(c for c in client.calls if c[0] == "complete")[2] == {"outcome": "ok", "success": True}

    def test_traces_can_be_turned_off(self, started):
        memory, client = started(traces=False)
        memory.record(Q, EX, result())
        settle(memory)
        assert client.kinds() == ["message", "message", "link"]

    def test_the_user_is_linked_to_the_domain_graphs_player_by_uuid(self, started):
        memory, client = started()
        memory.record(Q, EX, result())
        settle(memory)
        (link,) = [c for c in client.calls if c[0] == "link"]
        assert link[2] == {"uuid": "uuid-1", "name": "Steve"}
        assert "(u)-[:IS_PLAYER]->(p)" in link[1] and "MERGE (p:Player {uuid: $uuid})" in link[1]
        assert "ON CREATE SET p.name" in link[1]                          # an authored player's name is not overwritten

    def test_the_link_is_made_after_what_creates_the_user(self, started):
        memory, client = started()
        memory.record(Q, EX, result())
        settle(memory)
        assert client.kinds().index("link") > client.kinds().index("message")

    def test_a_link_that_fails_is_logged_and_costs_nothing(self, started, caplog):
        memory, client = started()
        client.link_error = RuntimeError("no player")
        memory.record(Q, EX, result())
        settle(memory)
        assert "could not link uuid-1 to their Player: RuntimeError: no player" in caplog.text
        assert client.kinds().count("message") == 2 and "complete" in client.kinds()

    def test_a_run_with_no_good_answer_leaves_a_failed_trace_and_no_messages(self, started):
        memory, client = started()
        memory.record(Q, None, result("turn_limit"))
        settle(memory)
        assert "message" not in client.kinds()
        trace = next(c for c in client.calls if c[0] == "trace")
        assert trace[3]["triggered_by_message_id"] is None
        assert next(c for c in client.calls if c[0] == "complete")[2] == {"outcome": "turn_limit", "success": False}

    def test_writes_from_one_player_stay_in_order(self, started):
        memory, client = started(FakeClient(delay=0.02), traces=False)
        for n in range(4):
            memory.record(Q, Exchange(f"q{n}", (f"a{n}",), ()), result())
        settle(memory)
        assert [c[3] for c in client.calls if c[0] == "message"] == ["q0", "a0", "q1", "a1", "q2", "a2", "q3", "a3"]

    def test_recording_does_not_wait_for_the_database(self, started):
        memory, client = started(FakeClient(delay=0.3), traces=False)
        memory.record(Q, EX, result())                     # starts the loop thread: the first call is the slowest
        began = time.monotonic()
        memory.record(Q, EX, result())
        assert time.monotonic() - began < 0.15
        settle(memory)

    def test_the_database_is_created_once_and_the_client_connected_once(self, started):
        memory, client = started()
        for _ in range(3):
            memory.record(Q, EX, result())
        settle(memory)
        assert len(memory.created) == 1 and memory.created[0].database == "neo4j" and client.connected == 1

    def test_a_write_that_fails_is_logged_and_does_not_stop_the_next(self, started, caplog):
        memory, client = started(traces=False)

        async def broken(*a, **k):
            raise RuntimeError("neo4j said no")
        client.short_term.add_message, working = broken, client.short_term.add_message
        memory.record(Q, EX, result())
        settle(memory)
        assert "agent memory write failed: RuntimeError: neo4j said no" in caplog.text
        client.short_term.add_message = working
        memory.record(Q, EX, result())
        settle(memory)
        assert client.kinds() == ["message", "message", "link"]

    def test_too_many_waiting_writes_are_dropped_and_logged(self, started, monkeypatch, caplog):
        monkeypatch.setattr(mem, "MAX_PENDING", 2)
        memory, client = started(FakeClient(delay=0.3), traces=False)
        for _ in range(5):
            memory.record(Q, EX, result())
        assert memory._pending == 2 and "agent memory is behind" in caplog.text
        settle(memory)
        assert client.kinds().count("message") == 4          # two exchanges got through, two messages each


class TestWhenNeo4jIsDown:
    def test_a_failed_connection_is_logged_and_nothing_is_raised(self, started, caplog):
        memory, client = started(FakeClient(connect_error=ConnectionError("refused")))
        memory.record(Q, EX, result())
        settle(memory)
        assert "agent memory write failed: ConnectionError: refused" in caplog.text and client.calls == []

    def test_it_does_not_try_again_for_a_minute_and_then_does(self, started):
        now = [1000.0]
        client = FakeClient(connect_error=ConnectionError("refused"))
        memory, _ = started(client, clock=lambda: now[0])
        memory.record(Q, EX, result())
        settle(memory)
        memory.record(Q, EX, result())
        settle(memory)
        assert client.connected == 1                          # the second write found it closed for now
        now[0] += mem.RETRY_SECONDS + 1
        client.connect_error = None
        memory.record(Q, EX, result())
        settle(memory)
        assert client.connected == 2 and "message" in client.kinds()

    def test_a_read_gives_nothing_and_does_not_raise(self, started, caplog):
        memory, _ = started(FakeClient(connect_error=ConnectionError("refused")))
        assert memory.recent("uuid-1") == []
        assert "agent memory read failed" in caplog.text

    def test_a_slow_read_gives_up(self, started, monkeypatch):
        monkeypatch.setattr(mem, "READ_TIMEOUT", 0.1)
        memory, client = started(FakeClient())

        async def slow(*a, **k):
            await asyncio.sleep(1)
        client.short_term.get_conversation = slow
        began = time.monotonic()
        assert memory.recent("uuid-1") == []
        assert time.monotonic() - began < 0.8


class TestReading:
    def test_a_conversations_exchanges_come_back_from_its_session(self, started):
        memory, client = started()
        client.stored["uuid-1"] = [SimpleNamespace(role=r, content=c, metadata=m) for r, c, m in mem.messages_for(Q, EX, result())]
        assert memory.recent("uuid-1") == [EX]
        call = next(c for c in client.calls if c[0] == "read")
        assert call[2] == {"session_id": "uuid-1", "limit": 12}          # five exchanges are ten messages, and two spare for a stray one
        assert "ORDER BY m.timestamp DESC" in call[1] and "$session_id" in call[1]

    def test_it_is_the_latest_exchanges_that_come_back_not_the_oldest(self, started):
        memory, client = started()
        client.stored["uuid-1"] = [SimpleNamespace(role=r, content=c, metadata={})
                                   for n in range(9) for r, c in (("user", f"q{n}"), ("assistant", f"a{n}"))]
        assert [e.question for e in memory.recent("uuid-1")] == ["q4", "q5", "q6", "q7", "q8"]
        assert [e.question for e in memory.recent("uuid-1", limit=2)] == ["q7", "q8"]

    def test_a_cut_through_the_middle_of_an_exchange_does_not_shift_the_pairs(self, started):
        memory, client = started()
        client.stored["uuid-1"] = [SimpleNamespace(role=r, content=c, metadata={})
                                   for n in range(9) for r, c in (("user", f"q{n}"), ("assistant", f"a{n}"))]
        assert [(e.question, e.lines) for e in memory.recent("uuid-1", limit=1)] == [("q8", ("a8",))]

    def test_a_message_whose_stored_metadata_is_damaged_still_reads(self, started):
        memory, client = started()
        client.stored["uuid-1"] = [SimpleNamespace(role="user", content="q", metadata={}), SimpleNamespace(role="assistant", content="a", metadata={})]

        async def damaged(query, params):
            return [{"m": {"role": "assistant", "content": "a", "metadata": "{not json"}}, {"m": {"role": "user", "content": "q", "metadata": None}}]
        client._client.execute_read = damaged
        assert memory.recent("uuid-1") == [Exchange("q", ("a",), ())]

    def test_a_player_with_no_memory_gets_an_empty_list(self, started):
        assert started()[0].recent("nobody") == []


class TestClosing:
    def test_close_waits_for_pending_writes_and_closes_the_client(self):
        memory, client = make(FakeClient(delay=0.2), traces=False)
        memory.record(Q, EX, result())
        memory.close()
        assert client.kinds() == ["message", "message", "link"] and client.closed == 1

    def test_close_is_safe_when_never_used_and_when_repeated(self):
        memory, _ = make()
        memory.close()
        memory.close()
        memory2, _ = make()
        memory2.record(Q, EX, result())
        settle(memory2)
        memory2.close()
        memory2.close()
