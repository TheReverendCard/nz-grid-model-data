from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

GENERATION = Path("data/wholesale/model/generation_daily.csv")
STORAGE_ENERGY = Path("data/model/hydro_storage_energy_daily.csv")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
OUT_DAILY = Path("data/model/waitaki_solar_delta_dispatch_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_solar_delta_dispatch_summary.json")

YEAR = 2024
WAITAKI_GENERATION_SITES = {"TKA", "TKB", "OHA", "OHB", "OHC", "BEN", "AVI", "WTK"}
THERMAL_FUELS = {"Coal", "Gas", "Diesel"}
WAITAKI_STORAGE_COLUMNS = [
    "TKA_energy_equivalent_gwh",
    "PKI_energy_equivalent_gwh",
    "OHA_energy_equivalent_gwh",
]
SOLAR_SCENARIOS_GWH = [0, 500, 1000, 1500, 2500]
WINTER_START = "2024-05-01"


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
            if not d.startswith(f"{YEAR}-"):
                continue
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
    weights = {d: month_index[int(d[5:7])] for d in dates}
    denom = sum(weights.values())
    annual_mwh = annual_gwh * 1000.0
    return {d: annual_mwh * w / denom for d, w in weights.items()}


def main() -> None:
    waitaki_gen, thermal_gen = load_generation()
    storage = load_waitaki_storage()
    month_index = solar_month_weights()
    dates = sorted(storage)
    if len(dates) != 366:
        raise RuntimeError(f"Expected 366 complete 2024 storage days, got {len(dates)}")

    empirical_ceiling_gwh = max(storage.values())
    empirical_floor_gwh = min(storage.values())

    daily_rows = []
    scenarios = {}

    for annual_gwh in SOLAR_SCENARIOS_GWH:
        solar = scenario_daily_solar(annual_gwh, dates, month_index)
        delta_storage_gwh = 0.0
        winter_entry_gwh = None
        max_delta_gwh = 0.0

        direct_thermal_displaced = 0.0
        hydro_avoided = 0.0
        reserve_hydro_released = 0.0
        curtailed = 0.0
        storage_limited = 0.0

        for d in dates:
            observed_storage = storage[d]
            observed_waitaki = waitaki_gen.get(d, 0.0)
            observed_thermal = thermal_gen.get(d, 0.0)
            extra_solar = solar[d]

            # First use solar directly against observed thermal generation.
            solar_to_thermal = min(extra_solar, observed_thermal)
            direct_thermal_displaced += solar_to_thermal
            solar_remaining = extra_solar - solar_to_thermal
            thermal_remaining = observed_thermal - solar_to_thermal

            # Surplus solar can avoid an observed Waitaki release and therefore
            # increase the counterfactual stored-energy state, subject to headroom.
            hydro_candidate = min(solar_remaining, observed_waitaki)
            headroom_mwh = max(
                0.0,
                (empirical_ceiling_gwh - observed_storage - delta_storage_gwh) * 1000.0,
            )
            hydro_stored = min(hydro_candidate, headroom_mwh)
            storage_limited_mwh = hydro_candidate - hydro_stored
            delta_storage_gwh += hydro_stored / 1000.0
            hydro_avoided += hydro_stored
            storage_limited += storage_limited_mwh
            solar_remaining -= hydro_stored

            # Before winter, retained hydro is reserved. From 1 May onward the
            # counterfactual reserve can be released to displace thermal that solar
            # has not already displaced. This is a policy rule, not perfect foresight.
            reserve_release_mwh = 0.0
            if d >= WINTER_START and thermal_remaining > 0.0 and delta_storage_gwh > 0.0:
                reserve_release_mwh = min(thermal_remaining, delta_storage_gwh * 1000.0)
                delta_storage_gwh -= reserve_release_mwh / 1000.0
                reserve_hydro_released += reserve_release_mwh
                thermal_remaining -= reserve_release_mwh

            curtailed_today = max(0.0, solar_remaining)
            curtailed += curtailed_today
            max_delta_gwh = max(max_delta_gwh, delta_storage_gwh)

            if d == WINTER_START:
                winter_entry_gwh = delta_storage_gwh

            daily_rows.append({
                "scenario_incremental_solar_gwh": annual_gwh,
                "date": d,
                "incremental_solar_mwh": round(extra_solar, 6),
                "observed_waitaki_generation_mwh": round(observed_waitaki, 6),
                "observed_thermal_generation_mwh": round(observed_thermal, 6),
                "observed_upper_waitaki_storage_gwh": round(observed_storage, 6),
                "direct_thermal_displacement_mwh": round(solar_to_thermal, 6),
                "hydro_avoided_and_stored_mwh": round(hydro_stored, 6),
                "reserve_hydro_released_to_displace_thermal_mwh": round(reserve_release_mwh, 6),
                "counterfactual_storage_delta_gwh": round(delta_storage_gwh, 6),
                "counterfactual_upper_waitaki_storage_gwh": round(observed_storage + delta_storage_gwh, 6),
                "storage_limited_hydro_displacement_mwh": round(storage_limited_mwh, 6),
                "curtailed_incremental_solar_mwh": round(curtailed_today, 6),
            })

        total_thermal_displaced = direct_thermal_displaced + reserve_hydro_released
        scenarios[str(annual_gwh)] = {
            "incremental_solar_gwh": annual_gwh,
            "winter_entry_storage_increase_gwh": round(winter_entry_gwh or 0.0, 3),
            "maximum_storage_increase_gwh": round(max_delta_gwh, 3),
            "year_end_storage_increase_gwh": round(delta_storage_gwh, 3),
            "waitaki_hydro_avoided_and_stored_gwh": round(hydro_avoided / 1000.0, 3),
            "direct_thermal_displacement_by_solar_gwh": round(direct_thermal_displaced / 1000.0, 3),
            "thermal_displacement_by_later_release_of_conserved_hydro_gwh": round(reserve_hydro_released / 1000.0, 3),
            "total_thermal_generation_displaced_gwh": round(total_thermal_displaced / 1000.0, 3),
            "storage_limited_hydro_displacement_gwh": round(storage_limited / 1000.0, 3),
            "incremental_solar_curtailed_gwh": round(curtailed / 1000.0, 3),
        }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(daily_rows[0].keys()))
        writer.writeheader()
        writer.writerows(daily_rows)

    summary = {
        "status": "dynamic_delta_dispatch_diagnostic",
        "purpose": "Track the counterfactual storage and thermal effect of incremental solar around observed 2024 Waitaki hydrology without reconstructing every absolute reservoir flow.",
        "method": {
            "baseline": "Observed 2024 upper-Waitaki storage trajectory, Waitaki generation and thermal generation.",
            "delta_state": "Only storage changes caused by incremental solar are simulated; observed hydrology remains the baseline state.",
            "dispatch_rule": [
                "Incremental solar first displaces observed thermal generation.",
                "Remaining solar can avoid observed Waitaki generation and add to counterfactual storage, subject to empirical storage headroom.",
                "Before 1 May, conserved hydro is held as winter reserve.",
                "From 1 May onward, the conserved reserve can be released to displace thermal generation remaining after direct solar displacement.",
                "Remaining solar after thermal displacement and feasible hydro conservation is curtailed."
            ],
            "winter_start": WINTER_START,
            "empirical_storage_floor_gwh": round(empirical_floor_gwh, 3),
            "empirical_storage_ceiling_gwh": round(empirical_ceiling_gwh, 3),
        },
        "scenarios": scenarios,
        "important_limitations": [
            "This is a dynamic counterfactual delta model, not yet a full physical Waitaki water-routing model.",
            "It does not infer absolute releases from inflow minus storage change.",
            "Observed 2024 storage already embodies natural inflows, tributaries, diversions, spill, environmental releases and operator behaviour; only the incremental solar-caused storage difference is modelled.",
            "The combined upper-Waitaki storage ceiling is empirical rather than an individual-lake legal/engineering limit.",
            "The winter-reserve rule is intentionally simple and should later be replaced by scheme-specific storage targets or optimization.",
            "The full dispatcher must enforce individual Tekapo/Pukaki/Ohau volumes, canal/station flow limits, bypasses, minimum flows and downstream cascade capacity."
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} ({len(daily_rows)} rows)")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
