from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import dispatch_waitaki_rolling_horizon_v1 as rolling
import dispatch_waitaki_source_route_v1 as base

OUT = Path("data/model/waitaki_rolling_horizon_grid.json")
HORIZONS = (30, 60, 90, 120)
WATER_VALUE_FACTORS = (1.0, 1.25, 1.5, 2.0, 3.0)


def run_case(horizon_days: int, water_value_factor: float):
    old_horizon = rolling.HORIZON_DAYS
    old_factor = rolling.TERMINAL_WATER_VALUE_FACTOR
    rolling.HORIZON_DAYS = horizon_days
    rolling.TERMINAL_WATER_VALUE_FACTOR = water_value_factor
    try:
        observed, _, _ = base.load_storage_history()
        inflows = base.load_inflows()
        dates, waitaki_obs, thermal_obs = base.load_dispatchable_requirement()
        route = base.route_inputs()
        terminal_targets = rolling.historical_targets_excluding_validation_year()
        requirement = {d: waitaki_obs.get(d, 0.0) + thermal_obs.get(d, 0.0) for d in dates}
        state = dict(observed[dates[0]])
        totals = defaultdict(float)
        errors = {s: [] for s in base.SOURCES}
        winter_entry = None

        for day_i, d in enumerate(dates):
            decision = rolling.solve_window(day_i, dates, state, inflows, requirement, route, terminal_targets)
            releases = decision["release"]
            state = dict(decision["storage"])
            hydro = sum(releases[s] * route["energy_mwh_per_mm3"][s] for s in base.SOURCES)
            totals["hydro"] += hydro
            totals["thermal"] += decision["thermal_mwh"]
            for s in base.SOURCES:
                errors[s].append(abs(state[s] - observed[d][s]))
            if d == base.WINTER_ENTRY:
                winter_entry = dict(state)

        winter_entry = winter_entry or dict(state)
        ranges = {s: max(1e-9, route["ceilings"][s] - route["floors"][s]) for s in base.SOURCES}
        observed_hydro_gwh = sum(waitaki_obs.values()) / 1000.0
        modeled_hydro_gwh = totals["hydro"] / 1000.0
        normalized_mae = sum((sum(errors[s]) / len(errors[s])) / ranges[s] for s in base.SOURCES) / len(base.SOURCES)
        hydro_error = abs(modeled_hydro_gwh - observed_hydro_gwh) / observed_hydro_gwh
        winter_error = sum(abs(winter_entry[s] - observed[base.WINTER_ENTRY][s]) / ranges[s] for s in base.SOURCES) / len(base.SOURCES)
        end_error = sum(abs(state[s] - observed[dates[-1]][s]) / ranges[s] for s in base.SOURCES) / len(base.SOURCES)
        score = normalized_mae + hydro_error + 0.5 * winter_error + 0.5 * end_error
        return {
            "horizon_days": horizon_days,
            "terminal_water_value_factor": water_value_factor,
            "modeled_waitaki_hydro_gwh": round(modeled_hydro_gwh, 3),
            "modeled_thermal_gwh": round(totals["thermal"] / 1000.0, 3),
            "winter_entry_storage_mm3": {s: round(winter_entry[s], 3) for s in base.SOURCES},
            "winter_entry_difference_vs_observed_mm3": {s: round(winter_entry[s] - observed[base.WINTER_ENTRY][s], 3) for s in base.SOURCES},
            "year_end_storage_mm3": {s: round(state[s], 3) for s in base.SOURCES},
            "year_end_difference_vs_observed_mm3": {s: round(state[s] - observed[dates[-1]][s], 3) for s in base.SOURCES},
            "storage_mae_mm3": {s: round(sum(errors[s]) / len(errors[s]), 3) for s in base.SOURCES},
            "normalized_mean_storage_mae": round(normalized_mae, 6),
            "waitaki_hydro_relative_error": round(hydro_error, 6),
            "normalized_winter_entry_error": round(winter_error, 6),
            "normalized_year_end_error": round(end_error, 6),
            "trajectory_validation_score": round(score, 6),
        }
    finally:
        rolling.HORIZON_DAYS = old_horizon
        rolling.TERMINAL_WATER_VALUE_FACTOR = old_factor


def main():
    cases = []
    for horizon in HORIZONS:
        for factor in WATER_VALUE_FACTORS:
            print(f"Testing horizon={horizon} days, water_value_factor={factor}")
            cases.append(run_case(horizon, factor))
    cases.sort(key=lambda r: r["trajectory_validation_score"])
    output = {
        "status": "waitaki_rolling_horizon_parameter_sensitivity",
        "purpose": "Determine whether a reasonable rolling-horizon terminal water value can reproduce the 2024 storage trajectory before adding explicit thermal fuel scarcity/cost dynamics.",
        "horizons_days": list(HORIZONS),
        "terminal_water_value_factors": list(WATER_VALUE_FACTORS),
        "case_count": len(cases),
        "best_case": cases[0],
        "top_five": cases[:5],
        "all_cases": cases,
        "important_note": "This is a diagnostic parameter sweep, not final calibration. If good storage tracking requires extreme water-value factors or remains poor across the grid, the next model should add explicit thermal fuel scarcity/cost rather than further tune terminal penalties."
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}; best score {cases[0]['trajectory_validation_score']}")


if __name__ == "__main__":
    main()
