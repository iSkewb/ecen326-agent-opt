# ECEN 326 final project: multi-agent amplifier optimization

A Python loop around the Claude API that lets four agents (Planner, Simulation,
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
| `agents/llm.py` | Messages API wrapper: tool loop, transcripts, token and dollar accounting |
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

```
sudo apt install ngspice        # ngspice-42 was used; any recent version should work
python3 -m pip install -r requirements.txt
cp .env.example .env            # then paste your Anthropic API key after ANTHROPIC_API_KEY=
```

`.env` is gitignored, so the key never lands in the repo. Get a key at console.anthropic.com.

## Running

```
python3 orchestrator.py --dry-run          # no key needed: tests ngspice, checks and reports
python3 orchestrator.py                    # the real agent run (about $5 to $10 for 20 iterations)
python3 orchestrator.py --ol-method loop_break   # use the loop-break open-loop measurement
python3 -m sim.baseline                    # re-simulate the hand design into baseline/
```

Other flags: `--budget N`, `--max-cost DOLLARS` (default 20, stops early), `--run-dir PATH`.
Models: the Planner uses `claude-opus-5-5`; the other three use `claude-sonnet-5-5`
(override with `PLANNER_MODEL` / `WORKER_MODEL` in `.env`).

A dry run uses a coordinate-search planner and a code-only critic; its outputs are labeled
DRY RUN and are not the agent result.

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
| `transcripts.jsonl` | every API request, response and tool call (D8 reproducibility) |
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
