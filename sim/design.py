"""Design vector x(k): variables, allowed ranges, standard-value helpers.

Everything the agents are allowed to change lives here, so the problem statement (D2),
the planner's tool schema and the validation code all read from one place.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field

# E24 resistor mantissas and E12 capacitor mantissas
E24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
       3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
E12 = [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2]


def _in_series(value: float, series: list[float], tol: float = 0.005) -> bool:
    if value <= 0:
        return False
    exp = math.floor(math.log10(value))
    mant = value / 10 ** exp
    for m in series + [10.0]:
        if abs(mant - m) / m <= tol:
            return True
    return False


def snap(value: float, series: list[float]) -> float:
    """Nearest standard value (log distance)."""
    exp = math.floor(math.log10(value))
    best = None
    for e in (exp - 1, exp, exp + 1):
        for m in series:
            v = m * 10 ** e
            d = abs(math.log(v / value))
            if best is None or d < best[0]:
                best = (d, v)
    return float(f"{best[1]:.4g}")


def is_e24(v: float) -> bool:
    return _in_series(v, E24)


def is_e12(v: float) -> bool:
    return _in_series(v, E12)


# Variable table: name -> (unit, kind, low, high, description)
#   kind: "volt" (0.05 V grid), "res" (E24), "cap" (E12), "int"
VARIABLES = {
    "vcc1":  ("V",   "volt", 3.5, 5.0,   "Supply for stage 1 (diff pair, mirror, tail divider) and the 2N7000 buffer incl. Rup"),
    "vcc2":  ("V",   "volt", 4.5, 5.0,   "Supply for the class-AB output stage (IRF9520 source) and the input bias divider"),
    "r1":    ("ohm", "res",  1e3, 100e3, "Tail bias divider, top resistor (VCC1 to VG4)"),
    "r2":    ("ohm", "res",  1e3, 100e3, "Tail bias divider, bottom resistor (VG4 to ground)"),
    "rup":   ("ohm", "res",  22,  1e3,   "Class-AB bias: VCC1 to VQ2 (IRLZ44N gate)"),
    "rs":    ("ohm", "res",  10,  470,   "Class-AB bias: VQ2 to VQ1 (sets the gate-to-gate offset)"),
    "rk":    ("ohm", "res",  10,  1e3,   "Buffer sink resistor: VQ1 (IRF9520 gate, 2N7000 source) to ground"),
    "cc":    ("F",   "cap",  4.7e-12, 47e-12, "Miller compensation capacitor, Vo1 to Vodc"),
    "rin":   ("ohm", "res",  220e3, 1e6, "Inverting-amplifier input resistor (sets closed-loop Rin)"),
    "rf":    ("ohm", "res",  100e3, 10e6, "Feedback resistor, Vodc to inverting input. beta = Rin/(Rin+Rf)"),
    "n_out": ("",    "int",  1, 2,       "Number of parallel IRF9520 / IRLZ44N output devices"),
}

FIXED = {
    "topology": "CD4007 diff pair + PMOS mirror load, CD4007 tail with resistor-divider gate bias, "
                "2N7000 source-follower buffer, Rup/RS/Rk class-AB gate bias, IRF9520/IRLZ44N "
                "complementary common-source output, Miller CC from Vo1 to Vodc, 1 mF output coupling, 8 ohm load",
    "devices": "CD4007N/P (W/L 170u/10u, 360u/10u), 2N7000, IRF9520, IRLZ44N(S)",
    "cin": 100e-9,
    "c_out": 1e-3,
    "r_load": 8.0,
    "rb": 100e3,  # input bias divider, each leg, from VCC2 -> Vcm = VCC2/2
    "cb": 10e-6,
}


@dataclass
class Design:
    vcc1: float = 5.0
    vcc2: float = 5.0
    r1: float = 5.1e3
    r2: float = 10e3
    rup: float = 150.0
    rs: float = 47.0
    rk: float = 47.0
    cc: float = 10e-12
    rin: float = 220e3
    rf: float = 910e3
    n_out: int = 1
    label: str = field(default="", compare=False)

    @property
    def beta(self) -> float:
        return self.rin / (self.rin + self.rf)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["beta"] = round(self.beta, 4)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Design":
        keys = {k for k in cls.__dataclass_fields__}
        kw = {k: d[k] for k in d if k in keys}
        if "n_out" in kw:
            kw["n_out"] = int(round(float(kw["n_out"])))
        for k in VARIABLES:
            if k in kw and k != "n_out":
                kw[k] = float(kw[k])
        return cls(**kw)


# Hand design from the team's final schematic (final_circuit.png, preliminary report).
# R1 = 5 kOhm in the schematic is not an E24 value (5.1k is). The baseline keeps the
# schematic value so it reproduces the hand design exactly; the critic flags it.
BASELINE = Design(vcc1=5.0, vcc2=5.0, r1=5e3, r2=10e3, rup=150.0, rs=47.0, rk=47.0,
                  cc=10e-12, rin=220e3, rf=950e3, n_out=1, label="hand design (baseline)")


def validate(d: Design) -> list[str]:
    """Range and standard-value problems. Empty list means the candidate is legal to simulate."""
    errs = []
    for name, (unit, kind, lo, hi, _) in VARIABLES.items():
        v = getattr(d, name)
        if v < lo * (1 - 1e-9) or v > hi * (1 + 1e-9):
            errs.append(f"{name}={v:g}{unit} is outside the allowed range [{lo:g}, {hi:g}] {unit}")
        if kind == "res" and not is_e24(v):
            errs.append(f"{name}={v:g} ohm is not an E24 standard value (nearest: {snap(v, E24):g})")
        if kind == "cap" and not is_e12(v):
            errs.append(f"{name}={v:g} F is not an E12 standard value (nearest: {snap(v, E12):g})")
        if kind == "volt" and abs(round(v / 0.05) * 0.05 - v) > 1e-6:
            errs.append(f"{name}={v:g} V is not on the 0.05 V grid")
        if kind == "int" and int(v) != v:
            errs.append(f"{name} must be an integer")
    return errs


def standard_value_flags(d: Design) -> list[str]:
    """Only the standard-value part of validate() (used to flag the baseline)."""
    return [e for e in validate(d) if "standard value" in e]
