from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from build_distributed_solar_scenarios import (
    END_YEAR,
    FIT_START_YEAR,
    MONTHLY,
    SIZE_SUMMARY,
    fit_rate,
    iter_future_months,
    logistic_from_anchor,
    month_fraction,
    read_csv,
    total_icp_projection,
)

SATURATION = 0.20
OUT_CSV = Path("data/distributed_generation/model/distributed_solar_adoption_20pct.csv")
OUT_JSON = Path("data/distributed_generation/model/distributed_solar_adoption_20pct.json")


def main() -> None:
    monthly = read_csv(MONTHLY)
    size_summary = json.loads(SIZE_SUMMARY.read_text(encoding="utf-8"))
    latest = monthly[-1]
    latest_date = datetime.strptime(latest["month_end"], "%Y-%m-%d").date()

    national_solar_icps = float(size_summary["national_observed"]["solar_icps"])
    small = size_summary["model_groups"]["small_lt_25_kw"]
    small_icps = float(small["estimated_icps"])
    small_capacity_mw = float(small["capacity_mw"])
    small_share_of_solar_icps = small_icps / national_solar_icps

    all_uptake_latest = float(latest["icp_uptake_rate_pct"]) / 100.0
    current_small_share = all_uptake_latest * small_share_of_solar_icps
    current_small_avg_kw = small_capacity_mw * 1000.0 / small_icps

    history_dates = []
    history_shares = []
    for row in monthly:
        d = datetime.strptime(row["month_end"], "%Y-%m-%d").date()
        if d.year < FIT_START_YEAR:
            continue
        history_dates.append(d)
        history_shares.append(float(row["icp_uptake_rate_pct"]) / 100.0 * small_share_of_solar_icps)

    history_t = np.array([month_fraction(d, latest_date) for d in history_dates], dtype=float)
    history_share = np.array(history_shares, dtype=float)
    rate = fit_rate(history_t, history_share, SATURATION, current_small_share)
    project_total_icps, total_icp_growth_per_year, latest_total_icps = total_icp_projection(monthly, latest_date)

    rows = []
    for d in [latest_date, *iter_future_months(latest_date, END_YEAR)]:
        t = month_fraction(d, latest_date)
        total_icps = project_total_icps(t)
        adoption = current_small_share if t == 0 else float(logistic_from_anchor(t, SATURATION, current_small_share, rate))
        solar_icps = total_icps * adoption
        capacity_mw = solar_icps * current_small_avg_kw / 1000.0
        rows.append({
            "month_end": d.isoformat(),
            "projected_total_icps": round(total_icps, 1),
            "adoption_pct": round(adoption * 100.0, 5),
            "small_solar_icps": round(solar_icps, 1),
            "small_capacity_mw": round(capacity_mw, 3),
        })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    OUT_JSON.write_text(json.dumps({
        "source_latest_month": latest["month_end"],
        "scope": "Projected small distributed solar below 25 kW, used as the household-solar component in generation-pipeline visualisations.",
        "saturation_pct": 20.0,
        "fitted_logistic_rate": rate,
        "current_small_solar_uptake_pct": current_small_share * 100.0,
        "current_small_capacity_mw": small_capacity_mw,
        "current_small_average_kw": current_small_avg_kw,
        "estimated_current_total_icps": latest_total_icps,
        "projected_total_icp_growth_per_year": total_icp_growth_per_year,
        "method": "Same fixed-saturation logistic method as build_distributed_solar_scenarios.py, fitted to the same EA observed history but using a 20% small-solar ICP saturation ceiling.",
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
