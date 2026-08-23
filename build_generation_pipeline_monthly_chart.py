from __future__ import annotations

import csv
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

PIPELINE = Path("data/pipeline/ea_generation_investment_pipeline_current.csv")
EA_META = Path("data/metadata/ea_generation_pipeline_source.json")
DEMAND = Path("data/mbie/edgs2024/model/total_electricity_demand.csv")
HOUSEHOLD_SOLAR = Path("data/distributed_generation/model/distributed_solar_adoption_20pct.csv")
VISUAL_DIR = Path("data/visuals")
ARCHIVE_DIR = VISUAL_DIR / "archive"
LATEST_PNG = VISUAL_DIR / "generation_pipeline_monthly_latest.png"
LATEST_CSV = VISUAL_DIR / "generation_pipeline_monthly_plot_data.csv"
LATEST_META = VISUAL_DIR / "generation_pipeline_monthly_manifest.json"

START_YEAR = 2026
END_YEAR = 2040
YEARS = np.arange(START_YEAR, END_YEAR + 1)
BASELINE_TWH = 42.398
SOLAR_YIELD_GWH_PER_MW_YEAR = 1.3

CAPACITY_FACTORS = {
    "Geothermal": 0.90,
    "Hydro": 0.50,
    "Onshore wind": 0.40,
    "Offshore wind": 0.50,
    "Utility solar": 0.20,
    "Gas": 0.15,
}
TECHS = list(CAPACITY_FACTORS)
STATUS_ORDER = ["Committed", "Actively pursued", "Other / early-stage"]

COLORS = {
    "baseline": "#8a8a8a",
    "household": "#f5b642",
    "Geothermal": "#d9534f",
    "Hydro": "#3f7fbf",
    "Onshore wind": "#3f8f4f",
    "Offshore wind": "#76b76b",
    "Utility solar": "#d97706",
    "Gas": "#8c6d5a",
}


def read_pipeline() -> tuple[list[dict[str, str]], str, str]:
    with PIPELINE.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {PIPELINE}")
    return rows, rows[0]["snapshot_month"], rows[0]["captured_date"]


def generation_profile(annual_mw: np.ndarray, cf: float) -> np.ndarray:
    cumulative_mw = np.cumsum(annual_mw)
    effective_mw = cumulative_mw - 0.5 * annual_mw
    return effective_mw * cf * 8760.0 / 1_000_000.0


def pipeline_profiles(rows: list[dict[str, str]]):
    known: dict[tuple[str, str], np.ndarray] = {
        (status, tech): np.zeros(len(YEARS), dtype=float)
        for status in STATUS_ORDER for tech in TECHS
    }
    unknown_totals = defaultdict(float)

    for row in rows:
        status = row["status"]
        tech = row["technology"]
        if status not in STATUS_ORDER or tech not in TECHS:
            continue
        mw = float(row["capacity_mw"])
        year_text = row["expected_commissioning_year"].strip()
        if year_text.lower() == "unknown":
            unknown_totals[tech] += mw
            continue
        year = int(float(year_text))
        if START_YEAR <= year <= END_YEAR:
            known[(status, tech)][year - START_YEAR] += mw
        # Projects before START_YEAR are treated as already represented by the baseline.

    profiles = {
        key: generation_profile(annual, CAPACITY_FACTORS[key[1]])
        for key, annual in known.items()
    }

    unknown_profiles = {}
    for tech in TECHS:
        total = unknown_totals[tech]
        annual = np.full(len(YEARS), total / len(YEARS), dtype=float) if total else np.zeros(len(YEARS))
        unknown_profiles[tech] = generation_profile(annual, CAPACITY_FACTORS[tech])
    return profiles, unknown_profiles


def household_solar_twh() -> np.ndarray:
    df = pd.read_csv(HOUSEHOLD_SOLAR)
    df["month_end"] = pd.to_datetime(df["month_end"])
    anchor_mw = float(df.iloc[0]["small_capacity_mw"])
    output = []
    for year in YEARS:
        year_rows = df[df["month_end"].dt.year == int(year)]
        if year_rows.empty:
            output.append(output[-1] if output else 0.0)
            continue
        incremental_mw = np.maximum(year_rows["small_capacity_mw"].to_numpy(dtype=float) - anchor_mw, 0.0)
        avg_incremental_mw = float(np.mean(incremental_mw))
        output.append(avg_incremental_mw * SOLAR_YIELD_GWH_PER_MW_YEAR / 1000.0)
    return np.array(output)


