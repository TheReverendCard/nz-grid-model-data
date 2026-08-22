from __future__ import annotations

import csv
import json
from calendar import month_abbr, monthrange
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

YEAR = 2024
SCENARIO_YEAR = 2035
PRESERVATION_FRACTION = 0.75
WINTER_MONTHS = (4, 5, 6, 7, 8, 9)
HISTORY_START = 1980
HISTORY_END = 2023
Y_MAX_GWH = 6000

STORAGE = Path("data/model/hydro_storage_energy_daily.csv")
ERC = Path("data/model/observed_2024_erc_daily.csv")
DIST_WINTER = Path("data/model/distributed_solar_sosa_winter_energy.csv")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
OUT_CSV = Path("data/model/hydro_trough_counterfactual_2024.csv")
OUT_PNG = Path("data/visuals/hydro_trough_counterfactual_2024.png")

SCENARIOS = {
    "low_10pct": "10% distributed-solar case",
    "high_30pct": "30% distributed-solar case",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def storage_context() -> pd.DataFrame:
    df = pd.read_csv(STORAGE, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["national_energy_equivalent_gwh"] = pd.to_numeric(
        df["national_energy_equivalent_gwh"], errors="coerce"
    )
    df = df.dropna(subset=["date", "national_energy_equivalent_gwh"]).copy()

    hist = df[
        (df["date"].dt.year >= HISTORY_START)
        & (df["date"].dt.year <= HISTORY_END)
    ].copy()
    hist["year"] = hist["date"].dt.year
    complete_years = (
        hist.groupby("year")["date"].nunique().loc[lambda s: s >= 350].index
    )
    hist = hist[hist["year"].isin(complete_years)].copy()
    if len(complete_years) < 10:
        raise RuntimeError("Insufficient complete hydro-storage history")

    hist["month_day"] = hist["date"].dt.strftime("%m-%d")
    bands = (
        hist.groupby("month_day")["national_energy_equivalent_gwh"]
        .agg(
            p05_gwh=lambda x: float(np.quantile(x, 0.05)),
            p25_gwh=lambda x: float(np.quantile(x, 0.25)),
            median_gwh="median",
            p75_gwh=lambda x: float(np.quantile(x, 0.75)),
            p95_gwh=lambda x: float(np.quantile(x, 0.95)),
        )
        .reset_index()
    )

    observed = df[df["date"].dt.year == YEAR].copy()
    if len(observed) < 350:
        raise RuntimeError(f"Only {len(observed)} complete {YEAR} storage days")
    observed["month_day"] = observed["date"].dt.strftime("%m-%d")
    observed = observed.merge(bands, on="month_day", how="left")

    if ERC.exists():
        erc = pd.read_csv(ERC, low_memory=False)
        erc["date"] = pd.to_datetime(erc["date"], errors="coerce")
        wanted = ["date", "watch_gwh", "alert_gwh", "emergency_gwh"]
        missing = [column for column in wanted if column not in erc.columns]
        if missing:
            raise RuntimeError(f"Cached 2024 ERC file missing columns: {missing}")
        for column in wanted[1:]:
            erc[column] = pd.to_numeric(erc[column], errors="coerce")
        observed = observed.merge(erc[wanted], on="date", how="left")
        observed[wanted[1:]] = observed[wanted[1:]].interpolate(limit_direction="both")
    else:
        for column in ["watch_gwh", "alert_gwh", "emergency_gwh"]:
            observed[column] = np.nan

    return observed


def scenario_winter_energy() -> dict[str, float]:
    rows = [r for r in read_csv(DIST_WINTER) if int(r["year"]) == SCENARIO_YEAR]
    found = {
        r["scenario"]: float(r["difference_gwh"])
        for r in rows
        if r["scenario"] in SCENARIOS
    }
    missing = [scenario for scenario in SCENARIOS if scenario not in found]
    if missing:
        raise RuntimeError(
            f"Missing {SCENARIO_YEAR} distributed-solar winter cases: {missing}"
        )
    return found


def winter_daily_weights() -> dict[pd.Timestamp, float]:
    seasonality = json.loads(SEASONALITY.read_text(encoding="utf-8"))
    index = {
        int(k): float(v)
        for k, v in seasonality["solar_method"][
            "monthly_index_vs_annual_mean_daily"
        ].items()
    }
    denominator = sum(index[m] * monthrange(YEAR, m)[1] for m in WINTER_MONTHS)
    weights: dict[pd.Timestamp, float] = {}
    for month in WINTER_MONTHS:
        daily_weight = index[month] / denominator
        for day in range(1, monthrange(YEAR, month)[1] + 1):
            weights[pd.Timestamp(YEAR, month, day)] = daily_weight
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise RuntimeError("Winter solar weights do not sum to one")
    return weights


def apply_counterfactual(daily: pd.DataFrame) -> pd.DataFrame:
    winter_gwh = scenario_winter_energy()
    weights = winter_daily_weights()
    out = daily.copy()

    for scenario, total_gwh in winter_gwh.items():
        generation_daily: list[float] = []
        preserved_daily: list[float] = []
        counterfactual_daily: list[float] = []
        bank = 0.0
        for row in out.itertuples(index=False):
            generation = total_gwh * weights.get(pd.Timestamp(row.date), 0.0)
            bank += generation * PRESERVATION_FRACTION
            actual = float(row.national_energy_equivalent_gwh)
            generation_daily.append(generation)
            preserved_daily.append(bank)
            counterfactual_daily.append(actual + bank)

        out[f"{scenario}_incremental_solar_gwh_day"] = generation_daily
        out[f"{scenario}_preserved_hydro_gwh"] = preserved_daily
        out[f"{scenario}_counterfactual_storage_gwh"] = counterfactual_daily

    return out


def render(df: pd.DataFrame) -> None:
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    view = df[
        (df["date"] >= pd.Timestamp(f"{YEAR}-03-01"))
        & (df["date"] <= pd.Timestamp(f"{YEAR}-09-30"))
    ].copy()

    fig, ax = plt.subplots(figsize=(12, 7.4))
    ax.fill_between(
        view["date"], view["p05_gwh"], view["p95_gwh"],
        alpha=0.14, label=f"Historical P5-P95 ({HISTORY_START}-{HISTORY_END})"
    )
    ax.fill_between(
        view["date"], view["p25_gwh"], view["p75_gwh"],
        alpha=0.28, label="Historical P25-P75"
    )

    # Official 2024 System Operator risk thresholds are included only as a visual
    # reference. They use controlled-storage GWh, while the main storage series is
    # HMD-derived energy-equivalent storage, so they must not be read as a precise
    # like-for-like threshold crossing test.
    if view["watch_gwh"].notna().any():
        ax.plot(
            view["date"], view["watch_gwh"], linewidth=1.5, linestyle="--",
            alpha=0.85, label="2024 Watch curve (controlled-storage reference)"
        )
        ax.plot(
            view["date"], view["alert_gwh"], linewidth=1.5, linestyle="--",
            alpha=0.85, label="2024 Alert curve (controlled-storage reference)"
        )
        ax.plot(
            view["date"], view["emergency_gwh"], linewidth=1.3, linestyle=":",
            alpha=0.85, label="2024 Emergency curve (controlled-storage reference)"
        )

    ax.plot(
        view["date"], view["national_energy_equivalent_gwh"],
        linewidth=3.0, label="2024 observed hydro storage, energy-equivalent"
    )

    # Counterfactuals are direct-labelled rather than added to the legend.
    for scenario in SCENARIOS:
        ax.plot(
            view["date"],
            view[f"{scenario}_counterfactual_storage_gwh"],
            linewidth=2.5,
        )

    label_date = pd.Timestamp(f"{YEAR}-09-20")
    label_row = df.loc[df["date"] == label_date].iloc[0]
    ax.annotate(
        "2024 actual",
        (label_date, label_row["national_energy_equivalent_gwh"]),
        xytext=(-82, -14), textcoords="offset points", fontsize=9,
        ha="right",
    )
    ax.annotate(
        "10% distributed solar\n75% preservation sensitivity",
        (label_date, label_row["low_10pct_counterfactual_storage_gwh"]),
        xytext=(-92, -2), textcoords="offset points", fontsize=9,
        ha="right", va="center",
    )
    ax.annotate(
        "30% distributed solar\n75% preservation sensitivity",
        (label_date, label_row["high_30pct_counterfactual_storage_gwh"]),
        xytext=(-92, 12), textcoords="offset points", fontsize=9,
        ha="right", va="center",
    )

    ax.set_xlim(view["date"].min(), view["date"].max())
    ax.set_ylim(0, Y_MAX_GWH)
    ax.set_ylabel("Major-reservoir stored-energy equivalent (GWh)")
    ax.set_xlabel("2024 month")
    ax.set_title(
        "How additional distributed solar could have softened the 2024 hydro drawdown",
        loc="left", fontsize=15, pad=12,
    )
    ax.text(
        0.01,
        0.968,
        f"Illustrative sensitivity: apply the {SCENARIO_YEAR} solar increment to 2024; "
        f"{PRESERVATION_FRACTION:.0%} of each extra Apr-Sep GWh is treated as hydro initially not released.",
        transform=ax.transAxes,
        va="top",
        fontsize=9,
    )
    ax.grid(axis="y", alpha=0.22)
    month_starts = pd.date_range(f"{YEAR}-03-01", f"{YEAR}-09-01", freq="MS")
    ax.set_xticks(month_starts)
    ax.set_xticklabels([month_abbr[d.month] for d in month_starts])
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0.0, 0.91), fontsize=8.1)

    ax.text(
        0.50,
        0.035,
        "2024 Watch / Alert / Emergency curves are official controlled-storage references only; "
        "the main hydro lines use HMD-derived energy-equivalent storage and are not directly comparable threshold-for-threshold.",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=8.1,
    )

    fig.text(
        0.01,
        0.008,
        "Storage source: Electricity Authority HMD major-reservoir volumes converted to an energy-equivalent state using verified downstream cascade MWh/Mm3 route factors. "
        "Incremental solar is the project's 2035 distributed-solar generation above SOSA's embedded domestic solar+battery winter contribution, spread through Apr-Sep using the observed monthly solar shape. "
        "The 75% hydro-preservation factor is an explicit sensitivity, not a JADE/vSPD dispatch result; the remainder can represent thermal displacement, transfers or other system responses. Operational storage limits and spill are not modelled, so interpret the counterfactual primarily as a change in drawdown slope and trough depth.",
        fontsize=7.7,
        wrap=True,
    )
    fig.tight_layout(rect=(0, 0.095, 1, 1))
    fig.savefig(OUT_PNG, dpi=180)
    plt.close(fig)


def main() -> None:
    daily = storage_context()
    result = apply_counterfactual(daily)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_CSV, index=False)
    render(result)

    winter = result[result["date"].dt.month.isin(WINTER_MONTHS)]
    actual_min = winter["national_energy_equivalent_gwh"].min()
    print(f"Wrote {OUT_CSV} and {OUT_PNG}")
    print(f"2024 Apr-Sep minimum observed energy-equivalent storage: {actual_min:.1f} GWh")
    for scenario in SCENARIOS:
        minimum = winter[f"{scenario}_counterfactual_storage_gwh"].min()
        print(
            f"{scenario} counterfactual minimum at "
            f"{PRESERVATION_FRACTION:.0%} preservation: {minimum:.1f} GWh"
        )


if __name__ == "__main__":
    main()
