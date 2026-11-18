# Baseline validation: ngspice vs the team's Multisim results

The hand design (schematic in the final-circuit image: CD4007 diff pair, 2N7000 buffer,
IRF9520/IRLZ44N class AB, CC 10 pF, Rup 150, RS 47, Rk 47, R1 5k / R2 10k, Rin 220k, Rf 950k,
VCC1 = VCC2 = 5 V) was simulated with `python -m sim.baseline`. Results are in `baseline/`.

## Device models

Vendor PSpice libraries could not be downloaded from this environment, so `models/` holds
level-1 MOSFET models built from the parameters in the preliminary report's
"Values Extracted From Datasheet / Multisim" table (see the comments in each `.lib`).
Two threshold voltages were then calibrated to the team's Multisim operating point:

| Device | Report Vth | Calibrated Vth | Why |
|---|---|---|---|
| IRLZ44N | 2.09 V | 2.066 V | report value gave 42.7 mA output-stage current instead of 59.6 mA |
| 2N7000 | 2.24 V | 1.98 V | report value left Vo1 0.30 V high |

CD4007 gate capacitances and junction capacitances are estimates (not vendor data).
Vendor models can be dropped in later: keep the subcircuit names and pin order (D G S).

## DC operating point (matches)

| Quantity | Multisim (prelim report) | ngspice | Difference |
|---|---|---|---|
| Quiescent power, VCC1 + VCC2 | 0.43093 W | 0.43152 W | +0.14 % |
| Output-stage quiescent current | 59.6 mA | 59.64 mA | +0.07 % |
| Tail current | 701 uA (simulated) | 722 uA | +3 % |

All CD4007 devices except the tail (M4, triode in both simulators at this bias) are in saturation.

## AC and transient (do not match; known model gap)

| Quantity | Multisim | ngspice (follower bench, same as team) | ngspice (loop break) |
|---|---|---|---|
| Open-loop abs(Av) | 36.99 V/V | 66.7 V/V | 1294 V/V |
| Open-loop f3dB | 209.6 kHz | 136.6 kHz | 8.06 kHz |
| Open-loop input abs(Z), min to 200 kHz | 491.6 kOhm | 625 kOhm | - |
| Closed-loop Rin at 10 kHz | ~223 kOhm (hand calc) | 220.7 kOhm | - |
| Phase margin | not reported | - | 17.5 deg |
| Average supply power at 2 Vpk out | 0.5016 W (2.12 Vpk) | 0.7426 W | - |
| Non-harmonic content at 2 Vpk | not measured | -5.3 dB (oscillation) | - |

The ngspice model of the hand design oscillates on part of the output cycle (see
`baseline/tran_pw.png`), with a 17.5 deg loop-break phase margin. Multisim did not show
this. The likely cause is the capacitance data in the level-1 models: sweeps of the CD4007
and power-FET capacitances, lambda and body effect did not remove it. Because the
oscillation adds supply current, the ngspice baseline power (0.743 W) is higher than
Multisim's (0.502 W). For that reason:

* the optimizer adds a `stability` sanity constraint (non-harmonic output content below -30 dB);
* D5 compares the agent design to the hand design simulated in ngspice (same models), lists the
  Multisim numbers in their own column, and warns when the baseline's ngspice power is inflated
  by oscillation, also quoting quiescent power, which matches Multisim to 0.14 %.

## Open-loop measurement method (decision for the team)

The spec's open-loop gain and bandwidth can be measured two ways, selectable with
`--ol-method`:

* `follower` (default): the team's Multisim bench, unity-gain follower with A = Vodc/(Vin - Vodc).
  With the output stage near cut-off this reading drops to well under 1 V/V, so it also
  guards against class-B designs.
* `loop_break`: the textbook L-C loop break. The hand design then has 1294 V/V but an 8 kHz
  bandwidth, so the "> 200 kHz open-loop bandwidth" constraint becomes essentially
  impossible for this topology.

Both readings are logged every iteration either way.
