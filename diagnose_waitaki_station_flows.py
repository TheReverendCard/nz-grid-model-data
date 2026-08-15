from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
TRIBUTARY = Path("data/hydro/model/tributary_flows_daily.csv")
OUT_DAILY = Path("data/model/waitaki_station_flow_diagnostic_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_station_flow_diagnostic.json")
YEAR = 2024
STATIONS = ("TKA", "TKB", "OHA", "OHB", "OHC", "BEN", "AVI", "WTK")


def load_assets():
    assets = {}
    with ASSETS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["plant_group"] != "Waitaki river" or row["site_code"] not in STATIONS:
                continue
            pf = row.get("plant_factor_cumecs_per_mw", "")
            cap = row.get("generating_capacity_mw", "")
            if pf:
                assets[row["site_code"]] = {
                    "plant_factor_cumecs_per_mw": float(pf),
                    "capacity_mw": float(cap) if cap else None,
                }
    missing = sorted(set(STATIONS) - set(assets))
    if missing:
        raise RuntimeError(f"Missing Waitaki assets: {missing}")
    return assets


def load_generation():
    by_date = defaultdict(lambda: defaultdict(float))
    matched_site_codes = set()
    with GENERATION.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["date"]
            if not date.startswith(f"{YEAR}-"):
                continue
            site = row["site_code"]
            if site in STATIONS:
                by_date[date][site] += float(row["generation_mwh"])
                matched_site_codes.add(site)
    return by_date, sorted(matched_site_codes)


def load_tributaries():
    result = defaultdict(lambda: defaultdict(float))
    with TRIBUTARY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["date"]
            site = row["site_code"]
            if date.startswith(f"{YEAR}-") and site in {"BEN", "AVI", "WTK"}:
                result[date][site] += float(row["flow_m3s"])
    return result


def corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    ax = mean(x for x, _ in pairs)
    ay = mean(y for _, y in pairs)
    num = sum((x - ax) * (y - ay) for x, y in pairs)
    dx = math.sqrt(sum((x - ax) ** 2 for x, _ in pairs))
    dy = math.sqrt(sum((y - ay) ** 2 for _, y in pairs))
    return num / (dx * dy) if dx and dy else None


def main():
    assets = load_assets()
    generation, matched = load_generation()
    tributary = load_tributaries()
    dates = sorted(generation)
    if not dates:
        raise RuntimeError("No 2024 Waitaki station generation matched expected site codes")

    rows = []
    flows = {s: [] for s in STATIONS}
    annual_gen = defaultdict(float)
    for date in dates:
        row = {"date": date}
        for site in STATIONS:
            mwh = generation[date].get(site, 0.0)
            avg_mw = mwh / 24.0
            q = avg_mw * assets[site]["plant_factor_cumecs_per_mw"]
            row[f"{site}_generation_mwh"] = round(mwh, 6)
            row[f"{site}_implied_turbine_flow_m3s"] = round(q, 6)
            flows[site].append(q)
            annual_gen[site] += mwh
        for site in ("BEN", "AVI", "WTK"):
            row[f"{site}_derived_tributary_flow_m3s"] = round(tributary[date].get(site, 0.0), 6)
        rows.append(row)

    pair_tests = []
    for upstream, downstream in (("TKA", "TKB"), ("OHA", "OHB"), ("OHB", "OHC"), ("BEN", "AVI"), ("AVI", "WTK")):
        diffs = [flows[downstream][i] - flows[upstream][i] for i in range(len(dates))]
        pair_tests.append({
            "upstream": upstream,
            "downstream": downstream,
            "correlation_daily_implied_flow": round(corr(flows[upstream], flows[downstream]), 4) if corr(flows[upstream], flows[downstream]) is not None else None,
            "mean_downstream_minus_upstream_m3s": round(mean(diffs), 3),
            "mean_absolute_difference_m3s": round(mean(abs(x) for x in diffs), 3),
        })

    lower_balance_tests = []
    for upstream, downstream in (("BEN", "AVI"), ("AVI", "WTK")):
        residuals = []
        for i, date in enumerate(dates):
            expected_add = tributary[date].get(downstream, 0.0)
            residuals.append(flows[downstream][i] - flows[upstream][i] - expected_add)
        lower_balance_tests.append({
            "upstream": upstream,
            "downstream": downstream,
            "tributary_added_at": downstream,
            "mean_residual_m3s": round(mean(residuals), 3),
            "mean_absolute_residual_m3s": round(mean(abs(x) for x in residuals), 3),
            "note": "Residual also contains lower-reservoir storage change, spill, timing and generation/plant-factor approximation error.",
        })

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "status": "waitaki_observed_station_flow_diagnostic",
        "year": YEAR,
        "purpose": "Use observed daily station generation and HMD plant factors to infer average turbine flow, validating cascade topology before an explicit node-and-arc dispatcher is built.",
        "matched_generation_site_codes": matched,
        "expected_station_codes": list(STATIONS),
        "annual_generation_gwh_by_station": {s: round(annual_gen[s] / 1000.0, 3) for s in STATIONS},
        "mean_implied_turbine_flow_m3s_by_station": {s: round(mean(flows[s]), 3) for s in STATIONS},
        "adjacent_station_flow_tests": pair_tests,
        "lower_cascade_balance_tests": lower_balance_tests,
        "important_limitations": [
            "Daily MWh divided by 24 and multiplied by HMD plant factor is an average turbine-flow equivalent, not a measured instantaneous discharge.",
            "Station outages, efficiency variation, spill, reservoir storage changes and intra-day travel/timing can create real differences between adjacent stations.",
            "This diagnostic is intended to falsify or support routing assumptions, not to infer exact daily releases from generation alone."
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}; matched sites={matched}")


if __name__ == "__main__":
    main()
