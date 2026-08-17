from __future__ import annotations

import csv
import io
from calendar import month_abbr
from datetime import date, datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

STORAGE_SRC = Path("data/hydro/model/storage_daily.csv")
OUT_DIR = Path("data/visuals")
MODEL_DIR = Path("data/model")
EA_PRICE_ROOT = "https://www.emi.ea.govt.nz/Wholesale/Datasets/DispatchAndPricing/FinalEnergyPrices/ByMonth"
REFERENCE_NODES = {
    "BEN2201", "HAY2201", "INV2201", "ISL2201", "KIK2201",
    "OTA2201", "RDF2201", "SFD2201", "WKM2201",
}
YEAR = 2024


def read_storage() -> pd.DataFrame:
    df = pd.read_csv(STORAGE_SRC, low_memory=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["total_storage_mm3"] = pd.to_numeric(df["total_storage_mm3"], errors="coerce")
    df = df[(df["date"].dt.year == YEAR) & df["date"].notna()].copy()
    # HMD is reservoir-level. Sum represented active + contingent storage by day.
    daily = (
        df.groupby("date", as_index=False)["total_storage_mm3"]
        .sum(min_count=1)
        .rename(columns={"total_storage_mm3": "represented_storage_mm3"})
    )
    if daily.empty:
        raise RuntimeError("No 2024 HMD storage rows found")
    return daily


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
        # Trading period column is useful if present; otherwise group by every price row date.
        tp_candidates = [c for c in frame.columns if "tradingperiod" in c.replace(" ", "").lower() or "trading period" in c.lower()]
        tp_col = tp_candidates[0] if tp_candidates else None
        keep = frame[frame[poc_col].astype(str).str.strip().isin(REFERENCE_NODES)].copy()
        # EA FinalEnergyPrices trading dates are ISO year-month-day. Do not use
        # dayfirst=True here: that swaps the month/day for ambiguous dates and
        # leaves only the 12x12 set of coincidental date matches against HMD.
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
    daily = tp.groupby("date")["price"].agg(
        price_mean_nzd_mwh="mean",
        price_p95_nzd_mwh=lambda x: float(np.quantile(x, 0.95)),
        price_max_nzd_mwh="max",
    ).reset_index()
    return daily


def stripe_intensity(series: pd.Series) -> np.ndarray:
    vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    finite = vals[np.isfinite(vals)]
    if not len(finite):
        return np.zeros(len(vals))
    # Robust clipping: a single scarcity spike should not make every other day invisible.
    lo = max(0.0, float(np.quantile(finite, 0.05)))
    hi = float(np.quantile(finite, 0.95))
    if hi <= lo:
        return np.zeros(len(vals))
    return np.clip((vals - lo) / (hi - lo), 0, 1)


def render(df: pd.DataFrame, metric: str, label: str, filename: str) -> None:
    score = stripe_intensity(df[metric])
    fig, ax = plt.subplots(figsize=(13, 6.5))

    for d, s in zip(df["date"], score):
        if np.isfinite(s) and s > 0:
            ax.axvspan(d, d + pd.Timedelta(days=1), color="red", alpha=float(0.50 * s), linewidth=0)

    ax.plot(df["date"], df["represented_storage_mm3"], linewidth=2.2, label="Observed represented hydro storage")
    ax.set_ylim(bottom=0)
    ax.set_xlim(pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31"))
    ax.set_ylabel("Represented active hydro storage (Mm³)")
    ax.set_xlabel("Month")
    ax.set_title(f"2024 observed hydro storage with {label} price-stress stripes", loc="left", fontsize=15, pad=12)
    ax.grid(axis="y", alpha=0.22)
    ax.legend(frameon=False, loc="upper right")

    month_starts = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-01", freq="MS")
    ax.set_xticks(month_starts)
    ax.set_xticklabels([month_abbr[d.month] for d in month_starts])

    ax.text(
        0.01, 0.025,
        "Red intensity is scaled between the 5th and 95th percentile of the daily price metric; darker = more expensive.",
        transform=ax.transAxes, fontsize=9, va="bottom",
    )
    fig.text(
        0.01, 0.005,
        "Sources: NZ Electricity Authority HMD observed reservoir storage and final wholesale energy prices. "
        "Price metric is derived from the nine standard EA reference nodes, averaged within each trading period before daily summarisation.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    fig.savefig(OUT_DIR / filename, dpi=180)
    plt.close(fig)


def main() -> None:
    storage = read_storage()
    prices = load_final_prices()
    daily_prices = daily_price_summary(prices)
    merged = storage.merge(daily_prices, on="date", how="inner").sort_values("date")
    if len(merged) < 350:
        raise RuntimeError(f"Only {len(merged)} joined 2024 days; expected roughly a full year")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = MODEL_DIR / "observed_2024_hydro_price_daily.csv"
    merged.to_csv(out_csv, index=False)

    render(merged, "price_mean_nzd_mwh", "daily mean", "observed_2024_hydro_price_mean_stripes.png")
    render(merged, "price_p95_nzd_mwh", "daily P95", "observed_2024_hydro_price_p95_stripes.png")
    render(merged, "price_max_nzd_mwh", "daily maximum", "observed_2024_hydro_price_max_stripes.png")

    print(f"Wrote {out_csv} and 3 observed 2024 stripe charts")


if __name__ == "__main__":
    main()
