from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt

SECURITY_SRC = Path("data/jade/model/weekly_security_summary.csv")
THERMAL_SRC = Path("data/jade/model/weekly_thermal_dispatch_summary.csv")
VALLEY_BINS_SRC = Path("data/jade/model/thermal_valley_storage_bins.csv")
VALLEY_METRICS_SRC = Path("data/jade/model/thermal_valley_metrics.json")
OUT_DIR = Path("data/visuals")


def iso_monday(year: int, week: int) -> date:
    return date.fromisocalendar(year, week, 1)


def load_security_rows() -> list[dict[str, float | int | date]]:
    rows: list[dict[str, float | int | date]] = []
    with SECURITY_SRC.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            year = int(row["calendar_year"])
            week = int(row["calendar_week"])
            rows.append({
                "date": iso_monday(year, week),
                "year": year,
                "week": week,
                "p05": float(row["stored_energy_p05_gwh"]),
                "p25": float(row["stored_energy_p25_gwh"]),
                "median": float(row["stored_energy_median_gwh"]),
                "p75": float(row["stored_energy_p75_gwh"]),
                "p95": float(row["stored_energy_p95_gwh"]),
                "thermal_share": float(row["thermal_cost_positive_share"]),
                "lost_load_share": float(row["lost_load_positive_share"]),
            })
    return rows


def load_thermal_rows() -> list[dict[str, float | int | date]]:
    rows: list[dict[str, float | int | date]] = []
    with THERMAL_SRC.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            year = int(row["calendar_year"])
            week = int(row["calendar_week"])
            rows.append({
                "date": iso_monday(year, week),
                "year": year,
                "week": week,
                "p05_gwh": float(row["thermal_generation_p05_gwh"]),
                "p25_gwh": float(row["thermal_generation_p25_gwh"]),
                "median_gwh": float(row["thermal_generation_median_gwh"]),
                "p75_gwh": float(row["thermal_generation_p75_gwh"]),
                "p95_gwh": float(row["thermal_generation_p95_gwh"]),
                "positive_share": float(row["thermal_generation_positive_share"]),
                "peak_median_mw": float(row["thermal_peak_block_median_mw"]),
                "peak_p95_mw": float(row["thermal_peak_block_p95_mw"]),
            })
    return rows


def load_valley_bins() -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    with VALLEY_BINS_SRC.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({
                "storage_mean_gwh": float(row["storage_mean_gwh"]),
                "thermal_positive_share": float(row["thermal_positive_share"]),
                "thermal_generation_mean_gwh": float(row["thermal_generation_mean_gwh"]),
                "thermal_generation_median_gwh": float(row["thermal_generation_median_gwh"]),
                "thermal_peak_mean_mw": float(row["thermal_peak_mean_mw"]),
            })
    return rows


def load_valley_metrics() -> dict[str, float]:
    with VALLEY_METRICS_SRC.open(encoding="utf-8") as f:
        return json.load(f)["metrics"]


