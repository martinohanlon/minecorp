"""The benchmark's rules (agent/bench/run_bench.py) and the model-only mode of the agent, with a fake model: no network, no cost."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
from bench.run_bench import cost_of, is_correct, load_prices, outcome_of, summarise  # noqa: E402
from minecorp_agent.agent import Agent  # noqa: E402
from minecorp_agent.config import Settings  # noqa: E402
from minecorp_agent.models import Question  # noqa: E402
from minecorp_agent.prompt import BARE_PROMPT, SYSTEM_PROMPT  # noqa: E402

PRICE = {"input": 2.0, "output": 10.0, "cached_input": None}


# ------------------------------------------------------------------ cost

def test_cost_is_tokens_times_price_per_million():
    c = cost_of({"prompt_tokens": 1_000_000, "completion_tokens": 100_000}, PRICE)
    assert c["uncached"] == 2.0 + 1.0
    assert c["cached"] == c["uncached"]          # nothing was cached


def test_cached_input_is_cheaper_and_assumed_to_cost_a_tenth_without_a_listed_price():
    c = cost_of({"prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 800_000}, PRICE)
    assert c["uncached"] == 2.0
    assert round(c["cached"], 6) == round(0.2 * 2.0 + 0.8 * 0.2, 6)     # 200k at full price, 800k at a tenth
    listed = cost_of({"prompt_tokens": 1_000_000, "completion_tokens": 0, "cached_tokens": 1_000_000}, {**PRICE, "cached_input": 0.5})
    assert listed["cached"] == 0.5


def test_reasoning_tokens_are_not_billed_twice():
    base = {"prompt_tokens": 0, "completion_tokens": 1_000_000}
    assert cost_of({**base, "reasoning_tokens": 900_000}, PRICE) == cost_of(base, PRICE)


def test_every_price_has_input_and_output():
    prices = load_prices()
    assert {"gpt-6-luna", "gpt-6.2-astra"} <= set(prices)
    for name, p in prices.items():
        assert p["input"] > 0 and p["output"] > 0, name


# ------------------------------------------------------------------ scoring

def test_a_number_must_stand_alone():
    assert is_correct("It peaks at y 232.", ["232"])
    assert is_correct("y=232, and 90 veins", ["232"])
    assert not is_correct("It peaks at 2320", ["232"])
    assert not is_correct("It peaks at 1232", ["232"])
    assert not is_correct("It peaks at 232.5", ["232"])
    assert not is_correct("I do not know.", ["232"])


def test_every_expected_string_is_needed_and_case_does_not_matter():
    assert is_correct("Use IRON ingots and sticks", ["iron ingot", "stick"])
    assert not is_correct("Use iron ingots", ["iron ingot", "stick"])


def test_the_summary_gives_cost_per_correct_answer():
    def row(arm, model, correct, cost):
        return {"arm": arm, "model": model, "correct": correct, "cost": {"cached": cost}, "ms": 100, "tools": ["a"] if arm == "kl" else [], "status": "ok"}
    rows = [row("kl", "small", True, 0.01), row("kl", "small", False, 0.03), row("bare", "small", False, 0.001)]
    by = {(s["arm"], s["model"]): s for s in summarise(rows)}
    assert by[("kl", "small")]["rate"] == 0.5
    assert round(by[("kl", "small")]["cost_per_correct"], 6) == 0.04          # 0.04 spent for one right answer
    assert by[("bare", "small")]["cost_per_correct"] is None                   # nothing right: no figure, not a division by zero
    assert by[("kl", "small")]["tool_calls"] == 1


# ------------------------------------------------------------------ the agent's model-only mode

class FakeClient:
    def __init__(self, answer="232"):
        self.calls: list[dict] = []
        self.answer = answer
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        usage = NS(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=NS(cached_tokens=64),
                   completion_tokens_details=NS(reasoning_tokens=12))
        return NS(model="fake-model-2026", choices=[NS(message=NS(content=self.answer, tool_calls=None))], usage=usage)


def settings(tmp_path, **over):
    base = dict(host="x", port=1, shared_secret="s" * 16, openai_model="fake", neo4j_uri="bolt://x", neo4j_user="u", neo4j_password="p",
                neo4j_database="d", max_turns=6, llm_timeout=5, trace_dir=tmp_path)
    return Settings(**{**base, **over})


Q = Question("p", "Player", "s", "What is it?")


def test_model_only_sends_no_tools_and_the_bare_prompt(tmp_path):
    client = FakeClient()
    result = Agent(settings(tmp_path, use_tools=False), None, client=client).run(Q, "r1")
    sent = client.calls[0]
    assert "tools" not in sent and "tool_choice" not in sent
    assert sent["messages"][0]["content"] == BARE_PROMPT
    assert result.answer == "232" and result.steps == []


def test_the_default_still_sends_the_tools_and_the_full_prompt(tmp_path):
    client = FakeClient()
    Agent(settings(tmp_path), None, client=client).run(Q, "r2")
    sent = client.calls[0]
    assert sent["tools"] and sent["tool_choice"] == "auto"
    assert sent["messages"][0]["content"] == SYSTEM_PROMPT
    assert "reasoning_effort" not in sent            # nothing is sent unless asked


def test_reasoning_effort_is_sent_only_when_set(tmp_path):
    client = FakeClient()
    Agent(settings(tmp_path, reasoning_effort="low", use_tools=False), None, client=client).run(Q, "r3")
    assert client.calls[0]["reasoning_effort"] == "low"


def test_usage_records_cached_and_reasoning_tokens_and_the_exact_model(tmp_path):
    result = Agent(settings(tmp_path, use_tools=False), None, client=FakeClient()).run(Q, "r4")
    assert result.usage["cached_tokens"] == 64 and result.usage["reasoning_tokens"] == 12
    assert result.usage["api_model"] == "fake-model-2026"
    assert result.usage["prompt_tokens"] == 100 and result.usage["completion_tokens"] == 20


def test_the_bare_prompt_keeps_the_chat_style_and_names_no_tool():
    assert "at most 6 lines" in BARE_PROMPT and "Plain text only" in BARE_PROMPT
    for word in ("tool", "search_wiki", "get_recipes"):
        assert word not in BARE_PROMPT


# ------------------------------------------------------------------ the Responses API path (the benchmark's default)

class FakeResponses:
    """Plays back prepared Responses API replies and records every request."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.responses = NS(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return self.replies.pop(0)


