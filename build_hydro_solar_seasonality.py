from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path

INFLOWS = Path("data/hydro/model/inflows_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
OUT = Path("data/model/hydro_solar_seasonality.json")

# Long hydrological climatology. Start at the first full calendar year available.
HYDRO_START_YEAR = 1932
HYDRO_END_YEAR = 2024

# Use only plant-years with near-full daily coverage and material output so commissioning
# fragments and zero/alternate-network rows do not distort the observed solar shape.
SOLAR_MIN_DAYS = 330
SOLAR_MIN_ANNUAL_MWH = 1_000.0

SUMMER_MONTHS = {11, 12, 1, 2, 3}
WINTER_MONTHS = {5, 6, 7, 8}


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom == 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / denom


def normalise_monthly_mean(month_sums: dict[int, float], month_days: dict[int, int]) -> dict[int, float]:
    daily_means = {
        m: (month_sums.get(m, 0.0) / month_days[m]) if month_days.get(m, 0) else 0.0
        for m in range(1, 13)
    }
    annual_mean_daily = sum(month_sums.values()) / max(sum(month_days.values()), 1)
    if annual_mean_daily <= 0:
        return {m: 0.0 for m in range(1, 13)}
    return {m: daily_means[m] / annual_mean_daily for m in range(1, 13)}


def fraction_for_months(month_sums: dict[int, float], months: set[int]) -> float | None:
    total = sum(month_sums.values())
    if total <= 0:
        return None
    return sum(v for m, v in month_sums.items() if m in months) / total


def read_hydro() -> tuple[dict[str, dict[int, float]], dict[str, dict[int, int]], dict[str, str]]:
    sums: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    days: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    names: dict[str, str] = {}
    with INFLOWS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            d = date.fromisoformat(row["date"])
            if not (HYDRO_START_YEAR <= d.year <= HYDRO_END_YEAR):
                continue
            site = row["site_code"]
            volume = float(row["volume_mm3_day"])
            sums[site][d.month] += volume
            days[site][d.month] += 1
            names[site] = row["site"]
    return sums, days, names


def read_solar() -> tuple[list[dict[str, object]], dict[int, float], dict[int, int]]:
    # Aggregate any multiple POC/network rows to one site/day before testing coverage.
    daily: dict[tuple[str, int, str], float] = defaultdict(float)
    with GENERATION.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("fuel_code") or "").lower() != "solar":
                continue
            d = date.fromisoformat(row["date"])
            daily[(row["site_code"], d.year, d.isoformat())] += float(row["generation_mwh"])

    by_site_year: dict[tuple[str, int], list[tuple[str, float]]] = defaultdict(list)
    for (site, year, d), mwh in daily.items():
        by_site_year[(site, year)].append((d, mwh))

    qualified: list[dict[str, object]] = []
    pooled_sums: dict[int, float] = defaultdict(float)
    pooled_days: dict[int, int] = defaultdict(int)

    # Equal-weight qualified plant-years when building the pooled monthly shape so a
    # larger plant does not dominate solely because of capacity.
    per_year_indices: list[dict[int, float]] = []
    for (site, year), records in sorted(by_site_year.items()):
        annual = sum(v for _, v in records)
        coverage = len(records)
        if coverage < SOLAR_MIN_DAYS or annual < SOLAR_MIN_ANNUAL_MWH:
            continue
        month_sums: dict[int, float] = defaultdict(float)
        month_days: dict[int, int] = defaultdict(int)
        for d_text, value in records:
            d = date.fromisoformat(d_text)
            month_sums[d.month] += value
            month_days[d.month] += 1
        idx = normalise_monthly_mean(month_sums, month_days)
        per_year_indices.append(idx)
        qualified.append({
            "site_code": site,
            "year": year,
            "days_with_rows": coverage,
            "annual_generation_mwh": round(annual, 3),
            "summer_nov_mar_share": round(fraction_for_months(month_sums, SUMMER_MONTHS) or 0.0, 6),
            "winter_may_aug_share": round(fraction_for_months(month_sums, WINTER_MONTHS) or 0.0, 6),
        })

    # Build a synthetic equal-weight monthly index. For convenience also return
    # pseudo-sums/days whose normalised means equal this index.
    if per_year_indices:
        mean_idx = {
            m: sum(idx[m] for idx in per_year_indices) / len(per_year_indices)
            for m in range(1, 13)
        }
    else:
        mean_idx = {m: 0.0 for m in range(1, 13)}

    for m in range(1, 13):
        pooled_sums[m] = mean_idx[m]
        pooled_days[m] = 1
    return qualified, pooled_sums, pooled_days


