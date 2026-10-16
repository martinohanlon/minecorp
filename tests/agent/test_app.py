"""Unit tests for the HTTP layer (app.py) and the core wrapper (core.py), with the agent itself replaced."""
from __future__ import annotations

import importlib

import pytest

from minecorp_agent import config, core
from minecorp_agent.models import Question, Result

fastapi_testclient = pytest.importorskip("fastapi.testclient")

SECRET = "app-test-secret-0123456789"
GOOD = {"playerUuid": "u-1", "playerName": "Steve", "sessionId": "s-1", "question": "how do I smelt iron?"}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)         # never read the real .env
    monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
    import minecorp_agent.app as app_mod
    app_mod = importlib.reload(app_mod)                                       # the secret is read when the module loads
    calls = []

    def fake_answer(question, request_id):
        calls.append((question, request_id))
        return f"answer to: {question.text}", [{"kind": "game data", "label": "Recipes for Iron Ingot"}]

    monkeypatch.setattr(app_mod, "answer_with_sources", fake_answer)
    return fastapi_testclient.TestClient(app_mod.app), calls


def ask(client, body=None, key=SECRET, **headers):
    hdrs = dict(headers)
    if key is not None:
        hdrs["X-MineCorp-Key"] = key
    return client.post("/ask", json=GOOD if body is None else body, headers=hdrs)


class TestHealth:
    def test_health_needs_no_secret_and_reveals_nothing(self, api):
        client, _ = api
        r = client.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}

    def test_no_interactive_docs_are_served(self, api):
        client, _ = api
        assert client.get("/docs").status_code == 404 and client.get("/redoc").status_code == 404


class TestAuth:
    @pytest.mark.parametrize("key", [None, "", "wrong", SECRET[:-1], SECRET + "x", SECRET.upper()])
    def test_anything_but_the_exact_secret_is_unauthorised(self, api, key):
        client, calls = api
        assert ask(client, key=key).status_code == 401
        assert calls == [], "the agent must not run for an unauthorised request"

    def test_the_header_name_is_case_insensitive(self, api):
        client, _ = api
        assert client.post("/ask", json=GOOD, headers={"x-minecorp-key": SECRET}).status_code == 200

    def test_the_secret_in_the_body_is_not_enough(self, api):
        client, _ = api
        assert ask(client, body={**GOOD, "key": SECRET}, key=None).status_code == 401

    def test_the_key_is_checked_before_the_body_so_strangers_learn_nothing_about_the_fields(self, api):
        client, calls = api
        r = client.post("/ask", json={"nonsense": True})
        assert r.status_code == 401 and r.json() == {"detail": "unauthorized"}
        r = ask(client, body={"nonsense": True}, key="wrong")
        assert r.status_code == 401 and "playerUuid" not in r.text
        assert calls == []

    def test_malformed_json_is_rejected_by_the_framework_before_the_key_is_checked(self, api):
        # Known and harmless: this reveals only that JSON is expected, not any field names.
        client, _ = api
        r = client.post("/ask", content="not json", headers={"Content-Type": "application/json"})
        assert r.status_code == 422 and "playerUuid" not in r.text


