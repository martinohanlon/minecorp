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
    lw.apply_links(g, {})
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
    return {"guide": load(HTML, "Guide", wikidb), "other": load(OTHER, "Other", wikidb),
            "linker": load("<p>See the <a href='/w/Torch'>torch</a> page.</p>", "Linker", wikidb)}      # Torch is only a stub


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

    def test_every_hit_is_a_wiki_source_with_its_link(self, graph, pages, monkeypatch):
        from minecorp_agent import sources
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, "Walls")), "limit": 3})
        got = sources.sources_for("search_wiki", {}, result)
        assert got[0] == {"kind": "wiki", "label": "Guide > Shelter > Walls", "url": f"{BASE}#Walls", "graph": ["(Chunk)-[PART_OF]->(Section {Walls})"]}
        assert len(got) == len(result["hits"]) and all(s["kind"] == "wiki" and s["url"].startswith("https://minecraft.wiki/w/") for s in got)

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

    def test_a_section_that_was_read_is_a_wiki_source_with_its_link(self, graph, pages):
        from minecorp_agent import sources
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Walls"})
        assert sources.sources_for("read_section", {}, result) == [{"kind": "wiki", "label": "Guide > Shelter > Walls", "url": f"{BASE}#Walls",
                                                                    "graph": ["(Chunk)-[PART_OF]->(Section {Walls})"]}]

    def test_the_pattern_shown_for_a_passage_is_a_path_in_the_wiki_graph(self, graph, pages, monkeypatch):
        from minecorp_agent import sources
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, "Walls")), "limit": 6})
        patterns = [p for _s, ps in sources.patterned("search_wiki", {}, result) for p in ps]
        assert patterns
        for p in patterns:
            assert graph.read(f"MATCH {p.cypher()} RETURN count(*) AS n")[0]["n"] > 0, p.cypher()

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


@pytest.fixture(scope="module")
def linked(wikidb, pages):
    """Game nodes the pages talk about (the chicken is both an item and a mob), then the real link job run over the wiki."""
    from neo4j import GraphDatabase

    import link_wiki as lk
    db = wikidb["db"]
    db.q("UNWIND $rows AS r CREATE (:Item {id: r[0], name: r[1]})", rows=[["minecraft:bread", "Bread"], ["minecraft:wheat", "Wheat"],
                                                                          ["minecraft:torch", "Torch"], ["minecraft:chicken", "Chicken"]])
    db.q("CREATE (:Mob {id: 'minecraft:chicken', name: 'Chicken'})")
    farm = load("<p>Cook a chicken.</p>", "Farm", wikidb)
    with GraphDatabase.driver(wikidb["uri"], auth=(wikidb["user"], wikidb["password"])) as driver:
        with driver.session(database=wikidb["database"]) as session:
            lk.run(session, {"stoplist": set(), "aliases": {}, "pages": {}}, "2026-10-04T12:00:00+00:00", dry_run=False)
    return {"farm": farm}


class TestMentions:
    """What the link job wrote, read back through the tools: the graph says what each passage is about."""

    def hit_for(self, graph, monkeypatch, chunk, section_title=None):
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk)})
        return result["hits"][0]

    def test_a_passage_lists_the_game_nodes_it_is_about(self, graph, pages, linked, monkeypatch):
        top = self.hit_for(graph, monkeypatch, chunk_of(pages, "Bread"))
        assert top["section"] == "Guide > Food > Bread"
        assert {(m["name"], m["kind"]) for m in top["mentions"]} == {("Bread", "Item"), ("Wheat", "Item")}
        assert all("linked" not in m for m in top["mentions"]), "nothing links them: the passage only names them"

    def test_a_link_makes_a_mention_that_says_so(self, graph, pages, linked, monkeypatch):
        top = self.hit_for(graph, monkeypatch, pages["linker"]["chunks"][0])
        assert top["mentions"] == [{"name": "Torch", "kind": "Item", "id": "minecraft:torch", "linked": True}]

    def test_an_ambiguous_word_gives_the_item_and_the_mob(self, graph, pages, linked, monkeypatch):
        top = self.hit_for(graph, monkeypatch, linked["farm"]["chunks"][0])
        assert sorted((m["kind"], m["ambiguous"]) for m in top["mentions"]) == [("Item", True), ("Mob", True)]
        assert {m["id"] for m in top["mentions"]} == {"minecraft:chicken"}

    def test_a_passage_about_nothing_in_the_game_has_no_mentions(self, graph, pages, linked, monkeypatch):
        top = self.hit_for(graph, monkeypatch, chunk_of(pages, "Walls"))
        assert top["text"] == "Walls keep mobs out." and "mentions" not in top

    def test_the_whole_section_lists_them_too(self, graph, pages, linked):
        result = wiki.tool_read_section(graph, {"section": f"{BASE}#Bread"})
        assert {m["name"] for m in result["mentions"]} == {"Bread", "Wheat"} and "ambiguous" in result["note"]

    def test_the_tool_only_reads(self, wikidb, graph, pages, linked, monkeypatch):
        before = wikidb["db"].one("MATCH ()-[m:MENTIONS]->() RETURN count(m)")
        self.hit_for(graph, monkeypatch, chunk_of(pages, "Bread"))
        wiki.tool_read_section(graph, {"section": f"{BASE}#Bread"})
        assert wikidb["db"].one("MATCH ()-[m:MENTIONS]->() RETURN count(m)") == before > 0


class TestStubPages:
    """Pages that are only linked to (no text loaded) must be invisible to the wiki tools."""

    def test_a_stub_exists_in_the_graph(self, graph):
        assert graph.read("MATCH (p:Page {title: 'Torch'}) RETURN p.fetchedAt AS at") == [{"at": None}]

    def test_a_stub_is_never_a_search_hit(self, graph, pages, monkeypatch):
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, chunk_of(pages, "Walls")), "limit": 8})
        assert all(h["page"] != "Torch" for h in result["hits"]) and result["hits"][0]["section"] == "Guide > Shelter > Walls"

    def test_reading_a_stub_by_url_or_title_finds_no_section(self, graph, pages):
        assert "No wiki section matches" in wiki.tool_read_section(graph, {"section": "https://minecraft.wiki/w/Torch"})["error"]
        assert "No wiki section matches" in wiki.tool_read_section(graph, {"section": "Torch"})["error"]

    def test_the_linking_page_itself_is_searchable(self, graph, pages, monkeypatch):
        target = next(c for c in pages["linker"]["chunks"])
        result = wiki.tool_search_wiki(graph, {"question": ask_like(monkeypatch, target)})
        assert result["hits"][0]["page"] == "Linker" and result["hits"][0]["text"] == "See the torch page."


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
