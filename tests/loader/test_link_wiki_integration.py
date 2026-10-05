"""The link job against a real (throwaway) Neo4j database, `minecorplinktest`.

A small game graph is made here (including `minecraft:chicken` as both an Item and a Mob, and the torch as both an Item and a
Block), wiki pages are loaded with the real loader (fake embeddings), and `link_wiki` is run on them. The questions are graph
questions: which nodes does a page's title name, which nodes does a chunk point at by following its links and the redirects
between pages, and what does a name in the text mean. Needs Neo4j Enterprise; no jar, no OpenAI.
"""
from __future__ import annotations

import hashlib

import pytest
from neo4j import GraphDatabase

import link_wiki as lk
import load_wiki as lw
from graphdb import empty_test_database

pytestmark = pytest.mark.integration

NOW = "2026-10-04T12:00:00+00:00"
BASE = "https://minecraft.wiki/w/Guide"
EMPTY = {"stoplist": set(), "aliases": {}, "pages": {}}

ITEMS = [("minecraft:torch", "Torch", ""), ("minecraft:stick", "Stick", ""), ("minecraft:stone", "Stone", ""),
         ("minecraft:stone_pickaxe", "Stone Pickaxe", ""), ("minecraft:iron_ingot", "Iron Ingot", ""),
         ("minecraft:crafting_table", "Crafting Table", "Station"), ("minecraft:black_bed", "Black Bed", ""), ("minecraft:red_bed", "Red Bed", ""),
         ("minecraft:dirt", "Dirt", ""), ("#minecraft:dirt", "Dirt", "ItemClass"), ("minecraft:arrow", "Arrow", ""), ("#minecraft:arrows", "Arrow", "ItemClass"),
         ("minecraft:chicken", "Chicken", ""), ("minecraft:wheat", "Wheat", ""), ("#minecraft:planks", "Planks", "ItemClass"),
         ("#minecraft:beds", "Bed", "ItemClass"), ("#minecraft:logs", "Logs", "ItemClass"), ("#minecraft:walls", "Wall", "ItemClass"),
         ("#minecraft:axes", "Axe", "ItemClass"), ("minecraft:berry", "Berry", ""), ("minecraft:berries", "Berries", ""),
         ("#minecraft:leaves", "Leaves", "ItemClass")]
MOBS = [("minecraft:chicken", "Chicken"), ("minecraft:sheep", "Sheep"), ("minecraft:spider", "Spider")]
BLOCKS = [("minecraft:torch", "Torch"), ("minecraft:wheat", "Wheat"), ("minecraft:powder_snow", "Powder Snow")]
BIOMES = [("minecraft:desert", "Desert"), ("minecraft:plains", "Plains")]
STRUCTURES = [("minecraft:igloo", "Igloo"), ("minecraft:village_plains", "Village Plains")]


class Words:
    def count(self, text: str) -> int:
        return len(text.split())


def vector(text: str) -> list[float]:
    return [(b + 1) / 256 for b in hashlib.sha256(text.encode()).digest()[:8]]


def pages(*titles: str, **redirects: str) -> dict:
    """What the wiki API would say: these pages exist, and the keyword ones (Black_Bed="Bed") redirect to another page."""
    out = {t: {"page": t, "target": None} for t in titles}
    out.update({t.replace("_", " "): {"page": t.replace("_", " "), "target": target} for t, target in redirects.items()})
    return out


@pytest.fixture(scope="module")
def linkdb():
    yield from empty_test_database("minecorplinktest")


@pytest.fixture
def session(linkdb):
    with GraphDatabase.driver(linkdb["uri"], auth=(linkdb["user"], linkdb["password"])) as driver:
        with driver.session(database=linkdb["database"]) as s:
            yield s


@pytest.fixture
def world(linkdb):
    """A fresh game graph, and a helper to load wiki pages into it."""
    db = linkdb["db"]
    db.q("MATCH (n) DETACH DELETE n")
    db.q("UNWIND $rows AS r CREATE (:Item {id: r[0], name: r[1]})", rows=[[i, n] for i, n, _ in ITEMS])
    for mark in ("ItemClass", "Station"):
        db.q(f"MATCH (n:Item) WHERE n.id IN $ids SET n:{mark}", ids=[i for i, _, m in ITEMS if m == mark])
    db.q("UNWIND $rows AS r CREATE (:Mob {id: r[0], name: r[1]})", rows=[list(m) for m in MOBS])
    db.q("UNWIND $rows AS r CREATE (:Block {id: r[0], name: r[1]})", rows=[list(b) for b in BLOCKS])
    db.q("UNWIND $rows AS r CREATE (:Biome {id: r[0], name: r[1]})", rows=[list(b) for b in BIOMES])
    db.q("UNWIND $rows AS r CREATE (:Structure {id: r[0], name: r[1]})", rows=[list(s) for s in STRUCTURES])
    db.q("CREATE (:BiomeClass {id: '#minecraft:is_overworld', name: 'Is Overworld'}), (:MobClass {id: '#minecraft:undead', name: 'Undead'})")

    class World:
        pass

    w = World()
    w.db = db

    def load(html: str, title: str = "Guide", resolved: dict | None = None, revision: int = 1, size: int = 200, overlap: int = 10) -> dict:
        g = lw.build(title, revision, html, NOW, Words(), size, overlap)
        lw.apply_links(g, resolved or {})
        for c in g["chunks"]:
            c["embedding"] = lw.decode_vector(lw.encode_vector(vector(c["embedText"])))
        lw.write(g, "fake", linkdb["uri"], linkdb["user"], linkdb["password"], linkdb["database"])
        return g

    def text(sentence: str, resolved: dict | None = None) -> dict:
        return load(f"<p>{sentence}</p>", resolved=resolved)

    def stubs(*titles: str) -> None:
        db.q("UNWIND $t AS t CREATE (:Page {url: 'https://minecraft.wiki/w/' + replace(t, ' ', '_'), title: t})", t=list(titles))

    w.load, w.text, w.stubs = load, text, stubs
    return w


