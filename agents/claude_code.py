"""Claude Code backend: each agent turn is one non-interactive `claude -p` call.

Same interface as agents.llm.AgentLLM, so the agents in roles.py do not care which
backend runs them. Use it with `python orchestrator.py --backend claude-code`.

* Signs in with whatever account the local `claude` CLI is logged in to (a Claude
  subscription or an API key); no ANTHROPIC_API_KEY is needed in .env.
* Claude Code's own tools are switched off (--tools "", no MCP servers, no settings
  files, no slash commands), and every call runs in an empty scratch directory, so an
  agent cannot read or change files or run commands. It can only answer.
* The Python tools are emulated: the agent replies with JSON naming one tool and its
  input (enforced with --json-schema); this code runs the tool and sends the result back
  in the next call. The simulator is therefore still invoked from Python, as with the API.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .llm import Tool, Usage

TIMEOUT_S = int(os.environ.get("CLAUDE_CODE_TIMEOUT", "900"))

_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "name of the ONE tool to call now, or \"finish\""},
        "input": {"type": "object", "description": "the tool's input, matching its input schema"},
        "note": {"type": "string", "description": "optional short remark (logged)"},
    },
    "required": ["tool", "input"],
}


def cli_available() -> str | None:
    return shutil.which(os.environ.get("CLAUDE_CLI", "claude"))


class ClaudeCodeLLM:
    """One role bound to one model, run through the Claude Code CLI."""

    def __init__(self, role: str, model: str, system: str, transcript: Path,
                 effort: str = "medium", max_tokens: int = 16000):
        self.cli = cli_available()
        if not self.cli:
            raise RuntimeError("the `claude` command was not found. Install Claude Code and sign in "
                               "(run `claude` once), or use --backend api.")
        self.role, self.model, self.system = role, model, system
        self.effort, self.max_tokens = effort, max_tokens
        self.transcript = transcript
        self.usage = Usage()
        self.workdir = Path(tempfile.mkdtemp(prefix=f"agent-{role}-"))  # empty: nothing to read

    def _log(self, kind: str, payload) -> None:
        with self.transcript.open("a") as f:
            f.write(json.dumps({"t": time.time(), "role": self.role, "model": self.model, "backend": "claude-code",
                                "kind": kind, "data": payload}, default=str) + "\n")

    def _call(self, prompt: str, tool_names: list[str]) -> dict:
        schema = json.loads(json.dumps(_REPLY_SCHEMA))
        schema["properties"]["tool"]["enum"] = tool_names + ["finish"]
        cmd = [self.cli, "-p", "--model", self.model, "--effort", self.effort,
               "--system-prompt", self.system,
               "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands",
               "--permission-mode", "dontAsk", "--no-session-persistence",
               "--output-format", "json", "--json-schema", json.dumps(schema)]
        last_err = ""
        for attempt in range(3):
            try:
                p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                   timeout=TIMEOUT_S, cwd=self.workdir)
                out = json.loads(p.stdout)
            except subprocess.TimeoutExpired:
                last_err = f"timed out after {TIMEOUT_S} s"
                continue
            except json.JSONDecodeError:
                last_err = f"exit {p.returncode}: {(p.stderr or p.stdout)[-800:]}"
                time.sleep(5 * (attempt + 1))
                continue
            u = out.get("usage") or {}
            self.usage.input_tokens += u.get("input_tokens", 0) or 0
            self.usage.output_tokens += u.get("output_tokens", 0) or 0
            self.usage.cache_read += u.get("cache_read_input_tokens", 0) or 0
            self.usage.cache_write += u.get("cache_creation_input_tokens", 0) or 0
            self.usage.calls += 1
            self.usage.cost += out.get("total_cost_usd", 0) or 0
            self._log("response", {k: out.get(k) for k in ("is_error", "result", "structured_output", "usage",
                                                            "total_cost_usd", "num_turns", "stop_reason")})
            if out.get("is_error"):
                last_err = str(out.get("result"))[:800]
                time.sleep(5 * (attempt + 1))
                continue
            reply = out.get("structured_output")
            if not isinstance(reply, dict):
                try:
                    reply = json.loads(out.get("result") or "")
                except json.JSONDecodeError:
                    last_err = "no JSON reply"
                    continue
            return reply
        raise RuntimeError(f"{self.role}: claude -p failed: {last_err}")

    def run(self, user_text: str, tools: list[Tool], max_turns: int = 8,
            must_call: str | None = None) -> dict:
        by_name = {t.name: t for t in tools}
        tool_doc = "\n".join(f"- `{t.name}`: {t.description}\n  input schema: {json.dumps(t.input_schema)}"
                             for t in tools)
        history: list[str] = []
        calls = []
        nudged = False
        self._log("request", {"user": user_text})
        text = ""
        for _ in range(max_turns):
            prompt = (f"{user_text}\n\n---\nYou act only through these tools. Each reply calls exactly ONE tool by "
                      f"returning JSON {{\"tool\": name, \"input\": {{...}}}}. When you are finished, reply "
                      f"{{\"tool\": \"finish\", \"input\": {{}}}}.\n{tool_doc}\n")
            if history:
                prompt += "\n---\nWhat happened so far in this task:\n" + "\n".join(history)
            reply = self._call(prompt, list(by_name))
            name, inp = reply.get("tool"), reply.get("input") or {}
            text = reply.get("note", "") or text
            if name == "finish" or name not in by_name:
                if must_call and must_call not in {c[0] for c in calls} and not nudged:
                    nudged = True
                    history.append(f"[note] You have not called `{must_call}` yet. Call it now; "
                                   "it is the only way your answer is recorded.")
                    continue
                break
            tool = by_name[name]
            missing = [k for k in tool.input_schema.get("required", []) if k not in inp]
            if missing:
                out, err = f"input is missing required fields: {missing}", True
            else:
                try:
                    out, err = tool.fn(dict(inp))
                except Exception as e:  # tool bug: report to the agent, keep going
                    out, err = f"tool error: {e}", True
            calls.append((name, dict(inp), out, err))
            self._log("tool", {"name": name, "input": inp, "result": out, "is_error": err})
            history.append(f"[{len(calls)}] you called `{name}` with {json.dumps(inp)}\n"
                           f"    {'ERROR' if err else 'result'}: {out}")
            if name == must_call and not err:
                break  # the recorded answer is in; no extra turn needed
        return {"text": text, "tool_calls": calls}
