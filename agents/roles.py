"""The four agents: Planner, Simulation, Verification/critic, Reporting.

Each role exists twice: an LLM version (Claude via agents.llm) and a deterministic dry-run
stand-in with the same interface, so the whole flow can be tested with no API key.
Code, not the LLM, is the authority on feasibility (sim.specs.check) and on what the
simulator returned (raw metrics.json); every LLM claim that disagrees is logged for D7.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from sim.design import E12, E24, VARIABLES, Design, snap, validate
from sim.specs import SPEC_TABLE, check, objective, ol_metrics

from .llm import PLANNER_MODEL, WORKER_MODEL, AgentLLM, Tool

PROMPTS = Path(__file__).parent / "prompts"

# Numbers handed to the agents (the full y(k) has ~150 keys; this keeps prompts small).
KEY_METRICS = [
    "converged", "errors", "p_q", "p_q_vcc1", "p_q_vcc2", "i_vcc1", "i_vcc2", "i_tail", "i_q3",
    "i_out_q", "v_vo1", "v_vq1", "v_vq2", "v_vodc", "region_M1", "region_M2", "region_M3", "region_M4",
    "region_M6", "av_ol", "f3db_ol", "ugf_ol", "phase_margin_deg", "av_follower_method",
    "f3db_follower_method", "zin_ol_min", "rin_cl_10k", "av_cl_10k", "cl_peaking_db", "f3db_cl",
    "hdtest_fund_pk", "hdtest_thd_pct", "hdtest_hd_max_db", "hdtest_hd2_db", "hdtest_hd3_db",
    "swing_vin_pk", "swing_fund_pk", "swing_vpos_pk", "swing_vneg_pk", "swing_hd_max_db",
    "swing_thd_pct", "swing_nonharm_db", "swing_p_avg", "swing_p_avg_vcc1", "swing_p_avg_vcc2",
    "swing_p_load", "swing_p_irf9520_each", "swing_p_irlz44n_each", "swing_p_2n7000",
    "swing_ipk_2n7000", "pq_irf9520_each", "pq_irlz44n_each", "pq_2n7000", "pq_cd4007_max",
    "p_rup", "p_rs", "p_rk",
]

# Numbers the simulation agent must copy into report_results (checked against raw data).
TRANSCRIBED = {
    "p_avg_w": "swing_p_avg", "p_quiescent_w": "p_q", "av_ol_loop_break": "av_ol",
    "av_ol_follower": "av_follower_method", "f3db_follower_hz": "f3db_follower_method",
    "worst_harmonic_dbc": "hdtest_hd_max_db", "swing_fund_vpk": "swing_fund_pk",
    "nonharmonic_db": "swing_nonharm_db",
}


def _r(v):
    if isinstance(v, float):
        return v if not math.isfinite(v) else float(f"{v:.5g}")
    return v


def compact(y: dict) -> dict:
    return {k: _r(y[k]) for k in KEY_METRICS if k in y}


def design_key(d: Design) -> tuple:
    return tuple(round(getattr(d, k), 15) for k in VARIABLES)


def history_table(records: list[dict], method: str) -> str:
    """One line per simulated candidate, for the planner and critic prompts."""
    head = ("k | " + " ".join(VARIABLES) + " | P_avg W | P_q W | |Av| OL | f3dB OL kHz | Rin k | worst HD dBc | "
            "swing V | nonharm dB | code feasible | failed | critic")
    out = [head]
    for r in records:
        d = Design.from_dict(r["design"])
        y = r["metrics"]
        av, bw = ol_metrics(y, method)
        rin = min(y.get("zin_ol_min", float("nan")), y.get("rin_cl_10k", float("nan")))
        chk = r["check"]
        vals = " ".join(f"{getattr(d, k):g}" for k in VARIABLES)
        out.append(
            f"{r['k']} | {vals} | {objective(y):.4f} | {y.get('p_q', float('nan')):.4f} | {av:.4g} | {bw/1e3:.4g} | "
            f"{rin/1e3:.4g} | {y.get('hdtest_hd_max_db', float('nan')):.1f} | {y.get('swing_fund_pk', float('nan')):.3f} | "
            f"{y.get('swing_nonharm_db', float('nan')):.1f} | {chk['feasible']} | {','.join(chk['failed']) or '-'} | "
            f"{r.get('decision', '')}")
    return "\n".join(out)


def best_record(records: list[dict]) -> dict | None:
    ok = [r for r in records if r["k"] > 0 and r["check"]["feasible"] and r.get("decision") == "ACCEPT"]
    return min(ok, key=lambda r: objective(r["metrics"])) if ok else None


# =============================================================== planner

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        **{k: ({"type": "integer", "enum": [1, 2]} if kind == "int" else {"type": "number"})
           | {"description": f"{desc}. Unit {u or '-'}; range {lo:g} to {hi:g}."}
           for k, (u, kind, lo, hi, desc) in VARIABLES.items()},
        "rationale": {"type": "string", "description": "2-5 sentences of engineering reasoning, logged verbatim."},
        "expected_effect": {"type": "string", "description": "What you expect to change in P_avg and in the binding constraints."},
    },
    "required": list(VARIABLES) + ["rationale", "expected_effect"],
}


class Planner:
    """LLM planner: proposes x(k) through the propose_candidate tool; code validates it."""

    def __init__(self, problem_md: str, transcript: Path, simulated_keys: dict):
        self.llm = AgentLLM("planner", PLANNER_MODEL,
                            (PROMPTS / "planner.md").read_text() + "\n\n" + problem_md,
                            transcript, effort="high", max_tokens=32000)
        self.simulated = simulated_keys  # design_key -> k
        self.errors: list[str] = []

    def propose(self, k: int, records: list[dict], method: str, last_advice: str) -> tuple[Design, str, str]:
        accepted = {}

        def propose_candidate(inp: dict):
            d = Design.from_dict(inp)
            errs = validate(d)
            if errs:
                self.errors.append(f"k={k}: rejected proposal: " + "; ".join(errs))
                return "Rejected, fix and call again:\n- " + "\n- ".join(errs), True
            key = design_key(d)
            if key in self.simulated:
                self.errors.append(f"k={k}: proposed a duplicate of k={self.simulated[key]}")
                return (f"This exact design was already simulated at k={self.simulated[key]} (see history). "
                        "Propose a different candidate."), True
            accepted.update(design=d, rationale=inp.get("rationale", ""), expected=inp.get("expected_effect", ""))
            return f"Accepted as x({k}). It will be simulated now.", False

        tool = Tool("propose_candidate", "Submit the next candidate design x(k). Values must already be legal "
                    "(E24 resistors, E12 CC, 0.05 V supply grid, inside ranges).", _PLAN_SCHEMA, propose_candidate)
        best = best_record(records)
        msg = (f"Iteration k = {k}. Simulations left including this one: see budget in the problem statement.\n\n"
               f"History (k = 0 is the hand design, not counted in the budget):\n{history_table(records, method)}\n\n"
               f"Best feasible so far: {('k=' + str(best['k']) + f', P_avg={objective(best['metrics']):.4f} W') if best else 'none yet'}\n\n"
               f"Full measured results of the last candidate:\n{json.dumps(compact(records[-1]['metrics']))}\n\n"
               f"Deterministic check of the last candidate:\n{json.dumps(records[-1]['check']['checks'], default=str)}\n\n"
               f"Critic's advice after the last candidate:\n{last_advice or '(none)'}\n\n"
               "Propose x(k) now with propose_candidate.")
        for attempt in range(3):
            self.llm.run(msg, [tool], max_turns=6, must_call="propose_candidate")
            if accepted:
                return accepted["design"], accepted["rationale"], accepted["expected"]
            msg = "You did not get a candidate accepted. Call propose_candidate with a legal, new design."
        # last resort: snap the planner's last attempt is not available, so step the best design
        self.errors.append(f"k={k}: planner failed 3 times; dry-run heuristic used for this iteration")
        d, why = heuristic_step(records, self.simulated)
        return d, "[FALLBACK, planner failed] " + why, ""


# ---------------------------------------------------------- dry-run planner

LADDERS = {"res": E24, "cap": E12}
# coordinate moves, in the order a careful human would try them for lower power
MOVES = [("rk", +1), ("rup", +1), ("rs", -1), ("vcc2", -1), ("vcc1", -1), ("r2", -1), ("r1", +1),
         ("rk", -1), ("rup", -1), ("rs", +1), ("cc", +1), ("cc", -1), ("rf", +1), ("rf", -1),
         ("rin", +1), ("n_out", +1)]


def _step(d: Design, var: str, direction: int) -> Design | None:
    u, kind, lo, hi, _ = VARIABLES[var]
    v = getattr(d, var)
    if kind == "volt":
        nv = round(v + 0.1 * direction, 2)
    elif kind == "int":
        nv = v + direction
    else:
        series = LADDERS[kind]
        # all standard values over the decades in range
        vals = sorted({m * 10 ** e for e in range(-13, 8) for m in series if lo <= m * 10 ** e <= hi})
        vals = [float(f"{x:.6g}") for x in vals]
        cur = snap(v, series)
        i = min(range(len(vals)), key=lambda j: abs(vals[j] - cur))
        j = i + direction
        if j < 0 or j >= len(vals):
            return None
        nv = vals[j]
    if not (lo - 1e-12 <= nv <= hi + 1e-12):
        return None
    return Design.from_dict({**d.to_dict(), var: nv})


def legalize(d: Design) -> Design:
    kw = d.to_dict()
    for k, (u, kind, lo, hi, _) in VARIABLES.items():
        v = kw[k]
        if kind == "res":
            v = snap(v, E24)
        elif kind == "cap":
            v = snap(v, E12)
        elif kind == "volt":
            v = round(round(v / 0.05) * 0.05, 2)
        kw[k] = min(max(v, lo), hi)
    return Design.from_dict(kw)


def violation(chk: dict) -> float:
    """Sum of relative constraint shortfalls (0 when feasible). Used only by the dry-run search."""
    c = chk["checks"]
    def nz(v):
        return v if isinstance(v, (int, float)) and math.isfinite(v) else None
    rel = {
        "av_ol": lambda v: (25 - v) / 25, "rin": lambda v: (200e3 - v) / 200e3,
        "f3db_ol": lambda v: (200e3 - v) / 200e3, "hd": lambda v: (v + 30) / 30,
        "swing": lambda v: (1.95 - v) / 1.95, "stability": lambda v: (v + 30) / 30,
    }
    tot = 0.0
    for cid, r in c.items():
        if r["pass"]:
            continue
        v = nz(r["value"])
        tot += min(1.0, max(0.0, rel[cid](v))) + 0.05 if (cid in rel and v is not None) else 1.0
    return tot


def _score(r: dict) -> tuple:
    """Merit for the dry-run search: feasible first, then smallest total violation, then power."""
    feas = r["check"]["feasible"] and r.get("decision") == "ACCEPT"
    return (0 if feas else 1, 0.0 if feas else round(violation(r["check"]), 3), objective(r["metrics"]))


def heuristic_step(records: list[dict], simulated: dict) -> tuple[Design, str]:
    """Deterministic pattern search on the E24/E12/0.05 V grid (dry-run planner and LLM fallback).

    The start point is the best record by (feasible, fewest failed constraints, lowest power),
    legalized to standard values. Moves are tried in a fixed order, one grid step first and
    then two and three steps, skipping anything already simulated; a move that just improved
    the incumbent is tried again first.
    """
    inc = min(records, key=_score)
    base = legalize(Design.from_dict(inc["design"]))
    if design_key(base) not in simulated:
        base.label = "legalize"
        return base, f"Simulate k={inc['k']} snapped to standard values."
    order = list(MOVES)
    last = records[-1]
    if last is inc and last.get("move"):
        mv = (last["move"][0], 1 if last["move"][1] > 0 else -1)
        if mv in order:
            order.remove(mv)
        order.insert(0, mv)
    for size in (1, 2, 3):
        for var, dr in order:
            nd = _step(base, var, dr * size)
            if nd is not None and design_key(nd) not in simulated and not validate(nd):
                nd.label = f"move {var} {dr * size:+d}"
                return nd, (f"Pattern step from k={inc['k']} ({'feasible' if _score(inc)[0] == 0 else 'fails ' + ', '.join(inc['check']['failed'])}): "
                            f"{var} {getattr(base, var):g} -> {getattr(nd, var):g}.")
    raise RuntimeError("heuristic planner ran out of moves")


class DryPlanner:
    def __init__(self, simulated_keys: dict):
        self.simulated = simulated_keys
        self.errors: list[str] = []

    def propose(self, k, records, method, last_advice):
        d, why = heuristic_step(records, self.simulated)
        return d, "[DRY RUN heuristic] " + why, ""


# ============================================================ simulation

class SimulationAgent:
    """LLM simulation agent. It can only run the simulator on the planner's exact x(k)."""

    def __init__(self, transcript: Path, simulate_fn):
        self.llm = AgentLLM("simulation", WORKER_MODEL, (PROMPTS / "simulation.md").read_text(),
                            transcript, effort="low", max_tokens=8000)
        self.simulate = simulate_fn
        self.errors: list[str] = []

    def run(self, k: int, d: Design, outdir: Path) -> tuple[dict, dict]:
        state: dict = {}
        target = design_key(d)

        def run_simulation(inp: dict):
            try:
                cand = Design.from_dict(inp)
            except Exception as e:
                return f"bad design: {e}", True
            if design_key(cand) != target:
                diff = {v: (getattr(cand, v), getattr(d, v)) for v in VARIABLES if getattr(cand, v) != getattr(d, v)}
                self.errors.append(f"k={k}: tried to simulate a design different from the planner's x(k): {diff}")
                return f"Refused: these values differ from the planner's x(k) (yours, planner's): {diff}", True
            if "y" in state:
                return "Already simulated this iteration; results:\n" + json.dumps(compact(state["y"])), False
            state["y"] = self.simulate(d, outdir)
            return json.dumps(compact(state["y"])), False

        def report_results(inp: dict):
            if "y" not in state:
                return "Call run_simulation first.", True
            state["report"] = inp
            return "Recorded.", False

        props = {"design": {"type": "object"}}
        sim_schema = {"type": "object", "properties": {v: {"type": "number"} for v in VARIABLES}, "required": list(VARIABLES)}
        rep_schema = {"type": "object",
                      "properties": {**{n: {"type": "number"} for n in TRANSCRIBED},
                                     "converged": {"type": "boolean"},
                                     "anomalies": {"type": "array", "items": {"type": "string"}}},
                      "required": list(TRANSCRIBED) + ["converged", "anomalies"]}
        del props
        tools = [Tool("run_simulation", "Run the full ngspice analysis set on a design (fixed topology template).",
                      sim_schema, run_simulation),
                 Tool("report_results", "Report the key numbers (copied exactly from run_simulation output) and anomalies.",
                      rep_schema, report_results)]
        self.llm.run(f"Candidate x({k}) from the planner:\n{json.dumps({v: getattr(d, v) for v in VARIABLES})}",
                     tools, max_turns=6, must_call="report_results")
        if "y" not in state:  # the agent never ran it: run it in code so the budget is not lost
            self.errors.append(f"k={k}: simulation agent did not call run_simulation; code ran it")
            state["y"] = self.simulate(d, outdir)
        rep = state.get("report", {})
        rep["transcription_errors"] = transcription_errors(rep, state["y"])
        for e in rep["transcription_errors"]:
            self.errors.append(f"k={k}: {e}")
        return state["y"], rep


