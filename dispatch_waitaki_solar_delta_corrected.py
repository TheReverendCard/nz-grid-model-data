from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

GENERATION = Path("data/wholesale/model/generation_daily.csv")
STORAGE_ENERGY = Path("data/model/hydro_storage_energy_daily.csv")
STORAGE_CONSTRAINTS = Path("data/model/waitaki_storage_constraints.json")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
OUT_DAILY = Path("data/model/waitaki_solar_delta_dispatch_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_solar_delta_dispatch_summary.json")

YEAR = 2024
SITES = ["TKA", "PKI", "OHA"]
WAITAKI_GENERATION_SITES = {"TKA", "TKB", "OHA", "OHB", "OHC", "BEN", "AVI", "WTK"}
THERMAL_FUELS = {"Coal", "Gas", "Diesel"}
SOLAR_SCENARIOS_GWH = [0, 500, 1000, 1500, 2500]
POLICIES = ["fuel_minimizing", "winter_security"]
WINTER_START = "2024-05-01"


def load_generation():
    waitaki, thermal = defaultdict(float), defaultdict(float)
    with GENERATION.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d = r["date"]
            if not d.startswith(f"{YEAR}-"):
                continue
            v = float(r["generation_mwh"])
            if r["site_code"] in WAITAKI_GENERATION_SITES:
                waitaki[d] += v
            if r["fuel_code"] in THERMAL_FUELS:
                thermal[d] += v
    return dict(waitaki), dict(thermal)


def load_storage():
    cols = {s: f"{s}_energy_equivalent_gwh" for s in SITES}
    year_rows = {}
    simultaneous_max = 0.0
    with STORAGE_ENERGY.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            vals = {}
            for s, c in cols.items():
                raw = r.get(c, "")
                if raw == "":
                    break
                vals[s] = float(raw)
            if len(vals) != len(SITES):
                continue
            simultaneous_max = max(simultaneous_max, sum(vals.values()))
            if r["date"].startswith(f"{YEAR}-"):
                year_rows[r["date"]] = vals
    return year_rows, simultaneous_max


def load_ceilings():
    data = json.loads(STORAGE_CONSTRAINTS.read_text(encoding="utf-8"))
    return {s: float(data["sites"][s]["empirical_max"]["energy_equivalent_gwh"]) for s in SITES}


def solar_weights():
    data = json.loads(SEASONALITY.read_text(encoding="utf-8"))
    return {int(m): float(v) for m, v in data["solar_method"]["monthly_index_vs_annual_mean_daily"].items()}


def daily_solar(annual_gwh, dates, month_index):
    weights = {d: month_index[int(d[5:7])] for d in dates}
    z = sum(weights.values())
    return {d: annual_gwh * 1000.0 * w / z for d, w in weights.items()}


def trim_to_today(observed, delta, ceilings, combined_ceiling):
    """Force carried reserve back inside today's individual and simultaneous headroom."""
    spilled = {s: 0.0 for s in SITES}
    for s in SITES:
        allowed = max(0.0, ceilings[s] - observed[s])
        if delta[s] > allowed:
            spilled[s] += delta[s] - allowed
            delta[s] = allowed
    combined_allowed = max(0.0, combined_ceiling - sum(observed.values()))
    excess = max(0.0, sum(delta.values()) - combined_allowed)
    if excess > 0 and sum(delta.values()) > 0:
        total = sum(delta.values())
        for s in SITES:
            cut = min(delta[s], excess * delta[s] / total)
            delta[s] -= cut
            spilled[s] += cut
        # numerical remainder
        rem = max(0.0, sum(delta.values()) - combined_allowed)
        if rem > 1e-9:
            for s in sorted(SITES, key=lambda x: delta[x], reverse=True):
                cut = min(delta[s], rem)
                delta[s] -= cut
                spilled[s] += cut
                rem -= cut
                if rem <= 1e-9:
                    break
    return spilled


def available_headroom(observed, delta, ceilings, combined_ceiling):
    indiv = {s: max(0.0, ceilings[s] - observed[s] - delta[s]) for s in SITES}
    combined = max(0.0, combined_ceiling - sum(observed.values()) - sum(delta.values()))
    total = min(sum(indiv.values()), combined)
    return indiv, total


