"""The wiki lexical graph written to a real (throwaway) Neo4j database, `minecorpwikitest`, with fake embeddings.

Needs Neo4j Enterprise (a second database) but not the Paper jar, and makes no OpenAI calls. The database is created
fresh for the module and dropped at the end; your real database is never touched.
"""
from __future__ import annotations

import hashlib

import pytest

import load_wiki as lw
from graphdb import empty_test_database

pytestmark = pytest.mark.integration

DIMS = 8
MODEL = "fake-model"


class WordTokenizer:
    def count(self, text: str) -> int:
        return len(text.split())


def sentence(n: int) -> str:
    return f"S{n} w w w end."


HTML = f"""
<p>Intro text.</p>
<h2 id="Shelter">Shelter</h2><p>{' '.join(sentence(i) for i in range(12))}</p>
<h3 id="Walls">Walls</h3><p>Walls keep mobs out.</p>
<h3 id="Roof">Roof</h3><p>A roof stops spiders.</p>
<h2 id="Food">Food</h2>
<h4 id="Bread">Bread</h4><p>Bread is wheat.</p>
"""


def vector(text: str) -> list[float]:
    """A fixed, distinct, non-zero vector per text."""
    return [(b + 1) / 256 for b in hashlib.sha256(text.encode()).digest()[:DIMS]]


def make(html: str = HTML, title: str = "Guide", revision: int = 1, size: int = 20, overlap: int = 5) -> dict:
    g = lw.build(title, revision, html, "2026-10-04T12:00:00+00:00", WordTokenizer(), size, overlap)
    for c in g["chunks"]:
        c["embedding"] = lw.decode_vector(lw.encode_vector(vector(c["embedText"])))
    return g


@pytest.fixture(scope="module")
def wiki():
    yield from empty_test_database("minecorpwikitest")


@pytest.fixture
def write(wiki):
    """Write a built page into the test database."""
    def go(g: dict, wipe: bool = False):
        lw.write(g, MODEL, wiki["uri"], wiki["user"], wiki["password"], wiki["database"], wipe=wipe)
    return go


@pytest.fixture
def clean(wiki):
    """Start each test with no wiki nodes (and one unrelated node that must always survive)."""
    db = wiki["db"]
    db.q("MATCH (n) DETACH DELETE n")
    db.q("CREATE (:Item {id: 'minecraft:stick', name: 'Stick'})")
    return db


