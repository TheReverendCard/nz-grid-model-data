from __future__ import annotations

import csv
import json
from pathlib import Path

BATTERY_SCENARIOS = Path("data/distributed_generation/model/distributed_battery_attachment_scenarios.json")
SOSA_PEAK = Path("data/sosa/2026/medium_demand_winter_peak.csv")
SOSA_NIWCM = Path("data/sosa/2026/reference_niwcm.csv")
OUT_CSV = Path("data/model/distributed_battery_sosa_ni_capacity.csv")
OUT_JSON = Path("data/model/distributed_battery_sosa_ni_capacity.json")

BESS_PEAK_CREDIT = 0.60
SCENARIOS = ("low_10pct", "high_30pct")
SOSA_SENSITIVITIES = ("reference", "constrained_operational_capacity_low_wind_solar")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {path}")
    return rows


def main() -> None:
    battery = json.loads(BATTERY_SCENARIOS.read_text(encoding="utf-8"))
    peak_rows = {int(r["year"]): r for r in read_csv(SOSA_PEAK)}
    niwcm_rows = {
        (int(r["year"]), r["sensitivity"]): r
        for r in read_csv(SOSA_NIWCM)
    }

    rows: list[dict[str, object]] = []
    for year in range(2027, 2036):
        year_battery = battery["year_end"][str(year)]
        peak = peak_rows[year]
        sosa_ni_peak = float(peak["ni_solar_battery_peak_contribution_mw"])
        sosa_nz_peak = float(peak["nz_solar_battery_peak_contribution_mw"])
        ni_share = sosa_ni_peak / sosa_nz_peak if sosa_nz_peak else 0.0

        for scenario in SCENARIOS:
            national_power_proxy_mw = float(year_battery[f"{scenario}_registered_connection_power_proxy_mw"])
            allocated_ni_power_proxy_mw = national_power_proxy_mw * ni_share
            custom_ni_peak_contribution_mw = allocated_ni_power_proxy_mw * BESS_PEAK_CREDIT
            delta_mw = custom_ni_peak_contribution_mw - sosa_ni_peak

            for sensitivity in SOSA_SENSITIVITIES:
                base = niwcm_rows[(year, sensitivity)]
                rows.append(
                    {
                        "year": year,
                        "scenario": scenario,
                        "sensitivity": sensitivity,
                        "sosa_ni_share_of_domestic_solar_battery_peak_pct": round(ni_share * 100.0, 4),
                        "national_registered_connection_power_proxy_mw": round(national_power_proxy_mw, 3),
                        "allocated_ni_registered_connection_power_proxy_mw": round(allocated_ni_power_proxy_mw, 3),
                        "bess_peak_credit_pct": BESS_PEAK_CREDIT * 100.0,
                        "custom_ni_battery_peak_contribution_mw": round(custom_ni_peak_contribution_mw, 3),
                        "sosa_embedded_ni_solar_battery_peak_contribution_mw": round(sosa_ni_peak, 3),
                        "niwcm_delta_mw": round(delta_mw, 3),
                        "stage1_base_niwcm_mw": round(float(base["stage1_existing_committed_mw"]), 3),
                        "stage1_adjusted_niwcm_mw": round(float(base["stage1_existing_committed_mw"]) + delta_mw, 3),
                        "stage2_base_niwcm_mw": round(float(base["stage2_plus_consented_likely_mw"]), 3),
                        "stage2_adjusted_niwcm_mw": round(float(base["stage2_plus_consented_likely_mw"]) + delta_mw, 3),
                        "stage3_base_niwcm_mw": round(float(base["stage3_plus_likely_consent_2y_mw"]), 3),
                        "stage3_adjusted_niwcm_mw": round(float(base["stage3_plus_likely_consent_2y_mw"]) + delta_mw, 3),
                    }
                )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    output = {
        "scope": "First-pass adjustment of Transpower 2026 SOSA North Island winter capacity margins for alternative small distributed solar+battery attachment trajectories.",
        "method": {
            "battery_power": "Use the EA Solar (with battery) registered connection-capacity projection as a conservative national power ceiling/proxy, not as directly measured battery-only discharge power.",
            "peak_credit": "Apply Transpower SOSA's 60% winter peak capacity contribution used for BESS with storage duration of 2 hours or less. The model's 1.0 h, 1.5 h and 2.0 h duration cases therefore receive the same NIWCM credit.",
            "north_island_allocation": "Allocate the national distributed battery power proxy to the North Island using SOSA's own annual North Island share of its embedded domestic solar+battery winter-peak contribution.",
            "sosa_accounting": "Apply only the difference between the custom NI battery peak contribution and SOSA's already embedded NI domestic solar+battery contribution. Do not add the full custom contribution on top of SOSA.",
            "solar_at_peak": "No separate distributed-solar peak credit is added here. SOSA's domestic solar+battery peak contribution is treated as the baseline to replace; the incremental custom contribution is conservatively represented by battery peak support.",
            "caution": "This is a transparent sensitivity adjustment to published SOSA margins, not a rerun of Transpower's capacity model or a dispatch simulation.",
        },
        "bess_peak_credit": BESS_PEAK_CREDIT,
        "rows": rows,
    }
    OUT_JSON.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")
    print(f"Wrote {OUT_JSON}")
    for year in (2029, 2031, 2035):
        for scenario in SCENARIOS:
            row = next(
                r for r in rows
                if r["year"] == year and r["scenario"] == scenario and r["sensitivity"] == "constrained_operational_capacity_low_wind_solar"
            )
            print(
                f"{year} {scenario}: delta={row['niwcm_delta_mw']:+.0f} MW; "
                f"constrained Stage1={row['stage1_adjusted_niwcm_mw']:.0f} MW, "
                f"Stage2={row['stage2_adjusted_niwcm_mw']:.0f} MW, "
                f"Stage3={row['stage3_adjusted_niwcm_mw']:.0f} MW"
            )


if __name__ == "__main__":
    main()
