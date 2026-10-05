"""Unit tests for the parts of loader/link_wiki.py that are not graph work: the written forms of names, the scan of chunk
text for them, the review config, and the report text. The graph work (ABOUT, MENTIONS, ambiguity, redirects) is done in
Cypher and tested against a real database in test_link_wiki_integration.py."""
from __future__ import annotations

from collections import Counter

import pytest
import yaml

import link_wiki as lk


def chunk(id_: str, text: str | None) -> dict:
    return {"id": id_, "text": text}


# --------------------------------------------------------------------------- forms of names

class TestForms:
    def test_normalise(self):
        assert lk.normalise("  Iron   INGOT ") == "iron ingot"

    def test_plurals(self):
        assert lk.plurals("torch") == {"torch", "torchs", "torches"}
        assert "berries" in lk.plurals("berry") and "arrows" in lk.plurals("arrow")
        assert "daies" in lk.plurals("day"), "the rule is crude: it also makes forms that never occur, which is harmless"

    def test_the_name_is_exact_and_other_forms_are_variants(self):
        forms = lk.surface_map(["Torch"], {})
        assert forms["torch"] == {"names": ["torch"], "targets": [], "match": "exact"}
        assert forms["torches"] == {"names": ["torch"], "targets": [], "match": "variant"}

    def test_an_exact_name_beats_the_plural_of_another(self):
        forms = lk.surface_map(["Berry", "Berries"], {})
        assert forms["berries"] == {"names": ["berries"], "targets": [], "match": "exact"}
        assert forms["berry"]["names"] == ["berry"]

    def test_two_names_with_the_same_plural_are_both_kept_when_neither_is_exact(self):
        """'boxes' is the plural of both 'box' and 'boxe': an ambiguity the graph will keep, not something to guess."""
        forms = lk.surface_map(["Box", "Boxe"], {})
        assert forms["boxes"] == {"names": ["box", "boxe"], "targets": [], "match": "variant"}
        assert forms["box"]["names"] == ["box"] and forms["boxe"]["names"] == ["boxe"]

    def test_names_are_normalised_and_deduplicated(self):
        assert lk.surface_map(["Iron  Ingot", "iron ingot", None, ""], {})["iron ingot"]["names"] == ["iron ingot"]

    def test_an_alias_replaces_what_the_words_would_mean(self):
        forms = lk.surface_map(["Torch"], {"torch": [("Mob", "minecraft:x")], "bench": [("Item", "minecraft:crafting_table")]})
        assert forms["torch"] == {"names": [], "targets": [{"kind": "Mob", "id": "minecraft:x"}], "match": "alias"}
        assert forms["bench"]["targets"] == [{"kind": "Item", "id": "minecraft:crafting_table"}]

    def test_singular_forms_are_never_made_for_text(self):
        assert "log" not in lk.surface_map(["Logs"], {}) and "leave" not in lk.surface_map(["Leaves"], {})

    def test_no_names_no_forms(self):
        assert lk.surface_map([], {}) == {}


# --------------------------------------------------------------------------- scanning text

def scan(text, names=("Torch", "Stick", "Stone", "Stone Pickaxe", "Chicken"), stoplist=(), aliases=None, chunks=None):
    return lk.scan(chunks or [chunk("c1", text)], lk.surface_map(list(names), aliases or {}), set(stoplist))


def counted(found):
    return {(f["chunk"], f["surface"]): f["count"] for f in found}


