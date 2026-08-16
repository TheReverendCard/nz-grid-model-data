from __future__ import annotations

import json
from pathlib import Path

BASELINE = Path("data/model/baseline_2024.json")
EDGS = Path("data/mbie/edgs2024/model/demand_scenarios_summary.json")
OUTPUT = Path("data/model/future_demand_scenarios.json")

TARGET_YEARS = (2024, 2030, 2040, 2050)


def main() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    edgs = json.loads(EDGS.read_text(encoding="utf-8"))

    actual_2024_mwh = float(baseline["annual"]["underlying_consumption_mwh"])
    actual_2024_twh = actual_2024_mwh / 1_000_000.0

    aligned: dict[str, object] = {}
    for scenario in edgs["scenarios"]:
        raw = edgs["total_electricity_demand"][scenario]
        edgs_2024_twh = float(raw["2024"])
        years: dict[str, object] = {}
        for year in TARGET_YEARS:
            edgs_twh = float(raw[str(year)])
            growth_factor = edgs_twh / edgs_2024_twh
            calibrated_twh = actual_2024_twh * growth_factor
            years[str(year)] = {
                "edgs_raw_twh": round(edgs_twh, 6),
                "growth_factor_vs_edgs_2024": round(growth_factor, 8),
                "growth_pct_vs_2024": round((growth_factor - 1.0) * 100.0, 4),
                "calibrated_underlying_demand_twh": round(calibrated_twh, 6),
                "calibrated_underlying_demand_mwh": round(calibrated_twh * 1_000_000.0, 3),
            }
        aligned[scenario] = {
            "edgs_2024_start_twh": edgs_2024_twh,
            "years": years,
            "sector_demand_twh": edgs["electricity_demand_by_sector"].get(scenario, {}),
            "edgs_peak_demand_gw": edgs["peak_demand"].get(scenario, {}),
            "datacentre_load_capacity_mw": edgs.get("datacentre_load_capacity_mw", {}).get(scenario, {}),
            "datacentre_electricity_consumption_twh": edgs.get("datacentre_electricity_consumption_twh", {}).get(scenario, {}),
            "ev_share_of_vkt": edgs["ev_share_of_vkt"].get(scenario, {}),
        }

    output = {
        "source": "MBIE Electricity Demand and Generation Scenarios 2024, calibrated to observed/modelled 2024 national underlying electricity consumption",
        "baseline": {
            "year": 2024,
            "calibrated_underlying_consumption_twh": round(actual_2024_twh, 6),
            "definition": "EA reconciled offtake plus central estimate of residential PV retained behind the meter.",
        },
        "method": {
            "annual_energy": "For each EDGS scenario, preserve the EDGS percentage growth from its own 2024 starting value and apply that growth factor to the calibrated 2024 underlying-consumption baseline.",
            "reason": "This retains EDGS electrification/economic/sector assumptions without replacing observed 2024 electricity use with an older modelled starting value.",
            "daily_shape": "Not yet generated. Future replay will scale a calibrated historical daily underlying-demand shape to these annual targets, with future behind-meter PV represented separately.",
            "peak_demand": "EDGS peak demand is retained as a capacity-adequacy reference but is not yet recalibrated to an observed 2024 national peak.",
            "thermal": "Thermal plant remains an available high-cost backstop; energy adequacy should report thermal MWh required rather than assume gas generation is unavailable.",
        },
        "scenarios": aligned,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    for scenario, entry in aligned.items():
        y2030 = entry["years"]["2030"]
        print(
            f"{scenario}: 2030 {y2030['calibrated_underlying_demand_twh']:.3f} TWh "
            f"({y2030['growth_pct_vs_2024']:+.1f}% vs 2024)"
        )


if __name__ == "__main__":
    main()
