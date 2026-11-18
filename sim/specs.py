"""Deterministic specification checks: y(k) -> pass/fail table.

This is the ground truth for feasibility. The LLM critic reads this table and adds
judgement, but it cannot overturn a FAIL here (any attempt is logged as a
critic/code disagreement for the D7 assessment).
"""
from __future__ import annotations

import math

from .design import Design, validate

# Free-air (no heatsink) ratings at 25 C ambient, from the datasheets:
#   IRF9520  Tj 150 C, RthJA 62 C/W -> 2.0 W ; ID 6.8 A
#   IRLZ44N  Tj 175 C, RthJA 62 C/W -> 2.4 W ; ID 47 A
#   2N7000   PD 0.40 W (TO-92)               ; ID 0.2 A continuous
#   CD4007   0.5 W package, 10 mA per pin; 0.1 W per device used as a per-transistor cap
#   resistors 0.25 W (1/4 W through-hole)
RATINGS = {
    "irf9520_w": 2.0, "irlz44n_w": 2.4, "2n7000_w": 0.40, "2n7000_a": 0.20,
    "cd4007_w": 0.10, "cd4007_a": 0.010, "resistor_w": 0.25,
}

# Open-loop gain / bandwidth measurement method. "follower" is the method the team used
# in Multisim (unity-gain follower, A = Vodc/(Vin - Vodc)); "loop_break" is the
# classic L-C loop break (DC feedback through 1 GH, AC-grounded inverting input).
OL_METHOD = "follower"

SPEC_TABLE = [
    # id, description, limit text
    ("av_ol", "Open-loop |Av|", ">= 25 V/V"),
    ("rin", "Input resistance", ">= 200 kOhm"),
    ("f3db_ol", "Open-loop -3 dB bandwidth", "> 200 kHz"),
    ("hd", "Harmonic distortion, 0.5 Vpk (1 Vpp) in, 10 kHz, inverting feedback", "every harmonic < -30 dBc"),
    ("swing", "Output swing into 8 ohm", ">= 2 V peak, both polarities, harmonics < -30 dBc"),
    ("vcc", "Supply voltage(s)", "<= 5 V each"),
    ("std_values", "Standard component values", "E24 resistors, E12 capacitors"),
    ("ratings", "Device and resistor power/current ratings", "within datasheet free-air ratings"),
    ("stability", "No oscillation (added sanity constraint)", "non-harmonic output content < -30 dB at 2 Vpk"),
    ("converged", "Simulator converged on every analysis", "true"),
]


def ol_metrics(y: dict, method: str = OL_METHOD) -> tuple[float, float]:
    if method == "loop_break":
        return y.get("av_ol", float("nan")), y.get("f3db_ol", float("nan"))
    return y.get("av_follower_method", float("nan")), y.get("f3db_follower_method", float("nan"))


def _num(v):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else float("nan")