class TestScan:
    def test_a_name_in_the_text_is_found_with_its_count(self):
        found, _ = scan("Use a torch. A Torch gives light; torches too.")
        assert counted(found) == {("c1", "torch"): 2, ("c1", "torches"): 1}
        assert all(f["names"] == ["torch"] for f in found)

    def test_plurals_are_found(self):
        assert ("c1", "sticks") in counted(scan("Two sticks.")[0])

    def test_the_longest_name_wins_and_the_shorter_is_not_counted_inside_it(self):
        assert counted(scan("A stone pickaxe is made of stone.")[0]) == {("c1", "stone pickaxe"): 1, ("c1", "stone"): 1}
        assert counted(scan("Craft a stone pickaxe.")[0]) == {("c1", "stone pickaxe"): 1}

    def test_whole_words_only(self):
        assert scan("Torchlight and sticky and unstoppable.")[0] == []

    def test_punctuation_and_hyphens_do_not_hide_a_name(self):
        assert counted(scan("(torch) and torch-lit and torch.")[0]) == {("c1", "torch"): 3}

    def test_a_name_across_a_line_break_is_not_one_name(self):
        assert scan("a stone\npickaxe")[0] and ("c1", "stone pickaxe") not in counted(scan("a stone\npickaxe")[0])

    def test_matching_is_per_chunk_and_chunks_without_names_give_nothing(self):
        found, _ = scan(None, chunks=[chunk("c1", "a torch"), chunk("c2", "a stick"), chunk("c3", "nothing"), chunk("c4", None), chunk("c5", "")])
        assert counted(found) == {("c1", "torch"): 1, ("c2", "stick"): 1}

    def test_the_result_is_in_a_stable_order(self):
        found, _ = scan(None, chunks=[chunk("c2", "torch stick"), chunk("c1", "stick torch")])
        assert [(f["chunk"], f["surface"]) for f in found] == [("c2", "stick"), ("c2", "torch"), ("c1", "stick"), ("c1", "torch")]

    def test_no_names_no_scan(self):
        assert scan("a torch", names=()) == ([], Counter())

    def test_the_stoplist_skips_a_word_and_counts_what_it_skipped(self):
        found, stopped = scan("A stone and a stone. A torch.", stoplist={"stone"})
        assert counted(found) == {("c1", "torch"): 1} and stopped == {"stone": 2}

    def test_the_stoplist_covers_the_plural_through_the_name(self):
        found, stopped = scan("Torches and a torch.", stoplist={"torch"})
        assert found == [] and stopped == {"torches": 1, "torch": 1}

    def test_the_stoplist_stops_the_word_not_longer_names(self):
        found, _ = scan("A stone pickaxe and stone.", stoplist={"stone"})
        assert counted(found) == {("c1", "stone pickaxe"): 1}

    def test_an_alias_makes_other_wording_a_find_with_its_target(self):
        found, _ = scan("Use a crafting bench.", aliases={"crafting bench": [("Item", "minecraft:crafting_table")]})
        assert found == [{"chunk": "c1", "surface": "crafting bench", "count": 1, "names": [], "targets": [{"kind": "Item", "id": "minecraft:crafting_table"}]}]

    def test_an_alias_is_never_stoplisted(self):
        found, stopped = scan("Use a bench.", stoplist={"bench"}, aliases={"bench": [("Item", "minecraft:crafting_table")]})
        assert len(found) == 1 and not stopped

    def test_an_alias_replaces_a_name_for_the_same_words(self):
        found, _ = scan("a torch", aliases={"torch": [("Item", "minecraft:stick")]})
        assert found[0]["names"] == [] and found[0]["targets"][0]["id"] == "minecraft:stick"

    def test_names_with_regex_characters_are_matched_literally(self):
        found, _ = scan("Play Music Disc (13) now. Not music disc 13.", names=("Music Disc (13)",))
        assert counted(found) == {("c1", "music disc (13)"): 1}


# --------------------------------------------------------------------------- configuration

class TestTargets:
    @pytest.mark.parametrize("value,expected", [
        (None, []), ("minecraft:torch", [("Item", "minecraft:torch")]), ({"mob": "minecraft:chicken"}, [("Mob", "minecraft:chicken")]),
        ({"item": "a", "mob": "b"}, [("Item", "a"), ("Mob", "b")]), (["x", {"block": "y"}], [("Item", "x"), ("Block", "y")]), ([], []),
        ({"biome": "minecraft:desert"}, [("Biome", "minecraft:desert")]), ({"structure": "minecraft:igloo"}, [("Structure", "minecraft:igloo")])])
    def test_forms(self, value, expected):
        assert lk.targets(value, "pages.x") == expected

    @pytest.mark.parametrize("value", [{"recipe": "x"}, {"mob": 3}, 7, [{"nope": "x"}]])
    def test_bad_values_say_where(self, value):
        with pytest.raises(ValueError, match="pages.x"):
            lk.targets(value, "pages.x")


