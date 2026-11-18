"""Simulate the hand design with the full analysis set into baseline/ (python -m sim.baseline)."""
import json
from pathlib import Path

from report.reporting import _plots_full
from sim.design import BASELINE
from sim.runner import simulate
from sim.specs import check

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "baseline"
    y = simulate(BASELINE, out / "sim", full=True)
    _plots_full(out / "sim", out, BASELINE)
    for m in ("follower", "loop_break"):
        (out / f"check_{m}.json").write_text(encoding="utf-8", data=json.dumps(check(BASELINE, y, m), indent=2, default=str))
    print(json.dumps({k: y[k] for k in ("p_q", "i_out_q", "i_tail", "i_q3", "v_vo1", "v_vq1", "v_vq2", "v_vodc",
                                       "av_follower_method", "f3db_follower_method", "zin_ol_min", "rin_cl_10k",
                                       "av_ol", "f3db_ol", "ugf_ol", "phase_margin_deg", "cl_peaking_db",
                                       "hdtest_hd_max_db", "hdtest_thd_pct", "swing_fund_pk", "swing_p_avg",
                                       "swing_nonharm_db") if k in y}, indent=1))
