from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
INFLOWS = Path("data/hydro/model/inflows_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT_DAILY = Path("data/model/waitaki_network_balance_v4_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_network_balance_v4_summary.json")

YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 0.0864
STATIONS = ("OHA", "OHB", "OHC")
PKI_ACTUAL_FILE = "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv"
OHU_NATURAL_FILE = "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv"
PKI_SPILL_FILE = "SI_PKI_Spill_LakePukaki.csv"
RTH_SPILL_FILE = "SI_RTH_Spill_LakeRuataniwha.csv"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def stats(values: list[float]) -> dict[str, float | int | None]:
    vals = [v for v in values if math.isfinite(v)]
    if not vals:
        return {"count": 0, "mean": None, "mae": None, "rmse": None, "min": None, "max": None}
    return {
        "count": len(vals),
        "mean": round(sum(vals) / len(vals), 3),
        "mae": round(sum(abs(v) for v in vals) / len(vals), 3),
        "rmse": round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
    }


def station_factors() -> dict[str, float]:
    out = {}
    for row in read_csv(ASSETS):
        code = row.get("site_code", "")
        if code in STATIONS and row.get("plant_factor_cumecs_per_mw"):
            out[code] = float(row["plant_factor_cumecs_per_mw"])
    missing = sorted(set(STATIONS) - set(out))
    if missing:
        raise RuntimeError(f"Missing station factors: {missing}")
    return out


def station_flows(factors: dict[str, float]) -> dict[str, dict[str, float]]:
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


def source_series(path: Path, filename: str) -> dict[str, float]:
    out = {}
    for row in read_csv(path):
        if row.get("source_file") == filename and row.get("date", "").startswith(f"{YEAR}-"):
            out[row["date"]] = float(row["flow_m3s"])
    if len(out) < 300:
        raise RuntimeError(f"Validation series {filename} has only {len(out)} rows")
    return out


def storage_series(site_code: str) -> dict[str, float]:
    out = {}
    for row in read_csv(STORAGE):
        if row.get("site_code") == site_code and row.get("date", "").startswith(f"{YEAR}-"):
            raw = row.get("total_storage_mm3") or row.get("active_storage_mm3") or row.get("storage_mm3")
            if raw not in (None, ""):
                out[row["date"]] = float(raw)
    if len(out) < 300:
        raise RuntimeError(f"Storage series {site_code} has only {len(out)} rows")
    return out


def site_spill(site_code: str) -> dict[str, float]:
    out = defaultdict(float)
    found = False
    for row in read_csv(SPILL):
        if not row.get("date", "").startswith(f"{YEAR}-"):
            continue
        if row.get("site_code") == site_code:
            out[row["date"]] += float(row.get("flow_m3s") or 0.0)
            found = True
    return dict(out) if found else {}


def monthly(rows: list[dict[str, object]], field: str) -> dict[str, float]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row["date"])[:7]].append(float(row[field]))
    return {m: round(sum(vals) / len(vals), 3) for m, vals in sorted(grouped.items())}


