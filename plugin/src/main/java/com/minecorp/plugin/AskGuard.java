package com.minecorp.plugin;

import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The rules for accepting a question, with no Minecraft types so they can be unit tested:
 * a question is required, has a length limit, needs the plugin to be configured, and each sender
 * may have only one question in flight at a time.
 */
final class AskGuard {

    static final int MAX_QUESTION_LENGTH = 500;

    /** What to do with a question, in the order the rules are checked. */
    enum Outcome { ACCEPTED, USAGE, TOO_LONG, NOT_CONFIGURED, BUSY }

    private final Set<UUID> inFlight = ConcurrentHashMap.newKeySet();

    /** The question text from the command arguments: joined with spaces and trimmed. */
    static String question(String[] args) {
        return String.join(" ", args).trim();
    }

    /**
     * Decides whether a question can start. On ACCEPTED the sender is marked busy and the caller must call
     * {@link #finish(UUID)} when the answer (or failure) is in.
     */
    Outcome begin(UUID sender, String question, boolean configured) {
        if (question.isEmpty()) {
            return Outcome.USAGE;
        }
        if (question.length() > MAX_QUESTION_LENGTH) {
            return Outcome.TOO_LONG;
        }
        if (!configured) {
            return Outcome.NOT_CONFIGURED;
        }
        return inFlight.add(sender) ? Outcome.ACCEPTED : Outcome.BUSY;
    }

    /** Releases the sender so they can ask again. Safe to call twice. */
    void finish(UUID sender) {
        inFlight.remove(sender);
    }

    boolean isBusy(UUID sender) {
        return inFlight.contains(sender);
    }
}
