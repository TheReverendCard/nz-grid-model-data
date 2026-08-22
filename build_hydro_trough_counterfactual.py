from __future__ import annotations

import csv
import json
from calendar import month_abbr, monthrange
from io import StringIO
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests

YEAR = 2024
SCENARIO_YEAR = 2035
PRESERVATION_FRACTION = 0.75
WINTER_MONTHS = (4, 5, 6, 7, 8, 9)

DIST_WINTER = Path("data/model/distributed_solar_sosa_winter_energy.csv")
SEASONALITY = Path("data/model/hydro_solar_seasonality.json")
OUT_CSV = Path("data/model/hydro_trough_counterfactual_2024.csv")
OUT_PNG = Path("data/visuals/hydro_trough_counterfactual_2024.png")
ERC_CSV_URL = "https://www.emi.ea.govt.nz/All/Download/DataReport/CSV/RM3RAS"

SERIES_MAP = {
    "Watch status curve": "watch_gwh",
    "Alert status curve": "alert_gwh",
    "Emergency status curve": "emergency_gwh",
    "Controlled storage": "controlled_storage_gwh",
    "Nominal full": "nominal_full_gwh",
}
SCENARIOS = {
    "low_10pct": "2035 10% distributed-solar case",
    "high_30pct": "2035 30% distributed-solar case",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_erc_csv(content: bytes) -> pd.DataFrame:
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_index = None
    for i, line in enumerate(lines):
        normalized = line.lower().replace('"', "").replace(" ", "")
        if "riskdate" in normalized and "dateofpublication" in normalized and "series" in normalized and "value" in normalized:
            header_index = i
            break
    if header_index is None:
        raise RuntimeError("Could not locate EMI electricity-risk-curve CSV header")
    return pd.read_csv(StringIO("\n".join(lines[header_index:])), low_memory=False)


def fetch_controlled_storage() -> pd.DataFrame:
    params = {
        "DateFrom": f"{YEAR}0101",
        "DateTo": f"{YEAR}1231",
        "RegionCode": "NZ",
        "Show": "RMSB",
        "_si": "v|4",
    }
    response = requests.get(ERC_CSV_URL, params=params, timeout=180)
    response.raise_for_status()
    raw = parse_erc_csv(response.content)

    normalized = {c.strip().lower().replace(" ", "").replace("_", ""): c for c in raw.columns}

    def find_col(*needles: str) -> str:
        for norm, original in normalized.items():
            if all(needle.replace(" ", "").lower() in norm for needle in needles):
                return original
        raise RuntimeError(f"Missing ERC column {needles}; columns={list(raw.columns)}")

    risk_date = find_col("risk", "date")
    publication = find_col("dateofpublication")
    series = find_col("series")
    value = next((c for c in raw.columns if "value" in c.lower() and "gwh" in c.lower()), None) or find_col("value")

    data = raw[[risk_date, publication, series, value]].copy()
    data.columns = ["date", "publication_date", "series", "value_gwh"]
    data["date"] = pd.to_datetime(data["date"], dayfirst=True, errors="coerce")
    data["publication_date"] = pd.to_datetime(data["publication_date"], dayfirst=True, errors="coerce")
    data["value_gwh"] = pd.to_numeric(data["value_gwh"], errors="coerce")
    data = data[data["series"].astype(str).str.strip().isin(SERIES_MAP)].dropna(subset=["date", "value_gwh"])
    data = data[data["date"].dt.year == YEAR]
    eligible = data[data["publication_date"] <= data["date"]].copy()
    if eligible.empty:
        eligible = data.copy()
    eligible = eligible.sort_values(["date", "series", "publication_date"]).groupby(["date", "series"], as_index=False).tail(1)

    daily = eligible.pivot(index="date", columns="series", values="value_gwh").reset_index().rename(columns=SERIES_MAP)
    required = ["controlled_storage_gwh", "nominal_full_gwh", "watch_gwh", "alert_gwh", "emergency_gwh"]
    missing = [c for c in required if c not in daily]
    if missing:
        raise RuntimeError(f"EMI risk-curve report is missing required series: {missing}")

    daily = daily.set_index("date").reindex(pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31", freq="D"))
    daily[required] = daily[required].interpolate(limit_direction="both")
    daily.index.name = "date"
    return daily.reset_index()


def scenario_winter_energy() -> dict[str, float]:
    rows = [r for r in read_csv(DIST_WINTER) if int(r["year"]) == SCENARIO_YEAR]
    found = {r["scenario"]: float(r["difference_gwh"]) for r in rows if r["scenario"] in SCENARIOS}
    missing = [scenario for scenario in SCENARIOS if scenario not in found]
    if missing:
        raise RuntimeError(f"Missing {SCENARIO_YEAR} distributed-solar winter cases: {missing}")
    return found


def winter_daily_weights() -> dict[pd.Timestamp, float]:
    seasonality = json.loads(SEASONALITY.read_text(encoding="utf-8"))
    index = {int(k): float(v) for k, v in seasonality["solar_method"]["monthly_index_vs_annual_mean_daily"].items()}
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
        daily_generation = []
        cumulative_preserved = []
        counterfactual = []
        bank = 0.0
        for row in out.itertuples(index=False):
            generation = total_gwh * weights.get(pd.Timestamp(row.date), 0.0)
            bank += generation * PRESERVATION_FRACTION
            full = float(row.nominal_full_gwh)
            actual = float(row.controlled_storage_gwh)
            adjusted = min(full, actual + bank)
            # Any amount above nominal full cannot remain stored in this simple sensitivity.
            bank = max(0.0, adjusted - actual)
            daily_generation.append(generation)
            cumulative_preserved.append(bank)
            counterfactual.append(adjusted)
        out[f"{scenario}_incremental_solar_gwh_day"] = daily_generation
        out[f"{scenario}_preserved_hydro_gwh"] = cumulative_preserved
        out[f"{scenario}_counterfactual_storage_gwh"] = counterfactual

    return out


def render(df: pd.DataFrame) -> None:
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    view = df[(df["date"] >= pd.Timestamp(f"{YEAR}-03-01")) & (df["date"] <= pd.Timestamp(f"{YEAR}-10-31"))].copy()
    fig, ax = plt.subplots(figsize=(12, 7))

    ax.plot(view["date"], view["nominal_full_gwh"], linewidth=1.1, linestyle=":", label="Nominal full controlled storage")
    ax.plot(view["date"], view["watch_gwh"], linewidth=1.5, linestyle="--", label="Watch curve")
    ax.plot(view["date"], view["alert_gwh"], linewidth=1.5, linestyle="--", label="Alert curve")
    ax.plot(view["date"], view["emergency_gwh"], linewidth=1.2, linestyle="--", label="Emergency curve")
    ax.plot(view["date"], view["controlled_storage_gwh"], linewidth=3.0, label="2024 actual controlled storage")

    for scenario, label in SCENARIOS.items():
        column = f"{scenario}_counterfactual_storage_gwh"
        ax.plot(
            view["date"],
            view[column],
            linewidth=2.4,
            label=f"{label}, {PRESERVATION_FRACTION:.0%} preservation sensitivity",
        )

    # Direct labels at the end of September so the lines remain understandable without the legend.
    label_date = pd.Timestamp(f"{YEAR}-09-30")
    end = df.loc[df["date"] == label_date].iloc[0]
    ax.annotate(
        "2024 actual",
        (label_date, end["controlled_storage_gwh"]),
        xytext=(8, -12), textcoords="offset points", fontsize=9,
    )
    for scenario, short in [("low_10pct", "10% distributed solar"), ("high_30pct", "30% distributed solar")]:
        ax.annotate(
            short,
            (label_date, end[f"{scenario}_counterfactual_storage_gwh"]),
            xytext=(8, 6), textcoords="offset points", fontsize=9,
        )

    ax.set_xlim(view["date"].min(), view["date"].max())
    ax.set_ylim(bottom=0)
    ax.set_ylabel("NZ System Operator controlled storage (GWh)")
    ax.set_xlabel("2024 month")
    ax.set_title("How additional distributed solar could have softened the 2024 hydro drawdown", loc="left", fontsize=15, pad=12)
    ax.text(
        0.01,
        0.965,
        f"Illustrative counterfactual: apply the {SCENARIO_YEAR} solar increment to 2024, with {PRESERVATION_FRACTION:.0%} of each extra winter GWh initially preserving hydro.",
        transform=ax.transAxes,
        va="top",
        fontsize=9,
    )
    ax.grid(axis="y", alpha=0.22)
    month_starts = pd.date_range(f"{YEAR}-03-01", f"{YEAR}-10-01", freq="MS")
    ax.set_xticks(month_starts)
    ax.set_xticklabels([month_abbr[d.month] for d in month_starts])
    ax.legend(frameon=False, loc="upper right", fontsize=8.4)

    fig.text(
        0.01,
        0.01,
        "Source storage/risk curves: Electricity Authority EMI historical Electricity Risk Curves, NZ controlled-storage GWh. "
        "Incremental solar: project distributed-solar scenarios above SOSA's embedded domestic solar+battery winter contribution. "
        "Solar is spread through Apr-Sep using the project's observed monthly solar shape. This is a transparent sensitivity, not a JADE/vSPD dispatch rerun: "
        "extra generation can also displace thermal, alter transfers, or be constrained, so hydro preservation is not assumed one-for-one. Counterfactual storage is capped at nominal full.",
        fontsize=8,
        wrap=True,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(OUT_PNG, dpi=180)
    plt.close(fig)


def main() -> None:
    daily = fetch_controlled_storage()
    result = apply_counterfactual(daily)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_CSV, index=False)
    render(result)

    winter = result[result["date"].dt.month.isin(WINTER_MONTHS)]
    actual_min = winter["controlled_storage_gwh"].min()
    print(f"Wrote {OUT_CSV} and {OUT_PNG}")
    print(f"2024 Apr-Sep minimum actual controlled storage: {actual_min:.1f} GWh")
    for scenario in SCENARIOS:
        minimum = winter[f"{scenario}_counterfactual_storage_gwh"].min()
        print(f"{scenario} counterfactual minimum at {PRESERVATION_FRACTION:.0%} preservation: {minimum:.1f} GWh")


if __name__ == "__main__":
    main()
