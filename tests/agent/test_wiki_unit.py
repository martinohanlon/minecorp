"""Unit tests for agent/minecorp_agent/wiki.py (search_wiki and read_section) that need no database and no OpenAI.

A scripted stand-in for the Graph returns the rows each query would, so these check what the tools do with them:
merging, ordering, clamping, wording and the error cases. The real queries are tested in test_wiki_graph.py.
"""
from __future__ import annotations

import pytest
from neo4j.exceptions import ClientError

from minecorp_agent import wiki

import load_wiki as lw


class FakeGraph:
    """Answers `read` by looking for a marker in the query text; remembers every call."""

    def __init__(self, **by_marker):
        self.by_marker = by_marker
        self.calls: list[tuple[str, dict]] = []

    def read(self, query, **params):
        self.calls.append((query, params))
        for marker, rows in self.by_marker.items():
            if marker in " ".join(query.split()):
                return rows(params) if callable(rows) else rows
        raise AssertionError(f"no scripted answer for: {query}")


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    seen = []
    monkeypatch.setattr(wiki, "embed_query", lambda text: seen.append(text) or [0.1, 0.2])
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    return seen


def hit(idx, text, section="https://w/Guide#Shelter", score=0.9, page="Guide", trail=("Shelter",), model="text-embedding-3-small"):
    return {"id": f"chunk-{idx}", "idx": idx, "text": text, "model": model, "score": score, "section": section, "page": page, "trail": list(trail)}


def about(chunk, name, id_, labels=("Item",), links=0, count=1, ambiguous=False):
    """A row for (Chunk)-[:MENTIONS]->(node): the node's own labels say what it is."""
    return {"chunk": chunk, "name": name, "id": id_, "labels": list(labels), "links": links, "count": count, "ambiguous": ambiguous}


def search(rows, mentions=None, **args):
    graph = FakeGraph(**{"SEARCH c IN": rows, "MENTIONS": mentions or []})
    return wiki.tool_search_wiki(graph, {"question": "how do I build a shelter?", **args}), graph


# --------------------------------------------------------------------------- text helpers

class TestStitch:
    def test_one_chunk_is_unchanged(self):
        assert wiki.stitch(["only this"]) == "only this" and wiki.stitch([]) == ""

    def test_the_repeated_overlap_is_not_repeated(self):
        assert wiki.stitch(["First sentence here. Second sentence here.", "Second sentence here. Third one."]) == \
            "First sentence here. Second sentence here. Third one."

    def test_chunks_that_do_not_overlap_are_joined_by_a_newline(self):
        assert wiki.stitch(["Alpha beta gamma delta.", "Epsilon zeta eta theta."]) == "Alpha beta gamma delta.\nEpsilon zeta eta theta."

    def test_a_tiny_shared_ending_is_not_taken_for_the_overlap(self):
        assert wiki.stitch(["Some words end.", "end. Next thing is here."]) == "Some words end.\nend. Next thing is here."

    def test_the_longest_overlap_wins(self):
        a, b = "x one two three four five.", "two three four five. six seven."
        assert wiki.stitch([a, b]) == "x one two three four five. six seven."

    @pytest.mark.parametrize("size,overlap", [(30, 8), (22, 10), (40, 0), (25, 3), (12, 11)])
    def test_stitching_the_loaders_chunks_gives_back_the_section_text_exactly(self, size, overlap):
        class Words:
            def count(self, text):
                return len(text.split())

        text = "\n".join(" ".join(f"S{i}-{j} w w end." for j in range(5)) for i in range(8)) + "\n- a list item\n- another item here"
        chunks = lw.chunk_text(text, Words(), size, overlap)
        assert len(chunks) > 3
        assert wiki.stitch(chunks) == text


class TestClip:
    def test_short_text_is_untouched(self):
        assert wiki.clip("short", 10) == ("short", False)

    def test_long_text_is_cut_at_a_word_and_marked(self):
        text, cut = wiki.clip("one two three four five", 12)
        assert cut and text == "one two ..." and len(text) <= 16

    def test_a_single_long_word_is_cut_without_crashing(self):
        text, cut = wiki.clip("x" * 50, 10)
        assert cut and text.endswith("...")