def storage_chart(rows: list[dict[str, float | int | date]]) -> None:
    dates = [r["date"] for r in rows]
    p05 = [r["p05"] for r in rows]
    p25 = [r["p25"] for r in rows]
    median = [r["median"] for r in rows]
    p75 = [r["p75"] for r in rows]
    p95 = [r["p95"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.fill_between(dates, p05, p95, alpha=0.18, label="P5–P95")
    ax.fill_between(dates, p25, p75, alpha=0.32, label="P25–P75")
    ax.plot(dates, median, linewidth=2.2, label="Median")
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Stored hydro energy (GWh)")
    ax.set_title("JADE seasonal hydro storage outlook")
    ax.set_xlabel("Week")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.text(
        0.01,
        0.01,
        "Source: NZ Electricity Authority JADE published weekly simulation outputs. "
        "Bands show the distribution across stochastic inflow simulations.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(OUT_DIR / "jade_seasonal_hydro_storage.png", dpi=180)
    plt.close(fig)


def thermal_risk_chart(rows: list[dict[str, float | int | date]]) -> None:
    dates = [r["date"] for r in rows]
    thermal = [100 * r["thermal_share"] for r in rows]
    lost_load = [100 * r["lost_load_share"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.plot(dates, thermal, linewidth=2.2, label="Simulations with thermal cost")
    ax.plot(dates, lost_load, linewidth=2.2, label="Simulations with lost-load cost")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Share of simulations (%)")
    ax.set_title("JADE seasonal thermal and shortage exposure")
    ax.set_xlabel("Week")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.text(
        0.01,
        0.01,
        "Thermal cost is a JADE cost output. Lost-load cost indicates modeled shortage exposure.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "jade_thermal_shortage_exposure.png", dpi=180)
    plt.close(fig)


def thermal_dispatch_chart(rows: list[dict[str, float | int | date]]) -> None:
    dates = [r["date"] for r in rows]
    p05 = [r["p05_gwh"] for r in rows]
    p25 = [r["p25_gwh"] for r in rows]
    median = [r["median_gwh"] for r in rows]
    p75 = [r["p75_gwh"] for r in rows]
    p95 = [r["p95_gwh"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.fill_between(dates, p05, p95, alpha=0.18, label="P5–P95")
    ax.fill_between(dates, p25, p75, alpha=0.32, label="P25–P75")
    ax.plot(dates, median, linewidth=2.2, label="Median")
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Thermal generation (GWh/week)")
    ax.set_title("JADE seasonal thermal generation requirement")
    ax.set_xlabel("Week")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.text(
        0.01,
        0.01,
        "Source: NZ Electricity Authority JADE. Thermal GWh is derived directly from JADE thermal_use MW "
        "multiplied by the published hours_per_block.csv durations for each load block.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "jade_weekly_thermal_dispatch.png", dpi=180)
    plt.close(fig)


def thermal_peak_chart(rows: list[dict[str, float | int | date]]) -> None:
    dates = [r["date"] for r in rows]
    median = [r["peak_median_mw"] for r in rows]
    p95 = [r["peak_p95_mw"] for r in rows]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, median, linewidth=2.2, label="Median")
    ax.plot(dates, p95, linewidth=2.0, label="P95")
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Thermal dispatch (MW)")
    ax.set_title("JADE thermal capacity called on during the highest-use load block")
    ax.set_xlabel("Week")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.autofmt_xdate()
    fig.text(
        0.01,
        0.01,
        "This is modeled thermal output in the highest-use JADE load block, not installed thermal capacity.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "jade_thermal_peak_dispatch.png", dpi=180)
    plt.close(fig)


def thermal_valley_story_chart(
    security_rows: list[dict[str, float | int | date]],
    thermal_rows: list[dict[str, float | int | date]],
    valley_bins: list[dict[str, float]],
    metrics: dict[str, float],
) -> None:
    dates = [r["date"] for r in security_rows]
    storage_median = [r["median"] for r in security_rows]
    storage_p05 = [r["p05"] for r in security_rows]
    storage_p95 = [r["p95"] for r in security_rows]
    thermal_dates = [r["date"] for r in thermal_rows]
    thermal_median = [r["median_gwh"] for r in thermal_rows]
    thermal_p95 = [r["p95_gwh"] for r in thermal_rows]

    fig, axes = plt.subplots(3, 1, figsize=(12, 11), gridspec_kw={"height_ratios": [1.1, 1.0, 1.0]})

    ax = axes[0]
    ax.fill_between(dates, storage_p05, storage_p95, alpha=0.18, label="P5–P95")
    ax.plot(dates, storage_median, linewidth=2.2, label="Median storage")
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Stored hydro (GWh)")
    ax.set_title("The hydro valley and the thermal generation that fills it")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, loc="upper right")

    ax = axes[1]
    ax.plot(thermal_dates, thermal_median, linewidth=2.2, label="Median thermal GWh/week")
    ax.plot(thermal_dates, thermal_p95, linewidth=1.8, label="P95")
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Thermal generation (GWh/week)")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, loc="upper right")

    ax = axes[2]
    x = [r["storage_mean_gwh"] for r in valley_bins]
    y = [r["thermal_generation_mean_gwh"] for r in valley_bins]
    ax.plot(x, y, marker="o", linewidth=2.0)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Stored hydro energy (GWh, 500-GWh bins)")
    ax.set_ylabel("Mean thermal generation (GWh/week)")
    ax.grid(alpha=0.25)
    within = metrics["within_week_storage_vs_thermal_gwh_correlation"]
    raw = metrics["raw_storage_vs_thermal_gwh_correlation"]
    ax.text(
        0.02,
        0.94,
        f"Within-week correlation across 91 hydro simulations: r = {within:.2f}\n"
        f"Raw all-week correlation: r = {raw:.2f}",
        transform=ax.transAxes,
        va="top",
        fontsize=10,
    )

    fig.autofmt_xdate()
    fig.text(
        0.01,
        0.01,
        "Source: NZ Electricity Authority JADE published Week 52 stochastic simulations. "
        "The within-week statistic removes the common seasonal week level before correlation. "
        "Association does not imply storage alone causes thermal dispatch; JADE also responds to demand, inflows, outages, transmission, fuel costs and forward-looking water values.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "jade_hydro_thermal_valley_story.png", dpi=180)
    plt.close(fig)


def main() -> None:
    security_rows = load_security_rows()
    thermal_rows = load_thermal_rows()
    if not security_rows:
        raise RuntimeError(f"No rows found in {SECURITY_SRC}")
    if not thermal_rows:
        raise RuntimeError(f"No rows found in {THERMAL_SRC}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    storage_chart(security_rows)
    thermal_risk_chart(security_rows)
    thermal_dispatch_chart(thermal_rows)
    thermal_peak_chart(thermal_rows)
    if VALLEY_BINS_SRC.exists() and VALLEY_METRICS_SRC.exists():
        thermal_valley_story_chart(
            security_rows,
            thermal_rows,
            load_valley_bins(),
            load_valley_metrics(),
        )
    print(f"Wrote JADE visual story charts to {OUT_DIR}")


if __name__ == "__main__":
    main()
