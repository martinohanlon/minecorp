"""Unit tests for the agent loop (agent/minecorp_agent/agent.py) with a scripted fake model: no network, no cost.

The fake client plays back prepared model replies, and records every request so the tests can check what the
loop sent: the messages, the tool results, and `tool_choice`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from minecorp_agent import agent as agent_mod
from minecorp_agent.agent import ERROR_ANSWER, FALLBACK, MAX_ANSWER_CHARS, Agent
from minecorp_agent.config import Settings
from minecorp_agent.models import Question
from minecorp_agent.prompt import SYSTEM_PROMPT
from minecorp_agent.tools import Tool


# --------------------------------------------------------------------------- fakes

def tool_call(call_id, name, args):
    return NS(id=call_id, function=NS(name=name, arguments=args if isinstance(args, str) else json.dumps(args)))


def reply(content=None, calls=None, tokens=(10, 5)):
    return NS(choices=[NS(message=NS(content=content, tool_calls=calls or None))],
              usage=NS(prompt_tokens=tokens[0], completion_tokens=tokens[1]))


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def echo(graph, args):
    return {"echo": args}


def boom(graph, args):
    raise ValueError("kaboom")


def big(graph, args):
    return {"title": "t", "rows": [{"n": i, "text": "x" * 500} for i in range(40)]}   # about 20,000 characters


def outage(graph, args):
    raise ConnectionError("down")


def odd(graph, args):
    raise RuntimeError("a bug")


def recipes(graph, args):
    """A recipe result as the real tool gives it, with the private key for the category the graph really requires."""
    return {"item": "Iron Pickaxe", "recipes": [{"station": "Crafting Table", "requires": [{"item": "Iron Ingot", "count": 3, "_via": "Iron Tool Materials"}]}]}


FAKE_TOOLS = [Tool("get_recipes", "Recipes for an item.", {"type": "object", "properties": {}}, recipes),
              Tool("echo", "Echo the arguments back to the model.", {"type": "object", "properties": {}}, echo),
              Tool("boom", "Always fails.", {"type": "object", "properties": {}}, boom),
              Tool("big", "Returns far too much text.", {"type": "object", "properties": {}}, big, budget=3000),
              Tool("outage", "The graph is down.", {"type": "object", "properties": {}}, outage),
              Tool("odd", "Breaks in an unexpected way.", {"type": "object", "properties": {}}, odd)]


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch):
    monkeypatch.setattr(agent_mod, "TOOLS", FAKE_TOOLS)
    monkeypatch.setattr(agent_mod, "TOOLS_BY_NAME", {t.name: t for t in FAKE_TOOLS})


def settings(tmp_path, max_turns=6, model="test-model"):
    return Settings(host="127.0.0.1", port=8080, shared_secret="s" * 16, openai_model=model, neo4j_uri="bolt://x",
                    neo4j_user="u", neo4j_password="p", neo4j_database="d", max_turns=max_turns, llm_timeout=5.0,
                    trace_dir=tmp_path / "logs")


def run(client, tmp_path, text="How do I make a pickaxe?", **kw):
    a = Agent(settings(tmp_path, **kw), graph=None, client=client)
    return a.run(Question("uuid-1", "Steve", "session-1", text), "req-1"), a


def traces(tmp_path):
    f = tmp_path / "logs" / "traces.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


# --------------------------------------------------------------------------- the loop

class TestLoop:
    def test_an_answer_without_tools_takes_one_model_call(self, tmp_path):
        client = FakeClient(reply("Use a crafting table."))
        result, _ = run(client, tmp_path)
        assert result.answer == "Use a crafting table." and result.status == "ok" and result.error is None
        assert len(client.calls) == 1 and result.steps == []
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "model_calls": 1,
                                "cached_tokens": 0, "reasoning_tokens": 0, "api_model": "test-model"}   # the last three are for the benchmark's cost

    def test_the_first_request_has_the_system_prompt_the_player_and_all_tools(self, tmp_path):
        client = FakeClient(reply("ok"))
        run(client, tmp_path, text="hello there")
        sent = client.calls[0]
        assert sent["model"] == "test-model" and sent["tool_choice"] == "auto"
        assert sent["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        assert sent["messages"][1] == {"role": "user", "content": "Player Steve asks: hello there"}
        assert [t["function"]["name"] for t in sent["tools"]] == ["get_recipes", "echo", "boom", "big", "outage", "odd"]

    def test_a_tool_round_then_an_answer(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c1", "echo", {"item": "stick"})]), reply("Done."))
        result, _ = run(client, tmp_path)
        assert result.answer == "Done." and len(client.calls) == 2
        assert [(s.turn, s.tool, s.args, s.error) for s in result.steps] == [(1, "echo", {"item": "stick"}, None)]
        second = client.calls[1]["messages"]
        assistant = second[2]
        assert assistant["role"] == "assistant" and assistant["tool_calls"][0] == {
            "id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"item": "stick"}'}}
        assert second[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"echo":{"item":"stick"}}'}
        assert result.usage["model_calls"] == 2 and result.usage["prompt_tokens"] == 20

    def test_several_tool_calls_in_one_turn_each_get_a_result(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "echo", {"n": 1}), tool_call("b", "echo", {"n": 2})]), reply("Both."))
        result, _ = run(client, tmp_path)
        tool_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b"]
        assert len(result.steps) == 2 and {s.turn for s in result.steps} == {1}

    def test_tool_calls_across_turns_are_numbered_by_turn(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "echo", {})]), reply(None, [tool_call("b", "echo", {})]), reply("End."))
        result, _ = run(client, tmp_path)
        assert [s.turn for s in result.steps] == [1, 2]

    def test_the_answer_is_stripped(self, tmp_path):
        result, _ = run(FakeClient(reply("  padded  \n")), tmp_path)
        assert result.answer == "padded"


class TestToolProblems:
    def _second_request_tool_content(self, client):
        return [m for m in client.calls[1]["messages"] if m["role"] == "tool"][0]["content"]

    def test_an_unknown_tool_is_reported_to_the_model_and_the_loop_goes_on(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "nope", {})]), reply("Sorry."))
        result, _ = run(client, tmp_path)
        assert result.answer == "Sorry." and result.status == "ok"
        assert json.loads(self._second_request_tool_content(client))["type"] == "bad_argument"
        assert result.steps[0].error == "ValueError: unknown tool nope"

    def test_malformed_arguments_are_reported(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "echo", "{not json")]), reply("Sorry."))
        result, _ = run(client, tmp_path)
        assert result.steps[0].error.startswith("JSONDecodeError") and result.steps[0].args == {}

    def test_arguments_that_are_not_an_object_are_reported(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "echo", "[1, 2]")]), reply("Sorry."))
        result, _ = run(client, tmp_path)
        assert "must be an object" in result.steps[0].error

    def test_empty_arguments_mean_an_empty_object(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "echo", "")]), reply("ok"))
        result, _ = run(client, tmp_path)
        assert result.steps[0].error is None and result.steps[0].args == {}

    def test_a_tool_that_raises_does_not_leak_the_exception_to_the_player(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "boom", {})]), reply("The graph is down."))
        result, _ = run(client, tmp_path)
        content = self._second_request_tool_content(client)
        assert json.loads(content)["type"] == "bad_argument" and "kaboom" in content   # the model may fix its arguments
        assert result.steps[0].error == "ValueError: kaboom"
        assert result.answer == "The graph is down." and result.status == "ok"

    def test_an_outage_tells_the_model_to_say_the_graph_is_not_available(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "outage", {})]), reply("Down."))
        run(client, tmp_path)
        error = json.loads(self._second_request_tool_content(client))
        assert error["type"] == "unavailable" and "not available" in error["error"] and "down" not in error["error"]

    def test_an_unexpected_failure_is_not_called_an_outage(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "odd", {})]), reply("Hm."))
        run(client, tmp_path)
        error = json.loads(self._second_request_tool_content(client))
        assert error["type"] == "failed" and "not available" not in error["error"] and "a bug" not in error["error"]

    def test_long_tool_output_loses_whole_entries_and_stays_valid_json(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "big", {})]), reply("ok"))
        result, _ = run(client, tmp_path)
        content = self._second_request_tool_content(client)
        data = json.loads(content)                                   # never cut in the middle
        assert len(content) <= 3000 and 0 < len(data["rows"]) < 40
        assert data["omitted"]["rows"] == 40 - len(data["rows"]) and "left out" in data["note"]
        assert result.steps[0].result_chars == len(content)


class TestTurnLimit:
    def test_the_last_turn_forbids_tools_and_takes_the_answer(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "echo", {})]), reply(None, [tool_call("b", "echo", {})]), reply("Final."))
        result, _ = run(client, tmp_path, max_turns=3)
        assert [c["tool_choice"] for c in client.calls] == ["auto", "auto", "none"]
        assert all(c["tools"] for c in client.calls)                        # tools are still described on the last call
        assert result.answer == "Final." and result.status == "ok"

    def test_a_model_that_still_calls_tools_on_the_last_turn_gets_the_fallback(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "echo", {})]), reply(None, [tool_call("b", "echo", {})]))
        result, _ = run(client, tmp_path, max_turns=2)
        assert result.answer == FALLBACK and result.status == "turn_limit"

    def test_an_empty_answer_on_the_last_turn_is_a_turn_limit(self, tmp_path):
        result, _ = run(FakeClient(reply("")), tmp_path, max_turns=1)
        assert result.answer == FALLBACK and result.status == "turn_limit"

    def test_an_empty_answer_earlier_is_still_the_fallback(self, tmp_path):
        result, _ = run(FakeClient(reply(None)), tmp_path)
        assert result.answer == FALLBACK and result.status == "ok"

    def test_it_never_makes_more_model_calls_than_max_turns(self, tmp_path):
        client = FakeClient(*[reply(None, [tool_call(str(i), "echo", {})]) for i in range(10)])
        run(client, tmp_path, max_turns=4)
        assert len(client.calls) == 4


class TestFailures:
    def test_a_model_error_becomes_a_friendly_answer_and_an_error_status(self, tmp_path):
        result, _ = run(FakeClient(RuntimeError("rate limited")), tmp_path)
        assert result.status == "error" and result.answer == ERROR_ANSWER
        assert result.answer != FALLBACK                                    # a failure must not read like a question it could not answer
        assert result.error == "RuntimeError: rate limited"

    def test_a_model_error_after_a_tool_round_keeps_the_steps(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "echo", {})]), TimeoutError("slow"))
        result, _ = run(client, tmp_path)
        assert result.status == "error" and len(result.steps) == 1

    def test_run_never_raises(self, tmp_path):
        run(FakeClient(Exception("anything")), tmp_path)


# --------------------------------------------------------------------------- tidying the answer for chat

class TestTidy:
    tidy = staticmethod(Agent._tidy)

    def test_markdown_marks_are_removed(self):
        assert self.tidy("**Bold** and __also__ `code`") == "Bold and also code"

    def test_headings_lose_their_hashes(self):
        assert self.tidy("# Title\n## Sub") == "Title\nSub"

    def test_star_and_dot_bullets_become_dashes(self):
        assert self.tidy("* one\n• two\n- three") == "- one\n- two\n- three"

    def test_curly_quotes_become_plain_ones(self):
        assert self.tidy("It’s “quoted” and ‘this’") == "It's \"quoted\" and 'this'"

    def test_text_at_the_limit_is_untouched_and_over_it_is_cut_with_dots(self):
        assert self.tidy("a" * MAX_ANSWER_CHARS) == "a" * MAX_ANSWER_CHARS
        cut = self.tidy("a" * (MAX_ANSWER_CHARS + 50))
        assert len(cut) == MAX_ANSWER_CHARS + 3 and cut.endswith("...")

    def test_plain_text_passes_through(self):
        assert self.tidy("Smelt 3 raw iron.\nCraft the pickaxe.") == "Smelt 3 raw iron.\nCraft the pickaxe."

    def test_the_run_returns_tidied_text(self, tmp_path):
        result, _ = run(FakeClient(reply("**Use** the furnace")), tmp_path)
        assert result.answer == "Use the furnace"


# --------------------------------------------------------------------------- decision trace

class TestTrace:
    def test_one_json_line_per_question_with_the_decision_trace(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "echo", {"x": 1})]), reply("Answer."))
        result, _ = run(client, tmp_path)
        (entry,) = traces(tmp_path)
        assert entry["requestId"] == "req-1" and entry["player"] == "Steve" and entry["sessionId"] == "session-1"
        assert entry["question"] == "How do I make a pickaxe?" and entry["answer"] == "Answer."
        assert entry["status"] == "ok" and entry["model"] == "test-model" and entry["error"] is None
        assert entry["usage"]["model_calls"] == 2 and entry["ms"] >= 0 and entry["at"]
        assert entry["steps"] == [{"turn": 1, "tool": "echo", "args": {"x": 1}, "ms": entry["steps"][0]["ms"],
                                   "result_chars": result.steps[0].result_chars, "error": None, "sources": []}]

    def test_the_sources_of_each_tool_call_are_in_the_trace_and_on_the_result(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_mod, "sources_for", lambda tool, args, output: [{"kind": "game data", "label": f"From {tool} {args.get('x')}"}])
        client = FakeClient(reply(None, [tool_call("a", "echo", {"x": 1}), tool_call("b", "echo", {"x": 1}), tool_call("c", "echo", {"x": 2})]),
                            reply("Answer."))
        result, _ = run(client, tmp_path)
        (entry,) = traces(tmp_path)
        assert [s["sources"] for s in entry["steps"]][0] == [{"kind": "game data", "label": "From echo 1"}]
        assert result.sources == [{"kind": "game data", "label": "From echo 1"}, {"kind": "game data", "label": "From echo 2"}]   # each once

    def test_a_private_key_reaches_the_sources_but_never_the_model(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("a", "get_recipes", {})]), reply("Answer."))
        result, _ = run(client, tmp_path)
        sent = self._second_request_tool_content(client)
        assert "_via" not in sent and "Iron Tool Materials" not in sent and "Iron Ingot" in sent
        graph = result.steps[0].sources[0]["graph"]
        assert "(Item {Iron Pickaxe})<-[CREATES]-(Recipe)-[REQUIRES]->(ItemClass {Iron Tool Materials})-[INCLUDES]->(Item {Iron Ingot})" in graph
        (entry,) = traces(tmp_path)
        assert entry["steps"][0]["sources"][0]["graph"] == graph

    def _second_request_tool_content(self, client):
        return [m for m in client.calls[1]["messages"] if m["role"] == "tool"][0]["content"]

    def test_a_failed_tool_call_has_no_sources(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_mod, "sources_for", lambda tool, args, output: [{"kind": "game data", "label": "never"}])
        client = FakeClient(reply(None, [tool_call("a", "boom", {})]), reply("Sorry."))
        result, _ = run(client, tmp_path)
        assert result.steps[0].sources == [] and result.sources == []
    def test_traces_are_appended(self, tmp_path):
        run(FakeClient(reply("one")), tmp_path)
        run(FakeClient(reply("two")), tmp_path)
        assert [t["answer"] for t in traces(tmp_path)] == ["one", "two"]

    def test_failures_are_traced_too(self, tmp_path):
        run(FakeClient(RuntimeError("down")), tmp_path)
        (entry,) = traces(tmp_path)
        assert entry["status"] == "error" and entry["error"] == "RuntimeError: down"

    def test_a_trace_that_cannot_be_written_never_breaks_the_answer(self, tmp_path):
        blocked = tmp_path / "logs"
        blocked.write_text("a file where the folder should be")        # mkdir will fail
        result, _ = run(FakeClient(reply("Still answered.")), tmp_path)
        assert result.answer == "Still answered."
