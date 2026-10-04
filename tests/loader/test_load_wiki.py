"""Unit tests for loader/load_wiki.py: fetching, HTML to a section tree, chunking, the embedding cache and the report.

Nothing here touches the network, OpenAI or Neo4j: a word-count tokenizer, a fake embedder and a mock HTTP transport
stand in. One test checks the real tiktoken tokenizer and is skipped if its encoding is not available.
"""
from __future__ import annotations

import hashlib
import sys
from array import array

import httpx
import pytest

import load_wiki as lw


class WordTokenizer:
    """One token per whitespace-separated word, so chunk sizes can be worked out by hand."""

    def count(self, text: str) -> int:
        return len(text.split())


TOK = WordTokenizer()


def sentence(n: int, words: int = 5) -> str:
    """A sentence of exactly `words` words that is unique per n: 'S3 w w w w.'"""
    return " ".join([f"S{n}"] + ["w"] * (words - 2) + ["end."])


def prose(count: int, words: int = 5) -> str:
    return " ".join(sentence(i, words) for i in range(count))


PAGE_HTML = """
<div class="mw-parser-output">
<p>Intro paragraph. It has two sentences.</p>
<div class="mw-heading mw-heading2"><h2 id="Shelter">Shelter</h2></div>
<p>Build a shelter before night.</p>
<div class="mw-heading mw-heading3"><h3 id="Walls">Walls</h3></div>
<p>Walls keep mobs out.</p>
<ul><li>Use wood</li><li>Use stone</li></ul>
<div class="mw-heading mw-heading3"><h3 id="Roof">Roof</h3></div>
<p>A roof stops spiders. Spiders climb walls.</p>
<div class="mw-heading mw-heading2"><h2 id="Food">Food</h2></div>
<div class="mw-heading mw-heading4"><h4 id="Bread">Bread</h4></div>
<p>Bread is wheat.</p>
</div>
"""


# --------------------------------------------------------------------------- names and fetching

class TestNames:
    def test_page_url_uses_underscores_and_encodes_the_apostrophe(self):
        assert lw.page_url("Tutorial:Beginner's guide") == "https://minecraft.wiki/w/Tutorial:Beginner%27s_guide"

    def test_page_url_keeps_a_plain_title_readable(self):
        assert lw.page_url("Iron Pickaxe") == "https://minecraft.wiki/w/Iron_Pickaxe"

    @pytest.mark.parametrize("given", [
        "https://minecraft.wiki/w/Tutorial:Beginner%27s_guide", "https://minecraft.wiki/w/Tutorial:Beginner's_guide",
        "https://minecraft.wiki/index.php?title=Tutorial:Beginner%27s_guide", "Tutorial:Beginner's guide",
        "Tutorial:Beginner's_guide"])
    def test_every_way_of_naming_the_page_gives_the_same_title(self, given):
        assert lw.title_from_url(given) == "Tutorial:Beginner's guide"

    def test_url_and_title_round_trip(self):
        assert lw.title_from_url(lw.page_url("Brewing (Java Edition)")) == "Brewing (Java Edition)"


