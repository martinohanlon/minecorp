"""How the core uses agent memory: the setting that switches it on, what it records, and rebuilding the book from it.

The agent behind the core and the memory are fakes; no model, no Neo4j."""
from __future__ import annotations

import pytest

from minecorp_agent import core, memory as mem
from minecorp_agent.history import Exchange, History
from minecorp_agent.models import Question, Result, Step


class FakeAgent:
    def __init__(self, status="ok"):
        self.status = status

    def run(self, q, request_id, context=()):
        steps = [Step(turn=1, tool="get_recipes", args={}, ms=1, result_chars=1, sources=[{"kind": "game data", "label": "Recipes for X"}])]
        return Result(answer=f"{q.text} (answered)\nsecond line", status=self.status, model="m", ms=1, steps=steps)


class FakeMemory:
    def __init__(self, stored=None, fail_on_record=False):
        self.recorded, self.reads, self.stored, self.fail_on_record = [], [], stored or {}, fail_on_record

    def record(self, q, exchange, result):
        if self.fail_on_record:
            raise RuntimeError("memory exploded")
        self.recorded.append((q, exchange, result))

    def recent(self, player, limit=5):
        self.reads.append(player)
        return self.stored.get(player, [])


@pytest.fixture
def wired(monkeypatch):
    """The core with a fake agent, a fresh history and a fake memory switched on."""
    memory = FakeMemory()
    monkeypatch.setattr(core, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(core, "history", History())
    monkeypatch.setattr(core, "_memory", memory)
    monkeypatch.setattr(core, "_memory_ready", True)
    return memory


Q = Question("u-1", "Steve", "s", "how do I smelt iron?")


class TestTheSwitch:
    def test_by_default_there_is_no_memory(self):
        assert core.get_memory() is None

    def test_neo4j_gives_a_memory_with_the_options_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "pw")
        monkeypatch.setenv("AGENT_MEMORY_DATABASE", "elsewhere")
        got = core.get_memory()
        assert isinstance(got, mem.AgentMemory) and got.options.database == "elsewhere" and got.options.traces is True
        assert core.get_memory() is got                        # decided once

    def test_nothing_connects_while_the_memory_is_being_made(self, monkeypatch):
        monkeypatch.setenv("AGENT_MEMORY", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "pw")
        monkeypatch.setattr(mem, "build_client", lambda o: pytest.fail("connected"))
        monkeypatch.setattr(mem, "ensure_database", lambda o: pytest.fail("connected"))
        assert core.get_memory() is not None

    @pytest.mark.parametrize("env", [{"AGENT_MEMORY": "mongo"}, {"AGENT_MEMORY": "neo4j"}])
    def test_a_wrong_setting_turns_memory_off_and_says_so(self, monkeypatch, caplog, env):
        monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert core.get_memory() is None
        assert "agent memory is OFF because of a setting" in caplog.text

    def test_a_missing_package_turns_memory_off_and_says_so(self, monkeypatch, caplog):
        monkeypatch.setenv("AGENT_MEMORY", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", "pw")
        monkeypatch.setattr(mem, "check_package", lambda: (_ for _ in ()).throw(mem.MemoryConfigError("needs the neo4j-agent-memory package")))
        assert core.get_memory() is None and "needs the neo4j-agent-memory package" in caplog.text

    def test_the_mode_is_logged_at_start(self, monkeypatch, caplog):
        caplog.set_level("INFO")
        core.get_memory()
        assert "agent memory: local" in caplog.text


class TestShuttingDown:
    def test_close_memory_lets_it_finish_and_forgets_it(self, wired):
        closed = []
        wired.close = lambda: closed.append(True)
        core.close_memory()
        assert closed == [True] and core._memory is None and core._memory_ready is False

    def test_close_memory_with_no_memory_is_fine_and_repeatable(self):
        core.close_memory()
        core.close_memory()
        assert core.get_memory() is None

    def test_the_service_closes_memory_when_it_stops(self, monkeypatch):
        import importlib

        from minecorp_agent import config
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("AGENT_SHARED_SECRET", "shutdown-test-secret-0123456789")
        import minecorp_agent.app as app_mod
        app_mod = importlib.reload(app_mod)
        closed = []
        monkeypatch.setattr(app_mod, "close_memory", lambda: closed.append(True))
        with fastapi_testclient.TestClient(app_mod.app):
            assert closed == []                              # still running
        assert closed == [True]


class TestRecording:
    def test_a_good_answer_is_recorded_with_its_exchange(self, wired):
        core.answer_with_sources(Q, "r1")
        ((q, exchange, result),) = wired.recorded
        assert q is Q and result.status == "ok"
        assert exchange == Exchange(Q.text, ("how do I smelt iron? (answered)", "second line"), ({"kind": "game data", "label": "Recipes for X"},))

    def test_the_cache_is_still_filled_so_the_book_needs_no_database(self, wired):
        core.answer_with_sources(Q, "r1")
        assert [e.question for e in core.history.recent("u-1")] == [Q.text]

    def test_a_run_with_no_good_answer_is_recorded_as_a_run_only(self, wired, monkeypatch):
        monkeypatch.setattr(core, "get_agent", lambda: FakeAgent("turn_limit"))
        core.answer_with_sources(Q, "r1")
        ((_q, exchange, result),) = wired.recorded
        assert exchange is None and result.status == "turn_limit" and core.history.recent("u-1") == []

    def test_a_memory_that_fails_does_not_cost_the_player_the_answer(self, wired, caplog):
        wired.fail_on_record = True
        answer, sources = core.answer_with_sources(Q, "r1")
        assert answer.startswith("how do I smelt iron?") and sources == [{"kind": "game data", "label": "Recipes for X"}]
        assert "agent memory could not take the run: RuntimeError: memory exploded" in caplog.text
        assert [e.question for e in core.history.recent("u-1")] == [Q.text]

    def test_an_agent_that_cannot_start_records_nothing(self, wired, monkeypatch):
        monkeypatch.setattr(core, "get_agent", lambda: (_ for _ in ()).throw(RuntimeError("no key")))
        answer, sources = core.answer_with_sources(Q, "r1")
        assert "not available" in answer and sources == [] and wired.recorded == []

    def test_without_memory_nothing_changes(self, monkeypatch):
        monkeypatch.setattr(core, "get_agent", lambda: FakeAgent())
        monkeypatch.setattr(core, "history", History())
        core.answer_with_sources(Q, "r1")
        assert [e.question for e in core.history.recent("u-1")] == [Q.text]


class TestTheBook:
    STORED = [Exchange("older question", ("older answer",), ({"kind": "wiki", "label": "Tutorial:X > Y", "url": "https://minecraft.wiki/w/X"},))]

    def test_the_cache_is_used_when_it_has_the_player(self, wired):
        core.answer_with_sources(Q, "r1")           # a player's first question looks in memory once (for the conversation so far)
        assert wired.reads == ["u-1"]
        core.explain("u-1")
        core.answer_with_sources(Q, "r2")           # from here the cache has them
        assert wired.reads == ["u-1"]

    def test_after_a_restart_the_book_is_rebuilt_from_memory(self, wired):
        wired.stored["u-2"] = self.STORED
        book = core.explain("u-2")
        text = " ".join(line["text"] for page in book["pages"] for line in page["lines"])
        assert wired.reads == ["u-2"] and "older question" in text and "Tutorial:X > Y" in text
        assert any(line.get("url") == "https://minecraft.wiki/w/X" for page in book["pages"] for line in page["lines"])

    def test_what_memory_gave_is_cached_so_it_is_read_once(self, wired):
        wired.stored["u-2"] = self.STORED
        core.explain("u-2")
        core.explain("u-2")
        assert wired.reads == ["u-2"] and core.history.recent("u-2") == self.STORED

    def test_a_player_memory_has_never_seen_has_no_book(self, wired):
        assert core.explain("nobody") is None and wired.reads == ["nobody"]

    def test_new_answers_go_after_what_was_recalled(self, wired):
        wired.stored["u-1"] = self.STORED
        core.explain("u-1")
        core.answer_with_sources(Q, "r1")
        assert [e.question for e in core.history.recent("u-1")] == ["older question", Q.text]

    def test_without_memory_a_restart_means_an_empty_book(self, monkeypatch):
        monkeypatch.setattr(core, "history", History())
        assert core.explain("u-2") is None
