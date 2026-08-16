from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

INFLOWS = Path("data/hydro/model/inflows_daily.csv")
STORAGE = Path("data/hydro/model/storage_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT = Path("data/model/waitaki_upstream_source_balance.json")

YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 0.0864
PKI_ACTUAL = "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv"
PKI_NATURAL = "SI_PKI_Natural_LakePukaki_Inflow_98770(1).csv"
OHU_NATURAL = "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv"
PKI_SPILL = "SI_PKI_Spill_LakePukaki.csv"
OHA_SPILL = "SI_OHA_Spill_LakeOhau.csv"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as h:
        yield from csv.DictReader(h)


def source(path, filename):
    out = {}
    for r in read_csv(path):
        if r.get("source_file") == filename and r.get("date", "").startswith(f"{YEAR}-"):
            out[r["date"]] = float(r.get("flow_m3s") or 0.0)
    return out


def storage(site):
    out = {}
    for r in read_csv(STORAGE):
        if r.get("site_code") == site and r.get("date", "").startswith(f"{YEAR}-"):
            raw = r.get("total_storage_mm3") or r.get("active_storage_mm3") or r.get("storage_mm3")
            if raw not in (None, ""):
                out[r["date"]] = float(raw)
    return out


def factors():
    out = {}
    for r in read_csv(ASSETS):
        if r.get("site_code") in {"OHA", "TKB"} and r.get("plant_factor_cumecs_per_mw"):
            out[r["site_code"]] = float(r["plant_factor_cumecs_per_mw"])
    return out


def turbine_flows():
    pf = factors()
    daily = {s: defaultdict(float) for s in pf}
    for r in read_csv(GENERATION):
        d = r.get("date", "")
        s = r.get("site_code", "")
        if d.startswith(f"{YEAR}-") and s in daily:
            daily[s][d] += float(r["generation_mwh"])
    return {s: {d: mwh / 24.0 * pf[s] for d, mwh in vals.items()} for s, vals in daily.items()}


def stat(vals):
    a = [v for v in vals if math.isfinite(v)]
    return {
        "count": len(a),
        "mean": round(sum(a)/len(a), 3),
        "mae": round(sum(abs(v) for v in a)/len(a), 3),
        "rmse": round(math.sqrt(sum(v*v for v in a)/len(a)), 3),
        "max_abs": round(max(abs(v) for v in a), 3),
    }


def main():
    pki_actual = source(INFLOWS, PKI_ACTUAL)
    pki_nat = source(INFLOWS, PKI_NATURAL)
    ohu_nat = source(INFLOWS, OHU_NATURAL)
    pki_spill = source(SPILL, PKI_SPILL)
    oha_spill = source(SPILL, OHA_SPILL)
    pki_s = storage("PKI")
    oha_s = storage("OHA")
    q = turbine_flows()

    dates = sorted(set(pki_actual)&set(pki_nat)&set(ohu_nat)&set(pki_spill)&set(pki_s)&set(oha_s)&set(q["OHA"])&set(q["TKB"]))
    pki_delta = {dates[i]: pki_s[dates[i]] - pki_s[dates[i-1]] for i in range(1, len(dates))}
    oha_delta = {dates[i]: oha_s[dates[i]] - oha_s[dates[i-1]] for i in range(1, len(dates))}

    timing = {"previous": -1, "same_day": 0, "next": 1}
    results = []
    for source_mode in ("hmd_actual_combined", "natural_pukaki_plus_tkb"):
        for pki_name, pki_off in timing.items():
            for ohu_name, ohu_off in timing.items():
                for tkb_lag in (-1, 0, 1):
                    residuals = []
                    cumulative = 0.0
                    cumulative_vals = [0.0]
                    for i in range(2, len(dates)-2):
                        d = dates[i]
                        pd = dates[i+pki_off]
                        od = dates[i+ohu_off]
                        td = dates[i+tkb_lag]
                        pki_in = pki_actual[d] if source_mode == "hmd_actual_combined" else pki_nat[d] + q["TKB"][td]
                        pki_release = pki_in - pki_delta[pd] / MM3_PER_DAY_PER_CUMECS - pki_spill[d]
                        ohu_release = ohu_nat[d] - oha_delta[od] / MM3_PER_DAY_PER_CUMECS - oha_spill.get(d, 0.0)
                        res = pki_release + ohu_release - q["OHA"][d]
                        residuals.append(res)
                        cumulative += res * MM3_PER_DAY_PER_CUMECS
                        cumulative_vals.append(cumulative)
                    s = stat(residuals)
                    s.update({
                        "source_mode": source_mode,
                        "pki_storage_delta_timing": pki_name,
                        "ohu_storage_delta_timing": ohu_name,
                        "tkb_lag_days": tkb_lag,
                        "implied_unbounded_junction_storage_range_mm3": round(max(cumulative_vals)-min(cumulative_vals), 3),
                    })
                    s["score"] = round(s["mae"] + 0.05 * s["implied_unbounded_junction_storage_range_mm3"] + abs(s["mean"]), 3)
                    results.append(s)

    results.sort(key=lambda r: (r["score"], r["mae"], r["implied_unbounded_junction_storage_range_mm3"]))
    payload = {
        "status": "waitaki_explicit_upstream_source_balance",
        "year": YEAR,
        "purpose": "Compare HMD combined Pukaki operational inflow against a physically explicit natural-Pukaki plus observed-Tekapo-B delivery representation, with storage timing and short TKB transit lags.",
        "best": results[0],
        "top_12": results[:12],
        "interpretation": [
            "If natural Pukaki + observed TKB materially improves both MAE and cumulative implied junction storage, prefer it for explicit-network validation.",
            "TKB flow is treated as delivered Tekapo-branch water to Lake Pukaki; Gate 17 is not added separately because it enters the same canal system upstream of TKB.",
            "This diagnostic is historical validation only and does not yet define future dispatch policy."
        ],
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(json.dumps(payload["best"], indent=2))


if __name__ == "__main__":
    main()
