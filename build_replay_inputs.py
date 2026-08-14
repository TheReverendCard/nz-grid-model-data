from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FUTURE_DEMAND = Path("data/model/future_demand_scenarios.json")
RECONCILED = Path("data/wholesale/model/reconciled_daily.csv")
INFLOWS = Path("data/hydro/model/inflows_daily.csv")
STORAGE = Path("data/hydro/model/storage_daily.csv")
OUT_DEMAND = Path("data/model/replay_demand_2030_daily.csv")
OUT_HYDRO = Path("data/model/hydro_year_summary.csv")
OUT_SUMMARY = Path("data/model/replay_input_summary.json")

TARGET_YEAR = 2030
BASE_SHAPE_YEAR = 2024


def read_2024_demand_shape() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with RECONCILED.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row["date"].startswith(f"{BASE_SHAPE_YEAR}-"):
                continue
            rows.append({"date": row["date"], "mwh": float(row["reconciled_offtake_mwh"])})
    if len(rows) != 366:
        raise RuntimeError(f"Expected 366 demand rows for 2024, got {len(rows)}")
    total = sum(float(r["mwh"]) for r in rows)
    for i, row in enumerate(rows, start=1):
        dt = datetime.strptime(str(row["date"]), "%Y-%m-%d").date()
        row["shape_day"] = i
        row["month_day"] = dt.strftime("%m-%d")
        row["weight"] = float(row["mwh"]) / total
    return rows


def write_replay_demand(shape: list[dict[str, object]], future: dict[str, object]) -> dict[str, float]:
    OUT_DEMAND.parent.mkdir(parents=True, exist_ok=True)
    annual_totals: dict[str, float] = {}
    with OUT_DEMAND.open("w", encoding="utf-8", newline="") as handle:
        fields = ["scenario", "target_year", "shape_year", "shape_day", "month_day", "daily_demand_mwh"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scenario, entry in future["scenarios"].items():
            target_mwh = float(entry["years"][str(TARGET_YEAR)]["calibrated_underlying_demand_mwh"])
            written = 0.0
            for row in shape:
                daily = target_mwh * float(row["weight"])
                written += daily
                writer.writerow(
                    {
                        "scenario": scenario,
                        "target_year": TARGET_YEAR,
                        "shape_year": BASE_SHAPE_YEAR,
                        "shape_day": row["shape_day"],
                        "month_day": row["month_day"],
                        "daily_demand_mwh": round(daily, 6),
                    }
                )
            annual_totals[scenario] = written
    print(f"Wrote {OUT_DEMAND}")
    return annual_totals


def complete_years_by_counts(path: Path, date_field: str = "date") -> dict[int, int]:
    days: defaultdict[int, set[str]] = defaultdict(set)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row[date_field]
            year = int(date[:4])
            days[year].add(date)
    return {year: len(values) for year, values in days.items()}


def hydro_year_summary() -> list[dict[str, object]]:
    inflow_counts = complete_years_by_counts(INFLOWS)
    storage_counts = complete_years_by_counts(STORAGE)

    inflow: defaultdict[tuple[int, str], float] = defaultdict(float)
    with INFLOWS.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            year = int(row["date"][:4])
            value = row.get("volume_mm3_day")
            if value in (None, ""):
                continue
            island = row["island_code"]
            v = float(value)
            inflow[(year, island)] += v
            inflow[(year, "NZ")] += v

    daily_storage: defaultdict[tuple[str, str], float] = defaultdict(float)
    with STORAGE.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            value = row.get("total_storage_mm3")
            if value in (None, ""):
                continue
            date = row["date"]
            island = row["island_code"]
            v = float(value)
            daily_storage[(date, island)] += v
            daily_storage[(date, "NZ")] += v

    storage_stats: dict[tuple[int, str], dict[str, object]] = {}
    for (date, island), value in daily_storage.items():
        year = int(date[:4])
        key = (year, island)
        item = storage_stats.setdefault(key, {"min": value, "max": value, "min_date": date, "max_date": date})
        if value < float(item["min"]):
            item["min"] = value
            item["min_date"] = date
        if value > float(item["max"]):
            item["max"] = value
            item["max_date"] = date

    years = sorted(set(year for year, _ in inflow) | set(year for year, _ in storage_stats))
    rows: list[dict[str, object]] = []
    for year in years:
        for island in ("NI", "SI", "NZ"):
            stat = storage_stats.get((year, island), {})
            rows.append(
                {
                    "year": year,
                    "island": island,
                    "inflow_volume_mm3": round(inflow.get((year, island), 0.0), 6),
                    "storage_min_mm3": round(float(stat["min"]), 6) if stat else "",
                    "storage_min_date": stat.get("min_date", ""),
                    "storage_max_mm3": round(float(stat["max"]), 6) if stat else "",
                    "storage_max_date": stat.get("max_date", ""),
                    "inflow_days_present": inflow_counts.get(year, 0),
                    "storage_days_present": storage_counts.get(year, 0),
                    "calendar_year_complete_inflows": inflow_counts.get(year, 0) in (365, 366),
                    "calendar_year_complete_storage": storage_counts.get(year, 0) in (365, 366),
                }
            )
    with OUT_HYDRO.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {OUT_HYDRO} ({len(rows)} rows)")
    return rows


def main() -> None:
    future = json.loads(FUTURE_DEMAND.read_text(encoding="utf-8"))
    shape = read_2024_demand_shape()
    annual_totals = write_replay_demand(shape, future)
    hydro_rows = hydro_year_summary()

    complete_inflow_years = sorted(
        {int(r["year"]) for r in hydro_rows if r["island"] == "NZ" and r["calendar_year_complete_inflows"]}
    )
    complete_storage_years = sorted(
        {int(r["year"]) for r in hydro_rows if r["island"] == "NZ" and r["calendar_year_complete_storage"]}
    )
    output = {
        "target_year": TARGET_YEAR,
        "demand_shape_year": BASE_SHAPE_YEAR,
        "demand_method": "Scale the observed 2024 reconciled daily demand shape to each calibrated EDGS annual underlying-demand target. This is a first replay scaffold; future BTM PV and load-shape changes will be represented separately.",
        "annual_demand_check_mwh": {k: round(v, 3) for k, v in annual_totals.items()},
        "hydro": {
            "headwater_inflow_source": str(INFLOWS),
            "storage_source": str(STORAGE),
            "inflow_series_start": complete_inflow_years[0] if complete_inflow_years else None,
            "inflow_series_last_complete_year": complete_inflow_years[-1] if complete_inflow_years else None,
            "storage_series_start": complete_storage_years[0] if complete_storage_years else None,
            "storage_series_last_complete_year": complete_storage_years[-1] if complete_storage_years else None,
            "warning": "Headwater inflow volume is a hydrology index, not directly MWh. Conversion to hydroelectric energy requires cascade topology, plant factors, release constraints and reservoir dispatch.",
        },
        "next_step": "Construct hydro cascade/energy conversion and dispatch, then combine weather-dependent wind/solar profiles and thermal backstop to replay each historical hydrological year.",
    }
    OUT_SUMMARY.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
