from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

SOURCE = Path("data/hydro/infrastructure_and_constraints.csv")
OUT = Path("data/model/waitaki_routing_constraints.json")
MODEL_DATE = date(2030, 1, 1)

KEEP_ATTRIBUTES = {
    "Generating capacity",
    "Plant factor",
    "Active storage capacity",
    "Consented contingent storage capacity",
    "Max flow",
    "Min flow",
    "Max spill flow",
    "Min spill flow",
    "Max siphon flow",
    "Min siphon flow",
}


def parse_date(raw: str, fallback: date) -> date:
    if not raw:
        return fallback
    y, m, d = (int(x) for x in raw.split("-"))
    if y == 1:
        return date.min
    if y >= 9999:
        return date.max
    return date(y, m, d)


def active_on_model_date(row: dict[str, str]) -> bool:
    start = parse_date(row.get("StartDate", ""), date.min)
    end = parse_date(row.get("EndDate", ""), date.max)
    return start <= MODEL_DATE <= end


def main() -> None:
    active = []
    historical = []
    with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("PlantGroup") != "Waitaki river":
                continue
            if row.get("Attribute") not in KEEP_ATTRIBUTES:
                continue
            rec = {
                "site_code": row.get("SiteCode"),
                "plant_group_rank": row.get("PlantGroupRank"),
                "plant_type": row.get("PlantType"),
                "plant_subtype": row.get("PlantSubType"),
                "description": row.get("Description"),
                "attribute": row.get("Attribute"),
                "value": float(row["Value"]) if row.get("Value") not in (None, "") else None,
                "unit": row.get("Unit"),
                "start_date": row.get("StartDate"),
                "end_date": row.get("EndDate"),
                "schedule": row.get("Schedule"),
                "source": row.get("Source"),
                "consent": row.get("Consent"),
                "notes": row.get("Notes"),
            }
            historical.append(rec)
            if active_on_model_date(row):
                active.append(rec)

    by_site: dict[str, list[dict[str, object]]] = defaultdict(list)
    by_subtype: dict[str, list[dict[str, object]]] = defaultdict(list)
    for rec in active:
        by_site[str(rec["site_code"])].append(rec)
        by_subtype[str(rec["plant_subtype"])].append(rec)

    stations = {}
    for site, rows in sorted(by_site.items()):
        cap = next((r for r in rows if r["attribute"] == "Generating capacity"), None)
        pf = next((r for r in rows if r["attribute"] == "Plant factor"), None)
        max_flows = [r for r in rows if r["attribute"] == "Max flow"]
        min_flows = [r for r in rows if r["attribute"] == "Min flow"]
        storage = [r for r in rows if "storage capacity" in str(r["attribute"]).lower()]
        stations[site] = {
            "generating_capacity_mw": cap["value"] if cap else None,
            "plant_factor_cumecs_per_mw": pf["value"] if pf else None,
            "mw_per_cumec": (1.0 / float(pf["value"])) if pf and pf["value"] else None,
            "max_flow_constraints": max_flows,
            "min_flow_constraints": min_flows,
            "storage_constraints": storage,
            "all_active_records": rows,
        }

    result = {
        "status": "waitaki_hmd_routing_constraint_extract",
        "model_date": MODEL_DATE.isoformat(),
        "source": str(SOURCE),
        "purpose": "Provide an auditable compact extract of active HMD Waitaki infrastructure, station-flow, river/canal, spill/bypass and storage constraints before building source-water routing dispatch.",
        "active_record_count": len(active),
        "historical_record_count": len(historical),
        "stations_and_sites": stations,
        "active_records_by_subtype": {k: v for k, v in sorted(by_subtype.items())},
        "important_notes": [
            "Scheduled constraints may require calendar evaluation beyond simple date-range activity; schedule strings are preserved verbatim.",
            "Rows are extracted from HMD rather than inferred from observed generation.",
            "This file does not itself define topology; topology remains in model/hydro_topology.json and must be combined with these constraints.",
            "The next dispatcher should conserve water only at a source reservoir whose downstream release/generation path is actually being reduced."
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(active)} active Waitaki constraint records")
    for site in sorted(stations):
        s = stations[site]
        if s["generating_capacity_mw"] is not None or s["max_flow_constraints"] or s["storage_constraints"]:
            print(site, s["generating_capacity_mw"], len(s["max_flow_constraints"]), len(s["storage_constraints"]))


if __name__ == "__main__":
    main()
