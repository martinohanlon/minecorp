package com.minecorp.plugin;

import org.bukkit.configuration.file.YamlConfiguration;
import org.bukkit.plugin.java.JavaPlugin;
import org.junit.jupiter.api.Test;

import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Checks plugin.yml and config.yml as they will be packaged, without starting a server. */
class PluginDescriptorTest {

    private static YamlConfiguration load(String resource) throws Exception {
        try (InputStream in = PluginDescriptorTest.class.getResourceAsStream("/" + resource)) {
            assertNotNull(in, resource + " is missing from the plugin");
            return YamlConfiguration.loadConfiguration(new InputStreamReader(in, StandardCharsets.UTF_8));
        }
    }

    @Test
    void nameAndMainClass() throws Exception {
        YamlConfiguration yml = load("plugin.yml");
        assertEquals("MineCorp", yml.getString("name"));
        Class<?> main = Class.forName(yml.getString("main"), false, getClass().getClassLoader());
        assertTrue(JavaPlugin.class.isAssignableFrom(main), "main must extend JavaPlugin");
        assertEquals(MineCorpPlugin.class, main);
    }

    @Test
    void versionIsFilledInFromTheBuild() throws Exception {
        String version = load("plugin.yml").getString("version");
        assertEquals(System.getProperty("projectVersion"), version);
        assertTrue(version.matches("\\d+\\.\\d+\\.\\d+.*"), "version should look like 1.2.3, got " + version);
    }

    @Test
    void apiVersionMatchesTheMinecraftVersionOfThePaperApi() throws Exception {
        String api = System.getProperty("paperApiVersion");           // for example 26.2.build.129-stable
        String[] parts = api.split("\\.");
        assertEquals(parts[0] + "." + parts[1], load("plugin.yml").getString("api-version"));
    }

    @Test
    void theAgentCommandIsDeclaredWithItsPermission() throws Exception {
        YamlConfiguration yml = load("plugin.yml");
        assertNotNull(yml.getConfigurationSection("commands.agent"), "command /agent is not declared");
        assertEquals("minecorp.agent", yml.getString("commands.agent.permission"));
        assertEquals("/agent <question>", yml.getString("commands.agent.usage"));
        assertTrue(yml.getString("commands.agent.description", "").length() > 5);
    }

    @Test
    void thePermissionIsGrantedToEveryoneByDefault() throws Exception {
        YamlConfiguration yml = load("plugin.yml");
        assertNotNull(yml.getConfigurationSection("permissions.minecorp.agent"));
        assertEquals("true", String.valueOf(yml.get("permissions.minecorp.agent.default")));
    }

    @Test
    void theDeclaredCommandsAreAgentAndAgentExplain() throws Exception {
        assertEquals(java.util.Set.of("agent", "agent-explain"), load("plugin.yml").getConfigurationSection("commands").getKeys(false));
    }

    @Test
    void theExplainCommandHasItsOwnPermissionGrantedToEveryone() throws Exception {
        YamlConfiguration yml = load("plugin.yml");
        assertEquals("minecorp.explain", yml.getString("commands.agent-explain.permission"));
        assertEquals("/agent-explain", yml.getString("commands.agent-explain.usage"));
        assertEquals("true", String.valueOf(yml.get("permissions.minecorp.explain.default")));
    }

    @Test
    void defaultConfigPointsAtLocalhostAndShipsNoSecret() throws Exception {
        YamlConfiguration cfg = load("config.yml");
        assertEquals("http://127.0.0.1:8080", cfg.getString("agent-url"));
        assertEquals("", cfg.getString("shared-secret"), "the packaged config must never contain a real secret");
        assertTrue(cfg.getLong("timeout-seconds") > 0);
    }
}
