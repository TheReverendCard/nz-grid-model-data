from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

MODEL_DIR = Path("data/wholesale/model")
SUMMARY_PATH = MODEL_DIR / "baseline_summary.json"
RECONCILED_PATH = MODEL_DIR / "reconciled_daily.csv"


def read_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def ratio(numerator: float, denominator: float):
    return round(numerator / denominator, 6) if denominator else None


def pct_difference(value: float, reference: float):
    return round((value - reference) / reference * 100.0, 4) if reference else None


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

    grid_export_by_year = defaultdict(float)
    grid_export_by_year_date = defaultdict(dict)
    for row in read_csv(MODEL_DIR / "demand_daily.csv"):
        date = row["date"]
        year = date[:4]
        value = float(row["grid_export_mwh"])
        grid_export_by_year[year] += value
        grid_export_by_year_date[year][date] = value

    reconciled_injection_by_year = defaultdict(float)
    reconciled_offtake_by_year = defaultdict(float)
    reconciled_days_by_year = defaultdict(int)
    if RECONCILED_PATH.exists():
        for row in read_csv(RECONCILED_PATH):
            date = row["date"]
            year = date[:4]
            reconciled_offtake_by_year[year] += float(row["reconciled_offtake_mwh"])
            reconciled_injection_by_year[year] += float(row["reconciled_injection_mwh"])
            reconciled_days_by_year[year] += 1

    years = sorted(
        set(gen_by_year)
        | set(grid_export_by_year)
        | set(reconciled_injection_by_year)
        | set(reconciled_offtake_by_year)
    )
    summary = {"years": {}}

    for year in years:
        generation = gen_by_year.get(year, 0.0)
        grid_export = grid_export_by_year.get(year, 0.0)
        reconciled_injection = reconciled_injection_by_year.get(year, 0.0)
        reconciled_offtake = reconciled_offtake_by_year.get(year, 0.0)

        dates = sorted(set(gen_by_year_date[year]) & set(grid_export_by_year_date[year]))
        daily_gaps = [
            gen_by_year_date[year][d] - grid_export_by_year_date[year][d]
            for d in dates
        ]

        year_data = {
            "generation_md_mwh": round(generation, 3),
            "grid_export_mwh": round(grid_export, 3),
            "generation_md_minus_grid_export_mwh": round(generation - grid_export, 3),
            "generation_md_to_grid_export_ratio": ratio(generation, grid_export),
            "generation_grid_export_overlap_days": len(dates),
            "mean_daily_generation_minus_grid_export_mwh": (
                round(sum(daily_gaps) / len(daily_gaps), 3) if daily_gaps else None
            ),
            "min_daily_generation_minus_grid_export_mwh": (
                round(min(daily_gaps), 3) if daily_gaps else None
            ),
            "max_daily_generation_minus_grid_export_mwh": (
                round(max(daily_gaps), 3) if daily_gaps else None
            ),
            "generation_by_fuel_mwh": {
                fuel: round(value, 3)
                for fuel, value in sorted(gen_by_year_fuel[year].items())
            },
        }

        if reconciled_days_by_year.get(year, 0):
            year_data.update(
                {
                    "reconciled_days": reconciled_days_by_year[year],
                    "reconciled_injection_mwh": round(reconciled_injection, 3),
                    "reconciled_offtake_mwh": round(reconciled_offtake, 3),
                    "reconciled_injection_minus_offtake_mwh": round(
                        reconciled_injection - reconciled_offtake, 3
                    ),
                    "reconciled_injection_to_offtake_ratio": ratio(
                        reconciled_injection, reconciled_offtake
                    ),
                    "generation_md_minus_reconciled_injection_mwh": round(
                        generation - reconciled_injection, 3
                    ),
                    "generation_md_vs_reconciled_injection_pct": pct_difference(
                        generation, reconciled_injection
                    ),
                    "grid_export_minus_reconciled_offtake_mwh": round(
                        grid_export - reconciled_offtake, 3
                    ),
                    "grid_export_vs_reconciled_offtake_pct": pct_difference(
                        grid_export, reconciled_offtake
                    ),
                }
            )

        summary["years"][year] = year_data

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {SUMMARY_PATH}")

    for year in years:
        data = summary["years"][year]
        message = (
            f"{year}: Generation_MD={data['generation_md_mwh'] / 1_000_000:.3f} TWh, "
            f"grid_export={data['grid_export_mwh'] / 1_000_000:.3f} TWh"
        )
        if data.get("reconciled_days"):
            message += (
                f", reconciled_injection={data['reconciled_injection_mwh'] / 1_000_000:.3f} TWh, "
                f"reconciled_offtake={data['reconciled_offtake_mwh'] / 1_000_000:.3f} TWh, "
                f"Generation_MD vs injection={data['generation_md_vs_reconciled_injection_pct']:+.3f}%, "
                f"grid_export vs offtake={data['grid_export_vs_reconciled_offtake_pct']:+.3f}%"
            )
        print(message)


if __name__ == "__main__":
    main()
