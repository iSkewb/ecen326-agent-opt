"""SPICE netlist generation for a candidate design x(k).

The topology is fixed (the team's final schematic); only the values in Design change.
Each test bench is a separate ngspice deck so the analyses stay independent:

  op_cl    closed-loop inverting amplifier, DC operating point
  ac_ol    open-loop AC: + input driven, - input tied to Vodc through a 1 GH inductor
           (DC feedback for bias) and AC-grounded by 1 F, so loop is broken for AC only
  ac_fol   unity-gain follower, the Multisim bench from the preliminary report
           (gain extracted as V(vodc)/(V(vin)-V(vodc)), input |Z| from 1/|I(VSIG)|)
  ac_cl    closed-loop inverting amplifier, AC (closed-loop gain and input resistance)
  tran_hd  closed-loop inverting, 0.5 Vpk (1 Vpp) 10 kHz input  -> harmonic distortion spec
  tran_pw  closed-loop inverting, input set for 2 Vpk at the 8 ohm load -> swing + average power
  tran_ol  open-loop bench, 5 mV 10 kHz input (report figure; full runs only)

Node names: vcc1 vcc2 inp inn vg1 vo1 vt vt4 vg4 vq1 vq2 vodc vo
"""
from __future__ import annotations

from pathlib import Path

from .design import Design, FIXED

MODELS = Path(__file__).resolve().parent.parent / "models"

F_SIG = 10e3


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def amplifier_core(d: Design) -> str:
    """The amplifier itself, independent of the test bench."""
    lines = [
        f"* ---- amplifier core ----",
        f"VCC1 vcc1 0 DC {_fmt(d.vcc1)}",
        f"VCC2 vcc2 0 DC {_fmt(d.vcc2)}",
        "* stage 1: CD4007 differential pair, PMOS mirror load (bodies: P to VCC1, N to ground)",
        "M1 vg1 vg1 vcc1 vcc1 CD4007P W=360u L=10u",
        "M2 vo1 vg1 vcc1 vcc1 CD4007P W=360u L=10u",
        "M3 vg1 inn vt 0 CD4007N W=170u L=10u",
        "M6 vo1 inp vt 0 CD4007N W=170u L=10u",
        "VTAIL vt vt4 DC 0",
        "M4 vt4 vg4 0 0 CD4007N W=170u L=10u",
        f"R1 vcc1 vg4 {_fmt(d.r1)}",
        f"R2 vg4 0 {_fmt(d.r2)}",
        f"CC vo1 vodc {_fmt(d.cc)}",
        "* buffer: 2N7000 source follower, class-AB gate bias network",
        "VSQ3 vcc1 dq3 DC 0",
        "XQ3 dq3 vo1 vq1 2N7000",
        f"RK vq1 0 {_fmt(d.rk)}",
        f"RS vq2 vq1 {_fmt(d.rs)}",
        f"RUP vcc1 vq2 {_fmt(d.rup)}",
        f"* output stage: {d.n_out} x IRF9520 (high side) + {d.n_out} x IRLZ44N (low side)",
    ]
    for k in range(1, d.n_out + 1):
        lines += [
            f"XQ1_{k} dp{k} vq1 vcc2 IRF9520",
            f"VSP{k} dp{k} vodc DC 0",
            f"XQ2_{k} vodc vq2 sn{k} IRLZ44N",
            f"VSN{k} sn{k} 0 DC 0",
        ]
    lines += [
        f"C1 vodc vo {_fmt(FIXED['c_out'])}",
        f"RL vo 0 {_fmt(FIXED['r_load'])}",
    ]
    return "\n".join(lines)


def _header(title: str) -> str:
    return (f"* {title}\n"
            f".include {MODELS / 'cd4007.lib'}\n"
            f".include {MODELS / 'power_fets.lib'}\n"
            ".options reltol=1e-4 abstol=1e-10 vntol=1e-7 gmin=1e-12 itl1=500 itl2=500 itl4=200\n")


def _bias_divider(d: Design) -> str:
    rb = _fmt(FIXED["rb"])
    return (f"RB1 vcc2 inp {rb}\nRB2 inp 0 {rb}\nCB inp 0 {_fmt(FIXED['cb'])}\n")


def _cl_feedback(d: Design, src: str) -> str:
    return (f"CIN {src} nin {_fmt(FIXED['cin'])}\n"
            f"RIN nin inn {_fmt(d.rin)}\n"
            f"RF vodc inn {_fmt(d.rf)}\n")


def _save_vectors(d: Design) -> str:
    cur = " ".join([f"i(VSP{k}) i(VSN{k})" for k in range(1, d.n_out + 1)])
    return f"i(VCC1) i(VCC2) i(VTAIL) i(VSQ3) {cur}"


def _op_block(d: Design, outdir: Path, name: str) -> str:
    return (f"op\n"
            f"print all > {outdir / (name + '_op.txt')}\n")


