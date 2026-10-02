package com.minecorp.plugin;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

class RepliesTest {

    @Test
    void aSingleLineStaysOneLine() {
        assertEquals(List.of("Smelt raw iron in a furnace."), Replies.lines("Smelt raw iron in a furnace."));
    }

    @Test
    void splitsOnUnixWindowsAndOldMacLineBreaks() {
        assertEquals(List.of("a", "b", "c"), Replies.lines("a\nb\nc"));
        assertEquals(List.of("a", "b", "c"), Replies.lines("a\r\nb\r\nc"));
        assertEquals(List.of("a", "b"), Replies.lines("a\rb"));
    }

    @Test
    void keepsLeadingSpacesAndBlankLinesInTheMiddle() {
        assertEquals(List.of("- one", "", "  - two"), Replies.lines("- one\n\n  - two"));
    }

    @Test
    void dropsTrailingEmptyLines() {
        assertEquals(List.of("a", "b"), Replies.lines("a\nb\n\n"));
    }

    @Test
    void anEmptyAnswerIsOneEmptyLine() {
        assertEquals(List.of(""), Replies.lines(""));
    }
}
