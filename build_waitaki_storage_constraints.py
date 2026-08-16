from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
STORAGE_ENERGY_SUMMARY = Path("data/model/hydro_storage_energy_summary.json")
OUT = Path("data/model/waitaki_storage_constraints.json")

SITES = ["TKA", "PKI", "OHA"]
KEY_DATES = ["2024-01-01", "2024-05-01", "2024-08-01", "2024-12-31"]


def main() -> None:
    energy_summary = json.loads(STORAGE_ENERGY_SUMMARY.read_text(encoding="utf-8"))
    mwh_per_mm3 = {
        site: float(energy_summary["included_storage_sites"][site]["mwh_per_mm3"])
        for site in SITES
    }

    values: dict[str, list[tuple[str, float]]] = defaultdict(list)
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    names: dict[str, str] = {}

    with STORAGE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            site = row["site_code"]
            if site not in SITES:
                continue
            raw = row.get("total_storage_mm3") or row.get("active_storage_mm3")
            if raw in (None, ""):
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            d = row["date"]
            values[site].append((d, value))
            by_date[d][site] = value
            names[site] = row.get("reservoir") or site

    result = {
        "status": "empirical_individual_lake_storage_constraints",
        "purpose": "Replace the combined upper-Waitaki storage bucket with auditable per-lake storage headroom before adding detailed routing constraints.",
        "sites": {},
        "notes": [
            "Current limits are empirical extrema from the downloaded normalized storage history, not yet legal/consent engineering limits.",
            "Energy-equivalent values use the verified full downstream generating route for each lake.",
            "This is a tighter constraint than one combined storage bucket because unused headroom in one lake cannot automatically absorb water conserved in another.",
            "Future refinement should replace empirical extrema with explicit HMD/consent operating bounds where available."
        ],
    }

    for site in SITES:
        rows = values[site]
        if not rows:
            raise RuntimeError(f"No storage observations for {site}")
        min_date, min_mm3 = min(rows, key=lambda x: x[1])
        max_date, max_mm3 = max(rows, key=lambda x: x[1])
        conv = mwh_per_mm3[site]

        key_states = {}
        for d in KEY_DATES:
            if site in by_date.get(d, {}):
                mm3 = by_date[d][site]
                key_states[d] = {
                    "storage_mm3": round(mm3, 6),
                    "energy_equivalent_gwh": round(mm3 * conv / 1000.0, 6),
                    "headroom_to_empirical_max_mm3": round(max_mm3 - mm3, 6),
                    "headroom_to_empirical_max_gwh": round((max_mm3 - mm3) * conv / 1000.0, 6),
                }

        result["sites"][site] = {
            "reservoir": names.get(site, site),
            "mwh_per_mm3_full_route": round(conv, 6),
            "historical_observations": len(rows),
            "empirical_min": {
                "date": min_date,
                "storage_mm3": round(min_mm3, 6),
                "energy_equivalent_gwh": round(min_mm3 * conv / 1000.0, 6),
            },
            "empirical_max": {
                "date": max_date,
                "storage_mm3": round(max_mm3, 6),
                "energy_equivalent_gwh": round(max_mm3 * conv / 1000.0, 6),
            },
            "key_2024_states": key_states,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    for site in SITES:
        info = result["sites"][site]
        print(
            f"{site}: empirical {info['empirical_min']['energy_equivalent_gwh']:.1f}-"
            f"{info['empirical_max']['energy_equivalent_gwh']:.1f} GWh"
        )


if __name__ == "__main__":
    main()
