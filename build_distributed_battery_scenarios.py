from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

SOLAR_SCENARIOS = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.csv")
BATTERY_OBS = Path("data/distributed_generation/model/national_solar_battery_attachment_monthly.csv")
OUT_CSV = Path("data/distributed_generation/model/distributed_battery_attachment_scenarios.csv")
OUT_JSON = Path("data/distributed_generation/model/distributed_battery_attachment_scenarios.json")

ATTACHMENT_SATURATION = 0.95
OBS_START = "2023-11-01"
ANCHOR_MONTHS = 6
BATTERY_DURATION_HOURS = {"low": 1.0, "central": 1.5, "high": 2.0}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def logistic_from_anchor(t_years: float | np.ndarray, saturation: float, current_share: float, rate: float):
    a = saturation / current_share - 1.0
    return saturation / (1.0 + a * np.exp(-rate * t_years))


def fit_attachment_rate(obs_rows: list[dict[str, str]], latest_date: datetime, anchor_share: float) -> float:
    usable = [r for r in obs_rows if r["month_end"] >= OBS_START]
    if len(usable) < 6:
        raise RuntimeError("Not enough post-November-2023 battery attachment observations to fit")

    dates = [datetime.strptime(r["month_end"], "%Y-%m-%d") for r in usable]
    shares = np.array([float(r["battery_attachment_share_of_new_solar_pct"]) / 100.0 for r in usable])
    t = np.array([((d.year - latest_date.year) * 12 + (d.month - latest_date.month)) / 12.0 for d in dates])
    shares = np.clip(shares, 0.001, ATTACHMENT_SATURATION - 0.001)

    def objective(rate: float) -> float:
        predicted = logistic_from_anchor(t, ATTACHMENT_SATURATION, anchor_share, rate)
        weights = np.linspace(0.5, 1.0, len(shares))
        return float(np.average((predicted - shares) ** 2, weights=weights))

    result = minimize_scalar(objective, bounds=(0.01, 2.0), method="bounded")
    if not result.success:
        raise RuntimeError(f"Battery attachment fit failed: {result.message}")
    return float(result.x)


