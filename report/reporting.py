"""Deliverables D3-D6 from the run's iteration records (deterministic code, called by the
reporting agent's tools and as a fallback by the orchestrator)."""
from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sim.design import Design, VARIABLES  # noqa: E402
from sim.specs import SPEC_TABLE, check, objective, ol_metrics  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES, GOOD, BAD = "#2a78d6", "#0ca30c", "#d03b3b"


def _f(v, nd=4):
    if isinstance(v, float):
        if not math.isfinite(v):
            return str(v)
        return f"{v:.{nd}g}"
    return str(v)


def _eng(v, unit=""):
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        return str(v)
    for div, pre in ((1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
        if abs(v) >= div:
            return f"{v/div:.3g}{pre}{unit}"
    return f"{v:.3g}{unit}"


COLS = [
    ("k", "k"), ("vcc1", "VCC1 V"), ("vcc2", "VCC2 V"), ("r1", "R1"), ("r2", "R2"), ("rup", "Rup"),
    ("rs", "RS"), ("rk", "Rk"), ("cc", "CC"), ("rin", "Rin"), ("rf", "Rf"), ("beta", "beta"), ("n_out", "N"),
    ("av", "|Av| OL"), ("f3db", "f3dB OL Hz"), ("av_lb", "|Av| loop-break"), ("f3db_lb", "f3dB loop-break Hz"),
    ("rin_meas", "Rin ohm"), ("hd", "worst HD dBc"), ("thd", "THD %"), ("swing", "swing Vpk"),
    ("vneg", "-peak V"), ("nonharm", "non-harm dB"), ("p_avg", "P avg W"), ("p_q", "P quiescent W"),
    ("feasible", "code: feasible"), ("failed", "code: failed"), ("decision", "critic"), ("reason", "critic reason"),
]


def row_for(rec: dict, method: str) -> dict:
    d = Design.from_dict(rec["design"])
    y = rec.get("metrics", {})
    av, bw = ol_metrics(y, method)
    chk = rec.get("check") or check(d, y, method)
    return {
        "k": rec["k"], **{k: getattr(d, k) for k in VARIABLES}, "beta": round(d.beta, 4),
        "av": av, "f3db": bw, "av_lb": y.get("av_ol"), "f3db_lb": y.get("f3db_ol"),
        "rin_meas": min(y.get("zin_ol_min", float("nan")), y.get("rin_cl_10k", float("nan"))),
        "hd": y.get("hdtest_hd_max_db"), "thd": y.get("hdtest_thd_pct"), "swing": y.get("swing_fund_pk"),
        "vneg": y.get("swing_vneg_pk"), "nonharm": y.get("swing_nonharm_db"),
        "p_avg": objective(y), "p_q": y.get("p_q"),
        "feasible": chk["feasible"], "failed": " ".join(chk["failed"]),
        "decision": rec.get("decision", ""), "reason": (rec.get("critic_reason") or "").replace("\n", " "),
    }


def write_iteration_log(records: list[dict], outdir: Path, method: str) -> tuple[Path, Path]:
    rows = [row_for(r, method) for r in records]
    csv_p = outdir / "D3_iteration_log.csv"
    with csv_p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[c for c, _ in COLS] + ["planner_rationale"])
        w.writeheader()
        for r, rec in zip(rows, records):
            w.writerow({**r, "planner_rationale": rec.get("rationale", "")})
    md = ["# D3 Iteration log", "",
          f"Open-loop gain/bandwidth method: `{method}`. k = 0 is the hand design (baseline), "
          "simulated with the same flow but not counted in the budget.", "",
          "| " + " | ".join(h for _, h in COLS) + " |", "|" + "---|" * len(COLS)]
    for r in rows:
        cells = []
        for c, _ in COLS:
            v = r[c]
            if c in ("r1", "r2", "rup", "rs", "rk", "rin", "rf"):
                v = _eng(v)
            elif c == "cc":
                v = _eng(v, "F")
            elif c == "reason":
                v = (v[:160] + "...") if len(v) > 160 else v
            else:
                v = _f(v)
            cells.append(str(v).replace("|", "/"))
        md.append("| " + " | ".join(cells) + " |")
    md_p = outdir / "D3_iteration_log.md"
    md_p.write_text(encoding="utf-8", data="\n".join(md) + "\n")
    return csv_p, md_p


def best_feasible(records: list[dict]) -> dict | None:
    ok = [r for r in records if r["k"] > 0 and r.get("check", {}).get("feasible") and r.get("decision") == "ACCEPT"]
    return min(ok, key=lambda r: objective(r["metrics"])) if ok else None


def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_convergence(records: list[dict], outdir: Path, method: str) -> tuple[Path, Path]:
    base = next((r for r in records if r["k"] == 0), None)
    its = [r for r in records if r["k"] > 0]
    fig, ax = plt.subplots(figsize=(8, 4.6), facecolor=SURFACE)
    _style(ax)
    ks = [r["k"] for r in its]
    ps = [objective(r["metrics"]) for r in its]
    ps_plot = [p if math.isfinite(p) else float("nan") for p in ps]
    ax.plot(ks, ps_plot, color=SERIES, linewidth=2, zorder=2, label="candidate p(k)")
    feas = [r.get("check", {}).get("feasible") and r.get("decision") == "ACCEPT" for r in its]
    ax.scatter([k for k, f in zip(ks, feas) if f], [p for p, f in zip(ps_plot, feas) if f], s=60,
               marker="o", color=GOOD, edgecolor=SURFACE, linewidth=2, zorder=3, label="accepted (feasible)")
    ax.scatter([k for k, f in zip(ks, feas) if not f], [p for p, f in zip(ps_plot, feas) if not f], s=60,
               marker="X", color=BAD, edgecolor=SURFACE, linewidth=1, zorder=3, label="rejected / infeasible")
    # running best feasible
    best, run = float("inf"), []
    for p, f in zip(ps, feas):
        if f and p < best:
            best = p
        run.append(best if math.isfinite(best) else float("nan"))
    ax.step(ks, run, where="post", color=INK2, linewidth=1.5, linestyle=":", label="best feasible so far")
    b = best_feasible(records)
    if b:
        pb = objective(b["metrics"])
        ax.scatter([b["k"]], [pb], s=220, marker="*", color=GOOD, edgecolor=INK, linewidth=1, zorder=4)
        ax.annotate(f"best feasible x*: k={b['k']}, {pb:.3f} W", (b["k"], pb), xytext=(8, -16),
                    textcoords="offset points", color=INK, fontsize=9)
    if base:
        pb0 = objective(base["metrics"])
        if math.isfinite(pb0):
            ax.axhline(pb0, color=INK2, linewidth=1.2, linestyle="--")
            ax.annotate(f"hand design (ngspice) {pb0:.3f} W", (ks[-1] if ks else 1, pb0), xytext=(0, 5),
                        textcoords="offset points", color=INK2, fontsize=9, ha="right")
    ax.set_xlabel("iteration k (simulator call)", color=INK)
    ax.set_ylabel("average supply power at 2 Vpk (W)", color=INK)
    ax.set_title("D4: power vs iteration", color=INK, fontsize=11, loc="left")
    if ks:
        ax.set_xticks(ks)
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=4, labelcolor=INK)
    fig.tight_layout()
    p1 = outdir / "D4_convergence.png"
    fig.savefig(p1, dpi=150, facecolor=SURFACE)
    plt.close(fig)

    # constraint satisfaction map
    ids = [s[0] for s in SPEC_TABLE]
    allr = [r for r in records]
    fig, ax = plt.subplots(figsize=(max(6, 0.45 * len(allr) + 2.5), 0.42 * len(ids) + 1.4), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    for j, r in enumerate(allr):
        chk = r.get("check", {}).get("checks", {})
        for i, cid in enumerate(ids):
            ok = chk.get(cid, {}).get("pass")
            col = GOOD if ok else BAD
            ax.add_patch(plt.Rectangle((j + 0.04, i + 0.04), 0.92, 0.92, color=col, linewidth=0))
            ax.text(j + 0.5, i + 0.5, "P" if ok else "F", ha="center", va="center", color="#ffffff", fontsize=8,
                    fontweight="bold")
    ax.set_xlim(0, len(allr))
    ax.set_ylim(len(ids), 0)
    ax.set_xticks([j + 0.5 for j in range(len(allr))])
    ax.set_xticklabels(["base" if r["k"] == 0 else str(r["k"]) for r in allr], fontsize=8, color=INK2)
    ax.set_yticks([i + 0.5 for i in range(len(ids))])
    ax.set_yticklabels(ids, fontsize=9, color=INK)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    ax.set_xlabel("iteration k  (P = pass, F = fail; deterministic code check)", color=INK, fontsize=9)
    ax.set_title("D4: constraint satisfaction per iteration", color=INK, fontsize=11, loc="left")
    fig.tight_layout()
    p2 = outdir / "D4_constraints.png"
    fig.savefig(p2, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return p1, p2


MULTISIM = {  # hand design, Multisim, preliminary report
    "av": 36.99, "f3db": 209.63e3, "rin": 491.6e3, "p_q": 0.43093, "p_avg": 0.5016, "swing": 2.12,
}


def write_comparison(records: list[dict], outdir: Path, method: str) -> Path:
    base = next(r for r in records if r["k"] == 0)
    best = best_feasible(records)
    def col(rec):
        if rec is None:
            return None
        d = Design.from_dict(rec["design"])
        y = rec["metrics"]
        av, bw = ol_metrics(y, method)
        c = rec.get("check") or check(d, y, method)
        return d, y, av, bw, c
    B, X = col(base), col(best)
    lines = ["# D5 Hand design vs agent-optimized design", "",
             f"Open-loop method: `{method}`. Both ngspice columns use the same models and test benches.", ""]
    hdr = "| Item | Spec | Hand design (Multisim, prelim report) | Hand design (ngspice) | Agent best feasible (ngspice) |"
    lines += [hdr, "|---|---|---|---|---|"]
    def v(c, key, fmt):
        if c is None:
            return "none feasible"
        d, y, av, bw, chk = c
        val = {"av": av, "f3db": bw, "rin": min(y.get("zin_ol_min", float("nan")), y.get("rin_cl_10k", float("nan"))),
               "hd": y.get("hdtest_hd_max_db"), "thd": y.get("hdtest_thd_pct"), "swing": y.get("swing_fund_pk"),
               "p_avg": objective(y), "p_q": y.get("p_q"), "vcc1": d.vcc1, "vcc2": d.vcc2,
               "nonharm": y.get("swing_nonharm_db")}[key]
        cid = {"av": "av_ol", "f3db": "f3db_ol", "rin": "rin", "hd": "hd", "swing": "swing",
               "nonharm": "stability"}.get(key)
        mark = ""
        if cid:
            mark = " (pass)" if chk["checks"][cid]["pass"] else " (FAIL)"
        return fmt(val) + mark
    ms = MULTISIM
    rows = [
        ("Open-loop abs(Av)", ">= 25 V/V", f"{ms['av']}", "av", lambda x: f"{x:.1f}"),
        ("Open-loop f3dB", "> 200 kHz", f"{ms['f3db']/1e3:.1f} kHz", "f3db", lambda x: f"{x/1e3:.1f} kHz"),
        ("Rin", ">= 200 kOhm", f"{ms['rin']/1e3:.1f} kOhm (open loop)", "rin", lambda x: f"{x/1e3:.1f} kOhm"),
        ("Worst harmonic, 1 Vpp in", "< -30 dBc", "-77.6 dB THD at beta 0.5", "hd", lambda x: f"{x:.1f} dBc"),
        ("THD, 1 Vpp in", "report", "0.0131 % (beta 0.5)", "thd", lambda x: f"{x:.3g} %"),
        ("Output swing", ">= 2 Vpk", f"{ms['swing']} Vpk", "swing", lambda x: f"{x:.2f} Vpk"),
        ("Non-harmonic content at 2 Vpk", "< -30 dB", "not measured", "nonharm", lambda x: f"{x:.1f} dB"),
        ("VCC1 (stage 1 + buffer)", "<= 5 V", "5.0 V", "vcc1", lambda x: f"{x:.2f} V"),
        ("VCC2 (output stage)", "<= 5 V", "5.0 V", "vcc2", lambda x: f"{x:.2f} V"),
        ("Quiescent power", "log", f"{ms['p_q']:.4f} W", "p_q", lambda x: f"{x:.4f} W"),
        ("Average power at 2 Vpk (objective)", "minimize", f"{ms['p_avg']:.4f} W (at 2.12 Vpk)", "p_avg", lambda x: f"{x:.4f} W"),
    ]
    for name, spec, msv, key, fmt in rows:
        lines.append(f"| {name} | {spec} | {msv} | {v(B, key, fmt)} | {v(X, key, fmt)} |")
    lines.append("")
    if X is None:
        lines.append("**The agents did not find a feasible design that the critic accepted, so they did not improve "
                     "on the hand design.**")
    else:
        pb, px = objective(B[1]), objective(X[1])
        lines.append(f"Feasible in ngspice: hand design **{'yes' if B[4]['feasible'] else 'no (fails ' + ', '.join(B[4]['failed']) + ')'}**, "
                     f"agent design **yes** (k = {best['k']}).")
        if math.isfinite(pb):
            ch = 100 * (pb - px) / pb
            word = "reduction" if ch > 0 else "increase"
            lines.append(f"Average power at 2 Vpk: {pb:.4f} W -> {px:.4f} W, a {abs(ch):.1f} % {word} "
                         f"against the hand design simulated in ngspice.")
        if not B[4]["checks"]["stability"]["pass"]:
            lines.append("Caution: the hand design oscillates in the ngspice model (it did not in Multisim), which raises its "
                         "ngspice power at 2 Vpk. Part of this reduction is removing that oscillation, so also quote the "
                         f"quiescent power ({B[1].get('p_q', float('nan')):.4f} W -> {X[1].get('p_q', float('nan')):.4f} W).")
        ms_ch = 100 * (ms["p_avg"] - px) / ms["p_avg"]
        lines.append(f"Against the Multisim figure for the hand design ({ms['p_avg']} W) the change is "
                     f"{-ms_ch:+.1f} %; that comparison mixes two simulators and models, so quote the ngspice-to-ngspice number.")
        lines += ["", "## Agent-optimized design values", "", "| Variable | Hand design | Agent design |", "|---|---|---|"]
        for k in VARIABLES:
            lines.append(f"| {k} | {_eng(getattr(B[0], k))} | {_eng(getattr(X[0], k))} |")
        lines.append(f"| beta | {B[0].beta:.4f} | {X[0].beta:.4f} |")
    p = outdir / "D5_comparison.md"
    p.write_text(encoding="utf-8", data="\n".join(lines) + "\n")
    return p


def export_final(records: list[dict], outdir: Path, method: str, simulate_full) -> Path | None:
    """D6: re-simulate the best feasible design with the full analysis set and copy netlists + data."""
    best = best_feasible(records)
    if best is None:
        return None
    d6 = outdir / "D6_final_design"
    d = Design.from_dict(best["design"])
    y = simulate_full(d, d6 / "sim")
    for cir in (d6 / "sim").glob("*.cir"):
        shutil.copy(cir, d6 / cir.name)
    (d6 / "design.json").write_text(encoding="utf-8", data=json.dumps(d.to_dict(), indent=2))
    (d6 / "check.json").write_text(encoding="utf-8", data=json.dumps(check(d, y, method), indent=2, default=str))
    _plots_full(d6 / "sim", d6, d)
    return d6


def _plots_full(simdir: Path, outdir: Path, d: Design, tag: str = "") -> None:
    """AC gain, Rin, transients and spectrum plots for a full simulation set."""
    import numpy as np

    def load(name):
        p = simdir / name
        return np.loadtxt(p, skiprows=1, ndmin=2) if p.exists() else None

    ol, fol, cl = load("ac_ol.txt"), load("ac_fol.txt"), load("ac_cl.txt")
    fig, axs = plt.subplots(1, 2, figsize=(10, 3.8), facecolor=SURFACE)
    for ax in axs:
        _style(ax)
    if ol is not None:
        f = ol[:, 0]
        axs[0].semilogx(f, 20 * np.log10(np.abs(ol[:, 1] + 1j * ol[:, 2])), color=SERIES, lw=2, label="loop-break")
    if fol is not None:
        f = fol[:, 0]
        vod, vin = fol[:, 1] + 1j * fol[:, 2], fol[:, 3] + 1j * fol[:, 4]
        axs[0].semilogx(f, 20 * np.log10(np.abs(vod / (vin - vod))), color="#eb6834", lw=2, label="follower method")
        iin = fol[:, 5] + 1j * fol[:, 6]
        axs[1].loglog(f, np.abs(vin / iin), color=SERIES, lw=2, label="open loop |Zin|")
    if cl is not None:
        f = cl[:, 0]
        axs[1].loglog(f, np.abs(1 / (cl[:, 5] + 1j * cl[:, 6])), color="#eb6834", lw=2, label="closed loop Rin")
    axs[0].set_title("open-loop gain (dB)", color=INK, fontsize=10, loc="left")
    axs[1].set_title("input impedance (ohm)", color=INK, fontsize=10, loc="left")
    for ax in axs:
        ax.set_xlabel("frequency (Hz)", color=INK)
        ax.set_xlim(100, 1e8)
        ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(outdir / f"ac{tag}.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)

    for name, title in (("tran_hd", "closed loop, 0.5 Vpk in"), ("tran_pw", "closed loop, 2 Vpk out"),
                        ("tran_ol", "open loop, 5 mVpk in")):
        a = load(f"{name}.txt")
        if a is None:
            continue
        t, vo = a[:, 0], a[:, 1]
        fig, axs = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=SURFACE)
        for ax in axs:
            _style(ax)
        m = t >= t[-1] - 5e-4
        axs[0].plot(t[m] * 1e3, vo[m], color=SERIES, lw=2)
        axs[0].set_xlabel("time (ms)", color=INK)
        axs[0].set_ylabel("V(vo) (V)", color=INK)
        axs[0].set_title(f"transient, {title}", color=INK, fontsize=10, loc="left")
        T = 10 / 10e3
        mm = t >= t[-1] - T
        n = 4096
        ts = np.linspace(t[-1] - T, t[-1], n, endpoint=False)
        vs = np.interp(ts, t[mm], vo[mm])
        sp = np.abs(np.fft.rfft(vs)) / n * 2
        fr = np.fft.rfftfreq(n, ts[1] - ts[0])
        fund = sp[10] if sp[10] > 0 else 1
        k = fr > 0
        axs[1].semilogx(fr[k] / 1e3, 20 * np.log10(np.maximum(sp[k], 1e-12) / fund), color=SERIES, lw=1.5)
        axs[1].set_xlabel("frequency (kHz)", color=INK)
        axs[1].set_ylabel("dB relative to fundamental", color=INK)
        axs[1].set_ylim(-140, 5)
        axs[1].set_title("spectrum, last 10 cycles (up to 2 MHz)", color=INK, fontsize=10, loc="left")
        fig.tight_layout()
        fig.savefig(outdir / f"{name}{tag}.png", dpi=150, facecolor=SURFACE)
        plt.close(fig)
