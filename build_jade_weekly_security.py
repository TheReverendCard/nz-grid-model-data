from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path
from statistics import median

import requests

LATEST = Path("data/metadata/jade_latest.json")
OUT_DETAIL = Path("data/jade/model/weekly_security_simulations.csv")
OUT_SUMMARY = Path("data/jade/model/weekly_security_summary.csv")
OUT_META = Path("data/jade/model/weekly_security_sources.json")

CORE_BASENAMES = {
    "calendar": "tidy_results.csv",
    "stored_energy": "StoredEnergy.csv",
    "thermal_cost": "ThermalCosts.csv",
    "lost_load_cost": "LostLoadCost.csv",
}


def clean_key(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def parse_csv(url: str) -> list[dict[str, str]]:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append({clean_key(k): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k is not None})
    return rows


def pick_url(latest: dict, basename: str) -> str:
    matches = [item for item in latest["output_files"] if item["name"].endswith("/" + basename)]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one latest JADE output named {basename}; found {len(matches)}")
    return matches[0]["source_url"]


def to_int(value: str | None) -> int:
    if value is None or value == "":
        raise ValueError("Missing integer value")
    return int(float(value))


def to_float(value: str | None) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * p
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    frac = index - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def build_calendar(rows: list[dict[str, str]]) -> dict[tuple[int, int], tuple[int, int]]:
    mapping: dict[tuple[int, int], tuple[int, int]] = {}
    for row in rows:
        if not {"simulation", "stage", "year", "week"}.issubset(row):
            continue
        key = (to_int(row["simulation"]), to_int(row["stage"]))
        value = (to_int(row["year"]), to_int(row["week"]))
        previous = mapping.get(key)
        if previous is not None and previous != value:
            raise RuntimeError(f"Conflicting JADE calendar mapping for {key}: {previous} vs {value}")
        mapping[key] = value
    if not mapping:
        raise RuntimeError("No simulation/stage/year/week mapping found in tidy_results.csv")
    return mapping


def keyed_metric(rows: list[dict[str, str]], value_candidates: tuple[str, ...]) -> dict[tuple[int, int], float]:
    result: dict[tuple[int, int], float] = {}
    for row in rows:
        if "simulation" not in row or "stage" not in row:
            continue
        value_key = next((candidate for candidate in value_candidates if candidate in row), None)
        if value_key is None:
            continue
        key = (to_int(row["simulation"]), to_int(row["stage"]))
        result[key] = to_float(row[value_key])
    if not result:
        raise RuntimeError(f"No metric rows found for candidate columns {value_candidates}")
    return result


def main() -> None:
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    urls = {name: pick_url(latest, basename) for name, basename in CORE_BASENAMES.items()}

    calendar_rows = parse_csv(urls["calendar"])
    stored_rows = parse_csv(urls["stored_energy"])
    thermal_rows = parse_csv(urls["thermal_cost"])
    lost_rows = parse_csv(urls["lost_load_cost"])

    calendar = build_calendar(calendar_rows)
    stored = keyed_metric(stored_rows, ("energy_gwh", "stored_energy_gwh", "value"))
    thermal = keyed_metric(thermal_rows, ("thermal_cost", "cost", "value"))
    lost = keyed_metric(lost_rows, ("lost_load_cost", "cost", "value"))

    keys = sorted(set(calendar) & set(stored) & set(thermal) & set(lost))
    if not keys:
        raise RuntimeError("No common JADE simulation/stage rows across calendar, stored energy, thermal cost and lost-load cost")

    OUT_DETAIL.parent.mkdir(parents=True, exist_ok=True)
    detail_fields = [
        "jade_source_year", "jade_source_week", "simulation", "stage", "calendar_year", "calendar_week",
        "stored_energy_gwh", "thermal_cost_nzd", "lost_load_cost_nzd",
    ]
    with OUT_DETAIL.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=detail_fields)
        writer.writeheader()
        for simulation, stage in keys:
            year, week = calendar[(simulation, stage)]
            writer.writerow({
                "jade_source_year": latest["latest_year"],
                "jade_source_week": latest["latest_week"],
                "simulation": simulation,
                "stage": stage,
                "calendar_year": year,
                "calendar_week": week,
                "stored_energy_gwh": f"{stored[(simulation, stage)]:.6f}",
                "thermal_cost_nzd": f"{thermal[(simulation, stage)]:.6f}",
                "lost_load_cost_nzd": f"{lost[(simulation, stage)]:.6f}",
            })

    grouped: dict[tuple[int, int], list[tuple[float, float, float]]] = defaultdict(list)
    for key in keys:
        grouped[calendar[key]].append((stored[key], thermal[key], lost[key]))

    summary_fields = [
        "calendar_year", "calendar_week", "simulation_count",
        "stored_energy_p05_gwh", "stored_energy_p25_gwh", "stored_energy_median_gwh", "stored_energy_p75_gwh", "stored_energy_p95_gwh",
        "thermal_cost_median_nzd", "thermal_cost_p95_nzd", "thermal_cost_positive_share",
        "lost_load_cost_median_nzd", "lost_load_cost_p95_nzd", "lost_load_positive_share",
    ]
    with OUT_SUMMARY.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_fields)
        writer.writeheader()
        for (year, week), values in sorted(grouped.items()):
            storage_values = [x[0] for x in values]
            thermal_values = [x[1] for x in values]
            lost_values = [x[2] for x in values]
            writer.writerow({
                "calendar_year": year,
                "calendar_week": week,
                "simulation_count": len(values),
                "stored_energy_p05_gwh": f"{percentile(storage_values, 0.05):.6f}",
                "stored_energy_p25_gwh": f"{percentile(storage_values, 0.25):.6f}",
                "stored_energy_median_gwh": f"{median(storage_values):.6f}",
                "stored_energy_p75_gwh": f"{percentile(storage_values, 0.75):.6f}",
                "stored_energy_p95_gwh": f"{percentile(storage_values, 0.95):.6f}",
                "thermal_cost_median_nzd": f"{median(thermal_values):.6f}",
                "thermal_cost_p95_nzd": f"{percentile(thermal_values, 0.95):.6f}",
                "thermal_cost_positive_share": f"{sum(v > 0 for v in thermal_values) / len(thermal_values):.6f}",
                "lost_load_cost_median_nzd": f"{median(lost_values):.6f}",
                "lost_load_cost_p95_nzd": f"{percentile(lost_values, 0.95):.6f}",
                "lost_load_positive_share": f"{sum(v > 0 for v in lost_values) / len(lost_values):.6f}",
            })

    meta = {
        "source": "New Zealand Electricity Authority published JADE weekly outputs",
        "jade_source_year": latest["latest_year"],
        "jade_source_week": latest["latest_week"],
        "upstream_files": urls,
        "derived_files": [str(OUT_DETAIL), str(OUT_SUMMARY)],
        "method": "Join JADE tidy_results simulation/stage calendar mapping to StoredEnergy, ThermalCosts and LostLoadCost. Summarise stochastic simulations by calendar week using storage percentiles and shares/cost percentiles for thermal and lost load.",
        "important_caveat": "ThermalCosts is a cost output, not thermal generation energy. This first dataset is suitable for storage/security-risk visuals. Thermal GWh/MW will be added only after an authoritative JADE dispatch output is identified from its published schemas.",
        "raw_data_policy": "Raw JADE CSV files remain upstream at the Electricity Authority; this repository stores only the compact derived join needed for visualisation and reproducibility.",
    }
    OUT_META.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_DETAIL} ({len(keys)} simulation-stage rows)")
    print(f"Wrote {OUT_SUMMARY} ({len(grouped)} calendar weeks)")
    print(f"Wrote {OUT_META}")


if __name__ == "__main__":
    main()
