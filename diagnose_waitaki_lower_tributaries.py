from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

TRIBUTARY = Path("data/hydro/model/tributary_flows_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT_DAILY = Path("data/model/waitaki_lower_tributary_energy_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_lower_tributary_energy_summary.json")
YEAR = 2024

# Incremental HMD tributary flow entering each lower Waitaki reservoir can pass
# through the named station and every station below it in the cascade.
DOWNSTREAM_STATIONS = {
    "BEN": ("BEN", "AVI", "WTK"),
    "AVI": ("AVI", "WTK"),
    "WTK": ("WTK",),
}


def load_station_conversion() -> dict[str, float]:
    result: dict[str, float] = {}
    with ASSETS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["plant_group"] != "Waitaki river":
                continue
            value = row.get("mw_per_cumec", "")
            if value:
                result[row["site_code"]] = float(value)
    missing = sorted({s for route in DOWNSTREAM_STATIONS.values() for s in route} - set(result))
    if missing:
        raise RuntimeError(f"Missing Waitaki station conversion factors: {missing}")
    return result


def load_daily() -> tuple[list[dict[str, object]], dict[str, float]]:
    mw_per_cumec = load_station_conversion()
    route_mw_per_cumec = {
        source: sum(mw_per_cumec[s] for s in stations)
        for source, stations in DOWNSTREAM_STATIONS.items()
    }
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with TRIBUTARY.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            d = row["date"]
            site = row["site_code"]
            if not d.startswith(f"{YEAR}-") or site not in DOWNSTREAM_STATIONS:
                continue
            by_date[d][site] += float(row["flow_m3s"])

    rows: list[dict[str, object]] = []
    for d in sorted(by_date):
        out: dict[str, object] = {"date": d}
        total = 0.0
        for site in DOWNSTREAM_STATIONS:
            flow = by_date[d].get(site, 0.0)
            energy = flow * 24.0 * route_mw_per_cumec[site]
            out[f"{site}_tributary_flow_m3s"] = round(flow, 6)
            out[f"{site}_downstream_mw_per_cumec"] = round(route_mw_per_cumec[site], 9)
            out[f"{site}_potential_generation_mwh"] = round(energy, 6)
            total += energy
        out["lower_tributary_potential_generation_mwh"] = round(total, 6)
        rows.append(out)
    return rows, route_mw_per_cumec


def main() -> None:
    rows, route_conversion = load_daily()
    if not rows:
        raise RuntimeError("No 2024 lower Waitaki tributary rows found")

    annual = {
        site: sum(float(r[f"{site}_potential_generation_mwh"]) for r in rows) / 1000.0
        for site in DOWNSTREAM_STATIONS
    }
    total_gwh = sum(float(r["lower_tributary_potential_generation_mwh"]) for r in rows) / 1000.0
    may1_gwh = sum(
        float(r["lower_tributary_potential_generation_mwh"])
        for r in rows
        if str(r["date"]) < "2024-05-01"
    ) / 1000.0

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "status": "waitaki_lower_tributary_generation_potential_diagnostic",
        "year": YEAR,
        "purpose": "Quantify electricity that incremental lower-catchment Waitaki tributary water could produce without drawing Tekapo, Pukaki or Ohau headwater storage.",
        "downstream_station_routes": {k: list(v) for k, v in DOWNSTREAM_STATIONS.items()},
        "downstream_mw_per_cumec": {k: round(v, 9) for k, v in route_conversion.items()},
        "potential_generation_gwh_by_entry": {k: round(v, 3) for k, v in annual.items()},
        "total_2024_potential_generation_gwh": round(total_gwh, 3),
        "potential_generation_before_1_may_gwh": round(may1_gwh, 3),
        "method": "HMD derived tributary flow at BEN/AVI/WTK multiplied by 24 h and the sum of MW-per-cumec conversion factors at that station and stations downstream.",
        "important_limitations": [
            "This is hydrological generation potential, not assured actual generation.",
            "It assumes incremental tributary water can traverse all downstream stations on the same day and ignores Benmore/Aviemore/Waitaki short-term storage timing.",
            "It does not yet couple tributary water and headwater releases to shared downstream station MW/flow limits.",
            "Spill and bypass can reduce realized generation, so this is best treated as an upper-bound diagnostic before explicit lower-reservoir routing is added."
        ]
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}; lower tributary potential={total_gwh:.3f} GWh")


if __name__ == "__main__":
    main()
