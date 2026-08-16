from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

YEARS = (2024, 2025, 2026)
GENERATION_DAILY = Path("data/wholesale/model/generation_daily.csv")
OUTPUT_JSON = Path("data/distributed_generation/model/solar_generation_candidates_by_year.json")
OUTPUT_CSV = Path("data/distributed_generation/model/solar_generation_candidates_by_year.csv")


def is_solar(row: dict[str, str]) -> bool:
    fuel = (row.get("fuel_code") or "").strip().lower()
    tech = (row.get("tech_code") or "").strip().lower()
    return fuel in {"solar", "sol"} or tech in {"solar", "sol", "pv"}


def main() -> None:
    by_key: defaultdict[tuple[int, str, str, str, str, str], float] = defaultdict(float)
    days: defaultdict[tuple[int, str, str, str, str, str], set[str]] = defaultdict(set)

    with GENERATION_DAILY.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["date"]
            if not is_solar(row):
                continue
            try:
                year = int(date[:4])
            except (TypeError, ValueError):
                continue
            if year not in YEARS:
                continue
            key = (
                year,
                row.get("site_code", ""),
                row.get("poc_code", ""),
                row.get("network_code", ""),
                row.get("generator_code", ""),
                row.get("fuel_code", ""),
            )
            by_key[key] += float(row["generation_mwh"])
            days[key].add(date)

    rows = []
    for key, mwh in by_key.items():
        year, site, poc, network, generator, fuel = key
        rows.append({
            "year": year,
            "site_code": site,
            "poc_code": poc,
            "network_code": network,
            "generator_code": generator,
            "fuel_code": fuel,
            "generation_mwh": round(mwh, 6),
            "days_with_generation_rows": len(days[key]),
        })

    rows.sort(key=lambda r: (r["year"], -r["generation_mwh"], r["generator_code"]))

    by_year = {}
    for year in YEARS:
        year_rows = [r for r in rows if r["year"] == year]
        by_year[str(year)] = {
            "total_solar_generation_mwh": round(sum(r["generation_mwh"] for r in year_rows), 6),
            "candidate_count": len(year_rows),
            "candidates": year_rows,
        }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({
        "years": by_year,
        "note": (
            "Use only sites with independently verified capacity and sufficient operating coverage. "
            "For rooftop-yield calibration, prefer DC MWp when comparing against PV-array yield; "
            "retain AC/export capacity separately because utility farms may have materially different DC:AC ratios."
        ),
    }, indent=2) + "\n", encoding="utf-8")

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "year", "site_code", "poc_code", "network_code", "generator_code",
            "fuel_code", "generation_mwh", "days_with_generation_rows"
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {OUTPUT_JSON} and {OUTPUT_CSV} ({len(rows)} solar site-years)")
    for year in YEARS:
        year_rows = [r for r in rows if r["year"] == year]
        print(f"{year}: {len(year_rows)} candidates, {sum(r['generation_mwh'] for r in year_rows):.3f} MWh")
        for row in year_rows[:10]:
            print(row)


if __name__ == "__main__":
    main()
