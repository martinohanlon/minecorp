"""End-to-end test of the plugin: a real Paper server, the real plugin jar and a stub agent service.

It builds the plugin, copies the server files from server/ into .test-server/ (first run only, about 230 MB),
starts Paper on a free port with the plugin and a config pointing at the stub, then types commands on the server
console and checks both what the player would see and what the agent received. Your real server (server/) is not
touched and may keep running. Needs: Java 25, Maven, and server/ set up once with scripts/start-server.ps1.

Run with:  pytest -m e2e      (about a minute)
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_DIR = ROOT / "server"
TEST_DIR = ROOT / ".test-server"
SECRET = "e2e-secret-0123456789"
CONSOLE_UUID = "00000000-0000-0000-0000-000000000000"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


# --------------------------------------------------------------------------- tools on this machine

def find_java() -> str | None:
    candidates = []
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe")
        candidates.append(Path(os.environ["JAVA_HOME"]) / "bin" / "java")
    which = shutil.which("java")
    if which:
        candidates.append(Path(which))
    candidates += [Path(p) for p in sorted(glob.glob("C:/Program Files/Eclipse Adoptium/jdk-25*/bin/java.exe"), reverse=True)]
    return next((str(c) for c in candidates if c.exists()), None)


def find_mvn() -> str | None:
    which = shutil.which("mvn") or shutil.which("mvn.cmd")
    if which:
        return which
    found = sorted(glob.glob(str(Path.home() / "AppData/Roaming/apache-maven-*/bin/mvn.cmd")), reverse=True)
    return found[0] if found else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- stub agent

class StubAgent:
    """A tiny stand-in for the agent service. The question text picks the behaviour."""

    def __init__(self, secret: str):
        self.secret = secret
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the test output quiet
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length).decode("utf-8")
                key = self.headers.get("X-MineCorp-Key")
                record = {"path": self.path, "key": key, "type": self.headers.get("Content-Type"), "raw": raw}
                stub.requests.append(record)
                if self.path != "/ask" or key != stub.secret:
                    return self._send(401, '{"detail":"unauthorized"}')
                question = json.loads(raw)["question"]
                record["body"] = json.loads(raw)
                if question.startswith("boom"):
                    return self._send(500, '{"detail":"boom"}')
                if question.startswith("garbage"):
                    return self._send(200, "<html>not json</html>", "text/html")
                if question.startswith("slow"):
                    time.sleep(1.5)
                if question.startswith("timeout"):
                    time.sleep(4.5)
                answer = "Line one\nLine two" if question.startswith("multi") else f"Echo: {question}"
                self._send(200, json.dumps({"requestId": "e2e", "answer": answer}))

            def _send(self, status, body, ctype="application/json"):
                data = body.encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):   # the plugin gave up waiting
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def asks(self) -> list[dict]:
        return [r for r in self.requests if r.get("body")]


# --------------------------------------------------------------------------- the server under test

class Console:
    def __init__(self, proc: subprocess.Popen, log: Path):
        self.proc, self.log = proc, log

    def text(self) -> str:
        return ANSI.sub("", self.log.read_text(encoding="utf-8", errors="replace")) if self.log.exists() else ""

    def mark(self) -> int:
        return len(self.text())

    def send(self, line: str) -> int:
        start = self.mark()
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return start

    def wait_for(self, needle: str, since: int = 0, timeout: float = 20) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.text()[since:]
            if needle in text:
                return text
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        raise AssertionError(f"timed out waiting for {needle!r}. Console since the command:\n{self.text()[since:][-1500:]}")

    def count(self, needle: str, since: int = 0) -> int:
        return self.text()[since:].count(needle)


@pytest.fixture(scope="module")
def stub():
    s = StubAgent(SECRET)
    yield s
    s.stop()


@pytest.fixture(scope="module")
def console(stub):
    java, mvn = find_java(), find_mvn()
    if not java:
        pytest.skip("Java 25 not found (run scripts/install-java.ps1)")
    if not mvn:
        pytest.skip("Maven not found")
    jars = sorted(SERVER_DIR.glob("paper-*.jar"))
    if not jars or not (SERVER_DIR / "libraries").exists():
        pytest.skip("server/ is not set up (run scripts/start-server.ps1 once)")
    eula = SERVER_DIR / "eula.txt"
    if not eula.exists() or "eula=true" not in eula.read_text():
        pytest.skip("the Minecraft EULA has not been accepted in server/eula.txt")

    # build the plugin
    env = dict(os.environ, JAVA_HOME=str(Path(java).parent.parent))
    built = subprocess.run([mvn, "-B", "-q", "package", "-DskipTests"], cwd=ROOT / "plugin", env=env,
                           capture_output=True, text=True, shell=os.name == "nt")
    assert built.returncode == 0, built.stdout[-2000:] + built.stderr[-2000:]
    plugin_jar = sorted((ROOT / "plugin" / "target").glob("MineCorp-*.jar"))[-1]

    # a private copy of the server, so the real one is never touched
    TEST_DIR.mkdir(exist_ok=True)
    for name in ("libraries", "versions", "cache"):
        if (SERVER_DIR / name).exists() and not (TEST_DIR / name).exists():
            shutil.copytree(SERVER_DIR / name, TEST_DIR / name)
    shutil.copy(jars[-1], TEST_DIR / jars[-1].name)
    shutil.copy(eula, TEST_DIR / "eula.txt")
    for world in TEST_DIR.glob("world*"):
        assert TEST_DIR in world.resolve().parents            # only ever delete inside the test folder
        shutil.rmtree(world, ignore_errors=True)
    plugins = TEST_DIR / "plugins"
    shutil.rmtree(plugins, ignore_errors=True)
    (plugins / "MineCorp").mkdir(parents=True)
    shutil.copy(plugin_jar, plugins / plugin_jar.name)
    (plugins / "MineCorp" / "config.yml").write_text(
        f'agent-url: "http://127.0.0.1:{stub.port}"\nshared-secret: "{SECRET}"\ntimeout-seconds: 3\n', encoding="utf-8")
    port = free_port()
    (TEST_DIR / "server.properties").write_text(
        f"server-port={port}\nserver-ip=127.0.0.1\nonline-mode=false\nview-distance=2\nsimulation-distance=2\n"
        "spawn-protection=0\nenable-rcon=false\nmotd=MineCorp e2e test\n", encoding="utf-8")

    log = TEST_DIR / "console.log"
    log.write_text("")
    proc = subprocess.Popen([java, "-Xms1G", "-Xmx1G", "-jar", jars[-1].name, "--nogui"], cwd=TEST_DIR,
                            stdin=subprocess.PIPE, stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT, text=True)
    c = Console(proc, log)
    try:
        c.wait_for("Done (", timeout=240)
        yield c
    finally:
        if proc.poll() is None:
            try:
                c.send("stop")
                proc.wait(timeout=60)
            except Exception:
                proc.kill()


# --------------------------------------------------------------------------- the tests

def test_plugin_loads_and_reports_its_agent(console, stub):
    text = console.text()
    assert "[MineCorp] Enabling MineCorp v0.1.0" in text
    assert f"MineCorp plugin enabled (agent: http://127.0.0.1:{stub.port})" in text
    assert "shared-secret is not set" not in text


def test_no_question_shows_the_usage_and_asks_nothing(console, stub):
    before = len(stub.requests)
    since = console.send("agent")
    console.wait_for("Usage: /agent <question>", since)
    time.sleep(0.5)
    assert len(stub.requests) == before


def test_a_question_is_sent_to_the_agent_and_the_answer_is_shown(console, stub):
    since = console.send("agent how do I make an iron pickaxe?")
    console.wait_for("MineCorp agent: thinking...", since)
    console.wait_for("Echo: how do I make an iron pickaxe?", since)
    req = stub.asks()[-1]
    assert req["path"] == "/ask" and req["key"] == SECRET and req["type"].startswith("application/json")
    assert req["body"] == {"playerUuid": CONSOLE_UUID, "playerName": "Console", "sessionId": CONSOLE_UUID,
                           "question": "how do I make an iron pickaxe?"}


def test_a_multi_line_answer_arrives_as_separate_lines_in_order(console):
    since = console.send("agent multi please")
    text = console.wait_for("Line two", since)
    assert text.index("Line one") < text.index("Line two")


def test_a_question_that_is_too_long_is_refused_without_asking(console, stub):
    before = len(stub.requests)
    since = console.send("agent " + "x" * 501)
    console.wait_for("That question is too long (max 500 characters).", since)
    time.sleep(0.5)
    assert len(stub.requests) == before


def test_a_question_of_exactly_500_characters_is_accepted(console):
    question = "y" * 500
    since = console.send("agent " + question)
    console.wait_for("Echo: " + question, since)


def test_an_agent_error_gives_a_friendly_message_and_a_log_line(console):
    since = console.send("agent boom now")
    console.wait_for("The agent is not available right now. Try again later.", since)
    console.wait_for("Agent request failed: java.io.IOException: Agent returned HTTP 500", since)


def test_a_reply_that_is_not_json_gives_the_same_friendly_message(console):
    since = console.send("agent garbage in")
    console.wait_for("The agent is not available right now. Try again later.", since)
    console.wait_for("Agent reply was not valid JSON", since)


def test_a_second_question_while_one_is_running_is_held_back_then_allowed(console, stub):
    since = console.send("agent slow first")
    console.send("agent second")
    console.wait_for("Still thinking about your last question...", since)
    console.wait_for("Echo: slow first", since)                       # the first one still completes
    assert console.count("Echo: second", since) == 0                   # the second never reached the agent
    after = console.send("agent third")
    console.wait_for("Echo: third", after)


def test_a_slow_agent_times_out_and_the_player_can_ask_again(console):
    since = console.send("agent timeout please")
    console.wait_for("The agent is not available right now. Try again later.", since, timeout=15)
    again = console.send("agent after the timeout")
    console.wait_for("Echo: after the timeout", again)


def test_the_explain_command_is_for_players_and_the_console_is_told_so_without_asking(console, stub):
    # opening a book needs a real player; the console only proves the command is registered and guarded
    before = len(stub.requests)
    since = console.send("agent-explain")
    console.wait_for("Only a player can open the notes book.", since)
    time.sleep(0.5)
    assert len(stub.requests) == before


def test_the_clear_command_is_for_players_and_the_console_is_told_so_without_asking(console, stub):
    before = len(stub.requests)
    since = console.send("agent-clear")
    console.wait_for("Only a player has a chat to clear.", since)
    time.sleep(0.5)
    assert len(stub.requests) == before


def test_an_unreachable_agent_is_reported_and_does_not_hang_the_server(console, stub):
    stub.stop()
    since = console.send("agent anyone there")
    console.wait_for("The agent is not available right now. Try again later.", since)
    console.wait_for("Agent request failed", since)
    started = time.time()
    since = console.send("list")                                       # the server is still responsive
    console.wait_for("players online", since, timeout=10)
    assert time.time() - started < 10


def test_the_plugin_disables_cleanly_when_the_server_stops(console):
    since = console.send("stop")
    console.wait_for("MineCorp plugin disabled", since, timeout=60)
    console.proc.wait(timeout=60)
    assert console.proc.returncode == 0
