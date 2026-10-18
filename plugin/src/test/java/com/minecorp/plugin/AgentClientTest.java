package com.minecorp.plugin;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Runs the real client against a small local HTTP server standing in for the agent service. */
class AgentClientTest {

    private HttpServer server;
    private final AtomicReference<String> method = new AtomicReference<>();
    private final AtomicReference<String> path = new AtomicReference<>();
    private final AtomicReference<String> key = new AtomicReference<>();
    private final AtomicReference<String> contentType = new AtomicReference<>();
    private final AtomicReference<String> body = new AtomicReference<>();

    private volatile int status = 200;
    private volatile String reply = "{\"requestId\":\"abc\",\"answer\":\"Smelt raw iron.\"}";
    private volatile long delayMillis = 0;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    private void handle(HttpExchange ex) throws IOException {
        method.set(ex.getRequestMethod());
        path.set(ex.getRequestURI().getPath());
        key.set(ex.getRequestHeaders().getFirst("X-MineCorp-Key"));
        contentType.set(ex.getRequestHeaders().getFirst("Content-Type"));
        body.set(new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
        try {
            Thread.sleep(delayMillis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        byte[] out = reply.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, out.length);
        ex.getResponseBody().write(out);
        ex.close();
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private AgentClient client(String base, Duration timeout) {
        return new AgentClient(base, "secret-123", timeout);
    }

    @Test
    void returnsTheAnswerText() throws Exception {
        assertEquals("Smelt raw iron.", client(baseUrl(), Duration.ofSeconds(5)).ask("u1", "Steve", "s1", "how?"));
    }

    @Test
    void postsJsonToAskWithTheSharedSecret() throws Exception {
        client(baseUrl(), Duration.ofSeconds(5)).ask("11111111-2222-3333-4444-555555555555", "Steve", "session-1", "How do I craft a pickaxe?");
        assertEquals("POST", method.get());
        assertEquals("/ask", path.get());
        assertEquals("secret-123", key.get());
        assertTrue(contentType.get().startsWith("application/json"));
        JsonObject sent = new Gson().fromJson(body.get(), JsonObject.class);
        assertEquals("11111111-2222-3333-4444-555555555555", sent.get("playerUuid").getAsString());
        assertEquals("Steve", sent.get("playerName").getAsString());
        assertEquals("session-1", sent.get("sessionId").getAsString());
        assertEquals("How do I craft a pickaxe?", sent.get("question").getAsString());
        assertEquals(4, sent.size());
    }

    @Test
    void escapesQuotesAndNewlinesInTheQuestion() throws Exception {
        String question = "say \"hi\"\nthen \\ done é";
        client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", question);
        assertEquals(question, new Gson().fromJson(body.get(), JsonObject.class).get("question").getAsString());
    }

    @Test
    void aTrailingSlashOnTheBaseUrlIsIgnored() throws Exception {
        client(baseUrl() + "/", Duration.ofSeconds(5)).ask("u", "n", "s", "q");
        assertEquals("/ask", path.get());
        client(baseUrl() + "///", Duration.ofSeconds(5)).ask("u", "n", "s", "q");
        assertEquals("/ask", path.get());
    }

    @Test
    void anAnswerWithSeveralLinesIsReturnedWhole() throws Exception {
        reply = "{\"requestId\":\"x\",\"answer\":\"- one\\n- two\"}";
        assertEquals("- one\n- two", client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
    }

    @Test
    void aNon200StatusIsAnError() {
        status = 401;
        reply = "{\"detail\":\"unauthorized\"}";
        IOException e = assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
        assertTrue(e.getMessage().contains("401"));
    }

    @Test
    void aServerErrorIsAnError() {
        status = 500;
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
    }

    @Test
    void aBodyThatIsNotJsonIsAnIoError() {
        reply = "<html>oops</html>";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
    }

    @Test
    void jsonWithoutAnAnswerIsAnIoError() {
        reply = "{\"requestId\":\"x\"}";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
    }

    @Test
    void anAnswerThatIsNotAStringIsAnIoError() {
        reply = "{\"answer\":{\"text\":\"nested\"}}";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
    }

    @Test
    void anEmptyBodyIsAnIoError() {
        reply = "";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).ask("u", "n", "s", "q"));
    }

    @Test
    void aSlowAgentTimesOut() {
        delayMillis = 1500;
        long started = System.nanoTime();
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofMillis(300)).ask("u", "n", "s", "q"));
        long tookMillis = (System.nanoTime() - started) / 1_000_000;
        assertTrue(tookMillis < 1400, "should give up near the timeout, not wait for the reply (took " + tookMillis + " ms)");
    }

