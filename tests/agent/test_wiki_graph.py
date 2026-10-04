"""search_wiki and read_section against a real (throwaway) Neo4j database, `minecorpwikiagent`.

The page is loaded with the real loader code and fake, deterministic embeddings; the question is embedded by a fake that
maps a chosen question to the vector of a chosen chunk. So these check the real Cypher, the vector index and the walk to the
section, the neighbours and the tree, but not whether OpenAI embeddings rank well (the evals do that).
Needs Neo4j Enterprise; no jar, no OpenAI.
"""
from __future__ import annotations

import hashlib

import pytest

import load_wiki as lw
from graphdb import empty_test_database
from minecorp_agent import wiki
from minecorp_agent.graph import Graph

pytestmark = pytest.mark.integration

MODEL = "fake-model"
DIMS = 8
BASE = "https://minecraft.wiki/w/Guide"


class Words:
    def count(self, text: str) -> int:
        return len(text.split())


def sentence(n: int) -> str:
    return f"S{n} w w w end."


LONG = " ".join(sentence(i) for i in range(14))
HTML = f"""
<p>Welcome to the guide. Read it all.</p>
<h2 id="Shelter">Shelter</h2><p>{LONG}</p>
<h3 id="Walls">Walls</h3><p>Walls keep mobs out.</p>
<h3 id="Roof">Roof</h3><p>A roof stops spiders.</p>
<h2 id="Food">Food</h2>
<h4 id="Bread">Bread</h4><p>Bread is made from wheat.</p>
<h2 id="Tips">Tips</h2><p>Sleep in a bed.</p>
"""
OTHER = "<h2 id='Tips'>Tips</h2><p>Other page tips.</p>"


def vector(text: str) -> list[float]:
    return [(b + 1) / 256 for b in hashlib.sha256(text.encode()).digest()[:DIMS]]


def load(page_html: str, title: str, db: dict) -> dict:
    g = lw.build(title, 1, page_html, "2026-10-04T12:00:00+00:00", Words(), 20, 5)
    for c in g["chunks"]:
        c["embedding"] = lw.decode_vector(lw.encode_vector(vector(c["embedText"])))
    lw.write(g, MODEL, db["uri"], db["user"], db["password"], db["database"])
    return g


@pytest.fixture(scope="module")
def wikidb():
    yield from empty_test_database("minecorpwikiagent")


@pytest.fixture(scope="module")
def pages(wikidb):
    """Two pages loaded once for the whole module."""
    return {"guide": load(HTML, "Guide", wikidb), "other": load(OTHER, "Other", wikidb)}


@pytest.fixture(scope="module")
def graph(wikidb, pages):
    wikidb["db"].q("CALL db.awaitIndex('chunk_embedding', 60)")
    g = Graph(wikidb["uri"], wikidb["user"], wikidb["password"], wikidb["database"])
    yield g
    g.close()


@pytest.fixture(autouse=True)
def model(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", MODEL)


def ask_like(monkeypatch, chunk: dict) -> str:
    """Make the next question embed to exactly this chunk's vector; returns the question text."""
    question = f"question about {chunk['id']}"
    monkeypatch.setattr(wiki, "embed_query", lambda text: vector(chunk["embedText"]) if text == question else vector(text))
    return question


def chunk_of(pages, section_suffix: str, n: int = 0) -> dict:
    return next(c for c in pages["guide"]["chunks"] if c["id"] == f"{BASE}#{section_suffix}~{n}" or c["id"] == f"{BASE}~{n}" and not section_suffix)


# --------------------------------------------------------------------------- search_wiki

class TestSearchWiki:
    def test_the_best_hit_is_the_chunk_the_question_embeds_to(self, graph, pages, monkeypatch):
        target = chunk_of(pages, "Walls")
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, target)})
        top = result["hits"][0]
        assert top["text"] == "Walls keep mobs out." and top["sectionUrl"] == f"{BASE}#Walls"
        assert top["section"] == "Guide > Shelter > Walls" and top["page"] == "Guide" and top["score"] == pytest.approx(1.0, abs=1e-3)

    @pytest.mark.parametrize("suffix,path", [("Roof", "Guide > Shelter > Roof"), ("Bread", "Guide > Food > Bread"),
                                             ("Tips", "Guide > Tips"), ("Shelter", "Guide > Shelter")])
    def test_the_section_path_follows_the_tree(self, graph, pages, monkeypatch, suffix, path):
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, suffix))})
        assert result["hits"][0]["section"] == path

    def test_the_intro_is_shown_as_the_page(self, graph, pages, monkeypatch):
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, ""))})
        assert result["hits"][0]["section"] == "Guide" and result["hits"][0]["sectionUrl"] == BASE

    def test_two_pages_with_the_same_section_title_stay_separate_hits(self, graph, pages, monkeypatch):
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, "Tips")), "limit": 8})
        tips = [h for h in result["hits"] if h["section"].endswith("Tips")]
        assert {h["page"] for h in tips} == {"Guide", "Other"} and tips[0]["page"] == "Guide"

    def test_hits_are_unique_sections_best_first_and_limited(self, graph, pages, monkeypatch):
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, "Roof")), "limit": 4})
        urls = [h["sectionUrl"] for h in result["hits"]]
        assert len(urls) == len(set(urls)) <= 4
        assert [h["score"] for h in result["hits"]] == sorted((h["score"] for h in result["hits"]), reverse=True)

    def test_chunks_of_a_long_section_come_back_as_one_clean_passage(self, graph, pages, monkeypatch):
        section_chunks = [c for c in pages["guide"]["chunks"] if c["section"] == f"{BASE}#Shelter"]
        assert len(section_chunks) >= 3
        monkeypatch.setattr(wiki, "embed_query", lambda text: [sum(col) / len(col) for col in zip(*(vector(c["embedText"]) for c in section_chunks[:2]))])
        result = wiki.tool_search_wiki(graph, {"question": "anything", "limit": 8})
        shelter = next(h for h in result["hits"] if h["sectionUrl"] == f"{BASE}#Shelter")
        assert shelter["text"].count(sentence(0)) == 1, "the overlap must not be repeated"
        assert shelter["text"].startswith(sentence(0))

    def test_a_question_with_a_different_embedding_model_is_refused(self, graph, pages, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
        monkeypatch.setattr(wiki, "embed_query", lambda text: vector(text))
        result = wiki.tool_search_wiki(graph, {"question": "anything"})
        assert MODEL in result["error"]

    def test_the_search_is_read_only(self, graph, wikidb, pages, monkeypatch):
        before = wikidb["db"].one("MATCH (n) RETURN count(n)")
        wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, "Walls"))})
        assert wikidb["db"].one("MATCH (n) RETURN count(n)") == before