def main() -> None:
    hydro_sums, hydro_days, hydro_names = read_hydro()
    qualified_solar, solar_sums, solar_days = read_solar()
    solar_index = normalise_monthly_mean(solar_sums, solar_days)

    hydro_output: dict[str, object] = {}
    for site in sorted(hydro_sums):
        idx = normalise_monthly_mean(hydro_sums[site], hydro_days[site])
        corr = pearson([idx[m] for m in range(1, 13)], [solar_index[m] for m in range(1, 13)])
        peak_month = max(range(1, 13), key=lambda m: idx[m])
        hydro_output[site] = {
            "site": hydro_names.get(site, site),
            "monthly_inflow_index_vs_annual_mean_daily": {str(m): round(idx[m], 4) for m in range(1, 13)},
            "peak_inflow_month": peak_month,
            "summer_nov_mar_inflow_share": round(fraction_for_months(hydro_sums[site], SUMMER_MONTHS) or 0.0, 6),
            "winter_may_aug_inflow_share": round(fraction_for_months(hydro_sums[site], WINTER_MONTHS) or 0.0, 6),
            "monthly_correlation_with_observed_solar_shape": None if corr is None else round(corr, 4),
        }

    # The pooled solar profile is an equal-weight mean of qualified plant-year monthly
    # indices. A share cannot be reconstructed exactly from the index alone, so report
    # mean plant-year shares separately.
    mean_summer_share = (
        sum(float(r["summer_nov_mar_share"]) for r in qualified_solar) / len(qualified_solar)
        if qualified_solar else None
    )
    mean_winter_share = (
        sum(float(r["winter_may_aug_share"]) for r in qualified_solar) / len(qualified_solar)
        if qualified_solar else None
    )

    waitaki_sites = [s for s in ("TEK", "PKI", "OHU") if s in hydro_output]
    waitaki_mean_index = {
        m: (sum(float(hydro_output[s]["monthly_inflow_index_vs_annual_mean_daily"][str(m)]) for s in waitaki_sites) / len(waitaki_sites))
        if waitaki_sites else 0.0
        for m in range(1, 13)
    }
    waitaki_corr = pearson([waitaki_mean_index[m] for m in range(1, 13)], [solar_index[m] for m in range(1, 13)])

    result = {
        "definition": "Long-run monthly hydro inflow seasonality compared with observed NZ grid-scale solar generation seasonality.",
        "purpose": "Test whether solar-rich months coincide with natural refill/snowmelt periods, which determines how effectively summer solar can conserve hydro for winter.",
        "hydro_period": {"start_year": HYDRO_START_YEAR, "end_year": HYDRO_END_YEAR},
        "solar_method": {
            "source": str(GENERATION),
            "qualification": f"solar plant-year with at least {SOLAR_MIN_DAYS} days of rows and {SOLAR_MIN_ANNUAL_MWH:.0f} MWh annual generation",
            "qualified_plant_years": qualified_solar,
            "monthly_index_vs_annual_mean_daily": {str(m): round(solar_index[m], 4) for m in range(1, 13)},
            "mean_summer_nov_mar_generation_share": None if mean_summer_share is None else round(mean_summer_share, 6),
            "mean_winter_may_aug_generation_share": None if mean_winter_share is None else round(mean_winter_share, 6),
            "caution": "Current observed utility-solar history is short and dominated by tracker/bifacial plants; use this primarily for seasonal shape, not residential rooftop yield.",
        },
        "hydro_sites": hydro_output,
        "waitaki_snowfed_composite": {
            "sites": waitaki_sites,
            "monthly_inflow_index_vs_annual_mean_daily": {str(m): round(waitaki_mean_index[m], 4) for m in range(1, 13)},
            "monthly_correlation_with_observed_solar_shape": None if waitaki_corr is None else round(waitaki_corr, 4),
        },
        "interpretation_note": "Positive monthly correlation means high-solar months tend also to be high-inflow months. That can be favourable for hydro conservation because solar can reduce turbine releases while reservoirs are naturally refilling, subject to storage capacity and spill constraints.",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"Qualified solar plant-years: {len(qualified_solar)}")
    if waitaki_corr is not None:
        print(f"Waitaki snow-fed composite monthly solar correlation: {waitaki_corr:.3f}")


if __name__ == "__main__":
    main()
