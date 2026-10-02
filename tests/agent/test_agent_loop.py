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
from minecorp_agent.agent import FALLBACK, MAX_ANSWER_CHARS, MAX_TOOL_CHARS, Agent
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
    return {"text": "x" * (MAX_TOOL_CHARS * 2)}


FAKE_TOOLS = [Tool("echo", "Echo the arguments back to the model.", {"type": "object", "properties": {}}, echo),
              Tool("boom", "Always fails.", {"type": "object", "properties": {}}, boom),
              Tool("big", "Returns far too much text.", {"type": "object", "properties": {}}, big)]


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
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5, "model_calls": 1}

    def test_the_first_request_has_the_system_prompt_the_player_and_all_tools(self, tmp_path):
        client = FakeClient(reply("ok"))
        run(client, tmp_path, text="hello there")
        sent = client.calls[0]
        assert sent["model"] == "test-model" and sent["tool_choice"] == "auto"
        assert sent["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        assert sent["messages"][1] == {"role": "user", "content": "Player Steve asks: hello there"}
        assert [t["function"]["name"] for t in sent["tools"]] == ["echo", "boom", "big"]

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
        assert "failed" in self._second_request_tool_content(client)
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
        assert "kaboom" not in content and "not available" in content
        assert result.steps[0].error == "ValueError: kaboom"
        assert result.answer == "The graph is down." and result.status == "ok"

    def test_long_tool_output_is_cut_before_the_model_sees_it(self, tmp_path):
        client = FakeClient(reply(None, [tool_call("c", "big", {})]), reply("ok"))
        result, _ = run(client, tmp_path)
        content = self._second_request_tool_content(client)
        assert len(content) < MAX_TOOL_CHARS + 50 and content.endswith('"[truncated]"')
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
        assert result.status == "error" and result.answer == FALLBACK
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
                                   "result_chars": result.steps[0].result_chars, "error": None}]

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
