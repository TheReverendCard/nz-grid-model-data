from __future__ import annotations

import csv
import json
from pathlib import Path

ASSETS = Path("data/model/hydro_assets_current.csv")
TOPOLOGY = Path("model/hydro_topology.json")
OUTPUT = Path("data/model/hydro_water_value_diagnostic.json")

MWH_PER_MM3_PER_MW_PER_CUMEC = 1_000_000.0 / 3_600.0


def main() -> None:
    with ASSETS.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assets = {(row["plant_group"], row["site_code"]): row for row in rows}
    by_code: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_code.setdefault(row["site_code"], []).append(row)

    topology = json.loads(TOPOLOGY.read_text(encoding="utf-8"))
    results: list[dict[str, object]] = []
    warnings: list[str] = []

    for scheme_name, scheme in topology["schemes"].items():
        for path in scheme.get("generation_paths", []):
            station_details = []
            total_mwh_per_mm3 = 0.0
            complete = True
            for code in path.get("stations", []):
                candidates = [r for r in by_code.get(code, []) if r["plant_group"] == scheme_name]
                if not candidates:
                    candidates = by_code.get(code, [])
                if len(candidates) != 1:
                    complete = False
                    warnings.append(f"{scheme_name}:{path['name']} station {code} resolved to {len(candidates)} asset rows")
                    continue
                row = candidates[0]
                try:
                    mw_per_cumec = float(row["mw_per_cumec"])
                except (TypeError, ValueError):
                    complete = False
                    warnings.append(f"{scheme_name}:{path['name']} station {code} has no MW/cumec conversion")
                    continue
                contribution = MWH_PER_MM3_PER_MW_PER_CUMEC * mw_per_cumec
                total_mwh_per_mm3 += contribution
                station_details.append({
                    "site_code": code,
                    "mw_per_cumec": round(mw_per_cumec, 8),
                    "mwh_per_mm3": round(contribution, 3),
                })

            results.append({
                "scheme": scheme_name,
                "path": path["name"],
                "source_storage": path.get("source"),
                "stations": station_details,
                "full_route_mwh_per_mm3": round(total_mwh_per_mm3, 3) if complete else None,
                "complete": complete,
                "note": path.get("note"),
            })

    output = {
        "method": {
            "definition": "Diagnostic potential electrical energy from routing one Mm3 of water through every generating station on a topology path.",
            "conversion": "For a station with HMD MW-per-cumec conversion c, one Mm3 produces (1,000,000 / 3,600) * c MWh if all of that volume passes through the turbine.",
            "limitations": [
                "This is an energy-accounting sanity check, not dispatchable storage MWh.",
                "It assumes the whole Mm3 follows the listed generating route and ignores spill/bypass, head variation, efficiency changes with lake level, travel time and operating constraints.",
                "Run-of-river tributary inflows can add water between stations, so downstream generation is not solely attributable to upstream storage.",
                "Actual dispatch must enforce reservoir balances, station MW/flow limits, minimum flows and bypass routes."
            ]
        },
        "paths": results,
        "warnings": sorted(set(warnings)),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    for item in results:
        if item["complete"]:
            print(f"{item['scheme']} / {item['path']}: {item['full_route_mwh_per_mm3']:.1f} MWh per Mm3")
        else:
            print(f"{item['scheme']} / {item['path']}: incomplete")
    if warnings:
        print("Warnings:")
        for warning in sorted(set(warnings)):
            print(f"- {warning}")


if __name__ == "__main__":
    main()
