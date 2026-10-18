"""The real neo4j-agent-memory library against a real database: a throwaway one called `minecorpmemorytest`, created and dropped here.

Embeddings and entity extraction are off, so nothing calls OpenAI. This checks what the unit tests cannot: that what we write is what the
library stores, that it reads back in order and per player, that a restart loses nothing, and what the memory graph looks like.
Run with:  pytest -m integration"""
from __future__ import annotations

import time

import pytest

from graphdb import empty_test_database
from minecorp_agent import core, memory as mem
from minecorp_agent.sessions import Sessions
from minecorp_agent.history import Exchange, History
from minecorp_agent.models import Question, Result, Step

pytestmark = pytest.mark.integration

DB = "minecorpmemorytest"


@pytest.fixture(scope="module")
def database():
    yield from empty_test_database(DB)


def options(database, **kw):
    return mem.MemoryOptions(backend="neo4j", database=DB, uri=database["uri"], user=database["user"], password=database["password"], **kw)


def question(player="p1", text="how do I make an eye of ender?", session=None):
    return Question(player, "Steve", session or f"{player}:c1", text)


def run(status="ok"):
    steps = [Step(1, "get_recipes", {"name": "eye of ender"}, 12, 900, sources=[{"kind": "game data", "label": "Recipes for Ender Eye"}]),
             Step(2, "find", {"query": "x"}, 5, 40, error="boom")]
    return Result(answer="a", status=status, model="m1", ms=321, steps=steps)


def exchange(n):
    return Exchange(f"question {n}", (f"line one of {n}", "- línea ñ"),
                    ({"kind": "game data", "label": f"Recipes {n}", "graph": ["(Item {A})<-[CREATES]-(Recipe)"]}, {"kind": "wiki", "label": "P > S", "url": "https://minecraft.wiki/w/P#S"}))


def settle(memory):
    deadline = time.monotonic() + 30
    while memory._pending and time.monotonic() < deadline:
        time.sleep(0.05)
    assert memory._pending == 0


@pytest.fixture(scope="module")
def memory(database):
    m = mem.AgentMemory(options(database))
    for n in range(7):
        m.record(question("p1", f"question {n}"), exchange(n), run())
    m.record(question("p2", "someone else"), exchange(99), run())
    m.record(question("p1", "one that failed"), None, run("turn_limit"))
    settle(m)
    yield m
    m.close()


def test_the_last_five_come_back_oldest_first_exactly_as_they_were_written(memory):
    assert memory.recent("p1:c1") == [exchange(n) for n in range(2, 7)]


def test_each_player_has_their_own_conversation(memory):
    assert memory.recent("p2:c1") == [exchange(99)]
    assert memory.recent("nobody:c1") == []


def test_each_user_has_their_conversation_and_their_traces_and_is_the_domain_graphs_player(memory, database):
    db = database["db"]
    assert db.one("MATCH (:User {identifier: 'p1'})-[:HAS_CONVERSATION]->(c:Conversation) RETURN c.session_id") == "p1:c1"
    assert db.one("MATCH (:User {identifier: 'p1'})-[:HAS_TRACE]->(t:ReasoningTrace) RETURN count(t)") == 8
    assert db.one("MATCH (:User {identifier: 'p1'})-[:IS_PLAYER]->(p:Player) RETURN p.uuid") == "p1"
    assert db.one("MATCH (:User {identifier: 'p1'})-[:IS_PLAYER]->(p:Player) RETURN p.name") == "Steve"


def test_an_authored_player_keeps_their_name_and_is_not_duplicated(database):
    db = database["db"]
    db.q("MERGE (:Player {uuid: 'p5', name: 'martinohanlon'})")
    m = mem.AgentMemory(options(database))
    try:
        for n in (1, 2):
            m.record(question("p5", f"q{n}"), exchange(n), run())
        settle(m)
        assert db.q("MATCH (p:Player {uuid: 'p5'}) RETURN p.name AS n") == [{"n": "martinohanlon"}]
        assert db.one("MATCH (:User {identifier: 'p5'})-[r:IS_PLAYER]->(:Player {uuid: 'p5'}) RETURN count(r)") == 1
    finally:
        m.close()


def test_the_newest_conversation_of_a_player_is_found(memory):
    assert memory.latest_session("p1") == "p1:c1"
    assert memory.latest_session("p2") == "p2:c1"
    assert memory.latest_session("nobody") is None


def test_a_player_has_many_conversations_and_each_is_read_on_its_own(database):
    m = mem.AgentMemory(options(database))
    try:
        m.record(question("p3", "first chat", session="p3:a"), exchange(1), run())
        m.record(question("p3", "second chat", session="p3:b"), exchange(2), run())
        settle(m)
        assert m.recent("p3:a") == [exchange(1)] and m.recent("p3:b") == [exchange(2)]
        assert m.latest_session("p3") == "p3:b"
        m.record(question("p3", "third chat", session="p3:c"), exchange(3), run())
        settle(m)
        assert m.latest_session("p3") == "p3:c"
        users = database["db"].q("MATCH (c:Conversation) WHERE c.session_id STARTS WITH 'p3:' RETURN c.user_identifier AS u")
        assert [r["u"] for r in users] == ["p3"] * 3                      # the conversations belong to the player
    finally:
        m.close()


def test_a_conversation_filed_under_the_bare_uuid_is_still_found(database):
    m = mem.AgentMemory(options(database))
    try:
        m.record(question("p4", "old style", session="p4"), exchange(1), run())
        settle(m)
        assert m.latest_session("p4") == "p4"
    finally:
        m.close()


