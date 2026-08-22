from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data/distributed_generation")
MODEL_DIR = DATA_DIR / "model"
ALL_TREND = DATA_DIR / "installed_dg_trends_solar_all.csv"
SOLAR_ONLY_TREND = DATA_DIR / "installed_dg_trends_solar_only.csv"
SOLAR_BATTERY_TREND = DATA_DIR / "installed_dg_trends_solar_with_battery.csv"
RES_TREND = DATA_DIR / "installed_dg_trends_solar_residential.csv"
REGION = DATA_DIR / "solar_installations_by_region.csv"
OUT_ALL_TREND = MODEL_DIR / "national_solar_all_monthly.csv"
OUT_SOLAR_ONLY_TREND = MODEL_DIR / "national_solar_only_monthly.csv"
OUT_SOLAR_BATTERY_TREND = MODEL_DIR / "national_solar_with_battery_monthly.csv"
OUT_BATTERY_ATTACHMENT = MODEL_DIR / "national_solar_battery_attachment_monthly.csv"
OUT_RES_TREND = MODEL_DIR / "national_residential_solar_monthly.csv"
OUT_REGIONS = MODEL_DIR / "current_residential_solar_by_region.csv"
OUT_SUMMARY = MODEL_DIR / "distributed_solar_summary.json"

BATTERY_CATEGORY_RELIABLE_FROM = "2023-11-01"


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


def build_battery_attachment(
    all_rows: list[dict[str, object]],
    solar_only_rows: list[dict[str, object]],
    explicit_battery_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    solar_only_by_month = {str(row["month_end"]): row for row in solar_only_rows}
    explicit_by_month = {str(row["month_end"]): row for row in explicit_battery_rows}
    rows: list[dict[str, object]] = []

    for all_row in all_rows:
        month = str(all_row["month_end"])
        solar_only = solar_only_by_month.get(month)
        explicit = explicit_by_month.get(month)
        if solar_only is None or explicit is None:
            continue

        all_count = int(all_row["icp_count"])
        only_count = int(solar_only["icp_count"])
        explicit_count = int(explicit["icp_count"])
        subtraction_count = max(0, all_count - only_count)
        all_new = int(all_row["new_installations"])
        only_new = int(solar_only["new_installations"])
        explicit_new = int(explicit["new_installations"])
        subtraction_new = max(0, all_new - only_new)

        rows.append(
            {
                "month_end": month,
                "all_solar_icps": all_count,
                "solar_only_icps": only_count,
                "solar_plus_battery_icps": explicit_count,
                "solar_plus_battery_icps_subtraction_check": subtraction_count,
                "battery_attachment_share_of_solar_pct": round(explicit_count / all_count * 100.0, 5) if all_count else 0.0,
                "all_new_solar_installations": all_new,
                "new_solar_only_installations": only_new,
                "new_solar_plus_battery_installations": explicit_new,
                "new_solar_plus_battery_installations_subtraction_check": subtraction_new,
                "battery_attachment_share_of_new_solar_pct": round(explicit_new / all_new * 100.0, 5) if all_new else 0.0,
                "solar_plus_battery_registered_capacity_mw": float(explicit["installed_capacity_mw"]),
                "solar_plus_battery_average_registered_kw": float(explicit["average_capacity_kw"]),
                "solar_plus_battery_average_new_registered_kw": float(explicit["average_new_install_capacity_kw"]),
                "category_reliability": "post_2023_11_registry_categories" if month >= BATTERY_CATEGORY_RELIABLE_FROM else "legacy_category_caution",
            }
        )

    OUT_BATTERY_ATTACHMENT.parent.mkdir(parents=True, exist_ok=True)
    with OUT_BATTERY_ATTACHMENT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {OUT_BATTERY_ATTACHMENT} ({len(rows)} rows)")
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
    solar_only_rows = normalize_trend(SOLAR_ONLY_TREND, OUT_SOLAR_ONLY_TREND)
    explicit_battery_rows = normalize_trend(SOLAR_BATTERY_TREND, OUT_SOLAR_BATTERY_TREND)
    battery_rows = build_battery_attachment(all_rows, solar_only_rows, explicit_battery_rows)
    res_rows = normalize_trend(RES_TREND, OUT_RES_TREND)
    regional_rows, national_snapshot = normalize_regions()

    res_2024 = latest_on_or_before(res_rows, "2024-12-31")
    all_2024 = latest_on_or_before(all_rows, "2024-12-31")
    latest_res = res_rows[-1]
    latest_all = all_rows[-1]
    latest_battery = battery_rows[-1] if battery_rows else None

    summary = {
        "historical": {
            "2024_year_end": {
                "residential_solar": res_2024,
                "all_solar": all_2024,
            },
            "latest_trend": {
                "residential_solar": latest_res,
                "all_solar": latest_all,
                "solar_battery_attachment": latest_battery,
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
            "distributed_solar_scope": "Future distributed-solar scenarios use <25 kW ICP adoption plus a separate 25 kW to <1 MW capacity component; >=1 MW is treated as utility-scale.",
            "battery_attachment_observation": "Primary battery counts and connection-capacity figures use the explicit EA Solar (with battery) GUEHMT series. solar_all minus Solar-only is retained only as an audit cross-check. Treat pre-November-2023 category history cautiously because registry recategorisation is not reliably backdated.",
            "battery_power_interpretation": "Solar-with-battery registered generation capacity is the lesser of summed generating capacity or inverter/injection capacity. It is useful as an observed connection-power ceiling, especially for peak-support modelling, but is not battery-only power and is not storage energy capacity.",
            "battery_energy_duration": "Until public aggregated registry kWh data become available, model battery energy conservatively with 1.0 h low, 1.5 h central, and 2.0 h high duration assumptions. Replace these where defensible observed EA battery storage data are available.",
            "gross_generation_method": "Apply regional observed/representative solar yield (kWh per installed kW) to distributed installed capacity.",
        },
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_SUMMARY}")
    if latest_battery:
        print(
            "Latest explicit solar+battery series: "
            f"{latest_battery['solar_plus_battery_icps']:,} ICPs, "
            f"{latest_battery['battery_attachment_share_of_solar_pct']:.1f}% of solar ICPs, "
            f"{latest_battery['solar_plus_battery_registered_capacity_mw']:.1f} MW registered connection capacity, "
            f"{latest_battery['battery_attachment_share_of_new_solar_pct']:.1f}% of new solar installs."
        )


if __name__ == "__main__":
    main()
