from __future__ import annotations

from calendar import month_abbr
from io import StringIO
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import requests

YEAR = 2024
INPUT = Path("data/model/observed_2024_hydro_stress_daily.csv")
ERC_CACHE = Path("data/model/observed_2024_erc_daily.csv")
OUTPUT_TOP = Path("data/visuals/observed_2024_hydro_bands_price_p95_erc_zones_top_ribbon.png")
OUTPUT_LINE = Path("data/visuals/observed_2024_hydro_bands_price_p95_erc_zones_line_overlay.png")
ERC_CSV_URL = "https://www.emi.ea.govt.nz/All/Download/DataReport/CSV/RM3RAS"

TARGET_SERIES = {
    "Watch status curve": "watch_gwh",
    "Alert status curve": "alert_gwh",
    "Emergency status curve": "emergency_gwh",
    "Controlled storage": "controlled_storage_gwh",
    "Nominal full": "nominal_full_gwh",
}


def parse_emi_csv_download(content: bytes) -> pd.DataFrame:
    """Parse an EMI DataReport CSV export, allowing report metadata before the table."""
    text = content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_index: int | None = None

    for i, line in enumerate(lines):
        normalized = line.lower().replace('"', "").replace(" ", "")
        if (
            "riskdate" in normalized
            and "dateofpublication" in normalized
            and "series" in normalized
            and "value" in normalized
        ):
            header_index = i
            break

    if header_index is None:
        preview = " | ".join(lines[:12])[:1200]
        raise RuntimeError(
            "Could not locate the ERC table header in EMI CSV download. "
            f"First lines: {preview}"
        )

    raw = pd.read_csv(StringIO("\n".join(lines[header_index:])), low_memory=False)
    if raw.empty:
        raise RuntimeError("EMI ERC CSV table was found but contained no rows")
    print(
        f"Parsed EMI ERC CSV after {header_index} metadata lines: "
        f"{len(raw)} rows; columns={list(raw.columns)}"
    )
    return raw


