from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

import dispatch_waitaki_source_route_v1 as base
import dispatch_waitaki_rolling_horizon_v1 as rolling

OUT_DAILY = Path("data/model/waitaki_thermal_timing_diagnostic_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_thermal_timing_diagnostic.json")
YEAR = 2024
THERMAL_COST = {"Gas": 1.0, "Coal": 1.35, "Diesel": 3.0}
SHORTAGE_COST = 1000.0
TERMINAL_WATER_VALUE_FACTOR = 1.25


def load_observed_thermal_by_fuel():
    daily = {f: defaultdict(float) for f in THERMAL_COST}
    with base.GENERATION.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            d = row["date"]
            if not d.startswith(f"{YEAR}-"):
                continue
            fuel = row["fuel_code"]
            if fuel in daily:
                daily[fuel][d] += float(row["generation_mwh"])
    return {f: dict(v) for f, v in daily.items()}


def main():
    observed, _, _ = base.load_storage_history()
    inflows = base.load_inflows()
    dates, waitaki_obs, thermal_obs = base.load_dispatchable_requirement()
    route = base.route_inputs()
    terminal_targets = rolling.historical_targets_excluding_validation_year()
    thermal_daily = load_observed_thermal_by_fuel()

    sites = list(base.SOURCES)
    fuels = list(THERMAL_COST)
    ns = len(sites)
    nf = len(fuels)
    n = len(dates)
    energy = route["energy_mwh_per_mm3"]
    ceilings = route["ceilings"]
    floors = route["floors"]
    source_caps = {
        "TKA": route["tka_source_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
        "PKI": route["pki_source_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
        "OHA": route["common_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
    }
    common_cap = route["common_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS
    daily_caps = {f: max(thermal_daily[f].values() or [0.0]) for f in fuels}

    monthly_budget = {f: defaultdict(float) for f in fuels}
    for f in fuels:
        for d, value in thermal_daily[f].items():
            monthly_budget[f][d[:7]] += value

    # Per day: release[3], spill[3], storage[3], thermal[3], shortage.
    block = ns * 3 + nf + 1
    terminal_offset = n * block
    total_vars = terminal_offset + ns

    def idx(t: int, kind: int, i: int = 0) -> int:
        if kind <= 2:
            return t * block + kind * ns + i
        if kind == 3:
            return t * block + ns * 3 + i
        return t * block + ns * 3 + nf

    c = np.zeros(total_vars)
    bounds = [(0.0, None)] * total_vars
    for t in range(n):
        for i, s in enumerate(sites):
            bounds[idx(t, 0, i)] = (0.0, source_caps[s])
            bounds[idx(t, 1, i)] = (0.0, None)
            bounds[idx(t, 2, i)] = (floors[s], ceilings[s])
        for j, f in enumerate(fuels):
            bounds[idx(t, 3, j)] = (0.0, daily_caps[f])
            # Tiny cost only breaks within-month degeneracy; monthly totals are fixed below.
            c[idx(t, 3, j)] = THERMAL_COST[f] * 1e-6
        bounds[idx(t, 4)] = (0.0, None)
        c[idx(t, 4)] = SHORTAGE_COST

    for i, s in enumerate(sites):
        bounds[terminal_offset + i] = (0.0, None)
        c[terminal_offset + i] = energy[s] * TERMINAL_WATER_VALUE_FACTOR

    A_eq, b_eq, A_ub, b_ub = [], [], [], []

    for t, d in enumerate(dates):
        for i, s in enumerate(sites):
            row = np.zeros(total_vars)
            row[idx(t, 2, i)] = 1.0
            row[idx(t, 0, i)] = 1.0
            row[idx(t, 1, i)] = 1.0
            if t > 0:
                row[idx(t - 1, 2, i)] = -1.0
                rhs = inflows[d][s]
            else:
                rhs = observed[dates[0]][s] + inflows[d][s]
            A_eq.append(row)
            b_eq.append(rhs)

        # Daily system balance for the controlled Waitaki + thermal slice.
        row = np.zeros(total_vars)
        for i, s in enumerate(sites):
            row[idx(t, 0, i)] = energy[s]
        for j in range(nf):
            row[idx(t, 3, j)] = 1.0
        row[idx(t, 4)] = 1.0
        A_eq.append(row)
        b_eq.append(waitaki_obs.get(d, 0.0) + thermal_obs.get(d, 0.0))

        row = np.zeros(total_vars)
        for i in range(ns):
            row[idx(t, 0, i)] = 1.0
        A_ub.append(row)
        b_ub.append(common_cap)

    # Validation-only revealed timing constraint: reproduce each fuel's observed
    # monthly 2024 electrical energy, while allowing redistribution within month.
    months = sorted({d[:7] for d in dates})
    for f_i, f in enumerate(fuels):
        for month in months:
            row = np.zeros(total_vars)
            for t, d in enumerate(dates):
                if d.startswith(month):
                    row[idx(t, 3, f_i)] = 1.0
            A_eq.append(row)
            b_eq.append(monthly_budget[f][month])

    end_md = dates[-1][5:]
    for i, s in enumerate(sites):
        target = max(floors[s], terminal_targets[s][end_md])
        row = np.zeros(total_vars)
        row[idx(n - 1, 2, i)] = -1.0
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
        raise RuntimeError(result.message)

    x = result.x
    rows = []
    hydro_total = 0.0
    shortage_total = 0.0
    errors = {s: [] for s in sites}
    winter = None
    thermal_totals = defaultdict(float)

    for t, d in enumerate(dates):
        release = {s: x[idx(t, 0, i)] for i, s in enumerate(sites)}
        storage = {s: x[idx(t, 2, i)] for i, s in enumerate(sites)}
        hydro = sum(release[s] * energy[s] for s in sites)
        thermal = {f: x[idx(t, 3, j)] for j, f in enumerate(fuels)}
        shortage = x[idx(t, 4)]
        hydro_total += hydro
        shortage_total += shortage
        for f, value in thermal.items():
            thermal_totals[f] += value
        for s in sites:
            errors[s].append(abs(storage[s] - observed[d][s]))
        if d == base.WINTER_ENTRY:
            winter = dict(storage)

        row = {
            "date": d,
            "modeled_waitaki_hydro_mwh": round(hydro, 6),
            "observed_waitaki_hydro_mwh": round(waitaki_obs.get(d, 0.0), 6),
            "gas_mwh": round(thermal["Gas"], 6),
            "coal_mwh": round(thermal["Coal"], 6),
            "diesel_mwh": round(thermal["Diesel"], 6),
            "shortage_mwh": round(shortage, 6),
        }
        for s in sites:
            row[f"{s}_storage_mm3"] = round(storage[s], 6)
            row[f"{s}_observed_storage_mm3"] = round(observed[d][s], 6)
        rows.append(row)

    winter = winter or {s: x[idx(n - 1, 2, i)] for i, s in enumerate(sites)}
    end_state = {s: x[idx(n - 1, 2, i)] for i, s in enumerate(sites)}
    summary = {
        "status": "waitaki_revealed_monthly_thermal_timing_diagnostic",
        "purpose": "Test whether getting the seasonal timing of 2024 thermal availability right is sufficient for the simplified Waitaki hydrology to reproduce the observed storage trajectory.",
        "validation_only": True,
        "thermal_timing_constraint": "Each fuel's observed 2024 monthly electrical generation total is imposed exactly; daily timing within the month remains optimized.",
        "modeled_waitaki_hydro_gwh": round(hydro_total / 1000.0, 3),
        "observed_waitaki_hydro_gwh": round(sum(waitaki_obs.values()) / 1000.0, 3),
        "modeled_thermal_gwh": {f: round(thermal_totals[f] / 1000.0, 3) for f in fuels},
        "shortage_gwh": round(shortage_total / 1000.0, 6),
        "winter_entry_storage_mm3": {s: round(winter[s], 3) for s in sites},
        "winter_entry_difference_vs_observed_mm3": {
            s: round(winter[s] - observed[base.WINTER_ENTRY][s], 3) for s in sites
        },
        "year_end_storage_mm3": {s: round(end_state[s], 3) for s in sites},
        "year_end_difference_vs_observed_mm3": {
            s: round(end_state[s] - observed[dates[-1]][s], 3) for s in sites
        },
        "storage_mae_mm3": {
            s: round(sum(errors[s]) / len(errors[s]), 3) for s in sites
        },
        "interpretation_rule": "If storage tracking remains poor even with revealed monthly thermal timing, the dominant remaining error is in hydro source allocation/routing/storage physics rather than thermal seasonal timing.",
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
