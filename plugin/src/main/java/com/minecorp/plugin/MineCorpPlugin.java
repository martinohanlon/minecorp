package com.minecorp.plugin;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;
import org.jetbrains.annotations.NotNull;

import java.time.Duration;
import java.util.List;
import java.util.UUID;

/**
 * Thin plugin: forwards /agent questions to the external MineCorp agent service and shows the reply.
 * The HTTP call runs off the main server thread, so a slow agent never stalls the game.
 * The rules live in {@link AskGuard} and {@link Replies}; this class only connects them to Bukkit.
 */
public final class MineCorpPlugin extends JavaPlugin {

    /** Stands in for the server console, which has no UUID. */
    private static final UUID CONSOLE_ID = new UUID(0L, 0L);

    private final AskGuard guard = new AskGuard();
    private AgentClient agent;
    private boolean configured;

    @Override
    public void onEnable() {
        saveDefaultConfig();
        String secret = getConfig().getString("shared-secret", "");
        configured = !secret.isBlank();
        if (!configured) {
            getLogger().warning("shared-secret is not set in config.yml; /agent will not work until it is.");
        }
        agent = new AgentClient(
                getConfig().getString("agent-url", "http://127.0.0.1:8080"),
                secret,
                Duration.ofSeconds(getConfig().getLong("timeout-seconds", 30)));
        getLogger().info("MineCorp plugin enabled (agent: " + getConfig().getString("agent-url") + ")");
    }

    @Override
    public void onDisable() {
        getLogger().info("MineCorp plugin disabled");
    }

    @Override
    public boolean onCommand(@NotNull CommandSender sender, @NotNull Command command,
                             @NotNull String label, @NotNull String[] args) {
        if (!command.getName().equalsIgnoreCase("agent")) {
            return false;
        }
        Player player = sender instanceof Player p ? p : null;
        UUID id = player != null ? player.getUniqueId() : CONSOLE_ID;
        String name = player != null ? player.getName() : "Console";
        String question = AskGuard.question(args);

        switch (guard.begin(id, question, configured)) {
            case USAGE -> {
                sender.sendMessage(Component.text("Usage: /agent <question>", NamedTextColor.YELLOW));
                return true;
            }
            case TOO_LONG -> {
                sender.sendMessage(Component.text("That question is too long (max "
                        + AskGuard.MAX_QUESTION_LENGTH + " characters).", NamedTextColor.RED));
                return true;
            }
            case NOT_CONFIGURED -> {
                sender.sendMessage(Component.text("The agent is not configured yet. Ask an admin.", NamedTextColor.RED));
                return true;
            }
            case BUSY -> {
                sender.sendMessage(Component.text("Still thinking about your last question...", NamedTextColor.YELLOW));
                return true;
            }
            case ACCEPTED -> {
                // continue below
            }
        }

        sender.sendMessage(Component.text("MineCorp agent: thinking...", NamedTextColor.GRAY));
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            Component reply;
            try {
                reply = format(agent.ask(id.toString(), name, id.toString(), question));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                reply = Component.text("The agent request was interrupted.", NamedTextColor.RED);
            } catch (Exception e) {
                getLogger().warning("Agent request failed: " + e);
                reply = Component.text("The agent is not available right now. Try again later.", NamedTextColor.RED);
            } finally {
                guard.finish(id);
            }
            Component message = reply;
            // Reply on the main thread; the player may have logged out in the meantime.
            Bukkit.getScheduler().runTask(this, () -> {
                CommandSender target = player == null ? sender : Bukkit.getPlayer(id);
                if (target != null) {
                    target.sendMessage(message);
                }
            });
        });
        return true;
    }

    /** The answer as gold chat lines, one component line per line of text. */
    private static Component format(String answer) {
        List<String> lines = Replies.lines(answer);
        Component text = Component.empty();
        for (int i = 0; i < lines.size(); i++) {
            text = text.append(Component.text(lines.get(i), NamedTextColor.GOLD));
            if (i < lines.size() - 1) {
                text = text.append(Component.newline());
            }
        }
        return text;
    }
}