def resp(rid, text="", calls=(), tokens=(100, 20)):
    output = [NS(type="function_call", call_id=cid, name=name, arguments=args) for cid, name, args in calls]
    return NS(id=rid, model="fake-6", output=output, output_text=text,
              usage=NS(input_tokens=tokens[0], output_tokens=tokens[1], input_tokens_details=NS(cached_tokens=10),
                       output_tokens_details=NS(reasoning_tokens=5)))


def test_responses_api_without_tools_sends_effort_and_no_tools(tmp_path):
    client = FakeResponses(resp("r1", "232"))
    result = Agent(settings(tmp_path, api="responses", use_tools=False, reasoning_effort="medium"), None, client=client).run(Q, "x1")
    sent = client.calls[0]
    assert sent["instructions"] == BARE_PROMPT and sent["reasoning"] == {"effort": "medium"}
    assert "tools" not in sent and "previous_response_id" not in sent
    assert result.answer == "232" and result.usage["cached_tokens"] == 10 and result.usage["reasoning_tokens"] == 5
    assert result.usage["api_model"] == "fake-6"


def test_responses_api_tool_round_continues_from_the_previous_response(tmp_path):
    client = FakeResponses(resp("r1", "", [("c1", "no_such_tool", "{}")]), resp("r2", "done"))
    result = Agent(settings(tmp_path, api="responses", reasoning_effort="low"), None, client=client).run(Q, "x2")
    first, second = client.calls
    assert first["tools"] and all(t["type"] == "function" and t["strict"] is False and "name" in t for t in first["tools"])
    assert first["tool_choice"] == "auto"
    assert second["previous_response_id"] == "r1"
    assert [i["type"] for i in second["input"]] == ["function_call_output"] and second["input"][0]["call_id"] == "c1"
    assert result.answer == "done" and result.usage["model_calls"] == 2 and result.usage["prompt_tokens"] == 200
    assert [s.tool for s in result.steps] == ["no_such_tool"] and result.steps[0].error      # an unknown tool is an error the model sees, not a crash


# ------------------------------------------------------------------ alternatives, outcomes and the question file

def test_an_entry_may_list_alternatives():
    assert is_correct("It needs 3 iron ingots.", ["3 iron ingot|three iron ingot"])
    assert is_correct("It needs three iron ingots.", ["3 iron ingot|three iron ingot"])
    assert not is_correct("It needs 4 iron ingots.", ["3 iron ingot|three iron ingot"])
    assert is_correct("Level V", ["5|five|level v"])


