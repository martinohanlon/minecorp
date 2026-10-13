package com.minecorp.plugin;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ExplainBookTest {

    private static ExplainBook parse(String json) throws IOException {
        return ExplainBook.parse(new Gson().fromJson(json, JsonObject.class));
    }

    private static String book(String lines, int pages) {
        return "{\"pages\":[" + ("{\"lines\":[" + lines + "]},").repeat(pages).replaceAll(",$", "") + "]}";
    }

    @Test
    void titleAndAuthorComeFromTheReplyAndHaveDefaults() throws Exception {
        ExplainBook given = parse("{\"title\":\"T\",\"author\":\"A\",\"pages\":[]}");
        assertEquals("T", given.title());
        assertEquals("A", given.author());
        ExplainBook plain = parse("{\"pages\":[]}");
        assertEquals("Agent notes", plain.title());
        assertEquals("MineCorp Agent", plain.author());
        assertTrue(plain.isEmpty());
    }

    @Test
    void linesKeepTheirTextInOrderIncludingBlankOnes() throws Exception {
        ExplainBook b = parse(book("{\"text\":\"one\"},{\"text\":\"\"},{\"text\":\"three\"}", 1));
        assertEquals(java.util.List.of("one", "", "three"), b.pages().get(0).stream().map(ExplainBook.Line::text).toList());
        assertFalse(b.isEmpty());
    }

    @Test
    void aWebAddressIsKeptAndAnythingElseIsDropped() throws Exception {
        String[] kept = {"https://minecraft.wiki/w/X#Y", "http://example.com/a", "HTTPS://minecraft.wiki/w/X"};
        for (String url : kept) {
            assertEquals(url, parse(book("{\"text\":\"l\",\"url\":\"" + url + "\"}", 1)).pages().get(0).get(0).url(), url);
        }
        String[] dropped = {"javascript:alert(1)", "file:///etc/passwd", "ftp://x.org/f", "https://", "https://a b.com", "minecraft.wiki", "run_command:/op me", ""};
        for (String url : dropped) {
            assertNull(parse(book("{\"text\":\"l\",\"url\":\"" + url + "\"}", 1)).pages().get(0).get(0).url(), url);
        }
    }

    @Test
    void aJumpMustBeAPageThatExists() throws Exception {
        ExplainBook b = parse(book("{\"text\":\"a\",\"goto\":1},{\"text\":\"b\",\"goto\":2},{\"text\":\"c\",\"goto\":3},{\"text\":\"d\",\"goto\":0},{\"text\":\"e\",\"goto\":-1},{\"text\":\"f\",\"goto\":\"2\"}", 2));
        var jumps = b.pages().get(0).stream().map(ExplainBook.Line::jumpTo).toList();
        assertEquals(java.util.Arrays.asList(1, 2, null, null, null, null), jumps);
    }

    @Test
    void aLineWithoutALinkOrJumpHasNeither() throws Exception {
        ExplainBook.Line line = parse(book("{\"text\":\"plain\"}", 1)).pages().get(0).get(0);
        assertNull(line.url());
        assertNull(line.jumpTo());
    }

    @Test
    void aMalformedBookIsAnIoError() {
        String[] bad = {"{}", "{\"pages\":\"x\"}", "{\"pages\":[1]}", "{\"pages\":[{}]}", "{\"pages\":[{\"lines\":\"x\"}]}", "{\"pages\":[{\"lines\":[\"text\"]}]}"};
        for (String json : bad) {
            assertThrows(IOException.class, () -> parse(json), json);
        }
        assertThrows(IOException.class, () -> ExplainBook.parse(null));
    }
}