def transcription_errors(rep: dict, y: dict) -> list[str]:
    errs = []
    if not rep:
        return ["simulation agent did not call report_results"]
    for name, key in TRANSCRIBED.items():
        a, b = rep.get(name), y.get(key)
        if not isinstance(b, (int, float)) or not math.isfinite(b):
            continue
        if not isinstance(a, (int, float)) or abs(a - b) > 0.01 * abs(b) + 1e-9:
            errs.append(f"reported {name}={a} but the simulator returned {b:.5g}")
    return errs


class DrySimulationAgent:
    def __init__(self, simulate_fn):
        self.simulate = simulate_fn
        self.errors: list[str] = []

    def run(self, k, d, outdir):
        y = self.simulate(d, outdir)
        rep = {n: y.get(key) for n, key in TRANSCRIBED.items()}
        rep.update(converged=y.get("converged"), anomalies=y.get("errors", []), transcription_errors=[])
        return y, rep


# ================================================================ critic

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["ACCEPT", "REVISE", "REJECT"]},
        "constraint_claims": {
            "type": "object", "description": "For every constraint id, true if you judge it passes.",
            "properties": {cid: {"type": "boolean"} for cid, _, _ in SPEC_TABLE},
            "required": [cid for cid, _, _ in SPEC_TABLE]},
        "reason": {"type": "string", "description": "2-6 sentences: why this decision, and any concern the table does not encode."},
        "advice_to_planner": {"type": "string", "description": "1-2 concrete, quantitative suggestions for x(k+1)."},
    },
    "required": ["decision", "constraint_claims", "reason", "advice_to_planner"],
}


