"""The benchmark script (scripts/run_benchmark.py): the order of its steps, what it passes on, and that it asks before spending money.
No model, no graph, no cost: the steps are replaced by fakes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_benchmark as rb  # noqa: E402


class Steps:
    """Fake verify, run and report: they record that they were called and what they were given."""
    def __init__(self, verify_code=0, run_code=0):
        self.calls, self.verify_code, self.run_code = [], verify_code, run_code

    def verify(self, argv):
        self.calls.append(("verify", argv))
        return self.verify_code

    def run(self, argv):
        self.calls.append(("run", argv))
        return self.run_code

    def report(self, argv):
        self.calls.append(("report", argv))
        return 0


def go(argv, steps=None, answer="y", interactive=True):
    steps = steps or Steps()
    code = rb.main(argv, verify=steps.verify, run=steps.run, report=steps.report, ask=lambda prompt: answer, interactive=interactive)
    return code, steps.calls


def test_a_run_verifies_then_runs_then_reports():
    code, calls = go(["--yes"])
    assert code == 0 and [c[0] for c in calls] == ["verify", "run", "report"]


def test_everything_that_is_not_its_own_option_goes_to_the_benchmark():
    _, calls = go(["--yes", "--repeats", "1", "--arms", "bare", "s1", "s2"])
    assert ("run", ["--repeats", "1", "--arms", "bare", "s1", "s2"]) in calls


def test_a_gold_answer_that_does_not_match_stops_it_before_any_money_is_spent(capsys):
    code, calls = go(["--yes"], Steps(verify_code=1))
    assert code == 1 and [c[0] for c in calls] == ["verify"]
    assert "fix that before spending" in capsys.readouterr().err


def test_verify_only_does_not_run_or_report():
    code, calls = go(["--verify-only"])
    assert code == 0 and [c[0] for c in calls] == ["verify"]


def test_report_only_skips_the_check_and_the_run_and_can_name_a_run():
    code, calls = go(["--report-only"])
    assert code == 0 and calls == [("report", [])]
    _, calls = go(["--report-only", "--run", "20261118-170033"])
    assert calls == [("report", ["20261118-170033"])]


def test_it_asks_first_and_a_no_means_nothing_runs():
    code, calls = go([], answer="n")
    assert code == 1 and [c[0] for c in calls] == ["verify"]
    code, calls = go([], answer="yes")
    assert code == 0 and [c[0] for c in calls] == ["verify", "run", "report"]


def test_with_nobody_to_ask_it_refuses_unless_told_to_go_ahead(capsys):
    code, calls = go([], interactive=False)
    assert code == 1 and "run" not in [c[0] for c in calls] and "--yes" in capsys.readouterr().err
    code, calls = go(["--yes"], interactive=False)
    assert code == 0 and "run" in [c[0] for c in calls]


def test_a_failed_run_is_not_reported_as_a_success():
    code, calls = go(["--yes"], Steps(run_code=3))
    assert code == 3 and [c[0] for c in calls] == ["verify", "run"]


@pytest.mark.parametrize("name", ["run-benchmark.ps1", "run-benchmark.sh"])
def test_the_wrappers_call_the_python_program(name):
    assert "run_benchmark.py" in (ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_the_powershell_options_are_passed_on_as_the_program_expects():
    ps = (ROOT / "scripts" / "run-benchmark.ps1").read_text(encoding="utf-8")
    for flag in ("--yes", "--verify-only", "--report-only", "--run", "--repeats", "--max-cost", "--arms", "--models", "--effort", "--seed", "--no-shuffle"):
        assert f'"{flag}"' in ps, flag


def test_a_prompt_that_cannot_be_read_is_a_refusal_not_a_crash(capsys):
    def no_input(prompt):
        raise EOFError
    steps = Steps()
    code = rb.main([], verify=steps.verify, run=steps.run, report=steps.report, ask=no_input, interactive=True)
    assert code == 1 and [c[0] for c in steps.calls] == ["verify"] and "--yes" in capsys.readouterr().err


def test_a_run_id_without_report_only_is_refused_before_anything_is_spent(capsys):
    """A misrouted word (PowerShell once bound a question id to -Run) must fail loudly, not start a full paid run."""
    code, calls = go(["--yes", "--run", "s1-bucket"])
    assert code == 2 and calls == [] and "--report-only" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell is the Windows version")
def test_the_powershell_wrapper_sends_a_bare_word_to_the_question_filter_not_to_a_parameter():
    """The bug this guards: with positional binding on, `-Repeats 1 s1-bucket` bound s1-bucket to -Run and the whole question set ran."""
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-File", str(ROOT / "scripts" / "run-benchmark.ps1"), "-DryRun", "-Yes", "-Repeats", "1", "s1-bucket", "s2"],
                         capture_output=True, text=True, timeout=60).stdout
    assert out.strip().endswith("--yes --repeats 1 s1-bucket s2"), out
    assert "--run" not in out