def link(session, **config) -> dict:
    return lk.run(session, {**EMPTY, **config}, NOW, dry_run=False)


def key(labels: list[str], id_: str) -> str:
    """'Item:minecraft:chicken', 'Mob:minecraft:chicken', 'Category:#minecraft:dirt': the node says what it is."""
    return f"{lk.kind_of(labels)}:{id_}"


def about(db, page: str) -> dict[str, dict]:
    rows = db.q("MATCH (:Page {title: $t})-[a:ABOUT]->(n) RETURN labels(n) AS labels, n.id AS id, a {.*} AS a", t=page)
    return {key(r["labels"], r["id"]): r["a"] for r in rows}


def mentions(db) -> dict[tuple[str, str], dict]:
    """{(chunk, node): relationship properties}; the chunk is its id minus the page url ('~0', '#Shelter~0')."""
    rows = db.q("MATCH (c:Chunk)-[m:MENTIONS]->(n) RETURN c.id AS chunk, labels(n) AS labels, n.id AS id, m {.*} AS m")
    return {(r["chunk"].removeprefix(BASE), key(r["labels"], r["id"])): r["m"] for r in rows}


def nodes_mentioned(db, chunk: str = "~0") -> set[str]:
    return {k for (c, k) in mentions(db) if c == chunk}


# --------------------------------------------------------------------------- pages are about nodes

class TestAbout:
    @pytest.mark.parametrize("title,expected", [
        ("Torch", {"Item:minecraft:torch"}),                                                  # the torch block twins the item: one target
        ("torch", {"Item:minecraft:torch"}),
        ("Wheat", {"Item:minecraft:wheat"}),
        ("Log", {"Category:#minecraft:logs"}), ("Logs", {"Category:#minecraft:logs"}),
        ("Leave", {"Category:#minecraft:leaves"}), ("Leaves", {"Category:#minecraft:leaves"}),
        ("Sticks", {"Item:minecraft:stick"}), ("Walls", {"Category:#minecraft:walls"}),
        ("Crafting Table", {"Station:minecraft:crafting_table"}),
        ("Black Bed", {"Item:minecraft:black_bed"}), ("Bed", {"Category:#minecraft:beds"}),
        ("Sheep", {"Mob:minecraft:sheep"}), ("Powder Snow", {"Block:minecraft:powder_snow"}),
        ("Berries", {"Item:minecraft:berries"}),                                              # an exact name beats the plural of Berry
        ("Berry", {"Item:minecraft:berry"}),
        ("Biome", set())])
    def test_a_page_is_about_the_nodes_its_title_names(self, world, session, title, expected):
        world.stubs(title)
        link(session)
        assert set(about(world.db, title)) == expected

    @pytest.mark.parametrize("title,expected", [
        ("Chicken", {"Item:minecraft:chicken", "Mob:minecraft:chicken"}),
        ("Dirt", {"Item:minecraft:dirt", "Category:#minecraft:dirt"}),
        ("Arrow", {"Item:minecraft:arrow", "Category:#minecraft:arrows"}),
        ("Arrows", {"Item:minecraft:arrow", "Category:#minecraft:arrows"})])
    def test_an_ambiguous_title_is_linked_to_every_candidate(self, world, session, title, expected):
        world.stubs(title)
        link(session)
        found = about(world.db, title)
        assert set(found) == expected
        assert all(a["ambiguous"] is True and a["candidates"] == 2 for a in found.values())

    def test_an_unambiguous_title_says_so(self, world, session):
        world.stubs("Torch", "Log")
        link(session)
        torch = about(world.db, "Torch")["Item:minecraft:torch"]
        assert (torch["method"], torch["match"], torch["ambiguous"], torch["candidates"], torch["status"], torch["origin"], torch["extractedAt"]) == (
            "title", "exact", False, 1, "candidate", "link_wiki", NOW)
        log = about(world.db, "Log")["Category:#minecraft:logs"]
        assert (log["match"], log["ambiguous"]) == ("variant", False)

    def test_no_label_or_id_is_copied_onto_the_relationship(self, world, session):
        world.stubs("Chicken")
        link(session)
        props = about(world.db, "Chicken")["Mob:minecraft:chicken"]
        assert set(props) == {"origin", "method", "match", "ambiguous", "candidates", "status", "extractedAt"}

    def test_loaded_pages_and_stubs_are_both_considered(self, world, session):
        world.load("<p>Text.</p>", title="Torch")
        world.stubs("Stick")
        link(session)
        assert "Item:minecraft:torch" in about(world.db, "Torch") and "Item:minecraft:stick" in about(world.db, "Stick")

    def test_a_page_with_no_node_of_that_name_has_no_relationship(self, world, session):
        world.stubs("Biome", "Java Edition")
        link(session)
        assert world.db.one("MATCH (:Page)-[a:ABOUT]->() RETURN count(a)") == 0


