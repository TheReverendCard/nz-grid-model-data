from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
INFLOWS = Path("data/hydro/model/inflows_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT_DAILY = Path("data/model/waitaki_network_balance_v3_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_network_balance_v3_summary.json")

YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 0.0864
STATIONS = ("OHA", "OHB", "OHC")
PKI_ACTUAL_FILE = "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv"
OHU_NATURAL_FILE = "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv"
OHU_ACTUAL_FILE = "SI_OHU_Actual_LakeOhau(ExclDiverted)_Outflow_98614(6).csv"
RTH_SPILL_FILE = "SI_RTH_Spill_LakeRuataniwha.csv"
PKI_SPILL_FILE = "SI_PKI_Spill_LakePukaki.csv"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


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
    generation = defaultdict(lambda: defaultdict(float))
    for row in read_csv(GENERATION):
        d = row["date"]
        code = row["site_code"]
        if d.startswith(f"{YEAR}-") and code in STATIONS:
            generation[d][code] += float(row["generation_mwh"])
    return {
        d: {code: (mwh / 24.0) * factors[code] for code, mwh in sites.items()}
        for d, sites in generation.items()
    }


def source_series(path: Path, filename: str) -> dict[str, float]:
    result = {}
    for row in read_csv(path):
        if row["source_file"] == filename and row["date"].startswith(f"{YEAR}-"):
            result[row["date"]] = float(row["flow_m3s"])
    if len(result) < 300:
        raise RuntimeError(f"Validation series {filename} has only {len(result)} rows")
    return result


def pki_storage() -> dict[str, float]:
    out = {}
    for row in read_csv(STORAGE):
        if row["site_code"] == "PKI" and row["date"].startswith(f"{YEAR}-"):
            raw = row.get("total_storage_mm3") or row.get("active_storage_mm3")
            if raw not in (None, ""):
                out[row["date"]] = float(raw)
    return out


def stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "mae": None, "rmse": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 3),
        "mae": round(sum(abs(v) for v in values) / len(values), 3),
        "rmse": round((sum(v * v for v in values) / len(values)) ** 0.5, 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def monthly_mean(rows: list[dict[str, float]], field: str) -> dict[str, float]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["date"][:7]].append(float(row[field]))
    return {m: round(sum(v) / len(v), 3) for m, v in sorted(grouped.items())}


def main() -> None:
    factors = station_factors()
    q = station_flows(factors)
    storage = pki_storage()
    pki_actual = source_series(INFLOWS, PKI_ACTUAL_FILE)
    ohu_natural = source_series(INFLOWS, OHU_NATURAL_FILE)
    ohu_actual = source_series(INFLOWS, OHU_ACTUAL_FILE)
    pki_spill = source_series(SPILL, PKI_SPILL_FILE)
    rth_spill = source_series(SPILL, RTH_SPILL_FILE)

    dates = sorted(
        set(storage) & set(q) & set(pki_actual) & set(ohu_natural) & set(ohu_actual)
        & set(pki_spill) & set(rth_spill)
    )
    if len(dates) < 300:
        raise RuntimeError(f"Insufficient overlap: {len(dates)} days")

    rows = []
    residuals = defaultdict(list)
    previous = None
    for d in dates:
        if previous is None:
            previous = d
            continue
        if not all(code in q[d] for code in STATIONS):
            previous = d
            continue

        delta_pki_mm3 = storage[d] - storage[previous]
        pki_release = pki_actual[d] - delta_pki_mm3 / MM3_PER_DAY_PER_CUMECS - pki_spill[d]

        # Lake Ohau joins the Pukaki/Tekapo route upstream of Ohau A. Test both HMD
        # candidate series at that junction. A valid candidate should make the Pukaki
        # release plus Lake Ohau contribution close on observed OHA turbine flow.
        oha_expected_natural = pki_release + ohu_natural[d]
        oha_expected_actual = pki_release + ohu_actual[d]
        res_oha_natural = oha_expected_natural - q[d]["OHA"]
        res_oha_actual = oha_expected_actual - q[d]["OHA"]

        # Downstream of Ohau A, the observed station chain already nearly closes.
        # Do not add the Lake Ohau inflow again at Ruataniwha: that double-counts it.
        res_rth = q[d]["OHA"] - rth_spill[d] - q[d]["OHB"]
        res_ohbc = q[d]["OHB"] - q[d]["OHC"]

        # Difference between natural inflow and the HMD actual-excluding-diverted
        # series exposes the seasonal modelling/diversion term embedded in metadata.
        ohu_difference = ohu_natural[d] - ohu_actual[d]

        vals = {
            "PKI_plus_OHU_natural_minus_OHA": res_oha_natural,
            "PKI_plus_OHU_actual_minus_OHA": res_oha_actual,
            "OHA_minus_RTHspill_minus_OHB": res_rth,
            "OHB_minus_OHC": res_ohbc,
            "OHU_natural_minus_actual": ohu_difference,
        }
        for key, value in vals.items():
            residuals[key].append(value)

        rows.append({
            "date": d,
            "PKI_actual_combined_inflow_m3s": round(pki_actual[d], 6),
            "PKI_storage_change_mm3": round(delta_pki_mm3, 6),
            "PKI_spill_m3s": round(pki_spill[d], 6),
            "PKI_balance_release_m3s": round(pki_release, 6),
            "OHU_natural_inflow_m3s": round(ohu_natural[d], 6),
            "OHU_actual_excl_diverted_m3s": round(ohu_actual[d], 6),
            "OHU_natural_minus_actual_m3s": round(ohu_difference, 6),
            "OHA_turbine_flow_m3s": round(q[d]["OHA"], 6),
            "PKI_plus_OHU_natural_minus_OHA_m3s": round(res_oha_natural, 6),
            "PKI_plus_OHU_actual_minus_OHA_m3s": round(res_oha_actual, 6),
            "RTH_spill_m3s": round(rth_spill[d], 6),
            "OHB_turbine_flow_m3s": round(q[d]["OHB"], 6),
            "OHA_minus_RTHspill_minus_OHB_m3s": round(res_rth, 6),
            "OHC_turbine_flow_m3s": round(q[d]["OHC"], 6),
            "OHB_minus_OHC_m3s": round(res_ohbc, 6),
        })
        previous = d

    summary = {
        "status": "waitaki_network_balance_v3_corrected_ohau_junction",
        "year": YEAR,
        "network_backbone": [
            "Tekapo contribution -> Lake Pukaki actual combined inflow/storage balance",
            "Pukaki release + Lake Ohau contribution -> Ohau A",
            "Ohau A -> Lake Ruataniwha -> Ohau B -> Ohau C -> Lake Benmore",
        ],
        "candidate_balance_residual_m3s": {
            key: stats(values) for key, values in residuals.items()
        },
        "monthly_OHU_natural_minus_actual_m3s": monthly_mean(rows, "OHU_natural_minus_actual_m3s"),
        "decision_rule": (
            "Use the Lake Ohau candidate that best closes Pukaki-release-plus-Ohau on observed OHA, "
            "while respecting the HMD metadata definition. Never add Lake Ohau again between OHA and OHB."
        ),
        "important_limitations": [
            "Generation-derived turbine flows are daily-average equivalents using fixed HMD plant factors.",
            "The Pukaki actual-combined series is a historical validation construct, not a future dispatch rule.",
            "Unobserved short-term canal/reservoir storage and timestamp conventions remain in daily residuals.",
        ],
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")
    for key, value in summary["candidate_balance_residual_m3s"].items():
        print(f"{key}: mean={value['mean']} m3/s, MAE={value['mae']} m3/s")


if __name__ == "__main__":
    main()
