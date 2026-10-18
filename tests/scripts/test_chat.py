"""Unit tests for scripts/chat.py, the terminal stand-in for the Minecraft plugin.

No Minecraft, no OpenAI, no database: the agent service is replaced by a mock transport, or the real FastAPI app with a fake
agent behind it. The stub must send what the plugin sends and show what the plugin shows, so these pin both down."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import chat  # noqa: E402

SECRET = "stub-test-secret-0123456789"


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def ok_handler(answer="Smelt it.", request_id="abc123"):
    """Answers questions; `seen` holds the questions asked, `clears` the new chats started (joining, or /clear)."""
    seen, clears = [], []

    def handler(request):
        if request.url.path == "/clear":
            clears.append(request)
            return httpx.Response(200, json={"cleared": True})
        seen.append(request)
        return httpx.Response(200, json={"requestId": request_id, "answer": answer})

    handler.seen, handler.clears = seen, clears
    return handler


def make_chat(handler, **kwargs):
    return chat.Chat(url="http://agent.test", secret=SECRET, client=mock_client(handler), **kwargs)


class Screen:
    """Collects what the terminal would print."""

    def __init__(self, colour=False):
        self.lines: list[str] = []
        self.term = chat.Terminal(colour, self.lines.append)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# --------------------------------------------------------------------------- the plugin's rules

class TestQuestion:
    @pytest.mark.parametrize("raw,expected", [("how do I make a bed?", "how do I make a bed?"), ("  spaced  ", "spaced"),
                                              ("/agent how do I smelt iron?", "how do I smelt iron?"), ("/AGENT   hello", "hello"),
                                              ("/agent", ""), ("/agentx", "/agentx"), ("what is /agent?", "what is /agent?")])
    def test_the_question_as_the_plugin_sees_it(self, raw, expected):
        assert chat.question_text(raw) == expected

    @pytest.mark.parametrize("question", ["", "   "])
    def test_no_question_gives_the_usage(self, question):
        assert chat.check_question(chat.question_text(question)) == ("Usage: /agent <question>", "yellow")

    def test_five_hundred_characters_is_fine_and_five_hundred_and_one_is_too_long(self):
        assert chat.check_question("q" * 500) is None
        assert chat.check_question("q" * 501) == ("That question is too long (max 500 characters).", "red")

    def test_not_configured(self):
        assert chat.check_question("hello", configured=False) == ("The agent is not configured yet. Ask an admin.", "red")

    def test_the_rules_are_checked_in_the_plugins_order(self):
        assert chat.check_question("", configured=False)[0].startswith("Usage")
        assert chat.check_question("q" * 501, configured=False)[0].startswith("That question is too long")


class TestLines:
    @pytest.mark.parametrize("answer,expected", [("one", ["one"]), ("a\nb", ["a", "b"]), ("a\r\nb\rc", ["a", "b", "c"]),
                                                 ("a\nb\n\n", ["a", "b"]), ("", [""]), ("a\n\nb", ["a", "", "b"])])
    def test_split_like_the_plugin(self, answer, expected):
        assert chat.answer_lines(answer) == expected


class TestStyle:
    def test_a_good_answer_has_no_warnings(self):
        assert chat.style_warnings(["- Craft 3 iron ingots and 2 sticks.", "- Use a crafting table."]) == []

    def test_too_many_lines(self):
        assert chat.style_warnings(["x"] * 7) == ["7 lines (asked for at most 6)"]

    def test_six_lines_is_allowed(self):
        assert chat.style_warnings(["x"] * 6) == []

    def test_long_lines_are_counted_with_the_longest(self):
        assert chat.style_warnings(["a" * 101, "b" * 120, "ok"]) == ["2 line(s) over 100 characters (longest 120)"]

    def test_a_line_a_little_over_the_ninety_the_prompt_asks_for_is_good_enough(self):
        assert chat.style_warnings(["a" * 90, "b" * 93, "c" * 100]) == []

    @pytest.mark.parametrize("line", ["**bold** text", "some `code` here", "# Heading", "| a | b |", "[link](http://x.y)", "__under__"])
    def test_markdown_the_chat_cannot_show(self, line):
        assert chat.style_warnings([line]) == ["markdown the chat cannot show"]

    def test_a_dash_list_is_not_markdown(self):
        assert chat.style_warnings(["- one", "- two"]) == []


# --------------------------------------------------------------------------- what is sent

class TestRequest:
    def test_the_plugins_request_exactly(self):
        handler = ok_handler()
        make_chat(handler, name="Alex").ask("how do I make a bed?")
        (request,) = handler.seen
        assert request.method == "POST" and str(request.url) == "http://agent.test/ask"
        assert request.headers["X-MineCorp-Key"] == SECRET and request.headers["content-type"].startswith("application/json")
        body = json.loads(request.content)
        assert set(body) == {"playerUuid", "playerName", "sessionId", "question"}
        assert body["playerName"] == "Alex" and body["question"] == "how do I make a bed?"
        assert body["sessionId"] == body["playerUuid"], "the plugin uses the player's uuid as the session id"

    def test_a_player_keeps_one_uuid_and_two_players_differ(self):
        assert chat.player_uuid("Steve") == chat.player_uuid("Steve") != chat.player_uuid("Alex")
        assert len(chat.player_uuid("Steve")) == 36

    def test_a_trailing_slash_on_the_url_is_fine(self):
        handler = ok_handler()
        chat.Chat(url="http://agent.test/", secret=SECRET, client=mock_client(handler)).ask("hi")
        assert str(handler.seen[0].url) == "http://agent.test/ask"

    def test_the_question_is_sent_as_typed_including_quotes_and_unicode(self):
        handler = ok_handler()
        make_chat(handler).ask('what is "tnt" — and a über chest?')
        assert json.loads(handler.seen[0].content)["question"] == 'what is "tnt" — and a über chest?'


class TestReply:
    def test_the_answer_comes_back_as_lines_with_the_request_id(self):
        reply = make_chat(ok_handler("- one\n- two", "r-1")).ask("q")
        assert reply.lines == ["- one", "- two"] and reply.request_id == "r-1" and reply.error is None and reply.seconds >= 0

    @pytest.mark.parametrize("status", [401, 422, 500, 503])
    def test_a_refusal_or_failure_is_an_error_with_the_status(self, status):
        reply = make_chat(lambda r: httpx.Response(status, text="nope")).ask("q")
        assert reply.lines == [] and reply.error.startswith(f"HTTP {status}")

    @pytest.mark.parametrize("response", [httpx.Response(200, text="<html>not json</html>"), httpx.Response(200, json={"requestId": "x"}),
                                          httpx.Response(200, json={"answer": 5}), httpx.Response(200, json=["answer"])])
    def test_a_reply_that_is_not_an_answer_is_an_error(self, response):
        assert make_chat(lambda r: response).ask("q").error

    def test_a_timeout_and_a_refused_connection_are_errors_not_exceptions(self):
        def timeout(request):
            raise httpx.ReadTimeout("too slow")

        def refused(request):
            raise httpx.ConnectError("connection refused")

        assert "ReadTimeout" in make_chat(timeout).ask("q").error and "ConnectError" in make_chat(refused).ask("q").error


class TestDirect:
    def test_the_agent_is_called_in_this_process_with_a_question_and_a_request_id(self):
        seen = []

        def fake(question, request_id):
            seen.append((question, request_id))
            return "Direct answer."

        reply = chat.Chat("Alex", direct=fake).ask("hello")
        assert reply.lines == ["Direct answer."] and reply.request_id == seen[0][1] and len(reply.request_id) == 12
        question = seen[0][0]
        assert (question.player_name, question.text) == ("Alex", "hello") and question.session_id == question.player_uuid == chat.player_uuid("Alex")

    def test_direct_needs_no_secret(self):
        assert chat.Chat(direct=lambda q, r: "x").configured and not chat.Chat().configured

    def test_a_failure_inside_the_agent_is_an_error(self):
        def broken(question, request_id):
            raise RuntimeError("graph down")

        assert chat.Chat(direct=broken).ask("q").error == "RuntimeError: graph down"


class TestHealth:
    def test_a_running_service(self):
        assert make_chat(lambda r: httpx.Response(200, json={"status": "ok"})).healthy() is None

    def test_a_service_that_is_down_or_unwell_says_why(self):
        def refused(request):
            raise httpx.ConnectError("refused")

        assert "ConnectError" in make_chat(refused).healthy()
        assert make_chat(lambda r: httpx.Response(500)).healthy() == "HTTP 500"


# --------------------------------------------------------------------------- the trace

TRACE = {"requestId": "r-1", "status": "ok", "model": "gpt-5.4-mini", "ms": 1234, "error": None,
         "usage": {"model_calls": 2, "prompt_tokens": 900, "completion_tokens": 100},
         "steps": [{"tool": "get_recipes", "args": {"item": "bed"}, "ms": 40, "result_chars": 600, "error": None},
                   {"tool": "find_item", "args": {"query": "x"}, "ms": 5, "result_chars": 67, "error": "ValueError: bad"}]}


class TestTrace:
    def write(self, tmp_path, entries):
        path = tmp_path / "traces.jsonl"
        path.write_text("\n".join(json.dumps(e) if isinstance(e, dict) else e for e in entries) + "\n", encoding="utf-8")
        return path

    def test_finds_the_entry_for_a_request_id(self, tmp_path):
        path = self.write(tmp_path, [{"requestId": "other"}, TRACE, {"requestId": "later"}])
        assert chat.trace_for("r-1", path) == TRACE

    def test_the_last_entry_wins_when_an_id_repeats(self, tmp_path):
        path = self.write(tmp_path, [{"requestId": "r-1", "ms": 1}, {"requestId": "r-1", "ms": 2}])
        assert chat.trace_for("r-1", path)["ms"] == 2

    def test_a_damaged_line_is_skipped(self, tmp_path):
        assert chat.trace_for("r-1", self.write(tmp_path, [TRACE, "{not json"])) == TRACE

    @pytest.mark.parametrize("request_id", ["missing", None, ""])
    def test_unknown_ids_give_none(self, tmp_path, request_id):
        assert chat.trace_for(request_id, self.write(tmp_path, [TRACE])) is None

    def test_no_file_gives_none(self, tmp_path):
        assert chat.trace_for("r-1", tmp_path / "nope.jsonl") is None

    def test_the_trace_directory_can_be_set_in_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
        assert chat.trace_path() == tmp_path / "traces.jsonl"

    def test_the_account_lists_each_tool_with_its_arguments_and_cost(self):
        lines = chat.format_trace(TRACE)
        assert lines[0] == "agent: ok, gpt-5.4-mini, 1234 ms, 2 model call(s), 1000 tokens"
        assert lines[1] == "  tool get_recipes(item='bed')  40 ms, 600 chars"
        assert lines[2] == "  tool find_item(query='x')  5 ms, 67 chars  ERROR ValueError: bad"

    def test_no_tools_and_an_error_are_said(self):
        lines = chat.format_trace({"status": "error", "model": "m", "ms": 1, "usage": {}, "steps": [], "error": "RateLimitError: slow down"})
        assert "  no tools used" in lines and "  error: RateLimitError: slow down" in lines


# --------------------------------------------------------------------------- what the player sees

class TestRespond:
    def test_a_good_question_shows_thinking_then_the_answer_lines_then_the_stub_notes(self):
        screen = Screen()
        assert chat.respond(make_chat(ok_handler("- one\n- two")), "how do I make a bed?", screen.term, trace=False) is True
        assert screen.lines[:3] == ["MineCorp agent: thinking...", "- one", "- two"]
        assert screen.lines[3].startswith("(stub) ") and screen.lines[3].endswith(" s") and len(screen.lines) == 4

    def test_the_answer_is_in_gold_and_the_wait_in_grey(self):
        screen = Screen(colour=True)
        chat.respond(make_chat(ok_handler("hello")), "q", screen.term, trace=False)
        assert screen.lines[0] == "\x1b[90mMineCorp agent: thinking...\x1b[0m" and screen.lines[1] == "\x1b[33mhello\x1b[0m"

    def test_a_refused_question_never_reaches_the_agent(self):
        handler = ok_handler()
        screen = Screen()
        assert chat.respond(make_chat(handler), "   ", screen.term, trace=False) is False
        assert screen.lines == ["Usage: /agent <question>"] and handler.seen == []
        screen = Screen()
        chat.respond(make_chat(handler), "q" * 501, screen.term, trace=False)
        assert screen.lines == ["That question is too long (max 500 characters)."] and handler.seen == []

    def test_without_a_secret_the_agent_is_not_configured_and_nothing_is_sent(self):
        handler = ok_handler()
        screen = Screen()
        chat.respond(chat.Chat(url="http://agent.test", client=mock_client(handler)), "hello", screen.term, trace=False)
        assert screen.lines == ["The agent is not configured yet. Ask an admin."] and handler.seen == []

    def test_a_failure_shows_the_players_message_and_the_developers_reason(self):
        screen = Screen()
        assert chat.respond(make_chat(lambda r: httpx.Response(401, json={"detail": "unauthorized"})), "q", screen.term, trace=False) is False
        assert screen.lines[0] == "MineCorp agent: thinking..." and screen.lines[1] == "The agent is not available right now. Try again later."
        assert screen.lines[2].startswith("(stub) HTTP 401")

    def test_an_answer_that_breaks_the_style_rules_says_so(self):
        screen = Screen()
        chat.respond(make_chat(ok_handler("**bold**\n" + "x" * 105)), "q", screen.term, trace=False)
        assert "style: 1 line(s) over 100 characters (longest 105)" in screen.text and "style: markdown the chat cannot show" in screen.text

    def test_a_slash_agent_prefix_is_dropped(self):
        handler = ok_handler()
        chat.respond(make_chat(handler), "/agent how do I smelt iron?", Screen().term, trace=False)
        assert json.loads(handler.seen[0].content)["question"] == "how do I smelt iron?"

    def test_the_trace_follows_the_answer_when_asked(self, monkeypatch, tmp_path):
        (tmp_path / "traces.jsonl").write_text(json.dumps(TRACE) + "\n", encoding="utf-8")
        monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
        screen = Screen()
        chat.respond(make_chat(ok_handler("hello", "r-1")), "q", screen.term, trace=True)
        assert "(stub) agent: ok, gpt-5.4-mini, 1234 ms, 2 model call(s), 1000 tokens" in screen.lines
        assert "(stub)   tool get_recipes(item='bed')  40 ms, 600 chars" in screen.lines

    def test_a_missing_trace_is_explained_not_hidden(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
        screen = Screen()
        chat.respond(make_chat(ok_handler("hello", "r-9")), "q", screen.term, trace=True)
        assert "no trace found for request r-9" in screen.text

    def test_no_trace_unless_asked(self, monkeypatch, tmp_path):
        (tmp_path / "traces.jsonl").write_text(json.dumps(TRACE) + "\n", encoding="utf-8")
        monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
        screen = Screen()
        chat.respond(make_chat(ok_handler("hello", "r-1")), "q", screen.term, trace=False)
        assert "tool get_recipes" not in screen.text


class TestTerminal:
    def test_no_colour_is_plain_text(self):
        screen = Screen(colour=False)
        screen.term.say("hello", "gold")
        assert screen.lines == ["hello"]

    @pytest.mark.parametrize("colour,code", [("gold", "33"), ("grey", "90"), ("red", "31"), ("yellow", "93")])
    def test_colours(self, colour, code):
        screen = Screen(colour=True)
        screen.term.say("x", colour)
        assert screen.lines == [f"\x1b[{code}mx\x1b[0m"]


class TestRepl:
    def run(self, chat_, inputs, **kwargs):
        screen = Screen()
        feed = iter(inputs)

        def read(prompt):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError

        chat.repl(chat_, screen.term, read=read, **kwargs)
        return screen

    def test_questions_are_answered_until_quit(self):
        handler = ok_handler("Answer.")
        screen = self.run(make_chat(handler), ["first", "second", "/quit", "never asked"])
        assert [json.loads(r.content)["question"] for r in handler.seen] == ["first", "second"]
        assert screen.text.count("MineCorp agent: thinking...") == 2

    def test_starting_the_chat_is_joining_so_it_starts_a_new_chat_first(self):
        handler = ok_handler()
        self.run(make_chat(handler), ["first"])
        assert len(handler.clears) == 1 and json.loads(handler.clears[0].content) == {"playerUuid": chat.player_uuid("Steve")}

    def test_a_service_that_cannot_start_a_new_chat_does_not_stop_the_chat(self):
        def refused(request):
            raise httpx.ConnectError("refused")

        screen = self.run(make_chat(refused), [])
        assert "Steve is in the MineCorp stub chat" in screen.text and chat.UNAVAILABLE not in screen.text

    def test_the_end_of_input_leaves_quietly(self):
        assert "Steve is in the MineCorp stub chat" in self.run(make_chat(ok_handler()), []).text

    def test_an_empty_line_shows_the_usage(self):
        handler = ok_handler()
        screen = self.run(make_chat(handler), [""])
        assert "Usage: /agent <question>" in screen.text and handler.seen == []

    def test_trace_can_be_switched_on_and_off(self, monkeypatch, tmp_path):
        (tmp_path / "traces.jsonl").write_text(json.dumps(TRACE) + "\n", encoding="utf-8")
        monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
        screen = self.run(make_chat(ok_handler("a", "r-1")), ["/trace", "q1", "/trace", "q2"])
        assert "trace on" in screen.text and "trace off" in screen.text
        assert screen.text.count("tool get_recipes") == 1, "only the question asked while the trace was on"

    def test_name_changes_the_player(self):
        handler = ok_handler()
        c = make_chat(handler)
        screen = self.run(c, ["/name Alex", "hello", "/name"])
        assert json.loads(handler.seen[0].content)["playerName"] == "Alex"
        assert "you are Alex" in screen.text and "Usage: /name <player>" in screen.text and c.name == "Alex"

    def test_help_lists_the_commands(self):
        assert "/quit" in self.run(make_chat(ok_handler()), ["/help"]).text

    def test_commands_are_not_questions(self):
        handler = ok_handler()
        self.run(make_chat(handler), ["/help", "/trace", "/name Bo", "/quit"])
        assert handler.seen == []

    def test_a_failing_question_does_not_end_the_chat(self):
        calls = []

        def flaky(request):
            if request.url.path == "/clear":
                return httpx.Response(200, json={"cleared": True})
            calls.append(1)
            return httpx.Response(500) if len(calls) == 1 else httpx.Response(200, json={"requestId": "x", "answer": "Back."})

        screen = self.run(make_chat(flaky), ["one", "two"])
        assert "The agent is not available right now" in screen.text and "Back." in screen.text


# --------------------------------------------------------------------------- the command line

class TestMain:
    @pytest.fixture(autouse=True)
    def clean_environment(self, monkeypatch):
        monkeypatch.setattr(chat, "load_dotenv", lambda *a, **k: None)
        for key in ("AGENT_SHARED_SECRET", "AGENT_URL", "NO_COLOR"):
            monkeypatch.delenv(key, raising=False)

    def test_a_question_on_the_command_line_is_asked_once(self, monkeypatch, capsys):
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        monkeypatch.setattr(chat.Chat, "healthy", lambda self: None)
        asked = []
        monkeypatch.setattr(chat.Chat, "ask", lambda self, q: asked.append((self.name, q, self.url, self.secret)) or chat.Reply(lines=["Done."], request_id="r"))
        assert chat.main(["--name", "Alex", "how", "do", "I", "make", "a", "bed?"]) == 0
        assert asked == [("Alex", "how do I make a bed?", "http://127.0.0.1:8080", SECRET)]
        assert "Done." in capsys.readouterr().out

    def test_the_url_comes_from_the_option_then_the_environment_then_the_default(self, monkeypatch):
        urls = []
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        monkeypatch.setattr(chat.Chat, "healthy", lambda self: urls.append(self.url))
        monkeypatch.setattr(chat.Chat, "ask", lambda self, q: chat.Reply(lines=["x"]))
        chat.main(["hi"])
        monkeypatch.setenv("AGENT_URL", "http://env.test:9000")
        chat.main(["hi"])
        chat.main(["--url", "http://flag.test:1/", "hi"])
        assert urls == ["http://127.0.0.1:8080", "http://env.test:9000", "http://flag.test:1"]

    def test_a_service_that_is_down_is_explained_and_nothing_is_asked(self, monkeypatch, capsys):
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        monkeypatch.setattr(chat.Chat, "healthy", lambda self: "ConnectError: refused")
        monkeypatch.setattr(chat.Chat, "ask", lambda self, q: pytest.fail("must not ask"))
        assert chat.main(["hi"]) == 1
        out = capsys.readouterr().out
        assert "is not answering (ConnectError: refused)" in out and "start-agent.ps1" in out and "--direct" in out

    def test_without_a_secret_the_plugin_message_is_shown(self, monkeypatch, capsys):
        monkeypatch.setattr(chat.Chat, "ask", lambda self, q: pytest.fail("must not ask"))
        assert chat.main(["hi"]) == 1
        assert "The agent is not configured yet. Ask an admin." in capsys.readouterr().out

    def test_a_failed_answer_is_a_non_zero_exit(self, monkeypatch):
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        monkeypatch.setattr(chat.Chat, "healthy", lambda self: None)
        monkeypatch.setattr(chat.Chat, "ask", lambda self, q: chat.Reply(error="HTTP 500"))
        assert chat.main(["hi"]) == 1

    def test_direct_runs_the_agent_here_and_needs_no_service_or_secret(self, monkeypatch, capsys):
        import minecorp_agent.core as core
        monkeypatch.setattr(core, "answer_with_sources", lambda question, request_id: (f"Direct: {question.text}", []))
        monkeypatch.setattr(chat.Chat, "healthy", lambda self: pytest.fail("direct mode does not check a service"))
        assert chat.main(["--direct", "hello", "there"]) == 0
        assert "Direct: hello there" in capsys.readouterr().out

    def test_no_question_starts_the_chat(self, monkeypatch, capsys):
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        monkeypatch.setattr(chat.Chat, "healthy", lambda self: None)
        monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
        assert chat.main([]) == 0
        assert "Steve is in the MineCorp stub chat" in capsys.readouterr().out

    def test_the_shipped_powershell_wrapper_exists_and_passes_the_options_on(self):
        script = (Path(chat.__file__).parent / "chat-with-agent.ps1").read_text(encoding="utf-8")
        assert "chat.py" in script and "--direct" in script and "--trace" in script and "--name" in script


# --------------------------------------------------------------------------- against the real service code

class TestAgainstTheRealApp:
    """The stub speaks to the real FastAPI app (secret check, validation) with a fake agent behind it."""

    @pytest.fixture
    def app_client(self, monkeypatch):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        from minecorp_agent import config
        monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("AGENT_SHARED_SECRET", SECRET)
        import minecorp_agent.app as app_mod
        app_mod = importlib.reload(app_mod)
        monkeypatch.setattr(app_mod, "answer_with_sources", lambda question, request_id: (f"- You asked: {question.text}\n- As {question.player_name}", []))
        return fastapi_testclient.TestClient(app_mod.app)

    def test_a_question_is_answered_with_the_request_id(self, app_client):
        reply = chat.Chat("Alex", "http://testserver", SECRET, client=app_client).ask("hello")
        assert reply.error is None and reply.lines == ["- You asked: hello", "- As Alex"] and len(reply.request_id) == 12

    def test_the_wrong_secret_is_a_401_the_player_sees_as_unavailable(self, app_client):
        screen = Screen()
        chat.respond(chat.Chat(url="http://testserver", secret="wrong-secret-0123456789", client=app_client), "hello", screen.term, trace=False)
        assert screen.lines[1] == "The agent is not available right now. Try again later." and "HTTP 401" in screen.lines[2]

    def test_the_health_check(self, app_client):
        assert chat.Chat(url="http://testserver", secret=SECRET, client=app_client).healthy() is None
