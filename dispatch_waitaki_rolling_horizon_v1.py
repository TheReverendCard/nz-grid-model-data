from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

import dispatch_waitaki_source_route_v1 as base
import diagnose_waitaki_storage_guard as guards

OUT_DAILY = Path("data/model/waitaki_rolling_horizon_v1_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_rolling_horizon_v1_summary.json")

HORIZON_DAYS = 60
TERMINAL_TARGET_PERCENTILE = 0.50
TERMINAL_WATER_VALUE_FACTOR = 1.0
YEAR = 2024


def historical_targets_excluding_validation_year() -> dict[str, dict[str, float]]:
    climatology = {s: defaultdict(list) for s in base.SOURCES}
    with base.STORAGE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            site = row["site_code"]
            if site not in base.SOURCES or row["total_storage_mm3"] == "":
                continue
            if row["date"].startswith(f"{YEAR}-"):
                continue
            climatology[site][row["date"][5:]].append(float(row["total_storage_mm3"]))
    return {
        site: {
            md: guards.percentile(values, TERMINAL_TARGET_PERCENTILE)
            for md, values in by_md.items()
        }
        for site, by_md in climatology.items()
    }


def solve_window(
    start_index: int,
    dates: list[str],
    state0: dict[str, float],
    inflows,
    requirement,
    route,
    terminal_targets,
):
    window = dates[start_index : min(len(dates), start_index + HORIZON_DAYS)]
    n = len(window)
    sites = list(base.SOURCES)
    ns = len(sites)
    energy = route["energy_mwh_per_mm3"]
    ceilings = route["ceilings"]
    floors = route["floors"]
    source_caps = {
        "TKA": route["tka_source_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
        "PKI": route["pki_source_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
        "OHA": route["common_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
    }
    common_cap = route["common_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS

    # Per day: release[site], spill[site], storage[site], thermal.
    block = ns * 3 + 1
    terminal_offset = n * block
    total_vars = terminal_offset + ns

    def idx(day: int, kind: int, site_i: int = 0) -> int:
        # kind: 0 release, 1 spill, 2 storage, 3 thermal
        if kind == 3:
            return day * block + ns * 3
        return day * block + kind * ns + site_i

    c = np.zeros(total_vars)
    bounds: list[tuple[float | None, float | None]] = [(0.0, None)] * total_vars
    for t in range(n):
        for i, s in enumerate(sites):
            bounds[idx(t, 0, i)] = (0.0, source_caps[s])
            bounds[idx(t, 1, i)] = (0.0, None)
            bounds[idx(t, 2, i)] = (floors[s], ceilings[s])
        c[idx(t, 3)] = 1.0
        bounds[idx(t, 3)] = (0.0, None)
    for i, s in enumerate(sites):
        c[terminal_offset + i] = energy[s] * TERMINAL_WATER_VALUE_FACTOR
        bounds[terminal_offset + i] = (0.0, None)

    A_eq = []
    b_eq = []
    for t, d in enumerate(window):
        for i, s in enumerate(sites):
            row = np.zeros(total_vars)
            row[idx(t, 2, i)] = 1.0
            row[idx(t, 0, i)] = 1.0
            row[idx(t, 1, i)] = 1.0
            if t > 0:
                row[idx(t - 1, 2, i)] = -1.0
                rhs = inflows[d][s]
            else:
                rhs = state0[s] + inflows[d][s]
            A_eq.append(row)
            b_eq.append(rhs)

    A_ub = []
    b_ub = []
    for t, d in enumerate(window):
        # Generation + thermal must cover the dispatchable requirement.
        row = np.zeros(total_vars)
        for i, s in enumerate(sites):
            row[idx(t, 0, i)] = -energy[s]
        row[idx(t, 3)] = -1.0
        A_ub.append(row)
        b_ub.append(-requirement[d])

        # Common upper-Waitaki hydraulic route limit.
        row = np.zeros(total_vars)
        for i in range(ns):
            row[idx(t, 0, i)] = 1.0
        A_ub.append(row)
        b_ub.append(common_cap)

    # Soft end-of-window seasonal reserve. Slack is priced at the downstream
    # energy value of the missing water rather than imposed as a hard guard.
    end_day = n - 1
    end_md = window[-1][5:]
    for i, s in enumerate(sites):
        target = max(floors[s], terminal_targets[s][end_md])
        row = np.zeros(total_vars)
        row[idx(end_day, 2, i)] = -1.0
        row[terminal_offset + i] = -1.0
        A_ub.append(row)
        b_ub.append(-target)

    result = linprog(
        c,
        A_ub=np.asarray(A_ub),
        b_ub=np.asarray(b_ub),
        A_eq=np.asarray(A_eq),
        b_eq=np.asarray(b_eq),
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"Rolling-horizon LP failed on {window[0]}: {result.message}")

    x = result.x
    first = {
        "release": {s: x[idx(0, 0, i)] for i, s in enumerate(sites)},
        "spill": {s: x[idx(0, 1, i)] for i, s in enumerate(sites)},
        "storage": {s: x[idx(0, 2, i)] for i, s in enumerate(sites)},
        "thermal_mwh": x[idx(0, 3)],
        "objective": result.fun,
        "window_days": n,
    }
    return first


def main() -> None:
    observed, _, _ = base.load_storage_history()
    inflows = base.load_inflows()
    dates, waitaki_obs, thermal_obs = base.load_dispatchable_requirement()
    route = base.route_inputs()
    terminal_targets = historical_targets_excluding_validation_year()

    requirement = {
        d: waitaki_obs.get(d, 0.0) + thermal_obs.get(d, 0.0)
        for d in dates
    }
    state = dict(observed[dates[0]])
    rows = []
    totals = defaultdict(float)
    errors = {s: [] for s in base.SOURCES}
    winter_entry = None

    for day_i, d in enumerate(dates):
        decision = solve_window(
            day_i, dates, state, inflows, requirement, route, terminal_targets
        )
        releases = decision["release"]
        spills = decision["spill"]
        state = dict(decision["storage"])
        hydro_mwh = sum(
            releases[s] * route["energy_mwh_per_mm3"][s]
            for s in base.SOURCES
        )
        thermal_mwh = decision["thermal_mwh"]
        totals["hydro"] += hydro_mwh
        totals["thermal"] += thermal_mwh
        totals["spill"] += sum(spills.values())
        for s in base.SOURCES:
            totals[f"release_{s}"] += releases[s]
            errors[s].append(abs(state[s] - observed[d][s]))
        if d == base.WINTER_ENTRY:
            winter_entry = dict(state)

        row = {
            "date": d,
            "dispatchable_requirement_mwh": round(requirement[d], 6),
            "modeled_waitaki_hydro_mwh": round(hydro_mwh, 6),
            "modeled_thermal_backstop_mwh": round(thermal_mwh, 6),
            "observed_waitaki_hydro_mwh": round(waitaki_obs.get(d, 0.0), 6),
            "observed_thermal_mwh": round(thermal_obs.get(d, 0.0), 6),
            "rolling_window_days": decision["window_days"],
        }
        for s in base.SOURCES:
            row[f"{s}_natural_inflow_mm3"] = round(inflows[d][s], 6)
            row[f"{s}_release_mm3"] = round(releases[s], 6)
            row[f"{s}_spill_mm3"] = round(spills[s], 6)
            row[f"{s}_storage_mm3"] = round(state[s], 6)
            row[f"{s}_observed_storage_mm3"] = round(observed[d][s], 6)
        rows.append(row)

    observed_waitaki_gwh = sum(waitaki_obs.values()) / 1000.0
    modeled_waitaki_gwh = totals["hydro"] / 1000.0
    observed_end = observed[dates[-1]]
    winter_entry = winter_entry or dict(state)
    summary = {
        "status": "waitaki_rolling_horizon_v1_validation",
        "year": YEAR,
        "purpose": "Test a receding-horizon water-value dispatcher before using it for future solar counterfactuals.",
        "method": {
            "horizon_days": HORIZON_DAYS,
            "forecast_information": "Perfect knowledge of historical inflows and dispatchable requirement only within the rolling horizon.",
            "terminal_storage_target": "Historical day-of-year median storage excluding the 2024 validation year.",
            "terminal_target_type": "Soft target; shortfall is allowed at a penalty rather than enforced as a hard minimum.",
            "terminal_water_value_factor": TERMINAL_WATER_VALUE_FACTOR,
            "thermal_cost_units": "1 objective unit per MWh thermal",
            "terminal_shortfall_cost": "Source downstream MWh-per-Mm3 multiplied by terminal_water_value_factor per missing Mm3.",
            "daily_storage_guard": "None. Storage decisions are made by the rolling LP subject to reservoir floors/ceilings and route capacities."
        },
        "observed_waitaki_generation_gwh": round(observed_waitaki_gwh, 3),
        "modeled_waitaki_generation_gwh": round(modeled_waitaki_gwh, 3),
        "waitaki_generation_relative_error": round((modeled_waitaki_gwh - observed_waitaki_gwh) / observed_waitaki_gwh, 6),
        "observed_thermal_generation_gwh": round(sum(thermal_obs.values()) / 1000.0, 3),
        "modeled_thermal_backstop_gwh": round(totals["thermal"] / 1000.0, 3),
        "forced_or_optimized_spill_mm3": round(totals["spill"], 3),
        "winter_entry_storage_mm3": {s: round(winter_entry[s], 3) for s in base.SOURCES},
        "winter_entry_storage_difference_vs_observed_mm3": {
            s: round(winter_entry[s] - observed[base.WINTER_ENTRY][s], 3)
            for s in base.SOURCES
        },
        "year_end_storage_mm3": {s: round(state[s], 3) for s in base.SOURCES},
        "year_end_storage_difference_vs_observed_mm3": {
            s: round(state[s] - observed_end[s], 3) for s in base.SOURCES
        },
        "storage_mae_vs_observed_mm3": {
            s: round(sum(errors[s]) / len(errors[s]), 3) for s in base.SOURCES
        },
        "source_release_mm3": {
            s: round(totals[f"release_{s}"], 3) for s in base.SOURCES
        },
        "important_limitations": [
            "This is still a simplified three-source upper-Waitaki routing model; Ruataniwha, Benmore and Aviemore are not independent storage decision nodes.",
            "The LP sees perfect inflow and load information inside each rolling horizon, so it is an optimistic operational benchmark rather than a reconstruction of operator behaviour.",
            "The terminal reserve target is historical climatology, not a calibrated market water-value curve.",
            "Travel time, intra-day dispatch and lower-catchment tributary additions are still omitted."
        ]
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")
    print(f"Waitaki generation: observed {observed_waitaki_gwh:.3f} GWh; modeled {modeled_waitaki_gwh:.3f} GWh")


if __name__ == "__main__":
    main()
