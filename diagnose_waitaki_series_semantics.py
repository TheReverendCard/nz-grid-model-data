from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

INFLOWS = Path("data/hydro/model/inflows_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
STORAGE = Path("data/hydro/model/storage_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT = Path("data/model/waitaki_series_semantics_2024.json")
YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 0.0864

FILES = {
    "pki_actual_combined": "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv",
    "pki_natural": "SI_PKI_Natural_LakePukaki_Inflow_98770(1).csv",
    "ohu_natural": "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv",
    "ohu_actual_excl_diverted": "SI_OHU_Actual_LakeOhau(ExclDiverted)_Outflow_98614(6).csv",
    "pki_spill": "SI_PKI_Spill_LakePukaki.csv",
    "rth_spill": "SI_RTH_Spill_LakeRuataniwha.csv",
}
STATIONS = ("TKB", "OHA", "OHB", "OHC")


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def stats(values: list[float]) -> dict[str, float | int | None]:
    vals = [v for v in values if math.isfinite(v)]
    if not vals:
        return {"count": 0, "mean": None, "min": None, "max": None, "annual_volume_mm3": None}
    return {
        "count": len(vals),
        "mean": round(sum(vals) / len(vals), 3),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
        "annual_volume_mm3": round(sum(vals) * MM3_PER_DAY_PER_CUMECS, 3),
    }


def corr_pairs(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a)
    db = sum((y - mb) ** 2 for y in b)
    if da <= 0 or db <= 0:
        return None
    return num / math.sqrt(da * db)


def lagged_corr(series: dict[str, float], target: dict[str, float]) -> dict[str, float | None]:
    dates = sorted(set(series) & set(target))
    pos = {d: i for i, d in enumerate(dates)}
    out: dict[str, float | None] = {}
    for lag in range(-3, 4):
        xs, ys = [], []
        for d in dates:
            j = pos[d] + lag
            if 0 <= j < len(dates):
                d2 = dates[j]
                xs.append(series[d])
                ys.append(target[d2])
        c = corr_pairs(xs, ys)
        out[str(lag)] = None if c is None else round(c, 4)
    return out


def load_selected_flows(path: Path, selected: dict[str, str]) -> dict[str, dict[str, float]]:
    by_filename = {v: k for k, v in selected.items()}
    out = {k: {} for k in selected}
    for row in read_csv(path):
        d = row["date"]
        key = by_filename.get(row.get("source_file", ""))
        if key and d.startswith(f"{YEAR}-"):
            out[key][d] = float(row["flow_m3s"])
    return out


def load_station_flows() -> dict[str, dict[str, float]]:
    factors: dict[str, float] = {}
    for row in read_csv(ASSETS):
        code = row.get("site_code", "")
        if code in STATIONS and row.get("plant_factor_cumecs_per_mw"):
            factors[code] = float(row["plant_factor_cumecs_per_mw"])
    missing = sorted(set(STATIONS) - set(factors))
    if missing:
        raise RuntimeError(f"Missing plant factors: {missing}")

    daily = {s: defaultdict(float) for s in STATIONS}
    for row in read_csv(GENERATION):
        d = row["date"]
        code = row["site_code"]
        if d.startswith(f"{YEAR}-") and code in STATIONS:
            daily[code][d] += float(row["generation_mwh"])
    return {
        code: {d: mwh / 24.0 * factors[code] for d, mwh in vals.items()}
        for code, vals in daily.items()
    }


def load_pki_storage() -> dict[str, float]:
    out = {}
    for row in read_csv(STORAGE):
        if row["site_code"] == "PKI" and row["date"].startswith(f"{YEAR}-"):
            raw = row.get("total_storage_mm3") or row.get("active_storage_mm3")
            if raw not in (None, ""):
                out[row["date"]] = float(raw)
    return out


def monthly(series: dict[str, float]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for d, v in series.items():
        grouped[d[:7]].append(v)
    return {m: round(sum(v) / len(v), 3) for m, v in sorted(grouped.items())}


def residual_stats(series: dict[str, float]) -> dict[str, object]:
    vals = list(series.values())
    base = stats(vals)
    base["mae"] = round(sum(abs(v) for v in vals) / len(vals), 3) if vals else None
    base["rmse"] = round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3) if vals else None
    base["monthly_mean_m3s"] = monthly(series)
    return base


def main() -> None:
    inflow = load_selected_flows(INFLOWS, {k: v for k, v in FILES.items() if "spill" not in k})
    spills = load_selected_flows(SPILL, {k: v for k, v in FILES.items() if "spill" in k})
    q = load_station_flows()
    storage = load_pki_storage()

    all_series: dict[str, dict[str, float]] = {**inflow, **spills, **{f"station_{k}": v for k, v in q.items()}}
    for key, series in all_series.items():
        if len(series) < 300:
            raise RuntimeError(f"Insufficient 2024 observations for {key}: {len(series)}")

    dates = sorted(set(storage) & set(q["OHA"]) & set(q["OHB"]) & set(q["OHC"]) & set(q["TKB"]) &
                   set(inflow["pki_actual_combined"]) & set(inflow["pki_natural"]) &
                   set(inflow["ohu_natural"]) & set(inflow["ohu_actual_excl_diverted"]) &
                   set(spills["pki_spill"]) & set(spills["rth_spill"]))

    equations: dict[str, dict[str, float]] = {
        "pki_actual_combined_minus_storage_spill_OHA": {},
        "pki_natural_plus_TKB_minus_storage_spill_OHA": {},
        "OHA_minus_RTHspill_minus_OHB": {},
        "OHA_plus_OHUactual_minus_RTHspill_minus_OHB": {},
        "OHA_plus_OHUnatural_minus_RTHspill_minus_OHB": {},
        "OHB_minus_OHC": {},
    }
    prev = None
    for d in dates:
        if prev is None:
            prev = d
            continue
        delta_q = (storage[d] - storage[prev]) / MM3_PER_DAY_PER_CUMECS
        equations["pki_actual_combined_minus_storage_spill_OHA"][d] = (
            inflow["pki_actual_combined"][d] - delta_q - spills["pki_spill"][d] - q["OHA"][d]
        )
        equations["pki_natural_plus_TKB_minus_storage_spill_OHA"][d] = (
            inflow["pki_natural"][d] + q["TKB"][d] - delta_q - spills["pki_spill"][d] - q["OHA"][d]
        )
        equations["OHA_minus_RTHspill_minus_OHB"][d] = q["OHA"][d] - spills["rth_spill"][d] - q["OHB"][d]
        equations["OHA_plus_OHUactual_minus_RTHspill_minus_OHB"][d] = (
            q["OHA"][d] + inflow["ohu_actual_excl_diverted"][d] - spills["rth_spill"][d] - q["OHB"][d]
        )
        equations["OHA_plus_OHUnatural_minus_RTHspill_minus_OHB"][d] = (
            q["OHA"][d] + inflow["ohu_natural"][d] - spills["rth_spill"][d] - q["OHB"][d]
        )
        equations["OHB_minus_OHC"][d] = q["OHB"][d] - q["OHC"][d]
        prev = d

    summary = {
        "status": "waitaki_hmd_series_semantics_diagnostic",
        "year": YEAR,
        "purpose": "Determine which HMD Waitaki flow series can be used as literal physical terms before building the optimizing network dispatcher.",
        "series": {
            key: {
                "source_file": FILES.get(key),
                **stats(list(series.values())),
                "monthly_mean_m3s": monthly(series),
            }
            for key, series in all_series.items()
        },
        "candidate_balance_residuals": {key: residual_stats(series) for key, series in equations.items()},
        "lagged_correlations": {
            "pki_actual_combined_vs_OHA": lagged_corr(inflow["pki_actual_combined"], q["OHA"]),
            "pki_natural_vs_OHA": lagged_corr(inflow["pki_natural"], q["OHA"]),
            "TKB_vs_OHA": lagged_corr(q["TKB"], q["OHA"]),
            "OHA_vs_OHB": lagged_corr(q["OHA"], q["OHB"]),
            "OHU_actual_vs_OHB": lagged_corr(inflow["ohu_actual_excl_diverted"], q["OHB"]),
            "OHU_natural_vs_OHB": lagged_corr(inflow["ohu_natural"], q["OHB"]),
        },
        "interpretation_rules": [
            "Prefer the candidate balance with residual mean closest to zero and materially lower MAE/RMSE, subject to HMD metadata semantics.",
            "If OHU actual is approximately 8 m3/s Nov-Apr and 12 m3/s May-Oct, treat it as the indexed seasonal modelling term rather than a measured total Lake Ohau contribution.",
            "Do not add a Lake Ohau term to the Ruataniwha balance merely because it exists in the HMD index; only include it if both metadata and closure support that interpretation.",
            "OHB-OHC remains a direct empirical benchmark for the serial lower-Ohau chain."
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    for name, values in summary["candidate_balance_residuals"].items():
        print(f"{name}: mean={values['mean']} m3/s, MAE={values['mae']} m3/s")


if __name__ == "__main__":
    main()
