"""Unit tests for scripts/build_graph.py: the order of the steps, what is wiped, how .env is used, and what happens when a step fails.

No database and no loader runs: the subprocess runner and the clock are replaced."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import build_graph as bg  # noqa: E402


class Runner:
    """Stands in for subprocess.run: records each command, fails the ones whose script name is in `fail`."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, cmd, cwd=None, env=None):
        self.calls.append((cmd, cwd, env))
        return SimpleNamespace(returncode=3 if Path(cmd[1]).name in self.fail else 0)

    def scripts(self):
        return [Path(c[0][1]).name for c in self.calls]


def ticker():
    t = iter(range(0, 1000, 5))
    return lambda: next(t)


class TestTheSteps:
    def test_the_order_is_the_rebuild_order_in_the_loader_readme(self):
        assert [s.script for s in bg.STEPS] == ["load_recipes.py", "load_actions.py", "load_world.py", "load_enchantments.py",
                                                "load_trades.py", "load_model.py", "compute_routes.py", "load_wiki.py", "link_wiki.py"]
        text = (ROOT / "loader" / "README.md").read_text(encoding="utf-8")
        assert "`load_recipes.py --all --wipe`, `load_actions.py`, `load_world.py`, `load_enchantments.py`, `load_trades.py`, `load_model.py`, then `compute_routes.py`" in text

    def test_every_step_runs_a_loader_that_exists(self):
        for s in bg.STEPS:
            assert (ROOT / "loader" / s.script).is_file(), s.script

    def test_only_the_first_step_wipes(self):
        assert [s.key for s in bg.STEPS if "--wipe" in s.args] == ["recipes"]

    def test_the_wiki_is_loaded_after_the_game_graph_and_linked_last(self):
        assert [s.key for s in bg.STEPS][-2:] == ["wiki", "links"]
        assert [s.key for s in bg.STEPS if s.wiki] == ["wiki", "links"]

    def test_keys_are_unique(self):
        assert len(set(bg.KEYS)) == len(bg.KEYS)


class TestPlan:
    def test_everything_by_default(self):
        assert [s.key for s in bg.plan()] == bg.KEYS

    def test_no_wiki_leaves_out_both_wiki_steps(self):
        assert [s.key for s in bg.plan(only_game=True)] == ["recipes", "actions", "world", "enchantments", "trades", "model", "routes"]

    def test_from_a_step_starts_there_and_has_no_wipe(self):
        steps = bg.plan("model")
        assert [s.key for s in steps] == ["model", "routes", "wiki", "links"]
        assert not any("--wipe" in s.args for s in steps)

    def test_from_combines_with_no_wiki(self):
        assert [s.key for s in bg.plan("trades", only_game=True)] == ["trades", "model", "routes"]

    def test_an_unknown_step_is_an_error_naming_the_steps(self):
        with pytest.raises(ValueError, match="unknown step 'nope'.*recipes.*links"):
            bg.plan("nope")