class TestShape:
    def test_counts_and_labels(self, wiki, write, clean):
        g = make()
        write(g)
        db = clean
        assert db.one("MATCH (:Wiki) RETURN count(*)") == 1 and db.one("MATCH (:Page) RETURN count(*)") == 1
        assert db.one("MATCH (:Section) RETURN count(*)") == len(g["sections"]) == 6
        assert db.one("MATCH (:Chunk) RETURN count(*)") == len(g["chunks"])

    def test_wiki_and_page_properties(self, wiki, write, clean):
        write(make(revision=77))
        row = clean.one("MATCH (w:Wiki)-[:HAS_PAGE]->(p:Page) RETURN w.url AS wiki, w.name AS name, p.url AS url, p.title AS title, "
                        "p.revisionId AS rev, p.fetchedAt AS at")
        assert row == {"wiki": "https://minecraft.wiki", "name": "Minecraft Wiki", "url": "https://minecraft.wiki/w/Guide",
                       "title": "Guide", "rev": 77, "at": "2026-10-04T12:00:00+00:00"}

    def test_the_section_tree(self, wiki, write, clean):
        write(make())
        top = clean.q("MATCH (:Page)-[:HAS_SECTION]->(s:Section) RETURN s.title AS t, s.order AS o ORDER BY o")
        assert [(r["t"], r["o"]) for r in top] == [("Guide", 0), ("Shelter", 1), ("Food", 4)]
        kids = clean.q("MATCH (p:Section)-[:HAS_SUBSECTION]->(c:Section) RETURN p.title AS parent, c.title AS child ORDER BY c.order")
        assert [(r["parent"], r["child"]) for r in kids] == [("Shelter", "Walls"), ("Shelter", "Roof"), ("Food", "Bread")]
        assert clean.one("MATCH (s:Section) WHERE NOT (s)<-[:HAS_SECTION|HAS_SUBSECTION]-() RETURN count(s)") == 0
        assert clean.one("MATCH (s:Section)<-[:HAS_SECTION|HAS_SUBSECTION]-() WITH s, count(*) AS n WHERE n > 1 RETURN count(s)") == 0

    def test_section_properties(self, wiki, write, clean):
        write(make())
        row = clean.one("MATCH (s:Section {url: 'https://minecraft.wiki/w/Guide#Walls'}) RETURN s.title AS title, s.level AS level, s.order AS o")
        assert row == {"title": "Walls", "level": 3, "o": 2}

    def test_each_chunk_is_part_of_exactly_one_section(self, wiki, write, clean):
        g = make()
        write(g)
        assert clean.one("MATCH (c:Chunk) WHERE size([(c)-[:PART_OF]->() | 1]) <> 1 RETURN count(c)") == 0
        rows = clean.q("MATCH (c:Chunk)-[:PART_OF]->(s:Section) RETURN c.id AS id, s.url AS url")
        assert {r["id"]: r["url"] for r in rows} == {c["id"]: c["section"] for c in g["chunks"]}

    def test_a_long_section_has_several_chunks_with_overlap(self, wiki, write, clean):
        write(make())
        chunks = clean.q("MATCH (c:Chunk)-[:PART_OF]->(:Section {title: 'Shelter'}) RETURN c.text AS text, c.tokens AS tokens ORDER BY c.index")
        assert len(chunks) >= 3 and all(c["tokens"] <= 20 for c in chunks)
        for a, b in zip(chunks, chunks[1:]):
            assert b["text"].startswith(a["text"].split()[-5] + " "), "the next chunk begins with the end of the previous one"

    def test_sections_with_no_text_have_no_chunks(self, wiki, write, clean):
        write(make())
        assert clean.one("MATCH (s:Section {title: 'Food'}) RETURN size([(s)<-[:PART_OF]-() | 1])") == 0

    def test_chunk_properties(self, wiki, write, clean):
        g = make()
        write(g)
        c = next(c for c in g["chunks"] if c["section"].endswith("#Walls"))
        row = clean.one("MATCH (c:Chunk {id: $id}) RETURN c.text AS text, c.tokens AS tokens, c.index AS idx, c.textHash AS h, "
                        "c.embeddingModel AS model, size(c.embedding) AS dims", id=c["id"])
        assert row == {"text": "Walls keep mobs out.", "tokens": 4, "idx": c["index"], "h": c["textHash"], "model": MODEL, "dims": DIMS}

    def test_the_stored_text_has_no_heading_path(self, wiki, write, clean):
        write(make())
        assert clean.one("MATCH (c:Chunk) WHERE c.text CONTAINS ' > ' RETURN count(c)") == 0

    def test_the_embedding_is_stored_as_given(self, wiki, write, clean):
        g = make()
        write(g)
        c = g["chunks"][0]
        stored = clean.one("MATCH (c:Chunk {id: $id}) RETURN c.embedding", id=c["id"])
        assert stored == pytest.approx(c["embedding"], abs=1e-6)


class TestNextChain:
    def test_one_unbroken_chain_in_reading_order(self, wiki, write, clean):
        g = make()
        write(g)
        assert clean.one("MATCH ()-[r:NEXT]->() RETURN count(r)") == len(g["chunks"]) - 1
        path = clean.one("MATCH p = (a:Chunk {index: 0})-[:NEXT*]->(z:Chunk) WHERE NOT (z)-[:NEXT]->() "
                         "RETURN [n IN nodes(p) | n.index]")
        assert path == list(range(len(g["chunks"])))

    def test_the_chain_crosses_section_boundaries(self, wiki, write, clean):
        write(make())
        assert clean.one("MATCH (a:Chunk)-[:NEXT]->(b:Chunk) MATCH (a)-[:PART_OF]->(x), (b)-[:PART_OF]->(y) "
                         "WHERE x <> y RETURN count(*)") >= 3

    def test_every_chunk_has_at_most_one_next_and_one_previous(self, wiki, write, clean):
        write(make())
        assert clean.one("MATCH (c:Chunk) WHERE size([(c)-[:NEXT]->() | 1]) > 1 OR size([(c)<-[:NEXT]-() | 1]) > 1 RETURN count(c)") == 0