class TestClamp:
    @pytest.mark.parametrize("value,expected", [(3, 3), ("4", 4), (0, 1), (-5, 1), (99, 8), (None, 5), ("lots", 5), (2.9, 2)])
    def test_values(self, value, expected):
        assert wiki.clamp(value, 5, 1, 8) == expected


class TestPathText:
    def test_nested_sections(self):
        assert wiki.path_text("Guide", ["Your first day", "Making shelter"]) == "Guide > Your first day > Making shelter"

    def test_the_intro_is_just_the_page(self):
        assert wiki.path_text("Guide", ["Guide"]) == "Guide" and wiki.path_text("Guide", []) == "Guide"


class TestEmbeddingModel:
    def test_default_and_override(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        assert wiki.embedding_model() == "text-embedding-3-small"
        monkeypatch.setenv("EMBEDDING_MODEL", "other")
        assert wiki.embedding_model() == "other"


# --------------------------------------------------------------------------- search_wiki

class TestSearchWiki:
    def test_the_question_is_embedded_and_sent_to_the_vector_index(self, fake_embedder):
        result, graph = search([hit(1, "Build walls.")])
        assert fake_embedder == ["how do I build a shelter?"]
        query, params = graph.calls[0]
        assert "VECTOR INDEX chunk_embedding" in query, "the supported SEARCH clause, not the deprecated procedure"
        assert "queryNodes" not in query
        assert params == {"vector": [0.1, 0.2], "k": 20}

    def test_a_hit_has_page_section_path_url_text_and_score(self):
        result, _ = search([hit(1, "Build walls.", trail=("Your first day", "Making shelter"), score=0.91234)])
        assert result["hits"] == [{"page": "Guide", "section": "Guide > Your first day > Making shelter",
                                   "sectionUrl": "https://w/Guide#Shelter", "text": "Build walls.", "score": 0.912}]

    def test_the_result_says_where_the_text_comes_from_and_how_to_read_more(self):
        result, _ = search([hit(1, "x")])
        assert "community-written" in result["source"] and "according to the wiki" in result["source"]
        assert "read_section" in result["note"]

    def test_the_intro_section_is_shown_as_the_page(self):
        result, _ = search([hit(0, "Welcome.", section="https://w/Guide", trail=("Guide",))])
        assert result["hits"][0]["section"] == "Guide"

    def test_hits_come_back_best_first(self):
        result, _ = search([hit(1, "a", section="s1", score=0.5), hit(9, "b", section="s2", score=0.9), hit(5, "c", section="s3", score=0.7)])
        assert [h["sectionUrl"] for h in result["hits"]] == ["s2", "s3", "s1"]

    def test_chunks_of_one_section_are_merged_into_one_passage_with_the_best_score(self):
        result, _ = search([hit(4, "Walls keep mobs out. Roofs stop spiders.", score=0.6),
                            hit(3, "Build a base. Walls keep mobs out.", score=0.8)])
        assert len(result["hits"]) == 1
        assert result["hits"][0]["text"] == "Build a base. Walls keep mobs out. Roofs stop spiders."
        assert result["hits"][0]["score"] == 0.8

    def test_chunks_that_are_not_next_to_each_other_are_kept_apart_in_one_hit(self):
        result, _ = search([hit(2, "Alpha beta gamma delta."), hit(7, "Epsilon zeta eta theta.")])
        assert result["hits"][0]["text"] == "Alpha beta gamma delta.\n[...]\nEpsilon zeta eta theta."

    def test_the_same_title_in_two_places_stays_two_hits(self):
        result, _ = search([hit(1, "a", section="https://w/A#Tips"), hit(1, "b", section="https://w/B#Tips", page="B")])
        assert len(result["hits"]) == 2

    def test_a_long_passage_is_cut(self):
        result, _ = search([hit(1, "word " * 600)])
        assert len(result["hits"][0]["text"]) <= wiki.MAX_HIT_CHARS + 5 and result["hits"][0]["text"].endswith("...")

    def test_limit_defaults_to_five_and_is_clamped_to_eight(self):
        rows = [hit(i, f"t{i}", section=f"s{i}", score=1 - i / 100) for i in range(12)]
        assert len(search(rows)[0]["hits"]) == 5
        assert len(search(rows, limit=3)[0]["hits"]) == 3
        assert len(search(rows, limit=50)[0]["hits"]) == 8
        assert len(search(rows, limit=0)[0]["hits"]) == 1
        assert search(rows, limit=50)[1].calls[0][1]["k"] == 32

    def test_nothing_found(self):
        result, _ = search([])
        assert result["hits"] == [] and "could not find" in result["note"]

    @pytest.mark.parametrize("question", ["", "   ", None])
    def test_an_empty_question_is_an_error_for_the_model_and_costs_nothing(self, question, fake_embedder):
        graph = FakeGraph()
        result = wiki.tool_search_wiki(graph, {"question": question})
        assert "error" in result and fake_embedder == [] and graph.calls == []

    def test_chunks_embedded_with_another_model_are_refused_not_searched(self):
        result, _ = search([hit(1, "x", model="old-model")])
        assert "old-model" in result["error"] and "text-embedding-3-small" in result["error"]

    def test_the_model_can_be_changed_with_the_environment(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_MODEL", "new-model")
        assert "error" in search([hit(1, "x")])[0]
        assert "hits" in search([hit(1, "x", model="new-model")])[0]

    def test_a_missing_index_means_the_wiki_is_not_loaded(self):
        def missing(params):
            raise ClientError("There is no such vector schema index: chunk_embedding")

        result, _ = search(missing)
        assert "not been loaded" in result["error"]

    def test_other_database_errors_are_not_hidden(self):
        def broken(params):
            raise ClientError("Syntax error somewhere else")

        with pytest.raises(ClientError):
            search(broken)


# --------------------------------------------------------------------------- read_section

URL = "https://w/Guide#Shelter"


def section_graph(chunks=(), subsections=(), trail=("Shelter",), before=(), after=(), titles=None, mentions=()):
    """A graph for read_section. `titles` maps a title to the section urls with that title."""
    def trail_rows(params):
        known = {URL, "https://w/Guide#Food"} | {u for urls in (titles or {}).values() for u in urls}
        return [{"page": "Guide", "trail": list(trail)}] if params["url"] in known else []

    return FakeGraph(**{
        "toLower": lambda params: [{"url": u} for u in (titles or {}).get(params["t"].lower(), [])],
        "RETURN p.title AS page": trail_rows,
        "MENTIONS": list(mentions),
        "(c:Chunk)-[:PART_OF]->": [{"id": f"c{i}", "text": t} for i, t in enumerate(chunks)],
        "HAS_SUBSECTION]->(c:Section)": [{"title": t, "url": u} for t, u in subsections],
        "(b:Chunk)-[:NEXT*1..3]->(:Chunk {id: $id})": [{"section": s, "text": t, "d": d} for s, t, d in before],
        "(:Chunk {id: $id})-[:NEXT*1..3]->(b:Chunk)": [{"section": s, "text": t, "d": d} for s, t, d in after],
    })


class TestKindOf:
    @pytest.mark.parametrize("labels,kind", [(["Item"], "Item"), (["Item", "ItemClass"], "Category"), (["Item", "Station"], "Station"),
                                             (["Mob"], "Mob"), (["Block"], "Block"), ([], "Item")])
    def test_the_node_says_what_it_is(self, labels, kind):
        assert wiki.kind_of(labels) == kind


class TestSearchMentions:
    def test_a_hit_lists_the_game_nodes_its_chunk_is_about(self):
        result, _ = search([hit(1, "Place a torch.")], mentions=[about("chunk-1", "Torch", "minecraft:torch")])
        assert result["hits"][0]["mentions"] == [{"name": "Torch", "kind": "Item", "id": "minecraft:torch"}]

    def test_the_kind_comes_from_the_nodes_own_labels(self):
        rows = [about("chunk-1", "Sheep", "minecraft:sheep", ("Mob",), count=5), about("chunk-1", "Powder Snow", "minecraft:powder_snow", ("Block",), count=4),
                about("chunk-1", "Planks", "#minecraft:planks", ("Item", "ItemClass"), count=3),
                about("chunk-1", "Crafting Table", "minecraft:crafting_table", ("Item", "Station"), count=2), about("chunk-1", "Torch", "minecraft:torch", count=1)]
        kinds = {m["name"]: m["kind"] for m in search([hit(1, "x")], mentions=rows)[0]["hits"][0]["mentions"]}
        assert kinds == {"Sheep": "Mob", "Powder Snow": "Block", "Planks": "Category", "Crafting Table": "Station", "Torch": "Item"}

    def test_linked_and_ambiguous_are_shown_only_when_true(self):
        rows = [about("chunk-1", "Torch", "minecraft:torch", links=2), about("chunk-1", "Stick", "minecraft:stick", count=1)]
        found = {m["name"]: m for m in search([hit(1, "x")], mentions=rows)[0]["hits"][0]["mentions"]}
        assert found["Torch"]["linked"] is True and "linked" not in found["Stick"]
        assert "ambiguous" not in found["Torch"] and "ambiguous" not in found["Stick"]

    def test_an_ambiguous_word_lists_every_candidate_each_marked(self):
        rows = [about("chunk-1", "Chicken", "minecraft:chicken", ("Item",), count=1, ambiguous=True),
                about("chunk-1", "Chicken", "minecraft:chicken", ("Mob",), count=1, ambiguous=True)]
        found = search([hit(1, "Cook a chicken.")], mentions=rows)[0]["hits"][0]["mentions"]
        assert sorted((m["kind"], m["ambiguous"]) for m in found) == [("Item", True), ("Mob", True)]
        assert {m["id"] for m in found} == {"minecraft:chicken"}, "the same id is two nodes: they must not be merged into one"

    def test_the_chunks_of_one_passage_put_their_mentions_together(self):
        rows = [about("chunk-3", "Torch", "minecraft:torch", count=2), about("chunk-4", "Torch", "minecraft:torch", links=1, count=1),
                about("chunk-4", "Stick", "minecraft:stick", count=1)]
        result, _ = search([hit(3, "a"), hit(4, "b")], mentions=rows)
        assert len(result["hits"]) == 1
        assert result["hits"][0]["mentions"] == [{"name": "Torch", "kind": "Item", "id": "minecraft:torch", "linked": True},
                                                 {"name": "Stick", "kind": "Item", "id": "minecraft:stick"}]

    def test_linked_nodes_come_first_then_the_most_often_named_then_by_name(self):
        rows = [about("chunk-1", "Zebra", "z", count=9), about("chunk-1", "Apple", "a", count=1), about("chunk-1", "Mango", "m", count=1),
                about("chunk-1", "Linked", "l", links=1, count=0)]
        names = [m["name"] for m in search([hit(1, "x")], mentions=rows)[0]["hits"][0]["mentions"]]
        assert names == ["Linked", "Zebra", "Apple", "Mango"]

    def test_only_a_handful_are_listed_and_the_linked_ones_survive_the_cut(self):
        rows = [about("chunk-1", f"Thing {i:02}", f"id{i}", count=10 - i % 3) for i in range(12)] + [about("chunk-1", "Linked", "linked", links=1, count=0)]
        found = search([hit(1, "x")], mentions=rows)[0]["hits"][0]["mentions"]
        assert len(found) == wiki.MAX_MENTIONS == 8 and found[0]["name"] == "Linked"

    def test_a_hit_with_nothing_mentioned_has_no_mentions_key(self):
        result, _ = search([hit(1, "x"), hit(5, "y", section="https://w/Guide#Food")], mentions=[about("chunk-1", "Torch", "minecraft:torch")])
        by_section = {h["sectionUrl"]: h for h in result["hits"]}
        assert "mentions" in by_section["https://w/Guide#Shelter"] and "mentions" not in by_section["https://w/Guide#Food"]

    def test_when_the_wiki_has_not_been_linked_nothing_changes_for_the_model(self):
        result, _ = search([hit(1, "Place a torch.")])
        assert "mentions" not in result["hits"][0] and result["hits"][0]["text"] == "Place a torch."

    def test_mentions_are_fetched_in_one_query_for_the_chunks_that_were_found(self):
        _, graph = search([hit(1, "a"), hit(2, "b", section="s2"), hit(3, "c", section="s3")])
        queries = [(q, p) for q, p in graph.calls if "MENTIONS" in q]
        assert len(queries) == 1 and sorted(queries[0][1]["ids"]) == ["chunk-1", "chunk-2", "chunk-3"]

    def test_a_relationship_made_by_hand_without_counts_does_not_break_it(self):
        rows = [{"chunk": "chunk-1", "name": "Torch", "id": "minecraft:torch", "labels": ["Item"], "links": None, "count": None, "ambiguous": None}]
        assert search([hit(1, "x")], mentions=rows)[0]["hits"][0]["mentions"] == [{"name": "Torch", "kind": "Item", "id": "minecraft:torch"}]

    def test_the_note_tells_the_model_how_to_read_them(self):
        note = search([hit(1, "x")])[0]["note"]
        assert "mentions" in note and "ambiguous" in note and "read_section" in note

    def test_the_text_is_not_changed_by_mentions(self):
        result, _ = search([hit(1, "Place a torch.")], mentions=[about("chunk-1", "Torch", "minecraft:torch")])
        assert result["hits"][0]["text"] == "Place a torch."


class TestReadSectionMentions:
    def test_the_section_lists_what_it_is_about(self):
        graph = section_graph(["Place a torch."], mentions=[about("c0", "Torch", "minecraft:torch", links=1)])
        result = wiki.tool_read_section(graph, {"section": URL})
        assert result["mentions"] == [{"name": "Torch", "kind": "Item", "id": "minecraft:torch", "linked": True}]
        assert "ambiguous" in result["note"]

    def test_all_the_chunks_of_the_section_count(self):
        graph = section_graph(["One two three four.", "three four. Five."], mentions=[
            about("c0", "Torch", "minecraft:torch", count=1), about("c1", "Torch", "minecraft:torch", count=2), about("c1", "Stick", "minecraft:stick", count=1)])
        result = wiki.tool_read_section(graph, {"section": URL})
        assert [m["name"] for m in result["mentions"]] == ["Torch", "Stick"]
        assert next(q for q, _ in graph.calls if "MENTIONS" in q) and next(p for q, p in graph.calls if "MENTIONS" in q)["ids"] == ["c0", "c1"]

    def test_ambiguous_candidates_are_all_listed(self):
        graph = section_graph(["Cook a chicken."], mentions=[about("c0", "Chicken", "minecraft:chicken", ("Item",), ambiguous=True),
                                                             about("c0", "Chicken", "minecraft:chicken", ("Mob",), ambiguous=True)])
        assert sorted(m["kind"] for m in wiki.tool_read_section(graph, {"section": URL})["mentions"]) == ["Item", "Mob"]

    def test_a_section_with_nothing_mentioned_has_neither_mentions_nor_note(self):
        result = wiki.tool_read_section(section_graph(["Plain text."]), {"section": URL})
        assert "mentions" not in result and "note" not in result

    def test_a_section_with_no_text_asks_for_nothing(self):
        graph = section_graph([], subsections=[("Wood", "u")])
        result = wiki.tool_read_section(graph, {"section": URL})
        assert "mentions" not in result and not any("MENTIONS" in q for q, _ in graph.calls)


class TestReadSection:
    def test_returns_the_stitched_text_with_page_path_and_url(self):
        graph = section_graph(["Alpha beta gamma delta epsilon.", "delta epsilon. Zeta eta."], trail=("Your first day", "Shelter"))
        result = wiki.tool_read_section(graph, {"section": URL})
        assert result["text"] == "Alpha beta gamma delta epsilon. Zeta eta."
        assert result["page"] == "Guide" and result["section"] == "Guide > Your first day > Shelter" and result["sectionUrl"] == URL
        assert "community-written" in result["source"] and "truncated" not in result

    def test_a_section_title_works_when_it_is_unique(self):
        graph = section_graph(["Some text here."], titles={"making shelter": [URL]})
        result = wiki.tool_read_section(graph, {"section": "Making Shelter"})
        assert result["sectionUrl"] == URL and result["text"] == "Some text here."

    def test_an_ambiguous_title_lists_candidates(self):
        graph = section_graph(titles={"tips": ["https://w/A#Tips", "https://w/B#Tips"]})
        result = wiki.tool_read_section(graph, {"section": "Tips"})
        assert "ambiguous" in result["error"]
        assert [c["sectionUrl"] for c in result["candidates"]] == ["https://w/A#Tips", "https://w/B#Tips"]

    def test_an_unknown_section(self):
        result = wiki.tool_read_section(section_graph(), {"section": "Nope"})
        assert "No wiki section matches 'Nope'" in result["error"]

    @pytest.mark.parametrize("ref", ["", "  ", None])
    def test_an_empty_reference_is_an_error(self, ref):
        assert "error" in wiki.tool_read_section(FakeGraph(), {"section": ref})

    def test_a_section_with_no_text_points_at_its_subsections(self):
        graph = section_graph([], subsections=[("Wood", "https://w/Guide#Wood"), ("Stone", "https://w/Guide#Stone")])
        result = wiki.tool_read_section(graph, {"section": URL})
        assert result["text"] == "" and "subsections" in result["note"]
        assert result["subsections"] == [{"title": "Wood", "sectionUrl": "https://w/Guide#Wood"},
                                         {"title": "Stone", "sectionUrl": "https://w/Guide#Stone"}]

    def test_a_section_with_text_and_subsections_returns_both(self):
        result = wiki.tool_read_section(section_graph(["Text here."], subsections=[("Wood", "u")]), {"section": URL})
        assert result["text"] == "Text here." and result["subsections"] == [{"title": "Wood", "sectionUrl": "u"}] and "note" not in result

    def test_no_subsections_key_when_there_are_none(self):
        assert "subsections" not in wiki.tool_read_section(section_graph(["Text."]), {"section": URL})

    def test_a_long_section_is_cut_and_marked(self, monkeypatch):
        monkeypatch.setattr(wiki, "MAX_SECTION_CHARS", 100)
        result = wiki.tool_read_section(section_graph(["word " * 100]), {"section": URL})
        assert result["truncated"] is True and len(result["text"]) <= 105 and result["text"].endswith("...")

    def test_neighbours_are_only_fetched_when_asked_for(self):
        graph = section_graph(["Text."], before=[("Earlier", "before text", 1)], after=[("Later", "after text", 1)])
        assert "before" not in wiki.tool_read_section(graph, {"section": URL})
        assert not any("NEXT" in q for q, _ in graph.calls)

    def test_before_and_after_add_the_neighbouring_passages_with_their_sections(self):
        graph = section_graph(["Text."], before=[("Earlier", "before text", 1)], after=[("Later", "after text", 1)])
        result = wiki.tool_read_section(graph, {"section": URL, "before": 1, "after": 1})
        assert result["before"] == [{"section": "Earlier", "text": "before text"}]
        assert result["after"] == [{"section": "Later", "text": "after text"}]

    def test_neighbours_start_from_the_first_and_last_chunk_of_the_section(self):
        graph = section_graph(["One two three four.", "three four. Five."], before=[], after=[])
        wiki.tool_read_section(graph, {"section": URL, "before": 2, "after": 2})
        nexts = [(q, p) for q, p in graph.calls if "NEXT" in q]
        assert [(p["id"], p["count"]) for _, p in nexts] == [("c0", 2), ("c1", 2)]

    @pytest.mark.parametrize("asked,used", [(9, 3), (-1, 0), ("x", 0)])
    def test_before_and_after_are_clamped(self, asked, used):
        graph = section_graph(["Text."])
        wiki.tool_read_section(graph, {"section": URL, "before": asked, "after": asked})
        counts = [p["count"] for q, p in graph.calls if "NEXT" in q]
        assert counts == ([used, used] if used else [])
