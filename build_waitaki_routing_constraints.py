from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

SOURCE = Path("data/hydro/infrastructure_and_constraints.csv")
OUT = Path("data/model/waitaki_routing_constraints.json")
SNAPSHOTS = {
    "validation_2024": date(2024, 1, 1),
    "future_2030": date(2030, 1, 1),
}

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


def rec_start(rec: dict[str, object]) -> date:
    return parse_date(str(rec.get("start_date") or ""), date.min)


def rec_end(rec: dict[str, object]) -> date:
    return parse_date(str(rec.get("end_date") or ""), date.max)


def active_on(rec: dict[str, object], when: date) -> bool:
    return rec_start(rec) <= when <= rec_end(rec)


def compact_by_site(rows: list[dict[str, object]]) -> dict[str, object]:
    by_site: dict[str, list[dict[str, object]]] = defaultdict(list)
    for rec in rows:
        by_site[str(rec["site_code"])].append(rec)

    sites = {}
    for site, site_rows in sorted(by_site.items()):
        cap = next((r for r in site_rows if r["attribute"] == "Generating capacity"), None)
        pf = next((r for r in site_rows if r["attribute"] == "Plant factor"), None)
        sites[site] = {
            "generating_capacity_mw": cap["value"] if cap else None,
            "plant_factor_cumecs_per_mw": pf["value"] if pf else None,
            "mw_per_cumec": (1.0 / float(pf["value"])) if pf and pf["value"] else None,
            "max_flow_constraints": [r for r in site_rows if r["attribute"] == "Max flow"],
            "min_flow_constraints": [r for r in site_rows if r["attribute"] == "Min flow"],
            "spill_siphon_constraints": [
                r for r in site_rows
                if r["attribute"] in {"Max spill flow", "Min spill flow", "Max siphon flow", "Min siphon flow"}
            ],
            "storage_constraints": [r for r in site_rows if "storage capacity" in str(r["attribute"]).lower()],
            "records": site_rows,
        }
    return sites


def latest_known_rows(historical: list[dict[str, object]], when: date) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    for rec in historical:
        if rec_start(rec) > when:
            continue
        key = (
            str(rec.get("site_code")),
            str(rec.get("plant_group_rank")),
            str(rec.get("plant_subtype")),
            str(rec.get("description")),
            str(rec.get("attribute")),
        )
        grouped[key].append(rec)

    chosen = []
    for rows in grouped.values():
        best = max(rows, key=lambda r: (rec_start(r), rec_end(r)))
        out = dict(best)
        out["active_on_snapshot_date"] = active_on(best, when)
        out["stale_fallback"] = not active_on(best, when)
        chosen.append(out)
    return sorted(chosen, key=lambda r: (str(r["plant_group_rank"]), str(r["site_code"]), str(r["attribute"])))


def build_snapshot(historical: list[dict[str, object]], when: date) -> dict[str, object]:
    active = [dict(r, active_on_snapshot_date=True, stale_fallback=False) for r in historical if active_on(r, when)]
    latest = latest_known_rows(historical, when)
    active_identities = {
        (str(r["site_code"]), str(r["plant_group_rank"]), str(r["plant_subtype"]), str(r["description"]), str(r["attribute"]))
        for r in active
    }
    stale = [
        r for r in latest
        if r["stale_fallback"] and (
            str(r["site_code"]), str(r["plant_group_rank"]), str(r["plant_subtype"]), str(r["description"]), str(r["attribute"])
        ) not in active_identities
    ]
    return {
        "date": when.isoformat(),
        "active_record_count": len(active),
        "stale_latest_known_record_count": len(stale),
        "active_sites": compact_by_site(active),
        "stale_latest_known_records": stale,
        "latest_known_sites_including_stale": compact_by_site(latest),
    }


def main() -> None:
    historical: list[dict[str, object]] = []
    with SOURCE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("PlantGroup") != "Waitaki river":
                continue
            if row.get("Attribute") not in KEEP_ATTRIBUTES:
                continue
            historical.append({
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
            })

    snapshots = {name: build_snapshot(historical, when) for name, when in SNAPSHOTS.items()}
    result = {
        "status": "waitaki_hmd_routing_constraint_snapshots",
        "source": str(SOURCE),
        "historical_record_count": len(historical),
        "purpose": "Separate constraints actually active for 2024 validation from future-2030 constraints, while exposing expired latest-known HMD rows rather than silently dropping them.",
        "snapshots": snapshots,
        "important_notes": [
            "Use validation_2024.active_sites for the 2024 source-water dispatcher.",
            "For future_2030, active rows are authoritative HMD rows for that date; stale_latest_known_records are diagnostics only, not evidence that expired consent terms remain legally operative.",
            "Schedule strings are preserved and still require calendar evaluation during daily dispatch.",
            "Topology is defined separately in model/hydro_topology.json.",
            "Current operator capacity overrides remain separate from HMD hydrology and constraints."
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} from {len(historical)} historical Waitaki records")
    for name, snap in snapshots.items():
        print(name, snap["active_record_count"], "active;", snap["stale_latest_known_record_count"], "stale latest-known")


if __name__ == "__main__":
    main()
