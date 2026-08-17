from __future__ import annotations

import io
from calendar import month_abbr
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

STORAGE_SRC = Path("data/hydro/model/storage_daily.csv")
GENERATION_DIR = Path("data/wholesale/raw/generation")
OUT_DIR = Path("data/visuals")
MODEL_DIR = Path("data/model")
EA_PRICE_ROOT = "https://www.emi.ea.govt.nz/Wholesale/Datasets/DispatchAndPricing/FinalEnergyPrices/ByMonth"
REFERENCE_NODES = {
    "BEN2201", "HAY2201", "INV2201", "ISL2201", "KIK2201",
    "OTA2201", "RDF2201", "SFD2201", "WKM2201",
}
THERMAL_FUELS = {"Coal", "Gas", "Diesel", "Oil"}
YEAR = 2024
HISTORY_START = 1980
HISTORY_END = 2023


def read_storage_context() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return 2024 storage, historical seasonal bands, and coverage metadata.

    HMD is reservoir-level and expressed in Mm3.  The chart deliberately keeps
    this as represented active + contingent volume rather than pretending it is
    the System Operator's controlled-storage GWh series.  Historical bands are
    therefore derived from the same HMD series as the 2024 line.
    """
    df = pd.read_csv(STORAGE_SRC, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["total_storage_mm3"] = pd.to_numeric(df["total_storage_mm3"], errors="coerce")
    df = df.dropna(subset=["date", "total_storage_mm3"]).copy()

    current_sites = set(
        df.loc[df["date"].dt.year == YEAR, "site_code"].dropna().astype(str).unique()
    )
    if not current_sites:
        raise RuntimeError("No 2024 HMD storage sites found")

    # Keep one consistent represented reservoir set through history.  A day is
    # only used when every 2024 represented site has a storage observation, so
    # changing reservoir coverage cannot create false historical low-storage bands.
    represented = df[df["site_code"].astype(str).isin(current_sites)].copy()
    daily = (
        represented.groupby("date")
        .agg(
            represented_storage_mm3=("total_storage_mm3", "sum"),
            represented_site_count=("site_code", "nunique"),
        )
        .reset_index()
    )
    daily = daily[daily["represented_site_count"] == len(current_sites)].copy()

    observed = daily[daily["date"].dt.year == YEAR].copy()
    if len(observed) < 350:
        raise RuntimeError(f"Only {len(observed)} complete 2024 storage days")

    hist = daily[
        (daily["date"].dt.year >= HISTORY_START)
        & (daily["date"].dt.year <= HISTORY_END)
    ].copy()
    hist["year"] = hist["date"].dt.year

    # Exclude years without near-complete daily coverage rather than allowing
    # a few surviving dates to distort the seasonal percentiles.
    complete_years = (
        hist.groupby("year")["date"].nunique().loc[lambda s: s >= 350].index.tolist()
    )
    hist = hist[hist["year"].isin(complete_years)].copy()
    if len(complete_years) < 10:
        raise RuntimeError(f"Only {len(complete_years)} complete historical storage years")

    hist["month_day"] = hist["date"].dt.strftime("%m-%d")
    bands = (
        hist.groupby("month_day")["represented_storage_mm3"]
        .agg(
            storage_p05_mm3=lambda x: float(np.quantile(x, 0.05)),
            storage_p25_mm3=lambda x: float(np.quantile(x, 0.25)),
            storage_median_mm3="median",
            storage_p75_mm3=lambda x: float(np.quantile(x, 0.75)),
            storage_p95_mm3=lambda x: float(np.quantile(x, 0.95)),
            historical_years="count",
        )
        .reset_index()
    )
    observed["month_day"] = observed["date"].dt.strftime("%m-%d")
    observed = observed.merge(bands, on="month_day", how="left")

    meta = {
        "represented_sites": len(current_sites),
        "historical_complete_years": len(complete_years),
        "historical_first_year": min(complete_years),
        "historical_last_year": max(complete_years),
    }
    return observed, bands, meta


def detect_columns(df: pd.DataFrame) -> tuple[str, str, str]:
    cols = {c.strip().lower(): c for c in df.columns}

    def pick(*needles: str) -> str:
        for lower, original in cols.items():
            if all(n in lower for n in needles):
                return original
        raise RuntimeError(f"Could not find column containing {needles}; columns={list(df.columns)}")

    date_col = pick("trading", "date")
    poc_col = pick("point", "connection")
    price_col = pick("price") if any("price" in c for c in cols) else pick("dollar")
    return date_col, poc_col, price_col


def load_final_prices() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for month in range(1, 13):
        ym = f"{YEAR}{month:02d}"
        url = f"{EA_PRICE_ROOT}/{ym}_FinalEnergyPrices.csv"
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        frame = pd.read_csv(io.BytesIO(r.content), low_memory=False)
        date_col, poc_col, price_col = detect_columns(frame)
        tp_candidates = [
            c for c in frame.columns
            if "tradingperiod" in c.replace(" ", "").lower() or "trading period" in c.lower()
        ]
        tp_col = tp_candidates[0] if tp_candidates else None
        keep = frame[frame[poc_col].astype(str).str.strip().isin(REFERENCE_NODES)].copy()
        keep["date"] = pd.to_datetime(keep[date_col], errors="coerce")
        keep["price"] = pd.to_numeric(keep[price_col], errors="coerce")
        if tp_col:
            keep["trading_period"] = pd.to_numeric(keep[tp_col], errors="coerce")
        else:
            keep["trading_period"] = keep.groupby("date").cumcount()
        frames.append(keep[["date", "trading_period", "price"]])
        print(f"Loaded {ym} final prices ({len(keep)} reference-node rows)")

    prices = pd.concat(frames, ignore_index=True).dropna(subset=["date", "price"])
    prices = prices[prices["date"].dt.year == YEAR]
    if prices.empty:
        raise RuntimeError("No 2024 final price rows found")
    return prices


def daily_price_summary(prices: pd.DataFrame) -> pd.DataFrame:
    # Average the nine standard EA reference nodes for each trading period first,
    # then summarise the resulting national reference-node proxy by day.
    tp = prices.groupby(["date", "trading_period"], as_index=False)["price"].mean()
    return tp.groupby("date")["price"].agg(
        price_mean_nzd_mwh="mean",
        price_p95_nzd_mwh=lambda x: float(np.quantile(x, 0.95)),
        price_max_nzd_mwh="max",
    ).reset_index()


def daily_thermal_summary() -> pd.DataFrame:
    """Derive observed daily thermal energy and peak thermal MW from Generation_MD.

    Generation_MD trading-period values are kWh.  Daily energy is summed to GWh.
    Peak thermal MW is the maximum national thermal generation in any half-hour:
    kWh / 0.5 h / 1000 = MW.
    """
    daily_energy: list[pd.DataFrame] = []
    daily_peak: list[pd.DataFrame] = []
    tp_cols = [f"TP{i}" for i in range(1, 51)]

    for month in range(1, 13):
        path = GENERATION_DIR / f"{YEAR}{month:02d}_Generation_MD.csv"
        if not path.exists():
            raise RuntimeError(f"Missing observed generation file {path}")
        frame = pd.read_csv(path, low_memory=False)
        thermal = frame[frame["Fuel_Code"].astype(str).str.strip().isin(THERMAL_FUELS)].copy()
        thermal["date"] = pd.to_datetime(thermal["Trading_Date"], errors="coerce")
        for col in tp_cols:
            if col in thermal:
                thermal[col] = pd.to_numeric(thermal[col], errors="coerce").fillna(0.0)
            else:
                thermal[col] = 0.0

        # Sum every generator and trading period for daily thermal energy.
        thermal["thermal_kwh_day"] = thermal[tp_cols].sum(axis=1)
        energy = thermal.groupby("date", as_index=False)["thermal_kwh_day"].sum()
        energy["thermal_generation_gwh"] = energy["thermal_kwh_day"] / 1_000_000.0
        daily_energy.append(energy[["date", "thermal_generation_gwh"]])

        # First sum generators within each half-hour, then take the day's peak.
        by_date_tp = thermal.groupby("date")[tp_cols].sum()
        peak = by_date_tp.max(axis=1).rename("thermal_peak_mw").reset_index()
        peak["thermal_peak_mw"] = peak["thermal_peak_mw"] * 2.0 / 1000.0
        daily_peak.append(peak)

    energy = pd.concat(daily_energy, ignore_index=True)
    peak = pd.concat(daily_peak, ignore_index=True)
    return energy.merge(peak, on="date", how="outer").sort_values("date")


def stripe_intensity(series: pd.Series) -> np.ndarray:
    vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    finite = vals[np.isfinite(vals)]
    if not len(finite):
        return np.zeros(len(vals))
    lo = max(0.0, float(np.quantile(finite, 0.05)))
    hi = float(np.quantile(finite, 0.95))
    if hi <= lo:
        return np.zeros(len(vals))
    return np.clip((vals - lo) / (hi - lo), 0, 1)


def render(
    df: pd.DataFrame,
    metric: str,
    title: str,
    stripe_note: str,
    filename: str,
    history_label: str,
) -> None:
    score = stripe_intensity(df[metric])
    fig, ax = plt.subplots(figsize=(13, 7))

    # Climate-stripe-style system stress layer.
    for d, s in zip(df["date"], score):
        if np.isfinite(s) and s > 0:
            ax.axvspan(
                d,
                d + pd.Timedelta(days=1),
                color="red",
                alpha=float(0.50 * s),
                linewidth=0,
                zorder=0,
            )

    # Historical hydro envelope, derived from the same represented HMD series.
    ax.fill_between(
        df["date"], df["storage_p05_mm3"], df["storage_p95_mm3"],
        alpha=0.16, label=f"Historical P5–P95 ({history_label})", zorder=1,
    )
    ax.fill_between(
        df["date"], df["storage_p25_mm3"], df["storage_p75_mm3"],
        alpha=0.30, label="Historical P25–P75", zorder=2,
    )
    ax.plot(
        df["date"], df["represented_storage_mm3"],
        linewidth=2.4, label="2024 observed hydro storage", zorder=3,
    )

    ax.set_ylim(bottom=0)
    ax.set_xlim(pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31"))
    ax.set_ylabel("Represented active + contingent hydro storage (Mm³)")
    ax.set_xlabel("Month")
    ax.set_title(title, loc="left", fontsize=15, pad=12)
    ax.grid(axis="y", alpha=0.22)
    ax.legend(frameon=False, loc="upper right")

    month_starts = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-01", freq="MS")
    ax.set_xticks(month_starts)
    ax.set_xticklabels([month_abbr[d.month] for d in month_starts])

    ax.text(
        0.01, 0.025,
        stripe_note,
        transform=ax.transAxes, fontsize=9, va="bottom",
    )
    fig.text(
        0.01, 0.004,
        "Hydro: Electricity Authority HMD. Price: EA final wholesale prices. Thermal: EA Generation_MD. "
        "Historical bands use only years with near-complete observations for the same represented reservoir set. "
        "Electricity Risk Curves are not overlaid here because the System Operator publishes them in controlled-storage GWh, not HMD reservoir-volume Mm³.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.055, 1, 1))
    fig.savefig(OUT_DIR / filename, dpi=180)
    plt.close(fig)


def main() -> None:
    storage, _, storage_meta = read_storage_context()
    prices = daily_price_summary(load_final_prices())
    thermal = daily_thermal_summary()
    merged = (
        storage.merge(prices, on="date", how="inner")
        .merge(thermal, on="date", how="inner")
        .sort_values("date")
    )
    if len(merged) < 350:
        raise RuntimeError(f"Only {len(merged)} joined 2024 days; expected roughly a full year")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = MODEL_DIR / "observed_2024_hydro_stress_daily.csv"
    merged.to_csv(out_csv, index=False)

    history_label = (
        f"{storage_meta['historical_first_year']}–{storage_meta['historical_last_year']}, "
        f"n={storage_meta['historical_complete_years']} years"
    )

    render(
        merged,
        "price_p95_nzd_mwh",
        "2024 hydro storage and wholesale price stress",
        "Red stripe intensity = daily P95 wholesale price, robustly scaled P5–P95; darker = more expensive.",
        "observed_2024_hydro_bands_price_p95_stripes.png",
        history_label,
    )
    render(
        merged,
        "thermal_peak_mw",
        "2024 hydro storage and peak thermal generation",
        "Red stripe intensity = highest national thermal output in any half-hour of the day; darker = more thermal capacity in use.",
        "observed_2024_hydro_bands_thermal_peak_stripes.png",
        history_label,
    )

    print(
        f"Wrote {out_csv} and matched 2024 price/thermal stripe charts; "
        f"historical years={storage_meta['historical_complete_years']}"
    )


if __name__ == "__main__":
    main()