class TestAsk:
    def test_a_good_request_returns_the_answer_and_a_request_id(self, api):
        client, calls = api
        r = ask(client)
        assert r.status_code == 200
        body = r.json()
        assert body["answer"] == "answer to: how do I smelt iron?"
        assert len(body["requestId"]) == 12 and int(body["requestId"], 16) >= 0
        assert set(body) == {"requestId", "answer", "sources"}
        assert body["sources"] == [{"kind": "game data", "label": "Recipes for Iron Ingot"}]      # a source with no link has no url key

    def test_a_source_with_a_link_carries_it(self, api, monkeypatch):
        import minecorp_agent.app as app_mod
        monkeypatch.setattr(app_mod, "answer_with_sources", lambda q, rid: ("ok", [{"kind": "wiki", "label": "Tutorial:Shelters > Hole in the wall",
                                                                                   "url": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"}]))
        client, _ = api
        assert ask(client).json()["sources"] == [{"kind": "wiki", "label": "Tutorial:Shelters > Hole in the wall",
                                                  "url": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"}]

    def test_a_source_carries_its_graph_lines_and_one_without_has_no_graph_key(self, api, monkeypatch):
        import minecorp_agent.app as app_mod
        graph = ["(Item {Ender Eye})<-[CREATES]-(Recipe)-[REQUIRES]->(Item {Blaze Powder})"]
        monkeypatch.setattr(app_mod, "answer_with_sources", lambda q, rid: ("ok", [{"kind": "game data", "label": "Recipes for Ender Eye", "graph": graph},
                                                                                   {"kind": "game data", "label": "Rank"}]))
        client, _ = api
        assert ask(client).json()["sources"] == [{"kind": "game data", "label": "Recipes for Ender Eye", "graph": graph}, {"kind": "game data", "label": "Rank"}]

    def test_an_answer_with_no_sources_says_so_with_an_empty_list(self, api, monkeypatch):
        import minecorp_agent.app as app_mod
        monkeypatch.setattr(app_mod, "answer_with_sources", lambda q, rid: ("hello", []))
        client, _ = api
        assert ask(client).json()["sources"] == []

    def test_the_agent_gets_the_question_and_the_same_request_id(self, api):
        client, calls = api
        body = ask(client).json()
        (question, request_id), = calls
        assert question == Question("u-1", "Steve", "s-1", "how do I smelt iron?")
        assert request_id == body["requestId"]

    def test_each_request_gets_its_own_id(self, api):
        client, _ = api
        assert ask(client).json()["requestId"] != ask(client).json()["requestId"]

    def test_a_question_of_exactly_500_characters_is_accepted(self, api):
        client, calls = api
        assert ask(client, body={**GOOD, "question": "q" * 500}).status_code == 200

    @pytest.mark.parametrize("change", [
        {"question": ""}, {"question": "q" * 501}, {"playerName": ""}, {"playerName": "n" * 65},
        {"playerUuid": ""}, {"sessionId": ""}, {"sessionId": "s" * 129}])
    def test_invalid_fields_are_rejected_without_running_the_agent(self, api, change):
        client, calls = api
        assert ask(client, body={**GOOD, **change}).status_code == 422
        assert calls == []

    @pytest.mark.parametrize("missing", list(GOOD))
    def test_every_field_is_required(self, api, missing):
        client, calls = api
        body = {k: v for k, v in GOOD.items() if k != missing}
        assert ask(client, body=body).status_code == 422 and calls == []

    def test_a_body_that_is_not_json_is_rejected(self, api):
        client, calls = api
        r = client.post("/ask", content="not json", headers={"X-MineCorp-Key": SECRET, "Content-Type": "application/json"})
        assert r.status_code == 422 and calls == []

    def test_get_is_not_allowed_on_ask(self, api):
        client, _ = api
        assert client.get("/ask", headers={"X-MineCorp-Key": SECRET}).status_code == 405


# --------------------------------------------------------------------------- core.answer()

class TestCore:
    def test_returns_the_agents_answer(self, monkeypatch):
        fake = type("A", (), {"run": lambda self, q, rid, context=(): Result(answer="Smelt it.", status="ok", model="m", ms=1)})()
        monkeypatch.setattr(core, "get_agent", lambda: fake)
        assert core.answer(Question("u", "n", "s", "q"), "r") == "Smelt it."

    def test_start_up_problems_become_a_friendly_message(self, monkeypatch):
        def broken():
            raise RuntimeError("OPENAI_API_KEY is not set")
        monkeypatch.setattr(core, "get_agent", broken)
        text = core.answer(Question("u", "n", "s", "q"), "r")
        assert "not available" in text and "RuntimeError" in text and "OPENAI_API_KEY" not in text

    def test_get_agent_needs_an_openai_key(self, monkeypatch):
        monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(core, "_agent", None)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            core.get_agent()