class Critic:
    def __init__(self, problem_md: str, transcript: Path):
        self.llm = AgentLLM("critic", WORKER_MODEL, (PROMPTS / "critic.md").read_text() + "\n\n" + problem_md,
                            transcript, effort="medium", max_tokens=16000)

    def review(self, k, d, y, chk, rationale, sim_report, records, method) -> dict:
        out = {}

        def submit_verdict(inp: dict):
            out.update(inp)
            return "Verdict recorded.", False

        tool = Tool("submit_verdict", "Submit your verdict on x(k).", _VERDICT_SCHEMA, submit_verdict)
        best = best_record(records)
        msg = (f"Candidate x({k}): {json.dumps({v: getattr(d, v) for v in VARIABLES})} (beta={d.beta:.4f})\n\n"
               f"Planner rationale: {rationale}\n\n"
               f"Measured results y({k}):\n{json.dumps(compact(y))}\n\n"
               f"Simulation agent report: {json.dumps(sim_report, default=str)}\n\n"
               f"Deterministic spec check (code, open-loop method `{method}`):\n{json.dumps(chk, default=str)}\n\n"
               f"Best feasible so far: {('k=' + str(best['k']) + f', P_avg={objective(best['metrics']):.4f} W') if best else 'none'}\n\n"
               f"History:\n{history_table(records, method)}")
        self.llm.run(msg, [tool], max_turns=4, must_call="submit_verdict")
        return reconcile(out, chk)


