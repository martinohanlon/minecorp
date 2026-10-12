"""Tests for /explain in scripts/chat.py: the stub asks the service for the book, as the plugin does, and shows its pages.

The service keeps the history and builds the pages (tests/agent/test_history.py, test_book.py, test_explain_api.py); the stub only asks
and draws. No Minecraft, no OpenAI, no database."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import chat  # noqa: E402

SECRET = "stub-explain-secret-0123456789"
URL = "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"
BOOK = {"title": "Agent notes", "author": "MineCorp Agent", "pages": [
    {"lines": [{"text": "Agent notes"}, {"text": ""}, {"text": "Last 1 question(s):"}, {"text": "1. how do I smelt iron?", "goto": 2}]},
    {"lines": [{"text": "1. how do I smelt iron?"}, {"text": ""}, {"text": "Smelt it in a furnace."}]},
    {"lines": [{"text": "Sources for 1:"}, {"text": ""}, {"text": "- game data: Recipes for Iron Pickaxe"},
               {"text": "- wiki: Tutorial:Shelters > Hole in the wall", "url": URL}]}]}
EMPTY = {"title": "Agent notes", "author": "MineCorp Agent", "pages": []}


class Screen:
    def __init__(self):
        self.lines: list[str] = []
        self.term = chat.Terminal(False, self.lines.append)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def service(book=BOOK, status=200):
    """A mock service that records what it was sent and answers /explain with the book."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/explain":
            return httpx.Response(status, text=book) if isinstance(book, str) else httpx.Response(status, json=book)
        return httpx.Response(200, json={"requestId": "r1", "answer": "hello", "sources": [{"kind": "game data", "label": "x"}]})
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def chat_for(client, name="Alex"):
    return chat.Chat(name, "http://svc", SECRET, client=client)


# --------------------------------------------------------------------------- asking the service

class TestAskingForTheBook:
    def test_it_posts_the_players_uuid_with_the_secret_to_explain(self):
        client, seen = service()
        book, error = chat_for(client).explain()
        (request,) = seen
        assert error is None and request.method == "POST" and request.url.path == "/explain"
        assert request.headers["X-MineCorp-Key"] == SECRET
        assert json.loads(request.content) == {"playerUuid": chat.player_uuid("Alex")}

    def test_the_book_comes_back_with_its_pages_links_and_jumps(self):
        book, _ = chat_for(service()[0]).explain()
        assert (book["title"], book["author"], len(book["pages"])) == ("Agent notes", "MineCorp Agent", 3)
        assert book["pages"][0][3] == {"text": "1. how do I smelt iron?", "goto": 2}
        assert book["pages"][2][3]["url"] == URL

    def test_another_player_is_another_uuid(self):
        client, seen = service()
        chat_for(client, "Sam").explain()
        assert json.loads(seen[0].content)["playerUuid"] == chat.player_uuid("Sam") != chat.player_uuid("Alex")

    def test_an_empty_book_has_no_pages(self):
        book, error = chat_for(service(EMPTY)[0]).explain()
        assert error is None and book["pages"] == []

    @pytest.mark.parametrize("status", [401, 422, 500])
    def test_a_refusal_or_failure_is_a_reason_not_an_exception(self, status):
        book, error = chat_for(service({"detail": "no"}, status)[0]).explain()
        assert book is None and error.startswith(f"HTTP {status}")

    @pytest.mark.parametrize("raw", ["not json at all", [1, 2], {"title": "x"}, {"pages": "x"}, "null"])
    def test_a_reply_that_is_not_a_book_is_a_reason(self, raw):
        book, error = chat_for(service(raw)[0]).explain()
        assert book is None and error

    def test_a_service_that_cannot_be_reached_is_a_reason(self):
        def refuse(request):
            raise httpx.ConnectError("refused")
        book, error = chat_for(httpx.Client(transport=httpx.MockTransport(refuse))).explain()
        assert book is None and "ConnectError" in error

    def test_the_stub_keeps_no_history_of_its_own(self):
        c = chat_for(service()[0])
        c.ask("hello")
        assert not hasattr(c, "history")

    def test_a_reply_with_sources_is_still_just_the_answer(self):
        reply = chat_for(service()[0]).ask("hello")
        assert reply.error is None and reply.lines == ["hello"] and not hasattr(reply, "sources")


class TestDirect:
    def test_it_asks_the_function_it_was_given_for_the_players_book(self):
        asked = []
        c = chat.Chat("Alex", direct=lambda q, rid: ("hi", []), direct_explain=lambda uuid: asked.append(uuid) or BOOK)
        book, error = c.explain()
        assert error is None and asked == [chat.player_uuid("Alex")] and len(book["pages"]) == 3

    def test_nothing_to_explain_is_an_empty_book(self):
        book, _ = chat.Chat("Alex", direct=lambda q, rid: "hi", direct_explain=lambda uuid: None).explain()
        assert book["pages"] == []

    def test_a_failure_inside_is_a_reason(self):
        def boom(uuid):
            raise RuntimeError("no graph")
        book, error = chat.Chat("Alex", direct=lambda q, rid: "hi", direct_explain=boom).explain()
        assert book is None and "RuntimeError" in error

    def test_direct_answers_may_be_text_or_text_and_sources(self):
        assert chat.Chat("Alex", direct=lambda q, rid: "plain").ask("q").lines == ["plain"]
        assert chat.Chat("Alex", direct=lambda q, rid: ("with sources", [{"kind": "wiki", "label": "x"}])).ask("q").lines == ["with sources"]


