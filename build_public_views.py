from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

PUBLIC_DIR = Path("data/public")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, object]]) -> bool:
    if not rows:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    content = buffer.getvalue()
    if path.exists() and path.read_text(encoding="utf-8") == content:
        print(f"Unchanged {path}")
        return False
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path} ({len(rows)} rows)")
    return True


def fuel_group(fuel: str) -> str:
    value = (fuel or "").lower()
    if "hydro" in value or "water" in value:
        return "Hydro"
    if "geo" in value:
        return "Geothermal"
    if "wind" in value:
        return "Wind"
    if "solar" in value or value == "sol":
        return "Solar"
    if "coal" in value:
        return "Coal"
    if "gas" in value:
        return "Gas"
    if "diesel" in value or "liquid" in value or "oil" in value:
        return "Liquid fuel"
    if "bio" in value:
        return "Biomass/biogas"
    return fuel or "Other/unknown"


def build_generation_views() -> None:
    grouped: defaultdict[tuple[str, str], float] = defaultdict(float)
    for row in read_rows(Path("data/wholesale/model/generation_daily.csv")):
        grouped[(row["date"], fuel_group(row["fuel_code"]))] += float(row["generation_mwh"])

    mix = [
        {"date": date, "fuel": fuel, "generation_mwh": round(value, 3)}
        for (date, fuel), value in sorted(grouped.items())
    ]
    write_rows(PUBLIC_DIR / "generation_mix_daily.csv", mix)

    totals: defaultdict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    renewable_groups = {"Hydro", "Geothermal", "Wind", "Solar", "Biomass/biogas"}
    for (date, fuel), value in grouped.items():
        totals[date][0] += value
        if fuel in renewable_groups:
            totals[date][1] += value
    renewable = [
        {
            "date": date,
            "total_generation_mwh": round(total, 3),
            "renewable_generation_mwh": round(renewable_value, 3),
            "renewable_pct": round(100.0 * renewable_value / total, 3) if total else "",
        }
        for date, (total, renewable_value) in sorted(totals.items())
    ]
    write_rows(PUBLIC_DIR / "renewable_share_daily.csv", renewable)


def build_demand_view() -> None:
    source = Path("data/wholesale/model/demand_daily.csv")
    if source.exists():
        write_rows(PUBLIC_DIR / "demand_daily.csv", read_rows(source))


def build_hydro_view() -> None:
    storage: defaultdict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for row in read_rows(Path("data/hydro/model/storage_daily.csv")):
        date = row["date"]
        storage[date][0] += float(row.get("active_storage_mm3") or 0)
        storage[date][1] += float(row.get("contingent_storage_mm3") or 0)
        storage[date][2] += float(row.get("total_usable_storage_mm3") or 0)
    rows = [
        {
            "date": date,
            "active_storage_mm3": round(values[0], 3),
            "contingent_storage_mm3": round(values[1], 3),
            "total_usable_storage_mm3": round(values[2], 3),
        }
        for date, values in sorted(storage.items())
    ]
    write_rows(PUBLIC_DIR / "hydro_storage_daily.csv", rows)


def build_solar_view() -> None:
    all_rows = {
        row["month_end"]: row
        for row in read_rows(Path("data/distributed_generation/model/national_solar_all_monthly.csv"))
    }
    residential_rows = {
        row["month_end"]: row
        for row in read_rows(Path("data/distributed_generation/model/national_residential_solar_monthly.csv"))
    }
    battery_path = Path("data/distributed_generation/model/national_solar_battery_attachment_monthly.csv")
    battery_rows = {row["month_end"]: row for row in read_rows(battery_path)} if battery_path.exists() else {}

    rows: list[dict[str, object]] = []
    for month in sorted(all_rows):
        all_row = all_rows[month]
        residential = residential_rows.get(month, {})
        battery = battery_rows.get(month, {})
        rows.append(
            {
                "month_end": month,
                "all_solar_icps": all_row.get("icp_count", ""),
                "all_solar_installed_capacity_mw": all_row.get("installed_capacity_mw", ""),
                "new_solar_installations": all_row.get("new_installations", ""),
                "residential_solar_icps": residential.get("icp_count", ""),
                "residential_solar_installed_capacity_mw": residential.get("installed_capacity_mw", ""),
                "solar_plus_battery_icps": battery.get("solar_plus_battery_icps", ""),
                "solar_plus_battery_registered_capacity_mw": battery.get("solar_plus_battery_registered_capacity_mw", ""),
                "battery_attachment_share_of_solar_pct": battery.get("battery_attachment_share_of_solar_pct", ""),
                "category_reliability": battery.get("category_reliability", ""),
            }
        )
    write_rows(PUBLIC_DIR / "solar_installations_monthly.csv", rows)


def build_price_join() -> None:
    price_path = PUBLIC_DIR / "wholesale_prices_daily.csv"
    renewable_path = PUBLIC_DIR / "renewable_share_daily.csv"
    if not price_path.exists() or not renewable_path.exists():
        return
    prices = {row["date"]: row for row in read_rows(price_path)}
    renewable = {row["date"]: row for row in read_rows(renewable_path)}
    rows = [
        {
            "date": date,
            "renewable_pct": renewable[date]["renewable_pct"],
            "reference_mean_nzd_mwh": prices[date]["reference_mean_nzd_mwh"],
        }
        for date in sorted(set(prices) & set(renewable))
    ]
    write_rows(PUBLIC_DIR / "renewables_vs_price_daily.csv", rows)


def write_manifest() -> None:
    payload = {
        "datasets": sorted(str(path) for path in PUBLIC_DIR.glob("*.csv")),
        "note": "Observed/public-data derivatives only. Scenario-model outputs live outside data/public.",
        "builders": {
            "generation_and_renewable_share": "data/wholesale/model/generation_daily.csv",
            "demand": "data/wholesale/model/demand_daily.csv",
            "hydro_storage": "data/hydro/model/storage_daily.csv",
            "solar": "data/distributed_generation/model national monthly tables",
            "wholesale_prices": "update_prices.py",
        },
    }
    text = json.dumps(payload, indent=2) + "\n"
    path = PUBLIC_DIR / "manifest.json"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
        return
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")


def main() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    build_generation_views()
    build_demand_view()
    build_hydro_view()
    build_solar_view()
    build_price_join()
    write_manifest()


if __name__ == "__main__":
    main()
