# Agent errors and critic/code disagreements

- k=1 critic vs code: swing: critic said fail, code says pass (value 1.985207792913077, limit >= 1.95 Vpk each side, fund >= 1.98)
- k=4 simulation agent: reported worst_harmonic_dbc=-34.814 but the simulator returned -35.28
- k=6 critic vs code: critic chose REVISE on a design that passes every coded check (kept; see reason)
- k=8 critic vs code: swing: critic said fail, code says pass (value 2.0427041190465443, limit >= 1.95 Vpk each side, fund >= 1.98)
- k=8 simulation agent: reported worst_harmonic_dbc=-38.367 but the simulator returned -38.819
- k=10 critic vs code: critic chose REVISE on a design that passes every coded check (kept; see reason)
- k=11 critic vs code: critic chose REVISE on a design that passes every coded check (kept; see reason)
- k=16 critic vs code: stability: critic said fail, code says pass (value -39.84757742070772, limit < -30 dB)
- k=19 critic vs code: av_ol: critic said fail, code says pass (value 164.8633030544274, limit >= 25)
- k=20 critic vs code: stability: critic said fail, code says pass (value -47.984909714383306, limit < -30 dB)
- simulation agent: k=4: reported worst_harmonic_dbc=-34.814 but the simulator returned -35.28
- simulation agent: k=8: reported worst_harmonic_dbc=-38.367 but the simulator returned -38.819
