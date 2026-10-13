package com.minecorp.plugin;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * The book the agent service builds for POST /explain, as plain data (no Minecraft types, so it can be unit tested).
 * The service owns the layout; this only reads it safely. A line may carry a link to open ({@code url}, http or https
 * only: the game ignores any other kind) or a page to jump to ({@code goto}, counted from 1 and inside the book).
 */
record ExplainBook(String title, String author, List<List<Line>> pages) {

    record Line(String text, String url, Integer jumpTo) {
    }

    /** True when the player has asked nothing yet. */
    boolean isEmpty() {
        return pages.isEmpty();
    }

    /** Reads the reply body; throws IOException when it is not a book. */
    static ExplainBook parse(JsonObject json) throws IOException {
        if (json == null || !json.has("pages") || !json.get("pages").isJsonArray()) {
            throw new IOException("Agent reply had no pages");
        }
        JsonArray rawPages = json.getAsJsonArray("pages");
        List<List<Line>> pages = new ArrayList<>();
        for (JsonElement rawPage : rawPages) {
            pages.add(page(rawPage, rawPages.size()));
        }
        return new ExplainBook(text(json, "title", "Agent notes"), text(json, "author", "MineCorp Agent"), pages);
    }

    private static List<Line> page(JsonElement rawPage, int pageCount) throws IOException {
        if (!rawPage.isJsonObject() || !rawPage.getAsJsonObject().has("lines")
                || !rawPage.getAsJsonObject().get("lines").isJsonArray()) {
            throw new IOException("Agent reply had a page without lines");
        }
        List<Line> lines = new ArrayList<>();
        for (JsonElement rawLine : rawPage.getAsJsonObject().getAsJsonArray("lines")) {
            if (!rawLine.isJsonObject()) {
                throw new IOException("Agent reply had a line that is not an object");
            }
            JsonObject line = rawLine.getAsJsonObject();
            String text = text(line, "text", "");
            String url = text(line, "url", null);
            Integer jumpTo = line.has("goto") && line.get("goto").isJsonPrimitive() && line.get("goto").getAsJsonPrimitive().isNumber()
                    ? line.get("goto").getAsInt() : null;
            lines.add(new Line(
                    text,
                    isWebAddress(url) ? url : null,
                    jumpTo != null && jumpTo >= 1 && jumpTo <= pageCount ? jumpTo : null));
        }
        return lines;
    }

    private static String text(JsonObject json, String key, String fallback) {
        JsonElement value = json.get(key);
        return value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isString() ? value.getAsString() : fallback;
    }

    static boolean isWebAddress(String url) {
        if (url == null) {
            return false;
        }
        String lower = url.toLowerCase(java.util.Locale.ROOT);
        return (lower.startsWith("http://") || lower.startsWith("https://")) && url.length() > 8 && url.indexOf(' ') < 0;
    }
}
