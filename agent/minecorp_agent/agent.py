"""The agent loop: model call -> tool calls -> model call ... -> answer. No framework.

Each question gets a bounded number of model calls. Every tool call is recorded as a Step; the
steps, token usage and timing are the decision trace, appended to a JSON-lines file.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone

from openai import OpenAI

from .budget import classify, fit, without_private
from .config import Settings
from .graph import Graph
from .history import Exchange
from .models import Question, Result, Step
from .prompt import BARE_PROMPT, SYSTEM_PROMPT
from .sources import sources_for
from .tools import TOOLS, TOOLS_BY_NAME

log = logging.getLogger("minecorp.agent")

MAX_ANSWER_CHARS = 1200   # chat is not the place for essays
FALLBACK = "Sorry, I could not work out an answer to that. Try asking in a different way."
ERROR_ANSWER = "Sorry, something went wrong on my side and I could not get an answer. Please try again in a moment."   # a failure, not a bad question


class Agent:
    """Answers one question at a time, using the graph tools. Safe to share between threads."""

    def __init__(self, settings: Settings, graph: Graph, client: OpenAI | None = None) -> None:
        self.settings = settings
        self.graph = graph
        self.client = client or OpenAI(timeout=settings.llm_timeout, max_retries=1)
        self.tool_specs = [t.spec() for t in TOOLS]

    # ------------------------------------------------------------------ public

    def run(self, q: Question, request_id: str, context: Sequence[Exchange] = ()) -> Result:
        """Answer a question. Never raises: failures become a friendly answer and a trace entry.

        `context` is the earlier part of this player's conversation, oldest first: each exchange is sent as a question and its answer, so the
        model can tell what "it" or "those" means. Only the text is replayed, not the tool results.
        """
        started = time.monotonic()
        steps: list[Step] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0}
        messages = [{"role": "system", "content": SYSTEM_PROMPT if self.settings.use_tools else BARE_PROMPT}]
        for earlier in context:
            messages.append({"role": "user", "content": f"Player {q.player_name} asks: {earlier.question}"})
            messages.append({"role": "assistant", "content": "\n".join(earlier.lines)})
        messages.append({"role": "user", "content": f"Player {q.player_name} asks: {q.text}"})
        answer, status, error = "", "ok", None
        responses_api = self.settings.api == "responses"
        prior_id: str | None = None                       # Responses API: the server keeps the conversation; each turn sends only what is new
        new_input = [m for m in messages[1:]]
        try:
            for turn in range(1, self.settings.max_turns + 1):
                last = turn == self.settings.max_turns
                if responses_api:
                    text, calls, prior_id = self._responses_turn(messages[0]["content"], new_input, prior_id, last, usage)
                    if not calls:
                        answer = text
                        if last and not answer:
                            status = "turn_limit"
                        break
                    new_input = []
                    for call_id, name, raw_args in calls:
                        content, step = self._run_tool(turn, name, raw_args)
                        steps.append(step)
                        new_input.append({"type": "function_call_output", "call_id": call_id, "output": content})
                    continue
                options: dict = {}
                if self.settings.use_tools:
                    options.update(tools=self.tool_specs, tool_choice="none" if last else "auto")
                if self.settings.reasoning_effort:
                    options["reasoning_effort"] = self.settings.reasoning_effort
                resp = self.client.chat.completions.create(model=self.settings.openai_model, messages=messages, **options)
                usage["model_calls"] += 1
                usage["api_model"] = getattr(resp, "model", None) or self.settings.openai_model   # the exact model string the API answered with
                if resp.usage:
                    usage["prompt_tokens"] += resp.usage.prompt_tokens
                    usage["completion_tokens"] += resp.usage.completion_tokens
                    # kept for the benchmark's cost estimate: cached input is cheaper, reasoning tokens are already inside completion_tokens
                    prompt_details = getattr(resp.usage, "prompt_tokens_details", None)
                    completion_details = getattr(resp.usage, "completion_tokens_details", None)
                    usage["cached_tokens"] = usage.get("cached_tokens", 0) + (getattr(prompt_details, "cached_tokens", 0) or 0)
                    usage["reasoning_tokens"] = usage.get("reasoning_tokens", 0) + (getattr(completion_details, "reasoning_tokens", 0) or 0)
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

        result = Result(answer=ERROR_ANSWER if status == "error" else self._tidy(answer) or FALLBACK, status=status, model=self.settings.openai_model,
                        ms=int((time.monotonic() - started) * 1000), steps=steps, usage=usage, error=error, context_turns=len(context))
        self._write_trace(q, request_id, result)
        return result

    # ------------------------------------------------------------------ internals

    def _responses_turn(self, instructions: str, new_input: list, prior_id: str | None, last: bool, usage: dict) -> tuple[str, list, str]:
        """One model call through the Responses API (the benchmark's path: the gpt-6 models do not accept function tools with a reasoning
        effort on Chat Completions). Returns the text, the tool calls as (call_id, name, arguments) and the response id for the next turn."""
        options: dict = {}
        if self.settings.use_tools:
            options.update(tools=[{"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters, "strict": False}
                                  for t in TOOLS],
                           tool_choice="none" if last else "auto")
        if self.settings.reasoning_effort:
            options["reasoning"] = {"effort": self.settings.reasoning_effort}
        if prior_id:
            options["previous_response_id"] = prior_id
        resp = self.client.responses.create(model=self.settings.openai_model, instructions=instructions, input=new_input, **options)
        usage["model_calls"] += 1
        usage["api_model"] = getattr(resp, "model", None) or self.settings.openai_model
        if resp.usage:
            usage["prompt_tokens"] += resp.usage.input_tokens
            usage["completion_tokens"] += resp.usage.output_tokens
            input_details = getattr(resp.usage, "input_tokens_details", None)
            output_details = getattr(resp.usage, "output_tokens_details", None)
            usage["cached_tokens"] = usage.get("cached_tokens", 0) + (getattr(input_details, "cached_tokens", 0) or 0)
            usage["reasoning_tokens"] = usage.get("reasoning_tokens", 0) + (getattr(output_details, "reasoning_tokens", 0) or 0)
        calls = [(c.call_id, c.name, c.arguments) for c in resp.output if getattr(c, "type", None) == "function_call"]
        return (resp.output_text or "").strip(), calls, resp.id

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
        sources = [] if error else sources_for(name, args, output)        # from what the model saw, with the private keys still in
        text = json.dumps(without_private(output), separators=(",", ":"))
        step = Step(turn=turn, tool=name, args=args, ms=int((time.monotonic() - started) * 1000),
                    result_chars=len(text), error=error, sources=sources)
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
                "usage": r.usage, "error": r.error, "contextTurns": r.context_turns, "steps": [asdict(s) for s in r.steps],
            }
            with (self.settings.trace_dir / "traces.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            log.warning("could not write trace: %s", exc)
