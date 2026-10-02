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
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Thin plugin: forwards /agent questions to the external MineCorp agent service and shows the reply.
 * The HTTP call runs off the main server thread, so a slow agent never stalls the game.
 */
public final class MineCorpPlugin extends JavaPlugin {

    private static final int MAX_QUESTION_LENGTH = 500;
    /** Stands in for the server console, which has no UUID. */
    private static final UUID CONSOLE_ID = new UUID(0L, 0L);

    private AgentClient agent;
    /** Senders with a question in flight; each may have only one at a time. */
    private final Set<UUID> inFlight = ConcurrentHashMap.newKeySet();

    @Override
    public void onEnable() {
        saveDefaultConfig();
        String secret = getConfig().getString("shared-secret", "");
        if (secret.isBlank()) {
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
        String question = String.join(" ", args).trim();
        if (question.isEmpty()) {
            sender.sendMessage(Component.text("Usage: /agent <question>", NamedTextColor.YELLOW));
            return true;
        }
        if (question.length() > MAX_QUESTION_LENGTH) {
            sender.sendMessage(Component.text(
                    "That question is too long (max " + MAX_QUESTION_LENGTH + " characters).", NamedTextColor.RED));
            return true;
        }
        if (getConfig().getString("shared-secret", "").isBlank()) {
            sender.sendMessage(Component.text("The agent is not configured yet. Ask an admin.", NamedTextColor.RED));
            return true;
        }

        Player player = sender instanceof Player p ? p : null;
        UUID id = player != null ? player.getUniqueId() : CONSOLE_ID;
        String name = player != null ? player.getName() : "Console";
        if (!inFlight.add(id)) {
            sender.sendMessage(Component.text("Still thinking about your last question...", NamedTextColor.YELLOW));
            return true;
        }

        sender.sendMessage(Component.text("MineCorp agent: thinking...", NamedTextColor.GRAY));
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            Component reply;
            try {
                String answer = agent.ask(id.toString(), name, id.toString(), question);
                reply = Component.empty();
                String[] lines = answer.split("\\R");
                for (int i = 0; i < lines.length; i++) {
                    reply = reply.append(Component.text(lines[i], NamedTextColor.GOLD));
                    if (i < lines.length - 1) {
                        reply = reply.append(Component.newline());
                    }
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                reply = Component.text("The agent request was interrupted.", NamedTextColor.RED);
            } catch (Exception e) {
                getLogger().warning("Agent request failed: " + e);
                reply = Component.text("The agent is not available right now. Try again later.", NamedTextColor.RED);
            } finally {
                inFlight.remove(id);
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
}
