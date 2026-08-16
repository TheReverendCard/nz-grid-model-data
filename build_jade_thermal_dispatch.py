from __future__ import annotations

import csv
import io
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median

import requests

LATEST = Path("data/metadata/jade_latest.json")
OUT_SIM = Path("data/jade/model/weekly_thermal_dispatch_simulations.csv")
OUT_SUMMARY = Path("data/jade/model/weekly_thermal_dispatch_summary.csv")
OUT_SOURCES = Path("data/jade/model/weekly_thermal_dispatch_sources.json")


def percentile(values: list[float], p: float) -> float:
    vals = sorted(values)
    if not vals:
        return math.nan
    if len(vals) == 1:
        return vals[0]
    x = (len(vals) - 1) * p
    lo = math.floor(x)
    hi = math.ceil(x)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - x) + vals[hi] * (x - lo)


def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content.decode("utf-8-sig", errors="replace")


def find_upstream(latest: dict, filename: str, kind: str) -> dict:
    items = latest.get(kind, [])
    matches = [item for item in items if item["name"].lower().endswith(filename.lower())]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one latest JADE {filename} in {kind}, found {len(matches)}")
    return matches[0]


def read_hours(url: str) -> dict[tuple[int, int, str], float]:
    text = fetch_text(url)
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    clean = [h.strip().upper() for h in header]
    if len(clean) < 3 or clean[0] != "YEAR" or clean[1] != "WEEK":
        raise RuntimeError(f"Unexpected hours_per_block header: {header}")
    blocks = clean[2:]
    hours: dict[tuple[int, int, str], float] = {}
    for row in reader:
        if not row or not row[0].strip() or row[0].strip().startswith("%"):
            continue
        year = int(row[0].strip())
        week = int(row[1].strip())
        for idx, block in enumerate(blocks, start=2):
            hours[(year, week, block)] = float(row[idx].strip())
    return hours


def main() -> None:
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    tidy = find_upstream(latest, "tidy_results.csv", "output_files")
    hours_file = find_upstream(latest, "hours_per_block.csv", "input_files")

    hours = read_hours(hours_file["source_url"])

    # JADE tidy_results schema:
    # simulation,stage,year,week,type,name,demand,node,value
    # For thermal_use, name is the thermal station and demand is the load block.
    r = requests.get(tidy["source_url"], stream=True, timeout=120)
    r.raise_for_status()
    lines = (line.decode("utf-8-sig", errors="replace") for line in r.iter_lines() if line)
    reader = csv.DictReader(lines)

    required = {"simulation", "stage", "year", "week", "type", "name", "demand", "value"}
    if not reader.fieldnames or not required.issubset({f.strip() for f in reader.fieldnames}):
        raise RuntimeError(f"Unexpected tidy_results columns: {reader.fieldnames}")

    # Normalize keys because upstream headings may contain incidental whitespace.
    weekly_energy: dict[tuple[int, int, int], float] = defaultdict(float)
    weekly_block_mw: dict[tuple[int, int, int, str], float] = defaultdict(float)
    station_energy: dict[tuple[int, int, int, str], float] = defaultdict(float)
    row_count = 0

    for raw in reader:
        row = {str(k).strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        if row.get("type") != "thermal_use":
            continue
        sim = int(row["simulation"])
        year = int(row["year"])
        week = int(row["week"])
        station = row["name"]
        block = row["demand"].upper()
        mw = float(row["value"])
        try:
            block_hours = hours[(year, week, block)]
        except KeyError as exc:
            raise RuntimeError(f"No JADE block duration for {year} week {week} {block}") from exc
        gwh = mw * block_hours / 1000.0
        weekly_energy[(sim, year, week)] += gwh
        weekly_block_mw[(sim, year, week, block)] += mw
        station_energy[(sim, year, week, station)] += gwh
        row_count += 1

    if row_count == 0:
        raise RuntimeError("No thermal_use rows found in latest JADE tidy_results.csv")

    simulation_rows: list[dict[str, float | int]] = []
    grouped: dict[tuple[int, int], list[dict[str, float | int]]] = defaultdict(list)
    for (sim, year, week), gwh in sorted(weekly_energy.items()):
        block_values = [
            mw for (s, y, w, _block), mw in weekly_block_mw.items()
            if s == sim and y == year and w == week
        ]
        max_mw = max(block_values, default=0.0)
        row = {
            "simulation": sim,
            "calendar_year": year,
            "calendar_week": week,
            "thermal_generation_gwh": gwh,
            "thermal_peak_block_mw": max_mw,
        }
        simulation_rows.append(row)
        grouped[(year, week)].append(row)

    OUT_SIM.parent.mkdir(parents=True, exist_ok=True)
    with OUT_SIM.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(simulation_rows[0].keys()))
        writer.writeheader()
        writer.writerows(simulation_rows)

    summary_rows = []
    for (year, week), rows in sorted(grouped.items()):
        gwh = [float(r["thermal_generation_gwh"]) for r in rows]
        mw = [float(r["thermal_peak_block_mw"]) for r in rows]
        summary_rows.append({
            "calendar_year": year,
            "calendar_week": week,
            "simulation_count": len(rows),
            "thermal_generation_p05_gwh": percentile(gwh, 0.05),
            "thermal_generation_p25_gwh": percentile(gwh, 0.25),
            "thermal_generation_median_gwh": median(gwh),
            "thermal_generation_p75_gwh": percentile(gwh, 0.75),
            "thermal_generation_p95_gwh": percentile(gwh, 0.95),
            "thermal_generation_positive_share": sum(v > 1e-9 for v in gwh) / len(gwh),
            "thermal_peak_block_median_mw": median(mw),
            "thermal_peak_block_p95_mw": percentile(mw, 0.95),
        })

    with OUT_SUMMARY.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    sources = {
        "source": "New Zealand Electricity Authority published JADE weekly outputs and inputs",
        "jade_source_year": latest["latest_year"],
        "jade_source_week": latest["latest_week"],
        "upstream_files": {
            "thermal_dispatch": tidy["source_url"],
            "block_durations": hours_file["source_url"],
        },
        "jade_code_evidence": {
            "simulate": "JADE src/simulate.jl records :thermal_use and writes it to tidy_results.csv",
            "results": "JADE src/results.jl computes thermal costs as thermal_use * fuel cost * heat rate * block duration",
            "durations": "JADE src/data.jl loads hours_per_block.csv into d.durations",
        },
        "method": "Filter tidy_results.csv to type=thermal_use. JADE represents each station's thermal dispatch by load block in MW. Multiply station MW by the matching JADE hours_per_block.csv duration and divide by 1000 to obtain GWh, then aggregate by stochastic simulation and calendar week. Peak-block MW is the maximum across weekly load blocks after summing stations within each block.",
        "raw_data_policy": "Raw JADE files remain upstream; this repository stores only the compact derived thermal dispatch tables needed for visualisation and reproducibility.",
        "derived_files": [str(OUT_SIM), str(OUT_SUMMARY)],
        "thermal_use_rows_processed": row_count,
    }
    OUT_SOURCES.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")

    print(f"Processed {row_count} thermal_use rows")
    print(f"Wrote {OUT_SIM} ({len(simulation_rows)} simulation-weeks)")
    print(f"Wrote {OUT_SUMMARY} ({len(summary_rows)} calendar weeks)")
    print(f"Wrote {OUT_SOURCES}")


if __name__ == "__main__":
    main()
