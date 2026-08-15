from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

import dispatch_waitaki_source_route_v1 as base
import dispatch_waitaki_rolling_horizon_v1 as rolling

OUT_DAILY = Path("data/model/waitaki_thermal_scarcity_v1_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_thermal_scarcity_v1_summary.json")

YEAR = 2024
GAS_HEAT_RATE_GJ_PER_MWH = 7.2
GAS_START_PJ = 5.0
GAS_MAX_PJ = 7.0
GAS_STORAGE_DELIVERABILITY_TJ_DAY = 65.0
THERMAL_COST = {"Gas": 1.0, "Coal": 1.35, "Diesel": 3.0}
SHORTAGE_COST = 100.0
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
    thermal_by_fuel = load_observed_thermal_by_fuel()

    n = len(dates)
    sites = list(base.SOURCES)
    ns = len(sites)
    fuels = list(THERMAL_COST)
    nf = len(fuels)
    energy = route["energy_mwh_per_mm3"]
    ceilings = route["ceilings"]
    floors = route["floors"]
    source_caps = {
        "TKA": route["tka_source_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
        "PKI": route["pki_source_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
        "OHA": route["common_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS,
    }
    common_cap = route["common_cap_m3s"] * base.MM3_PER_DAY_PER_CUMECS

    observed_thermal_total = {f: sum(thermal_by_fuel[f].values()) for f in fuels}
    daily_caps = {
        f: max(thermal_by_fuel[f].values() or [0.0])
        for f in fuels
    }

    # Flat gas supply is calibrated only to the observed annual electricity-gas budget.
    # Storage starts and finishes at 5 PJ so the annual gas-energy budget remains neutral.
    gas_total_tj = observed_thermal_total["Gas"] * GAS_HEAT_RATE_GJ_PER_MWH / 1000.0
    flat_gas_supply_tj_day = gas_total_tj / n

    # Variables by day: release[3], spill[3], storage[3], thermal[3], gas_stock_tj,
    # unused_gas_supply_tj, shortage_mwh. Final 3 vars are hydro terminal shortfall.
    block = ns * 3 + nf + 3
    terminal_offset = n * block
    total_vars = terminal_offset + ns

    def idx(t, kind, i=0):
        # 0 rel, 1 spill, 2 storage, 3 thermal fuel, 4 gas stock, 5 unused gas, 6 shortage
        if kind <= 2:
            return t * block + kind * ns + i
        if kind == 3:
            return t * block + ns * 3 + i
        if kind == 4:
            return t * block + ns * 3 + nf
        if kind == 5:
            return t * block + ns * 3 + nf + 1
        return t * block + ns * 3 + nf + 2

    c = np.zeros(total_vars)
    bounds = [(0.0, None)] * total_vars
    for t in range(n):
        for i, s in enumerate(sites):
            bounds[idx(t, 0, i)] = (0.0, source_caps[s])
            bounds[idx(t, 1, i)] = (0.0, None)
            bounds[idx(t, 2, i)] = (floors[s], ceilings[s])
        for j, f in enumerate(fuels):
            bounds[idx(t, 3, j)] = (0.0, daily_caps[f])
            c[idx(t, 3, j)] = THERMAL_COST[f]
        bounds[idx(t, 4)] = (0.0, GAS_MAX_PJ * 1000.0)
        bounds[idx(t, 5)] = (0.0, flat_gas_supply_tj_day)
        bounds[idx(t, 6)] = (0.0, None)
        c[idx(t, 6)] = SHORTAGE_COST

    for i, s in enumerate(sites):
        bounds[terminal_offset + i] = (0.0, None)
        c[terminal_offset + i] = energy[s] * TERMINAL_WATER_VALUE_FACTOR

    A_eq, b_eq = [], []
    A_ub, b_ub = [], []

    for t, d in enumerate(dates):
        # Hydro storage balances.
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
            A_eq.append(row); b_eq.append(rhs)

        # Gas stock: stock_t = stock_prev + flat supply - unused supply - gas fuel burned.
        row = np.zeros(total_vars)
        row[idx(t, 4)] = 1.0
        row[idx(t, 5)] = 1.0
        gas_j = fuels.index("Gas")
        row[idx(t, 3, gas_j)] = GAS_HEAT_RATE_GJ_PER_MWH / 1000.0
        if t > 0:
            row[idx(t - 1, 4)] = -1.0
            rhs = flat_gas_supply_tj_day
        else:
            rhs = GAS_START_PJ * 1000.0 + flat_gas_supply_tj_day
        A_eq.append(row); b_eq.append(rhs)

        # Demand balance. Shortage is allowed only at a very high penalty.
        row = np.zeros(total_vars)
        for i, s in enumerate(sites):
            row[idx(t, 0, i)] = energy[s]
        for j in range(nf):
            row[idx(t, 3, j)] = 1.0
        row[idx(t, 6)] = 1.0
        A_eq.append(row)
        b_eq.append(waitaki_obs.get(d, 0.0) + thermal_obs.get(d, 0.0))

        # Shared upper-Waitaki hydraulic path.
        row = np.zeros(total_vars)
        for i in range(ns): row[idx(t, 0, i)] = 1.0
        A_ub.append(row); b_ub.append(common_cap)

        # Gas generation above contemporaneous flat supply can only come from storage,
        # and storage withdrawal is capped at Concept's 65 TJ/day base-case deliverability.
        row = np.zeros(total_vars)
        row[idx(t, 3, gas_j)] = GAS_HEAT_RATE_GJ_PER_MWH / 1000.0
        A_ub.append(row)
        b_ub.append(flat_gas_supply_tj_day + GAS_STORAGE_DELIVERABILITY_TJ_DAY)

    # Keep coal and diesel within their observed 2024 annual revealed availability envelope.
    for f in ("Coal", "Diesel"):
        row = np.zeros(total_vars)
        j = fuels.index(f)
        for t in range(n): row[idx(t, 3, j)] = 1.0
        A_ub.append(row); b_ub.append(observed_thermal_total[f])

    # End gas storage at the initial 5 PJ; this prevents using the initial stock as free annual fuel.
    row = np.zeros(total_vars); row[idx(n - 1, 4)] = 1.0
    A_eq.append(row); b_eq.append(GAS_START_PJ * 1000.0)

    # Soft climatological hydro terminal reserve, excluding 2024 from its construction.
    end_md = dates[-1][5:]
    for i, s in enumerate(sites):
        target = max(floors[s], terminal_targets[s][end_md])
        row = np.zeros(total_vars)
        row[idx(n - 1, 2, i)] = -1.0
        row[terminal_offset + i] = -1.0
        A_ub.append(row); b_ub.append(-target)

    result = linprog(c, A_ub=np.asarray(A_ub), b_ub=np.asarray(b_ub),
                     A_eq=np.asarray(A_eq), b_eq=np.asarray(b_eq), bounds=bounds,
                     method="highs")
    if not result.success:
        raise RuntimeError(result.message)

    x = result.x
    rows = []
    hydro_total = 0.0
    thermal_total = defaultdict(float)
    shortage_total = 0.0
    errors = {s: [] for s in sites}
    winter = None
    for t, d in enumerate(dates):
        release = {s: x[idx(t, 0, i)] for i, s in enumerate(sites)}
        storage = {s: x[idx(t, 2, i)] for i, s in enumerate(sites)}
        hydro = sum(release[s] * energy[s] for s in sites)
        hydro_total += hydro
        thermal = {f: x[idx(t, 3, j)] for j, f in enumerate(fuels)}
        for f, v in thermal.items(): thermal_total[f] += v
        shortage = x[idx(t, 6)]
        shortage_total += shortage
        for s in sites: errors[s].append(abs(storage[s] - observed[d][s]))
        if d == base.WINTER_ENTRY: winter = dict(storage)
        row = {"date": d, "modeled_waitaki_hydro_mwh": round(hydro, 6),
               "gas_mwh": round(thermal["Gas"], 6), "coal_mwh": round(thermal["Coal"], 6),
               "diesel_mwh": round(thermal["Diesel"], 6), "shortage_mwh": round(shortage, 6),
               "gas_stock_pj": round(x[idx(t, 4)] / 1000.0, 6)}
        for s in sites:
            row[f"{s}_storage_mm3"] = round(storage[s], 6)
            row[f"{s}_observed_storage_mm3"] = round(observed[d][s], 6)
        rows.append(row)

    winter = winter or {s: x[idx(n - 1, 2, i)] for i, s in enumerate(sites)}
    end_state = {s: x[idx(n - 1, 2, i)] for i, s in enumerate(sites)}
    summary = {
        "status": "waitaki_thermal_scarcity_v1_validation",
        "purpose": "Test whether finite thermal availability and gas storage create a more realistic intertemporal hydro water value before building a full price model.",
        "thermal_envelope": {
            "observed_2024_generation_budget_gwh": {f: round(observed_thermal_total[f] / 1000.0, 3) for f in fuels},
            "daily_generation_caps_mwh": {f: round(daily_caps[f], 3) for f in fuels},
            "relative_dispatch_cost": THERMAL_COST,
            "gas_heat_rate_gj_per_mwh": GAS_HEAT_RATE_GJ_PER_MWH,
            "gas_start_and_terminal_storage_pj": GAS_START_PJ,
            "gas_max_storage_pj": GAS_MAX_PJ,
            "gas_storage_deliverability_tj_day": GAS_STORAGE_DELIVERABILITY_TJ_DAY,
            "flat_gas_supply_tj_day": round(flat_gas_supply_tj_day, 3),
        },
        "modeled_waitaki_hydro_gwh": round(hydro_total / 1000.0, 3),
        "observed_waitaki_hydro_gwh": round(sum(waitaki_obs.values()) / 1000.0, 3),
        "modeled_thermal_gwh": {f: round(thermal_total[f] / 1000.0, 3) for f in fuels},
        "shortage_gwh": round(shortage_total / 1000.0, 6),
        "winter_entry_storage_mm3": {s: round(winter[s], 3) for s in sites},
        "winter_entry_difference_vs_observed_mm3": {s: round(winter[s] - observed[base.WINTER_ENTRY][s], 3) for s in sites},
        "year_end_storage_mm3": {s: round(end_state[s], 3) for s in sites},
        "year_end_difference_vs_observed_mm3": {s: round(end_state[s] - observed[dates[-1]][s], 3) for s in sites},
        "storage_mae_mm3": {s: round(sum(errors[s]) / len(errors[s]), 3) for s in sites},
        "important_limitations": [
            "Observed 2024 thermal generation totals are used as revealed fuel-availability envelopes for validation; they are not future assumptions.",
            "Gas supply is represented as a flat electricity-sector-equivalent flow calibrated to observed gas generation, not a full NZ gas market balance.",
            "Coal and diesel are finite annual electrical-energy budgets rather than physical stockpile models in this version.",
            "Hydro routing remains the simplified three-source upper-Waitaki abstraction used by the preceding diagnostics."
        ]
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
