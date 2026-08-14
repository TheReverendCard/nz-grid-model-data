from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

INFRA = Path("data/hydro/infrastructure_and_constraints.csv")
OVERRIDES = Path("model/hydro_capacity_overrides.json")
OUT_ASSETS = Path("data/model/hydro_assets_current.csv")
OUT_SUMMARY = Path("data/model/hydro_model_input_summary.json")
MODEL_DATE = date(2030, 1, 1)


def parse_date(value: str) -> date:
    y, m, d = (int(x) for x in value.split("-")[:3])
    if y >= 9999:
        return date.max
    return date(y, m, d)


def active_on_model_date(row: dict[str, str]) -> bool:
    start = parse_date(row["StartDate"])
    end = parse_date(row["EndDate"])
    return start <= MODEL_DATE <= end


def as_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    rows: list[dict[str, str]] = []
    with INFRA.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if active_on_model_date(row):
                rows.append(row)

    sites: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        key = (row["PlantGroup"], row["SiteCode"])
        rec = sites.setdefault(
            key,
            {
                "plant_group": row["PlantGroup"],
                "island_code": row["IslandCode"],
                "site_code": row["SiteCode"],
                "description": row["Description"],
                "plant_type": row["PlantType"],
                "plant_subtype": row["PlantSubType"],
                "hmd_generating_capacity_mw": None,
                "generating_capacity_mw": None,
                "capacity_source": "HMD",
                "capacity_override_note": None,
                "plant_factor_cumecs_per_mw": None,
                "mw_per_cumec": None,
                "active_storage_capacity_mm3": None,
                "contingent_storage_capacity_mm3": None,
                "max_station_flow_cumecs": None,
                "min_flow_cumecs": None,
                "max_spill_flow_cumecs": None,
                "source_rows": 0,
                "notes": [],
            },
        )
        rec["source_rows"] = int(rec["source_rows"]) + 1
        attr = row["Attribute"]
        val = as_float(row["Value"])
        desc = (row.get("Description") or "").lower()
        notes = row.get("Notes") or ""
        if notes:
            rec["notes"].append(notes)

        if attr == "Generating capacity" and val is not None and "combined" not in desc:
            rec["hmd_generating_capacity_mw"] = val
            rec["generating_capacity_mw"] = val
        elif attr == "Plant factor" and val is not None and "combined" not in desc:
            rec["plant_factor_cumecs_per_mw"] = val
            if val > 0:
                rec["mw_per_cumec"] = 1.0 / val
        elif attr == "Active storage capacity" and val is not None:
            rec["active_storage_capacity_mm3"] = val
        elif attr == "Consented contingent storage capacity" and val is not None:
            rec["contingent_storage_capacity_mm3"] = val
        elif attr == "Max flow" and val is not None and row["PlantSubType"] == "Station flow":
            rec["max_station_flow_cumecs"] = val
        elif attr == "Min flow" and val is not None:
            current = rec["min_flow_cumecs"]
            rec["min_flow_cumecs"] = val if current is None else max(float(current), val)
        elif attr == "Max spill flow" and val is not None:
            current = rec["max_spill_flow_cumecs"]
            rec["max_spill_flow_cumecs"] = val if current is None else max(float(current), val)

    overrides_doc = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {"overrides": {}}
    applied_overrides: list[dict[str, object]] = []
    for override_key, override in overrides_doc.get("overrides", {}).items():
        plant_group, site_code = override_key.split(":", 1)
        key = (plant_group, site_code)
        if key not in sites:
            sites[key] = {
                "plant_group": plant_group,
                "island_code": None,
                "site_code": site_code,
                "description": site_code,
                "plant_type": "Hydro",
                "plant_subtype": "Hydro",
                "hmd_generating_capacity_mw": None,
                "generating_capacity_mw": None,
                "capacity_source": "override_only",
                "capacity_override_note": None,
                "plant_factor_cumecs_per_mw": None,
                "mw_per_cumec": None,
                "active_storage_capacity_mm3": None,
                "contingent_storage_capacity_mm3": None,
                "max_station_flow_cumecs": None,
                "min_flow_cumecs": None,
                "max_spill_flow_cumecs": None,
                "source_rows": 0,
                "notes": [],
            }
        rec = sites[key]
        old = rec.get("generating_capacity_mw")
        new = float(override["generating_capacity_mw"])
        rec["generating_capacity_mw"] = new
        rec["capacity_source"] = override.get("source", "operator override")
        rec["capacity_override_note"] = override.get("note")
        applied_overrides.append({
            "site": override_key,
            "hmd_capacity_mw": old,
            "override_capacity_mw": new,
            "source": override.get("source"),
        })

    assets = list(sites.values())
    assets = [
        r for r in assets
        if r["generating_capacity_mw"] is not None
        or r["active_storage_capacity_mm3"] is not None
        or r["max_station_flow_cumecs"] is not None
    ]
    assets.sort(key=lambda r: (str(r["island_code"]), str(r["plant_group"]), str(r["site_code"])))

    OUT_ASSETS.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "plant_group", "island_code", "site_code", "description", "plant_type", "plant_subtype",
        "hmd_generating_capacity_mw", "generating_capacity_mw", "capacity_source", "capacity_override_note",
        "plant_factor_cumecs_per_mw", "mw_per_cumec",
        "active_storage_capacity_mm3", "contingent_storage_capacity_mm3",
        "max_station_flow_cumecs", "min_flow_cumecs", "max_spill_flow_cumecs", "source_rows"
    ]
    with OUT_ASSETS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rec in assets:
            writer.writerow({k: rec.get(k) for k in fieldnames})

    station_rows = [r for r in assets if r["generating_capacity_mw"] is not None]
    storage_rows = [r for r in assets if r["active_storage_capacity_mm3"] is not None]
    total_capacity = sum(float(r["generating_capacity_mw"]) for r in station_rows)
    total_active_storage = sum(float(r["active_storage_capacity_mm3"]) for r in storage_rows)
    with_pf = [r for r in station_rows if r["plant_factor_cumecs_per_mw"] is not None]
    stations_missing_pf = [f"{r['plant_group']}:{r['site_code']}" for r in station_rows if r["plant_factor_cumecs_per_mw"] is None]

    by_scheme: dict[str, dict[str, float | int]] = {}
    for r in station_rows:
        s = by_scheme.setdefault(str(r["plant_group"]), {"stations": 0, "capacity_mw": 0.0})
        s["stations"] = int(s["stations"]) + 1
        s["capacity_mw"] = round(float(s["capacity_mw"]) + float(r["generating_capacity_mw"]), 6)

    summary = {
        "model_date": MODEL_DATE.isoformat(),
        "hydrology_source": str(INFRA),
        "capacity_override_source": str(OVERRIDES),
        "station_count": len(station_rows),
        "station_count_with_plant_factor": len(with_pf),
        "stations_missing_plant_factor": stations_missing_pf,
        "total_station_capacity_mw_after_overrides": round(total_capacity, 3),
        "storage_asset_count": len(storage_rows),
        "sum_active_storage_capacity_mm3": round(total_active_storage, 3),
        "scheme_capacity": by_scheme,
        "capacity_overrides_applied": applied_overrides,
        "conversion": "HMD plant factor is cumecs per MW, so instantaneous MW per cumec is 1 / plant_factor. Daily MWh from a station flow q is q * mw_per_cumec * 24, subject to station MW and flow constraints.",
        "important_limitations": [
            "HMD is used for hydrological constraints and plant factors, but generating capacity can be overridden by newer operator-published values.",
            "Reservoir water can generate at multiple downstream stations in a cascade. Storage volume must not be multiplied by one station conversion factor and summed naively.",
            "Stage-dependent, seasonal and consent constraints remain in the original HMD infrastructure table and must be evaluated during dispatch.",
            "Capacity overrides must be periodically reviewed as refurbishments change station ratings."
        ],
        "next_step": "Validate cascade topology and then run a water-balance dispatch against historical inflows, with operator capacity overrides kept separate from HMD hydrology."
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_ASSETS} ({len(assets)} asset rows)")
    print(f"Wrote {OUT_SUMMARY}")
    print(f"Hydro station capacity after overrides: {total_capacity:.1f} MW across {len(station_rows)} station rows")
    if stations_missing_pf:
        print("WARNING stations missing HMD plant factor:", ", ".join(stations_missing_pf))


if __name__ == "__main__":
    main()