def reconcile(verdict: dict, chk: dict) -> dict:
    """Code has the last word on feasibility. Record every claim that disagrees."""
    v = dict(verdict) if verdict else {"decision": "REJECT", "constraint_claims": {},
                                       "reason": "critic gave no verdict", "advice_to_planner": ""}
    dis = []
    for cid, c in chk["checks"].items():
        claim = v.get("constraint_claims", {}).get(cid)
        if claim is not None and bool(claim) != c["pass"]:
            dis.append(f"{cid}: critic said {'pass' if claim else 'fail'}, code says {'pass' if c['pass'] else 'fail'} "
                       f"(value {c['value']}, limit {c['limit']})")
    v["llm_decision"] = v.get("decision")
    if v.get("decision") == "ACCEPT" and not chk["feasible"]:
        v["decision"] = "REJECT"
        dis.append(f"critic ACCEPTED a design that fails {', '.join(chk['failed'])}; overridden to REJECT")
    if v.get("decision") != "ACCEPT" and chk["feasible"]:
        dis.append(f"critic chose {v.get('decision')} on a design that passes every coded check (kept; see reason)")
    v["disagreements"] = dis
    return v


class DryCritic:
    def review(self, k, d, y, chk, rationale, sim_report, records, method):
        if chk["feasible"]:
            dec, why = "ACCEPT", "All coded constraints pass."
        else:
            narrow = len(chk["failed"]) == 1
            dec = "REVISE" if narrow else "REJECT"
            why = "Fails: " + "; ".join(f"{c} ({chk['checks'][c]['detail']})" for c in chk["failed"])
        return {"decision": dec, "llm_decision": None, "constraint_claims": {c: v["pass"] for c, v in chk["checks"].items()},
                "reason": "[DRY RUN code critic] " + why, "advice_to_planner": "", "disagreements": []}


