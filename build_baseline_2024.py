from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

YEAR = 2024
BASELINE_SUMMARY = Path("data/wholesale/model/baseline_summary.json")
DG_SUMMARY = Path("data/distributed_generation/model/distributed_solar_summary.json")
BTM_ESTIMATE = Path("data/distributed_generation/model/btm_solar_estimate_2024.json")
RECONCILED_DAILY = Path("data/wholesale/model/reconciled_daily.csv")
GENERATION_DAILY = Path("data/wholesale/model/generation_daily.csv")
OUTPUT = Path("data/model/baseline_2024.json")


def read_reconciled_2024() -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    with RECONCILED_DAILY.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["date"]
            if not date.startswith(f"{YEAR}-"):
                continue
            rows[date] = {
                "reconciled_offtake_mwh": float(row["reconciled_offtake_mwh"]),
                "reconciled_injection_mwh": float(row["reconciled_injection_mwh"]),
            }
    return rows


def read_generation_2024() -> tuple[dict[str, float], dict[str, float]]:
    daily: defaultdict[str, float] = defaultdict(float)
    fuel: defaultdict[str, float] = defaultdict(float)
    with GENERATION_DAILY.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            date = row["date"]
            if not date.startswith(f"{YEAR}-"):
                continue
            value = float(row["generation_mwh"])
            daily[date] += value
            fuel[row["fuel_code"]] += value
    return dict(daily), dict(sorted(fuel.items()))


def main() -> None:
    summary = json.loads(BASELINE_SUMMARY.read_text(encoding="utf-8"))["years"][str(YEAR)]
    dg_summary = json.loads(DG_SUMMARY.read_text(encoding="utf-8"))
    dg_2024 = dg_summary["historical"]["2024_year_end"]
    btm = json.loads(BTM_ESTIMATE.read_text(encoding="utf-8"))
    central_btm = btm["scenarios"]["central"]
    reconciled = read_reconciled_2024()
    generation_daily, generation_by_fuel = read_generation_2024()

    if len(reconciled) != 366:
        raise RuntimeError(f"Expected 366 reconciled days for 2024, got {len(reconciled)}")

    daily_rows = []
    for date in sorted(reconciled):
        injection = reconciled[date]["reconciled_injection_mwh"]
        offtake = reconciled[date]["reconciled_offtake_mwh"]
        mapped = generation_daily.get(date, 0.0)
        daily_rows.append(
            {
                "date": date,
                "measured_grid_demand_mwh": round(offtake, 6),
                "reconciled_injection_mwh": round(injection, 6),
                "mapped_generation_mwh": round(mapped, 6),
                "unmapped_generation_residual_mwh": round(injection - mapped, 6),
                "transmission_and_reconciliation_difference_mwh": round(injection - offtake, 6),
            }
        )

    retained = float(central_btm["retained_behind_meter_pv_mwh"])
    measured_demand = float(summary["reconciled_offtake_mwh"])

    output = {
        "model_year": YEAR,
        "status": "calibrated_energy_accounting_baseline",
        "definitions": {
            "measured_grid_demand_mwh": "EA reconciled offtake; excludes behind-meter self-consumption",
            "reconciled_injection_mwh": "EA reconciled injection; authoritative national supply-side accounting total",
            "mapped_generation_mwh": "EA Generation_MD mapped plant generation",
            "unmapped_generation_residual_mwh": "reconciled injection minus Generation_MD mapped generation",
            "transmission_and_reconciliation_difference_mwh": "reconciled injection minus reconciled offtake",
            "underlying_consumption_mwh": "measured grid demand plus modelled behind-meter retained residential solar",
        },
        "annual": {
            "reconciled_injection_mwh": summary["reconciled_injection_mwh"],
            "measured_grid_demand_mwh": measured_demand,
            "mapped_generation_mwh": summary["generation_md_mwh"],
            "unmapped_generation_residual_mwh": round(summary["reconciled_injection_mwh"] - summary["generation_md_mwh"], 6),
            "transmission_and_reconciliation_difference_mwh": round(summary["reconciled_injection_mwh"] - measured_demand, 6),
            "mapped_generation_share_of_reconciled_injection_pct": round(summary["generation_md_mwh"] / summary["reconciled_injection_mwh"] * 100, 4),
            "behind_meter_solar_self_consumption_mwh": round(retained, 3),
            "underlying_consumption_mwh": round(measured_demand + retained, 3),
            "btm_solar_share_of_underlying_consumption_pct": round(retained / (measured_demand + retained) * 100, 4),
        },
        "mapped_generation_by_fuel_mwh": generation_by_fuel,
        "distributed_solar": {
            "year_end_2024_residential_solar": dg_2024["residential_solar"],
            "year_end_2024_all_solar": dg_2024["all_solar"],
            "btm_estimate": btm,
            "selected_baseline_scenario": "central",
            "battery_export_assumption": "zero by default except explicit VPP/export scenarios",
        },
        "daily": daily_rows,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} ({len(daily_rows)} days)")
    print(
        f"Central BTM estimate: retained={retained / 1000:.1f} GWh, "
        f"underlying consumption={(measured_demand + retained) / 1_000_000:.3f} TWh"
    )


if __name__ == "__main__":
    main()
