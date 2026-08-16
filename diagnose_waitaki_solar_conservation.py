from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

BASELINE = Path("data/model/baseline_2024.json")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
STORAGE_ENERGY = Path("data/model/hydro_storage_energy_daily.csv")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
OUT_DAILY = Path("data/model/waitaki_solar_conservation_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_solar_conservation_summary.json")

YEAR = 2024
WAITAKI_GENERATION_SITES = {"TKA", "TKB", "OHA", "OHB", "OHC", "BEN", "AVI", "WTK"}
THERMAL_FUELS = {"Coal", "Gas", "Diesel"}
WAITAKI_STORAGE_COLUMNS = [
    "TKA_energy_equivalent_gwh",
    "PKI_energy_equivalent_gwh",
    "OHA_energy_equivalent_gwh",
]
SOLAR_SCENARIOS_GWH = [0, 500, 1000, 1500, 2500]
WINTER_ENTRY_DATE = "2024-05-01"


def load_baseline_demand() -> dict[str, float]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {
        row["date"]: float(row["measured_grid_demand_mwh"])
        for row in baseline["daily"]
        if row["date"].startswith(f"{YEAR}-")
    }


def load_generation() -> tuple[dict[str, float], dict[str, float]]:
    waitaki: defaultdict[str, float] = defaultdict(float)
    thermal: defaultdict[str, float] = defaultdict(float)
    with GENERATION.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            d = row["date"]
            if not d.startswith(f"{YEAR}-"):
                continue
            value = float(row["generation_mwh"])
            if row["site_code"] in WAITAKI_GENERATION_SITES:
                waitaki[d] += value
            if row["fuel_code"] in THERMAL_FUELS:
                thermal[d] += value
    return dict(waitaki), dict(thermal)


def load_waitaki_storage() -> dict[str, float]:
    rows: dict[str, float] = {}
    with STORAGE_ENERGY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            d = row["date"]
            vals = []
            complete = True
            for col in WAITAKI_STORAGE_COLUMNS:
                raw = row.get(col, "")
                if raw == "":
                    complete = False
                    break
                vals.append(float(raw))
            if complete:
                rows[d] = sum(vals)
    return rows


def solar_month_weights() -> dict[int, float]:
    seasonality = json.loads(SEASONALITY.read_text(encoding="utf-8"))
    raw = seasonality["solar_method"]["monthly_index_vs_annual_mean_daily"]
    return {int(month): float(value) for month, value in raw.items()}


def scenario_daily_solar(annual_gwh: float, dates: list[str], month_index: dict[int, float]) -> dict[str, float]:
    # The monthly index is relative to mean daily output. Scale daily values so the
    # calendar-year sum exactly equals the requested incremental generation.
    unscaled = {}
    for d in dates:
        month = int(d[5:7])
        unscaled[d] = month_index[month]
    denom = sum(unscaled.values())
    annual_mwh = annual_gwh * 1000.0
    return {d: annual_mwh * w / denom for d, w in unscaled.items()}