# ============================================================== reporter

class Reporter:
    def __init__(self, transcript: Path):
        self.llm = AgentLLM("reporter", WORKER_MODEL, (PROMPTS / "reporter.md").read_text(),
                            transcript, effort="medium", max_tokens=16000)

    def write(self, make_deliverables, context: str, outdir: Path) -> str:
        out = {}

        def write_tables_and_plots(_inp):
            return make_deliverables(), False

        def write_summary(inp):
            out["md"] = inp["markdown"]
            (outdir / "SUMMARY.md").write_text(inp["markdown"])
            return "Written.", False

        tools = [Tool("write_tables_and_plots", "Generate D3-D6 from the run records.",
                      {"type": "object", "properties": {}}, write_tables_and_plots),
                 Tool("write_summary", "Write SUMMARY.md for the final report.",
                      {"type": "object", "properties": {"markdown": {"type": "string"}}, "required": ["markdown"]},
                      write_summary)]
        self.llm.run(context, tools, max_turns=6, must_call="write_summary")
        return out.get("md", "")


class DryReporter:
    def write(self, make_deliverables, context: str, outdir: Path) -> str:
        made = make_deliverables()
        md = ("# Run summary (DRY RUN: no LLM agents were called)\n\n"
              "This run used the deterministic stand-ins (coordinate-search planner, pass-through simulation agent, "
              "code-only critic, template reporter). It tests the plumbing, simulator and reports; it is not the "
              "agent result for the final report.\n\n" + made + "\n\n" + context)
        (outdir / "SUMMARY.md").write_text(md)
        return md
