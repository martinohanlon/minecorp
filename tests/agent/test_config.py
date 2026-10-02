"""Unit tests for agent/minecorp_agent/config.py: reading .env and building the settings."""
from __future__ import annotations

import pytest

from minecorp_agent import config

KEYS = ["AGENT_SHARED_SECRET", "AGENT_HOST", "AGENT_PORT", "OPENAI_MODEL", "NEO4J_URI", "NEO4J_USER",
        "NEO4J_PASSWORD", "NEO4J_DATABASE", "AGENT_MAX_TURNS", "AGENT_LLM_TIMEOUT", "AGENT_TRACE_DIR"]


@pytest.fixture
def clean_env(monkeypatch):
    """No settings in the environment, and the real .env is not read."""
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    return monkeypatch


class TestLoadDotenv:
    def test_sets_missing_variables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("T_ONE", raising=False)
        f = tmp_path / ".env"
        f.write_text("T_ONE=hello\n", encoding="utf-8")
        config.load_dotenv(f)
        import os
        assert os.environ["T_ONE"] == "hello"
        monkeypatch.delenv("T_ONE")

    def test_never_overrides_a_real_environment_variable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("T_TWO", "real")
        f = tmp_path / ".env"
        f.write_text("T_TWO=from-file\n", encoding="utf-8")
        config.load_dotenv(f)
        import os
        assert os.environ["T_TWO"] == "real"

    def test_ignores_comments_blank_lines_and_lines_without_equals(self, tmp_path, monkeypatch):
        for k in ("T_A", "T_B"):
            monkeypatch.delenv(k, raising=False)
        f = tmp_path / ".env"
        f.write_text("# comment\n\nNOT A SETTING\nT_A=1\n  # indented comment\nT_B=2\n", encoding="utf-8")
        config.load_dotenv(f)
        import os
        assert (os.environ["T_A"], os.environ["T_B"]) == ("1", "2")
        assert "NOT A SETTING" not in os.environ
        for k in ("T_A", "T_B"):
            monkeypatch.delenv(k)

    def test_strips_quotes_and_surrounding_spaces(self, tmp_path, monkeypatch):
        for k in ("T_Q1", "T_Q2", "T_Q3"):
            monkeypatch.delenv(k, raising=False)
        f = tmp_path / ".env"
        f.write_text('T_Q1="double"\nT_Q2=\'single\'\n  T_Q3 =  spaced  \n', encoding="utf-8")
        config.load_dotenv(f)
        import os
        assert (os.environ["T_Q1"], os.environ["T_Q2"], os.environ["T_Q3"]) == ("double", "single", "spaced")
        for k in ("T_Q1", "T_Q2", "T_Q3"):
            monkeypatch.delenv(k)

    def test_a_value_may_contain_equals_signs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("T_EQ", raising=False)
        f = tmp_path / ".env"
        f.write_text("T_EQ=abc=def==\n", encoding="utf-8")
        config.load_dotenv(f)
        import os
        assert os.environ["T_EQ"] == "abc=def=="
        monkeypatch.delenv("T_EQ")

    def test_a_missing_file_is_fine(self, tmp_path):
        config.load_dotenv(tmp_path / "nope.env")


class TestGetSettings:
    def test_a_missing_secret_stops_the_service(self, clean_env):
        with pytest.raises(SystemExit):
            config.get_settings()

    def test_a_short_secret_is_refused(self, clean_env):
        clean_env.setenv("AGENT_SHARED_SECRET", "x" * 15)
        with pytest.raises(SystemExit):
            config.get_settings()

    def test_sixteen_characters_is_enough(self, clean_env):
        clean_env.setenv("AGENT_SHARED_SECRET", "x" * 16)
        assert config.get_settings().shared_secret == "x" * 16

    def test_defaults(self, clean_env):
        clean_env.setenv("AGENT_SHARED_SECRET", "s" * 20)
        s = config.get_settings()
        assert s.host == "127.0.0.1", "the service must listen on localhost unless told otherwise"
        assert s.port == 8080 and s.openai_model == "gpt-5.4-mini"
        assert s.neo4j_uri == "bolt://localhost:7687" and s.neo4j_user == "neo4j" and s.neo4j_database == "neo4j"
        assert s.neo4j_password == ""
        assert s.max_turns == 6 and s.llm_timeout == 40.0
        assert s.trace_dir == config.ROOT / "agent" / "logs"

    def test_environment_overrides_and_types(self, clean_env, tmp_path):
        for k, v in {"AGENT_SHARED_SECRET": "s" * 20, "AGENT_HOST": "0.0.0.0", "AGENT_PORT": "9090", "OPENAI_MODEL": "other-model",
                     "NEO4J_URI": "bolt://db:7687", "NEO4J_USER": "me", "NEO4J_PASSWORD": "pw", "NEO4J_DATABASE": "mine",
                     "AGENT_MAX_TURNS": "3", "AGENT_LLM_TIMEOUT": "12.5", "AGENT_TRACE_DIR": str(tmp_path)}.items():
            clean_env.setenv(k, v)
        s = config.get_settings()
        assert (s.host, s.port, s.openai_model) == ("0.0.0.0", 9090, "other-model")
        assert (s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) == ("bolt://db:7687", "me", "pw", "mine")
        assert (s.max_turns, s.llm_timeout, s.trace_dir) == (3, 12.5, tmp_path)
        assert isinstance(s.port, int) and isinstance(s.max_turns, int) and isinstance(s.llm_timeout, float)

    def test_settings_are_immutable(self, clean_env):
        clean_env.setenv("AGENT_SHARED_SECRET", "s" * 20)
        s = config.get_settings()
        with pytest.raises(Exception):
            s.port = 1