def main() -> None:
    q = station_flows(station_factors())
    pki_storage = storage_series("PKI")
    ohu_storage = storage_series("OHA")
    pki_actual = source_series(INFLOWS, PKI_ACTUAL_FILE)
    ohu_natural = source_series(INFLOWS, OHU_NATURAL_FILE)
    pki_spill = source_series(SPILL, PKI_SPILL_FILE)
    rth_spill = source_series(SPILL, RTH_SPILL_FILE)
    ohu_spill = site_spill("OHA")

    dates = sorted(
        set(pki_storage) & set(ohu_storage) & set(pki_actual) & set(ohu_natural)
        & set(pki_spill) & set(rth_spill) & set(q["OHA"]) & set(q["OHB"]) & set(q["OHC"])
    )
    if len(dates) < 300:
        raise RuntimeError(f"Insufficient 2024 overlap: {len(dates)}")

    # Precompute storage deltas. Testing adjacent-day offsets makes the diagnostic
    # explicit about HMD timestamp convention rather than burying it in the LP.
    pki_delta = {}
    ohu_delta = {}
    for i in range(1, len(dates)):
        d = dates[i]
        prev = dates[i - 1]
        pki_delta[d] = pki_storage[d] - pki_storage[prev]
        ohu_delta[d] = ohu_storage[d] - ohu_storage[prev]

    residuals = defaultdict(list)
    rows = []
    for i in range(2, len(dates) - 1):
        d = dates[i]
        pki_release = pki_actual[d] - pki_delta[d] / MM3_PER_DAY_PER_CUMECS - pki_spill[d]
        spill_ohu = ohu_spill.get(d, 0.0)

        # Three timing conventions for the Lake Ohau storage change.
        releases = {
            "previous_delta": ohu_natural[d] - ohu_delta[dates[i - 1]] / MM3_PER_DAY_PER_CUMECS - spill_ohu,
            "same_day_delta": ohu_natural[d] - ohu_delta[d] / MM3_PER_DAY_PER_CUMECS - spill_ohu,
            "next_delta": ohu_natural[d] - ohu_delta[dates[i + 1]] / MM3_PER_DAY_PER_CUMECS - spill_ohu,
            "no_storage_timing": ohu_natural[d] - spill_ohu,
        }

        record: dict[str, object] = {
            "date": d,
            "PKI_balance_release_m3s": round(pki_release, 6),
            "OHU_natural_inflow_m3s": round(ohu_natural[d], 6),
            "OHU_spill_m3s": round(spill_ohu, 6),
            "OHA_turbine_flow_m3s": round(q["OHA"][d], 6),
            "OHB_turbine_flow_m3s": round(q["OHB"][d], 6),
            "OHC_turbine_flow_m3s": round(q["OHC"][d], 6),
            "RTH_spill_m3s": round(rth_spill[d], 6),
        }
        for label, ohu_release in releases.items():
            res = pki_release + ohu_release - q["OHA"][d]
            residuals[f"junction_{label}"].append(res)
            record[f"OHU_release_{label}_m3s"] = round(ohu_release, 6)
            record[f"junction_residual_{label}_m3s"] = round(res, 6)

        rth_res = q["OHA"][d] - rth_spill[d] - q["OHB"][d]
        ohbc_res = q["OHB"][d] - q["OHC"][d]
        residuals["OHA_minus_RTHspill_minus_OHB"].append(rth_res)
        residuals["OHB_minus_OHC"].append(ohbc_res)
        record["OHA_minus_RTHspill_minus_OHB_m3s"] = round(rth_res, 6)
        record["OHB_minus_OHC_m3s"] = round(ohbc_res, 6)
        rows.append(record)

    summary = {
        "status": "waitaki_network_balance_v4_storage_timing",
        "year": YEAR,
        "purpose": "Use observed Lake Ohau storage changes to infer its daily release into the validated upstream-Ohau-A junction, and identify the HMD daily timestamp convention before optimization.",
        "oha_spill_rows_found": bool(ohu_spill),
        "candidate_residual_m3s": {key: stats(vals) for key, vals in residuals.items()},
        "monthly_same_day_junction_residual_m3s": monthly(rows, "junction_residual_same_day_delta_m3s"),
        "decision_rule": "Prefer the storage timing convention with materially lower MAE/RMSE and weak mean bias. If none improves on the no-storage case, retain the v3 daily residual as unresolved timing/canal storage noise rather than forcing an artificial balance.",
        "next_gate": "If a storage-timed junction materially improves daily closure while downstream OHA-OHB-OHC remains tight, proceed to the explicit network validation LP.",
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")
    for key, vals in summary["candidate_residual_m3s"].items():
        print(f"{key}: mean={vals['mean']} m3/s, MAE={vals['mae']} m3/s, RMSE={vals['rmse']} m3/s")


if __name__ == "__main__":
    main()
