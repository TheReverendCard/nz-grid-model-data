from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt

SECURITY_SRC = Path("data/jade/model/weekly_security_summary.csv")
THERMAL_SRC = Path("data/jade/model/weekly_thermal_dispatch_summary.csv")
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
    print(f"Wrote JADE visual story charts to {OUT_DIR}")


if __name__ == "__main__":
    main()