# --------------------------------------------------------------------------- cleaning what came back

class TestCleanBook:
    def test_a_good_book_is_kept(self):
        assert chat.clean_book(BOOK)["pages"][2][3] == {"text": "- wiki: Tutorial:Shelters > Hole in the wall", "url": URL}

    def test_only_a_web_address_is_kept_as_a_link(self):
        book = chat.clean_book({"pages": [{"lines": [{"text": "a", "url": "javascript:alert(1)"}, {"text": "b", "url": "file:///etc/passwd"},
                                                      {"text": "c", "url": "http://x.y"}, {"text": "d", "url": 5}]}]})
        assert book["pages"][0] == [{"text": "a"}, {"text": "b"}, {"text": "c", "url": "http://x.y"}, {"text": "d"}]

    @pytest.mark.parametrize("goto,kept", [(2, True), (1, True), (0, False), (-3, False), ("2", False), (True, False), (None, False), (2.5, False)])
    def test_a_jump_is_a_page_number_from_one(self, goto, kept):
        line = chat.clean_book({"pages": [{"lines": [{"text": "a", "goto": goto}]}]})["pages"][0][0]
        assert ("goto" in line) is kept

    def test_odd_lines_and_pages_are_dropped_not_fatal(self):
        book = chat.clean_book({"pages": [{"lines": [None, 3, {"x": 1}, {"text": 5}, {"text": "ok"}]}, "page", {"lines": "x"}, {}]})
        assert book["pages"] == [[{"text": "ok"}], [], [], []]

    @pytest.mark.parametrize("raw", [None, "x", [], {}, {"pages": None}, {"pages": {}}])
    def test_anything_that_is_not_a_book_is_none(self, raw):
        assert chat.clean_book(raw) is None

    def test_a_missing_title_and_author_are_empty_strings(self):
        assert chat.clean_book({"pages": []}) == {"title": "", "author": "", "pages": []}


# --------------------------------------------------------------------------- showing it

class TestShowing:
    def test_every_page_is_shown_with_its_jumps_and_links(self):
        screen = Screen()
        chat.explain(chat_for(service()[0]), screen.term)
        assert screen.lines[0] == '(stub) book "Agent notes" by MineCorp Agent, 3 page(s)'
        assert screen.text.count("--- page") == 3
        assert "  1. how do I smelt iron?   [jumps to page 2]" in screen.lines
        assert "  - wiki: Tutorial:Shelters > Hole in the wall" in screen.lines and f"    link: {URL}" in screen.lines
        assert "  Smelt it in a furnace." in screen.lines

    def test_no_link_line_for_a_line_without_one(self):
        screen = Screen()
        chat.explain(chat_for(service()[0]), screen.term)
        assert screen.text.count("link:") == 1

    def test_nothing_to_explain_before_a_question(self):
        screen = Screen()
        chat.explain(chat_for(service(EMPTY)[0]), screen.term)
        assert screen.lines == [chat.NOTHING_TO_EXPLAIN]

    def test_a_failure_is_shown_as_the_player_would_see_it_with_the_reason_in_grey(self):
        screen = Screen()
        chat.explain(chat_for(service({"detail": "unauthorized"}, 401)[0]), screen.term)
        assert screen.lines[0] == chat.UNAVAILABLE and screen.lines[1].startswith("(stub) HTTP 401")

    @pytest.mark.parametrize("command", ["/explain", "/agent-explain", "/EXPLAIN"])
    def test_the_chat_command_works_under_both_names(self, command):
        screen = Screen()
        inputs = iter([command, "/quit"])
        chat.repl(chat_for(service()[0]), screen.term, read=lambda prompt: next(inputs))
        assert 'book "Agent notes"' in screen.text

    def test_help_mentions_it(self):
        assert "/explain" in chat.HELP


class TestCommandLine:
    def test_the_explain_option_shows_the_book_after_one_question(self, monkeypatch, capsys):
        import minecorp_agent.core as core
        monkeypatch.setattr(core, "answer_with_sources", lambda q, rid: ("Mine it.", []))
        monkeypatch.setattr(core, "explain", lambda uuid: BOOK)
        assert chat.main(["--direct", "--explain", "how", "do", "I", "get", "iron?"]) == 0
        out = capsys.readouterr().out
        assert "Mine it." in out and 'book "Agent notes"' in out and f"link: {URL}" in out

    def test_a_refused_question_is_not_explained(self, monkeypatch, capsys):
        import minecorp_agent.core as core
        monkeypatch.setattr(core, "answer_with_sources", lambda q, rid: pytest.fail("not asked"))
        assert chat.main(["--direct", "--explain", "x" * 600]) == 1
        assert "book" not in capsys.readouterr().out

    def test_without_the_option_no_book_is_asked_for(self, monkeypatch, capsys):
        import minecorp_agent.core as core
        monkeypatch.setattr(core, "answer_with_sources", lambda q, rid: ("Mine it.", []))
        monkeypatch.setattr(core, "explain", lambda uuid: pytest.fail("not asked for"))
        assert chat.main(["--direct", "how", "do", "I", "get", "iron?"]) == 0