# --------------------------------------------------------------------------- read_section

class TestReadSection:
    def test_the_whole_section_text_is_given_back_exactly(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Shelter"})
        assert result["text"] == LONG, "stitching the chunks must restore the original text"
        assert result["section"] == "Guide > Shelter" and result["page"] == "Guide" and result["sectionUrl"] == f"{BASE}#Shelter"

    def test_subsections_are_listed_in_order(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Shelter"})
        assert result["subsections"] == [{"title": "Walls", "sectionUrl": f"{BASE}#Walls"}, {"title": "Roof", "sectionUrl": f"{BASE}#Roof"}]

    def test_a_section_with_no_text_points_at_its_children(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Food"})
        assert result["text"] == "" and "subsections" in result["note"]
        assert result["subsections"] == [{"title": "Bread", "sectionUrl": f"{BASE}#Bread"}]

    def test_a_leaf_section_has_no_subsections_key(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Walls"})
        assert result["text"] == "Walls keep mobs out." and "subsections" not in result

    def test_a_unique_title_works_in_place_of_the_url(self, graph, pages):
        assert wiki.tool_read_section(graph, {"section": "walls"})["sectionUrl"] == f"{BASE}#Walls"

    def test_a_title_used_on_two_pages_is_ambiguous(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": "Tips"})
        assert "ambiguous" in result["error"]
        assert {c["sectionUrl"] for c in result["candidates"]} == {f"{BASE}#Tips", "https://minecraft.wiki/w/Other#Tips"}
        assert {c["section"] for c in result["candidates"]} == {"Guide > Tips", "Other > Tips"}

    def test_an_unknown_section(self, graph, pages):
        assert "No wiki section matches" in wiki.tool_read_section(graph, {"section": "https://nope"})["error"]

    def test_after_adds_the_passage_that_follows_even_in_the_next_section(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Roof", "after": 1})
        assert result["after"] == [{"section": "Bread", "text": "Bread is made from wheat."}]
        assert "before" not in result

    def test_before_adds_the_passage_that_precedes_in_reading_order(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Roof", "before": 1})
        assert result["before"] == [{"section": "Walls", "text": "Walls keep mobs out."}]

    def test_several_neighbours_come_in_reading_order(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Tips", "before": 3})
        assert [n["section"] for n in result["before"]] == ["Walls", "Roof", "Bread"][-3:]
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Walls", "after": 2})
        assert [n["section"] for n in result["after"]] == ["Roof", "Bread"]

    def test_neighbours_stop_at_the_ends_of_the_page(self, graph, pages):
        assert wiki.tool_read_section(graph, {"section": BASE, "before": 3}).get("before", []) == []
        assert wiki.tool_read_section(graph, {"section": f"{BASE}#Tips", "after": 3}).get("after", []) == []

    def test_neighbours_never_cross_into_another_page(self, graph, pages):
        result = wiki.tool_read_section(graph, {"section": "https://minecraft.wiki/w/Other#Tips", "before": 3, "after": 3})
        assert result["text"] == "Other page tips." and not result.get("before") and not result.get("after")

    def test_a_long_section_is_cut(self, graph, pages, monkeypatch):
        monkeypatch.setattr(wiki, "MAX_SECTION_CHARS", 40)
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Shelter"})
        assert result["truncated"] is True and result["text"].endswith("...")

    @pytest.mark.parametrize("section", ["Shelter", "Walls", "Roof", "Food", "Bread"])
    def test_every_section_of_the_page_can_be_read_by_its_search_hit_url(self, graph, pages, section):
        assert "error" not in wiki.tool_read_section(graph, {"section": f"{BASE}#{section}"})


class TestNotLoaded:
    def test_search_says_the_wiki_is_not_loaded_when_there_is_no_vector_index(self, wikidb, monkeypatch):
        """A database with no wiki at all: the index does not exist, which must be a clear message, not a crash."""
        empty = "minecorpwikiempty"
        driver = wikidb["db"].driver
        driver.execute_query(f"CREATE DATABASE {empty} IF NOT EXISTS WAIT", database_="system")
        try:
            g = Graph(wikidb["uri"], wikidb["user"], wikidb["password"], empty)
            monkeypatch.setattr(wiki, "embed_query", lambda text: vector(text))
            try:
                result = wiki.tool_search_wiki(g, {"question": "anything"})
            finally:
                g.close()
            assert "not been loaded" in result["error"]
        finally:
            driver.execute_query(f"DROP DATABASE {empty} IF EXISTS WAIT", database_="system")
