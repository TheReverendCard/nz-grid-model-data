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


def load_lake_storage() -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    cols = {site: f"{site}_energy_equivalent_gwh" for site in SITES}
    with STORAGE_ENERGY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            d = row["date"]
            if not d.startswith(f"{YEAR}-"):
                continue
            vals = {}
            for site, col in cols.items():
                raw = row.get(col, "")
                if raw == "":
                    break
                vals[site] = float(raw)
            if len(vals) == len(SITES):
                rows[d] = vals
    return rows


def load_lake_ceilings() -> dict[str, float]:
    data = json.loads(STORAGE_CONSTRAINTS.read_text(encoding="utf-8"))
    return {site: float(data["sites"][site]["empirical_max"]["energy_equivalent_gwh"]) for site in SITES}


def solar_month_weights() -> dict[int, float]:
    data = json.loads(SEASONALITY.read_text(encoding="utf-8"))
    return {int(m): float(v) for m, v in data["solar_method"]["monthly_index_vs_annual_mean_daily"].items()}


def scenario_daily_solar(annual_gwh: float, dates: list[str], month_index: dict[int, float]) -> dict[str, float]:
    weights = {d: month_index[int(d[5:7])] for d in dates}
    denom = sum(weights.values())
    return {d: annual_gwh * 1000.0 * w / denom for d, w in weights.items()}


def allocate_to_headroom(candidate_mwh: float, observed: dict[str, float], delta: dict[str, float],
                         ceilings: dict[str, float]) -> tuple[float, dict[str, float]]:
    headroom_gwh = {site: max(0.0, ceilings[site] - observed[site] - delta[site]) for site in SITES}
    total_headroom_mwh = sum(headroom_gwh.values()) * 1000.0
    stored_mwh = min(candidate_mwh, total_headroom_mwh)
    allocated = {site: 0.0 for site in SITES}
    if stored_mwh <= 0.0 or total_headroom_mwh <= 0.0:
        return 0.0, allocated

    # Interim, explicit allocation rule: distribute conserved energy in proportion
    # to each lake's available daily energy-equivalent headroom. This respects each
    # individual ceiling but does not yet represent canal routing or source water.
    total_headroom_gwh = sum(headroom_gwh.values())
    remaining = stored_mwh
    for i, site in enumerate(SITES):
        if i == len(SITES) - 1:
            amount = remaining
        else:
            amount = stored_mwh * headroom_gwh[site] / total_headroom_gwh
            amount = min(amount, headroom_gwh[site] * 1000.0)
        allocated[site] = amount
        remaining -= amount
    return stored_mwh, allocated


def release_reserve(required_mwh: float, delta: dict[str, float]) -> tuple[float, dict[str, float]]:
    available_mwh = sum(delta.values()) * 1000.0
    released = min(required_mwh, available_mwh)
    by_site = {site: 0.0 for site in SITES}
    if released <= 0.0 or available_mwh <= 0.0:
        return 0.0, by_site
    total_delta = sum(delta.values())
    remaining = released
    for i, site in enumerate(SITES):
        if i == len(SITES) - 1:
            amount = remaining
        else:
            amount = released * delta[site] / total_delta if total_delta > 0 else 0.0
            amount = min(amount, delta[site] * 1000.0)
        by_site[site] = amount
        remaining -= amount
    return released, by_site


