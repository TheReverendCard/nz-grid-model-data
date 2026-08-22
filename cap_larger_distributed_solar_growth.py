from __future__ import annotations

import csv
import json
from pathlib import Path

CSV_PATH = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.csv")
JSON_PATH = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.json")
GROWTH_END_YEAR = 2035
SCENARIOS = ("low_10pct", "high_30pct")


def main() -> None:
    with CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {CSV_PATH}")

    cap_row = next(
        (row for row in reversed(rows) if row["month_end"].startswith(f"{GROWTH_END_YEAR}-")),
        None,
    )
    if cap_row is None:
        raise RuntimeError(f"No {GROWTH_END_YEAR} row in {CSV_PATH}")

    cap_mw = float(cap_row["larger_distributed_25kw_to_lt_1mw_capacity_mw"])
    changed = False
    for row in rows:
        year = int(row["month_end"][:4])
        if year <= GROWTH_END_YEAR:
            continue
        if float(row["larger_distributed_25kw_to_lt_1mw_capacity_mw"]) != cap_mw:
            changed = True
        row["larger_distributed_25kw_to_lt_1mw_capacity_mw"] = f"{cap_mw:.3f}"
        for scenario in SCENARIOS:
            small_mw = float(row[f"{scenario}_small_capacity_mw"])
            row[f"{scenario}_distributed_sub_1mw_capacity_mw"] = f"{small_mw + cap_mw:.3f}"

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    for year, row in payload.get("year_end", {}).items():
        if int(year) <= GROWTH_END_YEAR:
            continue
        row["larger_distributed_25kw_to_lt_1mw_capacity_mw"] = round(cap_mw, 3)
        for scenario in SCENARIOS:
            small_mw = float(row[f"{scenario}_small_capacity_mw"])
            row[f"{scenario}_distributed_sub_1mw_capacity_mw"] = round(small_mw + cap_mw, 3)

    payload.setdefault("larger_distributed_growth", {})["growth_end_year"] = GROWTH_END_YEAR
    payload["larger_distributed_growth"]["post_growth_end_policy"] = "hold_capacity_flat"
    payload.setdefault("method", {})["larger_distributed_visual_note"] = (
        f"25 kW-<1 MW projection uses the selected provisional growth rate through {GROWTH_END_YEAR}, "
        "then holds capacity flat until new observed size-bucket history supports a revised trajectory."
    )
    JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    state = "updated" if changed else "already capped"
    print(f"Larger distributed solar {state}: {cap_mw:.1f} MW from {GROWTH_END_YEAR} onward")


if __name__ == "__main__":
    main()
