#!/usr/bin/env python3
"""Multi-agent optimization loop for the ECEN 326 final project amplifier.

    python orchestrator.py --dry-run            # no API key needed: deterministic stand-in agents
    python orchestrator.py                      # Claude agents (needs ANTHROPIC_API_KEY in .env)
    python orchestrator.py --baseline-only      # simulate and report the hand design only

Loop per iteration k (one simulator call = one unit of budget):
    Planner -> x(k) -> Simulation agent -> y(k) -> code spec check -> Critic -> verdict
Everything is written to runs/<timestamp>/ (see README for the file list).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sim.design import BASELINE, VARIABLES, Design  # noqa: E402
from sim.problem import problem_markdown  # noqa: E402
from sim.runner import simulate  # noqa: E402
from sim.specs import check, objective  # noqa: E402
from report import reporting as R  # noqa: E402


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def save(records, run):
    (run / "records.json").write_text(json.dumps(records, indent=1, default=str))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="deterministic stand-in agents, no API calls")
    ap.add_argument("--budget", type=int, default=20, help="simulator calls for candidates (baseline not counted)")
    ap.add_argument("--ol-method", choices=["follower", "loop_break"], default="follower",
                    help="open-loop gain/bandwidth measurement used for the av_ol and f3db_ol constraints")
    ap.add_argument("--baseline-only", action="store_true")
    ap.add_argument("--max-cost", type=float, default=20.0, help="stop early if API spend passes this many dollars")
    ap.add_argument("--run-dir", type=Path, default=None)
    a = ap.parse_args()
    load_env()

    if not a.dry_run and not a.baseline_only and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Put it in .env (see .env.example) or use --dry-run.")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = a.run_dir or ROOT / "runs" / (stamp + ("-dry" if a.dry_run else "") + ("-baseline" if a.baseline_only else ""))
    run.mkdir(parents=True, exist_ok=True)
    problem = problem_markdown(a.ol_method, a.budget)
    (run / "D2_problem_statement.md").write_text(problem)
    transcript = run / "transcripts.jsonl"
    config = {"started": stamp, "dry_run": a.dry_run, "budget": a.budget, "ol_method": a.ol_method,
              "planner_model": None if a.dry_run else os.environ.get("PLANNER_MODEL", "claude-opus-5-5"),
              "worker_model": None if a.dry_run else os.environ.get("WORKER_MODEL", "claude-sonnet-5-5")}
    (run / "config.json").write_text(json.dumps(config, indent=2))
    log = (run / "run.log").open("a")

    def say(s):
        line = f"[{datetime.now():%H:%M:%S}] {s}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    # ---- k = 0: the hand design, simulated by the same code (not counted in the budget)
    say("k=0 simulating the hand design (baseline)")
    y0 = simulate(BASELINE, run / "sims" / "k00")
    c0 = check(BASELINE, y0, a.ol_method)
    records = [{"k": 0, "design": BASELINE.to_dict(), "metrics": y0, "check": c0, "decision": "BASELINE",
                "rationale": "hand design from the team's schematic", "critic_reason": "", "disagreements": []}]
    save(records, run)
    say(f"k=0 P_avg={objective(y0):.4f} W, failed: {c0['failed'] or 'none'}")

    from agents import roles  # imported here so --baseline-only needs nothing else

    simulated = {roles.design_key(BASELINE): 0}
    if a.baseline_only:
        planner = sim_agent = critic = None
    elif a.dry_run:
        planner, sim_agent, critic = roles.DryPlanner(simulated), roles.DrySimulationAgent(simulate), roles.DryCritic()
    else:
        planner = roles.Planner(problem, transcript, simulated)
        sim_agent = roles.SimulationAgent(transcript, simulate)
        critic = roles.Critic(problem, transcript)

    def spend():
        if a.dry_run or planner is None:
            return 0.0
        return planner.llm.usage.cost + sim_agent.llm.usage.cost + critic.llm.usage.cost

    advice = ""
    for k in range(1, 0 if a.baseline_only else a.budget + 1):
        if spend() > a.max_cost:
            say(f"stopping: API spend ${spend():.2f} passed --max-cost ${a.max_cost:.2f}")
            break
        t0 = time.time()
        try:
            d, rationale, expected = planner.propose(k, records, a.ol_method, advice)
        except RuntimeError as e:
            say(f"planner stopped: {e}")
            break
        say(f"k={k} planner: {json.dumps({v: getattr(d, v) for v in VARIABLES})}")
        y, sim_rep = sim_agent.run(k, d, run / "sims" / f"k{k:02d}")
        simulated[roles.design_key(d)] = k
        chk = check(d, y, a.ol_method)
        verdict = critic.review(k, d, y, chk, rationale, sim_rep, records, a.ol_method)
        advice = verdict.get("advice_to_planner", "")
        move = None
        if d.label.startswith("move "):
            _, var, dr = d.label.split()
            move = [var, int(dr)]
        records.append({"k": k, "design": d.to_dict(), "metrics": y, "check": chk, "move": move,
                        "rationale": rationale, "expected_effect": expected,
                        "sim_report": sim_rep, "decision": verdict["decision"],
                        "llm_decision": verdict.get("llm_decision"),
                        "critic_reason": verdict.get("reason", ""), "advice_to_planner": advice,
                        "constraint_claims": verdict.get("constraint_claims", {}),
                        "disagreements": verdict.get("disagreements", []),
                        "seconds": round(time.time() - t0, 1), "api_cost_so_far": round(spend(), 4)})
        save(records, run)
        R.write_iteration_log(records, run, a.ol_method)
        R.plot_convergence(records, run, a.ol_method)
        b = R.best_feasible(records)
        say(f"k={k} P_avg={objective(y):.4f} W  feasible={chk['feasible']}  failed={chk['failed'] or '-'}  "
            f"critic={verdict['decision']}  best={'k=%d %.4f W' % (b['k'], objective(b['metrics'])) if b else 'none'}")

    # ---- disagreement log (D7 input) and cost
    dis = []
    for r in records:
        for x in r.get("disagreements", []):
            dis.append(f"k={r['k']} critic vs code: {x}")
        for x in (r.get("sim_report") or {}).get("transcription_errors", []):
            dis.append(f"k={r['k']} simulation agent: {x}")
    if planner is not None:
        dis += [f"planner: {e}" for e in planner.errors] + [f"simulation agent: {e}" for e in sim_agent.errors]
    (run / "D7_agent_errors.md").write_text("# Agent errors and critic/code disagreements\n\n" +
                                            ("\n".join(f"- {x}" for x in dis) if dis else "None logged.") + "\n")

    def deliverables():
        p = R.write_iteration_log(records, run, a.ol_method)
        R.plot_convergence(records, run, a.ol_method)
        cmp_ = R.write_comparison(records, run, a.ol_method)
        # D6 for the hand design always; for the best feasible design when there is one
        base_dir = run / "baseline_full"
        simulate(BASELINE, base_dir / "sim", full=True)
        R._plots_full(base_dir / "sim", base_dir, BASELINE)
        d6 = R.export_final(records, run, a.ol_method, lambda d, o: simulate(d, o, full=True))
        b = R.best_feasible(records)
        return (f"Wrote {p[0].name}, {p[1].name}, D4_convergence.png, D4_constraints.png, {cmp_.name}, "
                f"{'D6_final_design/ (k=%d)' % b['k'] if d6 else 'no D6 (no feasible design)'}.\n\n"
                + cmp_.read_text())

    ctx = (f"Run directory: {run.name}. Budget {a.budget}, simulated {len(records) - 1} candidates, "
           f"open-loop method {a.ol_method}.\n\nAgent error / disagreement log:\n" +
           ((run / "D7_agent_errors.md").read_text()) + "\n\nHistory:\n" + roles.history_table(records, a.ol_method))
    say("reporting")
    if a.dry_run or a.baseline_only:
        roles.DryReporter().write(deliverables, ctx, run)
        rep = None
    else:
        rep = roles.Reporter(transcript)
        try:
            rep.write(deliverables, ctx, run)
        except Exception as e:  # never lose the run's data because the summary failed
            say(f"reporter failed ({e}); writing deliverables without the summary")
            deliverables()

    cost = {}
    if planner is not None and not a.dry_run:
        for ag in (planner, sim_agent, critic) + ((rep,) if rep else ()):
            u = ag.llm.usage
            cost[ag.llm.role] = {"model": ag.llm.model, "calls": u.calls, "input_tokens": u.input_tokens,
                                 "output_tokens": u.output_tokens, "cache_read": u.cache_read,
                                 "cache_write": u.cache_write, "usd": round(u.cost, 4)}
        cost["total_usd"] = round(sum(v["usd"] for v in cost.values()), 4)
    (run / "cost.json").write_text(json.dumps(cost, indent=2))
    shutil.copy(ROOT / "docs" / "architecture.mmd", run / "D1_architecture.mmd") if (ROOT / "docs" / "architecture.mmd").exists() else None
    b = R.best_feasible(records)
    say(f"done. best feasible: {('k=%d, %.4f W' % (b['k'], objective(b['metrics']))) if b else 'none'}; "
        f"API cost ${cost.get('total_usd', 0):.2f}; outputs in {run}")


if __name__ == "__main__":
    main()
