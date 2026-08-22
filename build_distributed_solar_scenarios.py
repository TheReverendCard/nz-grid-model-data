from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

MONTHLY = Path("data/distributed_generation/model/national_solar_all_monthly.csv")
SIZE_SUMMARY = Path("data/distributed_generation/model/solar_size_buckets_current.json")
SOURCE_META = Path("data/metadata/distributed_generation_sources.json")
OUT_CSV = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.csv")
OUT_JSON = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.json")

SATURATION = {"low_10pct": 0.10, "high_30pct": 0.30}
END_YEAR = 2050
FIT_START_YEAR = 2018


def read_monthly() -> list[dict[str, str]]:
    with MONTHLY.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {MONTHLY}")
    return rows


def source_fingerprint(latest_month: str) -> tuple[str, dict[str, str]]:
    meta = json.loads(SOURCE_META.read_text(encoding="utf-8"))
    datasets = meta["datasets"]
    hashes = {
        "solar_all_monthly_source_sha256": datasets["installed_distributed_generation_trends_solar_all"]["sha256"],
        "solar_street_source_sha256": datasets["solar_installations_by_street"]["sha256"],
    }
    payload = json.dumps({"latest_month": latest_month, **hashes}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), hashes


def month_fraction(d: date, reference: date) -> float:
    months = (d.year - reference.year) * 12 + (d.month - reference.month)
    return months / 12.0


def logistic_from_anchor(t_years: float | np.ndarray, saturation: float, current_share: float, rate: float):
    a = saturation / current_share - 1.0
    return saturation / (1.0 + a * np.exp(-rate * t_years))


def fit_rate(history_t: np.ndarray, history_share: np.ndarray, saturation: float, current_share: float) -> float:
    def objective(rate: float) -> float:
        predicted = logistic_from_anchor(history_t, saturation, current_share, rate)
        # Weight recent observations somewhat more strongly because the model is
        # intended as a forward adoption trajectory, not a historical reconstruction.
        weights = np.linspace(0.5, 1.0, len(history_share))
        return float(np.average((predicted - history_share) ** 2, weights=weights))

    result = minimize_scalar(objective, bounds=(0.01, 1.0), method="bounded")
    if not result.success:
        raise RuntimeError(f"Logistic rate fit failed for saturation={saturation}: {result.message}")
    return float(result.x)


def total_icp_projection(monthly: list[dict[str, str]], reference: date):
    points = []
    for row in monthly:
        uptake = float(row["icp_uptake_rate_pct"]) / 100.0
        if uptake <= 0:
            continue
        d = datetime.strptime(row["month_end"], "%Y-%m-%d").date()
        if d.year < reference.year - 5:
            continue
        total_icps = float(row["icp_count"]) / uptake
        points.append((month_fraction(d, reference), total_icps))

    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    latest_total = float(monthly[-1]["icp_count"]) / (float(monthly[-1]["icp_uptake_rate_pct"]) / 100.0)

    # Anchor exactly to the latest observed denominator and retain only the fitted growth slope.
    def project(t_years: float) -> float:
        return max(latest_total, latest_total + slope * max(0.0, t_years))

    return project, float(slope), latest_total


def iter_future_months(start: date, end_year: int):
    year, month = start.year, start.month
    while True:
        month += 1
        if month == 13:
            month = 1
            year += 1
        if year > end_year:
            break
        # Month-end labels only need a stable monthly date for projection output.
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        yield date.fromordinal(next_month.toordinal() - 1)


