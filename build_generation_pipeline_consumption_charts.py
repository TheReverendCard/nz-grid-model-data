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
DEMAND = Path("data/mbie/edgs2024/model/total_electricity_demand.csv")
HOUSEHOLD_SOLAR = Path("data/distributed_generation/model/distributed_solar_adoption_20pct.csv")
SOLAR_MONTHLY = Path("data/distributed_generation/model/national_solar_all_monthly.csv")
SOLAR_SIZE = Path("data/distributed_generation/model/solar_size_buckets_current.json")
STATUS_METRICS = Path("data/metadata/ea_generation_pipeline_status_metrics.json")
VISUAL_DIR = Path("data/visuals")
ARCHIVE_DIR = VISUAL_DIR / "archive" / "consumption_pipeline"

TOTAL_PNG = VISUAL_DIR / "generation_pipeline_consumption_equivalent_latest.png"
TOTAL_CSV = VISUAL_DIR / "generation_pipeline_consumption_equivalent_plot_data.csv"
GROWTH_PNG = VISUAL_DIR / "generation_pipeline_consumption_growth_latest.png"
GROWTH_CSV = VISUAL_DIR / "generation_pipeline_consumption_growth_plot_data.csv"
MANIFEST = VISUAL_DIR / "generation_pipeline_consumption_manifest.json"

START_YEAR = 2026
END_YEAR = 2040
YEARS = np.arange(START_YEAR, END_YEAR + 1)

# MBIE Energy in New Zealand actuals.
BASE_YEAR = 2025
BASE_CONSUMPTION_TWH = 40.583
BASE_GENERATION_TWH = 44.140

# 2024: 40.002 TWh consumption / 43.879 TWh generation.
# 2025: 40.583 TWh consumption / 44.140 TWh generation.
# Pool the two years so one hydro year does not determine the conversion.
DELIVERY_RATIO = (40.002 + 40.583) / (43.879 + 44.140)

