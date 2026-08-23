from __future__ import annotations

import argparse
import calendar
import csv
import json
import math
import shutil
from datetime import date, datetime
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import minimize_scalar

HISTORY = Path("data/distributed_generation/model/solar_size_bucket_history.csv")
MONTHLY = Path("data/distributed_generation/model/national_solar_all_monthly.csv")
SCENARIO_CSV = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.csv")
SCENARIO_JSON = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.json")
PIPELINE = Path("data/pipeline/transpower_generation_storage_pipeline.csv")
OUT_DIR = Path("data/visuals")
ARCHIVE_ROOT = OUT_DIR / "archive" / "distributed_solar_size_split"
STATE = Path("data/metadata/solar_size_split_chart_state.json")
OUT_CAPACITY = OUT_DIR / "distributed_solar_size_split_capacity.png"
OUT_INSTALLS = OUT_DIR / "distributed_solar_size_split_installs.png"
OUT_DATA = OUT_DIR / "distributed_solar_size_split_plot_data.csv"

HISTORY_MONTHS = 36
FORECAST_MONTHS = 60
BAR_WIDTH = 0.24
PIPELINE_STAGES = {"Delivery", "Commissioning"}

GROUPS = (
    "small_lt_25_kw",
    "larger_distributed_25_kw_to_lt_1_mw",
    "utility_gte_1_mw",
)
LABELS = {
    "small_lt_25_kw": "<25 kW",
    "larger_distributed_25_kw_to_lt_1_mw": "25 kW–<1 MW",
    "utility_gte_1_mw": "≥1 MW",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {path}")
    return rows


def logistic(t_years: float, saturation: float, current_share: float, rate: float) -> float:
    a = saturation / current_share - 1.0
    return saturation / (1.0 + a * math.exp(-rate * t_years))


def month_delta(d: date, reference: date) -> float:
    return ((d.year - reference.year) * 12 + d.month - reference.month) / 12.0


def month_index(d: date) -> int:
    return d.year * 12 + d.month


def shift_month_end(d: date, months: int) -> date:
    value = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(value, 12)
    month = month0 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def fit_mid_rate(meta: dict) -> float:
    latest = datetime.strptime(meta["source_latest_month"], "%Y-%m-%d").date()
    current_share = float(meta["current"]["small_solar_uptake_pct"]) / 100.0
    small_share_of_solar = float(meta["current"]["small_share_of_all_solar_icps_pct"]) / 100.0

    xs: list[float] = []
    ys: list[float] = []
    for row in read_csv(MONTHLY):
        d = datetime.strptime(row["month_end"], "%Y-%m-%d").date()
        if d.year < 2018 or d > latest:
            continue
        uptake = float(row["icp_uptake_rate_pct"]) / 100.0
        xs.append(month_delta(d, latest))
        ys.append(uptake * small_share_of_solar)

    if len(ys) < 2:
        raise RuntimeError("Not enough EA uptake history to fit the independent 20% solar curve")

    x = np.array(xs, dtype=float)
    y = np.array(ys, dtype=float)
    weights = np.linspace(0.5, 1.0, len(y))

    def objective(rate: float) -> float:
        a = 0.20 / current_share - 1.0
        pred = 0.20 / (1.0 + a * np.exp(-rate * x))
        return float(np.average((pred - y) ** 2, weights=weights))

    result = minimize_scalar(objective, bounds=(0.01, 1.0), method="bounded")
    if not result.success:
        raise RuntimeError(f"20% logistic fit failed: {result.message}")
    return float(result.x)


def weekly_due(force: bool) -> bool:
    if force or not STATE.exists():
        return True
    state = json.loads(STATE.read_text(encoding="utf-8"))
    now = date.today().isocalendar()
    return state.get("last_render_iso_week") != f"{now.year}-W{now.week:02d}"


def complete_observed_snapshots() -> dict[str, dict[str, dict[str, float]]]:
    grouped: dict[str, dict[str, dict[str, float]]] = {}
    for row in read_csv(HISTORY):
        group = row["group"]
        if group not in GROUPS:
            continue
        grouped.setdefault(row["source_month"], {})[group] = {
            "icps": float(row["estimated_icps"]),
            "mw": float(row["capacity_mw"]),
        }
    complete = {
        month: values
        for month, values in grouped.items()
        if all(group in values for group in GROUPS)
    }
    if not complete:
        raise RuntimeError("No complete three-group snapshots in solar_size_bucket_history.csv")
    return dict(sorted(complete.items()))


def estimate_lumpy_utility_history(
    monthly_rows: list[dict[str, str]],
    start: date,
    anchor: date,
    anchor_utility_mw: float,
) -> tuple[dict[str, float], dict[str, object]]:
    dated = [
        (datetime.strptime(row["month_end"], "%Y-%m-%d").date(), row)
        for row in monthly_rows
    ]
    excess_by_month: dict[str, float] = {}

    for i, (d, row) in enumerate(dated):
        if d <= start or d > anchor or i == 0:
            continue
        previous_d, previous = dated[i - 1]
        if month_index(d) - month_index(previous_d) != 1:
            continue

        total_delta_mw = float(row["installed_capacity_mw"]) - float(previous["installed_capacity_mw"])
        if total_delta_mw <= 0:
            excess_by_month[d.isoformat()] = 0.0
            continue

        window = dated[max(0, i - 11) : i + 1]
        typical_new_sizes = [
            float(item["average_new_install_capacity_kw"])
            for _, item in window
            if float(item.get("average_new_install_capacity_kw") or 0.0) > 0
        ]
        typical_new_kw = median(typical_new_sizes) if typical_new_sizes else 0.0
        expected_distributed_delta_mw = float(row["new_installations"]) * typical_new_kw / 1000.0
        excess_by_month[d.isoformat()] = max(0.0, total_delta_mw - expected_distributed_delta_mw)

    raw_excess = sum(excess_by_month.values())
    if raw_excess > anchor_utility_mw and raw_excess > 0:
        scale = anchor_utility_mw / raw_excess
        opening_utility_mw = 0.0
    else:
        scale = 1.0
        opening_utility_mw = max(0.0, anchor_utility_mw - raw_excess)

    utility = opening_utility_mw
    values: dict[str, float] = {}
    for d, _ in dated:
        if d < start or d > anchor:
            continue
        if d > start:
            utility += excess_by_month.get(d.isoformat(), 0.0) * scale
        values[d.isoformat()] = utility

    values[anchor.isoformat()] = anchor_utility_mw
    return values, {
        "method": (
            "Utility-scale historical MW is reconstructed as a lumpy residual. Monthly national "
            "capacity additions above a rolling 12-month median new-system size baseline are treated "
            "as candidate utility additions, then scaled/offset so the series lands exactly on the "
            "first explicit EA street-level >=1 MW observation."
        ),
        "opening_utility_mw": opening_utility_mw,
        "raw_detected_excess_mw": raw_excess,
        "excess_scale": scale,
    }


def make_point(
    *,
    month: str,
    kind: str,
    provenance: str,
    small_icps: float,
    larger_icps: float,
    utility_icps: float,
    small_mw: float,
    larger_mw: float,
    utility_mw: float,
    official_total_icps: float | None = None,
    official_total_mw: float | None = None,
) -> dict[str, object]:
    total_icps = small_icps + larger_icps + utility_icps
    total_mw = small_mw + larger_mw + utility_mw
    return {
        "month": month,
        "kind": kind,
        "provenance": provenance,
        "small_icps": small_icps,
        "larger_icps": larger_icps,
        "utility_icps": utility_icps,
        "small_mw": small_mw,
        "larger_mw": larger_mw,
        "utility_mw": utility_mw,
        "total_solar_icps": total_icps,
        "total_solar_mw": total_mw,
        "official_total_icps": official_total_icps,
        "official_total_mw": official_total_mw,
        "reconciliation_error_icps": (
            total_icps - official_total_icps if official_total_icps is not None else None
        ),
        "reconciliation_error_mw": (
            total_mw - official_total_mw if official_total_mw is not None else None
        ),
    }


def history_points() -> tuple[list[dict[str, object]], dict[str, object]]:
    snapshots = complete_observed_snapshots()
    observed_months = list(snapshots)
    first_observed = datetime.strptime(observed_months[0], "%Y-%m-%d").date()
    latest_observed = datetime.strptime(observed_months[-1], "%Y-%m-%d").date()
    chart_start = shift_month_end(latest_observed, -HISTORY_MONTHS)

    monthly_rows = read_csv(MONTHLY)
    monthly_by_month = {row["month_end"]: row for row in monthly_rows}
    if first_observed.isoformat() not in monthly_by_month:
        raise RuntimeError(f"No national EA monthly total for first size-split month {first_observed}")

    anchor = snapshots[first_observed.isoformat()]
    anchor_total = monthly_by_month[first_observed.isoformat()]
    anchor_small_share = anchor[GROUPS[0]]["icps"] / float(anchor_total["icp_count"])

    scenario_meta = json.loads(SCENARIO_JSON.read_text(encoding="utf-8"))
    larger_rate = float(
        scenario_meta.get("larger_distributed_growth", {}).get("selected_rate_pct_per_year", 0.0)
    ) / 100.0

    utility_history, utility_notes = estimate_lumpy_utility_history(
        monthly_rows,
        chart_start,
        first_observed,
        anchor[GROUPS[2]]["mw"],
    )

    points: list[dict[str, object]] = []
    for row in monthly_rows:
        d = datetime.strptime(row["month_end"], "%Y-%m-%d").date()
        if d < chart_start or d > latest_observed:
            continue

        official_icps = float(row["icp_count"])
        official_mw = float(row["installed_capacity_mw"])
        snapshot = snapshots.get(row["month_end"])

        if snapshot is not None:
            point = make_point(
                month=row["month_end"],
                kind="observed",
                provenance="Observed size buckets from EA street-level Registry data",
                small_icps=snapshot[GROUPS[0]]["icps"],
                larger_icps=snapshot[GROUPS[1]]["icps"],
                utility_icps=snapshot[GROUPS[2]]["icps"],
                small_mw=snapshot[GROUPS[0]]["mw"],
                larger_mw=snapshot[GROUPS[1]]["mw"],
                utility_mw=snapshot[GROUPS[2]]["mw"],
                official_total_icps=official_icps,
                official_total_mw=official_mw,
            )
            point["utility_observed_baseline_mw"] = snapshot[GROUPS[2]]["mw"]
            point["utility_pipeline_provisional_mw"] = 0.0
            point["utility_pipeline_project_count"] = 0
            points.append(point)
            continue

        if d >= first_observed:
            continue

        t = month_delta(d, first_observed)
        growth_factor = math.exp(math.log1p(max(larger_rate, -0.999999)) * t)

        small_icps = official_icps * anchor_small_share
        residual_icps = max(0.0, official_icps - small_icps)
        larger_icps = min(anchor[GROUPS[1]]["icps"] * growth_factor, residual_icps)
        utility_icps = residual_icps - larger_icps

        larger_mw = min(anchor[GROUPS[1]]["mw"] * growth_factor, official_mw)
        utility_mw = min(
            max(0.0, utility_history.get(row["month_end"], 0.0)),
            max(0.0, official_mw - larger_mw),
        )
        small_mw = official_mw - larger_mw - utility_mw

        point = make_point(
            month=row["month_end"],
            kind="modeled_history",
            provenance="Modeled historical split constrained to official EA national monthly solar totals",
            small_icps=small_icps,
            larger_icps=larger_icps,
            utility_icps=utility_icps,
            small_mw=small_mw,
            larger_mw=larger_mw,
            utility_mw=utility_mw,
            official_total_icps=official_icps,
            official_total_mw=official_mw,
        )
        point["utility_observed_baseline_mw"] = utility_mw
        point["utility_pipeline_provisional_mw"] = 0.0
        point["utility_pipeline_project_count"] = 0
        points.append(point)

    if not points:
        raise RuntimeError("No solar history points in the requested chart window")

    return points, {
        "history_months": HISTORY_MONTHS,
        "chart_history_start": chart_start.isoformat(),
        "explicit_split_start_month": first_observed.isoformat(),
        "latest_observed_split_month": latest_observed.isoformat(),
        "historical_small_count_method": (
            "Before the explicit street-level split, <25 kW ICPs use the existing distributed-solar "
            "history proxy: official EA all-solar ICP totals multiplied by the <25 kW share measured "
            "at the first explicit size-split snapshot."
        ),
        "historical_larger_method": (
            "Before the explicit split, 25 kW-<1 MW ICPs and MW are back-cast from the first observed "
            "snapshot using the existing provisional larger-distributed growth rate; each month is "
            "capped by the official national total."
        ),
        "historical_reconciliation": (
            "Modeled pre-cutoff category estimates are residual-reconciled so their ICP and MW sums "
            "equal the official EA national monthly totals exactly."
        ),
        "larger_distributed_growth_rate_pct_per_year": larger_rate * 100.0,
        "utility_history": utility_notes,
    }


def parse_pipeline_date(row: dict[str, str]) -> date | None:
    for key in ("expected_connection_or_need_date", "raw_estimated_connection_livening_date"):
        value = (row.get(key) or "").strip()
        if not value or value.upper() == "TBC":
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, fmt).date()
                return date(parsed.year, parsed.month, calendar.monthrange(parsed.year, parsed.month)[1])
            except ValueError:
                pass
    return None


