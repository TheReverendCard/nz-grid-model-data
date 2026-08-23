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
from matplotlib.patches import Patch

PIPELINE = Path("data/pipeline/ea_generation_investment_pipeline_current.csv")
EA_META = Path("data/metadata/ea_generation_pipeline_source.json")
STATUS_METRICS = Path("data/metadata/ea_generation_pipeline_status_metrics.json")
HOUSEHOLD_SOLAR = Path("data/distributed_generation/model/distributed_solar_adoption_20pct.csv")
VISUAL_DIR = Path("data/visuals")
ARCHIVE_DIR = VISUAL_DIR / "archive"
LATEST_PNG = VISUAL_DIR / "generation_pipeline_capacity_monthly_latest.png"
LATEST_CSV = VISUAL_DIR / "generation_pipeline_capacity_monthly_plot_data.csv"
LATEST_META = VISUAL_DIR / "generation_pipeline_capacity_monthly_manifest.json"

START_YEAR = 2026
END_YEAR = 2040
YEARS = np.arange(START_YEAR, END_YEAR + 1)
STATUS_ORDER = ["Committed", "Actively pursued", "Other / early-stage"]
GENERATION_TECHS = [
    "Geothermal",
    "Hydro",
    "Onshore wind",
    "Offshore wind",
    "Utility solar",
    "Gas",
]
STORAGE_TECHS = ["BESS", "Pumped hydro"]
ALL_TECHS = GENERATION_TECHS + STORAGE_TECHS

COLORS = {
    "household": "#f5b642",
    "Geothermal": "#d9534f",
    "Hydro": "#3f7fbf",
    "Onshore wind": "#3f8f4f",
    "Offshore wind": "#76b76b",
    "Utility solar": "#d97706",
    "Gas": "#8c6d5a",
    "BESS": "#6f5aa8",
    "Pumped hydro": "#6a98c5",
}

STATUS_STYLE = {
    "Committed": {"alpha": 1.0, "hatch": None},
    "Actively pursued": {"alpha": 0.48, "hatch": None},
    "Other / early-stage": {"alpha": 0.18, "hatch": "\\\\"},
}


def read_pipeline() -> tuple[list[dict[str, str]], str, str]:
    with PIPELINE.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {PIPELINE}")
    return rows, rows[0]["snapshot_month"], rows[0]["captured_date"]


def capacity_profiles(rows: list[dict[str, str]]):
    known: dict[tuple[str, str], np.ndarray] = {
        (status, tech): np.zeros(len(YEARS), dtype=float)
        for status in STATUS_ORDER
        for tech in ALL_TECHS
    }
    unknown_totals: dict[tuple[str, str], float] = defaultdict(float)
    past_due_mw: dict[tuple[str, str], float] = defaultdict(float)

    for row in rows:
        status = row["status"]
        tech = row["technology"]
        if status not in STATUS_ORDER or tech not in ALL_TECHS:
            continue
        mw = float(row["capacity_mw"])
        year_text = row["expected_commissioning_year"].strip()
        if year_text.lower() == "unknown":
            unknown_totals[(status, tech)] += mw
            continue

        year = int(float(year_text))
        if year < START_YEAR:
            bucket_year = START_YEAR
            past_due_mw[(status, tech)] += mw
        elif year <= END_YEAR:
            bucket_year = year
        else:
            continue
        known[(status, tech)][bucket_year - START_YEAR] += mw

    cumulative_known = {
        key: np.cumsum(annual)
        for key, annual in known.items()
    }

    cumulative_unknown = {}
    for status in STATUS_ORDER:
        for tech in ALL_TECHS:
            total = unknown_totals[(status, tech)]
            annual = (
                np.full(len(YEARS), total / len(YEARS), dtype=float)
                if total
                else np.zeros(len(YEARS), dtype=float)
            )
            cumulative_unknown[(status, tech)] = np.cumsum(annual)

    return cumulative_known, cumulative_unknown, past_due_mw


def household_solar_capacity_mw() -> np.ndarray:
    df = pd.read_csv(HOUSEHOLD_SOLAR)
    df["month_end"] = pd.to_datetime(df["month_end"])
    df = df.sort_values("month_end")
    anchor_mw = float(df.iloc[0]["small_capacity_mw"])
    output = []
    last_value = 0.0
    for year in YEARS:
        eligible = df[df["month_end"] <= pd.Timestamp(year=int(year), month=12, day=31)]
        if eligible.empty:
            output.append(last_value)
            continue
        latest_mw = float(eligible.iloc[-1]["small_capacity_mw"])
        last_value = max(latest_mw - anchor_mw, 0.0)
        output.append(last_value)
    return np.array(output, dtype=float)


