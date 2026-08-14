from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data/distributed_generation")
MODEL_DIR = DATA_DIR / "model"
ALL_TREND = DATA_DIR / "installed_dg_trends_solar_all.csv"
RES_TREND = DATA_DIR / "installed_dg_trends_solar_residential.csv"
REGION = DATA_DIR / "solar_installations_by_region.csv"
OUT_ALL_TREND = MODEL_DIR / "national_solar_all_monthly.csv"
OUT_RES_TREND = MODEL_DIR / "national_residential_solar_monthly.csv"
OUT_REGIONS = MODEL_DIR / "current_residential_solar_by_region.csv"
OUT_SUMMARY = MODEL_DIR / "distributed_solar_summary.json"


def read_guehmt(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.lower().startswith("month end,")), None)
    if header_index is None:
        raise RuntimeError(f"Could not locate GUEHMT data header in {path}")
    return list(csv.DictReader(io.StringIO("\n".join(lines[header_index:]))))


def normalize_trend(path: Path, output: Path) -> list[dict[str, object]]:
    rows = []
    for row in read_guehmt(path):
        if not row.get("Month end"):
            continue
        date = datetime.strptime(row["Month end"], "%d/%m/%Y").date().isoformat()
        rows.append(
            {
                "month_end": date,
                "icp_count": int(float(row["ICP count"])),
                "icp_uptake_rate_pct": float(row["ICP uptake rate (%)"]),
                "installed_capacity_mw": float(row["Total capacity installed (MW)"]),
                "average_capacity_kw": float(row["Avg. capacity installed (kW)"]),
                "new_installations": int(float(row["ICP count - new installations"])),
                "average_new_install_capacity_kw": float(row["Avg. capacity - new installations (kW)"]),
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {output} ({len(rows)} rows)")
    return rows


def normalize_regions() -> tuple[list[dict[str, object]], dict[str, dict[str, float]]]:
    regional_rows: list[dict[str, object]] = []
    national: dict[str, dict[str, float]] = {}
    with REGION.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            segment = row["MarketSegment"]
            if row["RegionType"] == "NZ":
                national[segment] = {
                    "icps": int(row["ICPs"]),
                    "average_capacity_kw": float(row["GenerationCapacityKilowattsAvg"]),
                    "capacity_mw": float(row["GenerationCapacityKilowattsSum"]) / 1000.0,
                }
            if row["RegionType"] == "REG_COUNCIL" and segment == "Res":
                regional_rows.append(
                    {
                        "region_code": row["RegionCode"],
                        "region": row["Region"],
                        "residential_solar_icps": int(row["ICPs"]),
                        "average_capacity_kw": float(row["GenerationCapacityKilowattsAvg"]),
                        "installed_capacity_mw": round(float(row["GenerationCapacityKilowattsSum"]) / 1000.0, 6),
                    }
                )
    regional_rows.sort(key=lambda x: str(x["region"]))
    OUT_REGIONS.parent.mkdir(parents=True, exist_ok=True)
    with OUT_REGIONS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(regional_rows[0].keys()))
        writer.writeheader()
        writer.writerows(regional_rows)
    print(f"Wrote {OUT_REGIONS} ({len(regional_rows)} regions)")
    return regional_rows, national


def latest_on_or_before(rows: list[dict[str, object]], date: str) -> dict[str, object] | None:
    candidates = [row for row in rows if str(row["month_end"]) <= date]
    return candidates[-1] if candidates else None


def main() -> None:
    all_rows = normalize_trend(ALL_TREND, OUT_ALL_TREND)
    res_rows = normalize_trend(RES_TREND, OUT_RES_TREND)
    regional_rows, national_snapshot = normalize_regions()

    res_2024 = latest_on_or_before(res_rows, "2024-12-31")
    all_2024 = latest_on_or_before(all_rows, "2024-12-31")
    latest_res = res_rows[-1]
    latest_all = all_rows[-1]

    summary = {
        "historical": {
            "2024_year_end": {
                "residential_solar": res_2024,
                "all_solar": all_2024,
            },
            "latest_trend": {
                "residential_solar": latest_res,
                "all_solar": latest_all,
            },
        },
        "current_registry_snapshot": {
            "residential": national_snapshot.get("Res"),
            "business": national_snapshot.get("Bus"),
            "all": national_snapshot.get("All"),
            "regional_residential_capacity_mw_sum": round(sum(float(r["installed_capacity_mw"]) for r in regional_rows), 6),
            "regional_residential_icps_sum": sum(int(r["residential_solar_icps"]) for r in regional_rows),
        },
        "model_use": {
            "behind_meter_starting_population": "Residential solar ICPs are the primary BTM population. Business solar is kept separate because the category includes large installations.",
            "battery_export_assumption": "Zero by default except explicit VPP/export scenarios.",
            "gross_generation_method": "Apply regional observed/representative solar yield (kWh per installed kW) to regional residential installed capacity.",
            "self_consumption_method": "Split gross PV among direct load, battery charging, and residual export using explicit assumptions/sensitivity ranges.",
        },
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