class TestAboutBiomesAndStructures:
    """The world graph's biomes and structures are named too; its classes are not."""

    @pytest.mark.parametrize("title,expected", [("Desert", {"Biome:minecraft:desert"}), ("Plains", {"Biome:minecraft:plains"}),
                                                ("Igloo", {"Structure:minecraft:igloo"}), ("Igloos", {"Structure:minecraft:igloo"}),
                                                ("Village Plains", {"Structure:minecraft:village_plains"})])
    def test_a_page_is_about_a_biome_or_a_structure(self, world, session, title, expected):
        world.stubs(title)
        link(session)
        assert set(about(world.db, title)) == expected

    @pytest.mark.parametrize("title", ["Is Overworld", "Undead"])
    def test_a_class_is_not_something_a_page_is_about(self, world, session, title):
        world.stubs(title)
        link(session)
        assert about(world.db, title) == {}

    def test_text_names_a_biome_and_a_structure(self, world, session):
        world.text("In the desert we found an igloo and a village.")
        link(session)
        assert nodes_mentioned(world.db) == {"Biome:minecraft:desert", "Structure:minecraft:igloo"}

    def test_a_class_is_never_mentioned(self, world, session):
        world.text("The undead roam the overworld.")
        link(session)
        assert nodes_mentioned(world.db) == set()

    def test_a_fix_can_name_a_biome(self, world, session):
        world.stubs("Dry Lands")
        link(session, pages={"dry lands": [("Biome", "minecraft:desert")]})
        assert set(about(world.db, "Dry Lands")) == {"Biome:minecraft:desert"}

    def test_a_link_to_a_biome_page_is_a_linked_mention_of_it(self, world, session):
        world.text("We crossed the <a href='/w/Desert'>sands</a>.", pages("Desert"))
        link(session)
        m = mentions(world.db)[("~0", "Biome:minecraft:desert")]
        assert (m["method"], m["linkCount"], m["surfaces"]) == ("link", 1, ["sands"])


class TestAboutConfig:
    def test_a_fix_names_an_item_by_id(self, world, session):
        world.stubs("Wood")
        link(session, pages={"wood": [("Item", "#minecraft:logs")]})
        found = about(world.db, "Wood")
        assert set(found) == {"Category:#minecraft:logs"}
        a = found["Category:#minecraft:logs"]
        assert (a["method"], a["status"], a["match"], a["ambiguous"]) == ("override", "confirmed", "exact", False)

    def test_a_fix_can_choose_the_mob(self, world, session):
        world.stubs("Chicken")
        link(session, pages={"chicken": [("Mob", "minecraft:chicken")]})
        assert set(about(world.db, "Chicken")) == {"Mob:minecraft:chicken"}

    def test_a_fix_replaces_the_title_match_it_does_not_add_to_it(self, world, session):
        world.stubs("Dirt")
        link(session, pages={"dirt": [("Item", "minecraft:dirt")]})
        assert set(about(world.db, "Dirt")) == {"Item:minecraft:dirt"}

    def test_a_fix_with_two_targets_is_ambiguous_and_confirmed(self, world, session):
        world.stubs("Fowl")
        link(session, pages={"fowl": [("Item", "minecraft:chicken"), ("Mob", "minecraft:chicken")]})
        found = about(world.db, "Fowl")
        assert set(found) == {"Item:minecraft:chicken", "Mob:minecraft:chicken"}
        assert all(a["ambiguous"] and a["candidates"] == 2 and a["status"] == "confirmed" for a in found.values())

    def test_a_null_fix_says_the_page_is_about_nothing(self, world, session):
        world.stubs("Chicken", "Torch")
        link(session, pages={"chicken": []})
        assert about(world.db, "Chicken") == {} and "Item:minecraft:torch" in about(world.db, "Torch")

    def test_the_fix_matches_the_title_in_any_case(self, world, session):
        world.stubs("Chicken")
        link(session, pages={"chicken": [("Mob", "minecraft:chicken")]})
        assert about(world.db, "Chicken")

    def test_a_fix_for_a_node_that_is_not_there_is_a_problem_and_changes_nothing(self, world, session):
        world.stubs("Torch")
        link(session)
        before = world.db.one("MATCH ()-[a:ABOUT]->() RETURN count(a)")
        result = link(session, pages={"x": [("Mob", "minecraft:torch")]}, aliases={"y": [("Item", "minecraft:nope")]})
        assert sorted(result["problems"]) == ["aliases: 'y' points at unknown item 'minecraft:nope'", "pages: 'x' points at unknown mob 'minecraft:torch'"]
        assert result["written"] is False and world.db.one("MATCH ()-[a:ABOUT]->() RETURN count(a)") == before