class TestConfig:
    def write(self, tmp_path, text):
        path = tmp_path / "wiki_links.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    EMPTY = {"stoplist": set(), "aliases": {}, "pages": {}}

    def test_a_missing_or_empty_file_means_no_rules(self, tmp_path):
        assert lk.load_config(tmp_path / "nope.yaml") == self.EMPTY
        assert lk.load_config(self.write(tmp_path, "")) == self.EMPTY
        assert lk.load_config(self.write(tmp_path, "stoplist: []\naliases: {}\npages: {}\n")) == self.EMPTY

    def test_everything_is_lower_cased_and_targets_are_read(self, tmp_path):
        config = lk.load_config(self.write(tmp_path, (
            "stoplist: [Wall, ' Iron   Axe ']\n"
            "aliases: {Crafting Bench: minecraft:crafting_table}\n"
            "pages: {Chicken: {mob: 'minecraft:chicken'}, Log: '#minecraft:logs', Wind: null, Both: [{item: a}, {mob: b}]}\n")))
        assert config["stoplist"] == {"wall", "iron axe"}
        assert config["aliases"] == {"crafting bench": [("Item", "minecraft:crafting_table")]}
        assert config["pages"] == {"chicken": [("Mob", "minecraft:chicken")], "log": [("Item", "#minecraft:logs")], "wind": [],
                                   "both": [("Item", "a"), ("Mob", "b")]}

    def test_an_unknown_key_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="unknown keys"):
            lk.load_config(self.write(tmp_path, "stoplst: [wall]\n"))

    def test_a_bad_target_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="pages.Chicken"):
            lk.load_config(self.write(tmp_path, "pages: {Chicken: {recipe: x}}\n"))

    def test_bad_yaml_is_an_error(self, tmp_path):
        with pytest.raises(yaml.YAMLError):
            lk.load_config(self.write(tmp_path, "stoplist: [unclosed\n"))

    def test_the_shipped_config_is_valid(self):
        assert set(lk.load_config()) == {"stoplist", "aliases", "pages"}

    def test_a_target_as_a_query_parameter(self):
        assert lk.as_param([("Mob", "minecraft:chicken")]) == [{"kind": "Mob", "id": "minecraft:chicken"}]


# --------------------------------------------------------------------------- report text

def about(page, name, id_, labels=("Item",), method="title", match="exact", ambiguous=False):
    return {"page": page, "method": method, "match": match, "ambiguous": ambiguous, "name": name, "id": id_, "labels": list(labels)}


def mention(chunk_id, name, id_, method="name", count=1, link_count=0, surfaces=None, corroborated=False, ambiguous=False, labels=("Item",)):
    return {"chunk": chunk_id, "method": method, "count": count, "linkCount": link_count, "surfaces": surfaces or [name.lower()],
            "corroborated": corroborated, "ambiguous": ambiguous, "name": name, "id": id_, "labels": list(labels)}


def data(**kwargs):
    return {"pages": 10, "about": [], "unmapped": [], "mentions": [], **kwargs}


def text(d, stopped=None, problems=(), top=25):
    return "\n".join(lk.report(d, Counter(stopped or {}), list(problems), top))


class TestKindOf:
    @pytest.mark.parametrize("labels,kind", [(["Item"], "Item"), (["Item", "ItemClass"], "Category"), (["Item", "Station"], "Station"),
                                             (["Mob"], "Mob"), (["Block"], "Block"), (["Biome"], "Biome"), (["Structure"], "Structure"), ([], "Item")])
    def test_the_node_says_what_it_is(self, labels, kind):
        assert lk.kind_of(labels) == kind