def dispatch_policy(policy: str, annual_gwh: float, dates: list[str], solar: dict[str, float],
                    storage: dict[str, dict[str, float]], ceilings: dict[str, float],
                    waitaki_gen: dict[str, float], thermal_gen: dict[str, float]) -> tuple[list[dict[str, object]], dict[str, object]]:
    delta = {site: 0.0 for site in SITES}
    winter_entry_by_site = None
    max_total_delta = 0.0
    direct_thermal = hydro_avoided = reserve_released = curtailed = storage_limited = 0.0
    foregone_pre_winter = 0.0
    max_by_site = {site: 0.0 for site in SITES}
    rows = []

    for d in dates:
        observed = storage[d]
        observed_waitaki = waitaki_gen.get(d, 0.0)
        observed_thermal = thermal_gen.get(d, 0.0)
        extra_solar = solar[d]

        # Winter-entry is the stored reserve carried into the start of 1 May,
        # before any 1 May generation or release decision is made.
        if d == WINTER_START and winter_entry_by_site is None:
            winter_entry_by_site = dict(delta)

        solar_remaining = extra_solar
        thermal_remaining = observed_thermal
        hydro_first = policy == "winter_security" and d < WINTER_START
        solar_to_thermal = hydro_stored = storage_limited_today = reserve_release = 0.0
        allocation = {site: 0.0 for site in SITES}
        release_by_site = {site: 0.0 for site in SITES}

        if hydro_first:
            hydro_candidate = min(solar_remaining, observed_waitaki)
            hydro_stored, allocation = allocate_to_headroom(hydro_candidate, observed, delta, ceilings)
            storage_limited_today = hydro_candidate - hydro_stored
            for site in SITES:
                delta[site] += allocation[site] / 1000.0
            hydro_avoided += hydro_stored
            storage_limited += storage_limited_today
            solar_remaining -= hydro_stored

            solar_to_thermal = min(solar_remaining, observed_thermal)
            direct_thermal += solar_to_thermal
            solar_remaining -= solar_to_thermal
            thermal_remaining -= solar_to_thermal
            foregone_pre_winter += max(0.0, min(extra_solar, observed_thermal) - solar_to_thermal)
        else:
            solar_to_thermal = min(solar_remaining, observed_thermal)
            direct_thermal += solar_to_thermal
            solar_remaining -= solar_to_thermal
            thermal_remaining -= solar_to_thermal

            hydro_candidate = min(solar_remaining, observed_waitaki)
            hydro_stored, allocation = allocate_to_headroom(hydro_candidate, observed, delta, ceilings)
            storage_limited_today = hydro_candidate - hydro_stored
            for site in SITES:
                delta[site] += allocation[site] / 1000.0
            hydro_avoided += hydro_stored
            storage_limited += storage_limited_today
            solar_remaining -= hydro_stored

        if d >= WINTER_START and thermal_remaining > 0.0 and sum(delta.values()) > 0.0:
            reserve_release, release_by_site = release_reserve(thermal_remaining, delta)
            for site in SITES:
                delta[site] -= release_by_site[site] / 1000.0
            reserve_released += reserve_release
            thermal_remaining -= reserve_release

        curtailed_today = max(0.0, solar_remaining)
        curtailed += curtailed_today
        total_delta = sum(delta.values())
        max_total_delta = max(max_total_delta, total_delta)
        for site in SITES:
            max_by_site[site] = max(max_by_site[site], delta[site])

        row = {
            "policy": policy,
            "scenario_incremental_solar_gwh": annual_gwh,
            "date": d,
            "incremental_solar_mwh": round(extra_solar, 6),
            "observed_waitaki_generation_mwh": round(observed_waitaki, 6),
            "observed_thermal_generation_mwh": round(observed_thermal, 6),
            "direct_thermal_displacement_mwh": round(solar_to_thermal, 6),
            "hydro_avoided_and_stored_mwh": round(hydro_stored, 6),
            "reserve_hydro_released_to_displace_thermal_mwh": round(reserve_release, 6),
            "counterfactual_storage_delta_gwh": round(total_delta, 6),
            "storage_limited_hydro_displacement_mwh": round(storage_limited_today, 6),
            "curtailed_incremental_solar_mwh": round(curtailed_today, 6),
        }
        for site in SITES:
            row[f"{site}_observed_storage_gwh"] = round(observed[site], 6)
            row[f"{site}_delta_storage_gwh"] = round(delta[site], 6)
            row[f"{site}_counterfactual_storage_gwh"] = round(observed[site] + delta[site], 6)
            row[f"{site}_headroom_gwh"] = round(max(0.0, ceilings[site] - observed[site] - delta[site]), 6)
        rows.append(row)

    winter_entry_by_site = winter_entry_by_site or {site: 0.0 for site in SITES}
    result: dict[str, object] = {
        "incremental_solar_gwh": annual_gwh,
        "winter_entry_storage_increase_gwh": round(sum(winter_entry_by_site.values()), 3),
        "winter_entry_storage_increase_by_lake_gwh": {site: round(winter_entry_by_site[site], 3) for site in SITES},
        "maximum_storage_increase_gwh": round(max_total_delta, 3),
        "maximum_storage_increase_by_lake_gwh": {site: round(max_by_site[site], 3) for site in SITES},
        "year_end_storage_increase_gwh": round(sum(delta.values()), 3),
        "year_end_storage_increase_by_lake_gwh": {site: round(delta[site], 3) for site in SITES},
        "waitaki_hydro_avoided_and_stored_gwh": round(hydro_avoided / 1000.0, 3),
        "direct_thermal_displacement_by_solar_gwh": round(direct_thermal / 1000.0, 3),
        "thermal_displacement_by_later_release_of_conserved_hydro_gwh": round(reserve_released / 1000.0, 3),
        "total_thermal_generation_displaced_gwh": round((direct_thermal + reserve_released) / 1000.0, 3),
        "foregone_pre_winter_direct_thermal_savings_gwh": round(foregone_pre_winter / 1000.0, 3),
        "storage_limited_hydro_displacement_gwh": round(storage_limited / 1000.0, 3),
        "incremental_solar_curtailed_gwh": round(curtailed / 1000.0, 3),
    }
    return rows, result