def pipeline_solar_projects(start: date, end: date) -> tuple[list[dict[str, object]], dict[str, object]]:
    rows = read_csv(PIPELINE)
    projects: list[dict[str, object]] = []
    seen: set[str] = set()
    untimed_count = 0
    untimed_mw = 0.0

    for row in rows:
        if (row.get("technology") or "").strip().lower() != "solar":
            continue
        try:
            capacity_mw = float((row.get("capacity_mw") or "0").strip())
        except ValueError:
            continue
        if capacity_mw < 1.0:
            continue

        stage = (row.get("stage") or "").strip()
        timing = parse_pipeline_date(row)
        project_id = (row.get("raw_project_id") or row.get("project_name") or "").strip()
        if not project_id or project_id in seen:
            continue
        seen.add(project_id)

        if stage not in PIPELINE_STAGES or timing is None:
            untimed_count += 1
            untimed_mw += capacity_mw
            continue
        if timing <= start or timing > end:
            continue

        projects.append(
            {
                "project_id": project_id,
                "project_name": (row.get("raw_project_name") or row.get("project_name") or project_id).strip(),
                "capacity_mw": capacity_mw,
                "stage": stage,
                "timing_month": timing.isoformat(),
                "region": (row.get("region") or "").strip(),
            }
        )

    projects.sort(key=lambda project: (str(project["timing_month"]), str(project["project_name"])))
    return projects, {
        "source": str(PIPELINE),
        "included_stages": sorted(PIPELINE_STAGES),
        "timing_field_policy": (
            "Use the normalized expected connection/need date where populated, otherwise the raw "
            "Transpower estimated scope-completion/livening date. The plotted month is provisional "
            "timing, not a guaranteed commercial-operation date."
        ),
        "timed_projects_in_horizon": len(projects),
        "timed_capacity_mw_in_horizon": sum(float(project["capacity_mw"]) for project in projects),
        "undated_or_earlier_stage_solar_projects_not_timed": untimed_count,
        "undated_or_earlier_stage_capacity_mw_not_timed": untimed_mw,
    }