def test_a_run_with_no_good_answer_added_no_messages(memory, database):
    assert database["db"].one("MATCH (c:Conversation)-[:HAS_MESSAGE]->(m:Message) WHERE c.session_id IN ['p1:c1', 'p2:c1'] RETURN count(m)") == 16        # 8 exchanges, two messages each


def test_a_restart_loses_nothing(memory, database):
    fresh = mem.AgentMemory(options(database))
    try:
        assert fresh.recent("p1:c1") == memory.recent("p1:c1")
    finally:
        fresh.close()


def test_every_run_left_a_trace_with_its_tool_calls(memory, database):
    db = database["db"]
    mine = "t.session_id IN ['p1:c1', 'p2:c1']"                          # the fixture's conversations (other tests add their own)
    assert db.one(f"MATCH (t:ReasoningTrace) WHERE {mine} RETURN count(t)") == 9
    assert db.one(f"MATCH (t:ReasoningTrace) WHERE {mine} AND t.success = false RETURN count(t)") == 1
    failed = db.one("MATCH (t:ReasoningTrace {success: false}) RETURN t.task")
    assert failed == "one that failed"
    tools = db.q("MATCH (:ReasoningTrace {task: 'question 3'})-[:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->(:ToolCall)-[:INSTANCE_OF]->(t:Tool) "
                 "RETURN t.name AS name ORDER BY name")
    assert [r["name"] for r in tools] == ["find", "get_recipes"]


def test_a_trace_is_linked_to_the_message_that_began_it(memory, database):
    assert database["db"].one("MATCH (:ReasoningTrace {task: 'question 3'})-[:INITIATED_BY]->(m:Message {role: 'user'}) RETURN m.content") == "question 3"


def test_the_memory_lives_only_in_its_own_database(memory, database):
    """Nothing was written to the default database: build-graph's wipe cannot reach memory."""
    from neo4j import GraphDatabase
    with GraphDatabase.driver(database["uri"], auth=(database["user"], database["password"])) as driver:
        rows = driver.execute_query("MATCH (m:Message) WHERE m.content STARTS WITH 'question ' RETURN count(m) AS n", database_="neo4j").records
    assert rows[0]["n"] == 0


def test_the_book_is_rebuilt_from_memory_after_a_restart_of_the_service(memory, database, monkeypatch):
    monkeypatch.setattr(core, "history", History())                     # the service has just started: nothing cached
    monkeypatch.setattr(core, "sessions", Sessions())
    monkeypatch.setattr(core, "_memory", mem.AgentMemory(options(database)))
    monkeypatch.setattr(core, "_memory_ready", True)
    try:
        book = core.explain("p1")
        text = " ".join(line["text"] for page in book["pages"] for line in page["lines"])
        assert "question 2" in text and "question 6" in text and "question 1" not in text
        assert any(line.get("url") == "https://minecraft.wiki/w/P#S" for page in book["pages"] for line in page["lines"])
    finally:
        core._memory.close()


def test_memory_in_the_game_graphs_own_database_sits_beside_it_and_can_be_joined_to_it(built, agent_graph):
    """The default: one database. Memory adds its own labels without touching the game graph, and one query can join the two."""
    from graphdb import TEST_DB
    db = built["db"]
    game = "MATCH (n) WHERE n:Item OR n:Recipe OR n:Action OR n:Mob OR n:Biome OR n:Process OR n:Chunk RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l"
    before = db.q(game)
    o = mem.MemoryOptions(backend="neo4j", database=TEST_DB, uri=built["uri"], user=built["user"], password=built["password"])
    memory = mem.AgentMemory(o)
    try:
        asked = Exchange("how do I make an Ender Eye?", ("- Crafting Table: 1 Blaze Powder + 1 Ender Pearl",), ({"kind": "game data", "label": "Recipes for Ender Eye"},))
        memory.record(question("shared-1", asked.question), asked, run())
        settle(memory)
        assert memory.recent("shared-1:c1") == [asked]
        assert db.q(game) == before                                           # nothing of the game graph was touched
        joined = db.q("MATCH (m:Message {role: 'user'}), (i:Item) WHERE m.content CONTAINS i.name AND i.name = 'Ender Eye' RETURN i.name AS item, m.content AS said")
        assert joined == [{"item": "Ender Eye", "said": "how do I make an Ender Eye?"}]
        assert agent_graph.read("MATCH (i:Item {name: 'Ender Eye'}) RETURN count(i) AS n")[0]["n"] == 1      # the agent's own read still works
    finally:
        memory.close()
        db.q("MATCH (n) WHERE n:Conversation OR n:Message OR n:ReasoningTrace OR n:ReasoningStep OR n:ToolCall OR n:Tool OR n:User DETACH DELETE n")


def test_the_database_is_created_when_it_is_missing(database):
    from neo4j import GraphDatabase
    other = "minecorpmemorytest2"
    o = mem.MemoryOptions(backend="neo4j", database=other, uri=database["uri"], user=database["user"], password=database["password"])
    with GraphDatabase.driver(o.uri, auth=(o.user, o.password)) as driver:
        driver.execute_query(f"DROP DATABASE {other} IF EXISTS WAIT", database_="system")
        try:
            m = mem.AgentMemory(o)
            m.record(question(), exchange(1), run())
            settle(m)
            assert m.recent("p1:c1") == [exchange(1)]
            m.close()
        finally:
            driver.execute_query(f"DROP DATABASE {other} IF EXISTS WAIT", database_="system")
