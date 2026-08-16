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
OUT_DAILY = Path("data/model/waitaki_network_balance_v2_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_network_balance_v2_summary.json")

YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 86400.0 / 1_000_000.0
STATIONS = ("OHA", "OHB", "OHC")
PKI_ACTUAL_FILE = "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv"
OHU_ACTUAL_OUTFLOW_FILE = "SI_OHU_Actual_LakeOhau(ExclDiverted)_Outflow_98614(6).csv"
RTH_SPILL_FILE = "SI_RTH_Spill_LakeRuataniwha.csv"
PKI_SPILL_FILE = "SI_PKI_Spill_LakePukaki.csv"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def station_factors() -> dict[str, float]:
    out = {}
    for row in read_csv(ASSETS):
        code = row.get("site_code", "")
        if row.get("plant_group") == "Waitaki river" and code in STATIONS and row.get("plant_factor_cumecs_per_mw"):
            out[code] = float(row["plant_factor_cumecs_per_mw"])
    missing = sorted(set(STATIONS) - set(out))
    if missing:
        raise RuntimeError(f"Missing Waitaki station factors: {missing}")
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
        raise RuntimeError(f"Validation series {filename} has only {len(result)} rows for {YEAR}")
    return result


def pki_storage() -> dict[str, float]:
    out = {}
    for row in read_csv(STORAGE):
        if row["date"].startswith(f"{YEAR}-") and row["site_code"] == "PKI":
            value = row.get("total_storage_mm3") or row.get("active_storage_mm3")
            if value not in (None, ""):
                out[row["date"]] = float(value)
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


def main() -> None:
    factors = station_factors()
    q = station_flows(factors)
    storage = pki_storage()
    pki_actual_inflow = source_series(INFLOWS, PKI_ACTUAL_FILE)
    ohu_actual_outflow = source_series(INFLOWS, OHU_ACTUAL_OUTFLOW_FILE)
    pki_spill = source_series(SPILL, PKI_SPILL_FILE)
    rth_spill = source_series(SPILL, RTH_SPILL_FILE)

    dates = sorted(set(storage) & set(q) & set(pki_actual_inflow) & set(ohu_actual_outflow) & set(pki_spill) & set(rth_spill))
    if len(dates) < 300:
        raise RuntimeError(f"Insufficient overlap for corrected Waitaki validation: {len(dates)} days")

    rows = []
    pki_residuals = []
    rth_residuals = []
    ohb_ohc_residuals = []
    previous = None

    for d in dates:
        if previous is None:
            previous = d
            continue
        if not all(code in q[d] for code in STATIONS):
            previous = d
            continue

        delta_pki_mm3 = storage[d] - storage[previous]
        # HMD actual Pukaki inflow already includes the Tekapo contribution for the
        # separate-Tekapo simulation. Pukaki releases either through Ohau A or via
        # the Pukaki River spill/bypass.
        pki_balance_release = (
            pki_actual_inflow[d]
            - delta_pki_mm3 / MM3_PER_DAY_PER_CUMECS
            - pki_spill[d]
        )
        pki_residual = pki_balance_release - q[d]["OHA"]

        # Correct topology: Ohau A is fed from the Pukaki canal. Lake Ohau contributes
        # independently to Lake Ruataniwha. Ruataniwha then feeds Ohau B; its spill
        # bypasses Ohau B/C to Benmore. With no observed Ruataniwha storage series,
        # this residual also contains daily Ruataniwha storage/timing change.
        rth_expected_ohb = q[d]["OHA"] + ohu_actual_outflow[d] - rth_spill[d]
        rth_residual = q[d]["OHB"] - rth_expected_ohb
        ohb_ohc_residual = q[d]["OHC"] - q[d]["OHB"]

        pki_residuals.append(pki_residual)
        rth_residuals.append(rth_residual)
        ohb_ohc_residuals.append(ohb_ohc_residual)
        rows.append(
            {
                "date": d,
                "PKI_actual_inflow_m3s": round(pki_actual_inflow[d], 6),
                "PKI_storage_change_mm3": round(delta_pki_mm3, 6),
                "PKI_river_spill_m3s": round(pki_spill[d], 6),
                "PKI_balance_release_m3s": round(pki_balance_release, 6),
                "OHA_turbine_flow_m3s": round(q[d]["OHA"], 6),
                "PKI_balance_minus_OHA_m3s": round(pki_residual, 6),
                "OHU_actual_outflow_to_Ruataniwha_m3s": round(ohu_actual_outflow[d], 6),
                "RTH_spill_to_Benmore_m3s": round(rth_spill[d], 6),
                "OHB_turbine_flow_m3s": round(q[d]["OHB"], 6),
                "RTH_balance_residual_m3s": round(rth_residual, 6),
                "OHC_turbine_flow_m3s": round(q[d]["OHC"], 6),
                "OHC_minus_OHB_m3s": round(ohb_ohc_residual, 6),
            }
        )
        previous = d

    summary = {
        "status": "waitaki_corrected_network_balance_diagnostic",
        "year": YEAR,
        "network_backbone": [
            "Tekapo branch -> Pukaki storage",
            "Pukaki canal -> Ohau A -> Lake Ruataniwha",
            "Lake Ohau -> Upper Ohau River/canal -> Lake Ruataniwha",
            "Lake Ruataniwha -> Ohau B -> Ohau C -> Lake Benmore",
        ],
        "validation_series": {
            "pukaki_actual_inflow": PKI_ACTUAL_FILE,
            "lake_ohau_actual_outflow": OHU_ACTUAL_OUTFLOW_FILE,
            "ruataniwha_spill": RTH_SPILL_FILE,
            "pukaki_river_spill": PKI_SPILL_FILE,
        },
        "balance_residual_m3s": {
            "PKI_actual_balance_minus_OHA_turbine": stats(pki_residuals),
            "Ruataniwha_OHA_plus_Ohau_minus_spill_to_OHB": stats(rth_residuals),
            "OHC_minus_OHB": stats(ohb_ohc_residuals),
        },
        "interpretation": [
            "A small, weakly biased Pukaki residual supports treating Ohau A turbine flow as the main Pukaki-canal release in 2024 validation.",
            "The Ruataniwha residual should not be expected to be zero because no observed daily Ruataniwha storage state is available in the HMD storage index.",
            "The Ohau B/C residual is the cleanest serial-cascade check because the two stations are tightly coupled.",
        ],
        "important_limitations": [
            "Generation-derived turbine flow is a daily-average equivalent using fixed HMD plant factors.",
            "HMD actual flow series are used here as validation targets, not as future dispatch assumptions.",
            "Daily storage timestamp conventions can produce residual noise even when the physical balance is valid at finer resolution.",
        ],
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
