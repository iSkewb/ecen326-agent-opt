"""Thin wrapper around the Anthropic Messages API with a hand-written tool-use loop.

* every request and response is appended to the run's transcript (raw log for D8)
* token usage and dollar cost are tracked per agent
* the loop executes client-side tools until the agent stops calling them
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# $ per million tokens (input, output, cache read, cache write 5 min)
PRICING = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
}

PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "claude-opus-5-5")
WORKER_MODEL = os.environ.get("WORKER_MODEL", "claude-sonnet-5-5")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0
    cost: float = 0.0

    def add(self, model: str, u) -> None:
        pin, pout, pcr, pcw = PRICING.get(model, (4.0, 20.0, 0.2, 5.0))
        it = getattr(u, "input_tokens", 0) or 0
        ot = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        self.input_tokens += it
        self.output_tokens += ot
        self.cache_read += cr
        self.cache_write += cw
        self.calls += 1
        self.cost += (it * pin + ot * pout + cr * pcr + cw * pcw) / 1e6


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], tuple[str, bool]]  # returns (result text, is_error)

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class AgentLLM:
    """One role (planner, simulation, critic, reporter) bound to one model."""

    def __init__(self, role: str, model: str, system: str, transcript: Path,
                 effort: str = "medium", max_tokens: int = 16000):
        import anthropic  # imported lazily so dry runs need no SDK or key

        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=4)
        self.role, self.model, self.system = role, model, system
        self.effort, self.max_tokens = effort, max_tokens
        self.transcript = transcript
        self.usage = Usage()
        self.use_fallbacks = os.environ.get("CLAUDE_FALLBACKS", "1") != "0"

    def _log(self, kind: str, payload) -> None:
        with self.transcript.open("a") as f:
            f.write(json.dumps({"t": time.time(), "role": self.role, "model": self.model,
                                "kind": kind, "data": payload}, default=str) + "\n")

    def _create(self, messages, tools):
        kw = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            tools=[t.spec() for t in tools],
            tool_choice={"type": "auto"},
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        if self.use_fallbacks:
            # Server-side refusal fallback (beta). Disabled automatically if the account rejects it.
            kw["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kw["extra_body"] = {"fallbacks": "default"}
        try:
            return self.client.messages.create(**kw)
        except self.anthropic.BadRequestError as e:
            if self.use_fallbacks:  # beta not available on this account: retry once without it
                self.use_fallbacks = False
                self._log("note", f"request with server-side fallbacks rejected ({e}); retrying without them")
                kw.pop("extra_headers"), kw.pop("extra_body")
                return self.client.messages.create(**kw)
            raise

    def run(self, user_text: str, tools: list[Tool], max_turns: int = 8,
            must_call: str | None = None) -> dict:
        """Run the tool loop. Returns {"text": final text, "tool_calls": [(name, input, result, is_error)]}."""
        messages = [{"role": "user", "content": user_text}]
        by_name = {t.name: t for t in tools}
        calls = []
        self._log("request", {"user": user_text})
        nudged = False
        for _ in range(max_turns):
            resp = self._create(messages, tools)
            self.usage.add(self.model, resp.usage)
            self._log("response", {"stop_reason": resp.stop_reason,
                                   "content": [b.model_dump() for b in resp.content],
                                   "usage": resp.usage.model_dump()})
            if resp.stop_reason == "refusal":
                raise RuntimeError(f"{self.role}: model refused the request ({getattr(resp, 'stop_details', None)})")
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                called = {c[0] for c in calls}
                if must_call and must_call not in called and not nudged:
                    nudged = True
                    messages.append({"role": "user", "content":
                                     f"You have not called `{must_call}` yet. Call it now; it is the only way your answer is recorded."})
                    continue
                break
            results = []
            for b in uses:
                tool = by_name.get(b.name)
                if tool is None:
                    out, err = f"unknown tool {b.name}", True
                else:
                    try:
                        out, err = tool.fn(dict(b.input))
                    except Exception as e:  # tool bug: report to the agent, keep going
                        out, err = f"tool error: {e}", True
                calls.append((b.name, dict(b.input), out, err))
                self._log("tool", {"name": b.name, "input": b.input, "result": out, "is_error": err})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})
        text = "\n".join(b.text for b in resp.content if b.type == "text")
        return {"text": text, "tool_calls": calls}


def make_agent(role: str, model: str, system: str, transcript: Path, effort: str = "medium",
               max_tokens: int = 16000):
    """Pick the backend from LLM_BACKEND: "api" (Messages API, default) or "claude-code" (`claude -p`)."""
    backend = os.environ.get("LLM_BACKEND", "api")
    if backend == "claude-code":
        from .claude_code import ClaudeCodeLLM
        return ClaudeCodeLLM(role, model, system, transcript, effort, max_tokens)
    return AgentLLM(role, model, system, transcript, effort, max_tokens)