class TestIndexesAndConstraints:
    def test_uniqueness_constraints(self, wiki, write, clean):
        write(make())
        rows = clean.q("SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties WHERE type CONTAINS 'UNIQUENESS' "
                       "RETURN labelsOrTypes[0] AS label, properties[0] AS prop")
        assert {(r["label"], r["prop"]) for r in rows} >= {("Wiki", "url"), ("Page", "url"), ("Section", "url"), ("Chunk", "id")}

    def test_the_vector_index_exists_with_the_right_shape(self, wiki, write, clean):
        write(make())
        row = clean.one("SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options WHERE name = 'chunk_embedding' "
                        "RETURN type, labelsOrTypes AS labels, properties AS props, options.indexConfig AS cfg")
        assert row["type"] == "VECTOR" and row["labels"] == ["Chunk"] and row["props"] == ["embedding"]
        assert row["cfg"]["vector.dimensions"] == DIMS and row["cfg"]["vector.similarity_function"] == "COSINE"

    def test_a_vector_search_finds_the_chunk_whose_embedding_it_is_given(self, wiki, write, clean):
        g = make()
        write(g)
        clean.q("CALL db.awaitIndex('chunk_embedding', 60)")
        target = g["chunks"][3]
        hits = clean.q("MATCH (c:Chunk) SEARCH c IN (VECTOR INDEX chunk_embedding FOR $v LIMIT 3) SCORE AS score "
                       "RETURN c.id AS id, score ORDER BY score DESC", v=target["embedding"])
        assert hits[0]["id"] == target["id"] and hits[0]["score"] == pytest.approx(1.0, abs=1e-3)

    def test_a_hit_can_walk_to_its_section_and_neighbours(self, wiki, write, clean):
        g = make()
        write(g)
        walls = next(c for c in g["chunks"] if c["section"].endswith("#Walls"))
        row = clean.one("MATCH (c:Chunk {id: $id})-[:PART_OF]->(s:Section) OPTIONAL MATCH (c)-[:NEXT]->(n:Chunk) "
                        "RETURN s.title AS section, n.index AS next", id=walls["id"])
        assert row == {"section": "Walls", "next": walls["index"] + 1}


class TestReloading:
    def test_loading_the_same_page_twice_gives_the_same_graph(self, wiki, write, clean):
        g = make()
        write(g)
        before = clean.q("MATCH (n) WHERE n:Wiki OR n:Page OR n:Section OR n:Chunk RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l")
        write(g)
        after = clean.q("MATCH (n) WHERE n:Wiki OR n:Page OR n:Section OR n:Chunk RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l")
        assert before == after
        assert clean.one("MATCH ()-[r:NEXT]->() RETURN count(r)") == len(g["chunks"]) - 1

    def test_a_changed_page_replaces_its_sections_and_chunks(self, wiki, write, clean):
        write(make())
        new_html = "<p>New intro.</p><h2 id='Only'>Only</h2><p>One section now.</p>"
        write(make(new_html, revision=2))
        assert clean.q("MATCH (s:Section) RETURN s.title AS t ORDER BY s.order") == [{"t": "Guide"}, {"t": "Only"}]
        assert clean.one("MATCH (c:Chunk) RETURN count(c)") == 2
        assert clean.one("MATCH (p:Page) RETURN p.revisionId") == 2 and clean.one("MATCH (p:Page) RETURN count(p)") == 1

    def test_a_second_page_leaves_the_first_alone(self, wiki, write, clean):
        first, second = make(), make("<p>Other page.</p>", title="Other")
        write(first)
        write(second)
        assert clean.one("MATCH (:Page) RETURN count(*)") == 2 and clean.one("MATCH (:Wiki) RETURN count(*)") == 1
        assert clean.one("MATCH (:Chunk) RETURN count(*)") == len(first["chunks"]) + len(second["chunks"])
        assert clean.one("MATCH (:Page {title: 'Guide'})-[:HAS_SECTION|HAS_SUBSECTION*]->(:Section)<-[:PART_OF]-(c:Chunk) "
                         "RETURN count(DISTINCT c)") == len(first["chunks"])

    def test_pages_have_separate_next_chains(self, wiki, write, clean):
        first, second = make(), make("<p>Other page one. Other page two.</p>", title="Other")
        write(first)
        write(second)
        assert len(second["chunks"]) == 1
        assert clean.one("MATCH ()-[r:NEXT]->() RETURN count(r)") == len(first["chunks"]) - 1       # the other page has one chunk, so no link
        assert clean.one("MATCH (a:Chunk)-[:NEXT]->(b:Chunk) WHERE a.id STARTS WITH 'https://minecraft.wiki/w/Other' "
                         "OR b.id STARTS WITH 'https://minecraft.wiki/w/Other' RETURN count(*)") == 0

    def test_wipe_removes_wiki_nodes_and_nothing_else(self, wiki, write, clean):
        write(make())
        write(make("<p>Other.</p>", title="Other"))
        write(make(), wipe=True)
        assert clean.one("MATCH (:Page) RETURN count(*)") == 1
        assert clean.one("MATCH (i:Item {id: 'minecraft:stick'}) RETURN count(i)") == 1, "wipe must not touch the game graph"

    def test_without_wipe_unrelated_nodes_survive_a_reload(self, wiki, write, clean):
        write(make())
        write(make(revision=3))
        assert clean.one("MATCH (i:Item) RETURN count(i)") == 1

    def test_an_empty_page_loads_without_error(self, wiki, write, clean):
        write(make("<div></div>", title="Empty"))
        assert clean.one("MATCH (:Page {title: 'Empty'}) RETURN count(*)") == 1
        assert clean.one("MATCH (:Chunk) RETURN count(*)") == 0
