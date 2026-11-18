# D5 Hand design vs agent-optimized design

Open-loop method: `follower`. Both ngspice columns use the same models and test benches.

| Item | Spec | Hand design (Multisim, prelim report) | Hand design (ngspice) | Agent best feasible (ngspice) |
|---|---|---|---|---|
| Open-loop abs(Av) | >= 25 V/V | 36.99 | 66.7 (pass) | 25.7 (pass) |
| Open-loop f3dB | > 200 kHz | 209.6 kHz | 136.6 kHz (FAIL) | 200.5 kHz (pass) |
| Rin | >= 200 kOhm | 491.6 kOhm (open loop) | 220.7 kOhm (pass) | 228.6 kOhm (pass) |
| Worst harmonic, 1 Vpp in | < -30 dBc | -77.6 dB THD at beta 0.5 | -35.4 dBc (pass) | -43.4 dBc (pass) |
| THD, 1 Vpp in | report | 0.0131 % (beta 0.5) | 2.39 % | 0.704 % |
| Output swing | >= 2 Vpk | 2.12 Vpk | 2.00 Vpk (pass) | 2.00 Vpk (pass) |
| Non-harmonic content at 2 Vpk | < -30 dB | not measured | -5.3 dB (FAIL) | -58.3 dB (pass) |
| VCC1 (stage 1 + buffer) | <= 5 V | 5.0 V | 5.00 V | 4.90 V |
| VCC2 (output stage) | <= 5 V | 5.0 V | 5.00 V | 5.00 V |
| Quiescent power | log | 0.4309 W | 0.4315 W | 0.2202 W |
| Average power at 2 Vpk (objective) | minimize | 0.5016 W (at 2.12 Vpk) | 0.7422 W | 0.5658 W |

Feasible in ngspice: hand design **no (fails f3db_ol, std_values, stability)**, agent design **yes** (k = 18).
Average power at 2 Vpk: 0.7422 W -> 0.5658 W, a 23.8 % reduction against the hand design simulated in ngspice.
Caution: the hand design oscillates in the ngspice model (it did not in Multisim), which raises its ngspice power at 2 Vpk. Part of this reduction is removing that oscillation, so also quote the quiescent power (0.4315 W -> 0.2202 W).
Against the Multisim figure for the hand design (0.5016 W) the change is +12.8 %; that comparison mixes two simulators and models, so quote the ngspice-to-ngspice number.

## Agent-optimized design values

| Variable | Hand design | Agent design |
|---|---|---|
| vcc1 | 5 | 4.9 |
| vcc2 | 5 | 5 |
| r1 | 5k | 3k |
| r2 | 10k | 4.7k |
| rup | 150 | 180 |
| rs | 47 | 51 |
| rk | 47 | 39 |
| cc | 10p | 12p |
| rin | 220k | 220k |
| rf | 950k | 910k |
| n_out | 1 | 1 |
| beta | 0.1880 | 0.1947 |