def main() -> None:
    waitaki_gen, thermal_gen = load_generation()
    storage = load_lake_storage()
    ceilings = load_lake_ceilings()
    month_index = solar_month_weights()
    dates = sorted(storage)
    if len(dates) != 366:
        raise RuntimeError(f"Expected 366 complete 2024 storage days, got {len(dates)}")

    daily_rows = []
    policy_results: dict[str, dict[str, dict[str, object]]] = {}
    for policy in POLICIES:
        policy_results[policy] = {}
        for annual_gwh in SOLAR_SCENARIOS_GWH:
            solar = scenario_daily_solar(annual_gwh, dates, month_index)
            rows, result = dispatch_policy(policy, annual_gwh, dates, solar, storage, ceilings, waitaki_gen, thermal_gen)
            daily_rows.extend(rows)
            policy_results[policy][str(annual_gwh)] = result

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(daily_rows[0].keys()))
        writer.writeheader()
        writer.writerows(daily_rows)

    summary = {
        "status": "individual_lake_policy_comparison_delta_dispatch_diagnostic",
        "purpose": "Tighten the 2024 solar-to-Waitaki-storage counterfactual by enforcing separate Tekapo, Pukaki and Ohau empirical storage headroom.",
        "method": {
            "baseline": "Observed 2024 per-lake energy-equivalent storage, Waitaki generation and thermal generation.",
            "delta_state": "Only incremental-solar-caused storage changes are simulated around observed hydrology.",
            "individual_lake_empirical_ceilings_gwh": {site: round(ceilings[site], 3) for site in SITES},
            "allocation_rule": "Interim rule: conserved energy is distributed across TKA/PKI/OHA in proportion to each lake's available daily energy-equivalent headroom. Individual ceilings are enforced, but source-water/canal routing is not yet represented.",
            "winter_entry_definition": "Counterfactual stored reserve at the start of 1 May, before 1 May dispatch.",
            "policies": {
                "fuel_minimizing": "Solar always displaces observed thermal generation before conserving Waitaki hydro.",
                "winter_security": "Before 1 May solar conserves Waitaki hydro first; from 1 May solar displaces thermal first and conserved reserve can be released against remaining thermal generation.",
            },
        },
        "policies": policy_results,
        "important_limitations": [
            "This remains a delta diagnostic rather than a full physical Waitaki water-routing model.",
            "Separate lake ceilings remove the combined-bucket flexibility error, but proportional headroom allocation is still an explicit simplifying assumption.",
            "The next physical refinement must tie conserved water to source lake releases and enforce Tekapo/Pukaki/Ohau canal, station-flow, bypass and minimum-flow constraints.",
            "Observed 2024 storage remains the hydrological baseline, so natural inflows, tributaries, spill and historical operating behaviour are embedded rather than reconstructed."
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} ({len(daily_rows)} rows)")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