    private static final String BOOK = "{\"title\":\"Agent notes\",\"author\":\"MineCorp Agent\",\"pages\":["
            + "{\"lines\":[{\"text\":\"Agent notes\"},{\"text\":\"1. how?\",\"goto\":2}]},"
            + "{\"lines\":[{\"text\":\"- wiki: Tutorial:Nether portals\",\"url\":\"https://minecraft.wiki/w/Tutorial:Nether_portals\"}]}]}";

    @Test
    void explainPostsThePlayerUuidToExplainWithTheSharedSecret() throws Exception {
        reply = BOOK;
        client(baseUrl(), Duration.ofSeconds(5)).explain("11111111-2222-3333-4444-555555555555");
        assertEquals("POST", method.get());
        assertEquals("/explain", path.get());
        assertEquals("secret-123", key.get());
        assertTrue(contentType.get().startsWith("application/json"));
        JsonObject sent = new Gson().fromJson(body.get(), JsonObject.class);
        assertEquals("11111111-2222-3333-4444-555555555555", sent.get("playerUuid").getAsString());
        assertEquals(1, sent.size());
    }

    @Test
    void explainReturnsTheBook() throws Exception {
        reply = BOOK;
        ExplainBook book = client(baseUrl(), Duration.ofSeconds(5)).explain("u");
        assertEquals(2, book.pages().size());
        assertEquals(2, book.pages().get(0).get(1).jumpTo());
        assertEquals("https://minecraft.wiki/w/Tutorial:Nether_portals", book.pages().get(1).get(0).url());
    }

    @Test
    void explainAcceptsABookWithNoPages() throws Exception {
        reply = "{\"title\":\"Agent notes\",\"author\":\"MineCorp Agent\",\"pages\":[]}";
        assertTrue(client(baseUrl(), Duration.ofSeconds(5)).explain("u").isEmpty());
    }

    @Test
    void explainFailsLikeAskDoes() {
        status = 401;
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).explain("u"));
        status = 200;
        reply = "<html>oops</html>";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).explain("u"));
        reply = "{\"answer\":\"not a book\"}";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).explain("u"));
    }

    @Test
    void aTrailingSlashOnTheBaseUrlIsIgnoredForExplainToo() throws Exception {
        reply = BOOK;
        client(baseUrl() + "//", Duration.ofSeconds(5)).explain("u");
        assertEquals("/explain", path.get());
    }

    @Test
    void clearPostsThePlayerUuidToClearWithTheSharedSecret() throws Exception {
        reply = "{\"cleared\":true}";
        client(baseUrl(), Duration.ofSeconds(5)).clear("11111111-2222-3333-4444-555555555555");
        assertEquals("POST", method.get());
        assertEquals("/clear", path.get());
        assertEquals("secret-123", key.get());
        JsonObject sent = new Gson().fromJson(body.get(), JsonObject.class);
        assertEquals("11111111-2222-3333-4444-555555555555", sent.get("playerUuid").getAsString());
        assertEquals(1, sent.size());
    }

    @Test
    void clearFailsLikeAskDoes() {
        status = 401;
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).clear("u"));
        status = 200;
        reply = "<html>oops</html>";
        assertThrows(IOException.class, () -> client(baseUrl(), Duration.ofSeconds(5)).clear("u"));
    }

    @Test
    void anAgentThatIsNotRunningIsAnIoError() {
        int closedPort = server.getAddress().getPort();
        server.stop(0);
        assertThrows(IOException.class, () -> client("http://127.0.0.1:" + closedPort, Duration.ofSeconds(2)).ask("u", "n", "s", "q"));
        assertNull(null); // the server is stopped a second time in @AfterEach; stopping twice is harmless
    }
}
