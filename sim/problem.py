"""D2: the problem statement, generated from the same tables the code enforces."""
from __future__ import annotations

from .design import VARIABLES, FIXED, BASELINE
from .specs import SPEC_TABLE, RATINGS, OL_METHOD


def problem_markdown(ol_method: str = OL_METHOD, budget: int = 20) -> str:
    lines = [
        "# Optimization problem (exactly as given to the agents)",
        "",
        "## Objective",
        "Minimize p(k) = average total supply power, VCC1 + VCC2, with the amplifier in the inverting",
        "closed-loop configuration delivering a 10 kHz sine of 2.0 V peak (fundamental) into the 8 ohm",
        "load. The input amplitude is set by the simulator so the output fundamental is 2.0 Vpk.",
        "Quiescent power (no signal) is logged too but is not the objective.",
        "",
        f"Simulator budget: {budget} candidate evaluations (each runs the full analysis set).",
        "",
        "## Fixed (not design variables)",
        f"- Topology: {FIXED['topology']}",
        f"- Devices: {FIXED['devices']}",
        f"- Input coupling Cin = 100 nF, output coupling 1 mF, load 8 ohm, input bias divider 2 x 100 kOhm from VCC2 (+ input at VCC2/2) with 10 uF bypass",
        "",
        "## Design variables",
        "| Variable | Unit | Allowed values | Meaning |",
        "|---|---|---|---|",
    ]
    for k, (u, kind, lo, hi, desc) in VARIABLES.items():
        allowed = {"volt": f"{lo:g} to {hi:g} in 0.05 V steps",
                   "res": f"E24 values, {lo:g} to {hi:g}",
                   "cap": f"E12 values, {lo*1e12:g} pF to {hi*1e12:g} pF",
                   "int": f"{lo} or {hi}"}[kind]
        lines.append(f"| {k} | {u} | {allowed} | {desc} |")
    lines += [
        "",
        "Feedback factor beta = Rin/(Rin+Rf); closed-loop gain magnitude is about Rf/Rin.",
        "",
        "## Constraints",
        "| # | Constraint | Limit |",
        "|---|---|---|",
    ]
    for i, (cid, desc, lim) in enumerate(SPEC_TABLE, 1):
        lines.append(f"| {i} | {desc} (`{cid}`) | {lim} |")
    lines += [
        "",
        "### How each constraint is measured",
        f"- Open-loop |Av| and -3 dB bandwidth: method `{ol_method}`. `follower` = the team's Multisim bench "
        "(unity-gain follower, A = Vodc/(Vin - Vodc), gain read at 5.72 kHz); `loop_break` = DC feedback through "
        "a 1 GH inductor with the inverting input AC-grounded by 1 F, gain read at 1 kHz. Both are logged every iteration.",
        "- Rin: the smaller of the open-loop input |Z| (minimum over 100 Hz to 200 kHz, follower bench) and the "
        "closed-loop inverting-amplifier input resistance at 10 kHz.",
        "- Harmonic distortion: inverting closed loop, 0.5 Vpk (1 Vpp) 10 kHz input, FFT of the last 10 cycles at the "
        "load; each harmonic 2..10 relative to the fundamental (power ratio, dBc).",
        "- Swing: closed loop driven for a 2.0 Vpk fundamental; both peaks >= 1.95 V and every harmonic < -30 dBc.",
        f"- Ratings (free air, 25 C): IRF9520 {RATINGS['irf9520_w']} W, IRLZ44N {RATINGS['irlz44n_w']} W, "
        f"2N7000 {RATINGS['2n7000_w']} W and {RATINGS['2n7000_a']} A, CD4007 {RATINGS['cd4007_w']} W per device and "
        f"{RATINGS['cd4007_a']*1e3:g} mA, resistors {RATINGS['resistor_w']} W.",
        "- Stability (added by us as a sanity constraint): output content that is not DC or harmonics 1..10 must be "
        "below -30 dB relative to the fundamental at 2 Vpk. This catches oscillation that the harmonic bins miss.",
        "",
        "## Baseline (hand design)",
        "| Variable | Value |",
        "|---|---|",
    ]
    for k, v in BASELINE.to_dict().items():
        if k != "label":
            lines.append(f"| {k} | {v:g} |" if isinstance(v, (int, float)) else f"| {k} | {v} |")
    return "\n".join(lines) + "\n"
