package com.minecorp.plugin;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/** Minimal HTTP client for the MineCorp agent service (POST /ask). Blocking: call off the main thread. */
final class AgentClient {

    private static final Gson GSON = new Gson();

    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();
    private final URI askUri;
    private final URI explainUri;
    private final URI clearUri;
    private final String sharedSecret;
    private final Duration timeout;

    AgentClient(String baseUrl, String sharedSecret, Duration timeout) {
        String base = baseUrl.replaceAll("/+$", "");
        this.askUri = URI.create(base + "/ask");
        this.explainUri = URI.create(base + "/explain");
        this.clearUri = URI.create(base + "/clear");
        this.sharedSecret = sharedSecret;
        this.timeout = timeout;
    }

    /**
     * Sends a question and returns the answer text. Throws IOException if the agent is unreachable, times out,
     * returns a non-200 status, or returns a body that is not JSON with an "answer" string.
     */
    String ask(String playerUuid, String playerName, String sessionId, String question)
            throws IOException, InterruptedException {
        JsonObject body = new JsonObject();
        body.addProperty("playerUuid", playerUuid);
        body.addProperty("playerName", playerName);
        body.addProperty("sessionId", sessionId);
        body.addProperty("question", question);

        JsonObject json = post(askUri, body);
        if (json == null || !json.has("answer") || !json.get("answer").isJsonPrimitive()) {
            throw new IOException("Agent reply had no answer");
        }
        return json.get("answer").getAsString();
    }

    /**
     * Asks for the book of this player's last answers and their sources (POST /explain). The service keeps the history and
     * builds the pages; the book has no pages when the player has asked nothing yet. Same failures as {@link #ask}.
     */
    ExplainBook explain(String playerUuid) throws IOException, InterruptedException {
        JsonObject body = new JsonObject();
        body.addProperty("playerUuid", playerUuid);
        return ExplainBook.parse(post(explainUri, body));
    }

    /** Starts a new chat for this player (POST /clear): the service forgets what they asked. Same failures as {@link #ask}. */
    void clear(String playerUuid) throws IOException, InterruptedException {
        JsonObject body = new JsonObject();
        body.addProperty("playerUuid", playerUuid);
        post(clearUri, body);
    }

    private JsonObject post(URI uri, JsonObject body) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(uri)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-MineCorp-Key", sharedSecret)
                .POST(HttpRequest.BodyPublishers.ofString(GSON.toJson(body)))
                .build();

        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IOException("Agent returned HTTP " + response.statusCode());
        }
        try {
            return GSON.fromJson(response.body(), JsonObject.class);
        } catch (JsonParseException e) {
            throw new IOException("Agent reply was not valid JSON", e);
        }
    }
}
