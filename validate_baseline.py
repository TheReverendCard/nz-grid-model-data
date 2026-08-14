from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

MODEL_DIR = Path("data/wholesale/model")
SUMMARY_PATH = MODEL_DIR / "baseline_summary.json"


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def main() -> None:
    gen_by_year = defaultdict(float)
    gen_by_year_fuel = defaultdict(lambda: defaultdict(float))
    gen_by_year_date = defaultdict(lambda: defaultdict(float))

    for row in read_csv(MODEL_DIR / "generation_daily.csv"):
        date = row["date"]
        year = date[:4]
        value = float(row["generation_mwh"])
        fuel = row.get("fuel_code", "") or "Unknown"
        gen_by_year[year] += value
        gen_by_year_fuel[year][fuel] += value
        gen_by_year_date[year][date] += value

    demand_by_year = defaultdict(float)
    demand_by_year_date = defaultdict(dict)
    for row in read_csv(MODEL_DIR / "demand_daily.csv"):
        date = row["date"]
        year = date[:4]
        value = float(row["grid_export_mwh"])
        demand_by_year[year] += value
        demand_by_year_date[year][date] = value

    years = sorted(set(gen_by_year) | set(demand_by_year))
    summary = {"years": {}}

    for year in years:
        generation = gen_by_year.get(year, 0.0)
        demand = demand_by_year.get(year, 0.0)
        dates = sorted(set(gen_by_year_date[year]) & set(demand_by_year_date[year]))
        daily_gaps = [gen_by_year_date[year][d] - demand_by_year_date[year][d] for d in dates]

        summary["years"][year] = {
            "generation_mwh": round(generation, 3),
            "grid_export_mwh": round(demand, 3),
            "generation_minus_grid_export_mwh": round(generation - demand, 3),
            "generation_to_grid_export_ratio": round(generation / demand, 6) if demand else None,
            "overlap_days": len(dates),
            "mean_daily_gap_mwh": round(sum(daily_gaps) / len(daily_gaps), 3) if daily_gaps else None,
            "min_daily_gap_mwh": round(min(daily_gaps), 3) if daily_gaps else None,
            "max_daily_gap_mwh": round(max(daily_gaps), 3) if daily_gaps else None,
            "generation_by_fuel_mwh": {
                fuel: round(value, 3)
                for fuel, value in sorted(gen_by_year_fuel[year].items())
            },
        }

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {SUMMARY_PATH}")
    for year in years:
        data = summary["years"][year]
        print(
            f"{year}: generation={data['generation_mwh'] / 1_000_000:.3f} TWh, "
            f"grid_export={data['grid_export_mwh'] / 1_000_000:.3f} TWh, "
            f"ratio={data['generation_to_grid_export_ratio']}"
        )


if __name__ == "__main__":
    main()