def allocate(candidate_mwh, observed, delta, ceilings, combined_ceiling):
    head, total_head = available_headroom(observed, delta, ceilings, combined_ceiling)
    stored = min(candidate_mwh / 1000.0, total_head)
    alloc = {s: 0.0 for s in SITES}
    if stored <= 0:
        return 0.0, alloc
    denom = sum(head.values())
    if denom <= 0:
        return 0.0, alloc
    for s in SITES:
        alloc[s] = stored * head[s] / denom
    return stored * 1000.0, alloc


def release(required_mwh, delta):
    available = sum(delta.values()) * 1000.0
    used = min(required_mwh, available)
    by = {s: 0.0 for s in SITES}
    if used <= 0 or available <= 0:
        return 0.0, by
    total = sum(delta.values())
    for s in SITES:
        by[s] = used * delta[s] / total
    return used, by


def dispatch(policy, annual_gwh, dates, solar, storage, ceilings, combined_ceiling, waitaki, thermal):
    delta = {s: 0.0 for s in SITES}
    winter_entry = None
    rows = []
    totals = defaultdict(float)
    max_total = 0.0
    max_by = {s: 0.0 for s in SITES}

    for d in dates:
        obs = storage[d]
        forced = trim_to_today(obs, delta, ceilings, combined_ceiling)
        forced_spill_mwh = sum(forced.values()) * 1000.0
        totals["forced_spill"] += forced_spill_mwh

        if d == WINTER_START and winter_entry is None:
            winter_entry = dict(delta)

        extra = solar[d]
        solar_left = extra
        thermal_left = thermal.get(d, 0.0)
        hydro_first = policy == "winter_security" and d < WINTER_START
        solar_to_thermal = hydro_stored = limited = reserve_release = 0.0

        def store_from_solar():
            nonlocal solar_left, hydro_stored, limited
            candidate = min(solar_left, waitaki.get(d, 0.0))
            hydro_stored, alloc = allocate(candidate, obs, delta, ceilings, combined_ceiling)
            limited = candidate - hydro_stored
            for s in SITES:
                delta[s] += alloc[s]
            solar_left -= hydro_stored
            totals["hydro_avoided"] += hydro_stored
            totals["storage_limited"] += limited

        def displace_thermal():
            nonlocal solar_left, thermal_left, solar_to_thermal
            solar_to_thermal = min(solar_left, thermal_left)
            solar_left -= solar_to_thermal
            thermal_left -= solar_to_thermal
            totals["direct_thermal"] += solar_to_thermal

        if hydro_first:
            store_from_solar()
            before = min(extra, thermal.get(d, 0.0))
            displace_thermal()
            totals["foregone"] += max(0.0, before - solar_to_thermal)
        else:
            displace_thermal()
            store_from_solar()

        release_by = {s: 0.0 for s in SITES}
        if d >= WINTER_START and thermal_left > 0 and sum(delta.values()) > 0:
            reserve_release, release_by = release(thermal_left, delta)
            for s in SITES:
                delta[s] -= release_by[s] / 1000.0
            totals["reserve_release"] += reserve_release
            thermal_left -= reserve_release

        curtailed = max(0.0, solar_left)
        totals["curtailed"] += curtailed
        max_total = max(max_total, sum(delta.values()))
        for s in SITES:
            max_by[s] = max(max_by[s], delta[s])

        row = {
            "policy": policy,
            "scenario_incremental_solar_gwh": annual_gwh,
            "date": d,
            "incremental_solar_mwh": round(extra, 6),
            "direct_thermal_displacement_mwh": round(solar_to_thermal, 6),
            "hydro_avoided_and_stored_mwh": round(hydro_stored, 6),
            "reserve_hydro_released_to_displace_thermal_mwh": round(reserve_release, 6),
            "forced_counterfactual_spill_due_to_headroom_mwh": round(forced_spill_mwh, 6),
            "storage_limited_hydro_displacement_mwh": round(limited, 6),
            "curtailed_incremental_solar_mwh": round(curtailed, 6),
            "counterfactual_storage_delta_gwh": round(sum(delta.values()), 6),
        }
        for s in SITES:
            row[f"{s}_observed_storage_gwh"] = round(obs[s], 6)
            row[f"{s}_delta_storage_gwh"] = round(delta[s], 6)
            row[f"{s}_counterfactual_storage_gwh"] = round(obs[s] + delta[s], 6)
        rows.append(row)

    winter_entry = winter_entry or {s: 0.0 for s in SITES}
    result = {
        "incremental_solar_gwh": annual_gwh,
        "winter_entry_storage_increase_gwh": round(sum(winter_entry.values()), 3),
        "winter_entry_storage_increase_by_lake_gwh": {s: round(winter_entry[s], 3) for s in SITES},
        "maximum_storage_increase_gwh": round(max_total, 3),
        "maximum_storage_increase_by_lake_gwh": {s: round(max_by[s], 3) for s in SITES},
        "year_end_storage_increase_gwh": round(sum(delta.values()), 3),
        "year_end_storage_increase_by_lake_gwh": {s: round(delta[s], 3) for s in SITES},
        "waitaki_hydro_avoided_and_stored_gwh": round(totals["hydro_avoided"] / 1000.0, 3),
        "direct_thermal_displacement_by_solar_gwh": round(totals["direct_thermal"] / 1000.0, 3),
        "thermal_displacement_by_later_release_of_conserved_hydro_gwh": round(totals["reserve_release"] / 1000.0, 3),
        "total_thermal_generation_displaced_gwh": round((totals["direct_thermal"] + totals["reserve_release"]) / 1000.0, 3),
        "foregone_pre_winter_direct_thermal_savings_gwh": round(totals["foregone"] / 1000.0, 3),
        "forced_counterfactual_spill_due_to_headroom_gwh": round(totals["forced_spill"] / 1000.0, 3),
        "storage_limited_hydro_displacement_gwh": round(totals["storage_limited"] / 1000.0, 3),
        "incremental_solar_curtailed_gwh": round(totals["curtailed"] / 1000.0, 3),
    }
    return rows, result