class TestReport:
    def test_the_page_counts(self):
        out = text(data(about=[about("Torch", "Torch", "t"), about("Log", "Logs", "l", match="variant"), about("Chicken", "Chicken", "c", ambiguous=True)]))
        assert "pages: 10  about something in the game: 3  (ABOUT links 3: exact 2, variant 1, overrides 0)" in out

    def test_a_page_with_two_targets_counts_once(self):
        out = text(data(about=[about("Chicken", "Chicken", "c", ambiguous=True), about("Chicken", "Chicken", "c", labels=("Mob",), ambiguous=True)]))
        assert "about something in the game: 1  (ABOUT links 2" in out

    def test_ambiguous_pages_are_listed_with_every_candidate(self):
        out = text(data(about=[about("Chicken", "Chicken", "minecraft:chicken", ambiguous=True),
                               about("Chicken", "Chicken", "minecraft:chicken", labels=("Mob",), ambiguous=True)]))
        assert "ambiguous pages, linked to every candidate (1)" in out
        assert "Chicken -> Item Chicken (minecraft:chicken); Mob Chicken (minecraft:chicken)" in out

    def test_no_ambiguous_section_when_there_is_none(self):
        assert "ambiguous pages" not in text(data(about=[about("Torch", "Torch", "t")]))

    def test_unmapped_pages_are_listed_most_linked_first_and_cut_to_top(self):
        d = data(unmapped=[{"page": f"Concept {i}", "links": 40 - i} for i in range(40)])
        out = text(d, top=5)
        assert "are about nothing in the game, redirects followed (concepts the graph does not model): 40" in out
        assert out.count("Concept ") == 5 and out.index("Concept 0") < out.index("Concept 1")

    def test_the_mention_totals_by_method_and_kind(self):
        d = data(mentions=[mention("c1", "Torch", "t", "link+name", 2, 1, corroborated=True), mention("c1", "Stick", "s"),
                           mention("c2", "Black Bed", "b", "link", 0, 1, corroborated=True), mention("c1", "Sheep", "sh", labels=("Mob",)),
                           mention("c1", "Powder Snow", "ps", labels=("Block",), ambiguous=True),
                           mention("c1", "Desert", "de", labels=("Biome",)), mention("c1", "Igloo", "ig", labels=("Structure",))])
        out = text(d)
        assert "mentions: 7  (link 1, name 5, link+name 1)  distinct nodes 7 (Item 3, Mob 1, Block 1, Biome 1, Structure 1)  ambiguous 1" in out

    def test_the_review_list_has_only_name_only_mentions_the_page_never_links(self):
        d = data(mentions=[mention("c1", "Torch", "t", "link+name", 2, 1, corroborated=True), mention("c2", "Torch", "t", "name", corroborated=True),
                           mention("c1", "Wall", "w", count=3, surfaces=["wall", "walls"], labels=("Item", "ItemClass")),
                           mention("c2", "Wall", "w", count=1, labels=("Item", "ItemClass")),
                           mention("c1", "Sheep", "sh", labels=("Mob",), ambiguous=True)])
        out = text(d)
        assert "name-only mentions the page never links (2 nodes)" in out
        assert "4  Wall [Category]  in 2 chunks  ['wall', 'walls']" in out
        assert "1  Sheep [Mob]  in 1 chunks  ['sheep']  [ambiguous]" in out
        assert "Torch  in" not in out

    def test_the_review_list_is_ordered_by_count_and_cut_to_top(self):
        d = data(mentions=[mention("c1", f"Thing {i:02}", f"id{i}", count=i + 1) for i in range(30)])
        lines = [l for l in text(d, top=3).splitlines() if l.startswith("   ") and "Thing" in l]
        assert [l.split()[1:3] for l in lines] == [["Thing", "29"], ["Thing", "28"], ["Thing", "27"]]

    def test_items_found_only_through_links(self):
        d = data(mentions=[mention("c1", "Black Bed", "b", "link", 0, 1, corroborated=True), mention("c1", "Torch", "t", "link+name", 1, 1, corroborated=True)])
        assert "found only through the wiki's links (1 nodes, wording the text does not give): ['Black Bed']" in text(d)

    def test_the_stoplist_effect_and_problems_are_shown(self):
        out = text(data(), stopped={"wall": 2}, problems=["pages: 'x' points at unknown item 'y'"])
        assert "ignored by the stoplist: {'wall': 2}" in out and "PROBLEM: pages: 'x' points at unknown item 'y'" in out

    def test_an_empty_result_reports_without_error(self):
        out = text(data(pages=0))
        assert "pages: 0  about something in the game: 0" in out and "mentions: 0" in out