def status_tracking_note() -> str:
    if not STATUS_METRICS.exists():
        return "Status transition tracker is not yet available."
    try:
        summary = json.loads(STATUS_METRICS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Status transition tracker could not be read."

    empirical = summary.get("empirical_conversion", {})
    count = int(summary.get("snapshot_count", 0) or 0)
    first = summary.get("first_snapshot_month", "unknown")
    latest = summary.get("latest_snapshot_month", "unknown")
    mode = empirical.get("mode")

    if mode == "exact_project_identity" and empirical.get("display_ready"):
        return (
            f"Observed EA status history ({first}–{latest}, {count} snapshots): "
            f"{empirical.get('display_label')}. Historical conversion is not a guarantee "
            "that the same share of today's early-stage pipeline will proceed."
        )
    if mode == "aggregate_net_flow_proxy" and empirical.get("annotation_ready"):
        return (
            f"Observed EA status history ({first}–{latest}, {count} snapshots): "
            f"{empirical.get('annotation_label')}. This is an aggregate status-flow proxy, "
            "not an eventual project-completion probability."
        )
    if count <= 1:
        return "Status conversion tracker: 1 monthly EA snapshot retained; no empirical conversion rate yet."
    return (
        f"Status conversion tracker: {count} monthly EA snapshots retained ({first}–{latest}); "
        "the sample is not mature enough to display a conversion figure yet."
    )


def draw_stack(
    ax,
    x: np.ndarray,
    techs: list[str],
    known: dict[tuple[str, str], np.ndarray],
    unknown: dict[tuple[str, str], np.ndarray],
    bottom: np.ndarray,
    width: float,
    storage: bool = False,
) -> np.ndarray:
    current = bottom.copy()
    for status in STATUS_ORDER:
        for tech in techs:
            vals = known[(status, tech)]
            if np.any(vals > 0):
                style = STATUS_STYLE[status]
                storage_hatch = ".." if storage and style["hatch"] is None else style["hatch"]
                ax.bar(
                    x,
                    vals,
                    bottom=current,
                    width=width,
                    color=COLORS[tech],
                    alpha=style["alpha"],
                    hatch=storage_hatch,
                    edgecolor=COLORS[tech] if storage_hatch else None,
                    linewidth=0.35 if storage_hatch else 0.2,
                    zorder=2,
                )
                current += vals

        for tech in techs:
            vals = unknown[(status, tech)]
            if np.any(vals > 0):
                ax.bar(
                    x,
                    vals,
                    bottom=current,
                    width=width,
                    color=COLORS[tech],
                    alpha=0.09,
                    hatch="///" if not storage else "xx",
                    edgecolor=COLORS[tech],
                    linewidth=0.35,
                    zorder=2,
                )
                current += vals
    return current


def main() -> None:
    rows, snapshot_month, captured_date = read_pipeline()
    known, unknown, past_due = capacity_profiles(rows)
    household = household_solar_capacity_mw()

    old_manifest = {}
    if LATEST_META.exists():
        try:
            old_manifest = json.loads(LATEST_META.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old_manifest = {}
    prior_month = old_manifest.get("snapshot_month")

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if prior_month and prior_month != snapshot_month:
        if LATEST_PNG.exists():
            shutil.copy2(
                LATEST_PNG,
                ARCHIVE_DIR / f"generation_pipeline_capacity_{prior_month}.png",
            )
        if LATEST_CSV.exists():
            shutil.copy2(
                LATEST_CSV,
                ARCHIVE_DIR / f"generation_pipeline_capacity_{prior_month}.csv",
            )

    fig, ax = plt.subplots(figsize=(12, 10))
    generation_x = YEARS - 0.12
    storage_x = YEARS + 0.34
    generation_width = 0.68
    storage_width = 0.20

    generation_bottom = household.copy()
    ax.bar(
        generation_x,
        household,
        width=generation_width,
        color=COLORS["household"],
        alpha=0.95,
        zorder=2,
    )
    generation_top = draw_stack(
        ax,
        generation_x,
        GENERATION_TECHS,
        known,
        unknown,
        generation_bottom,
        generation_width,
        storage=False,
    )

    storage_bottom = np.zeros(len(YEARS), dtype=float)
    storage_top = draw_stack(
        ax,
        storage_x,
        STORAGE_TECHS,
        known,
        unknown,
        storage_bottom,
        storage_width,
        storage=True,
    )

    ymax = max(
        5_000.0,
        math.ceil(max(float(generation_top.max()), float(storage_top.max())) / 5_000.0) * 5_000.0 + 5_000.0,
    )
    ax.set_ylim(0, ymax)
    ax.set_yticks(np.arange(0, ymax + 0.1, 5_000))
    ax.set_xticks(YEARS)
    ax.set_xlabel("Year")
    ax.set_ylabel("Cumulative pipeline capacity / storage power (MW)")
    ax.set_title(f"NZ generation investment pipeline: cumulative nameplate capacity — {snapshot_month}")
    ax.grid(axis="y", alpha=0.20, zorder=0)

    handles = [
        Patch(facecolor=COLORS["household"], label="Projected household solar (incremental; 20% ICP ceiling)"),
        Patch(facecolor=COLORS["Geothermal"], label="Geothermal"),
        Patch(facecolor=COLORS["Hydro"], label="Hydro"),
        Patch(facecolor=COLORS["Onshore wind"], label="Onshore wind"),
        Patch(facecolor=COLORS["Offshore wind"], label="Offshore wind"),
        Patch(facecolor=COLORS["Utility solar"], label="Utility solar"),
        Patch(facecolor=COLORS["Gas"], label="Gas"),
        Patch(facecolor=COLORS["BESS"], hatch="..", edgecolor=COLORS["BESS"], label="BESS storage power (narrow bars)"),
        Patch(facecolor=COLORS["Pumped hydro"], hatch="..", edgecolor=COLORS["Pumped hydro"], label="Pumped-hydro storage power (narrow bars)"),
        Patch(facecolor="#777777", alpha=1.0, label="Committed"),
        Patch(facecolor="#777777", alpha=0.48, label="Actively pursued"),
        Patch(facecolor="#777777", alpha=0.18, hatch="\\\\", edgecolor="#666666", label="Other / early-stage"),
        Patch(facecolor="#aaaaaa", alpha=0.09, hatch="///", edgecolor="#777777", label="Unknown date, evenly allocated"),
    ]
    ax.legend(
        handles=handles,
        title="Technology / status",
        loc="upper left",
        fontsize=7.7,
        title_fontsize=9,
        frameon=True,
        ncol=2,
    )

    checked = "not yet checked"
    if EA_META.exists():
        try:
            checked = json.loads(EA_META.read_text(encoding="utf-8")).get("checked_at_utc", checked)
        except (OSError, json.JSONDecodeError):
            pass

    past_due_total = sum(past_due.values())
    method = (
        "Method: each dated project contributes its full nameplate MW from its stated commissioning year onward. "
        f"Projects with dates before {START_YEAR} are treated as present from {START_YEAR} because they remain in the retained pipeline snapshot "
        f"({past_due_total:,.0f} MW in this snapshot). Unknown-date MW is spread evenly across {START_YEAR}–{END_YEAR} only to show scale/timing uncertainty."
    )
    sources = (
        f"Sources: Electricity Authority Generation Investment Pipeline snapshot {snapshot_month} (captured {captured_date}; source checked {checked}); "
        "Electricity Authority distributed-generation data for the household-solar scenario."
    )
    disclaimer = (
        "Interpretation: MW is nameplate capacity, not firm/derated capacity and not annual energy. Wind/solar output varies with weather; geothermal, hydro and gas have different availability/fuel constraints. "
        "BESS and pumped hydro are plotted separately as storage discharge/connection power and require energy-duration/storage assumptions. The full pipeline is an upper envelope, not a forecast. "
        + status_tracking_note()
    )
    fig.subplots_adjust(bottom=0.25)
    fig.text(0.06, 0.122, method, fontsize=7.4, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.078, sources, fontsize=7.4, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.040, disclaimer, fontsize=7.4, fontweight="bold", ha="left", va="top", wrap=True)

    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(LATEST_PNG, dpi=180, bbox_inches="tight")
    plt.close(fig)

    plot = pd.DataFrame(
        {
            "year": YEARS,
            "household_solar_incremental_mw": household,
            "generation_pipeline_total_mw": generation_top - household,
            "generation_plus_household_total_mw": generation_top,
            "storage_power_total_mw": storage_top,
            "combined_generation_and_storage_power_mw": generation_top + storage_top,
        }
    )
    for status in STATUS_ORDER:
        key_status = status.lower().replace(" / ", "_").replace(" ", "_")
        for tech in ALL_TECHS:
            key_tech = tech.lower().replace(" ", "_").replace("-", "_")
            plot[f"{key_status}_{key_tech}_known_date_mw"] = known[(status, tech)]
            plot[f"{key_status}_{key_tech}_unknown_date_mw"] = unknown[(status, tech)]
    plot.to_csv(LATEST_CSV, index=False)

    manifest = {
        "snapshot_month": snapshot_month,
        "captured_date": captured_date,
        "latest_png": str(LATEST_PNG),
        "latest_csv": str(LATEST_CSV),
        "status_metrics": str(STATUS_METRICS),
        "generation_technologies": GENERATION_TECHS,
        "storage_power_technologies": STORAGE_TECHS,
        "past_due_pipeline_mw_included_from_start_year": round(past_due_total, 6),
        "unknown_date_policy": f"Evenly allocated across {START_YEAR}-{END_YEAR} for display only",
        "archived_previous_month": prior_month if prior_month and prior_month != snapshot_month else None,
    }
    LATEST_META.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {LATEST_PNG}")
    print(f"Wrote {LATEST_CSV}")
    if manifest["archived_previous_month"]:
        print(f"Archived previous capacity chart month: {manifest['archived_previous_month']}")


if __name__ == "__main__":
    main()
