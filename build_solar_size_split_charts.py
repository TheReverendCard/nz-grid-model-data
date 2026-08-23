from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from datetime import date, datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import minimize_scalar

HISTORY = Path("data/distributed_generation/model/solar_size_bucket_history.csv")
MONTHLY = Path("data/distributed_generation/model/national_solar_all_monthly.csv")
SCENARIO_CSV = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.csv")
SCENARIO_JSON = Path("data/distributed_generation/model/distributed_solar_adoption_scenarios.json")
OUT_DIR = Path("data/visuals")
ARCHIVE_ROOT = OUT_DIR / "archive" / "distributed_solar_size_split"
STATE = Path("data/metadata/solar_size_split_chart_state.json")
OUT_CAPACITY = OUT_DIR / "distributed_solar_size_split_capacity.png"
OUT_INSTALLS = OUT_DIR / "distributed_solar_size_split_installs.png"
OUT_DATA = OUT_DIR / "distributed_solar_size_split_plot_data.csv"

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


def history_points() -> list[dict[str, object]]:
    grouped: dict[str, dict[str, dict[str, float]]] = {}
    for row in read_csv(HISTORY):
        group = row["group"]
        if group not in GROUPS:
            continue
        grouped.setdefault(row["source_month"], {})[group] = {
            "icps": float(row["estimated_icps"]),
            "mw": float(row["capacity_mw"]),
        }

    points: list[dict[str, object]] = []
    for month in sorted(grouped):
        if not all(group in grouped[month] for group in GROUPS):
            continue
        values = grouped[month]
        points.append(
            {
                "month": month,
                "kind": "observed",
                "small_icps": values[GROUPS[0]]["icps"],
                "larger_icps": values[GROUPS[1]]["icps"],
                "utility_icps": values[GROUPS[2]]["icps"],
                "small_mw": values[GROUPS[0]]["mw"],
                "larger_mw": values[GROUPS[1]]["mw"],
                "utility_mw": values[GROUPS[2]]["mw"],
            }
        )
    if not points:
        raise RuntimeError("No complete three-group snapshots in solar_size_bucket_history.csv")
    return points


def future_points(observed: list[dict[str, object]]) -> tuple[list[dict[str, object]], dict[str, object]]:
    meta = json.loads(SCENARIO_JSON.read_text(encoding="utf-8"))
    rows = read_csv(SCENARIO_CSV)
    latest = datetime.strptime(meta["source_latest_month"], "%Y-%m-%d").date()
    end_index = latest.year * 12 + latest.month + 12
    current_share = float(meta["current"]["small_solar_uptake_pct"]) / 100.0
    small_avg_kw = float(meta["current"]["small_fleet_average_kw"])
    larger_avg_kw = float(meta["current"]["larger_distributed_average_kw"])
    mid_rate = fit_mid_rate(meta)

    utility_icps = float(observed[-1]["utility_icps"])
    utility_mw = float(observed[-1]["utility_mw"])

    future: list[dict[str, object]] = []
    for row in rows:
        d = datetime.strptime(row["month_end"], "%Y-%m-%d").date()
        index = d.year * 12 + d.month
        if d <= latest or index > end_index:
            continue

        t = month_delta(d, latest)
        total_icps = float(row["projected_total_icps"])
        mid_share = logistic(t, 0.20, current_share, mid_rate)
        mid_small_icps = total_icps * mid_share
        mid_small_mw = mid_small_icps * small_avg_kw / 1000.0
        larger_mw = float(row["larger_distributed_25kw_to_lt_1mw_capacity_mw"])
        larger_icps = larger_mw * 1000.0 / larger_avg_kw if larger_avg_kw else 0.0

        future.append(
            {
                "month": row["month_end"],
                "kind": "model",
                "small_icps": mid_small_icps,
                "larger_icps": larger_icps,
                "utility_icps": utility_icps,
                "small_mw": mid_small_mw,
                "larger_mw": larger_mw,
                "utility_mw": utility_mw,
                "low_total_icps": float(row["low_10pct_small_solar_icps"]) + larger_icps + utility_icps,
                "mid_total_icps": mid_small_icps + larger_icps + utility_icps,
                "high_total_icps": float(row["high_30pct_small_solar_icps"]) + larger_icps + utility_icps,
                "low_total_mw": float(row["low_10pct_small_capacity_mw"]) + larger_mw + utility_mw,
                "mid_total_mw": mid_small_mw + larger_mw + utility_mw,
                "high_total_mw": float(row["high_30pct_small_capacity_mw"]) + larger_mw + utility_mw,
            }
        )

    return future, {
        "mid_20pct_growth_rate_per_year": mid_rate,
        "utility_future_policy": "Hold the latest observed >=1 MW bucket flat until project-timed utility additions are explicitly modelled.",
    }