def main() -> None:
    monthly = read_monthly()
    size_summary = json.loads(SIZE_SUMMARY.read_text(encoding="utf-8"))
    latest = monthly[-1]
    latest_date = datetime.strptime(latest["month_end"], "%Y-%m-%d").date()

    fingerprint, source_hashes = source_fingerprint(latest["month_end"])
    if OUT_JSON.exists():
        previous = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        if previous.get("source_fingerprint") == fingerprint:
            print(f"Distributed solar sources unchanged at {latest['month_end']}; skipping adoption refit.")
            return

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
        # Larger installations are only ~1.3% of current solar ICPs. Scale the
        # historical all-solar ICP series by the current measured small-system share.
        history_shares.append(float(row["icp_uptake_rate_pct"]) / 100.0 * small_share_of_solar_icps)

    history_t = np.array([month_fraction(d, latest_date) for d in history_dates], dtype=float)
    history_share = np.array(history_shares, dtype=float)

    project_total_icps, total_icp_growth_per_year, latest_total_icps = total_icp_projection(monthly, latest_date)

    rates = {
        name: fit_rate(history_t, history_share, saturation, current_small_share)
        for name, saturation in SATURATION.items()
    }

    rows = []
    # Include the observed anchor as the first row.
    dates = [latest_date, *iter_future_months(latest_date, END_YEAR)]
    for d in dates:
        t = month_fraction(d, latest_date)
        total_icps = project_total_icps(t)
        row: dict[str, object] = {
            "month_end": d.isoformat(),
            "projected_total_icps": round(total_icps, 1),
        }
        for name, saturation in SATURATION.items():
            adoption = current_small_share if t == 0 else float(logistic_from_anchor(t, saturation, current_small_share, rates[name]))
            solar_icps = total_icps * adoption
            capacity_mw = solar_icps * current_small_avg_kw / 1000.0
            row[f"{name}_adoption_pct"] = round(adoption * 100.0, 5)
            row[f"{name}_solar_icps"] = round(solar_icps, 1)
            row[f"{name}_capacity_mw"] = round(capacity_mw, 3)
        rows.append(row)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    year_end = {str(y): next(r for r in reversed(rows) if str(r["month_end"]).startswith(f"{y}-")) for y in range(latest_date.year, END_YEAR + 1)}
    output = {
        "source_latest_month": latest["month_end"],
        "source_fingerprint": fingerprint,
        "source_hashes": source_hashes,
        "scope": "Solar installations below 25 kW, used as the small-scale distributed-solar ICP-adoption population.",
        "current": {
            "all_solar_icps": int(national_solar_icps),
            "small_solar_icps_estimated": round(small_icps, 1),
            "small_share_of_all_solar_icps_pct": round(small_share_of_solar_icps * 100.0, 4),
            "all_solar_uptake_pct": round(all_uptake_latest * 100.0, 5),
            "small_solar_uptake_pct": round(current_small_share * 100.0, 5),
            "estimated_total_icps": round(latest_total_icps, 1),
            "small_capacity_mw": round(small_capacity_mw, 6),
            "small_fleet_average_kw": round(current_small_avg_kw, 4),
        },
        "method": {
            "history_proxy": f"EA all-solar ICP uptake from {FIT_START_YEAR} onward is scaled by the current measured <25 kW share of solar ICPs.",
            "curve": "Fixed-saturation logistic curve. Growth rate is fitted to recent history while each scenario is forced exactly through the latest measured small-system penetration.",
            "saturation": {name: value * 100.0 for name, value in SATURATION.items()},
            "total_icps": "Future all-ICP denominator uses the linear growth slope fitted over the latest five years, anchored to the latest observed denominator.",
            "capacity_first_pass": "Projected <25 kW capacity uses the current measured fleet-average kW per small solar ICP. System-size evolution will be modelled separately.",
            "refresh_gate": "The adoption fit is regenerated only when the latest EA month or the all-solar/street source hashes change.",
        },
        "fit": {
            name: {"saturation_pct": SATURATION[name] * 100.0, "growth_rate_per_year": round(rate, 8)}
            for name, rate in rates.items()
        },
        "total_icp_growth_per_year": round(total_icp_growth_per_year, 3),
        "year_end": year_end,
    }
    OUT_JSON.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_CSV} ({len(rows)} monthly rows)")
    print(f"Wrote {OUT_JSON}")
    print(f"Current <25 kW penetration: {current_small_share * 100:.3f}%")
    for year in (2030, 2035, 2040, 2050):
        r = year_end[str(year)]
        print(
            f"{year}: low={r['low_10pct_adoption_pct']:.2f}% ({r['low_10pct_capacity_mw']:.0f} MW), "
            f"high={r['high_30pct_adoption_pct']:.2f}% ({r['high_30pct_capacity_mw']:.0f} MW)"
        )


if __name__ == "__main__":
    main()
