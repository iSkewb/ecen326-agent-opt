"""Simulator interface: x(k) -> ngspice -> y(k).

`simulate(design, outdir)` writes every test-bench netlist, runs ngspice in batch mode
(in parallel), parses the raw data files and returns a flat metrics dict. This is the
only place the simulator is called; the simulation agent reaches it through the
`run_simulation` tool, never by hand.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import netlist as N
from .design import Design, FIXED

class NgspiceNotFound(RuntimeError):
    pass


def find_ngspice() -> str:
    """Locate the ngspice batch executable.

    Order: the NGSPICE environment variable, `ngspice_con` on PATH (the Windows console
    build), `ngspice` on PATH, then the default Windows install folders.
    """
    env = os.environ.get("NGSPICE")
    if env:
        if Path(env).is_file() or shutil.which(env):
            return shutil.which(env) or env
        raise NgspiceNotFound(f"NGSPICE is set to {env!r}, but no such file exists.")
    for name in ("ngspice_con", "ngspice"):
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in (r"C:\Spice64\bin\ngspice_con.exe", r"C:\Spice\bin\ngspice_con.exe",
                 r"C:\Program Files\Spice64\bin\ngspice_con.exe"):
        if Path(cand).is_file():
            return cand
    raise NgspiceNotFound(
        "ngspice was not found. Install it (Linux: sudo apt install ngspice; macOS: brew install ngspice; "
        "Windows: extract the ngspice-4x_64.7z release to C:\\Spice64 so that C:\\Spice64\\bin\\ngspice_con.exe exists), "
        "or point the NGSPICE environment variable at the executable "
        "(PowerShell: $env:NGSPICE = \"C:\\path\\to\\ngspice_con.exe\").")


_NGSPICE: str | None = None


def ngspice() -> str:
    global _NGSPICE
    if _NGSPICE is None:
        _NGSPICE = find_ngspice()
    return _NGSPICE
TARGET_SWING = 2.0          # V peak at the 8 ohm load
HD_INPUT_PK = 0.5           # V peak (1 Vpp) for the distortion spec
N_CYCLES_FFT = 10           # analyse the last 10 cycles of each transient


class SimError(RuntimeError):
    pass


# ------------------------------------------------------------------ running

def _run_deck(text: str, path: Path, timeout: int = 240) -> str:
    path.write_text(text, encoding="utf-8")
    log = path.with_suffix(".log")
    t0 = time.time()
    try:
        # run in the deck's own folder: the decks write their output files by bare name, which
        # avoids drive letters, backslashes and spaces in paths inside the netlist
        p = subprocess.run([ngspice(), "-b", path.name], capture_output=True, text=True, timeout=timeout,
                           cwd=path.parent, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        raise SimError(f"{path.name}: ngspice timed out after {timeout}s") from e
    log.write_text(p.stdout + "\n--- stderr ---\n" + p.stderr, encoding="utf-8")
    out = p.stdout + p.stderr
    bad = [ln for ln in out.splitlines()
           if any(s in ln.lower() for s in ("singular matrix", "timestep too small", "no convergence",
                                             "simulation(s) aborted", "error"))
           and "warning" not in ln.lower()]
    if p.returncode != 0 or bad:
        raise SimError(f"{path.name}: ngspice failed ({p.returncode}): " + " | ".join(bad[:4]))
    return f"{path.name}: {time.time() - t0:.1f}s"


def _load(path: Path) -> np.ndarray:
    if not path.exists():
        raise SimError(f"missing output {path.name} (analysis did not run)")
    return np.loadtxt(path, skiprows=1, ndmin=2)


def _parse_op(path: Path) -> dict:
    vals = {}
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            try:
                vals[k.strip()] = float(v.split()[0])
            except ValueError:
                pass
    return vals


# ------------------------------------------------------------------ metrics

def _interp_cross(f, mag, level):
    """First frequency where mag falls below level (log interpolation)."""
    below = np.where(mag < level)[0]
    if len(below) == 0:
        return float("inf")
    i = below[0]
    if i == 0:
        return float(f[0])
    x0, x1 = math.log10(f[i - 1]), math.log10(f[i])
    y0, y1 = mag[i - 1], mag[i]
    return 10 ** (x0 + (level - y0) * (x1 - x0) / (y1 - y0))


def _at(f, y, f0):
    return float(np.interp(math.log10(f0), np.log10(f), y))


def _harmonics(t, v, f0=N.F_SIG, n_cycles=N_CYCLES_FFT, n_harm=10):
    T = n_cycles / f0
    m = t >= (t[-1] - T - 1e-12)
    tt, vv = t[m], v[m]
    # uniform resample over exactly n_cycles periods
    n = 4096 * n_cycles // 4
    ts = np.linspace(tt[-1] - T, tt[-1], n, endpoint=False)
    vs = np.interp(ts, tt, vv)
    spec = np.fft.rfft(vs) / n * 2
    amps = [abs(spec[k * n_cycles]) for k in range(0, n_harm + 1)]
    fund = amps[1]
    hd = {f"hd{k}_db": (20 * math.log10(amps[k] / fund) if amps[k] > 0 and fund > 0 else -200.0)
          for k in range(2, n_harm + 1)}
    thd = math.sqrt(sum(a * a for a in amps[2:])) / fund if fund > 0 else float("inf")
    # everything that is NOT the DC term or harmonics 1..n_harm (oscillation, noise, glitches)
    k = np.arange(len(spec))
    keep = np.zeros(len(spec), bool)
    keep[[h * n_cycles for h in range(0, n_harm + 1)]] = True
    resid = np.sqrt(np.sum(np.abs(spec[~keep & (k > 0)]) ** 2) / 2)
    nonharm_db = 20 * math.log10(resid / (fund / math.sqrt(2))) if resid > 0 and fund > 0 else -200.0
    return {
        "fund_pk": fund,
        "dc": spec[0].real / 2,
        "thd_pct": 100 * thd,
        "thd_db": 20 * math.log10(thd) if thd > 0 else -200.0,
        "hd_max_db": max(hd.values()),
        "nonharm_db": nonharm_db,
        "vpos_pk": float(vs.max() - spec[0].real / 2),
        "vneg_pk": float(spec[0].real / 2 - vs.min()),
        **hd,
    }


def _tran_metrics(path: Path, d: Design) -> dict:
    a = _load(path)
    t = a[:, 0]
    cols = ["vo", "vodc", "src", "vcc1", "vcc2", "vq1", "vo1", "i_vcc1", "i_vcc2", "i_tail", "i_q3"]
    for k in range(1, d.n_out + 1):
        cols += [f"i_p{k}", f"i_n{k}"]
    data = {c: a[:, i + 1] for i, c in enumerate(cols)}
    T = N_CYCLES_FFT / N.F_SIG
    m = t >= (t[-1] - T - 1e-12)
    avg = lambda x: float(np.trapezoid(x[m], t[m]) / (t[m][-1] - t[m][0]))
    p1 = avg(-data["vcc1"] * data["i_vcc1"])
    p2 = avg(-data["vcc2"] * data["i_vcc2"])
    res = _harmonics(t, data["vo"])
    res.update({
        "p_avg": p1 + p2, "p_avg_vcc1": p1, "p_avg_vcc2": p2,
        "p_load": avg(data["vo"] ** 2 / FIXED["r_load"]),
        "vin_pk": float(np.max(np.abs(data["src"][m]))),
    })
    # device dissipation (average) and peaks
    pq1 = [avg((data["vcc2"] - data["vodc"]) * data[f"i_p{k}"]) for k in range(1, d.n_out + 1)]
    pq2 = [avg(data["vodc"] * data[f"i_n{k}"]) for k in range(1, d.n_out + 1)]
    res["p_irf9520_each"] = max(pq1)
    res["p_irlz44n_each"] = max(pq2)
    res["p_2n7000"] = avg((data["vcc1"] - data["vq1"]) * data["i_q3"])
    res["ipk_irf9520_each"] = max(float(np.max(data[f"i_p{k}"][m])) for k in range(1, d.n_out + 1))
    res["ipk_irlz44n_each"] = max(float(np.max(data[f"i_n{k}"][m])) for k in range(1, d.n_out + 1))
    res["ipk_2n7000"] = float(np.max(data["i_q3"][m]))
    res["vo1_min"] = float(np.min(data["vo1"][m]))
    res["vo1_max"] = float(np.max(data["vo1"][m]))
    return res


def _op_metrics(op: dict, d: Design) -> dict:
    g = lambda k: op.get(k, float("nan"))
    i1, i2 = -g("vcc1#branch"), -g("vcc2#branch")
    vt = g("vt")
    res = {
        "p_q": d.vcc1 * i1 + d.vcc2 * i2,
        "p_q_vcc1": d.vcc1 * i1, "p_q_vcc2": d.vcc2 * i2,
        "i_vcc1": i1, "i_vcc2": i2,
        "i_tail": g("vtail#branch"), "i_q3": g("vsq3#branch"),
        "i_out_q": sum(g(f"vsn{k}#branch") for k in range(1, d.n_out + 1)),
        "v_vg1": g("vg1"), "v_vo1": g("vo1"), "v_vt": vt, "v_vg4": g("vg4"),
        "v_vq1": g("vq1"), "v_vq2": g("vq2"), "v_vodc": g("vodc"), "v_inp": g("inp"),
        "v_inn": g("inn"),
    }
    # CD4007 regions (level-1, body effect off): saturation if Vds >= Vgs - Vt
    vtn, vtp = 1.4, 1.65
    def region_n(vd, vg, vs):
        vov = vg - vs - vtn
        if vov <= 0:
            return "off"
        return "sat" if vd - vs >= vov else "triode"
    def region_p(vd, vg, vs):
        vov = vs - vg - vtp
        if vov <= 0:
            return "off"
        return "sat" if vs - vd >= vov else "triode"
    res["region_M1"] = region_p(g("vg1"), g("vg1"), d.vcc1)
    res["region_M2"] = region_p(g("vo1"), g("vg1"), d.vcc1)
    res["region_M3"] = region_n(g("vg1"), g("inn"), vt)
    res["region_M6"] = region_n(g("vo1"), g("inp"), vt)
    res["region_M4"] = region_n(vt, g("vg4"), 0.0)
    # quiescent device dissipation
    res["pq_irf9520_each"] = (d.vcc2 - g("vodc")) * g("vsp1#branch")
    res["pq_irlz44n_each"] = g("vodc") * g("vsn1#branch")
    res["pq_2n7000"] = (d.vcc1 - g("vq1")) * g("vsq3#branch")
    res["pq_cd4007_max"] = max(abs((d.vcc1 - g("vo1")) * g("vtail#branch") / 2),
                               abs((g("vo1") - vt) * g("vtail#branch") / 2),
                               abs(vt * g("vtail#branch")))
    # resistor dissipation (quiescent)
    res["p_rup"] = (d.vcc1 - g("vq2")) ** 2 / d.rup
    res["p_rs"] = (g("vq2") - g("vq1")) ** 2 / d.rs
    res["p_rk"] = g("vq1") ** 2 / d.rk
    res["p_r1"] = (d.vcc1 - g("vg4")) ** 2 / d.r1
    res["p_r2"] = g("vg4") ** 2 / d.r2
    return res


def _ac_metrics(outdir: Path, d: Design) -> dict:
    res = {}
    a = _load(outdir / "ac_ol.txt")
    f = a[:, 0]
    A = a[:, 1] + 1j * a[:, 2]
    mag = np.abs(A)
    a1k = _at(f, mag, 1e3)
    res["av_ol"] = a1k
    res["av_ol_db"] = 20 * math.log10(a1k) if a1k > 0 else -200
    res["f3db_ol"] = _interp_cross(f, mag, a1k / math.sqrt(2))
    ugf = _interp_cross(f, mag, 1.0)
    res["ugf_ol"] = ugf
    ph = np.unwrap(np.angle(A)) * 180 / math.pi
    ph0 = _at(f, ph, 1e3)
    if math.isfinite(ugf):
        # phase margin of the unity-feedback loop, relative to the low-frequency phase
        res["phase_margin_deg"] = 180 + (_at(f, ph, ugf) - ph0)
    else:
        res["phase_margin_deg"] = float("nan")
    res["ol_phase_1k_deg"] = ph0
    res["av_ol_10k"] = _at(f, mag, 1e4)

    # Multisim-style follower bench
    b = _load(outdir / "ac_fol.txt")
    f = b[:, 0]
    vod = b[:, 1] + 1j * b[:, 2]
    vin = b[:, 3] + 1j * b[:, 4]
    iin = b[:, 5] + 1j * b[:, 6]
    afol = np.abs(vod / (vin - vod))
    res["av_follower_method"] = _at(f, afol, 5.72e3)
    res["f3db_follower_method"] = _interp_cross(f, afol, res["av_follower_method"] / math.sqrt(2))
    zin = np.abs(vin / iin)
    band = (f >= 100) & (f <= 200e3)
    res["zin_ol_min"] = float(np.min(zin[band]))
    res["zin_ol_200k"] = _at(f, zin, 200e3)

    # closed loop
    c = _load(outdir / "ac_cl.txt")
    f = c[:, 0]
    vo = c[:, 1] + 1j * c[:, 2]
    isrc = c[:, 5] + 1j * c[:, 6]
    gcl = np.abs(vo)
    g10 = _at(f, gcl, 1e4)
    res["av_cl_10k"] = g10
    res["rin_cl_10k"] = _at(f, np.abs(1 / isrc), 1e4)
    hi = f >= 1e3
    res["cl_peaking_db"] = 20 * math.log10(float(np.max(gcl[hi])) / g10) if g10 > 0 else float("nan")
    res["f3db_cl"] = _interp_cross(f[hi], gcl[hi], g10 / math.sqrt(2))
    return res


# ------------------------------------------------------------------ public

def simulate(d: Design, outdir: Path, full: bool = False) -> dict:
    """Run the whole analysis set for one candidate. Returns y(k) as a flat dict.

    full=True also runs the open-loop 5 mV transient and ngspice's own Fourier analysis
    (the extra plots the report needs for the hand design and the final design).
    """
    ngspice()  # fail fast with a clear message if ngspice is not installed
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    y: dict = {"converged": True, "errors": []}

    vin_pw = TARGET_SWING * d.rin / d.rf  # ideal-gain first guess for a 2 Vpk output
    decks = {
        "op_cl": N.deck_op_cl(d, outdir),
        "ac_ol": N.deck_ac_ol(d, outdir),
        "ac_fol": N.deck_ac_fol(d, outdir),
        "ac_cl": N.deck_ac_cl(d, outdir),
        "tran_hd": N.deck_tran_cl(d, outdir, "tran_hd", HD_INPUT_PK, fourier=full),
        "tran_pw": N.deck_tran_cl(d, outdir, "tran_pw", vin_pw, fourier=full),
    }
    if full:
        decks["tran_ol"] = N.deck_tran_ol(d, outdir)

    with ThreadPoolExecutor(max_workers=len(decks)) as ex:
        futs = {k: ex.submit(_run_deck, txt, outdir / f"{k}.cir") for k, txt in decks.items()}
        for k, fu in futs.items():
            try:
                fu.result()
            except SimError as e:
                y["converged"] = False
                y["errors"].append(str(e))

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # parse failure means the analysis is unusable
            y["converged"] = False
            y["errors"].append(f"{fn.__name__}: {e}")
            return {}

    y.update(safe(lambda: _op_metrics(_parse_op(outdir / "op_cl.txt"), d)))
    y.update(safe(_ac_metrics, outdir, d))
    hd = safe(_tran_metrics, outdir / "tran_hd.txt", d)
    y.update({f"hdtest_{k}": v for k, v in hd.items()})

    pw = safe(_tran_metrics, outdir / "tran_pw.txt", d)
    # second pass: rescale the input so the fundamental at the load is exactly 2 Vpk
    if pw and pw.get("fund_pk", 0) > 0 and abs(pw["fund_pk"] - TARGET_SWING) / TARGET_SWING > 0.01:
        vin2 = min(vin_pw * TARGET_SWING / pw["fund_pk"], 2.0)
        try:
            _run_deck(N.deck_tran_cl(d, outdir, "tran_pw", vin2, fourier=full), outdir / "tran_pw.cir")
            pw = safe(_tran_metrics, outdir / "tran_pw.txt", d)
        except SimError as e:
            y["converged"] = False
            y["errors"].append(str(e))
    y.update({f"swing_{k}": v for k, v in pw.items()})

    y["beta"] = d.beta
    y["sim_seconds"] = round(time.time() - t0, 2)
    y = {k: (float(v) if isinstance(v, (np.floating,)) else v) for k, v in y.items()}
    (outdir / "metrics.json").write_text(json.dumps(y, indent=2, default=str), encoding="utf-8")
    return y