class TestFetch:
    def client(self, handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_returns_title_revision_and_html_and_asks_for_the_right_thing(self):
        seen = {}

        def handler(request):
            seen.update(dict(request.url.params))
            return httpx.Response(200, json={"parse": {"title": "Tutorial:Beginner's guide", "revid": 42, "text": "<p>x</p>"}})

        got = lw.fetch_page("Tutorial:Beginner's guide", self.client(handler))
        assert got == {"title": "Tutorial:Beginner's guide", "revisionId": 42, "html": "<p>x</p>"}
        assert seen["action"] == "parse" and seen["page"] == "Tutorial:Beginner's guide"
        assert seen["redirects"] == "1" and seen["format"] == "json" and seen["prop"] == "text|revid"
        assert seen["disableeditsection"] == "1"

    def test_a_redirect_gives_the_real_title(self):
        client = self.client(lambda r: httpx.Response(200, json={"parse": {"title": "Real", "revid": 1, "text": ""}}))
        assert lw.fetch_page("Alias", client)["title"] == "Real"

    def test_a_missing_page_is_a_clear_error(self):
        client = self.client(lambda r: httpx.Response(200, json={"error": {"code": "missingtitle", "info": "The page you specified doesn't exist."}}))
        with pytest.raises(lw.WikiError, match="doesn't exist"):
            lw.fetch_page("Nope", client)

    def test_a_server_error_is_raised(self):
        client = self.client(lambda r: httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            lw.fetch_page("Anything", client)

    def test_the_user_agent_names_the_project_and_no_person(self):
        assert "MineCorp" in lw.USER_AGENT and "github.com/martinohanlon/minecorp" in lw.USER_AGENT
        assert "@" not in lw.USER_AGENT


# --------------------------------------------------------------------------- HTML to sections

def parse(html: str, title: str = "Page") -> list[dict]:
    return lw.parse_sections(html, title)


class TestParseSections:
    def test_the_text_before_the_first_heading_is_the_intro(self):
        s = parse(PAGE_HTML, "Guide")
        assert s[0] == {"title": "Guide", "level": 1, "anchor": "", "blocks": ["Intro paragraph. It has two sentences."]}

    def test_headings_make_sections_in_order_with_their_levels_and_anchors(self):
        s = parse(PAGE_HTML)
        assert [(x["title"], x["level"], x["anchor"]) for x in s[1:]] == [
            ("Shelter", 2, "Shelter"), ("Walls", 3, "Walls"), ("Roof", 3, "Roof"), ("Food", 2, "Food"), ("Bread", 4, "Bread")]

    def test_paragraphs_and_list_items_are_separate_lines_and_items_start_with_a_dash(self):
        walls = next(x for x in parse(PAGE_HTML) if x["title"] == "Walls")
        assert walls["blocks"] == ["Walls keep mobs out.", "- Use wood", "- Use stone"]

    def test_older_markup_with_the_id_on_an_inner_span(self):
        s = parse('<p>a</p><h2><span class="mw-headline" id="Old_style">Old style</span></h2><p>b</p>')
        assert s[1]["title"] == "Old style" and s[1]["anchor"] == "Old_style" and s[1]["blocks"] == ["b"]

    def test_a_heading_without_an_id_gets_a_slug(self):
        assert parse("<h2>Two words</h2><p>x</p>")[1]["anchor"] == "Two_words"

    def test_table_rows_become_lines_with_cells_separated_by_bars(self):
        html = "<table><tr><th>Name</th><th>Use</th></tr><tr><td>Torch</td><td>Light</td></tr></table>"
        assert parse(html)[0]["blocks"] == ["Name | Use", "Torch | Light"]

    def test_an_empty_table_cell_does_not_leave_a_dangling_bar_at_the_start(self):
        assert parse("<table><tr><td></td><td>Only</td></tr></table>")[0]["blocks"] == ["Only"]

    def test_inline_markup_stays_in_the_same_line(self):
        assert parse("<p>Use <b>bold</b> and <a href='/w/X'>a link</a> here.</p>")[0]["blocks"] == ["Use bold and a link here."]

    def test_a_line_break_is_a_space(self):
        assert parse("<p>one<br>two<br/>three</p>")[0]["blocks"] == ["one two three"]

    def test_entities_and_odd_spaces_are_cleaned(self):
        html = "<p>Fish&nbsp;&amp; chips​ and F3‌[Java]</p>"
        assert parse(html)[0]["blocks"] == ["Fish & chips and F3[Java]"]

    @pytest.mark.parametrize("html", [
        '<p>keep</p><span class="mw-editsection">[edit]</span>',
        '<p>keep<sup class="reference">[1]</sup></p>',
        '<p>keep</p><style>.x{color:red}</style><script>var a = 1;</script>',
        '<p>keep</p><div class="toc"><p>Contents</p></div>',
        '<p>keep</p><div class="navbox"><p>Navigation junk</p></div>',
        '<p>keep</p><div class="mcui mcui-Crafting_Table"><span class="invslot-stacksize">3</span></div>',
        '<p>keep</p><span class="invslot-item">9</span>',
        '<p>keep</p><div style="display: none"><p>hidden</p></div>',
        '<p>keep</p><ol class="references"><li>A reference</li></ol>'])
    def test_noise_is_left_out(self, html):
        assert parse(html)[0]["blocks"] == ["keep"]

    def test_skipped_regions_nest_properly(self):
        html = '<div class="navbox"><div><p>a</p></div><p>b</p></div><p>after</p>'
        assert parse(html)[0]["blocks"] == ["after"]

    def test_a_heading_inside_a_skipped_region_is_ignored(self):
        html = '<div class="navbox"><h2>Hidden heading</h2><p>x</p></div><p>y</p>'
        s = parse(html)
        assert len(s) == 1 and s[0]["blocks"] == ["y"]

    def test_stray_and_unclosed_tags_do_not_crash(self):
        s = parse("</div><p>one<p>two</b><li>item<div>after")
        assert s[0]["blocks"] == ["one", "two", "- item", "after"]

    def test_text_in_the_heading_is_not_also_body_text(self):
        s = parse("<h2 id='A'>Head</h2><p>Body</p>")
        assert s[0]["blocks"] == [] and s[1]["blocks"] == ["Body"]

    def test_nested_lists_each_item_on_its_own_line(self):
        s = parse("<ul><li>One<ul><li>Inner</li></ul></li><li>Two</li></ul>")
        assert s[0]["blocks"] == ["- One", "- Inner", "- Two"]


class TestBuildTree:
    URL = "https://minecraft.wiki/w/Guide"

    def tree(self, html=PAGE_HTML):
        return lw.build_tree(parse(html, "Guide"), "Guide", self.URL)

    def test_urls_titles_levels_and_order(self):
        t = self.tree()
        assert [(s["title"], s["level"], s["order"]) for s in t] == [
            ("Guide", 1, 0), ("Shelter", 2, 1), ("Walls", 3, 2), ("Roof", 3, 3), ("Food", 2, 4), ("Bread", 4, 5)]
        assert t[0]["url"] == self.URL and t[1]["url"] == self.URL + "#Shelter"

    def test_parents_form_a_tree_and_the_intro_is_not_a_parent(self):
        t = {s["title"]: s for s in self.tree()}
        assert t["Guide"]["parent"] is None and t["Shelter"]["parent"] is None and t["Food"]["parent"] is None
        assert t["Walls"]["parent"] == t["Shelter"]["url"] and t["Roof"]["parent"] == t["Shelter"]["url"]
        assert t["Bread"]["parent"] == t["Food"]["url"]

    def test_paths_run_from_the_page_title_down(self):
        t = {s["title"]: s for s in self.tree()}
        assert t["Guide"]["path"] == "Guide"
        assert t["Shelter"]["path"] == "Guide > Shelter"
        assert t["Walls"]["path"] == "Guide > Shelter > Walls"
        assert t["Bread"]["path"] == "Guide > Food > Bread"

    def test_a_deeper_level_after_a_shallower_one_pops_the_stack(self):
        t = {s["title"]: s for s in self.tree("<p>i</p><h2 id='A'>A</h2><h3 id='B'>B</h3><h4 id='C'>C</h4><h3 id='D'>D</h3>")}
        assert t["D"]["parent"] == t["A"]["url"] and t["C"]["parent"] == t["B"]["url"]

    def test_a_skipped_level_attaches_to_the_nearest_shallower_section(self):
        t = {s["title"]: s for s in self.tree("<h2 id='A'>A</h2><h4 id='Deep'>Deep</h4>")}
        assert t["Deep"]["parent"] == t["A"]["url"] and t["Deep"]["path"] == "Guide > A > Deep"

    def test_a_subsection_with_no_section_above_is_top_level(self):
        t = {s["title"]: s for s in self.tree("<h3 id='Lone'>Lone</h3><p>x</p>")}
        assert t["Lone"]["parent"] is None and t["Lone"]["path"] == "Guide > Lone"

    def test_repeated_anchors_are_made_unique_the_way_mediawiki_does(self):
        t = self.tree("<h2 id='Tips'>Tips</h2><p>a</p><h2 id='Tips'>Tips</h2><p>b</p><h2 id='Tips'>Tips</h2>")
        assert [s["url"].rsplit("#", 1)[1] for s in t] == ["Tips", "Tips_2", "Tips_3"]

    def test_an_intro_with_no_text_is_dropped_and_order_starts_at_zero(self):
        t = self.tree("<h2 id='A'>A</h2><p>x</p>")
        assert [s["title"] for s in t] == ["A"] and t[0]["order"] == 0

    def test_section_text_joins_the_blocks_with_newlines(self):
        walls = next(s for s in self.tree() if s["title"] == "Walls")
        assert walls["text"] == "Walls keep mobs out.\n- Use wood\n- Use stone"

    def test_a_section_with_only_subsections_keeps_its_node_and_has_no_text(self):
        food = next(s for s in self.tree() if s["title"] == "Food")
        assert food["text"] == ""


# --------------------------------------------------------------------------- chunking

class TestSplitUnits:
    def test_sentences_in_one_line_are_separated_by_spaces_and_lines_by_newlines(self):
        assert lw.split_units("One. Two! Three?\nFour.") == [("", "One."), (" ", "Two!"), (" ", "Three?"), ("\n", "Four.")]

    def test_a_lowercase_word_after_a_full_stop_is_not_a_sentence_start(self):
        assert [t for _, t in lw.split_units("Use e.g. wood. Then stone.")] == ["Use e.g. wood.", "Then stone."]

    def test_blank_lines_are_ignored(self):
        assert lw.split_units("\n  \nOne.\n\n") == [("", "One.")]

    def test_a_sentence_can_start_with_a_digit_or_a_quote(self):
        assert [t for _, t in lw.split_units('Done. 3 logs. "Quoted" text.')] == ["Done.", "3 logs.", '"Quoted" text.']


class TestChunkText:
    def test_empty_text_has_no_chunks(self):
        assert lw.chunk_text("", TOK, 10, 2) == [] and lw.chunk_text("\n \n", TOK, 10, 2) == []

    def test_text_that_fits_is_one_chunk_returned_unchanged(self):
        text = "First line here.\n- an item\nLast bit."
        assert lw.chunk_text(text, TOK, 50, 10) == [text]

    def test_a_section_of_exactly_the_size_is_one_chunk(self):
        text = prose(4, 5)                                        # 20 words
        assert lw.chunk_text(text, TOK, 20, 5) == [text]

    def test_one_word_over_makes_two_chunks(self):
        chunks = lw.chunk_text(prose(4, 5) + " Extra.", TOK, 20, 5)
        assert len(chunks) == 2

    def test_no_chunk_is_longer_than_the_size_and_every_sentence_survives(self):
        text = prose(40, 7)
        chunks = lw.chunk_text(text, TOK, 30, 8)
        assert all(TOK.count(c) <= 30 for c in chunks)
        for n in range(40):
            assert any(sentence(n, 7) in c for c in chunks), f"sentence {n} was lost"

    def test_chunks_start_and_end_on_sentence_boundaries(self):
        chunks = lw.chunk_text(prose(40, 7), TOK, 30, 8)
        assert all(c.startswith("S") and c.endswith("end.") for c in chunks)

    def test_the_next_chunk_starts_with_the_end_of_the_previous_one(self):
        chunks = lw.chunk_text(prose(40, 5), TOK, 22, 10)         # overlap of 10 = the last two 5-word sentences
        for a, b in zip(chunks, chunks[1:]):
            assert b.startswith(" ".join(a.split()[-10:]))
            assert TOK.count(b) <= 22

    def test_the_overlap_never_exceeds_the_limit(self):
        chunks = lw.chunk_text(prose(40, 5), TOK, 22, 7)          # only one 5-word sentence fits in 7
        for a, b in zip(chunks, chunks[1:]):
            shared = sentence(int(a.split()[-5][1:]), 5)
            assert b.startswith(shared) and not b.startswith(" ".join(a.split()[-10:]))

    def test_zero_overlap_repeats_nothing(self):
        chunks = lw.chunk_text(prose(30, 5), TOK, 20, 0)
        assert " ".join(chunks) == prose(30, 5)

    def test_when_the_last_sentence_is_longer_than_the_overlap_the_last_words_are_repeated(self):
        long_sentence = lambda n: f"Start{n} " + "word " * 8 + f"stop{n}."
        text = " ".join(long_sentence(i) for i in range(6))        # six sentences of 10 words
        chunks = lw.chunk_text(text, TOK, 25, 3)
        for a, b in zip(chunks, chunks[1:]):
            assert b.split()[:3] == a.split()[-3:], (a, b)

    def test_a_sentence_longer_than_the_size_is_cut_at_word_boundaries(self):
        words = [f"w{i}" for i in range(95)]
        chunks = lw.chunk_text(" ".join(words), TOK, 30, 5)
        assert all(TOK.count(c) <= 30 for c in chunks) and len(chunks) >= 4
        assert chunks[0].split()[0] == "w0" and chunks[-1].split()[-1] == "w94"
        assert set(w for c in chunks for w in c.split()) == set(words)
        assert all(not w.endswith("w") or w in words for c in chunks for w in c.split()), "no word was cut in half"

    def test_overlap_close_to_the_size_still_makes_progress(self):
        chunks = lw.chunk_text(prose(30, 5), TOK, 12, 11)
        assert 5 < len(chunks) < 200
        assert all(TOK.count(c) <= 12 for c in chunks)

    def test_a_single_unbreakable_word_longer_than_the_size_is_kept_whole(self):
        chunks = lw.chunk_text("Before. " + "x" * 50 + " After.", TOK, 1, 0)
        assert "x" * 50 in chunks

    def test_lines_stay_on_separate_lines_inside_a_chunk(self):
        text = "Heading line.\n- one\n- two\n- three"
        assert lw.chunk_text(text, TOK, 50, 5)[0].split("\n") == ["Heading line.", "- one", "- two", "- three"]

    def test_chunks_are_deterministic(self):
        text = prose(25, 6)
        assert lw.chunk_text(text, TOK, 30, 8) == lw.chunk_text(text, TOK, 30, 8)

    def test_every_chunk_adds_something_new(self):
        chunks = lw.chunk_text(prose(30, 5), TOK, 22, 10)
        for a, b in zip(chunks, chunks[1:]):
            assert b != a and not a.endswith(b), "a chunk must not be wholly contained in the one before"


class TestBuild:
    def build(self, html=PAGE_HTML, size=300, overlap=50):
        return lw.build("Guide", 7, html, "2026-10-04T12:00:00+00:00", TOK, size, overlap)

    def test_wiki_and_page_rows(self):
        g = self.build()
        assert g["wiki"] == {"url": "https://minecraft.wiki", "name": "Minecraft Wiki"}
        assert g["page"] == {"url": "https://minecraft.wiki/w/Guide", "title": "Guide", "revisionId": 7,
                             "fetchedAt": "2026-10-04T12:00:00+00:00"}

    def test_small_sections_are_one_chunk_each_and_empty_sections_have_none(self):
        g = self.build()
        per = {}
        for c in g["chunks"]:
            per[c["section"]] = per.get(c["section"], 0) + 1
        assert per[g["page"]["url"]] == 1
        assert per[g["page"]["url"] + "#Walls"] == 1
        assert g["page"]["url"] + "#Food" not in per

    def test_index_is_the_reading_order_across_the_whole_page(self):
        g = self.build()
        assert [c["index"] for c in g["chunks"]] == list(range(len(g["chunks"])))
        assert [c["section"].rsplit("#", 1)[-1] for c in g["chunks"]] == [
            "https://minecraft.wiki/w/Guide", "Shelter", "Walls", "Roof", "Bread"]

    def test_chunk_ids_are_stable_and_unique(self):
        g = self.build()
        ids = [c["id"] for c in g["chunks"]]
        assert len(set(ids)) == len(ids) and ids[2] == "https://minecraft.wiki/w/Guide#Walls~0"
        assert [c["id"] for c in self.build()["chunks"]] == ids

    def test_the_embedded_text_is_the_heading_path_a_blank_line_and_the_chunk(self):
        walls = next(c for c in self.build()["chunks"] if c["section"].endswith("#Walls"))
        assert walls["embedText"] == "Guide > Shelter > Walls\n\n" + walls["text"]
        assert walls["text"] == "Walls keep mobs out.\n- Use wood\n- Use stone"

    def test_the_hash_is_the_sha256_of_the_embedded_text_and_the_token_count_is_of_the_chunk_alone(self):
        for c in self.build()["chunks"]:
            assert c["textHash"] == hashlib.sha256(c["embedText"].encode("utf-8")).hexdigest()
            assert c["tokens"] == TOK.count(c["text"])

    def test_the_same_text_in_two_places_gets_different_hashes_because_the_path_differs(self):
        html = "<h2 id='A'>A</h2><p>Same words.</p><h2 id='B'>B</h2><p>Same words.</p>"
        a, b = self.build(html)["chunks"]
        assert a["text"] == b["text"] and a["textHash"] != b["textHash"]

    def test_a_long_section_makes_several_chunks_that_all_belong_to_it_and_none_cross_a_heading(self):
        html = f"<h2 id='Long'>Long</h2><p>{prose(40, 6)}</p><h2 id='Next'>Next</h2><p>Other words entirely.</p>"
        g = self.build(html, size=40, overlap=10)
        long_chunks = [c for c in g["chunks"] if c["section"].endswith("#Long")]
        assert len(long_chunks) > 3 and [c["id"].rsplit("~", 1)[1] for c in long_chunks] == [str(i) for i in range(len(long_chunks))]
        assert all("Other" not in c["text"] for c in long_chunks)
        assert g["chunks"][-1]["section"].endswith("#Next") and g["chunks"][-1]["text"] == "Other words entirely."
        assert all(c["tokens"] <= 40 for c in g["chunks"])

    def test_overlap_does_not_cross_sections(self):
        html = f"<h2 id='A'>A</h2><p>{prose(10, 6)}</p><h2 id='B'>B</h2><p>{prose(10, 6)}</p>"
        g = self.build(html, size=30, overlap=12)
        first_b = next(c for c in g["chunks"] if c["section"].endswith("#B"))
        assert first_b["text"].startswith("S0 ")


class TestRealTokenizer:
    def test_counts_tokens_the_way_the_embedding_models_do(self):
        try:
            tok = lw.TikTokenizer()
        except Exception as exc:                                  # the encoding file could not be fetched
            pytest.skip(f"tiktoken encoding not available: {exc}")
        assert tok.count("hello world") == 2
        assert tok.count("") == 0
        assert tok.count("<|endoftext|> is only text here") > 3    # special-token look-alikes do not raise


# --------------------------------------------------------------------------- embeddings

def fake_embedder(dim: int = 4):
    calls: list[list[str]] = []

    def embed(texts):
        calls.append(list(texts))
        return [[(hashlib.sha256(t.encode()).digest()[i] + 1) / 256 for i in range(dim)] for t in texts]

    embed.calls = calls
    return embed


def chunk_rows(texts: list[str]) -> list[dict]:
    return [{"id": f"c{i}", "embedText": t, "textHash": hashlib.sha256(t.encode()).hexdigest()} for i, t in enumerate(texts)]


class TestVectorBytes:
    def test_round_trip_is_float32(self):
        v = [0.1, -2.5, 3.0, 1e-3]
        out = lw.decode_vector(lw.encode_vector(v))
        assert out == list(array("f", v)) and out[1] == -2.5 and out[0] != 0.1 and abs(out[0] - 0.1) < 1e-7

    def test_size_is_four_bytes_per_dimension(self):
        assert len(lw.encode_vector([0.0] * 1536)) == 6144


class TestEmbeddingCache:
    def test_a_miss_is_none(self, tmp_path):
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            assert cache.get("m", "abc") is None and not cache.has("m", "abc") and len(cache) == 0

    def test_put_then_get(self, tmp_path):
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            cache.put("m", "abc", [0.5, 0.25])
            assert cache.has("m", "abc") and cache.get("m", "abc") == [0.5, 0.25] and len(cache) == 1

    def test_it_survives_closing_and_reopening(self, tmp_path):
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            cache.put("m", "abc", [1.0, 2.0])
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            assert cache.get("m", "abc") == [1.0, 2.0]

    def test_the_model_is_part_of_the_key(self, tmp_path):
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            cache.put("small", "abc", [1.0])
            assert cache.get("large", "abc") is None

    def test_the_parent_folder_is_created(self, tmp_path):
        with lw.EmbeddingCache(tmp_path / "deep" / "er" / "c") as cache:
            cache.put("m", "h", [1.0])
        assert (tmp_path / "deep" / "er").is_dir()

    def test_cache_exists_sees_the_files(self, tmp_path):
        assert not lw.cache_exists(tmp_path / "c") and not lw.cache_exists(tmp_path / "missing" / "c")
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            cache.put("m", "h", [1.0])
        assert lw.cache_exists(tmp_path / "c")


class TestEmbedChunks:
    def test_every_chunk_gets_a_vector(self, tmp_path):
        rows = chunk_rows(["a", "b", "c"])
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            stats = lw.embed_chunks(rows, cache, "m", fake_embedder())
        assert all(len(r["embedding"]) == 4 for r in rows)
        assert stats == {"chunks": 3, "fromCache": 0, "embedded": 3}

    def test_the_vectors_are_the_float32_values_from_the_embedder(self, tmp_path):
        rows = chunk_rows(["a"])
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(rows, cache, "m", embedder)
        assert rows[0]["embedding"] == list(array("f", embedder([ "a" ])[0]))

    def test_a_second_run_makes_no_calls_and_gives_the_same_vectors(self, tmp_path):
        first, second = chunk_rows(["a", "b"]), chunk_rows(["a", "b"])
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(first, cache, "m", embedder)
            stats = lw.embed_chunks(second, cache, "m", embedder)
        assert len(embedder.calls) == 1 and stats == {"chunks": 2, "fromCache": 2, "embedded": 0}
        assert [r["embedding"] for r in first] == [r["embedding"] for r in second]

    def test_the_cache_works_after_the_graph_is_gone_because_it_is_a_separate_file(self, tmp_path):
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(chunk_rows(["a", "b"]), cache, "m", embedder)
        with lw.EmbeddingCache(tmp_path / "c") as cache:             # a new process, a rebuilt graph
            stats = lw.embed_chunks(chunk_rows(["a", "b"]), cache, "m", embedder)
        assert stats["embedded"] == 0 and len(embedder.calls) == 1

    def test_only_new_text_is_sent(self, tmp_path):
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(chunk_rows(["a", "b"]), cache, "m", embedder)
            stats = lw.embed_chunks(chunk_rows(["a", "b", "c"]), cache, "m", embedder)
        assert embedder.calls[-1] == ["c"] and stats == {"chunks": 3, "fromCache": 2, "embedded": 1}

    def test_changed_text_is_embedded_again(self, tmp_path):
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(chunk_rows(["Guide > A\n\nold"]), cache, "m", embedder)
            lw.embed_chunks(chunk_rows(["Guide > A\n\nnew"]), cache, "m", embedder)
        assert len(embedder.calls) == 2

    def test_the_same_text_twice_is_sent_once(self, tmp_path):
        embedder = fake_embedder()
        rows = chunk_rows(["same", "same", "other"])
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            stats = lw.embed_chunks(rows, cache, "m", embedder)
        assert embedder.calls == [["same", "other"]] and stats == {"chunks": 3, "fromCache": 0, "embedded": 2}
        assert rows[0]["embedding"] == rows[1]["embedding"]

    def test_a_different_model_does_not_reuse_the_cache(self, tmp_path):
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(chunk_rows(["a"]), cache, "small", embedder)
            lw.embed_chunks(chunk_rows(["a"]), cache, "large", embedder)
        assert len(embedder.calls) == 2

    def test_requests_are_batched(self, tmp_path):
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            lw.embed_chunks(chunk_rows([f"t{i}" for i in range(7)]), cache, "m", embedder, batch=3)
        assert [len(c) for c in embedder.calls] == [3, 3, 1]

    def test_a_failure_part_way_keeps_what_was_already_paid_for(self, tmp_path):
        good = fake_embedder()

        def flaky(texts):
            if len(good.calls) >= 1:
                raise RuntimeError("rate limited")
            return good(texts)

        rows = chunk_rows([f"t{i}" for i in range(5)])
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            with pytest.raises(RuntimeError, match="rate limited"):
                lw.embed_chunks(rows, cache, "m", flaky, batch=2)
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            assert sum(cache.has("m", r["textHash"]) for r in rows) == 2
            stats = lw.embed_chunks(chunk_rows([f"t{i}" for i in range(5)]), cache, "m", good, batch=2)
        assert stats["embedded"] == 3

    def test_a_wrong_number_of_vectors_is_an_error(self, tmp_path):
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            with pytest.raises(RuntimeError, match="asked for 2 embeddings, got 1"):
                lw.embed_chunks(chunk_rows(["a", "b"]), cache, "m", lambda texts: [[1.0]])

    def test_no_chunks_means_no_calls(self, tmp_path):
        embedder = fake_embedder()
        with lw.EmbeddingCache(tmp_path / "c") as cache:
            assert lw.embed_chunks([], cache, "m", embedder) == {"chunks": 0, "fromCache": 0, "embedded": 0}
        assert embedder.calls == []


class TestOpenAIEmbedder:
    class Item:
        def __init__(self, index, embedding):
            self.index, self.embedding = index, embedding

    class FakeClient:
        def __init__(self, outer):
            self.requests = []
            client = self

            class Embeddings:
                def create(self, model, input):
                    client.requests.append((model, list(input)))
                    return type("R", (), {"data": [outer.Item(i, [float(i)]) for i in reversed(range(len(input)))]})()

            self.embeddings = Embeddings()

    def test_sends_the_model_and_texts_and_puts_the_results_back_in_order(self):
        client = self.FakeClient(self)
        embed = lw.openai_embedder("text-embedding-3-small", client)
        assert embed(["a", "b", "c"]) == [[0.0], [1.0], [2.0]]
        assert client.requests == [("text-embedding-3-small", ["a", "b", "c"])]


# --------------------------------------------------------------------------- report and command line

class TestReport:
    def graph(self):
        return lw.build("Guide", 7, PAGE_HTML, "now", TOK, 300, 50)

    def test_shows_the_tree_with_indentation_and_counts(self):
        text = "\n".join(lw.report(self.graph(), "text-embedding-3-small"))
        assert "Guide  (revision 7, chunks of 300 tokens, overlap 50)" in text
        lines = text.split("\n")
        assert any(l.startswith("  Shelter  [h2]") for l in lines)            # top level: two spaces
        assert any(l.startswith("    Walls  [h3]") for l in lines)            # one level down: four
        assert any(l.startswith("    Bread  [h4]") for l in lines)            # indented by tree depth, not heading level: Food is its parent
        assert "6 sections, 5 chunks" in text

    def test_estimates_the_cost_for_known_models_only(self):
        assert "costs about $" in "\n".join(lw.report(self.graph(), "text-embedding-3-small"))
        assert "costs about" not in "\n".join(lw.report(self.graph(), "some-new-model"))

    def test_a_page_with_no_text_reports_without_error(self):
        g = lw.build("Empty", 1, "<div></div>", "now", TOK)
        assert lw.report(g, "text-embedding-3-small")[-1] == "0 sections, 0 chunks"


class TestMain:
    @pytest.fixture
    def cli(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lw, "get_settings", lambda: type("S", (), {"neo4j_uri": "bolt://x", "neo4j_user": "u", "neo4j_password": "p",
                                                                        "neo4j_database": "d"})())
        monkeypatch.setattr(lw, "TikTokenizer", WordTokenizer)
        monkeypatch.setattr(lw, "fetch_page", lambda title: {"title": title, "revisionId": 9, "html": PAGE_HTML})
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def run(*args):
            monkeypatch.setattr(sys, "argv", ["load_wiki.py", "--cache", str(tmp_path / "cache" / "e"), *args])
            return lw.main()

        return run

    def test_a_dry_run_reports_and_touches_nothing(self, cli, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(lw, "write", lambda *a, **k: pytest.fail("a dry run must not write to the database"))
        monkeypatch.setattr(lw, "openai_embedder", lambda *a, **k: pytest.fail("a dry run must not call OpenAI"))
        assert cli("--dry-run") == 0
        out = capsys.readouterr().out
        assert "6 sections, 5 chunks" in out
        assert not (tmp_path / "cache").exists(), "a dry run must not create the cache"

    def test_a_dry_run_says_how_much_is_already_cached(self, cli, monkeypatch, capsys, tmp_path):
        g = lw.build("Tutorial:Beginner's guide", 9, PAGE_HTML, "now", WordTokenizer())
        with lw.EmbeddingCache(tmp_path / "cache" / "e") as cache:
            for c in g["chunks"][:2]:
                cache.put(lw.DEFAULT_MODEL, c["textHash"], [1.0])
        assert cli("--dry-run") == 0
        assert "2 of 5 chunks are already in the embedding cache" in capsys.readouterr().out

    def test_the_default_page_is_the_beginners_guide(self, cli, monkeypatch, capsys):
        seen = []
        monkeypatch.setattr(lw, "fetch_page", lambda title: seen.append(title) or {"title": title, "revisionId": 1, "html": PAGE_HTML})
        cli("--dry-run")
        assert seen == ["Tutorial:Beginner's guide"]

    def test_a_url_is_accepted_for_the_page(self, cli, monkeypatch):
        seen = []
        monkeypatch.setattr(lw, "fetch_page", lambda title: seen.append(title) or {"title": title, "revisionId": 1, "html": PAGE_HTML})
        cli("--dry-run", "--page", "https://minecraft.wiki/w/Iron_Pickaxe")
        assert seen == ["Iron Pickaxe"]

    def test_size_and_overlap_are_options(self, cli, capsys):
        cli("--dry-run", "--size", "100", "--overlap", "10")
        assert "chunks of 100 tokens, overlap 10" in capsys.readouterr().out

    @pytest.mark.parametrize("args", [["--size", "10", "--overlap", "10"], ["--size", "10", "--overlap", "11"], ["--overlap", "-1"]])
    def test_a_bad_overlap_is_refused(self, cli, args):
        with pytest.raises(SystemExit, match="overlap"):
            cli("--dry-run", *args)

    def test_loading_needs_an_openai_key(self, cli):
        with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
            cli()

    def test_a_page_that_cannot_be_fetched_is_a_clean_exit(self, cli, monkeypatch):
        def missing(title):
            raise lw.WikiError("no such page")

        monkeypatch.setattr(lw, "fetch_page", missing)
        with pytest.raises(SystemExit, match="could not fetch the page: no such page"):
            cli("--dry-run")

    def test_a_full_run_embeds_through_the_cache_and_then_writes(self, cli, monkeypatch, capsys):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedder = fake_embedder()
        written = {}
        monkeypatch.setattr(lw, "openai_embedder", lambda model: embedder)
        monkeypatch.setattr(lw, "write", lambda g, model, uri, user, pw, db, wipe=False: written.update(g=g, model=model, db=db, wipe=wipe))
        assert cli("--wipe") == 0
        assert written["db"] == "d" and written["wipe"] is True and written["model"] == lw.DEFAULT_MODEL
        assert all(len(c["embedding"]) == 4 for c in written["g"]["chunks"])
        assert "0 chunks from the cache, 5 texts sent to OpenAI" in capsys.readouterr().out
        assert cli() == 0                                           # again: everything comes from the cache
        assert len(embedder.calls) == 1 and written["wipe"] is False
        assert "5 chunks from the cache, 0 texts sent to OpenAI" in capsys.readouterr().out