def check(d: Design, y: dict, method: str = OL_METHOD) -> dict:
    """Return {"feasible": bool, "checks": {id: {"pass": bool, "value": ..., "limit": ..., "detail": str}}}."""
    c = {}
    av, bw = ol_metrics(y, method)
    av, bw = _num(av), _num(bw)
    c["av_ol"] = dict(value=av, limit=">= 25", passed=av >= 25,
                      detail=f"method={method}; loop-break |Av|={_num(y.get('av_ol')):.1f}")
    rin = min(_num(y.get("zin_ol_min")), _num(y.get("rin_cl_10k")))
    c["rin"] = dict(value=rin, limit=">= 200e3", passed=rin >= 200e3,
                    detail=f"open-loop |Zin| min 100 Hz-200 kHz={_num(y.get('zin_ol_min')):.4g}, "
                           f"closed-loop Rin @10 kHz={_num(y.get('rin_cl_10k')):.4g}")
    c["f3db_ol"] = dict(value=bw, limit="> 200e3", passed=bw > 200e3,
                        detail=f"method={method}; loop-break f3dB={_num(y.get('f3db_ol')):.4g} Hz")
    hd = _num(y.get("hdtest_hd_max_db"))
    c["hd"] = dict(value=hd, limit="< -30 dB", passed=hd < -30,
                   detail=f"THD={_num(y.get('hdtest_thd_pct')):.3g}% ({_num(y.get('hdtest_thd_db')):.1f} dB), "
                          f"beta={d.beta:.3f}, Vout fundamental={_num(y.get('hdtest_fund_pk')):.3f} Vpk")
    fund = _num(y.get("swing_fund_pk"))
    vp, vn = _num(y.get("swing_vpos_pk")), _num(y.get("swing_vneg_pk"))
    shd = _num(y.get("swing_hd_max_db"))
    c["swing"] = dict(value=min(vp, vn), limit=">= 1.95 Vpk each side, fund >= 1.98",
                      passed=(fund >= 1.98 and min(vp, vn) >= 1.95 and shd < -30),
                      detail=f"fundamental={fund:.3f} Vpk, +peak={vp:.3f}, -peak={vn:.3f}, worst harmonic={shd:.1f} dBc")
    c["vcc"] = dict(value=max(d.vcc1, d.vcc2), limit="<= 5", passed=max(d.vcc1, d.vcc2) <= 5.0 + 1e-9,
                    detail=f"VCC1={d.vcc1} V, VCC2={d.vcc2} V")
    std = [e for e in validate(d) if "standard value" in e or "outside" in e or "grid" in e]
    c["std_values"] = dict(value=len(std), limit="0 violations", passed=not std, detail="; ".join(std) or "ok")

    R = RATINGS
    over = []
    def lim(name, val, rating):
        v = _num(val)
        if not (v <= rating):
            over.append(f"{name}={v:.3g} > {rating:g}")
    lim("IRF9520 avg W", y.get("swing_p_irf9520_each"), R["irf9520_w"])
    lim("IRLZ44N avg W", y.get("swing_p_irlz44n_each"), R["irlz44n_w"])
    lim("2N7000 avg W", y.get("swing_p_2n7000"), R["2n7000_w"])
    lim("2N7000 peak A", y.get("swing_ipk_2n7000"), R["2n7000_a"])
    lim("IRF9520 quiescent W", y.get("pq_irf9520_each"), R["irf9520_w"])
    lim("IRLZ44N quiescent W", y.get("pq_irlz44n_each"), R["irlz44n_w"])
    lim("CD4007 W", y.get("pq_cd4007_max"), R["cd4007_w"])
    lim("CD4007 tail A", y.get("i_tail"), R["cd4007_a"])
    for r in ("rup", "rs", "rk", "r1", "r2"):
        lim(f"{r.upper()} W", y.get(f"p_{r}"), R["resistor_w"])
    c["ratings"] = dict(value=len(over), limit="0 violations", passed=not over, detail="; ".join(over) or "ok")

    nh = _num(y.get("swing_nonharm_db"))
    c["stability"] = dict(value=nh, limit="< -30 dB", passed=nh < -30,
                          detail=f"non-harmonic content at 2 Vpk={nh:.1f} dB; "
                                 f"loop-break phase margin={_num(y.get('phase_margin_deg')):.1f} deg; "
                                 f"closed-loop peaking={_num(y.get('cl_peaking_db')):.1f} dB")
    c["converged"] = dict(value=bool(y.get("converged")), limit="true", passed=bool(y.get("converged")),
                          detail="; ".join(y.get("errors", [])) or "ok")
    for v in c.values():
        v["pass"] = bool(v.pop("passed"))
    feasible = all(v["pass"] for v in c.values())
    return {"feasible": feasible, "failed": [k for k, v in c.items() if not v["pass"]], "checks": c}


def objective(y: dict) -> float:
    """p(k): average total supply power at a 2 Vpk, 10 kHz sine into 8 ohm."""
    p = y.get("swing_p_avg")
    return float(p) if isinstance(p, (int, float)) and math.isfinite(p) else float("inf")


def summary_line(d: Design, y: dict, method: str = OL_METHOD) -> str:
    r = check(d, y, method)
    av, bw = ol_metrics(y, method)
    return (f"P={objective(y):.4f} W  Pq={_num(y.get('p_q')):.4f} W  |Av|={_num(av):.1f}  f3dB={_num(bw)/1e3:.1f} kHz  "
            f"Rin={min(_num(y.get('zin_ol_min')), _num(y.get('rin_cl_10k')))/1e3:.0f} k  "
            f"HD={_num(y.get('hdtest_hd_max_db')):.1f} dB  swing={_num(y.get('swing_fund_pk')):.2f} V  "
            f"nonharm={_num(y.get('swing_nonharm_db')):.1f} dB  -> {'FEASIBLE' if r['feasible'] else 'FAIL ' + ','.join(r['failed'])}")
