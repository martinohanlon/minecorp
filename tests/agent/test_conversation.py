"""Conversation context: which earlier exchanges go to the model with a new question (agent/minecorp_agent/conversation.py), and how the
agent and the core use them. No model, no Neo4j."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

import pytest

from minecorp_agent import conversation as conv, core, memory as mem
from minecorp_agent.agent import Agent
from minecorp_agent.config import Settings
from minecorp_agent.history import Exchange, History
from minecorp_agent.models import Question, Result

NOW = 1_000_000.0
MIN = 60.0


def ex(n, minutes_ago=None, at=None):
    when = at if at is not None else (0.0 if minutes_ago is None else NOW - minutes_ago * MIN)
    return Exchange(f"q{n}", (f"a{n}", f"a{n} second line"), ({"kind": "game data", "label": f"L{n}"},), at=when)


OPTIONS = conv.ConversationOptions(turns=3, idle_minutes=15)


# --------------------------------------------------------------------------- the settings

class TestOptions:
    def test_the_defaults_are_the_owners_choice(self):
        o = conv.options_from_env({})
        assert (o.turns, o.idle_minutes, o.idle_seconds) == (3, 15.0, 900.0)

    def test_both_are_read(self):
        o = conv.options_from_env({"AGENT_CONVERSATION_TURNS": " 5 ", "AGENT_CONVERSATION_IDLE_MINUTES": "0.5"})
        assert (o.turns, o.idle_seconds) == (5, 30.0)

    def test_zero_turns_turns_the_context_off(self):
        assert conv.options_from_env({"AGENT_CONVERSATION_TURNS": "0"}).turns == 0

    @pytest.mark.parametrize("env,words", [
        ({"AGENT_CONVERSATION_TURNS": "many"}, "AGENT_CONVERSATION_TURNS must be a whole number"),
        ({"AGENT_CONVERSATION_TURNS": "2.5"}, "AGENT_CONVERSATION_TURNS must be a whole number"),
        ({"AGENT_CONVERSATION_TURNS": "-1"}, "AGENT_CONVERSATION_TURNS must be from 0 to 10"),
        ({"AGENT_CONVERSATION_TURNS": "11"}, "AGENT_CONVERSATION_TURNS must be from 0 to 10"),
        ({"AGENT_CONVERSATION_IDLE_MINUTES": "soon"}, "AGENT_CONVERSATION_IDLE_MINUTES must be a number"),
        ({"AGENT_CONVERSATION_IDLE_MINUTES": "0"}, "AGENT_CONVERSATION_IDLE_MINUTES must be above 0"),
        ({"AGENT_CONVERSATION_IDLE_MINUTES": "-3"}, "AGENT_CONVERSATION_IDLE_MINUTES must be above 0"),
        ({"AGENT_CONVERSATION_IDLE_MINUTES": "nan"}, "AGENT_CONVERSATION_IDLE_MINUTES must be above 0")])
    def test_a_wrong_value_says_which(self, env, words):
        with pytest.raises(conv.ConversationConfigError, match=words):
            conv.options_from_env(env)


# --------------------------------------------------------------------------- which exchanges count

class TestRecentContext:
    def test_the_last_three_of_a_running_conversation_oldest_first(self):
        history = [ex(n, minutes_ago=10 - n) for n in range(1, 6)]            # 5 exchanges, a minute apart, the last one 5 minutes ago
        assert [e.question for e in conv.recent_context(history, now=NOW, options=OPTIONS)] == ["q3", "q4", "q5"]

    def test_fewer_than_three_are_all_sent(self):
        assert [e.question for e in conv.recent_context([ex(1, 2), ex(2, 1)], now=NOW, options=OPTIONS)] == ["q1", "q2"]

    def test_nothing_before_the_first_question(self):
        assert conv.recent_context([], now=NOW, options=OPTIONS) == []

    def test_a_player_who_has_been_quiet_longer_than_the_idle_time_starts_afresh(self):
        assert conv.recent_context([ex(1, 16)], now=NOW, options=OPTIONS) == []

    def test_exactly_the_idle_time_is_still_the_same_conversation(self):
        assert [e.question for e in conv.recent_context([ex(1, 15)], now=NOW, options=OPTIONS)] == ["q1"]

    def test_a_long_gap_inside_the_history_ends_the_conversation_there(self):
        history = [ex(1, 120), ex(2, 119), ex(3, 5), ex(4, 3)]                # two exchanges two hours ago, two just now
        assert [e.question for e in conv.recent_context(history, now=NOW, options=OPTIONS)] == ["q3", "q4"]

    def test_the_gap_is_measured_between_exchanges_not_from_now(self):
        history = [ex(1, 40), ex(2, 27), ex(3, 14), ex(4, 1)]                  # each 13 minutes after the one before: one long conversation
        assert [e.question for e in conv.recent_context(history, now=NOW, options=OPTIONS)] == ["q2", "q3", "q4"]

    def test_the_number_of_turns_is_the_limit(self):
        history = [ex(n, minutes_ago=n * 0.1) for n in range(9, 0, -1)]
        assert len(conv.recent_context(history, now=NOW, options=conv.ConversationOptions(turns=1, idle_minutes=15))) == 1
        assert len(conv.recent_context(history, now=NOW, options=conv.ConversationOptions(turns=10, idle_minutes=15))) == 9

    def test_zero_turns_sends_nothing(self):
        assert conv.recent_context([ex(1, 1)], now=NOW, options=conv.ConversationOptions(turns=0)) == []

    def test_an_exchange_with_no_time_is_taken_as_part_of_the_conversation(self):
        assert [e.question for e in conv.recent_context([ex(1), ex(2)], now=NOW, options=OPTIONS)] == ["q1", "q2"]

    def test_an_untimed_exchange_does_not_hide_the_gap_before_it(self):
        history = [ex(1, 200), ex(2), ex(3, 2)]                                # the untimed one cannot show the gap, but q1 is 200 minutes from q3
        assert [e.question for e in conv.recent_context(history, now=NOW, options=OPTIONS)] == ["q2", "q3"]

    def test_the_input_is_not_changed(self):
        history = [ex(1, 3), ex(2, 2)]
        conv.recent_context(history, now=NOW, options=OPTIONS)
        assert [e.question for e in history] == ["q1", "q2"]


# --------------------------------------------------------------------------- what the model is sent

def settings(tmp_path):
    return Settings(host="127.0.0.1", port=8080, shared_secret="s" * 16, openai_model="m", neo4j_uri="bolt://x", neo4j_user="u", neo4j_password="p",
                    neo4j_database="d", max_turns=3, llm_timeout=5.0, trace_dir=tmp_path / "logs")


class Client:
    def __init__(self):
        self.sent = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.sent.append(kwargs["messages"])
        return NS(choices=[NS(message=NS(content="Answer.", tool_calls=None))], usage=NS(prompt_tokens=1, completion_tokens=1))


class TestAgentSendsTheConversation:
    def ask(self, tmp_path, context):
        client = Client()
        result = Agent(settings(tmp_path), graph=None, client=client).run(Question("u", "Steve", "s", "and how many of those?"), "r", context=context)
        return client.sent[0], result

    def test_each_earlier_exchange_is_a_question_and_its_answer_before_the_new_question(self, tmp_path):
        sent, result = self.ask(tmp_path, [ex(1), ex(2)])
        assert [m["role"] for m in sent] == ["system", "user", "assistant", "user", "assistant", "user"]
        assert sent[1:5] == [{"role": "user", "content": "Player Steve asks: q1"}, {"role": "assistant", "content": "a1\na1 second line"},
                             {"role": "user", "content": "Player Steve asks: q2"}, {"role": "assistant", "content": "a2\na2 second line"}]
        assert sent[5] == {"role": "user", "content": "Player Steve asks: and how many of those?"}
        assert result.context_turns == 2

    def test_no_context_is_the_same_single_question_as_before(self, tmp_path):
        sent, result = self.ask(tmp_path, [])
        assert [m["role"] for m in sent] == ["system", "user"] and result.context_turns == 0

    def test_only_text_is_replayed_not_sources_or_tool_results(self, tmp_path):
        sent, _ = self.ask(tmp_path, [ex(1)])
        assert "L1" not in json.dumps(sent) and "game data" not in json.dumps(sent)

    def test_the_trace_says_how_many_earlier_exchanges_were_sent(self, tmp_path):
        self.ask(tmp_path, [ex(1), ex(2), ex(3)])
        entry = json.loads((tmp_path / "logs" / "traces.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert entry["contextTurns"] == 3

    def test_the_prompt_tells_the_model_how_to_use_the_earlier_turns(self):
        from minecorp_agent.prompt import SYSTEM_PROMPT
        assert "earlier part of this conversation" in SYSTEM_PROMPT and "ignore them" in SYSTEM_PROMPT


# --------------------------------------------------------------------------- the core builds it

class RecordingAgent:
    def __init__(self):
        self.contexts = []

    def run(self, q, request_id, context=()):
        self.contexts.append([e.question for e in context])
        return Result(answer=f"answer to {q.text}", status="ok", model="m", ms=1)


class StoredMemory:
    def __init__(self, stored=None, fail=False):
        self.stored, self.fail, self.reads = stored or {}, fail, 0

    def record(self, q, exchange, result):
        pass

    def recent(self, player, limit=5):
        self.reads += 1
        if self.fail:
            raise RuntimeError("memory is down")
        return self.stored.get(player, [])


@pytest.fixture
def wired(monkeypatch):
    agent = RecordingAgent()
    monkeypatch.setattr(core, "get_agent", lambda: agent)
    monkeypatch.setattr(core, "history", History())
    return agent


def ask(text, player="u-1"):
    return core.answer_with_sources(Question(player, "Steve", "s", text), "r")


class TestCoreContext:
    def test_the_first_question_has_none_and_the_next_has_the_one_before(self, wired):
        ask("first")
        ask("second")
        ask("third")
        assert wired.contexts == [[], ["first"], ["first", "second"]]

    def test_the_context_is_the_players_own(self, wired):
        ask("mine", "u-1")
        ask("theirs", "u-2")
        assert wired.contexts == [[], []]

    def test_only_the_last_three_are_sent(self, wired):
        for n in range(6):
            ask(f"q{n}")
        assert wired.contexts[-1] == ["q2", "q3", "q4"]

    def test_the_new_question_is_not_in_its_own_context(self, wired):
        ask("only")
        assert wired.contexts == [[]] and [e.question for e in core.history.recent("u-1")] == ["only"]

    def test_a_good_answer_is_stamped_with_the_time(self, wired, monkeypatch):
        monkeypatch.setattr(core.time, "time", lambda: 12345.0)
        ask("x")
        assert core.history.recent("u-1")[0].at == 12345.0

    def test_a_quiet_player_starts_a_new_conversation(self, wired, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(core.time, "time", lambda: clock[0])
        ask("morning")
        clock[0] += 14 * MIN
        ask("a bit later")
        clock[0] += 16 * MIN
        ask("much later")
        assert wired.contexts == [[], ["morning"], []]

    def test_zero_turns_turns_it_off(self, wired, monkeypatch):
        monkeypatch.setenv("AGENT_CONVERSATION_TURNS", "0")
        ask("a")
        ask("b")
        assert wired.contexts == [[], []]

    def test_the_settings_are_used(self, wired, monkeypatch):
        monkeypatch.setenv("AGENT_CONVERSATION_TURNS", "1")
        for n in range(3):
            ask(f"q{n}")
        assert wired.contexts == [[], ["q0"], ["q1"]]

    def test_a_wrong_setting_uses_the_defaults_and_says_so_once(self, wired, monkeypatch, caplog):
        monkeypatch.setenv("AGENT_CONVERSATION_TURNS", "lots")
        ask("a")
        ask("b")
        assert wired.contexts == [[], ["a"]]
        assert caplog.text.count("conversation context uses its defaults because of a setting") == 1

    def test_the_options_are_logged_at_first_use(self, wired, caplog):
        caplog.set_level(logging.INFO)
        ask("a")
        assert "conversation context: the last 3 exchange(s)" in caplog.text and "15 minute(s) quiet" in caplog.text

    def test_a_run_that_gave_no_good_answer_is_not_part_of_the_conversation(self, wired, monkeypatch):
        monkeypatch.setattr(core, "get_agent", lambda: type("A", (), {"run": lambda self, q, r, context=(): Result("x", "turn_limit", "m", 1)})())
        ask("hard one")
        monkeypatch.setattr(core, "get_agent", lambda: wired)
        ask("next")
        assert wired.contexts == [[]]


class TestCoreContextAfterARestart:
    def stored(self, minutes_ago):
        return [Exchange("before the restart", ("it was answered",), (), at=core.time.time() - minutes_ago * MIN)]

    def test_a_recent_conversation_is_picked_up_from_memory(self, wired, monkeypatch):
        memory = StoredMemory({"u-1": self.stored(5)})
        monkeypatch.setattr(core, "_memory", memory)
        monkeypatch.setattr(core, "_memory_ready", True)
        ask("and then?")
        assert wired.contexts == [["before the restart"]]

    def test_an_old_one_is_not(self, wired, monkeypatch):
        monkeypatch.setattr(core, "_memory", StoredMemory({"u-1": self.stored(120)}))
        monkeypatch.setattr(core, "_memory_ready", True)
        ask("hello again")
        assert wired.contexts == [[]]

    def test_memory_is_asked_once_and_the_cache_has_them_from_then_on(self, wired, monkeypatch):
        memory = StoredMemory({"u-1": self.stored(1)})
        monkeypatch.setattr(core, "_memory", memory)
        monkeypatch.setattr(core, "_memory_ready", True)
        ask("one")
        ask("two")
        assert memory.reads == 1 and wired.contexts == [["before the restart"], ["before the restart", "one"]]

    def test_memory_that_fails_means_no_context_not_no_answer(self, wired, monkeypatch):
        monkeypatch.setattr(core, "_memory", StoredMemory(fail=True))
        monkeypatch.setattr(core, "_memory_ready", True)
        answer, _ = ask("still answered")
        assert answer == "answer to still answered" and wired.contexts == [[]]

    def test_it_works_without_agent_memory_at_all(self, wired):
        assert core.get_memory() is None
        ask("a")
        ask("b")
        assert wired.contexts == [[], ["a"]]


# --------------------------------------------------------------------------- the stored times

class TestStoredTimes:
    def test_a_neo4j_time_becomes_seconds_since_the_epoch(self):
        from datetime import datetime, timezone
        moment = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
        assert mem._epoch(moment) == moment.timestamp()
        assert mem._epoch(NS(to_native=lambda: moment)) == moment.timestamp()

    def test_a_time_with_no_zone_is_utc(self):
        from datetime import datetime, timezone
        assert mem._epoch(datetime(2026, 10, 16, 12, 0, 0)) == datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    @pytest.mark.parametrize("stamp", [None, "yesterday", 12, object()])
    def test_nothing_usable_is_zero(self, stamp):
        assert mem._epoch(stamp) == 0.0

    def test_an_exchange_read_back_has_the_time_of_its_answer(self):
        msgs = [NS(role="user", content="q", metadata={}, at=100.0), NS(role="assistant", content="a", metadata={}, at=105.0)]
        (e,) = mem.exchanges_from(msgs)
        assert e.at == 105.0

    def test_a_time_is_not_part_of_what_an_exchange_is(self):
        assert Exchange("q", ("a",), (), at=1.0) == Exchange("q", ("a",), (), at=2.0)
