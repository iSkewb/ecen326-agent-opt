# ECEN 326 final project: multi-agent amplifier optimization

A Python loop around Claude (through the API or the Claude Code CLI) that lets four agents (Planner, Simulation,
Verification/critic, Reporting) tune the team's two-stage CMOS power amplifier for
minimum supply power, with ngspice as the simulator. Topology and device types are
fixed; component values and supplies are the design variables.

Private for now; to be shared with teammates and the TA later.

## What it optimizes

* **Objective:** average total supply power (VCC1 + VCC2) at a 10 kHz, 2.0 Vpk sine into 8 ohm, closed loop. Quiescent power is logged too.
* **Variables:** VCC1 3.5 to 5 V, VCC2 4.5 to 5 V (0.05 V steps), R1, R2, Rup, RS, Rk, Rin (>= 220k), Rf (E24), CC 4.7 to 47 pF (E12), 1 or 2 output devices.
* **Constraints:** the assignment's specs plus device ratings, standard values, convergence, and a no-oscillation check. The full statement the agents see is `docs/problem_statement.md` (D2), generated from the same tables the code enforces.
* **Budget:** 20 simulator calls per run (the hand design is simulated once more as k = 0 and not counted).

## Layout

| Path | What it is |
|---|---|
| `orchestrator.py` | the loop and command line |
| `agents/llm.py` | Messages API backend: tool loop, transcripts, token and dollar accounting |
| `agents/claude_code.py` | Claude Code backend: the same agents through `claude -p` |
| `agents/roles.py` | the four agents, plus deterministic dry-run stand-ins |
| `agents/prompts/*.md` | each agent's role prompt |
| `sim/netlist.py`, `sim/runner.py` | netlist templates, ngspice runs, measurements y(k) |
| `sim/specs.py` | deterministic pass/fail checks (ground truth for feasibility) |
| `sim/design.py` | variables, ranges, E24/E12, the hand design |
| `models/` | device models (see `docs/baseline_validation.md`) |
| `report/reporting.py` | D3 to D6 tables, plots and exports |
| `baseline/` | full simulation set of the hand design |
| `docs/` | D1 architecture diagram, D2 problem statement, baseline validation |

## Setup

Linux or macOS:

