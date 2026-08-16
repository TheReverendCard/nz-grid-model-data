from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

INFLOWS = Path("data/hydro/model/inflows_daily.csv")
STORAGE = Path("data/hydro/model/storage_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
STORAGE_CONSTRAINTS = Path("data/model/waitaki_storage_constraints.json")
ROUTING_CONSTRAINTS = Path("data/model/waitaki_routing_constraints.json")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
OUT_DAILY = Path("data/model/waitaki_source_route_dispatch_v1_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_source_route_dispatch_v1_summary.json")

YEAR = 2024
SOURCES = ("TKA", "PKI", "OHA")
INFLOW_SOURCE_FILES = {
    "TKA": "SI_TEK_Natural_LakeTekapo_Inflow_98770(2).csv",
    "PKI": "SI_PKI_Natural_LakePukaki_Inflow_98770(1).csv",
    "OHA": "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv",
}
WAITAKI_GENERATION_SITES = {"TKA", "TKB", "OHA", "OHB", "OHC", "BEN", "AVI", "WTK"}
THERMAL_FUELS = {"Coal", "Gas", "Diesel"}
SOLAR_SCENARIOS_GWH = (0, 500, 1000, 1500, 2500)
WINTER_ENTRY = "2024-05-01"
MM3_PER_DAY_PER_CUMECS = 0.0864


def q25(values: list[float]) -> float:
    if not values:
        raise RuntimeError("Cannot calculate q25 from no values")
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    pos = 0.25 * (len(values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return values[lo] * (1.0 - frac) + values[hi] * frac


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_storage_history():
    observed: dict[str, dict[str, float]] = defaultdict(dict)
    climatology: dict[str, dict[str, list[float]]] = {
        s: defaultdict(list) for s in SOURCES
    }
    with STORAGE.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            site = r["site_code"]
            if site not in SOURCES or r["total_storage_mm3"] == "":
                continue
            d = r["date"]
            value = float(r["total_storage_mm3"])
            climatology[site][d[5:]].append(value)
            if d.startswith(f"{YEAR}-"):
                observed[d][site] = value

    targets = {}
    medians = {}
    for site in SOURCES:
        targets[site] = {md: q25(vals) for md, vals in climatology[site].items()}
        medians[site] = {md: median(vals) for md, vals in climatology[site].items()}

    complete = {
        d: vals for d, vals in observed.items() if len(vals) == len(SOURCES)
    }
    if len(complete) != 366:
        raise RuntimeError(f"Expected 366 complete 2024 storage days, got {len(complete)}")
    return complete, targets, medians


def load_inflows():
    wanted = set(INFLOW_SOURCE_FILES.values())
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    file_to_source = {v: k for k, v in INFLOW_SOURCE_FILES.items()}
    with INFLOWS.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r["source_file"] not in wanted:
                continue
            d = r["date"]
            if not d.startswith(f"{YEAR}-"):
                continue
            source = file_to_source[r["source_file"]]
            by_date[d][source] = float(r["volume_mm3_day"])
    if len(by_date) != 366 or any(len(v) != len(SOURCES) for v in by_date.values()):
        raise RuntimeError("2024 source inflows are incomplete")
    return by_date


def load_dispatchable_requirement():
    waitaki = defaultdict(float)
    thermal = defaultdict(float)
    with GENERATION.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            d = r["date"]
            if not d.startswith(f"{YEAR}-"):
                continue
            value = float(r["generation_mwh"])
            if r["site_code"] in WAITAKI_GENERATION_SITES:
                waitaki[d] += value
            if r["fuel_code"] in THERMAL_FUELS:
                thermal[d] += value
    dates = sorted(set(waitaki) | set(thermal))
    if len(dates) != 366:
        raise RuntimeError(f"Expected 366 generation days, got {len(dates)}")
    return dates, dict(waitaki), dict(thermal)


def solar_weights():
    data = load_json(SEASONALITY)
    return {
        int(m): float(v)
        for m, v in data["solar_method"]["monthly_index_vs_annual_mean_daily"].items()
    }


def make_daily_solar(annual_gwh: float, dates: list[str], month_index: dict[int, float]):
    weights = {d: month_index[int(d[5:7])] for d in dates}
    denom = sum(weights.values())
    return {d: annual_gwh * 1000.0 * weights[d] / denom for d in dates}


def route_inputs():
    storage_data = load_json(STORAGE_CONSTRAINTS)
    routing = load_json(ROUTING_CONSTRAINTS)["snapshots"]["validation_2024"]["active_sites"]

    ceilings = {
        s: float(storage_data["sites"][s]["empirical_max"]["storage_mm3"])
        for s in SOURCES
    }
    floors = {
        s: float(storage_data["sites"][s]["empirical_min"]["storage_mm3"])
        for s in SOURCES
    }
    energy = {
        s: float(storage_data["sites"][s]["mwh_per_mm3_full_route"])
        for s in SOURCES
    }

    def hydraulic_capacity(site: str) -> float:
        rec = routing[site]
        cap = float(rec["generating_capacity_mw"])
        pf = float(rec["plant_factor_cumecs_per_mw"])
        values = [cap * pf]
        for row in rec.get("max_flow_constraints", []):
            if row.get("plant_subtype") == "Station flow":
                values.append(float(row["value"]))
        return min(values)

    # Tekapo source water must pass Tekapo A and B before reaching Pukaki.
    tka_source_cap_m3s = min(hydraulic_capacity("TKA"), hydraulic_capacity("TKB"))

    # Pukaki source release is independently constrained by the Pukaki canal.
    pki_canal_rows = [
        r for r in routing["PKI"].get("max_flow_constraints", [])
        if r.get("plant_subtype") == "Canal"
    ]
    if not pki_canal_rows:
        raise RuntimeError("No active 2024 Pukaki canal max-flow record found")
    pki_source_cap_m3s = min(float(r["value"]) for r in pki_canal_rows)

    # Once Pukaki/Ohau/Tekapo source water is on the common upper-Waitaki route,
    # it must fit through Ohau A, B and C. Lower stations have larger hydraulic
    # capacities but are included so the bottleneck is data-derived.
    common_station_sites = ("OHA", "OHB", "OHC", "BEN", "AVI", "WTK")
    common_cap_m3s = min(hydraulic_capacity(s) for s in common_station_sites)

    return {
        "ceilings": ceilings,
        "floors": floors,
        "energy_mwh_per_mm3": energy,
        "tka_source_cap_m3s": tka_source_cap_m3s,
        "pki_source_cap_m3s": pki_source_cap_m3s,
        "common_cap_m3s": common_cap_m3s,
        "station_hydraulic_caps_m3s": {s: hydraulic_capacity(s) for s in ("TKA", "TKB") + common_station_sites},
    }


def dispatch_one(
    annual_solar_gwh: float,
    dates: list[str],
    inflows,
    observed,
    q25_targets,
    median_targets,
    waitaki_obs,
    thermal_obs,
    route,
    solar,
):
    ceilings = route["ceilings"]
    floors = route["floors"]
    energy = route["energy_mwh_per_mm3"]
    source_caps = {
        "TKA": route["tka_source_cap_m3s"] * MM3_PER_DAY_PER_CUMECS,
        "PKI": route["pki_source_cap_m3s"] * MM3_PER_DAY_PER_CUMECS,
        "OHA": route["common_cap_m3s"] * MM3_PER_DAY_PER_CUMECS,
    }
    common_cap_mm3_day = route["common_cap_m3s"] * MM3_PER_DAY_PER_CUMECS

    first = dates[0]
    state = {s: observed[first][s] for s in SOURCES}
    rows = []
    totals = defaultdict(float)
    errors = {s: [] for s in SOURCES}
    winter_entry = None
    min_state = dict(state)
    max_state = dict(state)

    for d in dates:
        md = d[5:]
        for s in SOURCES:
            state[s] += inflows[d][s]

        requirement = waitaki_obs.get(d, 0.0) + thermal_obs.get(d, 0.0)
        solar_used = min(solar[d], requirement)
        requirement_left = requirement - solar_used
        totals["solar_used"] += solar_used
        totals["solar_curtailed"] += max(0.0, solar[d] - solar_used)

        releases = {s: 0.0 for s in SOURCES}
        route_generation = {s: 0.0 for s in SOURCES}
        forced_spill = {s: 0.0 for s in SOURCES}
        common_left = common_cap_mm3_day

        # First route unavoidable overflow through turbines where demand and
        # hydraulic capacity permit. Only the remaining overflow is spill.
        for s in sorted(SOURCES, key=lambda x: energy[x], reverse=True):
            overflow = max(0.0, state[s] - ceilings[s])
            if overflow <= 0:
                continue
            max_volume = min(overflow, source_caps[s], common_left)
            if requirement_left > 0:
                max_volume = min(max_volume, requirement_left / energy[s])
            else:
                max_volume = 0.0
            if max_volume > 0:
                releases[s] += max_volume
                state[s] -= max_volume
                common_left -= max_volume
                gen = max_volume * energy[s]
                route_generation[s] += gen
                requirement_left = max(0.0, requirement_left - gen)
            still_over = max(0.0, state[s] - ceilings[s])
            if still_over > 0:
                forced_spill[s] += still_over
                state[s] -= still_over

        # Normal dispatch uses water above the historical 25th-percentile
        # storage curve. Sources with the strongest normalized surplus and the
        # highest downstream energy value are selected first.
        while requirement_left > 1e-6 and common_left > 1e-9:
            candidates = []
            for s in SOURCES:
                target = max(floors[s], q25_targets[s].get(md, floors[s]))
                available = max(0.0, state[s] - target - releases[s])
                available = min(available, source_caps[s] - releases[s], common_left)
                if available <= 1e-9:
                    continue
                denom = max(1e-9, ceilings[s] - target)
                fullness = max(0.0, state[s] - target) / denom
                score = fullness * energy[s]
                candidates.append((score, s, available))
            if not candidates:
                break
            _, s, available = max(candidates)
            volume = min(available, requirement_left / energy[s])
            if volume <= 1e-9:
                break
            releases[s] += volume
            state[s] -= volume
            common_left -= volume
            gen = volume * energy[s]
            route_generation[s] += gen
            requirement_left = max(0.0, requirement_left - gen)

        hydro_generation = sum(route_generation.values())
        thermal_generation = requirement_left
        totals["hydro"] += hydro_generation
        totals["thermal"] += thermal_generation
        totals["spill_mm3"] += sum(forced_spill.values())
        for s in SOURCES:
            totals[f"release_{s}_mm3"] += releases[s]
            totals[f"spill_{s}_mm3"] += forced_spill[s]
            min_state[s] = min(min_state[s], state[s])
            max_state[s] = max(max_state[s], state[s])
            if d in observed:
                errors[s].append(abs(state[s] - observed[d][s]))

        if d == WINTER_ENTRY:
            winter_entry = dict(state)

        row = {
            "scenario_incremental_solar_gwh": annual_solar_gwh,
            "date": d,
            "dispatchable_requirement_mwh": round(requirement, 6),
            "incremental_solar_mwh": round(solar[d], 6),
            "incremental_solar_used_mwh": round(solar_used, 6),
            "modeled_waitaki_hydro_mwh": round(hydro_generation, 6),
            "modeled_thermal_backstop_mwh": round(thermal_generation, 6),
            "observed_waitaki_hydro_mwh": round(waitaki_obs.get(d, 0.0), 6),
            "observed_thermal_mwh": round(thermal_obs.get(d, 0.0), 6),
            "common_route_unused_capacity_mm3": round(common_left, 6),
        }
        for s in SOURCES:
            row[f"{s}_natural_inflow_mm3"] = round(inflows[d][s], 6)
            row[f"{s}_release_mm3"] = round(releases[s], 6)
            row[f"{s}_route_generation_mwh"] = round(route_generation[s], 6)
            row[f"{s}_forced_spill_mm3"] = round(forced_spill[s], 6)
            row[f"{s}_storage_mm3"] = round(state[s], 6)
            row[f"{s}_observed_storage_mm3"] = round(observed[d][s], 6)
            row[f"{s}_q25_target_mm3"] = round(q25_targets[s].get(md, 0.0), 6)
            row[f"{s}_median_target_mm3"] = round(median_targets[s].get(md, 0.0), 6)
        rows.append(row)

    winter_entry = winter_entry or dict(state)
    observed_winter = observed[WINTER_ENTRY]
    observed_end = observed[dates[-1]]
    modeled_end = dict(state)

    summary = {
        "incremental_solar_gwh": annual_solar_gwh,
        "modeled_waitaki_hydro_gwh": round(totals["hydro"] / 1000.0, 3),
        "modeled_thermal_backstop_gwh": round(totals["thermal"] / 1000.0, 3),
        "incremental_solar_used_gwh": round(totals["solar_used"] / 1000.0, 3),
        "incremental_solar_curtailed_gwh": round(totals["solar_curtailed"] / 1000.0, 3),
        "forced_spill_mm3": round(totals["spill_mm3"], 3),
        "winter_entry_storage_mm3": {s: round(winter_entry[s], 3) for s in SOURCES},
        "winter_entry_storage_difference_vs_observed_mm3": {
            s: round(winter_entry[s] - observed_winter[s], 3) for s in SOURCES
        },
        "year_end_storage_mm3": {s: round(modeled_end[s], 3) for s in SOURCES},
        "year_end_storage_difference_vs_observed_mm3": {
            s: round(modeled_end[s] - observed_end[s], 3) for s in SOURCES
        },
        "storage_mae_vs_observed_mm3": {
            s: round(sum(errors[s]) / len(errors[s]), 3) for s in SOURCES
        },
        "minimum_modeled_storage_mm3": {s: round(min_state[s], 3) for s in SOURCES},
        "maximum_modeled_storage_mm3": {s: round(max_state[s], 3) for s in SOURCES},
        "source_release_mm3": {s: round(totals[f"release_{s}_mm3"], 3) for s in SOURCES},
        "source_spill_mm3": {s: round(totals[f"spill_{s}_mm3"], 3) for s in SOURCES},
    }
    return rows, summary


def main():
    observed, q25_targets, median_targets = load_storage_history()
    inflows = load_inflows()
    dates, waitaki_obs, thermal_obs = load_dispatchable_requirement()
    route = route_inputs()
    month_index = solar_weights()

    observed_waitaki_gwh = sum(waitaki_obs.values()) / 1000.0
    observed_thermal_gwh = sum(thermal_obs.values()) / 1000.0

    all_rows = []
    scenarios = {}
    for annual in SOLAR_SCENARIOS_GWH:
        solar = make_daily_solar(float(annual), dates, month_index)
        rows, result = dispatch_one(
            float(annual), dates, inflows, observed, q25_targets, median_targets,
            waitaki_obs, thermal_obs, route, solar,
        )
        all_rows.extend(rows)
        scenarios[str(annual)] = result

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    baseline = scenarios["0"]
    for key, result in scenarios.items():
        result["change_vs_modeled_zero_solar"] = {
            "waitaki_hydro_gwh": round(result["modeled_waitaki_hydro_gwh"] - baseline["modeled_waitaki_hydro_gwh"], 3),
            "thermal_backstop_gwh": round(result["modeled_thermal_backstop_gwh"] - baseline["modeled_thermal_backstop_gwh"], 3),
            "winter_entry_storage_mm3": {
                s: round(result["winter_entry_storage_mm3"][s] - baseline["winter_entry_storage_mm3"][s], 3)
                for s in SOURCES
            },
            "forced_spill_mm3": round(result["forced_spill_mm3"] - baseline["forced_spill_mm3"], 3),
        }

    summary = {
        "status": "first_source_route_waitaki_water_balance_dispatcher",
        "year": YEAR,
        "purpose": "Replace generic Waitaki energy-bucket conservation with an explicit source-reservoir and common-canal water balance for Tekapo, Pukaki and Ohau.",
        "validation_reference": {
            "observed_waitaki_generation_gwh": round(observed_waitaki_gwh, 3),
            "observed_thermal_generation_gwh": round(observed_thermal_gwh, 3),
            "dispatchable_requirement_definition": "Observed 2024 Waitaki hydro plus observed coal/gas/diesel generation, with the rest of the grid held fixed.",
        },
        "routing": {
            "source_energy_mwh_per_mm3": {s: round(v, 6) for s, v in route["energy_mwh_per_mm3"].items()},
            "tka_source_cap_m3s": round(route["tka_source_cap_m3s"], 6),
            "pki_source_canal_cap_m3s": round(route["pki_source_cap_m3s"], 6),
            "common_route_cap_m3s": round(route["common_cap_m3s"], 6),
            "station_hydraulic_caps_m3s": {s: round(v, 6) for s, v in route["station_hydraulic_caps_m3s"].items()},
        },
        "dispatch_policy": {
            "seasonal_guard": "Normal releases are limited to water above each source lake's historical day-of-year 25th-percentile storage curve.",
            "source_selection": "Among available sources, release priority increases with normalized surplus above the seasonal guard and downstream MWh per Mm3.",
            "overflow": "Water above an empirical storage ceiling is routed through generation first if load and hydraulic capacity permit; remaining overflow is spill.",
            "thermal": "Unlimited thermal is an expensive backstop proxy and fills the remaining dispatchable requirement.",
        },
        "scenarios": scenarios,
        "important_limitations": [
            "This is a transparent daily heuristic, not an optimized market dispatch or a reconstruction of historical operator releases.",
            "Empirical storage extrema are still used as reservoir ceilings/floors; HMD scheduled consent curves are not yet evaluated day by day.",
            "The daily source-route abstraction allows Tekapo source water to traverse Pukaki and the common canal within the same model day; travel time and intra-day lake mixing are ignored.",
            "Ruataniwha, Benmore and Aviemore are treated as routing stages rather than independently dispatched storage nodes in this first version.",
            "Lower-catchment tributary additions are not yet included in the source-route water balance.",
            "The historical 25th-percentile storage guard is a transparent operating heuristic chosen for validation, not a claim about Meridian/Genesis water values.",
            "Incremental solar uses the observed utility-solar monthly seasonality diagnostic rather than a full hourly geographic PV model.",
        ],
        "next_validation_tests": [
            "Compare zero-solar modeled annual Waitaki hydro and thermal with observed 2024 totals.",
            "Compare zero-solar storage MAE and winter-entry storage with observed Tekapo/Pukaki/Ohau trajectories.",
            "If zero-solar validation is poor, calibrate the seasonal storage guard before interpreting solar counterfactuals.",
            "Then compare incremental solar effects on winter-entry storage, thermal generation and spill against the earlier energy-bucket ceiling."
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY}")
    print(f"Wrote {OUT_SUMMARY}")
    print("Observed Waitaki GWh:", round(observed_waitaki_gwh, 3))
    print("Modeled zero-solar Waitaki GWh:", baseline["modeled_waitaki_hydro_gwh"])
    print("Observed thermal GWh:", round(observed_thermal_gwh, 3))
    print("Modeled zero-solar thermal GWh:", baseline["modeled_thermal_backstop_gwh"])


if __name__ == "__main__":
    main()