def demand_lines() -> dict[str, np.ndarray]:
    df = pd.read_csv(DEMAND)
    result = {}
    for scenario in ["Constraint", "Reference", "Innovation"]:
        rows = df[(df["Scenario"] == scenario) & (df["TimePeriod"].between(START_YEAR, END_YEAR))]
        rows = rows.sort_values("TimePeriod")
        if len(rows) != len(YEARS):
            raise RuntimeError(f"Expected {len(YEARS)} annual {scenario} demand rows, found {len(rows)}")
        result[scenario] = rows["Value"].to_numpy(dtype=float)
    return result


def archive_previous_latest(current_snapshot_month: str) -> str | None:
    """Archive the prior latest render only when advancing to a new snapshot month."""
    if not (LATEST_META.exists() and LATEST_PNG.exists()):
        return None
    try:
        previous = json.loads(LATEST_META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    previous_month = str(previous.get("snapshot_month") or "").strip()
    if not previous_month or previous_month == current_snapshot_month:
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_png = ARCHIVE_DIR / f"generation_pipeline_{previous_month}.png"
    shutil.copy2(LATEST_PNG, archive_png)
    if LATEST_CSV.exists():
        shutil.copy2(LATEST_CSV, ARCHIVE_DIR / f"generation_pipeline_{previous_month}.csv")
    return previous_month


def main() -> None:
    rows, snapshot_month, captured_date = read_pipeline()
    archived_month = archive_previous_latest(snapshot_month)
    profiles, unknown = pipeline_profiles(rows)
    household = household_solar_twh()
    demand = demand_lines()

    fig, ax = plt.subplots(figsize=(11, 11))
    bottom = np.full(len(YEARS), BASELINE_TWH, dtype=float)
    ax.bar(YEARS, bottom, color=COLORS["baseline"], alpha=0.72, zorder=1)

    ax.bar(YEARS, household, bottom=bottom, color=COLORS["household"], alpha=0.95, zorder=2)
    bottom += household

    style = {
        "Committed": {"alpha": 1.0, "hatch": None},
        "Actively pursued": {"alpha": 0.48, "hatch": None},
        "Other / early-stage": {"alpha": 0.18, "hatch": "\\\\"},
    }
    for status in STATUS_ORDER:
        for tech in TECHS:
            vals = profiles[(status, tech)]
            if not np.any(vals > 0):
                continue
            kwargs = style[status]
            ax.bar(
                YEARS, vals, bottom=bottom, color=COLORS[tech], alpha=kwargs["alpha"],
                hatch=kwargs["hatch"], edgecolor=COLORS[tech] if kwargs["hatch"] else None,
                linewidth=0.35 if kwargs["hatch"] else 0.2, zorder=1,
            )
            bottom += vals

    for tech in TECHS:
        vals = unknown[tech]
        if not np.any(vals > 0):
            continue
        ax.bar(
            YEARS, vals, bottom=bottom, color=COLORS[tech], alpha=0.09,
            hatch="///", edgecolor=COLORS[tech], linewidth=0.35, zorder=1,
        )
        bottom += vals

    ax.plot(YEARS, demand["Constraint"], color="#555555", linestyle="--", marker="o", linewidth=1.8, zorder=5)
    ax.plot(YEARS, demand["Reference"], color="#111111", linestyle="-", marker="o", linewidth=2.4, zorder=5)
    ax.plot(YEARS, demand["Innovation"], color="#777777", linestyle=":", marker="o", linewidth=2.0, zorder=5)

    ymax = max(160.0, math.ceil(float(bottom.max()) / 20.0) * 20.0 + 20.0)
    ax.set_ylim(0, ymax)
    ax.set_yticks(np.arange(0, ymax + 0.1, 20))
    ax.set_xticks(YEARS)
    ax.set_xlabel("Year")
    ax.set_ylabel("Annual generation potential / demand (TWh)")
    ax.set_title(f"NZ generation potential pipeline and demand growth — {snapshot_month}")
    ax.grid(axis="y", alpha=0.20, zorder=0)

    handles = [
        Patch(facecolor=COLORS["baseline"], alpha=0.72, label="2026 energy benchmark"),
        Patch(facecolor=COLORS["household"], label="Projected household solar (20% ICP ceiling)"),
        Patch(facecolor=COLORS["Geothermal"], label="Geothermal"),
        Patch(facecolor=COLORS["Hydro"], label="Hydro"),
        Patch(facecolor=COLORS["Onshore wind"], label="Onshore wind"),
        Patch(facecolor=COLORS["Offshore wind"], label="Offshore wind"),
        Patch(facecolor=COLORS["Utility solar"], label="Utility solar"),
        Patch(facecolor=COLORS["Gas"], label="Gas"),
        Patch(facecolor="#777777", alpha=1.0, label="Committed"),
        Patch(facecolor="#777777", alpha=0.48, label="Actively pursued"),
        Patch(facecolor="#777777", alpha=0.18, hatch="\\\\", edgecolor="#666666", label="Other / early-stage"),
        Patch(facecolor="#aaaaaa", alpha=0.09, hatch="///", edgecolor="#777777", label="Unknown date, evenly allocated"),
        Line2D([0], [0], color="#555555", linewidth=1.8, linestyle="--", marker="o", label="MBIE Constraint"),
        Line2D([0], [0], color="#111111", linewidth=2.4, linestyle="-", marker="o", label="MBIE Reference"),
        Line2D([0], [0], color="#777777", linewidth=2.0, linestyle=":", marker="o", label="MBIE Innovation"),
    ]
    ax.legend(handles=handles, title="Demand / generation", loc="upper left", fontsize=8.0, title_fontsize=9, frameon=True)

    checked = "not yet checked"
    if EA_META.exists():
        try:
            checked = json.loads(EA_META.read_text(encoding="utf-8")).get("checked_at_utc", checked)
        except (OSError, json.JSONDecodeError):
            pass

    method = (
        "Method: projects contribute 50% of full annual generation potential in their commissioning year and 100% thereafter. "
        "Unknown-date MW is spread evenly across 2026–2040 for display. Capacity factors: solar 20%, onshore wind 40%, offshore wind 50%, geothermal 90%, hydro 50%; gas uses 15% illustrative utilisation. Storage is excluded because it shifts rather than creates net energy."
    )
    sources = (
        f"Sources: Electricity Authority Generation Investment Pipeline snapshot {snapshot_month} (captured {captured_date}; source checked {checked}); "
        "MBIE Electricity Demand and Generation Scenarios 2024; Electricity Authority distributed-generation data. Household solar is a 20% ICP-saturation scenario, not an EA forecast."
    )
    fig.subplots_adjust(bottom=0.18)
    fig.text(0.06, 0.075, method, fontsize=7.4, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.038, sources, fontsize=7.4, ha="left", va="top", wrap=True)

    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(LATEST_PNG, dpi=180, bbox_inches="tight")

    plot = pd.DataFrame({
        "year": YEARS,
        "baseline_twh": BASELINE_TWH,
        "household_solar_twh": household,
        "mbie_constraint_twh": demand["Constraint"],
        "mbie_reference_twh": demand["Reference"],
        "mbie_innovation_twh": demand["Innovation"],
        "full_pipeline_top_twh": bottom,
    })
    for status in STATUS_ORDER:
        key_status = status.lower().replace(" / ", "_").replace(" ", "_")
        for tech in TECHS:
            key_tech = tech.lower().replace(" ", "_")
            plot[f"{key_status}_{key_tech}_twh"] = profiles[(status, tech)]
    for tech in TECHS:
        plot[f"unknown_date_{tech.lower().replace(' ', '_')}_twh"] = unknown[tech]
    plot.to_csv(LATEST_CSV, index=False)

    LATEST_META.write_text(
        json.dumps(
            {
                "snapshot_month": snapshot_month,
                "captured_date": captured_date,
                "latest_png": str(LATEST_PNG),
                "latest_csv": str(LATEST_CSV),
                "archived_previous_month": archived_month,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {LATEST_PNG}")
    print(f"Wrote {LATEST_CSV}")
    print(f"Wrote {LATEST_META}")
    if archived_month:
        print(f"Archived prior monthly render: {archived_month}")
    else:
        print("No prior monthly render needed archiving")


if __name__ == "__main__":
    main()