# Calibrated from MBIE 2024-25 actual generation and average installed capacity.
# Solar uses average start/end-year capacity because capacity grew rapidly.
# Onshore wind and geothermal also use average start/end-year capacity.
CAPACITY_FACTORS = {
    "Geothermal": 0.7966,
    "Hydro": 0.50,
    "Onshore wind": 0.3583,
    "Offshore wind": 0.45,
    "Utility solar": 0.1523,
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


def published_generation_gwh(row: dict[str, str]) -> float | None:
    """Prefer an explicit annual-generation field if a future pipeline source supplies one."""
    for key in ("published_annual_generation_gwh", "annual_generation_gwh"):
        value = (row.get(key) or "").strip()
        if not value:
            continue
        try:
            parsed = float(value)
        except ValueError:
            continue
        if parsed > 0:
            return parsed
    return None


def profile_from_annual_additions(annual_twh: np.ndarray) -> np.ndarray:
    # With only a commissioning year, assume half-year output in the first year.
    return np.cumsum(annual_twh) - 0.5 * annual_twh


def capacity_to_consumption_twh(mw: float, tech: str) -> float:
    gross_twh = mw * CAPACITY_FACTORS[tech] * 8760.0 / 1_000_000.0
    return gross_twh * DELIVERY_RATIO


def pipeline_profiles(rows: list[dict[str, str]]):
    known: dict[tuple[str, str], np.ndarray] = {
        (status, tech): np.zeros(len(YEARS), dtype=float)
        for status in STATUS_ORDER
        for tech in TECHS
    }
    unknown_totals: dict[tuple[str, str], float] = defaultdict(float)
    explicit_generation_rows = 0
    modelled_capacity_rows = 0
    past_due_mw = 0.0

    for row in rows:
        status = row["status"]
        tech = row["technology"]
        if status not in STATUS_ORDER or tech not in TECHS:
            continue

        mw = float(row["capacity_mw"])
        explicit_gwh = published_generation_gwh(row)
        if explicit_gwh is not None:
            annual_consumption_twh = explicit_gwh / 1000.0 * DELIVERY_RATIO
            explicit_generation_rows += 1
        else:
            annual_consumption_twh = capacity_to_consumption_twh(mw, tech)
            modelled_capacity_rows += 1

        year_text = row["expected_commissioning_year"].strip()
        if year_text.lower() == "unknown":
            unknown_totals[(status, tech)] += annual_consumption_twh
            continue

        year = int(float(year_text))
        if year < START_YEAR:
            bucket_year = START_YEAR
            past_due_mw += mw
        elif year <= END_YEAR:
            bucket_year = year
        else:
            continue
        known[(status, tech)][bucket_year - START_YEAR] += annual_consumption_twh

    profiles = {
        key: profile_from_annual_additions(annual)
        for key, annual in known.items()
    }

    unknown_profiles: dict[tuple[str, str], np.ndarray] = {}
    for status in STATUS_ORDER:
        for tech in TECHS:
            total = unknown_totals[(status, tech)]
            annual = (
                np.full(len(YEARS), total / len(YEARS), dtype=float)
                if total
                else np.zeros(len(YEARS), dtype=float)
            )
            unknown_profiles[(status, tech)] = profile_from_annual_additions(annual)

    return profiles, unknown_profiles, {
        "explicit_generation_rows": explicit_generation_rows,
        "modelled_capacity_rows": modelled_capacity_rows,
        "past_due_mw_bucketed_into_2026": round(past_due_mw, 3),
    }


def demand_lines() -> dict[str, np.ndarray]:
    df = pd.read_csv(DEMAND)
    result = {}
    for scenario in ["Constraint", "Reference", "Innovation"]:
        rows = df[
            (df["Scenario"] == scenario)
            & (df["TimePeriod"].between(START_YEAR, END_YEAR))
        ].sort_values("TimePeriod")
        if len(rows) != len(YEARS):
            raise RuntimeError(
                f"Expected {len(YEARS)} annual {scenario} demand rows, found {len(rows)}"
            )
        result[scenario] = rows["Value"].to_numpy(dtype=float)
    return result


def estimated_2025_small_solar_mw() -> float:
    monthly = pd.read_csv(SOLAR_MONTHLY)
    dec = monthly[monthly["month_end"] == "2025-12-31"]
    if dec.empty:
        raise RuntimeError("Missing 2025-12-31 national solar row")
    total_2025_mw = float(dec.iloc[0]["installed_capacity_mw"])

    size = json.loads(SOLAR_SIZE.read_text(encoding="utf-8"))
    small_mw = float(size["model_groups"]["small_lt_25_kw"]["capacity_mw"])
    national_mw = float(size["national_observed"]["installed_capacity_mw"])
    if national_mw <= 0:
        raise RuntimeError("Invalid current national solar capacity")
    return total_2025_mw * (small_mw / national_mw)


def household_solar_consumption_twh() -> tuple[np.ndarray, float]:
    df = pd.read_csv(HOUSEHOLD_SOLAR)
    df["month_end"] = pd.to_datetime(df["month_end"])
    anchor_2025_mw = estimated_2025_small_solar_mw()

    # Fill Jan-Jul 2026 by interpolation between the estimated Dec-2025
    # small-solar capacity and the first observed/modelled Aug-2026 value.
    first_date = df.iloc[0]["month_end"]
    first_mw = float(df.iloc[0]["small_capacity_mw"])
    early_dates = pd.date_range("2026-01-31", first_date - pd.offsets.MonthEnd(1), freq="ME")
    early_rows = []
    n_steps = len(early_dates) + 1
    for idx, d in enumerate(early_dates, start=1):
        mw = anchor_2025_mw + (first_mw - anchor_2025_mw) * idx / n_steps
        early_rows.append({"month_end": d, "small_capacity_mw": mw})
    if early_rows:
        df = pd.concat([pd.DataFrame(early_rows), df], ignore_index=True)
        df = df.sort_values("month_end")

    result = []
    for year in YEARS:
        rows = df[df["month_end"].dt.year == int(year)]
        if rows.empty:
            result.append(result[-1] if result else 0.0)
            continue
        incremental_mw = np.maximum(
            rows["small_capacity_mw"].to_numpy(dtype=float) - anchor_2025_mw,
            0.0,
        )
        avg_incremental_mw = float(np.mean(incremental_mw))
        gross_twh = avg_incremental_mw * CAPACITY_FACTORS["Utility solar"] * 8760.0 / 1_000_000.0
        result.append(gross_twh * DELIVERY_RATIO)
    return np.array(result), anchor_2025_mw


def status_tracking_note() -> str:
    if not STATUS_METRICS.exists():
        return "Status-transition history is not yet available."
    try:
        summary = json.loads(STATUS_METRICS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Status-transition history could not be read."
    count = int(summary.get("snapshot_count", 0) or 0)
    first = summary.get("first_snapshot_month", "unknown")
    latest = summary.get("latest_snapshot_month", "unknown")
    if count <= 1:
        return "Only one monthly EA snapshot is retained so far; no project-conversion rate is assumed."
    return (
        f"{count} EA monthly snapshots are retained ({first}-{latest}); status opacity is descriptive, "
        "not a probability that projects will proceed."
    )


def add_pipeline_bars(ax, household, profiles, unknown, baseline=None):
    bottom = (
        np.full(len(YEARS), baseline, dtype=float)
        if baseline is not None
        else np.zeros(len(YEARS), dtype=float)
    )

    if baseline is not None:
        ax.bar(
            YEARS,
            bottom,
            color=COLORS["baseline"],
            alpha=0.72,
            zorder=1,
        )

    ax.bar(
        YEARS,
        household,
        bottom=bottom,
        color=COLORS["household"],
        alpha=0.95,
        zorder=2,
    )
    bottom = bottom + household

    for status in STATUS_ORDER:
        for tech in TECHS:
            vals = profiles[(status, tech)]
            if not np.any(vals > 0):
                continue
            style = STATUS_STYLE[status]
            ax.bar(
                YEARS,
                vals,
                bottom=bottom,
                color=COLORS[tech],
                alpha=style["alpha"],
                hatch=style["hatch"],
                edgecolor=COLORS[tech] if style["hatch"] else None,
                linewidth=0.35 if style["hatch"] else 0.2,
                zorder=1,
            )
            bottom += vals

    for status in STATUS_ORDER:
        for tech in TECHS:
            vals = unknown[(status, tech)]
            if not np.any(vals > 0):
                continue
            ax.bar(
                YEARS,
                vals,
                bottom=bottom,
                color=COLORS[tech],
                alpha=0.08,
                hatch="///",
                edgecolor=COLORS[tech],
                linewidth=0.3,
                zorder=1,
            )
            bottom += vals

    return bottom


def legend_handles(total_chart: bool) -> list:
    handles = []
    if total_chart:
        handles.append(
            Patch(
                facecolor=COLORS["baseline"],
                alpha=0.72,
                label="2025 observed electricity consumption",
            )
        )
    handles.extend(
        [
            Patch(facecolor=COLORS["household"], label="Additional small distributed solar (20% ICP ceiling)"),
            Patch(facecolor=COLORS["Geothermal"], label="Geothermal"),
            Patch(facecolor=COLORS["Hydro"], label="Hydro"),
            Patch(facecolor=COLORS["Onshore wind"], label="Onshore wind"),
            Patch(facecolor=COLORS["Offshore wind"], label="Offshore wind"),
            Patch(facecolor=COLORS["Utility solar"], label="Utility solar"),
            Patch(facecolor=COLORS["Gas"], label="Gas"),
            Patch(facecolor="#777777", alpha=1.0, label="Committed"),
            Patch(facecolor="#777777", alpha=0.48, label="Actively pursued"),
            Patch(
                facecolor="#777777",
                alpha=0.18,
                hatch="\\\\",
                edgecolor="#666666",
                label="Other / early-stage",
            ),
            Patch(
                facecolor="#aaaaaa",
                alpha=0.08,
                hatch="///",
                edgecolor="#777777",
                label="Unknown date, evenly allocated",
            ),
            Line2D([0], [0], color="#555555", linestyle="--", marker="o", linewidth=1.8, label="MBIE Constraint"),
            Line2D([0], [0], color="#111111", linestyle="-", marker="o", linewidth=2.4, label="MBIE Reference"),
            Line2D([0], [0], color="#777777", linestyle=":", marker="o", linewidth=2.0, label="MBIE Innovation"),
        ]
    )
    return handles


def plot_demand(ax, demand, growth=False):
    for scenario, style in [
        ("Constraint", {"color": "#555555", "linestyle": "--", "linewidth": 1.8}),
        ("Reference", {"color": "#111111", "linestyle": "-", "linewidth": 2.4}),
        ("Innovation", {"color": "#777777", "linestyle": ":", "linewidth": 2.0}),
    ]:
        vals = demand[scenario] - BASE_CONSUMPTION_TWH if growth else demand[scenario]
        ax.plot(YEARS, vals, marker="o", zorder=5, **style)


def archive_previous(prior_month: str | None, snapshot_month: str):
    if not prior_month or prior_month == snapshot_month:
        return
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for path in (TOTAL_PNG, TOTAL_CSV, GROWTH_PNG, GROWTH_CSV):
        if path.exists():
            suffix = path.suffix
            ARCHIVE_DIR.joinpath(
                f"{path.stem.replace('_latest', '')}_{prior_month}{suffix}"
            ).write_bytes(path.read_bytes())


def save_total(snapshot_month, household, profiles, unknown, demand, method_note):
    fig, ax = plt.subplots(figsize=(11, 11))
    top = add_pipeline_bars(
        ax,
        household,
        profiles,
        unknown,
        baseline=BASE_CONSUMPTION_TWH,
    )
    plot_demand(ax, demand, growth=False)

    ymax = max(
        80.0,
        math.ceil(max(float(top.max()), float(demand["Innovation"].max())) / 10.0) * 10.0 + 10.0,
    )
    ax.set_ylim(0, ymax)
    ax.set_yticks(np.arange(0, ymax + 0.1, 10))
    ax.set_xticks(YEARS)
    ax.set_xlabel("Year")
    ax.set_ylabel("End-use electricity consumption equivalent (TWh/year)")
    ax.set_title(f"NZ electricity consumption and generation pipeline - {snapshot_month}")
    ax.grid(axis="y", alpha=0.20, zorder=0)
    ax.legend(
        handles=legend_handles(total_chart=True),
        title="Consumption / generation",
        loc="upper left",
        fontsize=7.8,
        title_fontsize=9,
        frameon=True,
    )

    source_note = (
        "Sources: MBIE Energy in New Zealand 2025/2026 actual generation, consumption and installed-capacity data; "
        "MBIE EDGS 2024 future electricity-demand scenarios; Electricity Authority Generation Investment Pipeline and distributed-generation data."
    )
    interpretation = (
        "Interpretation: this is an annual-energy envelope, not a dispatch forecast. New renewable output can serve consumption growth, "
        "displace thermal generation, or preserve hydro rather than simply add one-for-one to annual system generation. "
        + status_tracking_note()
    )
    fig.subplots_adjust(bottom=0.245)
    fig.text(0.06, 0.125, method_note, fontsize=7.25, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.078, source_note, fontsize=7.25, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.036, interpretation, fontsize=7.25, fontweight="bold", ha="left", va="top", wrap=True)

    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(TOTAL_PNG, dpi=180, bbox_inches="tight")
    plt.close(fig)

    out = pd.DataFrame(
        {
            "year": YEARS,
            "observed_2025_consumption_baseline_twh": BASE_CONSUMPTION_TWH,
            "household_solar_consumption_equivalent_twh": household,
            "mbie_constraint_consumption_twh": demand["Constraint"],
            "mbie_reference_consumption_twh": demand["Reference"],
            "mbie_innovation_consumption_twh": demand["Innovation"],
            "full_pipeline_consumption_equivalent_top_twh": top,
        }
    )
    for status in STATUS_ORDER:
        s = status.lower().replace(" / ", "_").replace(" ", "_")
        for tech in TECHS:
            t = tech.lower().replace(" ", "_")
            out[f"{s}_{t}_consumption_equivalent_twh"] = profiles[(status, tech)]
            out[f"{s}_unknown_date_{t}_consumption_equivalent_twh"] = unknown[(status, tech)]
    out.to_csv(TOTAL_CSV, index=False)


def save_growth(snapshot_month, household, profiles, unknown, demand, method_note):
    fig, ax = plt.subplots(figsize=(11, 11))
    top = add_pipeline_bars(ax, household, profiles, unknown, baseline=None)
    plot_demand(ax, demand, growth=True)
    ax.axhline(0, color="#444444", linewidth=0.8)

    demand_growth_max = max(float((v - BASE_CONSUMPTION_TWH).max()) for v in demand.values())
    ymax = max(
        30.0,
        math.ceil(max(float(top.max()), demand_growth_max) / 5.0) * 5.0 + 5.0,
    )
    ax.set_ylim(0, ymax)
    ax.set_yticks(np.arange(0, ymax + 0.1, 5))
    ax.set_xticks(YEARS)
    ax.set_xlabel("Year")
    ax.set_ylabel("Additional end-use electricity consumption equivalent since 2025 (TWh/year)")
    ax.set_title(f"NZ new generation potential versus electricity-consumption growth - {snapshot_month}")
    ax.grid(axis="y", alpha=0.20, zorder=0)
    ax.legend(
        handles=legend_handles(total_chart=False),
        title="Growth / new generation",
        loc="upper left",
        fontsize=7.8,
        title_fontsize=9,
        frameon=True,
    )

    source_note = (
        f"Baseline: observed 2025 electricity consumption {BASE_CONSUMPTION_TWH:.3f} TWh. "
        "Demand-growth lines subtract that observed baseline from MBIE EDGS scenarios; bars show additional consumption-equivalent annual energy from the retained EA pipeline."
    )
    interpretation = (
        "Interpretation: bars show the amount of additional annual end-use electricity that new generation could support if its energy is needed. "
        "They do not imply every project is built or that all incremental generation raises total output; some will replace thermal generation or conserve hydro."
    )
    fig.subplots_adjust(bottom=0.245)
    fig.text(0.06, 0.125, method_note, fontsize=7.25, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.078, source_note, fontsize=7.25, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.036, interpretation, fontsize=7.25, fontweight="bold", ha="left", va="top", wrap=True)

    fig.savefig(GROWTH_PNG, dpi=180, bbox_inches="tight")
    plt.close(fig)

    out = pd.DataFrame(
        {
            "year": YEARS,
            "household_solar_consumption_equivalent_twh": household,
            "mbie_constraint_consumption_growth_from_2025_twh": demand["Constraint"] - BASE_CONSUMPTION_TWH,
            "mbie_reference_consumption_growth_from_2025_twh": demand["Reference"] - BASE_CONSUMPTION_TWH,
            "mbie_innovation_consumption_growth_from_2025_twh": demand["Innovation"] - BASE_CONSUMPTION_TWH,
            "full_pipeline_incremental_consumption_equivalent_twh": top,
        }
    )
    for status in STATUS_ORDER:
        s = status.lower().replace(" / ", "_").replace(" ", "_")
        for tech in TECHS:
            t = tech.lower().replace(" ", "_")
            out[f"{s}_{t}_consumption_equivalent_twh"] = profiles[(status, tech)]
            out[f"{s}_unknown_date_{t}_consumption_equivalent_twh"] = unknown[(status, tech)]
    out.to_csv(GROWTH_CSV, index=False)


def main() -> None:
    rows, snapshot_month, captured_date = read_pipeline()
    profiles, unknown, pipeline_meta = pipeline_profiles(rows)
    household, estimated_2025_small_mw = household_solar_consumption_twh()
    demand = demand_lines()

    old_manifest = {}
    if MANIFEST.exists():
        try:
            old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old_manifest = {}
    archive_previous(old_manifest.get("snapshot_month"), snapshot_month)

    method_note = (
        f"Method: generation is converted to end-use consumption-equivalent energy using a {DELIVERY_RATIO*100:.1f}% "
        "2024-25 MBIE consumption/generation ratio. Fallback annual capacity factors are calibrated to MBIE actual fleet data: "
        f"solar {CAPACITY_FACTORS['Utility solar']*100:.1f}%, onshore wind {CAPACITY_FACTORS['Onshore wind']*100:.1f}%, "
        f"geothermal {CAPACITY_FACTORS['Geothermal']*100:.1f}%; hydro 50%, offshore wind 45%, gas 15% utilisation. "
        "Projects contribute half-year output in their commissioning year and full-year output thereafter; unknown dates are spread across 2026-2040."
    )

    save_total(snapshot_month, household, profiles, unknown, demand, method_note)
    save_growth(snapshot_month, household, profiles, unknown, demand, method_note)

    manifest = {
        "snapshot_month": snapshot_month,
        "captured_date": captured_date,
        "base_year": BASE_YEAR,
        "observed_2025_consumption_twh": BASE_CONSUMPTION_TWH,
        "observed_2025_generation_twh": BASE_GENERATION_TWH,
        "delivery_ratio": DELIVERY_RATIO,
        "delivery_ratio_basis": {
            "2024": {"consumption_twh": 40.002, "generation_twh": 43.879},
            "2025": {"consumption_twh": 40.583, "generation_twh": 44.140},
        },
        "capacity_factors": CAPACITY_FACTORS,
        "capacity_factor_basis": {
            "Utility solar": "MBIE 2024-25 generation divided by average start/end-year installed capacity; pooled two-year factor.",
            "Onshore wind": "MBIE 2024-25 generation divided by average start/end-year installed capacity; pooled two-year factor.",
            "Geothermal": "MBIE 2024-25 generation divided by average start/end-year installed capacity; pooled two-year factor.",
            "Hydro": "50% annual-energy approximation, close to recent NZ hydro fleet utilisation; hydro energy remains hydrology constrained.",
            "Offshore wind": "45% conservative planning assumption because New Zealand has no operating offshore-wind fleet.",
            "Gas": "15% illustrative utilisation for prospective thermal capacity, not an energy availability assumption.",
        },
        "estimated_2025_small_solar_capacity_mw": estimated_2025_small_mw,
        "project_specific_generation_note": (
            "The retained EA pipeline snapshot is aggregated by status, technology and commissioning year and does not expose project identity "
            "or published annual MWh. If a future source provides published_annual_generation_gwh or annual_generation_gwh, this renderer "
            "automatically prefers that value over the technology capacity-factor fallback."
        ),
        "pipeline_conversion": pipeline_meta,
        "outputs": {
            "total_png": str(TOTAL_PNG),
            "total_csv": str(TOTAL_CSV),
            "growth_png": str(GROWTH_PNG),
            "growth_csv": str(GROWTH_CSV),
        },
        "sources": [
            "MBIE Energy in New Zealand 2026 electricity and renewables tables",
            "MBIE Electricity Demand and Generation Scenarios 2024",
            "Electricity Authority Generation Investment Pipeline",
            "Electricity Authority distributed-generation data",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {TOTAL_PNG}")
    print(f"Wrote {TOTAL_CSV}")
    print(f"Wrote {GROWTH_PNG}")
    print(f"Wrote {GROWTH_CSV}")
    print(f"Wrote {MANIFEST}")


if __name__ == "__main__":
    main()
