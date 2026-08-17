from __future__ import annotations

from calendar import month_abbr
from datetime import timedelta
from io import StringIO
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

YEAR = 2024
INPUT = Path("data/model/observed_2024_hydro_stress_daily.csv")
ERC_CACHE = Path("data/model/observed_2024_erc_daily.csv")
OUTPUT = Path("data/visuals/observed_2024_hydro_bands_price_p95_erc_zones.png")
ERC_REPORT_URL = "https://www.emi.ea.govt.nz/All/Reports/RM3RAS"

TARGET_SERIES = {
    "Watch status curve": "watch_gwh",
    "Alert status curve": "alert_gwh",
    "Emergency status curve": "emergency_gwh",
    "Controlled storage": "controlled_storage_gwh",
    "Nominal full": "nominal_full_gwh",
}


def parse_report_page(html: str) -> list[dict[str, object]]:
    """Extract visible EMI risk-curve table rows from one report response."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, object]] = []

    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 5:
            continue
        risk_date, publication, _curve_group, series, value = cells[:5]
        if series not in TARGET_SERIES:
            continue
        try:
            rows.append(
                {
                    "date": pd.to_datetime(risk_date, dayfirst=True),
                    "publication_date": pd.to_datetime(publication, dayfirst=True),
                    "series": series,
                    "value_gwh": float(str(value).replace(",", "")),
                }
            )
        except (TypeError, ValueError):
            continue

    return rows


def fetch_erc_history() -> pd.DataFrame:
    """Fetch 2024 NZ ERC history in short windows so EMI's table row cap is harmless.

    The report can expose several series per day and displays only a limited number
    of rows in a single response. Six-day windows keep the relevant rows below
    that cap. For each risk date/series we retain the latest publication that was
    already available on that date.
    """
    if ERC_CACHE.exists():
        cached = pd.read_csv(ERC_CACHE, parse_dates=["date"])
        required = {"date", "watch_gwh", "alert_gwh", "emergency_gwh"}
        if required.issubset(cached.columns) and len(cached) >= 300:
            print(f"Using cached ERC history from {ERC_CACHE} ({len(cached)} days)")
            return cached

    frames: list[dict[str, object]] = []
    start = pd.Timestamp(f"{YEAR}-01-01")
    stop = pd.Timestamp(f"{YEAR}-12-31")
    cursor = start

    while cursor <= stop:
        window_end = min(cursor + pd.Timedelta(days=5), stop)
        params = {
            "DateFrom": cursor.strftime("%Y%m%d"),
            "DateTo": window_end.strftime("%Y%m%d"),
            "RegionCode": "NZ",
            "Show": "RMSB",
            "_si": "v|3",
        }
        response = requests.get(ERC_REPORT_URL, params=params, timeout=120)
        response.raise_for_status()
        parsed = parse_report_page(response.text)
        frames.extend(parsed)
        print(
            f"ERC {cursor.date()} to {window_end.date()}: "
            f"{len(parsed)} relevant rows"
        )
        cursor = window_end + pd.Timedelta(days=1)

    if not frames:
        raise RuntimeError("EMI ERC report returned no parsable risk-curve rows")

    raw = pd.DataFrame(frames)
    raw = raw[(raw["date"].dt.year == YEAR)].copy()
    # If a response contains overlapping publication vintages, use the newest
    # one that was known by the risk date. This follows the information set the
    # System Operator had available at the time rather than hindsight.
    eligible = raw[raw["publication_date"] <= raw["date"]].copy()
    if eligible.empty:
        eligible = raw.copy()
    eligible = eligible.sort_values(["date", "series", "publication_date"])
    eligible = eligible.groupby(["date", "series"], as_index=False).tail(1)

    daily = eligible.pivot(index="date", columns="series", values="value_gwh").reset_index()
    daily = daily.rename(columns=TARGET_SERIES).sort_values("date")

    required = ["watch_gwh", "alert_gwh", "emergency_gwh"]
    for col in required:
        if col not in daily:
            raise RuntimeError(f"ERC scrape missing required series {col}")

    # Curves are smooth daily series; interpolate occasional report-table gaps,
    # but do not silently manufacture a mostly absent year.
    daily = daily.set_index("date").reindex(pd.date_range(start, stop, freq="D"))
    observed_days = daily[required].notna().all(axis=1).sum()
    if observed_days < 250:
        raise RuntimeError(
            f"Only {observed_days} 2024 ERC dates parsed before interpolation; "
            "report layout/query likely changed"
        )
    daily[required] = daily[required].interpolate(limit_direction="both")
    for optional in ["controlled_storage_gwh", "nominal_full_gwh"]:
        if optional in daily:
            daily[optional] = daily[optional].interpolate(limit_direction="both")
    daily.index.name = "date"
    daily = daily.reset_index()

    ERC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(ERC_CACHE, index=False)
    print(f"Wrote {ERC_CACHE} ({len(daily)} days)")
    return daily


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


def render(base: pd.DataFrame, erc: pd.DataFrame) -> None:
    df = base.merge(erc, on="date", how="left").sort_values("date")
    if len(df) < 350:
        raise RuntimeError(f"Only {len(df)} joined chart days")

    score = stripe_intensity(df["price_p95_nzd_mwh"])
    fig, ax = plt.subplots(figsize=(13.5, 7.4))
    risk_ax = ax.twinx()

    # System Operator risk-status zones, in their native controlled-storage GWh
    # scale on the right axis. These are deliberately not converted to HMD Mm3.
    x = df["date"]
    emergency = df["emergency_gwh"].to_numpy(dtype=float)
    alert = df["alert_gwh"].to_numpy(dtype=float)
    watch = df["watch_gwh"].to_numpy(dtype=float)

    risk_ax.fill_between(x, 0, emergency, color="#d73027", alpha=0.12, linewidth=0, zorder=0)
    risk_ax.fill_between(x, emergency, alert, color="#f46d43", alpha=0.11, linewidth=0, zorder=0)
    risk_ax.fill_between(x, alert, watch, color="#fdae61", alpha=0.12, linewidth=0, zorder=0)
    risk_ax.plot(x, emergency, color="#b2182b", linewidth=0.8, alpha=0.75, zorder=1)
    risk_ax.plot(x, alert, color="#ef8a62", linewidth=0.8, alpha=0.75, zorder=1)
    risk_ax.plot(x, watch, color="#f6b44b", linewidth=0.8, alpha=0.8, zorder=1)

    # Set the ERC axis from native report context where available. Nominal-full
    # or controlled-storage values prevent an arbitrary vertical scaling of the
    # risk zones; otherwise use a conservative multiple of the Watch maximum.
    scale_candidates = [float(np.nanmax(watch))]
    for col in ["nominal_full_gwh", "controlled_storage_gwh"]:
        if col in df and df[col].notna().any():
            scale_candidates.append(float(df[col].max()))
    risk_top = max(scale_candidates) * 1.05
    risk_ax.set_ylim(0, risk_top)
    risk_ax.set_ylabel("System Operator controlled-storage risk scale (GWh)")
    risk_ax.grid(False)

    # Climate-stripe-style daily wholesale price stress.
    for d, s in zip(df["date"], score):
        if np.isfinite(s) and s > 0:
            ax.axvspan(
                d,
                d + pd.Timedelta(days=1),
                color="red",
                alpha=float(0.48 * s),
                linewidth=0,
                zorder=0,
            )

    # Historical HMD hydro envelope and 2024 actual storage, on the left axis.
    ax.fill_between(
        x, df["storage_p05_mm3"], df["storage_p95_mm3"],
        alpha=0.14, label="Historical P5–P95", zorder=2,
    )
    ax.fill_between(
        x, df["storage_p25_mm3"], df["storage_p75_mm3"],
        alpha=0.28, label="Historical P25–P75", zorder=3,
    )
    ax.plot(
        x, df["represented_storage_mm3"], linewidth=2.5,
        label="2024 observed HMD storage", zorder=4,
    )

    ax.set_xlim(pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31"))
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Month")
    ax.set_ylabel("Represented active + contingent hydro storage (Mm³)")
    ax.set_title(
        "2024 hydro storage, wholesale price stress and WAEERC risk zones",
        loc="left", fontsize=15, pad=12,
    )
    ax.grid(axis="y", alpha=0.20)

    month_starts = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-01", freq="MS")
    ax.set_xticks(month_starts)
    ax.set_xticklabels([month_abbr[d.month] for d in month_starts])

    handles, labels = ax.get_legend_handles_labels()
    handles.extend(
        [
            Patch(facecolor="#fdae61", alpha=0.24, label="Watch zone (right axis)"),
            Patch(facecolor="#f46d43", alpha=0.22, label="Alert zone (right axis)"),
            Patch(facecolor="#d73027", alpha=0.24, label="Emergency zone (right axis)"),
            Patch(facecolor="red", alpha=0.35, label="Daily P95 wholesale price stress"),
        ]
    )
    labels.extend([
        "Watch zone (right axis)",
        "Alert zone (right axis)",
        "Emergency zone (right axis)",
        "Daily P95 wholesale price stress",
    ])
    ax.legend(handles, labels, frameon=False, loc="upper right", fontsize=8.5)

    ax.text(
        0.01,
        0.022,
        "Darker vertical red stripes = higher daily P95 wholesale price. WAEERC zones use the System Operator's native controlled-storage GWh scale on the right axis.",
        transform=ax.transAxes,
        fontsize=8.8,
        va="bottom",
    )
    fig.text(
        0.01,
        0.004,
        "Sources: Electricity Authority HMD, final wholesale prices, and EMI historical electricity risk curves (WAEERC). "
        "The HMD storage line/bands and WAEERC zones use different storage definitions and axes; the overlay is contextual, not a one-to-one threshold comparison.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT, dpi=180)
    plt.close(fig)
    print(f"Wrote {OUTPUT}")


def main() -> None:
    if not INPUT.exists():
        raise RuntimeError(f"Missing prerequisite {INPUT}")
    base = pd.read_csv(INPUT, parse_dates=["date"])
    erc = fetch_erc_history()
    render(base, erc)


if __name__ == "__main__":
    main()
