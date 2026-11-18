# Summary – run 20261118-122730

Budget 20, 20 candidates simulated (k=1..20 plus baseline k=0), open-loop method `follower`.

## What the agents tried
- Baseline (hand design, ngspice) is infeasible: fails f3db_ol (136.6 kHz), std_values and stability (non-harmonic -5.3 dB). Average power at 2 Vpk is 0.7422 W, quiescent 0.4315 W.
- k=1-5: changed cc, rf, rup, rs and rk. Several candidates failed f3db_ol or av_ol (e.g. k=2 |Av| 13.96, k=5 |Av| 23.1). k=4 failed swing, stability and f3db_ol.
- k=6-14: lowered VCC1 to 4.9 V and raised r1 (5.6k, 6.2k), then raised rup/rs/rk. Feasible designs found at k=6, 10, 11, 13 and 14 (P_avg 0.6989, 0.6438, 0.6218, 0.6218, 0.5815 W).
- k=15-17: pushed rup/rs/rk higher (180-200 / 51-56 / 39-43). All failed av_ol or f3db_ol.
- k=18: scaled r1/r2 down to 3k/4.7k. This is feasible and has the lowest average power of all feasible designs.
- k=19-20: r1/r2 up to 10k/15k and then 4.3k/6.8k, and VCC2 to 4.95 V at k=20. Both fail f3db_ol.

## Best feasible design: k=18
- vcc1 4.9, vcc2 5, r1 3k, r2 4.7k, rup 180, rs 51, rk 39, cc 12p, rin 220k, rf 910k, n_out 1 (beta 0.1947).
- |Av| OL 25.7 (limit >= 25), f3dB OL 200.5 kHz (limit > 200), Rin 228.6 kOhm, worst harmonic -43.4 dBc, swing 2.00 Vpk, non-harmonic -58.3 dB.
- Average power at 2 Vpk: 0.7422 W -> 0.5658 W = **23.8 % reduction** versus the hand design simulated in ngspice. Quiescent power 0.4315 W -> 0.2202 W.
- Caution: the hand design oscillates in ngspice (not in Multisim), which inflates its ngspice power. Part of the 23.8 % comes from removing that oscillation, so quote the quiescent reduction as well. Against the Multisim hand-design figure (0.5016 W at 2.12 Vpk) the change is +12.8 % (higher). That comparison mixes simulators and should not be used as the headline.

## Binding constraints
- Open-loop f3dB (> 200 kHz) is the most frequent failure and is binding at the best design (200.5 kHz).
- Open-loop |Av| (>= 25) is also binding (25.7 at k=18), and failed at k=2, 5, 9, 15.
- Stability and non-harmonic content failed at k=0, 1, 4, 8; swing failed once (k=4, 1.996 V).
- Rin (>= 200 kOhm) and worst-harmonic limits have margin at the best design.
- Note: k=19 and k=20 show f3dB OL of 0.01 kHz in the log; the cause is unknown from the logs.

## Agent errors from the logs
**Planner proposals rejected by validation:** none are recorded in the provided logs (the only std_values failure is the baseline hand design). Unknown whether any rejections occurred that were not logged.

**Simulation-agent transcription errors:**
- k=4: reported worst_harmonic_dbc = -34.814, simulator returned -35.28.
- k=8: reported worst_harmonic_dbc = -38.367, simulator returned -38.819.

**Critic claims that disagreed with the code checks:**
- k=1: critic said swing fails; code says pass (1.985 V; limit >= 1.95 Vpk each side, fundamental >= 1.98).
- k=6: critic chose REVISE on a design passing every coded check (kept).
- k=8: critic said swing fails; code says pass (2.043).
- k=10: critic chose REVISE on a design passing every coded check (kept).
- k=11: critic chose REVISE on a design passing every coded check (kept).
- k=16: critic said stability fails; code says pass (-39.85 dB, limit < -30 dB).
- k=19: critic said av_ol fails; code says pass (164.86, limit >= 25). The design was still infeasible on f3db_ol.
- k=20: critic said stability fails; code says pass (-47.98 dB, limit < -30 dB). The design was still infeasible on f3db_ol.