def future_points(history: list[dict[str, object]]) -> tuple[list[dict[str, object]], dict[str, object]]:
    meta = json.loads(SCENARIO_JSON.read_text(encoding="utf-8"))
    rows = read_csv(SCENARIO_CSV)

    latest_observed = datetime.strptime(str(history[-1]["month"]), "%Y-%m-%d").date()
    scenario_latest = datetime.strptime(meta["source_latest_month"], "%Y-%m-%d").date()
    if scenario_latest != latest_observed:
        raise RuntimeError(
            "Solar adoption scenario and retained size-bucket history are out of sync: "
            f"scenario={scenario_latest}, size_split={latest_observed}"
        )

    forecast_end = shift_month_end(latest_observed, FORECAST_MONTHS)
    end_index = month_index(forecast_end)
    pipeline_projects, pipeline_notes = pipeline_solar_projects(latest_observed, forecast_end)

    current_share = float(meta["current"]["small_solar_uptake_pct"]) / 100.0
    all_solar_uptake_pct = float(meta["current"]["all_solar_uptake_pct"])
    small_avg_kw = float(meta["current"]["small_fleet_average_kw"])
    larger_avg_kw = float(meta["current"]["larger_distributed_average_kw"])
    mid_rate = fit_mid_rate(meta)

    utility_icps = float(history[-1]["utility_icps"])
    utility_observed_mw = float(history[-1]["utility_mw"])

    future: list[dict[str, object]] = []
    for row in rows:
        d = datetime.strptime(row["month_end"], "%Y-%m-%d").date()
        if d <= latest_observed or month_index(d) > end_index:
            continue

        t = month_delta(d, latest_observed)
        total_grid_icps = float(row["projected_total_icps"])

        low_small_icps = float(row["low_10pct_small_solar_icps"])
        high_small_icps = float(row["high_30pct_small_solar_icps"])
        mid_share = logistic(t, 0.20, current_share, mid_rate)
        mid_small_icps = total_grid_icps * mid_share

        low_small_mw = float(row["low_10pct_small_capacity_mw"])
        high_small_mw = float(row["high_30pct_small_capacity_mw"])
        mid_small_mw = mid_small_icps * small_avg_kw / 1000.0

        larger_mw = float(row["larger_distributed_25kw_to_lt_1mw_capacity_mw"])
        larger_icps = larger_mw * 1000.0 / larger_avg_kw if larger_avg_kw else 0.0

        due_projects = [project for project in pipeline_projects if str(project["timing_month"]) <= d.isoformat()]
        due_this_month = [project for project in pipeline_projects if str(project["timing_month"]) == d.isoformat()]
        pipeline_mw = sum(float(project["capacity_mw"]) for project in due_projects)
        utility_total_mw = utility_observed_mw + pipeline_mw

        point = make_point(
            month=row["month_end"],
            kind="model_future",
            provenance="Modeled future distributed solar plus provisional dated Transpower utility-solar pipeline",
            small_icps=mid_small_icps,
            larger_icps=larger_icps,
            utility_icps=utility_icps,
            small_mw=mid_small_mw,
            larger_mw=larger_mw,
            utility_mw=utility_total_mw,
        )
        point.update(
            {
                "utility_observed_baseline_mw": utility_observed_mw,
                "utility_pipeline_provisional_mw": pipeline_mw,
                "utility_pipeline_project_count": len(due_projects),
                "utility_pipeline_projects_due_this_month": "; ".join(
                    f"{project['project_name']} ({float(project['capacity_mw']):g} MW)"
                    for project in due_this_month
                ),
                "low_10pct_small_icps": low_small_icps,
                "mid_20pct_small_icps": mid_small_icps,
                "high_30pct_small_icps": high_small_icps,
                "low_10pct_small_mw": low_small_mw,
                "mid_20pct_small_mw": mid_small_mw,
                "high_30pct_small_mw": high_small_mw,
                "low_10pct_total_icps": low_small_icps + larger_icps + utility_icps,
                "mid_20pct_total_icps": mid_small_icps + larger_icps + utility_icps,
                "high_30pct_total_icps": high_small_icps + larger_icps + utility_icps,
                "low_10pct_total_mw": low_small_mw + larger_mw + utility_total_mw,
                "mid_20pct_total_mw": mid_small_mw + larger_mw + utility_total_mw,
                "high_30pct_total_mw": high_small_mw + larger_mw + utility_total_mw,
            }
        )
        future.append(point)

    if len(future) < FORECAST_MONTHS:
        raise RuntimeError(f"Expected {FORECAST_MONTHS} future monthly rows, found {len(future)}")

    larger_method = meta.get("method", {}).get("larger_distributed_visual_note") or (
        "Use the existing provisional 25 kW-<1 MW capacity trajectory and its existing guardrail."
    )

    return future, {
        "forecast_months": FORECAST_MONTHS,
        "current_small_solar_uptake_pct": current_share * 100.0,
        "current_all_solar_uptake_pct": all_solar_uptake_pct,
        "mid_20pct_growth_rate_per_year": mid_rate,
        "small_future_policy": (
            "10% and 30% use the existing independently fitted fixed-saturation distributed-solar "
            "scenarios. The 20% midpoint is independently fitted through the latest measured small-"
            "system penetration; it is not the arithmetic average of the 10% and 30% curves."
        ),
        "larger_distributed_future_policy": larger_method,
        "utility_future_policy": (
            "Hold the latest observed >=1 MW capacity as a solid baseline, then add dated Solar projects "
            "in Transpower Delivery/Commissioning as a hatched provisional layer at their published "
            "estimated timing month. Undated/earlier-stage projects are not assigned arbitrary dates. "
            "Future utility ICPs remain flat because the pipeline does not provide a reliable future ICP count."
        ),
        "utility_pipeline": pipeline_notes,
        "utility_pipeline_projects": pipeline_projects,
    }


