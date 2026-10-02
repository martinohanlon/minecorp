package com.minecorp.plugin;

import com.minecorp.plugin.AskGuard.Outcome;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AskGuardTest {

    private final UUID alex = UUID.randomUUID();
    private final UUID sam = UUID.randomUUID();

    @Test
    void questionJoinsAndTrimsTheArguments() {
        assertEquals("how do I make a pickaxe?", AskGuard.question(new String[]{"how", "do", "I", "make", "a", "pickaxe?"}));
        assertEquals("hello", AskGuard.question(new String[]{" hello "}));
        assertEquals("", AskGuard.question(new String[]{}));
        assertEquals("", AskGuard.question(new String[]{" ", "  "}));
    }

    @Test
    void anEmptyQuestionShowsTheUsage() {
        assertEquals(Outcome.USAGE, new AskGuard().begin(alex, "", true));
    }

    @Test
    void aQuestionAtTheLimitIsAcceptedAndOneOverIsTooLong() {
        AskGuard guard = new AskGuard();
        assertEquals(Outcome.ACCEPTED, guard.begin(alex, "x".repeat(AskGuard.MAX_QUESTION_LENGTH), true));
        guard.finish(alex);
        assertEquals(Outcome.TOO_LONG, guard.begin(alex, "x".repeat(AskGuard.MAX_QUESTION_LENGTH + 1), true));
        assertFalse(guard.isBusy(alex), "a rejected question must not leave the sender busy");
    }

    @Test
    void theLimitIsFiveHundredCharacters() {
        assertEquals(500, AskGuard.MAX_QUESTION_LENGTH);
    }

    @Test
    void notConfiguredIsReportedAndDoesNotMarkTheSenderBusy() {
        AskGuard guard = new AskGuard();
        assertEquals(Outcome.NOT_CONFIGURED, guard.begin(alex, "hello", false));
        assertFalse(guard.isBusy(alex));
        assertEquals(Outcome.ACCEPTED, guard.begin(alex, "hello", true));
    }

    @Test
    void rulesAreCheckedInOrderUsageThenLengthThenConfiguration() {
        AskGuard guard = new AskGuard();
        assertEquals(Outcome.USAGE, guard.begin(alex, "", false));
        assertEquals(Outcome.TOO_LONG, guard.begin(alex, "x".repeat(501), false));
    }

    @Test
    void aSenderCanAskOnlyOneQuestionAtATime() {
        AskGuard guard = new AskGuard();
        assertEquals(Outcome.ACCEPTED, guard.begin(alex, "first", true));
        assertTrue(guard.isBusy(alex));
        assertEquals(Outcome.BUSY, guard.begin(alex, "second", true));
    }

    @Test
    void otherSendersAreNotAffected() {
        AskGuard guard = new AskGuard();
        assertEquals(Outcome.ACCEPTED, guard.begin(alex, "first", true));
        assertEquals(Outcome.ACCEPTED, guard.begin(sam, "mine", true));
    }

    @Test
    void finishingReleasesTheSender() {
        AskGuard guard = new AskGuard();
        guard.begin(alex, "first", true);
        guard.finish(alex);
        assertFalse(guard.isBusy(alex));
        assertEquals(Outcome.ACCEPTED, guard.begin(alex, "second", true));
    }

    @Test
    void finishingTwiceIsHarmless() {
        AskGuard guard = new AskGuard();
        guard.begin(alex, "first", true);
        guard.finish(alex);
        guard.finish(alex);
        assertEquals(Outcome.ACCEPTED, guard.begin(alex, "second", true));
    }

    @Test
    void aBusySenderIsStillToldAboutABadQuestionFirst() {
        AskGuard guard = new AskGuard();
        guard.begin(alex, "first", true);
        assertEquals(Outcome.USAGE, guard.begin(alex, "", true));
        assertTrue(guard.isBusy(alex), "a usage message must not release the question already running");
    }

    @Test
    void onlyOneOfManyConcurrentAttemptsFromTheSameSenderIsAccepted() throws Exception {
        AskGuard guard = new AskGuard();
        int threads = 16;
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch go = new CountDownLatch(1);
        List<Future<Outcome>> results = new ArrayList<>();
        for (int i = 0; i < threads; i++) {
            results.add(pool.submit(() -> {
                go.await();
                return guard.begin(alex, "same time", true);
            }));
        }
        go.countDown();
        int accepted = 0;
        for (Future<Outcome> f : results) {
            if (f.get() == Outcome.ACCEPTED) {
                accepted++;
            }
        }
        pool.shutdown();
        assertEquals(1, accepted);
    }
}
