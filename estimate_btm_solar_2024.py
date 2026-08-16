from __future__ import annotations

import csv
import json
import calendar
from pathlib import Path

YEAR = 2024
MONTHLY = Path("data/distributed_generation/model/national_residential_solar_monthly.csv")
ASSUMPTIONS = Path("model/btm_solar_assumptions.json")
OUT = Path("data/distributed_generation/model/btm_solar_estimate_2024.json")


def load_monthly_capacity() -> dict[str, float]:
    rows: dict[str, float] = {}
    with MONTHLY.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows[row["month_end"]] = float(row["installed_capacity_mw"])
    return rows


def weighted_average_capacity_mw(monthly: dict[str, float]) -> tuple[float, list[dict[str, float]]]:
    previous = monthly[f"{YEAR - 1}-12-31"]
    weighted_sum = 0.0
    total_days = 0
    detail = []

    for month in range(1, 13):
        last_day = calendar.monthrange(YEAR, month)[1]
        month_end = f"{YEAR}-{month:02d}-{last_day:02d}"
        current = monthly[month_end]
        average = (previous + current) / 2.0
        days = last_day
        weighted_sum += average * days
        total_days += days
        detail.append(
            {
                "month": f"{YEAR}-{month:02d}",
                "start_capacity_mw": round(previous, 6),
                "end_capacity_mw": round(current, 6),
                "average_capacity_mw": round(average, 6),
                "days": days,
            }
        )
        previous = current

    return weighted_sum / total_days, detail


def main() -> None:
    monthly = load_monthly_capacity()
    assumptions = json.loads(ASSUMPTIONS.read_text(encoding="utf-8"))
    average_capacity_mw, capacity_detail = weighted_average_capacity_mw(monthly)

    yield_cases = assumptions["yield_scenarios_kwh_per_kwp_year"]
    retention_cases = assumptions["retained_behind_meter_fraction"]

    scenarios = {}
    for name in ("low", "central", "high"):
        yield_kwh_per_kwp = float(yield_cases[name])
        retained_fraction = float(retention_cases[name])
        # 1 MW of PV producing 1 kWh/kWp-year yields 1 MWh/year.
        gross_mwh = average_capacity_mw * yield_kwh_per_kwp
        retained_mwh = gross_mwh * retained_fraction
        export_mwh = gross_mwh - retained_mwh
        scenarios[name] = {
            "specific_yield_kwh_per_kwp_year": yield_kwh_per_kwp,
            "retained_behind_meter_fraction": retained_fraction,
            "gross_residential_pv_generation_mwh": round(gross_mwh, 3),
            "retained_behind_meter_pv_mwh": round(retained_mwh, 3),
            "estimated_grid_export_mwh": round(export_mwh, 3),
        }

    output = {
        "model_year": YEAR,
        "method": "Monthly installed residential solar capacity, linearly averaged between month-end observations, multiplied by scenario-specific annual PV yield.",
        "capacity": {
            "year_start_mw": monthly[f"{YEAR - 1}-12-31"],
            "year_end_mw": monthly[f"{YEAR}-12-31"],
            "capacity_weighted_annual_average_mw": round(average_capacity_mw, 6),
            "monthly": capacity_detail,
        },
        "scenarios": scenarios,
        "accounting": assumptions["notes"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    for name, row in scenarios.items():
        print(
            f"{name}: gross={row['gross_residential_pv_generation_mwh'] / 1000:.1f} GWh, "
            f"BTM retained={row['retained_behind_meter_pv_mwh'] / 1000:.1f} GWh, "
            f"export={row['estimated_grid_export_mwh'] / 1000:.1f} GWh"
        )


if __name__ == "__main__":
    main()
