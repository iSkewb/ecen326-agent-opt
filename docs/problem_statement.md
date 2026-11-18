# Optimization problem (exactly as given to the agents)

## Objective
Minimize p(k) = average total supply power, VCC1 + VCC2, with the amplifier in the inverting
closed-loop configuration delivering a 10 kHz sine of 2.0 V peak (fundamental) into the 8 ohm
load. The input amplitude is set by the simulator so the output fundamental is 2.0 Vpk.
Quiescent power (no signal) is logged too but is not the objective.

Simulator budget: 20 candidate evaluations (each runs the full analysis set).

## Fixed (not design variables)
- Topology: CD4007 diff pair + PMOS mirror load, CD4007 tail with resistor-divider gate bias, 2N7000 source-follower buffer, Rup/RS/Rk class-AB gate bias, IRF9520/IRLZ44N complementary common-source output, Miller CC from Vo1 to Vodc, 1 mF output coupling, 8 ohm load
- Devices: CD4007N/P (W/L 170u/10u, 360u/10u), 2N7000, IRF9520, IRLZ44N(S)
- Input coupling Cin = 100 nF, output coupling 1 mF, load 8 ohm, input bias divider 2 x 100 kOhm from VCC2 (+ input at VCC2/2) with 10 uF bypass

## Design variables
| Variable | Unit | Allowed values | Meaning |
|---|---|---|---|
| vcc1 | V | 3.5 to 5 in 0.05 V steps | Supply for stage 1 (diff pair, mirror, tail divider) and the 2N7000 buffer incl. Rup |
| vcc2 | V | 4.5 to 5 in 0.05 V steps | Supply for the class-AB output stage (IRF9520 source) and the input bias divider |
| r1 | ohm | E24 values, 1000 to 100000 | Tail bias divider, top resistor (VCC1 to VG4) |
| r2 | ohm | E24 values, 1000 to 100000 | Tail bias divider, bottom resistor (VG4 to ground) |
| rup | ohm | E24 values, 22 to 1000 | Class-AB bias: VCC1 to VQ2 (IRLZ44N gate) |
| rs | ohm | E24 values, 10 to 470 | Class-AB bias: VQ2 to VQ1 (sets the gate-to-gate offset) |
| rk | ohm | E24 values, 10 to 1000 | Buffer sink resistor: VQ1 (IRF9520 gate, 2N7000 source) to ground |
| cc | F | E12 values, 4.7 pF to 47 pF | Miller compensation capacitor, Vo1 to Vodc |
| rin | ohm | E24 values, 220000 to 1e+06 | Inverting-amplifier input resistor (sets closed-loop Rin) |
| rf | ohm | E24 values, 100000 to 1e+07 | Feedback resistor, Vodc to inverting input. beta = Rin/(Rin+Rf) |
| n_out |  | 1 or 2 | Number of parallel IRF9520 / IRLZ44N output devices |

Feedback factor beta = Rin/(Rin+Rf); closed-loop gain magnitude is about Rf/Rin.

## Constraints
| # | Constraint | Limit |
|---|---|---|
| 1 | Open-loop |Av| (`av_ol`) | >= 25 V/V |
| 2 | Input resistance (`rin`) | >= 200 kOhm |
| 3 | Open-loop -3 dB bandwidth (`f3db_ol`) | > 200 kHz |
| 4 | Harmonic distortion, 0.5 Vpk (1 Vpp) in, 10 kHz, inverting feedback (`hd`) | every harmonic < -30 dBc |
| 5 | Output swing into 8 ohm (`swing`) | >= 2 V peak, both polarities, harmonics < -30 dBc |
| 6 | Supply voltage(s) (`vcc`) | <= 5 V each |
| 7 | Standard component values (`std_values`) | E24 resistors, E12 capacitors |
| 8 | Device and resistor power/current ratings (`ratings`) | within datasheet free-air ratings |
| 9 | No oscillation (added sanity constraint) (`stability`) | non-harmonic output content < -30 dB at 2 Vpk |
| 10 | Simulator converged on every analysis (`converged`) | true |

### How each constraint is measured
- Open-loop |Av| and -3 dB bandwidth: method `follower`. `follower` = the team's Multisim bench (unity-gain follower, A = Vodc/(Vin - Vodc), gain read at 5.72 kHz); `loop_break` = DC feedback through a 1 GH inductor with the inverting input AC-grounded by 1 F, gain read at 1 kHz. Both are logged every iteration.
- Rin: the smaller of the open-loop input |Z| (minimum over 100 Hz to 200 kHz, follower bench) and the closed-loop inverting-amplifier input resistance at 10 kHz.
- Harmonic distortion: inverting closed loop, 0.5 Vpk (1 Vpp) 10 kHz input, FFT of the last 10 cycles at the load; each harmonic 2..10 relative to the fundamental (power ratio, dBc).
- Swing: closed loop driven for a 2.0 Vpk fundamental; both peaks >= 1.95 V and every harmonic < -30 dBc.
- Ratings (free air, 25 C): IRF9520 2.0 W, IRLZ44N 2.4 W, 2N7000 0.4 W and 0.2 A, CD4007 0.1 W per device and 10 mA, resistors 0.25 W.
- Stability (added by us as a sanity constraint): output content that is not DC or harmonics 1..10 must be below -30 dB relative to the fundamental at 2 Vpk. This catches oscillation that the harmonic bins miss.

## Baseline (hand design)
| Variable | Value |
|---|---|
| vcc1 | 5 |
| vcc2 | 5 |
| r1 | 5000 |
| r2 | 10000 |
| rup | 150 |
| rs | 47 |
| rk | 47 |
| cc | 1e-11 |
| rin | 220000 |
| rf | 950000 |
| n_out | 1 |
| beta | 0.188 |
