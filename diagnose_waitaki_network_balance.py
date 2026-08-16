from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
INFLOWS = Path("data/hydro/model/inflows_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
TRIBUTARY = Path("data/hydro/model/tributary_flows_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT_DAILY = Path("data/model/waitaki_network_balance_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_network_balance_summary.json")
YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 86400.0 / 1_000_000.0
STATIONS = ("TKA", "TKB", "OHA", "OHB", "OHC", "BEN", "AVI", "WTK")
HEADWATER = ("TKA", "PKI", "OHA")
LOWER_TRIBUTARY = ("BEN", "AVI", "WTK")


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def station_factors() -> dict[str, float]:
    factors = {}
    for row in read_csv(ASSETS):
        if row["plant_group"] != "Waitaki river":
            continue
        code = row["site_code"]
        if code in STATIONS and row.get("plant_factor_cumecs_per_mw"):
            factors[code] = float(row["plant_factor_cumecs_per_mw"])
    missing = sorted(set(STATIONS) - set(factors))
    if missing:
        raise RuntimeError(f"Missing Waitaki plant factors: {missing}")
    return factors


def observed_station_flows(factors: dict[str, float]) -> dict[str, dict[str, float]]:
    generation = defaultdict(lambda: defaultdict(float))
    for row in read_csv(GENERATION):
        d = row["date"]
        code = row["site_code"]
        if d.startswith(f"{YEAR}-") and code in STATIONS:
            generation[d][code] += float(row["generation_mwh"])
    flows = defaultdict(dict)
    for d, sites in generation.items():
        for code, mwh in sites.items():
            mean_mw = mwh / 24.0
            flows[d][code] = mean_mw * factors[code]
    return dict(flows)


def load_storage() -> dict[str, dict[str, float]]:
    out = defaultdict(dict)
    for row in read_csv(STORAGE):
        d = row["date"]
        code = row["site_code"]
        if d.startswith(f"{YEAR}-") and code in HEADWATER:
            value = row.get("total_storage_mm3") or row.get("active_storage_mm3")
            if value not in (None, ""):
                out[d][code] = float(value)
    return dict(out)


def load_flows(path: Path, allowed: set[str]) -> dict[str, dict[str, float]]:
    out = defaultdict(lambda: defaultdict(float))
    for row in read_csv(path):
        d = row["date"]
        code = row["site_code"]
        if d.startswith(f"{YEAR}-") and code in allowed:
            out[d][code] += float(row["flow_m3s"])
    return {d: dict(v) for d, v in out.items()}


def stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "mae": None, "rmse": None, "min": None, "max": None}
    mean = sum(values) / len(values)
    mae = sum(abs(v) for v in values) / len(values)
    rmse = (sum(v * v for v in values) / len(values)) ** 0.5
    return {
        "count": len(values),
        "mean": round(mean, 3),
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def main() -> None:
    factors = station_factors()
    turbine = observed_station_flows(factors)
    storage = load_storage()
    inflow = load_flows(INFLOWS, set(HEADWATER))
    spill = load_flows(SPILL, set(HEADWATER))
    tributary = load_flows(TRIBUTARY, set(LOWER_TRIBUTARY))

    dates = sorted(set(storage) & set(turbine))
    if len(dates) < 300:
        raise RuntimeError(f"Insufficient overlapping 2024 Waitaki observations: {len(dates)} days")

    rows = []
    tka_residuals = []
    oha_release_residuals = []
    pki_balance_residuals = []
    pki_canal_values = []
    pki_canal_negative_days = 0
    lower_residuals = {"BEN_to_AVI": [], "AVI_to_WTK": []}

    previous = None
    for d in dates:
        if previous is None:
            previous = d
            continue
        if not all(code in storage.get(d, {}) and code in storage.get(previous, {}) for code in HEADWATER):
            previous = d
            continue
        if not all(code in turbine.get(d, {}) for code in STATIONS):
            previous = d
            continue

        delta = {s: storage[d][s] - storage[previous][s] for s in HEADWATER}
        natural = {s: inflow.get(d, {}).get(s, 0.0) for s in HEADWATER}
        sp = {s: spill.get(d, {}).get(s, 0.0) for s in HEADWATER}
        q = turbine[d]

        # Storage accounting release equivalents. These are diagnostics, not assumed
        # future dispatch rules. OHA is particularly useful because its earlier
        # residual diagnostic showed comparatively few negative accounting days.
        tka_accounting_release = natural["TKA"] - delta["TKA"] / MM3_PER_DAY_PER_CUMECS - sp["TKA"]
        oha_accounting_release = natural["OHA"] - delta["OHA"] / MM3_PER_DAY_PER_CUMECS - sp["OHA"]

        # Ohau A receives Lake Ohau water plus the Pukaki canal contribution.
        pki_canal_to_ohau_a = q["OHA"] - oha_accounting_release
        if pki_canal_to_ohau_a < 0:
            pki_canal_negative_days += 1
        pki_canal_values.append(pki_canal_to_ohau_a)

        # TKB discharge reaches Lake Pukaki. Pukaki natural inflow + TKB water -
        # storage accumulation - spill should approximately equal canal release.
        pki_implied_outflow = (
            natural["PKI"]
            + q["TKB"]
            - delta["PKI"] / MM3_PER_DAY_PER_CUMECS
            - sp["PKI"]
        )
        pki_balance_residual = pki_implied_outflow - pki_canal_to_ohau_a

        tka_residual = tka_accounting_release - q["TKA"]
        # OHA chain check after separating the headwater release contribution.
        oha_release_residual = oha_accounting_release + pki_canal_to_ohau_a - q["OHA"]

        ben_avi = q["AVI"] - q["BEN"] - tributary.get(d, {}).get("AVI", 0.0)
        avi_wtk = q["WTK"] - q["AVI"] - tributary.get(d, {}).get("WTK", 0.0)

        tka_residuals.append(tka_residual)
        oha_release_residuals.append(oha_release_residual)
        pki_balance_residuals.append(pki_balance_residual)
        lower_residuals["BEN_to_AVI"].append(ben_avi)
        lower_residuals["AVI_to_WTK"].append(avi_wtk)

        rows.append({
            "date": d,
            "TKA_turbine_flow_m3s": round(q["TKA"], 6),
            "TKB_turbine_flow_m3s": round(q["TKB"], 6),
            "OHA_turbine_flow_m3s": round(q["OHA"], 6),
            "OHB_turbine_flow_m3s": round(q["OHB"], 6),
            "OHC_turbine_flow_m3s": round(q["OHC"], 6),
            "BEN_turbine_flow_m3s": round(q["BEN"], 6),
            "AVI_turbine_flow_m3s": round(q["AVI"], 6),
            "WTK_turbine_flow_m3s": round(q["WTK"], 6),
            "TKA_accounting_release_m3s": round(tka_accounting_release, 6),
            "TKA_release_minus_turbine_m3s": round(tka_residual, 6),
            "OHA_accounting_lake_release_m3s": round(oha_accounting_release, 6),
            "PKI_canal_to_OHA_estimate_m3s": round(pki_canal_to_ohau_a, 6),
            "PKI_implied_outflow_from_balance_m3s": round(pki_implied_outflow, 6),
            "PKI_balance_residual_m3s": round(pki_balance_residual, 6),
            "AVI_incremental_tributary_m3s": round(tributary.get(d, {}).get("AVI", 0.0), 6),
            "WTK_incremental_tributary_m3s": round(tributary.get(d, {}).get("WTK", 0.0), 6),
            "BEN_to_AVI_balance_residual_m3s": round(ben_avi, 6),
            "AVI_to_WTK_balance_residual_m3s": round(avi_wtk, 6),
        })
        previous = d

    if not rows:
        raise RuntimeError("Network balance diagnostic produced no rows")

    mean_pki_canal = sum(pki_canal_values) / len(pki_canal_values)
    pki_canal_annual_mm3 = sum(pki_canal_values) * MM3_PER_DAY_PER_CUMECS
    summary = {
        "status": "waitaki_explicit_network_balance_diagnostic",
        "year": YEAR,
        "purpose": "Validate explicit Waitaki routing and empirically estimate the Pukaki-canal contribution to Ohau A before building an optimizing node-and-arc dispatcher.",
        "network_backbone": [
            "TKA storage -> Tekapo A -> Tekapo B -> PKI storage",
            "PKI canal + OHA lake release -> Ohau A -> Lake Ruataniwha -> Ohau B -> Ohau C -> BEN",
            "BEN -> Aviemore -> Waitaki",
        ],
        "pukaki_canal_estimate": {
            "method": "OHA turbine-equivalent flow minus Lake Ohau release inferred from natural inflow, observed storage change and HMD spill/release.",
            "mean_m3s": round(mean_pki_canal, 3),
            "annual_volume_mm3": round(pki_canal_annual_mm3, 3),
            "negative_day_count": pki_canal_negative_days,
            "day_count": len(pki_canal_values),
        },
        "balance_residual_m3s": {
            "TKA_storage_accounting_minus_TKA_turbine": stats(tka_residuals),
            "PKI_storage_balance_minus_estimated_canal_release": stats(pki_balance_residuals),
            "OHA_split_identity_check": stats(oha_release_residuals),
            "BEN_to_AVI_after_incremental_tributary": stats(lower_residuals["BEN_to_AVI"]),
            "AVI_to_WTK_after_incremental_tributary": stats(lower_residuals["AVI_to_WTK"]),
        },
        "interpretation": [
            "A small PKI residual would support using the inferred Pukaki-canal series as an empirical validation target for the explicit dispatcher.",
            "A large or biased PKI residual means additional Pukaki/Tekapo bypass, timing or series-definition terms must be represented before dispatch calibration.",
            "Lower-cascade residuals are expected to retain short-term storage, spill and daily timing effects because Benmore/Aviemore observed storage series are not present in the HMD storage index.",
        ],
        "important_limitations": [
            "Generation-derived turbine flows are daily-average equivalents using fixed HMD plant factors.",
            "Storage accounting uses day-to-day changes and can be sensitive to timestamp conventions.",
            "The inferred Pukaki-canal contribution is a validation diagnostic, not yet a direct measured canal-flow series.",
        ],
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}; inferred mean Pukaki canal flow={mean_pki_canal:.2f} m3/s")


if __name__ == "__main__":
    main()
