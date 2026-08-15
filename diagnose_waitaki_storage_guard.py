from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

import dispatch_waitaki_source_route_v1 as base

OUT = Path("data/model/waitaki_storage_guard_sensitivity.json")


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    if not values:
        raise RuntimeError("Cannot calculate percentile from no values")
    if len(values) == 1:
        return values[0]
    pos = p * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return values[lo] * (1.0 - frac) + values[hi] * frac


def load_targets():
    climatology = {s: defaultdict(list) for s in base.SOURCES}
    with base.STORAGE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            site = row["site_code"]
            if site not in base.SOURCES or row["total_storage_mm3"] == "":
                continue
            climatology[site][row["date"][5:]].append(float(row["total_storage_mm3"]))

    target_sets = {}
    for label, p in (("q25", 0.25), ("q50", 0.50), ("q75", 0.75)):
        target_sets[label] = {
            site: {md: percentile(vals, p) for md, vals in climatology[site].items()}
            for site in base.SOURCES
        }
    medians = {
        site: {md: median(vals) for md, vals in climatology[site].items()}
        for site in base.SOURCES
    }
    return target_sets, medians


def main() -> None:
    observed, _, _ = base.load_storage_history()
    targets, medians = load_targets()
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

    policies = {}
    for label, target in targets.items():
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
        normalized_storage_mae = sum(
            summary["storage_mae_vs_observed_mm3"][s] / ranges[s]
            for s in base.SOURCES
        ) / len(base.SOURCES)
        hydro_relative_error = abs(summary["modeled_waitaki_hydro_gwh"] - observed_waitaki_gwh) / observed_waitaki_gwh
        score = normalized_storage_mae + hydro_relative_error
        policies[label] = {
            **summary,
            "normalized_mean_storage_mae": round(normalized_storage_mae, 6),
            "waitaki_hydro_relative_error": round(hydro_relative_error, 6),
            "simple_validation_score": round(score, 6),
        }

    ranked = sorted(policies, key=lambda k: policies[k]["simple_validation_score"])
    result = {
        "status": "waitaki_storage_guard_sensitivity",
        "purpose": "Test transparent historical seasonal storage guard percentiles before changing the routed dispatcher policy.",
        "zero_solar_only": True,
        "guards": policies,
        "ranking_by_simple_validation_score": ranked,
        "score_definition": "Mean per-lake storage MAE normalized by empirical storage range, plus absolute annual Waitaki hydro relative error.",
        "important_note": "The ranking is diagnostic, not an automatic calibration choice. A better annual score can still hide poor winter-entry or terminal-storage behaviour.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}; ranking: {' > '.join(ranked)}")


if __name__ == "__main__":
    main()