def main():
    waitaki, thermal = load_generation()
    storage, combined_ceiling = load_storage()
    ceilings = load_ceilings()
    dates = sorted(storage)
    if len(dates) != 366:
        raise RuntimeError(f"Expected 366 complete 2024 days, got {len(dates)}")
    month_index = solar_weights()

    all_rows = []
    results = {}
    for policy in POLICIES:
        results[policy] = {}
        for annual in SOLAR_SCENARIOS_GWH:
            rows, result = dispatch(policy, annual, dates, daily_solar(annual, dates, month_index), storage, ceilings, combined_ceiling, waitaki, thermal)
            all_rows.extend(rows)
            results[policy][str(annual)] = result

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    summary = {
        "status": "dynamic_headroom_constrained_policy_comparison",
        "purpose": "Correct the Waitaki solar-storage delta diagnostic for day-to-day loss of headroom as observed lake storage rises.",
        "method": {
            "baseline": "Observed 2024 per-lake storage plus observed Waitaki and thermal generation.",
            "individual_lake_empirical_ceilings_gwh": {s: round(ceilings[s], 3) for s in SITES},
            "simultaneous_upper_waitaki_empirical_ceiling_gwh": round(combined_ceiling, 3),
            "dynamic_headroom_rule": "At the start of every day, counterfactual reserve that no longer fits above the observed lake state is forced to spill before dispatch.",
            "allocation_rule": "Remaining storable energy is allocated in proportion to current individual lake headroom and is also capped by the historical simultaneous upper-Waitaki envelope.",
            "winter_entry_definition": "Reserve remaining after the 1 May start-of-day headroom reconciliation and before 1 May dispatch.",
            "policies": {
                "fuel_minimizing": "Solar displaces thermal first.",
                "winter_security": "Before 1 May solar conserves hydro first; from 1 May thermal is displaced first and conserved reserve can be released."
            }
        },
        "policies": results,
        "important_limitations": [
            "This is still a counterfactual delta diagnostic rather than a full physical Waitaki water-routing model.",
            "The simultaneous empirical ceiling is a historical envelope, not a legal engineering maximum.",
            "Proportional allocation among Tekapo/Pukaki/Ohau remains an interim assumption.",
            "The next physical refinement should tie avoided generation to source-water routes and enforce canal, station-flow, bypass and minimum-flow constraints."
        ]
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Combined historical simultaneous upper-Waitaki ceiling: {combined_ceiling:.3f} GWh")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
