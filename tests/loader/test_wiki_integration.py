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


def make(html: str = HTML, title: str = "Guide", revision: int = 1, size: int = 20, overlap: int = 5, resolved: dict | None = None) -> dict:
    g = lw.build(title, revision, html, "2026-10-04T12:00:00+00:00", WordTokenizer(), size, overlap)
    lw.apply_links(g, resolved or {})
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


LINKED_HTML = ("<p>Intro with a <a href='/w/Torch'>torch</a>.</p>"
               "<h2 id='Shelter'>Shelter</h2><p>Use <a href='/w/Planks'>planks</a>, a <a href='/w/Torch'>torch</a> "
               "and <a href='/w/Torch'>torches</a>; also <a href='/w/Log'>logs</a>.</p>"
               "<h2 id='Other'>Other</h2><p>Nothing linked here.</p>")


def pages(*titles: str, **redirects: str) -> dict:
    """What the wiki API would say: these pages exist, and the keyword ones (Log="Logs") redirect to another page."""
    out = {t: {"page": t, "target": None} for t in titles}
    out.update({t.replace("_", " "): {"page": t.replace("_", " "), "target": target} for t, target in redirects.items()})
    return out


RESOLVED = pages("Torch", "Planks", "Biome", Log="Logs")


class TestLinks:
    def links(self, db):
        return db.q("MATCH (c:Chunk)-[l:LINKS_TO]->(p:Page) RETURN c.id AS chunk, p.title AS page, p.url AS url, l.count AS count, l.texts AS texts "
                    "ORDER BY c.index, p.title")

    def test_chunks_link_to_the_pages_the_wiki_links_with_count_and_texts(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED, size=50))
        base = "https://minecraft.wiki/w/Guide"
        rows = {(r["chunk"], r["page"]): (r["count"], r["texts"]) for r in self.links(clean)}
        assert rows == {(f"{base}~0", "Torch"): (1, ["torch"]),
                        (f"{base}#Shelter~0", "Planks"): (1, ["planks"]),
                        (f"{base}#Shelter~0", "Torch"): (2, ["torch", "torches"]),
                        (f"{base}#Shelter~0", "Log"): (1, ["logs"])}, "nothing for the chunk with no links"

    def test_a_page_that_is_only_linked_to_is_a_stub(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED))
        stub = clean.one("MATCH (p:Page {title: 'Torch'}) RETURN p {.url, .title, .fetchedAt, .revisionId} AS p")
        assert stub == {"url": "https://minecraft.wiki/w/Torch", "title": "Torch", "fetchedAt": None, "revisionId": None}
        assert clean.one("MATCH (p:Page {title: 'Torch'}) RETURN size([(p)-[:HAS_SECTION]->() | 1])") == 0
        assert clean.one("MATCH (:Wiki)-[:HAS_PAGE]->(p:Page {title: 'Torch'}) RETURN count(p)") == 1, "a stub belongs to the wiki too"

    def test_a_redirect_is_a_page_that_redirects_and_the_link_goes_to_it(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED))
        assert clean.one("MATCH (:Chunk)-[:LINKS_TO]->(:Page {title: 'Log'})-[:REDIRECTS_TO]->(t:Page) RETURN t.title") == "Logs"
        assert clean.one("MATCH (:Chunk)-[:LINKS_TO]->(:Page {title: 'Logs'}) RETURN count(*)") == 0, "nothing links to the target directly"
        assert clean.one("MATCH (p:Page {title: 'Logs'}) RETURN p.url") == "https://minecraft.wiki/w/Logs"

    def test_two_redirect_pages_to_one_page_are_two_pages_with_one_target(self, wiki, write, clean):
        html = "<p>Make <a href='/w/Black_Bed'>black</a> or <a href='/w/Red_Bed'>red</a>.</p>"
        write(make(html, resolved=pages(Black_Bed="Bed", Red_Bed="Bed")))
        assert clean.q("MATCH (p:Page)-[:REDIRECTS_TO]->(:Page {title: 'Bed'}) RETURN p.title AS t ORDER BY t") == [{"t": "Black Bed"}, {"t": "Red Bed"}]
        assert clean.one("MATCH (p:Page {title: 'Bed'}) RETURN count(p)") == 1
        assert clean.q("MATCH (:Chunk)-[l:LINKS_TO]->(p:Page) RETURN p.title AS t, l.texts AS texts ORDER BY t") == [
            {"t": "Black Bed", "texts": ["black"]}, {"t": "Red Bed", "texts": ["red"]}]

    def test_loading_twice_does_not_duplicate_a_redirect(self, wiki, write, clean):
        g = make(LINKED_HTML, resolved=RESOLVED)
        write(g)
        write(g)
        assert clean.one("MATCH (:Page)-[r:REDIRECTS_TO]->(:Page) RETURN count(r)") == 1

    def test_each_linked_page_exists_once_however_often_it_is_linked(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED, size=8, overlap=3))
        assert clean.one("MATCH (p:Page {title: 'Torch'}) RETURN count(p)") == 1
        assert clean.one("MATCH (:Chunk)-[:LINKS_TO]->(:Page {title: 'Torch'}) RETURN count(*)") >= 2

    def test_a_stub_later_loaded_becomes_the_same_node_and_keeps_its_incoming_links(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED))
        stub_id = clean.one("MATCH (p:Page {title: 'Torch'}) RETURN elementId(p)")
        write(make("<p>A torch gives light.</p>", title="Torch", revision=5))
        assert clean.one("MATCH (p:Page {title: 'Torch'}) RETURN count(p)") == 1
        row = clean.one("MATCH (p:Page {title: 'Torch'}) RETURN elementId(p) AS id, p.fetchedAt AS at, p.revisionId AS rev")
        assert row["id"] == stub_id and row["at"] == "2026-10-04T12:00:00+00:00" and row["rev"] == 5
        assert clean.one("MATCH (:Chunk)-[:LINKS_TO]->(:Page {title: 'Torch'}) RETURN count(*)") >= 1
        assert clean.one("MATCH (:Page {title: 'Torch'})-[:HAS_SECTION]->(:Section) RETURN count(*)") == 1

    def test_a_redirect_target_later_loaded_keeps_the_redirects_pointing_at_it(self, wiki, write, clean):
        write(make("<p>Make <a href='/w/Black_Bed'>black</a>.</p>", resolved=pages(Black_Bed="Bed")))
        write(make("<p>A bed lets you sleep.</p>", title="Bed", revision=3))
        assert clean.one("MATCH (:Page {title: 'Black Bed'})-[:REDIRECTS_TO]->(b:Page) RETURN b.fetchedAt IS NOT NULL") is True
        assert clean.one("MATCH (p:Page {title: 'Bed'}) RETURN count(p)") == 1

    def test_loading_a_linked_to_page_that_is_already_loaded_does_not_disturb_it(self, wiki, write, clean):
        write(make("<p>A torch gives light.</p>", title="Torch", revision=5))
        write(make(LINKED_HTML, resolved=RESOLVED))
        row = clean.one("MATCH (p:Page {title: 'Torch'}) RETURN p.revisionId AS rev, p.fetchedAt AS at")
        assert row == {"rev": 5, "at": "2026-10-04T12:00:00+00:00"}
        assert clean.one("MATCH (:Page {title: 'Torch'})-[:HAS_SECTION]->(:Section)<-[:PART_OF]-(c:Chunk) RETURN count(c)") == 1

    def test_reloading_a_page_replaces_its_links_and_does_not_duplicate_them(self, wiki, write, clean):
        g = make(LINKED_HTML, resolved=RESOLVED)
        write(g)
        before = self.links(clean)
        write(g)
        assert self.links(clean) == before
        write(make("<p>No links any more.</p>", resolved=RESOLVED))
        assert self.links(clean) == []
        assert clean.one("MATCH (p:Page {title: 'Torch'}) RETURN count(p)") == 1, "the stub is kept"

    def test_wipe_removes_stubs_and_redirects_too(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED))
        write(make("<p>Fresh.</p>", title="Fresh"), wipe=True)
        assert clean.one("MATCH (p:Page) RETURN count(p)") == 1
        assert clean.one("MATCH ()-[r:REDIRECTS_TO]->() RETURN count(r)") == 0

    def test_stubs_never_appear_as_sections_or_chunks(self, wiki, write, clean):
        write(make(LINKED_HTML, resolved=RESOLVED))
        assert clean.one("MATCH (p:Page) WHERE NOT (p)-[:HAS_SECTION]->() RETURN count(p)") == 4          # Torch, Planks, Log, Logs
        assert clean.one("MATCH (c:Chunk) RETURN count(c)") == 3

    def test_a_page_with_no_links_writes_none(self, wiki, write, clean):
        write(make(HTML))
        assert clean.one("MATCH ()-[l:LINKS_TO]->() RETURN count(l)") == 0
        assert clean.one("MATCH (p:Page) RETURN count(p)") == 1


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