def test_a_miss_is_either_abstained_or_wrong_and_both_fail():
    expect = ["sulfur caves"]
    assert outcome_of("They spawn in Sulfur Caves.", expect) == "right"
    assert outcome_of("I don't know that mob.", expect) == "abstained"
    assert outcome_of("I could not find a Sulfur Cube in the game.", expect) == "abstained"
    assert outcome_of("They spawn in the Nether wastes.", expect) == "wrong"
    assert outcome_of("whatever", expect, status="error") == "error"


def test_the_question_file_is_well_formed():
    import json
    from bench.run_bench import QUESTIONS
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate id"
    by_stratum = {}
    for c in cases:
        assert c["question"].endswith("?") and c["expect"] and c["gold"] and c["gold_query"].count("AS gold") == 1, c["id"]
        assert " AS gold" in c["gold_query"] and "MATCH" in c["gold_query"], c["id"]
        by_stratum[c["stratum"]] = by_stratum.get(c["stratum"], 0) + 1
    assert set(by_stratum.values()) == {15} and len(by_stratum) == 4     # fifteen questions in each of the four strata (the pilot 5 plus 10 generated)


# ------------------------------------------------------------------ the report (agent/bench/report.py)

def test_the_report_rescores_stored_answers_against_the_current_expectations():
    from bench.report import rescore
    rows = [{"id": "s4-lunge-max", "answer": "Lunge's maximum level is III.", "status": "ok", "correct": False, "outcome": "wrong"},
            {"id": "not-in-the-file", "answer": "x", "status": "ok", "correct": True, "outcome": "right"}]
    out = rescore(rows)
    assert len(out) == 1 and out[0]["correct"] and out[0]["outcome"] == "right"       # "III" is accepted; an unknown question is dropped


def test_the_bootstrap_interval_is_the_mean_difference_and_brackets_it():
    from bench.report import bootstrap_diff
    a = {f"q{i}": 1.0 for i in range(10)}
    b = {f"q{i}": 1.0 if i < 7 else 0.0 for i in range(10)}
    mean, lo, hi = bootstrap_diff(a, b)
    assert round(mean, 6) == 0.3 and lo <= mean <= hi and lo >= 0.0       # a never does worse than b, so the interval cannot go below zero
    assert bootstrap_diff(a, a)[:3] == (0.0, 0.0, 0.0)


# ------------------------------------------------------------------ the shuffled order and the word-boundary matching

def test_the_plan_covers_every_run_and_a_seed_makes_the_shuffle_repeatable():
    from bench.run_bench import plan
    cases = [{"id": f"q{i}"} for i in range(6)]
    arms = [("kl", "small"), ("bare", "small"), ("kl", "large"), ("bare", "large")]
    ordered = plan(cases, arms, 3, None)
    shuffled = plan(cases, arms, 3, 7)
    key = lambda t: (t[0]["id"], t[1], t[2])      # noqa: E731
    assert len(ordered) == 6 * 3 * 4 and sorted(map(key, ordered)) == sorted(map(key, shuffled))     # the same runs, in another order
    assert [key(t) for t in ordered] != [key(t) for t in shuffled]
    assert [key(t) for t in shuffled] == [key(t) for t in plan(cases, arms, 3, 7)]                  # same seed, same order
    assert [key(t) for t in shuffled] != [key(t) for t in plan(cases, arms, 3, 8)]
    assert [t[0]["id"] for t in ordered[:12]] == ["q0"] * 12                                        # unshuffled: question by question


def test_a_bare_alternative_has_to_start_a_word_and_a_roman_numeral_is_a_whole_word():
    assert not is_correct("The maximum level is unknown.", ["5|five|v"])          # "v" is in "level" but is not the numeral
    assert is_correct("Maximum level V.", ["5|five|v"])
    assert is_correct("It is level IV", ["4|four|iv"])
    assert not is_correct("It needs stone and iron", ["1|one"])                    # "one" is in "stone"
    assert is_correct("You need one", ["1|one"])
    assert is_correct("Use sticks", ["stick"])                                      # a plural still matches


def test_the_most_spawned_mob_answer_accepts_the_plural():
    import json
    from bench.run_bench import QUESTIONS
    case = next(c for c in json.loads(QUESTIONS.read_text(encoding="utf-8")) if c["id"] == "s3-most-spawned-mob")
    assert is_correct("Endermen spawn in 61 biomes.", case["expect"]) and is_correct("The Enderman.", case["expect"])