def deck_op_cl(d: Design, outdir: Path) -> str:
    return (_header("op_cl: closed-loop inverting amplifier, DC operating point")
            + amplifier_core(d) + "\n" + _bias_divider(d)
            + "VSIG src 0 DC 0\n" + _cl_feedback(d, "src")
            + ".control\nset filetype=ascii\nset wr_singlescale\nset wr_vecnames\n"
            + "op\n"
            + f"print all > {outdir / 'op_cl.txt'}\n"
            + f"print {_save_vectors(d)} > {outdir / 'op_cl_currents.txt'}\n"
            + ".endc\n.end\n")


def deck_ac_ol(d: Design, outdir: Path) -> str:
    vcm = d.vcc2 / 2
    return (_header("ac_ol: open loop (L-C loop break), + input driven")
            + amplifier_core(d) + "\n"
            + f"VSIG inp 0 DC {_fmt(vcm)} AC 1\n"
            + "LFB vodc inn 1e9\nCFB inn 0 1\n"
            + ".control\nset filetype=ascii\nset wr_singlescale\nset wr_vecnames\n"
            + "ac dec 50 10 100Meg\n"
            + f"wrdata {outdir / 'ac_ol.txt'} vr(vodc) vi(vodc) vr(vo) vi(vo)\n"
            + ".endc\n.end\n")


def deck_ac_fol(d: Design, outdir: Path) -> str:
    vcm = d.vcc2 / 2
    return (_header("ac_fol: unity-gain follower (Multisim open-loop bench)")
            + amplifier_core(d) + "\n"
            + f"VSIG inp 0 DC {_fmt(vcm)} AC 1\n"
            + "RFOL vodc inn 1m\n"
            + ".control\nset filetype=ascii\nset wr_singlescale\nset wr_vecnames\n"
            + "ac dec 50 10 100Meg\n"
            + f"wrdata {outdir / 'ac_fol.txt'} vr(vodc) vi(vodc) vr(inp) vi(inp) i(VSIG)\n"
            + ".endc\n.end\n")


def deck_ac_cl(d: Design, outdir: Path) -> str:
    return (_header("ac_cl: closed-loop inverting amplifier, AC")
            + amplifier_core(d) + "\n" + _bias_divider(d)
            + "VSIG src 0 DC 0 AC 1\n" + _cl_feedback(d, "src")
            + ".control\nset filetype=ascii\nset wr_singlescale\nset wr_vecnames\n"
            + "ac dec 50 10 100Meg\n"
            + f"wrdata {outdir / 'ac_cl.txt'} vr(vo) vi(vo) vr(vodc) vi(vodc) i(VSIG)\n"
            + ".endc\n.end\n")


def deck_tran_cl(d: Design, outdir: Path, name: str, vin_pk: float,
                 cycles: int = 20, fourier: bool = False) -> str:
    tstop = cycles / F_SIG
    tstep = 1 / F_SIG / 400
    return (_header(f"{name}: closed-loop inverting amplifier, {vin_pk:g} Vpk 10 kHz")
            + amplifier_core(d) + "\n" + _bias_divider(d)
            + f"VSIG src 0 DC 0 SIN(0 {_fmt(vin_pk)} {F_SIG:g})\n" + _cl_feedback(d, "src")
            + ".control\nset filetype=ascii\nset wr_singlescale\nset wr_vecnames\n"
            + f"tran {tstep:.4g} {tstop:.4g} 0 {tstep:.4g}\n"
            + "linearize\n"
            + f"wrdata {outdir / (name + '.txt')} v(vo) v(vodc) v(src) v(vcc1) v(vcc2) v(vq1) "
              f"v(vo1) {_save_vectors(d)}\n"
            + (f"fourier {F_SIG:g} v(vo) > {outdir / (name + '_fourier.txt')}\n" if fourier else "")
            + ".endc\n.end\n")


def deck_tran_ol(d: Design, outdir: Path, vin_pk: float = 5e-3, cycles: int = 20) -> str:
    vcm = d.vcc2 / 2
    tstop = cycles / F_SIG
    tstep = 1 / F_SIG / 400
    return (_header("tran_ol: open loop (L-C loop break), 5 mVpk 10 kHz")
            + amplifier_core(d) + "\n"
            + f"VSIG inp 0 DC {_fmt(vcm)} SIN({_fmt(vcm)} {_fmt(vin_pk)} {F_SIG:g})\n"
            + "LFB vodc inn 1e9\nCFB inn 0 1\n"
            + ".control\nset filetype=ascii\nset wr_singlescale\nset wr_vecnames\n"
            + f"tran {tstep:.4g} {tstop:.4g} 0 {tstep:.4g}\nlinearize\n"
            + f"wrdata {outdir / 'tran_ol.txt'} v(vo) v(vodc) v(inp)\n"
            + f"fourier {F_SIG:g} v(vo) > {outdir / 'tran_ol_fourier.txt'}\n"
            + ".endc\n.end\n")