def main() -> None:
    demand = load_baseline_demand()
    waitaki_gen, thermal_gen = load_generation()
    storage = load_waitaki_storage()
    month_index = solar_month_weights()

    dates = sorted(d for d in demand if d in storage)
    if len(dates) != 366:
        raise RuntimeError(f"Expected 366 complete 2024 days, got {len(dates)}")

    historical_storage_values = list(storage.values())
    empirical_storage_ceiling_gwh = max(historical_storage_values)
    empirical_storage_floor_gwh = min(historical_storage_values)

    scenario_results = {}
    daily_rows = []

    for annual_gwh in SOLAR_SCENARIOS_GWH:
        solar = scenario_daily_solar(annual_gwh, dates, month_index)
        retained_delta_gwh = 0.0
        retained_total_mwh = 0.0
        thermal_displaced_mwh = 0.0
        curtailed_mwh = 0.0
        storage_limited_mwh = 0.0
        max_delta_gwh = 0.0
        winter_entry_delta_gwh = None

        for d in dates:
            extra = solar[d]
            observed_waitaki = waitaki_gen.get(d, 0.0)
            observed_thermal = thermal_gen.get(d, 0.0)
            observed_storage = storage[d]

            # Upper-bound conservation case: incremental solar first displaces
            # Waitaki hydro generation. The avoided hydro is carried forward as
            # additional stored-energy equivalent, but may not exceed the largest
            # upper-Waitaki storage state observed in the historical record.
            hydro_candidate = min(extra, observed_waitaki)
            available_headroom_mwh = max(
                0.0,
                (empirical_storage_ceiling_gwh - observed_storage - retained_delta_gwh) * 1000.0,
            )
            hydro_stored = min(hydro_candidate, available_headroom_mwh)
            storage_limited = hydro_candidate - hydro_stored
            retained_delta_gwh += hydro_stored / 1000.0
            retained_total_mwh += hydro_stored
            storage_limited_mwh += storage_limited

            remaining = extra - hydro_stored
            thermal_displaced = min(remaining, observed_thermal)
            thermal_displaced_mwh += thermal_displaced
            remaining -= thermal_displaced
            curtailed_mwh += max(0.0, remaining)

            max_delta_gwh = max(max_delta_gwh, retained_delta_gwh)
            if d == WINTER_ENTRY_DATE:
                winter_entry_delta_gwh = retained_delta_gwh

            daily_rows.append({
                "scenario_incremental_solar_gwh": annual_gwh,
                "date": d,
                "incremental_solar_mwh": round(extra, 6),
                "observed_waitaki_generation_mwh": round(observed_waitaki, 6),
                "observed_thermal_generation_mwh": round(observed_thermal, 6),
                "observed_upper_waitaki_storage_gwh": round(observed_storage, 6),
                "counterfactual_storage_delta_gwh": round(retained_delta_gwh, 6),
                "counterfactual_upper_waitaki_storage_gwh": round(observed_storage + retained_delta_gwh, 6),
                "hydro_generation_avoided_and_stored_mwh": round(hydro_stored, 6),
                "storage_limited_hydro_displacement_mwh": round(storage_limited, 6),
                "thermal_generation_displaced_mwh": round(thermal_displaced, 6),
                "curtailed_incremental_solar_mwh": round(max(0.0, remaining), 6),
            })

        scenario_results[str(annual_gwh)] = {
            "incremental_solar_gwh": annual_gwh,
            "waitaki_hydro_generation_avoided_and_retained_gwh": round(retained_total_mwh / 1000.0, 3),
            "winter_entry_storage_increase_gwh": round(winter_entry_delta_gwh or 0.0, 3),
            "maximum_storage_increase_gwh": round(max_delta_gwh, 3),
            "thermal_generation_displaced_gwh": round(thermal_displaced_mwh / 1000.0, 3),
            "storage_limited_hydro_displacement_gwh": round(storage_limited_mwh / 1000.0, 3),
            "incremental_solar_curtailed_gwh": round(curtailed_mwh / 1000.0, 3),
        }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(daily_rows[0].keys()))
        writer.writeheader()
        writer.writerows(daily_rows)

    summary = {
        "status": "upper_bound_diagnostic_not_dispatch_model",
        "purpose": "Quantify the maximum plausible 2024 Waitaki hydro-storage conservation from incremental solar before building the full scheme dispatcher.",
        "method": {
            "weather_and_system": "Observed 2024 grid demand, Waitaki generation, thermal generation and upper-Waitaki storage trajectory.",
            "incremental_solar_shape": "Observed NZ utility-solar monthly seasonality, scaled to exact annual scenario energy.",
            "priority": "Incremental solar displaces observed Waitaki hydro first; retained hydro is carried as additional stored-energy equivalent subject to an empirical upper-storage ceiling. Remaining solar then displaces observed thermal generation, then is curtailed.",
            "upper_waitaki_storage_sites": ["TKA", "PKI", "OHA"],
            "waitaki_generation_sites": sorted(WAITAKI_GENERATION_SITES),
            "winter_entry_date": WINTER_ENTRY_DATE,
            "empirical_storage_floor_gwh": round(empirical_storage_floor_gwh, 3),
            "empirical_storage_ceiling_gwh": round(empirical_storage_ceiling_gwh, 3),
        },
        "scenarios": scenario_results,
        "important_limitations": [
            "This is deliberately an upper-bound conservation diagnostic, not an operational dispatch forecast.",
            "It assumes operators can choose to displace Waitaki hydro before thermal generation when incremental solar arrives.",
            "It carries conserved hydro forward without re-optimising later releases; a real dispatcher may use some of that water before winter or for peaks.",
            "The storage ceiling is the maximum observed combined Tekapo/Pukaki/Ohau energy-equivalent storage in the downloaded historical series, not a legal or engineering maximum.",
            "The calculation works in energy-equivalent storage and therefore does not yet enforce individual canal, station, spill, minimum-flow or travel-time constraints.",
            "Its value is as a ceiling and sanity check: the later scheme-based dispatcher should generally preserve no more hydro than this diagnostic for the same solar scenario."
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} ({len(daily_rows)} rows)")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