def main() -> None:
    solar_rows = read_csv(SOLAR_SCENARIOS)
    obs_rows = read_csv(BATTERY_OBS)
    if not solar_rows or not obs_rows:
        raise RuntimeError("Missing solar scenario or battery observation rows")

    latest_obs = obs_rows[-1]
    latest_date = datetime.strptime(latest_obs["month_end"], "%Y-%m-%d")
    recent = [r for r in obs_rows if r["month_end"] >= OBS_START][-ANCHOR_MONTHS:]
    recent_shares = [float(r["battery_attachment_share_of_new_solar_pct"]) / 100.0 for r in recent]
    anchor_share = min(ATTACHMENT_SATURATION - 0.001, max(0.001, float(np.median(recent_shares))))
    rate = fit_attachment_rate(obs_rows, latest_date, anchor_share)

    observed_battery_icps = float(latest_obs["solar_plus_battery_icps"])
    observed_all_solar_icps = float(latest_obs["all_solar_icps"])
    observed_stock_share = observed_battery_icps / observed_all_solar_icps if observed_all_solar_icps else 0.0
    observed_registered_capacity_mw = float(latest_obs["solar_plus_battery_registered_capacity_mw"])
    observed_avg_registered_kw = float(latest_obs["solar_plus_battery_average_registered_kw"])

    first = solar_rows[0]
    output_rows: list[dict[str, object]] = []
    scenario_names = ("low_10pct", "high_30pct")
    cumulative_battery: dict[str, float] = {}
    previous_solar: dict[str, float] = {}

    for scenario in scenario_names:
        start_solar = float(first[f"{scenario}_small_solar_icps"])
        start_battery = min(start_solar, observed_battery_icps)
        cumulative_battery[scenario] = start_battery
        previous_solar[scenario] = start_solar

    for i, solar_row in enumerate(solar_rows):
        d = datetime.strptime(solar_row["month_end"], "%Y-%m-%d")
        t_years = ((d.year - latest_date.year) * 12 + (d.month - latest_date.month)) / 12.0
        attachment = anchor_share if t_years <= 0 else float(
            logistic_from_anchor(t_years, ATTACHMENT_SATURATION, anchor_share, rate)
        )

        row: dict[str, object] = {
            "month_end": solar_row["month_end"],
            "new_solar_battery_attachment_pct": round(attachment * 100.0, 5),
        }

        for scenario in scenario_names:
            solar_icps = float(solar_row[f"{scenario}_small_solar_icps"])
            if i == 0:
                new_solar = 0.0
            else:
                new_solar = max(0.0, solar_icps - previous_solar[scenario])
                cumulative_battery[scenario] += new_solar * attachment
            cumulative_battery[scenario] = min(cumulative_battery[scenario], solar_icps)
            without_battery = max(0.0, solar_icps - cumulative_battery[scenario])
            observed_power_proxy_mw = cumulative_battery[scenario] * observed_avg_registered_kw / 1000.0

            row[f"{scenario}_small_solar_icps"] = round(solar_icps, 1)
            row[f"{scenario}_new_small_solar_icps"] = round(new_solar, 1)
            row[f"{scenario}_solar_plus_battery_icps"] = round(cumulative_battery[scenario], 1)
            row[f"{scenario}_solar_without_battery_icps"] = round(without_battery, 1)
            row[f"{scenario}_battery_stock_share_pct"] = round(
                cumulative_battery[scenario] / solar_icps * 100.0, 5
            ) if solar_icps else 0.0
            row[f"{scenario}_registered_connection_power_proxy_mw"] = round(observed_power_proxy_mw, 3)
            for duration_name, hours in BATTERY_DURATION_HOURS.items():
                row[f"{scenario}_{duration_name}_energy_proxy_mwh"] = round(observed_power_proxy_mw * hours, 3)
            previous_solar[scenario] = solar_icps

        output_rows.append(row)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0].keys()))
        writer.writeheader()
        writer.writerows(output_rows)

    year_end = {}
    for year in range(latest_date.year, 2051):
        candidates = [r for r in output_rows if str(r["month_end"]).startswith(f"{year}-")]
        if candidates:
            year_end[str(year)] = candidates[-1]

    summary = {
        "source_latest_month": latest_obs["month_end"],
        "scope": "Battery attachment to the <25 kW distributed-solar ICP population.",
        "observed_anchor": {
            "explicit_solar_plus_battery_icps": int(observed_battery_icps),
            "all_solar_icps": int(observed_all_solar_icps),
            "observed_stock_attachment_pct": round(observed_stock_share * 100.0, 5),
            "explicit_solar_plus_battery_registered_capacity_mw": observed_registered_capacity_mw,
            "explicit_average_registered_connection_kw": observed_avg_registered_kw,
            "new_install_attachment_anchor_pct": round(anchor_share * 100.0, 5),
            "anchor_method": f"Median of latest {ANCHOR_MONTHS} monthly observed new-install attachment shares.",
        },
        "capacity_assumptions": {
            "battery_duration_hours": BATTERY_DURATION_HOURS,
            "duration_policy": "Conservative provisional durations: 1.0 h low, 1.5 h central, 2.0 h high. Replace these assumptions with observed EA fleet storage data whenever defensible aggregated battery kWh become available.",
            "power_proxy": "Use the explicit EA Solar (with battery) average registered connection capacity per ICP as a conservative observed connection-power proxy. It is not battery-only nameplate power; it is the lesser of summed generation capability or inverter/injection capacity.",
            "priority": "Observed EA battery power/storage data override industry-derived or assumed values where the registry meaning is sufficiently clear.",
        },
        "method": {
            "observations": "Primary Solar+Batteries counts and registered connection-capacity data use the explicit EA GUEHMT Solar (with battery) category. Only post-November-2023 new-install observations are used for the attachment fit.",
            "new_install_curve": "Logistic attachment curve fitted to observed new-install battery attachment, anchored to the latest six-month median and capped at 95%.",
            "stock": "Projected battery-equipped solar ICPs accumulate from the observed starting stock plus the fitted share of each month's new <25 kW solar installations.",
            "energy_capacity": "Provisional MWh equals the observed connection-power proxy multiplied by 1.0/1.5/2.0 hours. This is deliberately conservative and should be replaced by aggregated registry battery kWh when publicly available.",
        },
        "fit": {
            "attachment_saturation_pct": ATTACHMENT_SATURATION * 100.0,
            "growth_rate_per_year": round(rate, 8),
        },
        "year_end": year_end,
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_CSV} ({len(output_rows)} monthly rows)")
    print(f"Wrote {OUT_JSON}")
    print(
        f"Observed battery stock: {observed_battery_icps:,.0f}/{observed_all_solar_icps:,.0f} solar ICPs "
        f"({observed_stock_share * 100:.1f}%); registered connection-power proxy={observed_registered_capacity_mw:.1f} MW "
        f"({observed_avg_registered_kw:.2f} kW/ICP); new-install anchor={anchor_share * 100:.1f}%."
    )
    print(f"Battery duration sensitivities (hours): {BATTERY_DURATION_HOURS}")


if __name__ == "__main__":
    main()
