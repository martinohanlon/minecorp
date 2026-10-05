"""The agent loop: model call -> tool calls -> model call ... -> answer. No framework.

Each question gets a bounded number of model calls. Every tool call is recorded as a Step; the
steps, token usage and timing are the decision trace, appended to a JSON-lines file.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime, timezone

from openai import OpenAI

from .budget import classify, fit
from .config import Settings
from .graph import Graph
from .models import Question, Result, Step
from .prompt import SYSTEM_PROMPT
from .tools import TOOLS, TOOLS_BY_NAME

log = logging.getLogger("minecorp.agent")

MAX_ANSWER_CHARS = 1200   # chat is not the place for essays
FALLBACK = "Sorry, I could not work out an answer to that. Try asking in a different way."


class Agent:
    """Answers one question at a time, using the graph tools. Safe to share between threads."""

    def __init__(self, settings: Settings, graph: Graph, client: OpenAI | None = None) -> None:
        self.settings = settings
        self.graph = graph
        self.client = client or OpenAI(timeout=settings.llm_timeout, max_retries=1)
        self.tool_specs = [t.spec() for t in TOOLS]

    # ------------------------------------------------------------------ public

    def run(self, q: Question, request_id: str) -> Result:
        """Answer a question. Never raises: failures become a friendly answer and a trace entry."""
        started = time.monotonic()
        steps: list[Step] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0}
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Player {q.player_name} asks: {q.text}"},
        ]
        answer, status, error = "", "ok", None
        try:
            for turn in range(1, self.settings.max_turns + 1):
                last = turn == self.settings.max_turns
                resp = self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    tools=self.tool_specs,
                    tool_choice="none" if last else "auto",
                )
                usage["model_calls"] += 1
                if resp.usage:
                    usage["prompt_tokens"] += resp.usage.prompt_tokens
                    usage["completion_tokens"] += resp.usage.completion_tokens
                msg = resp.choices[0].message
                if not msg.tool_calls:
                    answer = (msg.content or "").strip()
                    if last and not answer:
                        status = "turn_limit"
                    break
                messages.append({
                    "role": "assistant", "content": msg.content,
                    "tool_calls": [{"id": c.id, "type": "function",
                                    "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                   for c in msg.tool_calls]})
                for call in msg.tool_calls:
                    content, step = self._run_tool(turn, call.function.name, call.function.arguments)
                    steps.append(step)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            else:
                status = "turn_limit"
        except Exception as exc:  # network, auth, rate limit...
            log.exception("agent failed for request %s", request_id)
            status, error = "error", f"{type(exc).__name__}: {exc}"

        result = Result(answer=self._tidy(answer) or FALLBACK, status=status, model=self.settings.openai_model,
                        ms=int((time.monotonic() - started) * 1000), steps=steps, usage=usage, error=error)
        self._write_trace(q, request_id, result)
        return result

    # ------------------------------------------------------------------ internals

    def _run_tool(self, turn: int, name: str, raw_args: str) -> tuple[str, Step]:
        """Run one tool call; always returns text for the model and a Step for the trace."""
        started = time.monotonic()
        tool = TOOLS_BY_NAME.get(name)
        args: dict = {}
        error = None
        try:
            if tool is None:
                raise ValueError(f"unknown tool {name}")
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
            output = tool.fn(self.graph, args)
        except Exception as exc:
            log.warning("tool %s failed: %s", name, exc)
            error = f"{type(exc).__name__}: {exc}"
            kind, message = classify(exc)
            output = {"error": message, "type": kind}
        else:
            output = fit(output, tool.budget)
        text = json.dumps(output, separators=(",", ":"))
        step = Step(turn=turn, tool=name, args=args, ms=int((time.monotonic() - started) * 1000),
                    result_chars=len(text), error=error)
        return text, step

    @staticmethod
    def _tidy(text: str) -> str:
        """Strip markdown the chat cannot show, and cap the length."""
        text = text.replace("**", "").replace("__", "").replace("`", "")
        text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"'))
        text = re.sub(r"(?m)^\s*#+\s*", "", text)
        text = re.sub(r"(?m)^\s*[*•]\s+", "- ", text)
        text = text.strip()
        return text if len(text) <= MAX_ANSWER_CHARS else text[:MAX_ANSWER_CHARS].rstrip() + "..."

    def _write_trace(self, q: Question, request_id: str, r: Result) -> None:
        """Append the decision trace as one JSON line. Failure to write must never fail the answer."""
        try:
            self.settings.trace_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "requestId": request_id, "at": datetime.now(timezone.utc).isoformat(),
                "player": q.player_name, "sessionId": q.session_id, "question": q.text,
                "answer": r.answer, "status": r.status, "model": r.model, "ms": r.ms,
                "usage": r.usage, "error": r.error, "steps": [asdict(s) for s in r.steps],
            }
            with (self.settings.trace_dir / "traces.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            log.warning("could not write trace: %s", exc)
