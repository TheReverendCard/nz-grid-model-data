from __future__ import annotations

import itertools
import json
from pathlib import Path

import dispatch_waitaki_source_route_v1 as base
import diagnose_waitaki_storage_guard as guards

OUT = Path("data/model/waitaki_lake_guard_grid.json")
LEVELS = ("q25", "q50", "q75")


def main() -> None:
    observed, _, _ = base.load_storage_history()
    target_sets, medians = guards.load_targets()
    inflows = base.load_inflows()
    dates, waitaki_obs, thermal_obs = base.load_dispatchable_requirement()
    route = base.route_inputs()
    month_index = base.solar_weights()
    solar = base.make_daily_solar(0.0, dates, month_index)

    observed_waitaki_gwh = sum(waitaki_obs.values()) / 1000.0
    ranges = {
        s: max(1e-9, route["ceilings"][s] - route["floors"][s])
        for s in base.SOURCES
    }

    results = {}
    for combo in itertools.product(LEVELS, repeat=len(base.SOURCES)):
        choice = dict(zip(base.SOURCES, combo))
        target = {
            site: target_sets[choice[site]][site]
            for site in base.SOURCES
        }
        _, summary = base.dispatch_one(
            0.0,
            dates,
            inflows,
            observed,
            target,
            medians,
            waitaki_obs,
            thermal_obs,
            route,
            solar,
        )

        normalized_mae = sum(
            summary["storage_mae_vs_observed_mm3"][s] / ranges[s]
            for s in base.SOURCES
        ) / len(base.SOURCES)
        hydro_error = abs(summary["modeled_waitaki_hydro_gwh"] - observed_waitaki_gwh) / observed_waitaki_gwh
        winter_error = sum(
            abs(summary["winter_entry_storage_difference_vs_observed_mm3"][s]) / ranges[s]
            for s in base.SOURCES
        ) / len(base.SOURCES)
        year_end_error = sum(
            abs(summary["year_end_storage_difference_vs_observed_mm3"][s]) / ranges[s]
            for s in base.SOURCES
        ) / len(base.SOURCES)

        simple_score = normalized_mae + hydro_error
        trajectory_score = normalized_mae + hydro_error + 0.5 * winter_error + 0.5 * year_end_error
        key = ",".join(f"{s}={choice[s]}" for s in base.SOURCES)
        results[key] = {
            "guard_by_lake": choice,
            **summary,
            "normalized_mean_storage_mae": round(normalized_mae, 6),
            "waitaki_hydro_relative_error": round(hydro_error, 6),
            "normalized_winter_entry_error": round(winter_error, 6),
            "normalized_year_end_error": round(year_end_error, 6),
            "simple_validation_score": round(simple_score, 6),
            "trajectory_validation_score": round(trajectory_score, 6),
        }

    ranked_simple = sorted(results, key=lambda k: results[k]["simple_validation_score"])
    ranked_trajectory = sorted(results, key=lambda k: results[k]["trajectory_validation_score"])

    output = {
        "status": "waitaki_lake_specific_guard_grid",
        "purpose": "Test whether separate historical storage guards for Tekapo, Pukaki and Ohau materially improve 2024 routed-dispatch validation before moving to a rolling-horizon water-value rule.",
        "zero_solar_only": True,
        "levels": list(LEVELS),
        "combination_count": len(results),
        "ranking_by_simple_validation_score": ranked_simple,
        "ranking_by_trajectory_validation_score": ranked_trajectory,
        "top_five_by_trajectory_score": [
            {"policy": key, **results[key]} for key in ranked_trajectory[:5]
        ],
        "score_definition": {
            "simple": "Mean per-lake storage MAE normalized by empirical range plus absolute annual Waitaki hydro relative error.",
            "trajectory": "Simple score plus half-weighted normalized 1 May storage error and half-weighted normalized year-end storage error."
        },
        "important_note": "This is a diagnostic grid, not an automatic calibration. If no lake-specific fixed guard reproduces both seasonal trajectory and annual energy, the next step should be a rolling-horizon or water-value dispatch rule rather than further percentile tuning.",
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}; best trajectory policy: {ranked_trajectory[0]}")


if __name__ == "__main__":
    main()