# --------------------------------------------------------------------------- mentions from the wiki's links

LINKS = ("Light a <a href='/w/Torch'>torch</a>. Pick a <a href='/w/Black_Bed'>black</a> one. Cut <a href='/w/Wood'>timber</a>. "
         "See the <a href='/w/Chicken'>fowl</a> and the <a href='/w/Biome'>biome</a>.")
LINKS_RESOLVED = pages("Torch", "Chicken", "Biome", Black_Bed="Bed", Wood="Logs")


class TestMentionsFromLinks:
    def test_a_linked_page_that_is_about_a_node_is_a_mention_of_it(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        torch = mentions(world.db)[("~0", "Item:minecraft:torch")]
        assert (torch["method"], torch["linkCount"], torch["surfaces"], torch["corroborated"], torch["status"]) == ("link+name", 1, ["torch"], True, "candidate")

    def test_the_page_as_linked_beats_the_page_it_redirects_to(self, world, session):
        """Black Bed redirects to Bed, but the link is to Black Bed, which is about the item Black Bed."""
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        found = nodes_mentioned(world.db)
        assert "Item:minecraft:black_bed" in found and "Category:#minecraft:beds" not in found
        black = mentions(world.db)[("~0", "Item:minecraft:black_bed")]
        assert (black["method"], black["count"], black["linkCount"], black["surfaces"]) == ("link", 0, 1, ["black"])

    def test_a_redirect_is_followed_only_when_the_linked_page_is_about_nothing(self, world, session):
        """Wood is about nothing, but it redirects to Logs, which is the category."""
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        assert about(world.db, "Wood") == {}
        assert mentions(world.db)[("~0", "Category:#minecraft:logs")]["surfaces"] == ["timber"]

    def test_a_link_to_an_ambiguous_page_mentions_every_candidate(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        found = mentions(world.db)
        for k in ("Item:minecraft:chicken", "Mob:minecraft:chicken"):
            assert found[("~0", k)]["ambiguous"] is True and found[("~0", k)]["candidates"] == 2 and found[("~0", k)]["method"] == "link"

    def test_a_link_to_a_page_about_nothing_is_not_a_mention(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        assert nodes_mentioned(world.db) == {"Item:minecraft:torch", "Item:minecraft:black_bed", "Category:#minecraft:logs",
                                             "Item:minecraft:chicken", "Mob:minecraft:chicken"}, "nothing for the link to Biome"

    def test_a_page_a_person_marked_as_about_nothing_gives_no_mention(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session, pages={"chicken": []})
        assert not any("chicken" in k for k in nodes_mentioned(world.db))

    def test_a_hand_made_about_is_used_too(self, world, session):
        world.text("See the <a href='/w/Biome'>biome</a>.", pages("Biome"))
        world.db.q("MATCH (p:Page {title: 'Biome'}), (n:Item {id: 'minecraft:stick'}) CREATE (p)-[:ABOUT {origin: 'person', status: 'confirmed'}]->(n)")
        link(session)
        m = mentions(world.db)[("~0", "Item:minecraft:stick")]
        assert (m["method"], m["ambiguous"], m["candidates"]) == ("link", False, 1)

    def test_two_links_to_one_page_add_up(self, world, session):
        world.text("A <a href='/w/Torch'>torch</a> and more <a href='/w/Torch'>torches</a>.", pages("Torch"))
        link(session)
        m = mentions(world.db)[("~0", "Item:minecraft:torch")]
        assert (m["linkCount"], m["surfaces"], m["count"]) == (2, ["torch", "torches"], 2)

    def test_two_different_pages_that_mean_the_same_node_merge_their_wordings(self, world, session):
        """The page Torch and the page Torches (a redirect to Torch) are both about the torch: one mention, both wordings."""
        world.text("Light the <a href='/w/Torch'>flame</a> and the <a href='/w/Torches'>lamps</a>.", pages("Torch", Torches="Torch"))
        link(session)
        assert world.db.one("MATCH (:Chunk)-[m:MENTIONS]->(:Item {id: 'minecraft:torch'}) RETURN count(m)") == 1
        m = mentions(world.db)[("~0", "Item:minecraft:torch")]
        assert (m["method"], m["linkCount"], m["surfaces"], m["count"]) == ("link", 2, ["flame", "lamps"], 0)

    def test_the_wordings_are_always_in_alphabetical_order(self, world, session):
        world.text("Light the <a href='/w/Torches'>zebra</a> and the <a href='/w/Torch'>apple</a>, a <a href='/w/Torch'>mango</a>.", pages("Torch", Torches="Torch"))
        link(session)
        assert mentions(world.db)[("~0", "Item:minecraft:torch")]["surfaces"] == ["apple", "mango", "zebra"]

    def test_a_redirect_chain_is_followed_one_step_only(self, world, session):
        world.stubs("Start", "Middle", "Logs")
        world.db.q("MATCH (a:Page {title: 'Start'}), (b:Page {title: 'Middle'}), (c:Page {title: 'Logs'}) CREATE (a)-[:REDIRECTS_TO]->(b)-[:REDIRECTS_TO]->(c)")
        world.text("See <a href='/w/Start'>this</a>.", pages("Start"))
        link(session)
        assert not any("logs" in k for k in nodes_mentioned(world.db))

    def test_the_same_link_in_two_overlapping_chunks_is_a_mention_in_both(self, world, session):
        html = "<p>" + " ".join(f"Filler {i} word word word." for i in range(12)) + " A <a href='/w/Link_Target'>zzz</a> here. " + \
               " ".join(f"After {i} word word word." for i in range(12)) + "</p>"
        g = world.load(html, resolved=pages("Link_Target".replace("_", " ")), size=60, overlap=30)
        holders = [c["id"] for c in g["chunks"] if c["links"]]
        assert len(holders) == 2, "the link is in the overlap, so two chunks hold it"
        world.db.q("MATCH (p:Page {title: 'Link Target'}), (n:Item {id: 'minecraft:stick'}) CREATE (p)-[:ABOUT {origin: 'person'}]->(n)")
        link(session)
        rows = world.db.q("MATCH (c:Chunk)-[m:MENTIONS]->(:Item {id: 'minecraft:stick'}) RETURN c.id AS chunk, m.linkCount AS links")
        assert {r["chunk"] for r in rows} == set(holders) and {r["links"] for r in rows} == {1}


# --------------------------------------------------------------------------- mentions from names in the text

class TestMentionsFromNames:
    def test_a_name_in_the_text_is_a_mention_with_count_and_wording(self, world, session):
        world.text("Use a torch. A Torch gives light; torches too.")
        link(session)
        m = mentions(world.db)[("~0", "Item:minecraft:torch")]
        assert (m["method"], m["count"], m["linkCount"], m["surfaces"], m["corroborated"], m["ambiguous"], m["candidates"], m["status"]) == (
            "name", 3, 0, ["torch", "torches"], False, False, 1, "candidate")

    def test_plurals_are_found(self, world, session):
        world.text("Use sticks and arrows and wheat.")
        link(session)
        assert {"Item:minecraft:stick", "Item:minecraft:wheat"} <= nodes_mentioned(world.db)

    def test_the_longest_name_wins(self, world, session):
        world.text("A stone pickaxe is made of stone.")
        link(session)
        found = mentions(world.db)
        assert found[("~0", "Item:minecraft:stone_pickaxe")]["count"] == 1 and found[("~0", "Item:minecraft:stone")]["count"] == 1

    def test_part_of_a_longer_name_is_not_a_second_mention(self, world, session):
        world.text("Craft a stone pickaxe.")
        link(session)
        assert "Item:minecraft:stone" not in nodes_mentioned(world.db)

    def test_whole_words_only(self, world, session):
        world.text("Torchlight and sticky and unstoppable.")
        link(session)
        assert nodes_mentioned(world.db) == set()

    def test_the_verb_leave_is_not_the_category_leaves_but_leaves_is(self, world, session):
        world.text("Please leave the house.")
        link(session)
        assert nodes_mentioned(world.db) == set()
        world.text("Leaves fall in autumn.")
        link(session)
        assert nodes_mentioned(world.db) == {"Category:#minecraft:leaves"}

    def test_a_name_that_is_an_item_and_a_category_mentions_both(self, world, session):
        world.text("Dig dirt and shoot an arrow.")
        link(session)
        found = mentions(world.db)
        for k in ("Item:minecraft:dirt", "Category:#minecraft:dirt", "Item:minecraft:arrow", "Category:#minecraft:arrows"):
            assert found[("~0", k)]["ambiguous"] is True and found[("~0", k)]["candidates"] == 2

    def test_a_name_that_is_an_item_and_a_mob_mentions_both(self, world, session):
        world.text("Cook a chicken.")
        link(session)
        found = mentions(world.db)
        assert {k for (_, k) in found} == {"Item:minecraft:chicken", "Mob:minecraft:chicken"}
        assert all(m["ambiguous"] and m["candidates"] == 2 for m in found.values())

    def test_a_mob_or_a_block_that_is_not_an_item_is_a_mention(self, world, session):
        world.text("A sheep and a spider fell into powder snow.")
        link(session)
        found = mentions(world.db)
        assert {k for (_, k) in found} == {"Mob:minecraft:sheep", "Mob:minecraft:spider", "Block:minecraft:powder_snow"}
        assert not any(m["ambiguous"] for m in found.values())

    def test_a_block_that_is_also_an_item_is_not_mentioned_twice(self, world, session):
        world.text("Place the torch and the wheat.")
        link(session)
        assert nodes_mentioned(world.db) == {"Item:minecraft:torch", "Item:minecraft:wheat"}

    def test_a_station_is_an_item(self, world, session):
        world.text("Use the crafting table.")
        link(session)
        assert nodes_mentioned(world.db) == {"Station:minecraft:crafting_table"}

    def test_the_stoplist_ignores_a_word_and_its_plural(self, world, session):
        world.text("Build a wall and take an axe. Walls and axes.")
        result = link(session, stoplist={"wall", "axe"})
        assert nodes_mentioned(world.db) == set()
        assert result["stopped"] == {"wall": 1, "axe": 1, "walls": 1, "axes": 1}

    def test_without_the_stoplist_those_words_are_mentions(self, world, session):
        world.text("Build a wall and take an axe.")
        link(session)
        assert nodes_mentioned(world.db) == {"Category:#minecraft:walls", "Category:#minecraft:axes"}

    def test_the_stoplist_only_stops_that_word(self, world, session):
        world.text("A stone pickaxe and stone.")
        link(session, stoplist={"stone"})
        assert nodes_mentioned(world.db) == {"Item:minecraft:stone_pickaxe"}

    def test_the_stoplist_does_not_stop_links(self, world, session):
        world.text("See the <a href='/w/Torch'>torch</a>.", pages("Torch"))
        link(session, stoplist={"torch"})
        m = mentions(world.db)[("~0", "Item:minecraft:torch")]
        assert (m["method"], m["linkCount"], m["count"]) == ("link", 1, 0)

    def test_an_alias_makes_other_wording_a_mention_of_the_node_it_names(self, world, session):
        world.text("Use a crafting bench.")
        link(session, aliases={"crafting bench": [("Item", "minecraft:crafting_table")]})
        assert mentions(world.db)[("~0", "Station:minecraft:crafting_table")]["surfaces"] == ["crafting bench"]

    def test_an_alias_can_name_a_mob(self, world, session):
        world.text("A fowl.")
        link(session, aliases={"fowl": [("Mob", "minecraft:chicken")]})
        assert nodes_mentioned(world.db) == {"Mob:minecraft:chicken"}

    def test_an_alias_beats_the_stoplist_and_replaces_the_name(self, world, session):
        world.text("A torch and a bench.")
        link(session, stoplist={"bench"}, aliases={"bench": [("Item", "minecraft:crafting_table")], "torch": [("Item", "minecraft:stick")]})
        assert nodes_mentioned(world.db) == {"Station:minecraft:crafting_table", "Item:minecraft:stick"}

    def test_matching_is_per_chunk(self, world, session):
        world.load("<p>A torch.</p><h2 id='A'>A</h2><p>A stick.</p><h2 id='B'>B</h2><p>Nothing here.</p>")
        link(session)
        assert {(c, k) for (c, k) in mentions(world.db)} == {("~0", "Item:minecraft:torch"), ("#A~0", "Item:minecraft:stick")}


# --------------------------------------------------------------------------- the two kinds of evidence together

class TestMerge:
    def test_a_link_and_a_name_for_the_same_node_are_one_mention_with_both_counts(self, world, session):
        world.text("Use a <a href='/w/Torch'>torch</a> or torches.", pages("Torch"))
        link(session)
        assert world.db.one("MATCH (:Chunk)-[m:MENTIONS]->(:Item {id: 'minecraft:torch'}) RETURN count(m)") == 1
        m = mentions(world.db)[("~0", "Item:minecraft:torch")]
        assert (m["method"], m["count"], m["linkCount"], m["surfaces"], m["corroborated"]) == ("link+name", 2, 1, ["torch", "torches"], True)

    def test_a_link_with_wording_the_text_does_not_give_is_link_only(self, world, session):
        world.text("Smelt <a href='/w/Iron_Ingot'>iron</a>.", pages("Iron_Ingot".replace("_", " ")))
        link(session)
        m = mentions(world.db)[("~0", "Item:minecraft:iron_ingot")]
        assert (m["method"], m["count"], m["linkCount"], m["surfaces"]) == ("link", 0, 1, ["iron"])

    def test_a_name_in_another_chunk_is_corroborated_when_the_page_links_the_node_anywhere(self, world, session):
        world.load("<p>Light a <a href='/w/Torch'>torch</a>.</p><h2 id='A'>A</h2><p>Another torch.</p>", resolved=pages("Torch"))
        link(session)
        found = mentions(world.db)
        assert found[("#A~0", "Item:minecraft:torch")]["method"] == "name" and found[("#A~0", "Item:minecraft:torch")]["corroborated"] is True

    def test_a_link_from_another_page_does_not_corroborate(self, world, session):
        world.load("<p>Light a <a href='/w/Torch'>torch</a>.</p>", resolved=pages("Torch"))
        world.load("<p>Another torch.</p>", title="Other")
        link(session)
        other = world.db.one("MATCH (c:Chunk {id: 'https://minecraft.wiki/w/Other~0'})-[m:MENTIONS]->(:Item {id: 'minecraft:torch'}) RETURN m {.method, .corroborated} AS m")
        assert other == {"method": "name", "corroborated": False}

    def test_a_name_with_no_link_anywhere_is_not_corroborated(self, world, session):
        world.text("a torch")
        link(session)
        assert mentions(world.db)[("~0", "Item:minecraft:torch")]["corroborated"] is False

    def test_ambiguity_from_either_source_is_kept(self, world, session):
        world.text("Eat a chicken. See the <a href='/w/Chicken'>fowl</a>.", pages("Chicken"))
        link(session)
        found = mentions(world.db)
        assert all(found[("~0", k)]["ambiguous"] and found[("~0", k)]["method"] == "link+name" for k in ("Item:minecraft:chicken", "Mob:minecraft:chicken"))

    def test_every_mention_is_a_candidate_with_the_origin_and_a_timestamp(self, world, session):
        world.text("a torch and a stick and a sheep")
        link(session)
        assert all(m["status"] == "candidate" and m["origin"] == "link_wiki" and m["extractedAt"] == NOW for m in mentions(world.db).values())

    def test_no_label_or_id_is_copied_onto_the_relationship(self, world, session):
        world.text("a torch")
        link(session)
        assert set(mentions(world.db)[("~0", "Item:minecraft:torch")]) == {
            "origin", "method", "count", "linkCount", "surfaces", "corroborated", "ambiguous", "candidates", "status", "extractedAt"}


# --------------------------------------------------------------------------- running it

class TestRun:
    def counts(self, db):
        return db.one("MATCH ()-[a:ABOUT]->() RETURN count(a)"), db.one("MATCH ()-[m:MENTIONS]->() RETURN count(m)")

    def test_a_dry_run_gives_the_report_data_and_leaves_nothing_behind(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        result = lk.run(session, EMPTY, NOW, dry_run=True)
        assert result["written"] is False and result["data"]["about"] and result["data"]["mentions"]
        assert self.counts(world.db) == (0, 0)

    def test_a_real_run_keeps_the_links_and_commits_before_it_returns(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        for _ in range(3):
            result = link(session)
            assert result["written"] is True
            assert self.counts(world.db) == (len(result["data"]["about"]), len(result["data"]["mentions"])) and self.counts(world.db)[1] > 0

    def test_running_twice_gives_the_same_links(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        first = mentions(world.db), world.db.one("MATCH ()-[a:ABOUT]->() RETURN count(a)")
        link(session)
        assert (mentions(world.db), world.db.one("MATCH ()-[a:ABOUT]->() RETURN count(a)")) == first

    def test_changing_the_config_and_running_again_replaces_the_links(self, world, session):
        world.text("Build a wall.")
        link(session)
        assert "Category:#minecraft:walls" in nodes_mentioned(world.db)
        link(session, stoplist={"wall"})
        assert nodes_mentioned(world.db) == set()

    def test_links_made_by_hand_survive_every_run(self, world, session):
        world.text("a torch")
        world.stubs("Biome")
        world.db.q("MATCH (p:Page {title: 'Biome'}), (n:Item {id: 'minecraft:stick'}) CREATE (p)-[:ABOUT {origin: 'person'}]->(n)")
        world.db.q("MATCH (c:Chunk), (n:Item {id: 'minecraft:stick'}) WITH c, n LIMIT 1 CREATE (c)-[:MENTIONS {origin: 'person', status: 'confirmed'}]->(n)")
        link(session)
        link(session)
        assert world.db.one("MATCH ()-[a:ABOUT {origin: 'person'}]->() RETURN count(a)") == 1
        assert world.db.one("MATCH ()-[m:MENTIONS {origin: 'person'}]->() RETURN count(m)") == 1

    def test_a_problem_in_the_config_writes_nothing_and_leaves_the_old_links(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        before = self.counts(world.db)
        result = link(session, aliases={"bench": [("Item", "minecraft:nope")]})
        assert result["problems"] and result["written"] is False and self.counts(world.db) == before

    def test_a_failure_part_way_leaves_the_old_links_in_place(self, world, session, monkeypatch):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        before = mentions(world.db), self.counts(world.db)

        def boom(tx, at):
            raise RuntimeError("fails after the old links were deleted")

        monkeypatch.setattr(lk, "mention_links", boom)
        with pytest.raises(RuntimeError, match="fails after"):
            link(session, stoplist={"torch"})
        assert (mentions(world.db), self.counts(world.db)) == before

    def test_the_wiki_graph_itself_is_not_changed(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        shape = ("MATCH (n) WHERE n:Chunk OR n:Section OR n:Page OR n:Wiki RETURN labels(n)[0] AS l, count(*) AS n ORDER BY l")
        edges = "MATCH ()-[r:LINKS_TO|REDIRECTS_TO|NEXT|PART_OF|HAS_PAGE|HAS_SECTION]->() RETURN type(r) AS t, count(*) AS n ORDER BY t"
        before = world.db.q(shape), world.db.q(edges)
        link(session)
        assert (world.db.q(shape), world.db.q(edges)) == before

    def test_the_game_graph_is_not_changed(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        query = "MATCH (n) WHERE n:Item OR n:Mob OR n:Block RETURN labels(n) AS l, n.id AS id, n.name AS name, properties(n) AS p ORDER BY id, l"
        before = world.db.q(query)
        link(session)
        assert world.db.q(query) == before

    def test_a_reloaded_page_loses_its_mentions_with_its_chunks_and_a_rerun_restores_them(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        link(session)
        world.text(LINKS, LINKS_RESOLVED)
        assert world.db.one("MATCH (:Chunk)-[m:MENTIONS]->() RETURN count(m)") == 0, "mentions go with the chunks that held them"
        assert world.db.one("MATCH ()-[a:ABOUT]->() RETURN count(a)") > 0, "page mappings stay with the pages"
        link(session)
        assert world.db.one("MATCH (:Chunk)-[m:MENTIONS]->() RETURN count(m)") > 0

    def test_an_empty_graph_runs_without_error(self, linkdb, session):
        linkdb["db"].q("MATCH (n) DETACH DELETE n")
        result = link(session)
        assert result["written"] is True and result["data"]["about"] == [] and result["data"]["mentions"] == []


# --------------------------------------------------------------------------- the report

class TestReportFromTheGraph:
    def report(self, session, **config) -> str:
        result = link(session, **config)
        return "\n".join(lk.report(result["data"], result["stopped"], result["problems"]))

    def test_the_review_report_for_a_small_wiki(self, world, session):
        world.text(LINKS + " Dig dirt and take an axe.", LINKS_RESOLVED)
        out = self.report(session)
        assert "ambiguous pages, linked to every candidate" in out
        assert "Chicken -> Item Chicken (minecraft:chicken); Mob Chicken (minecraft:chicken)" in out
        assert "Biome" in out.split("are about nothing in the game")[1].split("mentions:")[0]
        assert "Axe [Category]" in out and "[ambiguous]" in out, "the name-only axe and the ambiguous dirt are on the review list"
        assert "found only through the wiki's links" in out and "Black Bed" in out

    def test_the_stoplist_effect_is_reported(self, world, session):
        world.text("Build a wall.")
        assert "ignored by the stoplist: {'wall': 1}" in self.report(session, stoplist={"wall"})

    def test_pages_whose_redirect_is_about_something_are_not_listed_as_unmapped(self, world, session):
        world.text(LINKS, LINKS_RESOLVED)
        unmapped = {u["page"] for u in link(session)["data"]["unmapped"]}
        assert "Wood" not in unmapped and "Biome" in unmapped


# --------------------------------------------------------------------------- what the links are for

class TestUse:
    def test_from_an_item_to_the_sections_that_mention_it(self, world, session):
        world.load("<p>A torch.</p><h2 id='A'>Shelter</h2><p>Another torch.</p><h2 id='B'>Food</h2><p>Bread.</p>")
        link(session)
        rows = world.db.q("MATCH (:Item {id: 'minecraft:torch'})<-[:MENTIONS]-(:Chunk)-[:PART_OF]->(s:Section) RETURN s.title AS section ORDER BY s.order")
        assert [r["section"] for r in rows] == ["Guide", "Shelter"]

    def test_from_a_passage_the_candidates_of_an_ambiguous_word_are_all_there_with_their_own_kind(self, world, session):
        world.text("Cook a chicken.")
        link(session)
        rows = world.db.q("MATCH (:Chunk)-[m:MENTIONS {ambiguous: true}]->(n) WHERE n.name = 'Chicken' RETURN labels(n) AS labels, m.candidates AS candidates")
        assert sorted(sorted(r["labels"]) for r in rows) == [["Item"], ["Mob"]] and {r["candidates"] for r in rows} == {2}

    def test_from_a_page_to_what_it_is_about_and_back_to_the_text_that_links_it(self, world, session):
        world.text("See the <a href='/w/Torch'>torch</a>.", pages("Torch"))
        link(session)
        row = world.db.one("MATCH (c:Chunk)-[:LINKS_TO]->(p:Page)-[:ABOUT]->(n:Item) RETURN n.name AS item, p.title AS page, c.text AS text")
        assert row == {"item": "Torch", "page": "Torch", "text": "See the torch."}

    def test_the_redirect_is_a_path_you_can_walk(self, world, session):
        world.text("Pick a <a href='/w/Black_Bed'>black</a> one.", pages(Black_Bed="Bed"))
        link(session)
        row = world.db.one("MATCH (c:Chunk)-[:LINKS_TO]->(:Page {title: 'Black Bed'})-[:REDIRECTS_TO]->(:Page)-[:ABOUT]->(n) RETURN n.name AS general")
        assert row == "Bed"
