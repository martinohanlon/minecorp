package com.minecorp.plugin;

import com.google.gson.Gson;
import com.google.gson.JsonObject;

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
    private final String sharedSecret;
    private final Duration timeout;

    AgentClient(String baseUrl, String sharedSecret, Duration timeout) {
        this.askUri = URI.create(baseUrl.replaceAll("/+$", "") + "/ask");
        this.sharedSecret = sharedSecret;
        this.timeout = timeout;
    }

    /** Sends a question and returns the answer text. Throws if the agent is unreachable or returns an error. */
    String ask(String playerUuid, String playerName, String sessionId, String question)
            throws IOException, InterruptedException {
        JsonObject body = new JsonObject();
        body.addProperty("playerUuid", playerUuid);
        body.addProperty("playerName", playerName);
        body.addProperty("sessionId", sessionId);
        body.addProperty("question", question);

        HttpRequest request = HttpRequest.newBuilder(askUri)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-MineCorp-Key", sharedSecret)
                .POST(HttpRequest.BodyPublishers.ofString(GSON.toJson(body)))
                .build();

        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IOException("Agent returned HTTP " + response.statusCode());
        }
        JsonObject json = GSON.fromJson(response.body(), JsonObject.class);
        return json.get("answer").getAsString();
    }
}
