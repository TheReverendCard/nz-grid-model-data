from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
WATER_VALUE = Path("data/model/hydro_water_value_diagnostic.json")
OUT_DAILY = Path("data/model/hydro_storage_energy_daily.csv")
OUT_SUMMARY = Path("data/model/hydro_storage_energy_summary.json")

# Major storage nodes whose stored water has a defined downstream generation route.
# These are intentionally limited to avoid double-counting small run-of-river storages.
INCLUDED_STORAGE = {
    "TPO",  # Taupo
    "WKA",  # Waikaremoana
    "TKA",  # Tekapo
    "PKI",  # Pukaki
    "OHA",  # Ohau
    "HWE",  # Hawea
    "WAN",  # Wanaka
    "WPU",  # Wakatipu
    "TAU",  # Te Anau
    "MAN",  # Manapouri
}

# Preferred topology path for each storage node.
PATH_BY_STORAGE = {
    "TPO": "Taupo_to_Karapiro",
    "WKA": "Waikaremoana_main",
    "TKA": "Tekapo_full_generation_route",
    "PKI": "Pukaki_Ohau_generation_route",
    "OHA": "Ohau_generation_route",
    "HWE": "Hawea_to_lower_Clutha",
    "WAN": "Wanaka_to_lower_Clutha",
    "WPU": "Wakatipu_to_lower_Clutha",
    "TAU": "Te_Anau_to_Manapouri_generation",
    "MAN": "Manapouri_storage_generation",
}


def main() -> None:
    diagnostic = json.loads(WATER_VALUE.read_text(encoding="utf-8"))
    path_value = {
        p["path"]: float(p["full_route_mwh_per_mm3"])
        for p in diagnostic["paths"]
        if p.get("complete")
    }

    value_by_storage: dict[str, float] = {}
    missing_paths: list[str] = []
    for site, path_name in PATH_BY_STORAGE.items():
        if path_name not in path_value:
            missing_paths.append(f"{site}:{path_name}")
        else:
            value_by_storage[site] = path_value[path_name]
    if missing_paths:
        raise RuntimeError(f"Missing water-value paths: {missing_paths}")

    daily: dict[str, dict[str, float]] = defaultdict(dict)
    names: dict[str, str] = {}
    islands: dict[str, str] = {}

    with STORAGE.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            site = row["site_code"]
            if site not in INCLUDED_STORAGE:
                continue
            storage_raw = row.get("total_storage_mm3") or row.get("active_storage_mm3")
            if storage_raw in (None, ""):
                continue
            try:
                volume = float(storage_raw)
            except ValueError:
                continue
            date = row["date"]
            daily[date][site] = volume
            names[site] = row.get("reservoir") or site
            islands[site] = row.get("island_code") or ""

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    sites = sorted(INCLUDED_STORAGE)
    fieldnames = ["date", "national_energy_equivalent_gwh", "ni_energy_equivalent_gwh", "si_energy_equivalent_gwh"]
    for site in sites:
        fieldnames.extend([f"{site}_storage_mm3", f"{site}_energy_equivalent_gwh"])

    valid_national: list[tuple[str, float]] = []
    complete_days = 0
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for date in sorted(daily):
            row_out: dict[str, object] = {"date": date}
            national = 0.0
            by_island = {"NI": 0.0, "SI": 0.0}
            complete = True
            for site in sites:
                vol = daily[date].get(site)
                if vol is None:
                    complete = False
                    row_out[f"{site}_storage_mm3"] = ""
                    row_out[f"{site}_energy_equivalent_gwh"] = ""
                    continue
                gwh = vol * value_by_storage[site] / 1000.0
                row_out[f"{site}_storage_mm3"] = round(vol, 6)
                row_out[f"{site}_energy_equivalent_gwh"] = round(gwh, 6)
                national += gwh
                if islands.get(site) in by_island:
                    by_island[islands[site]] += gwh
            row_out["national_energy_equivalent_gwh"] = round(national, 6) if complete else ""
            row_out["ni_energy_equivalent_gwh"] = round(by_island["NI"], 6) if complete else ""
            row_out["si_energy_equivalent_gwh"] = round(by_island["SI"], 6) if complete else ""
            writer.writerow(row_out)
            if complete:
                complete_days += 1
                valid_national.append((date, national))

    if not valid_national:
        raise RuntimeError("No complete daily hydro storage-energy observations were produced")

    min_date, min_gwh = min(valid_national, key=lambda x: x[1])
    max_date, max_gwh = max(valid_national, key=lambda x: x[1])

    yearly: dict[int, list[float]] = defaultdict(list)
    for date, gwh in valid_national:
        yearly[int(date[:4])].append(gwh)
    yearly_summary = {
        str(year): {
            "days": len(vals),
            "min_gwh": round(min(vals), 3),
            "max_gwh": round(max(vals), 3),
            "mean_gwh": round(sum(vals) / len(vals), 3),
        }
        for year, vals in sorted(yearly.items())
        if len(vals) >= 365
    }

    summary = {
        "definition": "Observed major-reservoir storage volume converted to an energy-equivalent state using verified full-route MWh-per-Mm3 cascade values.",
        "purpose": "Validation target and intuitive hydro-storage diagnostic. It is not a dispatchable-energy guarantee because operating constraints, spill/bypass, head variation and river routing are not applied here.",
        "included_storage_sites": {
            site: {
                "reservoir": names.get(site, site),
                "island": islands.get(site),
                "path": PATH_BY_STORAGE[site],
                "mwh_per_mm3": round(value_by_storage[site], 6),
            }
            for site in sites
        },
        "complete_daily_observations": complete_days,
        "first_complete_date": valid_national[0][0],
        "last_complete_date": valid_national[-1][0],
        "historical_min": {"date": min_date, "energy_equivalent_gwh": round(min_gwh, 3)},
        "historical_max": {"date": max_date, "energy_equivalent_gwh": round(max_gwh, 3)},
        "yearly_complete_summary": yearly_summary,
        "important_limitations": [
            "Energy-equivalent storage is not the same as assured dispatchable hydro energy.",
            "The conversion uses constant HMD plant factors and assumes stored water follows the full generating route.",
            "It excludes smaller run-of-river storages to avoid double counting water already represented by upstream major lakes.",
            "Te Anau and Manapouri are both included because they are distinct stored volumes that can ultimately pass through Manapouri generation.",
            "The eventual dispatch model must enforce daily water balances, release limits, minimum flows, spill/bypass routes and station MW constraints."
        ],
    }
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} ({complete_days} complete days)")
    print(f"Historical storage-energy range: {min_gwh:.1f} to {max_gwh:.1f} GWh")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
