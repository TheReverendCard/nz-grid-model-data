from __future__ import annotations

import csv
import json
from calendar import monthrange
from pathlib import Path

SOLAR_SCENARIOS = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.csv")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
SOSA_WINTER = Path("data/sosa/2026/medium_demand_winter_energy.csv")
SOSA_WEM = Path("data/sosa/2026/reference_nzwem.csv")
OUT_CSV = Path("data/model/distributed_solar_sosa_winter_energy.csv")
OUT_JSON = Path("data/model/distributed_solar_sosa_winter_energy.json")

SCENARIOS = ("low_10pct", "high_30pct")
CENTRAL_YIELD_KWH_PER_KWP_YEAR = 1150.0
WINTER_MONTHS = (4, 5, 6, 7, 8, 9)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {path}")
    return rows


def monthly_weights() -> dict[int, float]:
    seasonality = json.loads(SEASONALITY.read_text(encoding="utf-8"))
    indexes = {int(k): float(v) for k, v in seasonality["solar_method"]["monthly_index_vs_annual_mean_daily"].items()}
    # Average Gregorian month lengths over the leap-year cycle are close enough
    # for normalising the observed seasonal index into annual generation shares.
    average_days = {
        1: 31.0, 2: 28.25, 3: 31.0, 4: 30.0, 5: 31.0, 6: 30.0,
        7: 31.0, 8: 31.0, 9: 30.0, 10: 31.0, 11: 30.0, 12: 31.0,
    }
    total = sum(indexes[m] * average_days[m] for m in range(1, 13))
    return {m: indexes[m] * average_days[m] / total for m in range(1, 13)}


def main() -> None:
    solar = read_csv(SOLAR_SCENARIOS)
    sosa_winter = {int(r["year"]): r for r in read_csv(SOSA_WINTER)}
    wem_rows = read_csv(SOSA_WEM)
    reference_wem = {
        int(r["year"]): r for r in wem_rows if r["sensitivity"] == "reference"
    }
    weights = monthly_weights()
    winter_share = sum(weights[m] for m in WINTER_MONTHS)

    solar_by_month = {r["month_end"][:7]: r for r in solar}
    output_rows: list[dict[str, object]] = []

    for year in range(2027, 2036):
        if year not in sosa_winter or year not in reference_wem:
            continue
        baseline = sosa_winter[year]
        wem = reference_wem[year]
        demand = float(baseline["effective_nzwem_demand_gwh"])
        baseline_domestic = float(baseline["sosa_domestic_solar_battery_gwh"])

        for scenario in SCENARIOS:
            winter_gwh = 0.0
            month_details = []
            complete = True
            for month in WINTER_MONTHS:
                key = f"{year}-{month:02d}"
                current = solar_by_month.get(key)
                if current is None:
                    complete = False
                    continue
                current_capacity = float(current[f"{scenario}_distributed_sub_1mw_capacity_mw"])
                prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
                previous = solar_by_month.get(f"{prev_year}-{prev_month:02d}")
                previous_capacity = (
                    float(previous[f"{scenario}_distributed_sub_1mw_capacity_mw"])
                    if previous is not None
                    else current_capacity
                )
                avg_capacity = (previous_capacity + current_capacity) / 2.0
                month_gwh = avg_capacity * (CENTRAL_YIELD_KWH_PER_KWP_YEAR / 1000.0) * weights[month]
                winter_gwh += month_gwh
                month_details.append(
                    {
                        "month": month,
                        "average_capacity_mw": round(avg_capacity, 3),
                        "annual_generation_share": round(weights[month], 8),
                        "generation_gwh": round(month_gwh, 3),
                    }
                )

            if not complete:
                continue

            delta = winter_gwh - baseline_domestic
            margin_delta_pp = delta / demand * 100.0
            output_rows.append(
                {
                    "year": year,
                    "scenario": scenario,
                    "modelled_distributed_solar_winter_gwh": round(winter_gwh, 3),
                    "sosa_domestic_solar_battery_winter_gwh": round(baseline_domestic, 3),
                    "difference_gwh": round(delta, 3),
                    "effective_nzwem_demand_gwh": round(demand, 3),
                    "nzwem_margin_change_percentage_points": round(margin_delta_pp, 4),
                    "stage1_adjusted_nzwem_pct": round(float(wem["stage1_existing_committed_pct"]) + margin_delta_pp, 4),
                    "stage2_adjusted_nzwem_pct": round(float(wem["stage2_plus_consented_likely_pct"]) + margin_delta_pp, 4),
                    "stage3_adjusted_nzwem_pct": round(float(wem["stage3_plus_likely_consent_2y_pct"]) + margin_delta_pp, 4),
                }
            )

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0].keys()))
        writer.writeheader()
        writer.writerows(output_rows)

    summary = {
        "scope": "First-pass adjustment of Transpower 2026 SOSA reference NZ winter energy margins for alternative non-utility distributed-solar trajectories.",
        "winter_period": "April through September, matching NZ-WEM/SI-WEM.",
        "central_specific_yield_kwh_per_kwp_year": CENTRAL_YIELD_KWH_PER_KWP_YEAR,
        "observed_solar_apr_sep_generation_share": round(winter_share, 8),
        "method": {
            "solar_generation": "Integrate each scenario's monthly <1 MW distributed-solar capacity over April-September using the observed NZ solar monthly shape and a central fleet yield of 1150 kWh/kWp/year.",
            "sosa_accounting": "Replace only SOSA's domestic solar+battery seasonal-energy contribution. Underlying gross winter demand is held fixed, so the WEM change is delta domestic solar GWh divided by SOSA effective WEM demand.",
            "battery_energy": "No net seasonal GWh is added for batteries; batteries shift energy in time. Battery effects belong in the capacity/dispatch analysis.",
            "caution": "This is a transparent scenario adjustment to the published SOSA margin, not a rerun of Transpower's full security model. It does not recalculate transmission losses, demand response interactions, hydro dispatch or spill.",
        },
        "visual_note": "Distributed PV winter energy uses 1150 kWh/kWp-year and the observed Apr-Sep solar shape; SOSA margin changes show the difference from SOSA's own domestic solar+battery assumption, not an additive double count.",
        "rows": output_rows,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_CSV} ({len(output_rows)} rows)")
    print(f"Wrote {OUT_JSON}")
    print(f"Observed Apr-Sep solar generation share: {winter_share * 100:.1f}%")


if __name__ == "__main__":
    main()