```
sudo apt install ngspice        # or: brew install ngspice. ngspice-42 was used; any recent version works
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Windows (PowerShell): see [Windows](#windows) below.

Then pick how the agents reach Claude. Both run the same four agents with the same prompts,
models and checks; only the transport differs.

| Backend | Needs | Billing |
|---|---|---|
| `--backend api` (default) | `ANTHROPIC_API_KEY=...` in `.env` (key from console.anthropic.com) | API usage, roughly $10 to $15 per 20-iteration run (a 2-iteration test cost $1.20, mostly the Opus planner) |
| `--backend claude-code` | Claude Code installed and signed in (`npm install -g @anthropic-ai/claude-code`, then run `claude` once) | counts against the signed-in Claude account (subscription usage, or that account's API key); a 20-iteration run takes about an hour |

To make Claude Code the default, put `LLM_BACKEND=claude-code` in `.env`. `.env` is gitignored,
so a key never lands in the repo.

How the Claude Code backend works: each agent turn is one `claude -p` call with the agent's role
prompt as the system prompt, `--model` set per agent (Opus planner, Sonnet for the rest),
and the reply forced to JSON with `--json-schema`. Claude Code's own tools are switched off
(`--tools ""`, no MCP servers, no settings files, no slash commands) and each call runs in an
empty scratch folder, so an agent can only answer. The JSON names one of the loop's tools
(for example `propose_candidate` or `run_simulation`); Python runs that tool, including
ngspice, and sends the result back in the next call.

## Windows

The flow runs on Windows too. Differences from the Linux commands:

1. **Python:** use `python` (or `py`), not `python3`, in every command in this README.
   ```
   python -m pip install -r requirements.txt
   copy .env.example .env
   ```
2. **ngspice:** download the newest `ngspice-4x_64.7z` from the ngspice project's SourceForge page
   (Files > ng-spice-rework > the latest version folder), and extract it with 7-Zip so that
   `C:\Spice64\bin\ngspice_con.exe` exists. The flow looks there automatically. Use
   `ngspice_con.exe`, the console build; plain `ngspice.exe` opens a window and is not meant for batch runs.
   If you put it somewhere else, point the flow at it, either for the current window:
   ```
   $env:NGSPICE = "D:\tools\Spice64\bin\ngspice_con.exe"
   ```
   or permanently, by adding `NGSPICE=D:\tools\Spice64\bin\ngspice_con.exe` to `.env`.
   The search order is: `NGSPICE` (environment or `.env`), `ngspice_con` on PATH, `ngspice` on PATH,
   then `C:\Spice64\bin`, `C:\Spice\bin` and `C:\Program Files\Spice64\bin`. If none is found the
   run stops with a message listing these options.
3. **Claude Code backend:** install Claude Code for Windows (the native installer, or
   `npm install -g @anthropic-ai/claude-code`), open a new PowerShell window and run `claude` once
   to sign in. Then `python orchestrator.py --backend claude-code`. The flow finds `claude.exe`
   or `claude.cmd` on PATH; if it is elsewhere, set `CLAUDE_CLI` to its full path in `.env`.
4. Quick check that everything is wired up, with no Claude calls:
   ```
   python orchestrator.py --dry-run --budget 3
   ```

All files are read and written as UTF-8, and decks use paths relative to their own folder,
so Windows path separators and code pages do not matter.

## Running

```
python3 orchestrator.py --dry-run                     # no Claude at all: tests ngspice, checks and reports
python3 orchestrator.py                               # agent run through the API
python3 orchestrator.py --backend claude-code         # agent run through Claude Code
python3 orchestrator.py --ol-method loop_break        # use the loop-break open-loop measurement
python3 -m sim.baseline                               # re-simulate the hand design into baseline/
```

Other flags: `--budget N`, `--max-cost DOLLARS` (default 20, stops early; with Claude Code it
uses the equivalent API cost Claude Code reports), `--run-dir PATH`.
Models: the Planner uses `claude-opus-5-5`; the other three use `claude-sonnet-5-5`
(override with `PLANNER_MODEL` / `WORKER_MODEL` in `.env`).

A dry run uses a coordinate-search planner and a code-only critic; its outputs are labeled
DRY RUN and are not the agent result.

## Re-running a result (D8)

Each run folder records everything needed to check or repeat it: `config.json` (backend,
models, budget, open-loop method), `transcripts.jsonl` (every prompt, reply and tool call),
`records.json` (every x(k) and y(k)), and the netlists under `sims/`.

* **Re-check without Claude:** every candidate can be re-simulated from `records.json` with
  `sim.runner.simulate`, and `sim/specs.py` re-derives every pass/fail. No account is needed.
* **Repeat the whole agent run:** use either backend; a TA can use an API key or their own
  Claude Code sign-in. LLM output is not deterministic, so a repeat follows a different path;
  compare the best feasible design and its checks, not the exact sequence.

## Outputs (runs/&lt;timestamp&gt;/)

| File | Deliverable |
|---|---|
| `D1_architecture.mmd` | flow diagram (Mermaid; copy of `docs/architecture.mmd`) |
| `D2_problem_statement.md` | the exact problem given to the agents |
| `D3_iteration_log.csv` / `.md` | every x(k), y(k), code check, critic verdict, planner rationale |
| `D4_convergence.png`, `D4_constraints.png` | power vs iteration with best feasible marked; pass/fail map |
| `D5_comparison.md` | hand design (Multisim and ngspice) vs best feasible, % power change |
| `D6_final_design/` | final netlists, full simulation set and plots of the best design |
| `D7_agent_errors.md` | rejected proposals, transcription errors, critic/code disagreements |
| `SUMMARY.md` | the reporting agent's summary |
| `transcripts.jsonl` | every request, response and tool call, either backend (D8 reproducibility) |
| `records.json`, `config.json`, `cost.json`, `run.log` | raw records, settings, token use and cost |

Raw waveform `.txt` files under `runs/` are gitignored (large); netlists, logs and
`metrics.json` are kept, and re-running the design regenerates them.

## How the agents are kept honest

* Feasibility is decided by code (`sim/specs.py`), never by an LLM. If the critic accepts a design that fails a check, the code overrides it to REJECT and logs the disagreement.
* The simulation agent can only simulate the planner's exact x(k); its reported numbers are compared with the raw simulator output and mismatches are logged.
* Planner proposals outside the ranges, off the E24/E12 grid, or duplicating an earlier candidate are bounced back before any simulation is spent.

## Known limitations

See `docs/baseline_validation.md`: the DC operating point matches Multisim to 0.14 %, but
the level-1 models make the hand design oscillate in ngspice and the open-loop AC numbers
differ from Multisim. The open-loop measurement method (`follower` vs `loop_break`) is a
team decision.