def save_plot_data(points: list[dict[str, object]]) -> None:
    preferred = [
        "month", "kind", "provenance", "small_icps", "larger_icps", "utility_icps",
        "total_solar_icps", "small_mw", "larger_mw", "utility_observed_baseline_mw",
        "utility_pipeline_provisional_mw", "utility_mw", "total_solar_mw",
        "utility_pipeline_project_count", "utility_pipeline_projects_due_this_month",
        "official_total_icps", "official_total_mw", "reconciliation_error_icps",
        "reconciliation_error_mw", "low_10pct_small_icps", "mid_20pct_small_icps",
        "high_30pct_small_icps", "low_10pct_total_icps", "mid_20pct_total_icps",
        "high_30pct_total_icps", "low_10pct_small_mw", "mid_20pct_small_mw",
        "high_30pct_small_mw", "low_10pct_total_mw", "mid_20pct_total_mw",
        "high_30pct_total_mw",
    ]
    present = {key for point in points for key in point}
    fields = [key for key in preferred if key in present]
    fields.extend(sorted(present - set(fields)))

    OUT_DATA.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DATA.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(points)


def render(
    points: list[dict[str, object]],
    metric: str,
    out_path: Path,
    explicit_start_month: str,
    current_small_uptake_pct: float,
    current_all_solar_uptake_pct: float,
) -> None:
    months = [datetime.strptime(str(point["month"]), "%Y-%m-%d").date() for point in points]
    x = np.arange(len(points))
    small = np.array([float(point[f"small_{metric}"]) for point in points])
    larger = np.array([float(point[f"larger_{metric}"]) for point in points])
    utility = np.array([float(point[f"utility_{metric}"]) for point in points])

    if metric == "mw":
        pipeline = np.array([float(point.get("utility_pipeline_provisional_mw") or 0.0) for point in points])
        utility_solid = utility - pipeline
    else:
        pipeline = np.zeros(len(points), dtype=float)
        utility_solid = utility

    fig, ax = plt.subplots(figsize=(16, 7.5))
    ax.bar(x, small, label=LABELS[GROUPS[0]], width=BAR_WIDTH)
    ax.bar(x, larger, bottom=small, label=LABELS[GROUPS[1]], width=BAR_WIDTH)
    ax.bar(x, utility_solid, bottom=small + larger, label=LABELS[GROUPS[2]], width=BAR_WIDTH)

    if metric == "mw" and np.any(pipeline > 0):
        ax.bar(
            x,
            pipeline,
            bottom=small + larger + utility_solid,
            width=BAR_WIDTH,
            fill=False,
            hatch="////",
            linewidth=0.8,
            label="≥1 MW Transpower pipeline (provisional timing)",
        )

    ax.plot(x, small, linewidth=1.15)
    ax.plot(x, small + larger, linewidth=1.15)
    ax.plot(x, small + larger + utility, linewidth=1.55)

    future_idx = [i for i, point in enumerate(points) if point["kind"] == "model_future"]
    if future_idx:
        low_key = f"low_10pct_small_{metric}"
        mid_key = f"mid_20pct_small_{metric}"
        high_key = f"high_30pct_small_{metric}"
        future_x = np.array(future_idx)
        low = np.array([float(points[i][low_key]) for i in future_idx])
        mid = np.array([float(points[i][mid_key]) for i in future_idx])
        high = np.array([float(points[i][high_key]) for i in future_idx])

        ax.fill_between(future_x, low, high, alpha=0.10, label="<25 kW 10–30% saturation range")
        ax.plot(future_x, low, linestyle="--", linewidth=1.2, label="<25 kW: 10% saturation")
        ax.plot(future_x, mid, linewidth=2.0, label="<25 kW: 20% independently fitted")
        ax.plot(future_x, high, linestyle="--", linewidth=1.2, label="<25 kW: 30% saturation")

        future_boundary = future_idx[0] - 0.5
        ax.axvline(future_boundary, linewidth=1.0, linestyle=":")
        ax.text(future_boundary + 0.25, 0.97, "future model →", transform=ax.get_xaxis_transform(), va="top", fontsize=9)

    observed_idx = [i for i, point in enumerate(points) if point["kind"] == "observed"]
    if observed_idx:
        observed_boundary = observed_idx[0] - 0.5
        ax.axvline(observed_boundary, linewidth=1.0, linestyle=":")
        ax.text(observed_boundary + 0.25, 0.90, "EA street-level split →", transform=ax.get_xaxis_transform(), va="top", fontsize=9)

        current_idx = observed_idx[-1]
        current_month = months[current_idx].strftime("%b %Y")
        ax.scatter([current_idx], [small[current_idx]], s=34, zorder=8)
        ax.annotate(
            f"{current_month}: <25 kW = {current_small_uptake_pct:.3f}% of ICPs\nall solar = {current_all_solar_uptake_pct:.3f}%",
            xy=(current_idx, small[current_idx]),
            xytext=(-12, 20),
            textcoords="offset points",
            ha="right",
            va="bottom",
            fontsize=8.5,
            arrowprops={"arrowstyle": "-", "linewidth": 0.8},
        )

    tick_step = 6
    ticks = list(range(0, len(points), tick_step))
    if len(points) - 1 not in ticks:
        ticks.append(len(points) - 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels([months[i].strftime("%b %Y") for i in ticks], rotation=35, ha="right")
    ax.set_xlim(-0.7, len(points) - 0.3)
    ax.set_ylim(bottom=0)

    if metric == "mw":
        ax.set_ylabel("Installed solar capacity (MW)")
        title_metric = "capacity"
        if np.any(pipeline > 0):
            ax.text(
                0.995, 0.015,
                "Hatched utility MW = dated Transpower Delivery/Commissioning pipeline; timing is provisional",
                transform=ax.transAxes, ha="right", va="bottom", fontsize=8,
            )
    else:
        ax.set_ylabel("Solar installations / ICPs")
        title_metric = "installation count"
        ax.text(
            0.995, 0.015,
            "Future utility ICPs held flat: Transpower pipeline supplies MW/projects, not reliable future ICP counts",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=8,
        )

    explicit_label = datetime.strptime(explicit_start_month, "%Y-%m-%d").strftime("%b %Y")
    fig.suptitle(f"New Zealand solar by installation size: {title_metric}", fontsize=15)
    ax.set_title(
        f"{HISTORY_MONTHS // 12}-year history + {FORECAST_MONTHS // 12}-year outlook; "
        f"pre-{explicit_label} size split modeled, EA Registry size split observed from {explicit_label}",
        fontsize=10,
    )
    ax.legend(ncol=2, fontsize=8)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def archive_outputs(source_month: str) -> Path:
    archive_month = source_month[:7]
    destination = ARCHIVE_ROOT / archive_month
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT_CAPACITY, destination / OUT_CAPACITY.name)
    shutil.copy2(OUT_INSTALLS, destination / OUT_INSTALLS.name)
    shutil.copy2(OUT_DATA, destination / OUT_DATA.name)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Render even if this ISO week is already complete")
    args = parser.parse_args()

    if not weekly_due(args.force):
        print("Solar size-split charts already rendered this ISO week; skipping.")
        return

    history, history_notes = history_points()
    future, future_notes = future_points(history)
    points = history + future

    save_plot_data(points)
    explicit_start = str(history_notes["explicit_split_start_month"])
    current_small_uptake_pct = float(future_notes["current_small_solar_uptake_pct"])
    current_all_solar_uptake_pct = float(future_notes["current_all_solar_uptake_pct"])
    render(points, "mw", OUT_CAPACITY, explicit_start, current_small_uptake_pct, current_all_solar_uptake_pct)
    render(points, "icps", OUT_INSTALLS, explicit_start, current_small_uptake_pct, current_all_solar_uptake_pct)

    source_month = str(history_notes["latest_observed_split_month"])
    archive_dir = archive_outputs(source_month)

    now = date.today().isocalendar()
    state = {
        "last_render_date": date.today().isoformat(),
        "last_render_iso_week": f"{now.year}-W{now.week:02d}",
        "source_month": source_month,
        "archive_month": source_month[:7],
        "history_months": HISTORY_MONTHS,
        "forecast_months": FORECAST_MONTHS,
        "explicit_split_start_month": explicit_start,
        "current_small_solar_uptake_pct": current_small_uptake_pct,
        "current_all_solar_uptake_pct": current_all_solar_uptake_pct,
        "model_notes": {"history": history_notes, "future": future_notes},
        "outputs": [str(OUT_CAPACITY), str(OUT_INSTALLS), str(OUT_DATA)],
        "archive_dir": str(archive_dir),
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_CAPACITY}")
    print(f"Wrote {OUT_INSTALLS}")
    print(f"Wrote {OUT_DATA}")
    print(f"Archived monthly render under {archive_dir}")


if __name__ == "__main__":
    main()
