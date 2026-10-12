"""The explain book through the service: answers are remembered per player and POST /explain returns the book.

The real core, history and HTTP layer run; only the agent behind them is replaced by a fake that answers without a model or a database."""
from __future__ import annotations

import importlib

import pytest

from minecorp_agent import config, core
from minecorp_agent.history import History
from minecorp_agent.models import Result, Step

fastapi_testclient = pytest.importorskip("fastapi.testclient")

SECRET = "explain-test-secret-0123456789"
WIKI = {"kind": "wiki", "label": "Tutorial:Shelters > Hole in the wall", "url": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"}
GAME = {"kind": "game data", "label": "Recipes for Iron Pickaxe"}


class FakeAgent:
    """Answers `<question> (answered)` with the sources it is told to, and can be told to fail."""

    def __init__(self):
        self.sources, self.status, self.raises = [], "ok", None

    def run(self, q, request_id):
        if self.raises:
            raise self.raises
        steps = [Step(turn=1, tool="get_recipes", args={}, ms=1, result_chars=1, sources=list(self.sources))]
        return Result(answer=f"{q.text} (answered)\nsecond line", status=self.status, model="m", ms=1, steps=steps)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
    import minecorp_agent.app as app_mod
    app_mod = importlib.reload(app_mod)
    agent = FakeAgent()
    monkeypatch.setattr(core, "get_agent", lambda: agent)
    monkeypatch.setattr(core, "history", History())
    return fastapi_testclient.TestClient(app_mod.app), agent


def ask(client, question="how do I smelt iron?", player="u-1"):
    return client.post("/ask", json={"playerUuid": player, "playerName": "Steve", "sessionId": "s", "question": question}, headers={"X-MineCorp-Key": SECRET})


def explain(client, player="u-1", key=SECRET):
    headers = {} if key is None else {"X-MineCorp-Key": key}
    return client.post("/explain", json={"playerUuid": player}, headers=headers)


class TestAuth:
    @pytest.mark.parametrize("key", [None, "", "wrong", SECRET + "x"])
    def test_anything_but_the_secret_is_unauthorised(self, api, key):
        client, _ = api
        assert explain(client, key=key).status_code == 401

    @pytest.mark.parametrize("body", [{}, {"playerUuid": ""}, {"playerUuid": "x" * 65}, {"player": "u-1"}])
    def test_a_bad_body_is_rejected(self, api, body):
        client, _ = api
        assert client.post("/explain", json=body, headers={"X-MineCorp-Key": SECRET}).status_code == 422


class TestTheBook:
    def test_before_any_question_the_book_has_no_pages(self, api):
        client, _ = api
        r = explain(client)
        assert r.status_code == 200 and r.json() == {"title": "Agent notes", "author": "MineCorp Agent", "pages": []}

    def test_after_a_question_the_book_holds_the_question_answer_and_sources(self, api):
        client, agent = api
        agent.sources = [GAME, WIKI]
        ask(client)
        pages = explain(client).json()["pages"]
        assert [[l["text"] for l in p["lines"]] for p in pages] == [
            ["Agent notes", "", "Last 1 question(s):", "1. how do I smelt iron?"],
            ["1. how do I smelt iron?", "", "how do I smelt iron? (answered)", "second line"],
            ["Sources for 1:", "", "- game data: Recipes for Iron Pickaxe", "- wiki: Tutorial:Shelters > Hole in the wall"]]

    def test_links_and_jumps_are_in_the_book_and_empty_ones_are_left_out(self, api):
        client, agent = api
        agent.sources = [GAME, WIKI]
        ask(client)
        pages = explain(client).json()["pages"]
        assert pages[0]["lines"][3] == {"text": "1. how do I smelt iron?", "goto": 2}
        assert pages[2]["lines"][2] == {"text": "- game data: Recipes for Iron Pickaxe"} and pages[2]["lines"][3]["url"] == WIKI["url"]

    def test_the_graph_lines_of_a_source_are_in_the_book_under_it(self, api):
        client, agent = api
        agent.sources = [{"kind": "game data", "label": "Recipes for Ender Eye", "graph": ["(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})"]}, WIKI]
        ask(client)
        lines = explain(client).json()["pages"][2]["lines"]
        assert [l["text"] for l in lines][2:] == ["- game data: Recipes for Ender Eye", "   (Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})",
                                                  "- wiki: Tutorial:Shelters > Hole in the wall"]
        assert lines[4]["url"] == WIKI["url"] and "url" not in lines[3]

    def test_the_last_five_are_kept(self, api):
        client, _ = api
        for n in range(7):
            ask(client, f"question {n}")
        contents = [l["text"] for l in explain(client).json()["pages"][0]["lines"]]
        assert contents[2] == "Last 5 question(s):" and contents[3:] == [f"{n + 1}. question {n + 2}" for n in range(5)]

    def test_each_player_sees_only_their_own(self, api):
        client, _ = api
        ask(client, "alex asks", player="alex")
        ask(client, "sam asks", player="sam")
        alex = " ".join(l["text"] for p in explain(client, "alex").json()["pages"] for l in p["lines"])
        assert "alex asks" in alex and "sam asks" not in alex
        assert explain(client, "nobody").json()["pages"] == []

    def test_a_failed_run_is_not_remembered(self, api):
        client, agent = api
        agent.raises = RuntimeError("graph down")
        assert "not available" in ask(client).json()["answer"]
        assert explain(client).json()["pages"] == []

    def test_an_answer_that_ran_out_of_turns_is_not_remembered(self, api):
        client, agent = api
        agent.status = "turn_limit"
        ask(client)
        assert explain(client).json()["pages"] == []

    def test_an_answer_with_no_lookups_says_so(self, api):
        client, _ = api
        ask(client)
        texts = [l["text"] for l in explain(client).json()["pages"][2]["lines"]]
        assert texts[-1].startswith("No sources")

    def test_asking_to_explain_does_not_remember_itself(self, api):
        client, _ = api
        ask(client)
        explain(client)
        explain(client)
        assert [l["text"] for l in explain(client).json()["pages"][0]["lines"]][2] == "Last 1 question(s):"


def test_the_core_gives_the_same_book_without_the_service(monkeypatch):
    monkeypatch.setattr(core, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(core, "history", History())
    from minecorp_agent.models import Question
    core.answer_with_sources(Question("u-9", "Steve", "s", "hello"), "r1")
    book = core.explain("u-9")
    assert book["pages"][0]["lines"][3]["text"] == "1. hello" and core.explain("u-1") is None
