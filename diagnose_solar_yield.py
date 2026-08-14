from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

YEAR = 2024
GENERATION_DAILY = Path("data/wholesale/model/generation_daily.csv")
OUTPUT_JSON = Path("data/distributed_generation/model/solar_generation_candidates_2024.json")
OUTPUT_CSV = Path("data/distributed_generation/model/solar_generation_candidates_2024.csv")


def is_solar(row: dict[str, str]) -> bool:
    fuel = (row.get("fuel_code") or "").strip().lower()
    tech = (row.get("tech_code") or "").strip().lower()
    return fuel in {"solar", "sol"} or tech in {"solar", "sol", "pv"}


def main() -> None:
    by_key: defaultdict[tuple[str, str, str, str, str], float] = defaultdict(float)
    days: defaultdict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)

    with GENERATION_DAILY.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["date"]
            if not date.startswith(f"{YEAR}-") or not is_solar(row):
                continue
            key = (
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
        site, poc, network, generator, fuel = key
        rows.append({
            "site_code": site,
            "poc_code": poc,
            "network_code": network,
            "generator_code": generator,
            "fuel_code": fuel,
            "generation_mwh_2024": round(mwh, 6),
            "days_with_generation_rows": len(days[key]),
        })

    rows.sort(key=lambda r: r["generation_mwh_2024"], reverse=True)
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps({
        "year": YEAR,
        "total_solar_generation_mwh": round(sum(r["generation_mwh_2024"] for r in rows), 6),
        "candidate_count": len(rows),
        "candidates": rows,
        "note": "Use only sites with independently verified AC/export capacity and sufficient operating coverage for kWh/kW calibration."
    }, indent=2) + "\n", encoding="utf-8")

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["site_code", "poc_code", "network_code", "generator_code", "fuel_code", "generation_mwh_2024", "days_with_generation_rows"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {OUTPUT_JSON} and {OUTPUT_CSV} ({len(rows)} solar candidates)")
    for row in rows[:10]:
        print(row)


if __name__ == "__main__":
    main()
