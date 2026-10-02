package com.minecorp.plugin;

import java.util.Arrays;
import java.util.List;

/** Turns the agent's answer into the lines shown in chat. No Minecraft types, so it can be unit tested. */
final class Replies {

    private Replies() {
    }

    /** The answer split on any line break (\n, \r\n, \r); trailing empty lines are dropped. */
    static List<String> lines(String answer) {
        return Arrays.asList(answer.split("\\R"));
    }
}