def save_plot_data(points: list[dict[str, object]]) -> None:
    fields = sorted({key for point in points for key in point})
    OUT_DATA.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DATA.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(points)


def render(points: list[dict[str, object]], metric: str, out_path: Path) -> None:
    months = [datetime.strptime(str(point["month"]), "%Y-%m-%d").date() for point in points]
    x = np.arange(len(points))
    small = np.array([float(point[f"small_{metric}"]) for point in points])
    larger = np.array([float(point[f"larger_{metric}"]) for point in points])
    utility = np.array([float(point[f"utility_{metric}"]) for point in points])

    fig, ax = plt.subplots(figsize=(14, 7.5))
    ax.bar(x, small, label=LABELS[GROUPS[0]], width=0.82)
    ax.bar(x, larger, bottom=small, label=LABELS[GROUPS[1]], width=0.82)
    ax.bar(x, utility, bottom=small + larger, label=LABELS[GROUPS[2]], width=0.82)

    ax.plot(x, small, linewidth=1.4, marker=".")
    ax.plot(x, small + larger, linewidth=1.4, marker=".")
    ax.plot(x, small + larger + utility, linewidth=1.8, marker=".")

    model_idx = [i for i, point in enumerate(points) if point["kind"] == "model"]
    if model_idx:
        low_key = f"low_total_{metric}"
        mid_key = f"mid_total_{metric}"
        high_key = f"high_total_{metric}"
        model_x = np.array(model_idx)
        low = np.array([float(points[i][low_key]) for i in model_idx])
        mid = np.array([float(points[i][mid_key]) for i in model_idx])
        high = np.array([float(points[i][high_key]) for i in model_idx])
        ax.fill_between(model_x, low, high, alpha=0.12, label="10–30% small-system saturation range")
        ax.plot(model_x, low, linestyle="--", linewidth=1.2, label="10% saturation")
        ax.plot(model_x, mid, linewidth=2.2, label="20% saturation")
        ax.plot(model_x, high, linestyle="--", linewidth=1.2, label="30% saturation")
        boundary = model_idx[0] - 0.5
        ax.axvline(boundary, linewidth=1.0, linestyle=":")
        ax.text(boundary + 0.2, ax.get_ylim()[1] * 0.96, "model →", va="top")

    tick_step = max(1, len(points) // 12)
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
    else:
        ax.set_ylabel("Solar installations (estimated ICPs)")
        title_metric = "installation count"

    ax.set_title(f"New Zealand solar by installation size: observed + next 12 months ({title_metric})")
    ax.legend(ncol=2, fontsize=9)
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

    observed = history_points()
    future, model_notes = future_points(observed)
    points = observed + future
    save_plot_data(points)
    render(points, "mw", OUT_CAPACITY)
    render(points, "icps", OUT_INSTALLS)
    source_month = str(observed[-1]["month"])
    archive_dir = archive_outputs(source_month)

    now = date.today().isocalendar()
    state = {
        "last_render_date": date.today().isoformat(),
        "last_render_iso_week": f"{now.year}-W{now.week:02d}",
        "source_month": source_month,
        "archive_month": source_month[:7],
        "forecast_months": 12,
        "model_notes": model_notes,
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
