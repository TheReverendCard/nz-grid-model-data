from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt

SRC = Path("data/jade/model/weekly_security_summary.csv")
OUT_DIR = Path("data/visuals")


def load_rows() -> list[dict[str, float | int | date]]:
    rows: list[dict[str, float | int | date]] = []
    with SRC.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            year = int(row["calendar_year"])
            week = int(row["calendar_week"])
            rows.append({
                "date": date.fromisocalendar(year, week, 1),
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
        "Thermal cost is a JADE cost output, not thermal generation GWh. "
        "Lost-load cost indicates modeled shortage exposure.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "jade_thermal_shortage_exposure.png", dpi=180)
    plt.close(fig)


def main() -> None:
    rows = load_rows()
    if not rows:
        raise RuntimeError(f"No rows found in {SRC}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    storage_chart(rows)
    thermal_risk_chart(rows)
    print(f"Wrote JADE visual story charts to {OUT_DIR}")


if __name__ == "__main__":
    main()
