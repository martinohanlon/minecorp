"""Tests for the notes in scripts/chat.py: the sources in a reply, the last five exchanges, and the pages /explain shows.

The pages are the contract for the plugin's /agent-explain book (docs/explain-book.md): the plugin must build the same ones.
No Minecraft, no OpenAI, no database."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import chat  # noqa: E402

GAME = {"kind": "game data", "label": "Recipes for Iron Pickaxe"}
WIKI = {"kind": "wiki", "label": "Tutorial:Shelters > Hole in the wall", "url": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"}
PLAN = {"kind": "plan", "label": "Get to the Nether (draft, not confirmed)"}


class Screen:
    def __init__(self):
        self.lines: list[str] = []
        self.term = chat.Terminal(False, self.lines.append)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def client_with(answer="Smelt it.", sources=None):
    def handler(request):
        body = {"requestId": "abc123", "answer": answer}
        if sources is not None:
            body["sources"] = sources
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def talk(sources, question="how do I smelt iron?", answer="Smelt it in a furnace.", c=None):
    c = c or chat.Chat("Alex", "http://x", "secret-0123456789")
    c.client = client_with(answer, sources)                  # a new reply each time, with the sources given
    c.ask(question)
    return c


# --------------------------------------------------------------------------- the sources in a reply

class TestSourcesInAReply:
    def test_the_sources_come_with_the_answer(self):
        reply = chat.Chat("Alex", "http://x", "s", client=client_with(sources=[GAME, WIKI])).ask("q")
        assert reply.sources == [GAME, WIKI]

    def test_a_reply_without_sources_has_none(self):
        assert chat.Chat("Alex", "http://x", "s", client=client_with()).ask("q").sources == []

    @pytest.mark.parametrize("raw", ["text", 5, {"kind": "wiki"}, [None, 3, "x"], [{"kind": "wiki"}], [{"label": "x"}], [{"kind": 1, "label": "x"}],
                                     [{"kind": "wiki", "label": "  "}]])
    def test_odd_sources_are_dropped_not_fatal(self, raw):
        reply = chat.Chat("Alex", "http://x", "s", client=client_with(sources=raw)).ask("q")
        assert reply.error is None and reply.sources == []

    def test_only_a_web_address_is_kept_as_a_link(self):
        got = chat.clean_sources([{"kind": "wiki", "label": "a", "url": "javascript:alert(1)"}, {"kind": "wiki", "label": "b", "url": "https://minecraft.wiki/w/X"},
                                  {"kind": "wiki", "label": "c", "url": 5}])
        assert got == [{"kind": "wiki", "label": "a"}, {"kind": "wiki", "label": "b", "url": "https://minecraft.wiki/w/X"}, {"kind": "wiki", "label": "c"}]

    def test_direct_mode_takes_text_and_sources_or_just_text(self):
        with_sources = chat.Chat("Alex", direct=lambda q, rid: ("hello", [GAME]))
        assert with_sources.ask("q").sources == [GAME]
        text_only = chat.Chat("Alex", direct=lambda q, rid: "hello")
        reply = text_only.ask("q")
        assert reply.lines == ["hello"] and reply.sources == []

    def test_a_failed_request_has_no_sources_and_is_not_remembered(self):
        c = chat.Chat("Alex", "http://x", "s", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))))
        assert c.ask("q").error and c.history.recent() == []


# --------------------------------------------------------------------------- the last five

class TestHistory:
    def test_each_answered_question_is_kept_oldest_first(self):
        c = talk([GAME], question="one")
        talk([WIKI], question="two", c=c)
        assert [e.question for e in c.history.recent()] == ["one", "two"]
        assert c.history.recent()[1].sources == [WIKI]

    def test_only_the_last_five_are_kept(self):
        c = chat.Chat("Alex", "http://x", "s", client=client_with())
        for n in range(8):
            c.ask(f"question {n}")
        assert [e.question for e in c.history.recent()] == [f"question {n}" for n in range(3, 8)]

    def test_the_limit_is_five(self):
        assert chat.MAX_HISTORY == 5

    def test_a_new_chat_has_no_history(self):
        assert chat.Chat("Alex", "http://x", "s").history.recent() == []

    def test_the_history_hands_out_a_copy(self):
        c = talk([GAME])
        c.history.recent().clear()
        assert len(c.history.recent()) == 1


# --------------------------------------------------------------------------- the pages

class TestBookPages:
    def test_no_exchanges_no_pages(self):
        assert chat.book_pages(chat.History()) == []

    def test_a_contents_page_then_the_answer_and_the_sources(self):
        c = talk([GAME, WIKI], question="how do I smelt iron?", answer="Smelt it in a furnace.")
        pages = chat.book_pages(c.history)
        assert pages[0] == "Agent notes\n\nLast 1 question(s):\n1. how do I smelt iron?"
        assert pages[1] == "1. how do I smelt iron?\n\nSmelt it in a furnace."
        assert pages[2] == "Sources for 1:\n\n- game data: Recipes for Iron Pickaxe\n- wiki: Tutorial:Shelters > Hole in the wall"
        assert len(pages) == 3

    def test_links_are_not_in_the_text_of_a_page(self):
        pages = chat.book_pages(talk([WIKI]).history)
        assert not any("http" in p for p in pages)            # they are clickable in the game, not printed

    def test_an_answer_without_lookups_says_so(self):
        assert chat.NO_SOURCES in chat.book_pages(talk([]).history)[2]

    def test_questions_are_numbered_in_order_and_the_contents_lists_them(self):
        c = talk([GAME], question="first")
        talk([PLAN], question="second", c=c)
        pages = chat.book_pages(c.history)
        assert pages[0] == "Agent notes\n\nLast 2 question(s):\n1. first\n2. second"
        assert pages[1].startswith("1. first") and pages[3].startswith("2. second")
        assert pages[4].startswith("Sources for 2:") and "- plan: Get to the Nether (draft, not confirmed)" in pages[4]

    def test_a_long_question_is_cut_in_the_contents_only(self):
        question = "how do I build a really quite elaborate automatic farm that sorts everything?"
        pages = chat.book_pages(talk([GAME], question=question).history)
        assert f"1. {question[:40]}..." in pages[0] and f"1. {question}" in pages[1]

    def test_no_page_is_longer_than_the_limit(self):
        answer = "\n".join(f"- line {n} " + "word " * 20 for n in range(30))
        pages = chat.book_pages(talk([GAME] * 1, answer=answer).history)
        assert len(pages) > 3 and all(len(p) <= chat.MAX_PAGE_CHARS for p in pages)

    def test_a_long_answer_keeps_every_line(self):
        lines = [f"line {n}" for n in range(200)]
        pages = chat.book_pages(talk([], answer="\n".join(lines)).history)
        joined = "\n".join(pages)
        assert all(f"line {n}" in joined for n in (0, 99, 199))

    def test_many_sources_split_onto_more_pages(self):
        sources = [{"kind": "game data", "label": f"Recipes for Item Number {n} " + "x" * 40} for n in range(30)]
        pages = chat.book_pages(talk(sources).history)
        assert len([p for p in pages if "Recipes for Item Number" in p]) >= 2 and all(len(p) <= chat.MAX_PAGE_CHARS for p in pages)

    def test_a_list_of_exchanges_works_too(self):
        assert chat.book_pages([chat.Exchange("q", ["a"], [])])[1] == "1. q\n\na"


class TestSplitPages:
    def test_short_text_is_one_page(self):
        assert chat.split_pages(["a", "b"]) == ["a\nb"]

    def test_lines_break_between_lines(self):
        assert chat.split_pages(["aaaa", "bbbb", "cccc"], limit=9) == ["aaaa\nbbbb", "cccc"]

    def test_one_line_longer_than_a_page_is_cut_at_a_space(self):
        pages = chat.split_pages(["one two three four five"], limit=10)
        assert pages == ["one two", "three four", "five"]

    def test_a_line_with_no_space_is_cut_at_the_limit(self):
        assert chat.split_pages(["x" * 25], limit=10) == ["x" * 10, "x" * 10, "x" * 5]

    def test_nothing_is_one_empty_page(self):
        assert chat.split_pages([]) == [""]


# --------------------------------------------------------------------------- /explain

class TestExplainCommand:
    def test_nothing_to_explain_before_a_question(self):
        screen = Screen()
        chat.explain(chat.Chat("Alex", "http://x", "s"), screen.term)
        assert screen.lines == [chat.NOTHING_TO_EXPLAIN]

    def test_it_shows_every_page_and_lists_the_links(self):
        screen = Screen()
        chat.explain(talk([GAME, WIKI]), screen.term)
        assert screen.lines[0] == '(stub) book "Agent notes" by MineCorp Agent, 3 page(s)'
        assert screen.text.count("--- page") == 3 and "- wiki: Tutorial:Shelters > Hole in the wall" in screen.text
        assert screen.lines[-1] == "(stub) links in the book (clickable in the game): https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"

    def test_no_links_line_when_there_are_none(self):
        screen = Screen()
        chat.explain(talk([GAME]), screen.term)
        assert "links in the book" not in screen.text

    def test_the_repl_command_works_and_so_does_the_plugins_name(self):
        for command in ("/explain", "/agent-explain", "/EXPLAIN"):
            c = talk([GAME])
            screen = Screen()
            inputs = iter([command, "/quit"])
            chat.repl(c, screen.term, read=lambda prompt: next(inputs))
            assert 'book "Agent notes"' in screen.text, command

    def test_help_mentions_it(self):
        assert "/explain" in chat.HELP

    def test_asking_in_the_repl_then_explaining(self):
        c = chat.Chat("Alex", "http://x", "s", client=client_with("Use a furnace.", [GAME]))
        screen = Screen()
        inputs = iter(["how do I smelt iron?", "/explain", "/quit"])
        chat.repl(c, screen.term, read=lambda prompt: next(inputs))
        assert "Use a furnace." in screen.text and "- game data: Recipes for Iron Pickaxe" in screen.text

    def test_the_explain_option_shows_the_notes_after_one_question(self, monkeypatch, capsys):
        import minecorp_agent.core as core
        monkeypatch.setattr(core, "answer_with_sources", lambda q, rid: ("Mine it.", [GAME]))
        assert chat.main(["--direct", "--explain", "how", "do", "I", "get", "iron?"]) == 0
        out = capsys.readouterr().out
        assert "Mine it." in out and 'book "Agent notes"' in out and "- game data: Recipes for Iron Pickaxe" in out

    def test_a_refused_question_is_not_explained(self, monkeypatch, capsys):
        import minecorp_agent.core as core
        monkeypatch.setattr(core, "answer_with_sources", lambda q, rid: pytest.fail("not asked"))
        assert chat.main(["--direct", "--explain", "x" * 600]) == 1
        assert "book" not in capsys.readouterr().out
