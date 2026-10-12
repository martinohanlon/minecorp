"""Unit tests for agent/minecorp_agent/book.py: the pages of the explain book. The plugin draws exactly these pages (docs/explain-book.md)."""
from __future__ import annotations

import json

import pytest

from minecorp_agent import book
from minecorp_agent.history import Exchange

GAME = {"kind": "game data", "label": "Recipes for Iron Pickaxe"}
WIKI = {"kind": "wiki", "label": "Tutorial:Shelters > Hole in the wall", "url": "https://minecraft.wiki/w/Tutorial:Shelters#Hole_in_the_wall"}
PLAN = {"kind": "plan", "label": "Get to the Nether (draft, not confirmed)"}


def ex(question="how do I smelt iron?", lines=("Smelt it in a furnace.",), sources=(GAME,)):
    return Exchange(question, tuple(lines), tuple(sources))


def texts(page) -> list[str]:
    return [line["text"] for line in page["lines"]]


def page_size(page) -> int:
    return len("\n".join(texts(page)))


class TestBook:
    def test_nothing_asked_no_book(self):
        assert book.build_book([]) is None

    def test_a_contents_page_then_the_answer_and_the_sources(self):
        b = book.build_book([ex(sources=(GAME, WIKI))])
        assert (b["title"], b["author"]) == ("Agent notes", "MineCorp Agent")
        assert [texts(p) for p in b["pages"]] == [
            ["Agent notes", "", "Last 1 question(s):", "1. how do I smelt iron?"],
            ["1. how do I smelt iron?", "", "Smelt it in a furnace."],
            ["Sources for 1:", "", "- game data: Recipes for Iron Pickaxe", "- wiki: Tutorial:Shelters > Hole in the wall"]]

    def test_a_source_with_an_address_is_a_link_line_and_others_are_not(self):
        sources_page = book.build_book([ex(sources=(GAME, WIKI))])["pages"][2]["lines"]
        assert "url" not in sources_page[2] and sources_page[3]["url"] == WIKI["url"]

    def test_the_contents_lines_jump_to_each_question(self):
        b = book.build_book([ex("first"), ex("second"), ex("third")])
        contents = b["pages"][0]["lines"]
        assert [(line["text"], line.get("goto")) for line in contents[3:]] == [("1. first", 2), ("2. second", 4), ("3. third", 6)]
        for line in contents[3:]:                                    # each jump lands on that question's first page
            assert b["pages"][line["goto"] - 1]["lines"][0]["text"] == line["text"]

    def test_the_jumps_still_land_when_an_answer_takes_several_pages(self):
        long = tuple(f"line {n} " + "word " * 20 for n in range(30))
        b = book.build_book([ex("first", lines=long), ex("second")])
        second = b["pages"][0]["lines"][4]
        assert second["goto"] > 4 and b["pages"][second["goto"] - 1]["lines"][0]["text"] == "2. second"

    def test_an_answer_without_lookups_says_so(self):
        assert book.NO_SOURCES in texts(book.build_book([ex(sources=())])["pages"][2])

    def test_a_long_question_is_cut_in_the_contents_only(self):
        q = "how do I build a really quite elaborate automatic farm that sorts everything?"
        b = book.build_book([ex(q)])
        assert texts(b["pages"][0])[3] == f"1. {q[:40]}..." and texts(b["pages"][1])[0] == f"1. {q}"

    def test_a_plan_is_listed_as_a_source(self):
        assert "- plan: Get to the Nether (draft, not confirmed)" in texts(book.build_book([ex(sources=(PLAN,))])["pages"][2])

    def test_no_page_holds_more_than_the_limit(self):
        answer = tuple(f"- line {n} " + "word " * 20 for n in range(30))
        many = tuple({"kind": "game data", "label": f"Recipes for Item {n} " + "x" * 40} for n in range(8))
        b = book.build_book([ex(lines=answer, sources=many) for _ in range(5)])
        assert len(b["pages"]) > 15 and all(page_size(p) <= book.MAX_PAGE_CHARS for p in b["pages"])

    def test_a_long_answer_keeps_every_line(self):
        lines = tuple(f"line {n}" for n in range(200))
        joined = "\n".join(t for p in book.build_book([ex(lines=lines)])["pages"] for t in texts(p))
        assert all(f"line {n}" in joined for n in (0, 99, 199))

    def test_the_book_is_plain_json(self):
        json.dumps(book.build_book([ex(sources=(GAME, WIKI, PLAN))]))

    def test_a_blank_line_costs_a_character_like_any_other(self):
        b = book.build_book([ex(lines=tuple("x" * 99 for _ in range(7)))])
        assert all(page_size(p) <= book.MAX_PAGE_CHARS for p in b["pages"])


class TestPaginate:
    def lines(self, *texts_):
        return [{"text": t} for t in texts_]

    def test_short_text_is_one_page(self):
        assert [[l["text"] for l in p] for p in book.paginate(self.lines("a", "b"))] == [["a", "b"]]

    def test_pages_break_between_lines(self):
        pages = book.paginate(self.lines("aaaa", "bbbb", "cccc"), limit=9)
        assert [[l["text"] for l in p] for p in pages] == [["aaaa", "bbbb"], ["cccc"]]

    def test_the_newlines_count_toward_the_limit(self):
        assert len(book.paginate(self.lines("aaaa", "bbbb"), limit=8)) == 2 and len(book.paginate(self.lines("aaaa", "bbbb"), limit=9)) == 1

    def test_one_line_longer_than_a_page_is_cut_at_a_space(self):
        pages = book.paginate(self.lines("one two three four five"), limit=10)
        assert [[l["text"] for l in p] for p in pages] == [["one two"], ["three four"], ["five"]]

    def test_a_line_with_no_space_is_cut_at_the_limit(self):
        assert [[l["text"] for l in p] for p in book.paginate(self.lines("x" * 25), limit=10)] == [["x" * 10], ["x" * 10], ["x" * 5]]

    def test_a_cut_line_keeps_its_link(self):
        pages = book.paginate([{"text": "word " * 10, "url": "https://x.y/z"}], limit=12)
        assert len(pages) > 1 and all(l["url"] == "https://x.y/z" for p in pages for l in p)

    def test_nothing_is_one_empty_page(self):
        assert book.paginate([]) == [[{"text": ""}]]
