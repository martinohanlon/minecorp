package com.minecorp.plugin;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
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
public final class MineCorpPlugin extends JavaPlugin implements Listener {

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
        getServer().getPluginManager().registerEvents(this, this);
        getLogger().info("MineCorp plugin enabled (agent: " + getConfig().getString("agent-url") + ")");
    }

    /**
     * A player who joins starts a new chat: the agent opens a new conversation for them, so questions from an earlier visit are not
     * used to understand this one. Quiet on failure: the player did nothing, and the next question works either way.
     */
    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        if (!configured) {
            return;
        }
        UUID id = event.getPlayer().getUniqueId();
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            try {
                agent.clear(id.toString());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } catch (Exception e) {
                getLogger().fine("Could not start a new chat for " + id + ": " + e);
            }
        });
    }

    @Override
    public void onDisable() {
        getLogger().info("MineCorp plugin disabled");
    }

    @Override
    public boolean onCommand(@NotNull CommandSender sender, @NotNull Command command,
                             @NotNull String label, @NotNull String[] args) {
        if (command.getName().equalsIgnoreCase("agent-explain")) {
            return explain(sender);
        }
        if (command.getName().equalsIgnoreCase("agent-clear")) {
            return clear(sender);
        }
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

    /**
     * /agent-explain: asks the service for the player's book (their last answers and the sources behind them) and opens it.
     * The service keeps the history and lays out the pages; the book is opened, not given as an item.
     */
    private boolean explain(CommandSender sender) {
        if (!(sender instanceof Player player)) {
            sender.sendMessage(Component.text("Only a player can open the notes book.", NamedTextColor.RED));
            return true;
        }
        if (!configured) {
            sender.sendMessage(Component.text("The agent is not configured yet. Ask an admin.", NamedTextColor.RED));
            return true;
        }
        UUID id = player.getUniqueId();
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            ExplainBook book = null;
            Component failure = null;
            try {
                book = agent.explain(id.toString());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                failure = Component.text("The agent request was interrupted.", NamedTextColor.RED);
            } catch (Exception e) {
                getLogger().warning("Agent explain request failed: " + e);
                failure = Component.text("The agent is not available right now. Try again later.", NamedTextColor.RED);
            }
            ExplainBook result = book;
            Component message = failure;
            Bukkit.getScheduler().runTask(this, () -> {
                Player target = Bukkit.getPlayer(id);
                if (target == null) {
                    return;
                }
                if (message != null) {
                    target.sendMessage(message);
                } else if (result.isEmpty()) {
                    target.sendMessage(Component.text("Nothing to show yet. Ask something with /agent <question> first.", NamedTextColor.YELLOW));
                } else {
                    target.openBook(BookRender.book(result));
                }
            });
        });
        return true;
    }

    /**
     * /agent-clear: starts a new chat. The service forgets what the player asked, so it is no longer used to understand their
     * follow-up questions or shown in their book.
     */
    private boolean clear(CommandSender sender) {
        if (!(sender instanceof Player player)) {
            sender.sendMessage(Component.text("Only a player has a chat to clear.", NamedTextColor.RED));
            return true;
        }
        if (!configured) {
            sender.sendMessage(Component.text("The agent is not configured yet. Ask an admin.", NamedTextColor.RED));
            return true;
        }
        UUID id = player.getUniqueId();
        Bukkit.getScheduler().runTaskAsynchronously(this, () -> {
            Component message;
            try {
                agent.clear(id.toString());
                message = Component.text("Chat cleared. Your next question starts a new chat.", NamedTextColor.YELLOW);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                message = Component.text("The agent request was interrupted.", NamedTextColor.RED);
            } catch (Exception e) {
                getLogger().warning("Agent clear request failed: " + e);
                message = Component.text("The agent is not available right now. Try again later.", NamedTextColor.RED);
            }
            Component text = message;
            Bukkit.getScheduler().runTask(this, () -> {
                Player target = Bukkit.getPlayer(id);
                if (target != null) {
                    target.sendMessage(text);
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