def fetch_erc_history() -> pd.DataFrame:
    """Fetch the full 2024 NZ historical ERC report from EMI's CSV endpoint."""
    if ERC_CACHE.exists():
        cached = pd.read_csv(ERC_CACHE, parse_dates=["date"])
        required = {"date", "watch_gwh", "alert_gwh", "emergency_gwh"}
        if required.issubset(cached.columns) and len(cached) >= 300:
            print(f"Using cached ERC history from {ERC_CACHE} ({len(cached)} days)")
            return cached

    params = {
        "DateFrom": f"{YEAR}0101",
        "DateTo": f"{YEAR}1231",
        "RegionCode": "NZ",
        "Show": "RMSB",
        "_si": "v|4",
    }
    response = requests.get(ERC_CSV_URL, params=params, timeout=180)
    response.raise_for_status()
    raw = parse_emi_csv_download(response.content)

    normalized = {
        c.strip().lower().replace(" ", "").replace("_", ""): c
        for c in raw.columns
    }

    def find_col(*needles: str) -> str:
        for norm, original in normalized.items():
            if all(n.replace(" ", "").lower() in norm for n in needles):
                return original
        raise RuntimeError(
            f"Could not find ERC column {needles}; columns={list(raw.columns)}"
        )

    risk_date_col = find_col("risk", "date")
    publication_col = find_col("dateofpublication")
    series_col = find_col("series")
    value_col = next(
        (c for c in raw.columns if "value" in c.lower() and "gwh" in c.lower()),
        None,
    )
    if value_col is None:
        value_col = find_col("value")

    data = raw[[risk_date_col, publication_col, series_col, value_col]].copy()
    data.columns = ["date", "publication_date", "series", "value_gwh"]
    data["date"] = pd.to_datetime(data["date"], dayfirst=True, errors="coerce")
    data["publication_date"] = pd.to_datetime(
        data["publication_date"], dayfirst=True, errors="coerce"
    )
    data["value_gwh"] = pd.to_numeric(data["value_gwh"], errors="coerce")
    data = data[
        data["series"].astype(str).str.strip().isin(TARGET_SERIES)
    ].dropna(subset=["date", "value_gwh"])
    data = data[data["date"].dt.year == YEAR].copy()

    if data.empty:
        raise RuntimeError("EMI ERC CSV contained no 2024 target-series rows")

    eligible = data[data["publication_date"] <= data["date"]].copy()
    if eligible.empty:
        eligible = data.copy()
    eligible = eligible.sort_values(["date", "series", "publication_date"])
    eligible = eligible.groupby(["date", "series"], as_index=False).tail(1)

    daily = eligible.pivot(
        index="date", columns="series", values="value_gwh"
    ).reset_index()
    daily = daily.rename(columns=TARGET_SERIES).sort_values("date")

    required = ["watch_gwh", "alert_gwh", "emergency_gwh"]
    missing = [c for c in required if c not in daily]
    if missing:
        raise RuntimeError(f"ERC CSV missing required series: {missing}")

    start = pd.Timestamp(f"{YEAR}-01-01")
    stop = pd.Timestamp(f"{YEAR}-12-31")
    daily = daily.set_index("date").reindex(pd.date_range(start, stop, freq="D"))
    observed_days = int(daily[required].notna().all(axis=1).sum())
    print(f"ERC complete Watch/Alert/Emergency dates before interpolation: {observed_days}")
    if observed_days < 250:
        raise RuntimeError(
            f"Only {observed_days} 2024 ERC dates found in the full CSV; "
            "report query/series selection likely changed"
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


def price_norm(series: pd.Series) -> tuple[np.ndarray, Normalize]:
    vals = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
    finite = vals[np.isfinite(vals)]
    if not len(finite):
        return np.zeros(len(vals)), Normalize(0, 1)
    lo = max(0.0, float(np.quantile(finite, 0.05)))
    hi = float(np.quantile(finite, 0.95))
    if hi <= lo:
        hi = lo + 1.0
    score = np.clip((vals - lo) / (hi - lo), 0, 1)
    return score, Normalize(vmin=lo, vmax=hi, clip=True)


def draw_base(ax, risk_ax, df: pd.DataFrame) -> None:
    x = df["date"]
    emergency = df["emergency_gwh"].to_numpy(dtype=float)
    alert = df["alert_gwh"].to_numpy(dtype=float)
    watch = df["watch_gwh"].to_numpy(dtype=float)

    risk_ax.fill_between(x, 0, emergency, color="#d73027", alpha=0.10, linewidth=0, zorder=0)
    risk_ax.fill_between(x, emergency, alert, color="#f46d43", alpha=0.10, linewidth=0, zorder=0)
    risk_ax.fill_between(x, alert, watch, color="#fdae61", alpha=0.11, linewidth=0, zorder=0)
    risk_ax.plot(x, emergency, color="#b2182b", linewidth=0.8, alpha=0.72, zorder=1)
    risk_ax.plot(x, alert, color="#ef8a62", linewidth=0.8, alpha=0.72, zorder=1)
    risk_ax.plot(x, watch, color="#f6b44b", linewidth=0.8, alpha=0.78, zorder=1)

    scale_candidates = [float(np.nanmax(watch))]
    for col in ["nominal_full_gwh", "controlled_storage_gwh"]:
        if col in df and df[col].notna().any():
            scale_candidates.append(float(df[col].max()))
    risk_ax.set_ylim(0, max(scale_candidates) * 1.05)
    risk_ax.set_ylabel("System Operator controlled-storage risk scale (GWh)")
    risk_ax.grid(False)

    ax.fill_between(
        x, df["storage_p05_mm3"], df["storage_p95_mm3"],
        alpha=0.14, label="Historical P5–P95", zorder=2,
    )
    ax.fill_between(
        x, df["storage_p25_mm3"], df["storage_p75_mm3"],
        alpha=0.28, label="Historical P25–P75", zorder=3,
    )
    ax.plot(
        x, df["represented_storage_mm3"], linewidth=2.3,
        label="2024 observed HMD storage", zorder=5,
    )

    ax.set_xlim(pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31"))
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Month")
    ax.set_ylabel("Represented active + contingent hydro storage (Mm³)")
    ax.grid(axis="y", alpha=0.20)

    month_starts = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-01", freq="MS")
    ax.set_xticks(month_starts)
    ax.set_xticklabels([month_abbr[d.month] for d in month_starts])


def add_top_ribbon(ax, df: pd.DataFrame, score: np.ndarray) -> None:
    """Draw price stress as a narrow ribbon inside the top of the plotting area."""
    y0, y1 = ax.get_ylim()
    ribbon_bottom = y1 - 0.055 * (y1 - y0)
    for d, s in zip(df["date"], score):
        if np.isfinite(s):
            ax.fill_between(
                [d, d + pd.Timedelta(days=1)],
                [ribbon_bottom, ribbon_bottom],
                [y1, y1],
                color="#8b0000",
                alpha=float(0.08 + 0.78 * s),
                linewidth=0,
                zorder=8,
            )
    ax.axhline(ribbon_bottom, linewidth=0.5, alpha=0.35, zorder=9)
    ax.text(
        0.006, 0.976, "Daily P95 wholesale price stress",
        transform=ax.transAxes, fontsize=8.2, va="top", ha="left", zorder=10,
    )


def add_line_overlay(ax, df: pd.DataFrame, score: np.ndarray) -> None:
    """Colour a thin halo directly along the observed storage line by price stress."""
    dates = df["date"].map(pd.Timestamp.toordinal).to_numpy(dtype=float)
    storage = df["represented_storage_mm3"].to_numpy(dtype=float)
    points = np.column_stack([dates, storage])
    segments = np.stack([points[:-1], points[1:]], axis=1)
    seg_score = (score[:-1] + score[1:]) / 2

    rgba = plt.cm.Reds(0.28 + 0.72 * np.clip(seg_score, 0, 1))
    rgba[:, 3] = 0.25 + 0.70 * np.clip(seg_score, 0, 1)
    halo = LineCollection(segments, colors=rgba, linewidths=7.0, zorder=6)
    ax.add_collection(halo)

    # Restore the observed storage line over the halo so its trajectory remains crisp.
    ax.plot(
        df["date"], df["represented_storage_mm3"],
        linewidth=1.7, color="#222222", alpha=0.92, zorder=7,
    )


def finish(ax, fig, mode: str, output: Path) -> None:
    title_suffix = "top price-stress ribbon" if mode == "top" else "price stress on observed storage line"
    ax.set_title(
        f"2024 hydro storage and WAEERC risk zones, with {title_suffix}",
        loc="left", fontsize=14.5, pad=12,
    )

    handles, labels = ax.get_legend_handles_labels()
    handles.extend([
        Patch(facecolor="#fdae61", alpha=0.24, label="Watch zone (right axis)"),
        Patch(facecolor="#f46d43", alpha=0.22, label="Alert zone (right axis)"),
        Patch(facecolor="#d73027", alpha=0.24, label="Emergency zone (right axis)"),
        Patch(facecolor="#8b0000", alpha=0.65, label="Daily P95 wholesale price stress"),
    ])
    labels.extend([
        "Watch zone (right axis)",
        "Alert zone (right axis)",
        "Emergency zone (right axis)",
        "Daily P95 wholesale price stress",
    ])
    ax.legend(handles, labels, frameon=False, loc="upper right", fontsize=8.3)

    note = (
        "Price stress is shown only in the narrow top ribbon; it no longer washes across the hydro/WAEERC field."
        if mode == "top"
        else "Price stress colours a halo along the observed hydro line. This is a temporal overlay, not a claim that hydro level alone caused price."
    )
    ax.text(0.01, 0.022, note, transform=ax.transAxes, fontsize=8.6, va="bottom")
    fig.text(
        0.01, 0.004,
        "Sources: Electricity Authority HMD, final wholesale prices, and EMI historical electricity risk curves. "
        "The HMD storage line/bands and WAEERC zones use different storage definitions and axes; the overlay is contextual, not a one-to-one threshold comparison.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    plt.close(fig)
    print(f"Wrote {output}")


def render_variant(df: pd.DataFrame, mode: str, output: Path) -> None:
    score, _ = price_norm(df["price_p95_nzd_mwh"])
    fig, ax = plt.subplots(figsize=(13.5, 7.4))
    risk_ax = ax.twinx()
    draw_base(ax, risk_ax, df)

    if mode == "top":
        add_top_ribbon(ax, df, score)
    elif mode == "line":
        add_line_overlay(ax, df, score)
    else:
        raise ValueError(mode)

    finish(ax, fig, mode, output)


def main() -> None:
    if not INPUT.exists():
        raise RuntimeError(f"Missing prerequisite {INPUT}")
    base = pd.read_csv(INPUT, parse_dates=["date"])
    erc = fetch_erc_history()
    df = base.merge(erc, on="date", how="left").sort_values("date")
    if len(df) < 350:
        raise RuntimeError(f"Only {len(df)} joined chart days")

    render_variant(df, "top", OUTPUT_TOP)
    render_variant(df, "line", OUTPUT_LINE)


if __name__ == "__main__":
    main()