class TestDotenv:
    def test_pairs_comments_blank_lines_and_quotes(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('# a comment\n\nNEO4J_URI=bolt://x:1\nPASSWORD = "p w"\nSINGLE=\'q\'\nEMPTY=\nNOEQUALS\nWITH_EQ=a=b\n', encoding="utf-8")
        assert bg.read_dotenv(f) == {"NEO4J_URI": "bolt://x:1", "PASSWORD": "p w", "SINGLE": "q", "EMPTY": "", "WITH_EQ": "a=b"}

    def test_a_missing_file_gives_nothing(self, tmp_path):
        assert bg.read_dotenv(tmp_path / "nope") == {}

    def test_the_environment_wins_over_the_file_but_an_empty_value_does_not(self):
        env = bg.environment({"A": "env", "B": ""}, {"A": "file", "B": "file", "C": "file"})
        assert (env["A"], env["B"], env["C"]) == ("env", "file", "file")

    def test_output_is_utf8_for_the_loaders(self):
        assert bg.environment({}, {})["PYTHONIOENCODING"] == "utf-8"


class TestRun:
    def test_every_step_runs_in_order_from_the_project_root_with_the_environment(self):
        runner, out = Runner(), []
        env = {"NEO4J_PASSWORD": "x"}
        assert bg.run(bg.plan(), env, runner=runner, clock=ticker(), out=out.append) == 0
        assert runner.scripts() == [s.script for s in bg.STEPS]
        assert all(cwd == bg.ROOT and e is env for _c, cwd, e in runner.calls)
        assert runner.calls[0][0][2:] == ["--all", "--wipe"] and all(c[0][0] == sys.executable for c in runner.calls)
        assert "The graph is built" in out[-1]

    def test_a_failure_stops_there_and_says_how_to_carry_on(self):
        runner, out = Runner(fail={"load_world.py"}), []
        assert bg.run(bg.plan(), {}, runner=runner, clock=ticker(), out=out.append) == 3
        assert runner.scripts() == ["load_recipes.py", "load_actions.py", "load_world.py"]
        assert "Step 'world' failed (exit 3)" in out[-1] and "--from world" in out[-1] and "--no-wiki" not in out[-1]

    def test_the_resume_hint_keeps_no_wiki_when_the_wiki_was_not_planned(self):
        out = []
        bg.run(bg.plan(only_game=True), {}, runner=Runner(fail={"load_model.py"}), clock=ticker(), out=out.append)
        assert "--from model --no-wiki" in out[-1]


class TestConfirm:
    def test_a_run_that_wipes_must_be_confirmed_with_yes(self):
        asked = []
        assert bg.confirm("bolt://x (database d)", bg.plan(), ask=lambda p: asked.append(p) or "yes")
        assert asked and not bg.confirm("t", bg.plan(), ask=lambda p: "y")
        assert not bg.confirm("t", bg.plan(), ask=lambda p: "")

    def test_the_prompt_names_the_database_that_will_be_wiped(self, capsys):
        bg.confirm("bolt://localhost:7688 (database neo4j)", bg.plan(), ask=lambda p: "no")
        assert "WIPES everything in bolt://localhost:7688 (database neo4j)" in capsys.readouterr().out

    def test_a_run_that_does_not_wipe_is_not_asked(self):
        assert bg.confirm("t", bg.plan("model"), ask=lambda p: pytest.fail("asked"))


class TestMain:
    @pytest.fixture
    def quiet(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bg, "ROOT", tmp_path)           # no real .env is read
        for k in ("NEO4J_PASSWORD", "OPENAI_API_KEY", "NEO4J_URI", "NEO4J_DATABASE"):
            monkeypatch.delenv(k, raising=False)
        return tmp_path

    def test_list_changes_nothing_and_shows_the_target(self, quiet, monkeypatch, capsys):
        monkeypatch.setattr(bg, "run", lambda *a, **k: pytest.fail("ran"))
        assert bg.main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "Target: bolt://localhost:7687 (database neo4j)" in out and "1. recipes" in out and "9. links" in out

    def test_the_target_comes_from_the_dotenv_file(self, quiet, capsys):
        (quiet / ".env").write_text("NEO4J_URI=bolt://localhost:7688\nNEO4J_DATABASE=dev\n", encoding="utf-8")
        bg.main(["--list"])
        assert "Target: bolt://localhost:7688 (database dev)" in capsys.readouterr().out

    def test_no_password_stops_before_anything_is_wiped(self, quiet, monkeypatch, capsys):
        monkeypatch.setattr(bg, "run", lambda *a, **k: pytest.fail("ran"))
        assert bg.main(["--yes"]) == 2
        assert "NEO4J_PASSWORD is not set" in capsys.readouterr().err

    def test_the_wiki_needs_an_openai_key_but_no_wiki_does_not(self, quiet, monkeypatch, capsys):
        (quiet / ".env").write_text("NEO4J_PASSWORD=x\n", encoding="utf-8")
        monkeypatch.setattr(bg, "run", lambda steps, env, **k: 0)
        assert bg.main(["--yes"]) == 2 and "OPENAI_API_KEY is not set" in capsys.readouterr().err
        assert bg.main(["--yes", "--no-wiki"]) == 0

    def test_saying_no_changes_nothing(self, quiet, monkeypatch, capsys):
        (quiet / ".env").write_text("NEO4J_PASSWORD=x\nOPENAI_API_KEY=k\n", encoding="utf-8")
        monkeypatch.setattr("builtins.input", lambda p="": "no")
        monkeypatch.setattr(bg, "run", lambda *a, **k: pytest.fail("ran"))
        assert bg.main([]) == 1 and "Nothing changed." in capsys.readouterr().out

    def test_the_steps_get_the_environment_with_the_dotenv_values(self, quiet, monkeypatch):
        (quiet / ".env").write_text("NEO4J_PASSWORD=from-file\nOPENAI_API_KEY=k\n", encoding="utf-8")
        seen = {}
        monkeypatch.setattr(bg, "run", lambda steps, env, **k: seen.update(steps=[s.key for s in steps], pw=env["NEO4J_PASSWORD"]) or 0)
        assert bg.main(["--yes"]) == 0
        assert seen == {"steps": bg.KEYS, "pw": "from-file"}

    def test_an_unknown_step_exits_with_a_usage_error(self, quiet):
        with pytest.raises(SystemExit) as e:
            bg.main(["--from", "nope"])
        assert e.value.code == 2


class TestTheScripts:
    """Every script has a PowerShell and a bash version, and they are named for what they do."""

    NAMES = ["build-graph", "build-plugin", "chat-with-agent", "install-java", "install-maven", "run-tests", "start-agent", "start-server"]

    @pytest.mark.parametrize("name", NAMES)
    def test_each_script_has_both_versions(self, name):
        assert (ROOT / "scripts" / f"{name}.ps1").is_file() and (ROOT / "scripts" / f"{name}.sh").is_file()

    def test_there_are_no_other_scripts(self):
        found = {p.stem for p in (ROOT / "scripts").glob("*.ps1")} | {p.stem for p in (ROOT / "scripts").glob("*.sh") if not p.stem.startswith("_")}
        assert found == set(self.NAMES)

    @pytest.mark.parametrize("name", NAMES)
    def test_bash_scripts_have_a_shebang_and_no_carriage_returns(self, name):
        data = (ROOT / "scripts" / f"{name}.sh").read_bytes()
        assert data.startswith(b"#!/usr/bin/env bash\n") and b"\r" not in data

    def test_no_document_or_script_still_uses_an_old_name(self):
        old = re.compile(r"scripts[/\\](start|run-agent|test|chat)\.ps1|\./scripts/(start|run-agent|test|chat)\.ps1")
        offenders = []
        for p in list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")) + list((ROOT / "agent").glob("*.md")) + list((ROOT / "loader").glob("*.md")) \
                + list((ROOT / "plugin").glob("*.md")) + list((ROOT / "tests").glob("*.md")) + list((ROOT / "scripts").glob("*")) + list((ROOT / "tests").rglob("*.py")):
            if p.is_file() and p.suffix in {".md", ".ps1", ".sh", ".py"} and p.name != "test_build_graph.py" and old.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(p.name)
        assert offenders == []
