from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import least_squares

SOLAR_20 = Path("data/distributed_generation/model/distributed_solar_adoption_20pct.csv")
EV_ANCHOR = Path("data/transport/ev_fleet_anchor.csv")
VISUAL_DIR = Path("data/visuals")
OUT_PNG = VISUAL_DIR / "ev_feed_in_vs_small_solar_capacity.png"
OUT_CSV = VISUAL_DIR / "ev_feed_in_vs_small_solar_plot_data.csv"
OUT_JSON = VISUAL_DIR / "ev_feed_in_vs_small_solar_manifest.json"

START_YEAR = 2026
END_YEAR = 2035
YEARS = np.arange(START_YEAR, END_YEAR + 1)
TOTAL_LIGHT_FLEET_2035 = 4_500_000
EECA_2030_EV = 550_000
EECA_2035_EV = int(round(TOTAL_LIGHT_FLEET_2035 * 0.38))
MOT_2035_EV = int(round(TOTAL_LIGHT_FLEET_2035 * 0.30))

CURRENT_EXPORT_KW = 5.0
CURRENT_CHARGER_SHARE = 0.47
CURRENT_PARTICIPATION = 0.50
CURRENT_EFFECTIVE_KW = CURRENT_EXPORT_KW * CURRENT_CHARGER_SHARE * CURRENT_PARTICIPATION

FUTURE_EXPORT_KW = 7.0
FUTURE_CHARGER_SHARE = 0.70
FUTURE_PARTICIPATION = 0.50
FUTURE_EFFECTIVE_KW = FUTURE_EXPORT_KW * FUTURE_CHARGER_SHARE * FUTURE_PARTICIPATION

COLORS = {
    "solar": "#e6a700",
    "eeca": "#2962a3",
    "mot": "#6f5aa8",
}


def decimal_year(d: date) -> float:
    start = date(d.year, 1, 1)
    end = date(d.year + 1, 1, 1)
    return d.year + (d - start).days / (end - start).days


def read_ev_anchor() -> tuple[date, int, dict[str, str]]:
    with EV_ANCHOR.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"No rows in {EV_ANCHOR}")
    row = rows[-1]
    return (
        datetime.strptime(row["as_of_date"], "%Y-%m-%d").date(),
        int(float(row["zero_emission_light_vehicles"])),
        row,
    )


def annual_small_solar_capacity() -> np.ndarray:
    df = pd.read_csv(SOLAR_20)
    df["month_end"] = pd.to_datetime(df["month_end"])
    df = df.sort_values("month_end")
    output: list[float] = []
    for year in YEARS:
        eligible = df[df["month_end"] <= pd.Timestamp(year=int(year), month=12, day=31)]
        if eligible.empty:
            raise RuntimeError(f"{SOLAR_20} has no data available for {year}")
        output.append(float(eligible.iloc[-1]["small_capacity_mw"]))
    return np.array(output, dtype=float)


def fit_eeca_logistic(anchor_date: date, current_ev: int):
    x = np.array(
        [
            decimal_year(anchor_date),
            decimal_year(date(2030, 12, 31)),
            decimal_year(date(2035, 12, 31)),
        ],
        dtype=float,
    )
    y = np.array([current_ev, EECA_2030_EV, EECA_2035_EV], dtype=float)

    def predict(params: np.ndarray, t: np.ndarray) -> np.ndarray:
        saturation = np.exp(params[0])
        rate = np.exp(params[1])
        midpoint = params[2]
        return saturation / (1.0 + np.exp(-rate * (t - midpoint)))

    def residual(params: np.ndarray) -> np.ndarray:
        return (predict(params, x) - y) / y

    result = least_squares(
        residual,
        x0=np.array([np.log(3_000_000.0), np.log(0.4), 2034.0]),
        bounds=(
            np.array([np.log(EECA_2035_EV + 1.0), np.log(0.01), 2000.0]),
            np.array([np.log(100_000_000.0), np.log(5.0), 2060.0]),
        ),
        max_nfev=10_000,
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
    )
    if not result.success:
        raise RuntimeError(f"EV logistic fit failed: {result.message}")

    fitted = predict(result.x, x)
    if np.max(np.abs(fitted - y)) > 1.0:
        raise RuntimeError(f"EV logistic fit did not reproduce anchors closely enough: {fitted} vs {y}")

    saturation = float(np.exp(result.x[0]))
    rate = float(np.exp(result.x[1]))
    midpoint = float(result.x[2])

    def curve(t: np.ndarray) -> np.ndarray:
        return saturation / (1.0 + np.exp(-rate * (t - midpoint)))

    return curve, {
        "form": "K / (1 + exp(-rate * (year - midpoint)))",
        "saturation_vehicles": saturation,
        "rate_per_year": rate,
        "midpoint_year": midpoint,
        "anchors": {
            anchor_date.isoformat(): current_ev,
            "2030-12-31": EECA_2030_EV,
            "2035-12-31": EECA_2035_EV,
        },
    }


def build_ev_paths(anchor_date: date, current_ev: int) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    eeca_curve, fit_meta = fit_eeca_logistic(anchor_date, current_ev)
    year_points = np.array([decimal_year(date(int(year), 12, 31)) for year in YEARS], dtype=float)
    eeca = eeca_curve(year_points)

    eeca_anchor = float(eeca_curve(np.array([decimal_year(anchor_date)]))[0])
    eeca_end = float(eeca_curve(np.array([decimal_year(date(2035, 12, 31))]))[0])
    progress = np.clip((eeca - eeca_anchor) / (eeca_end - eeca_anchor), 0.0, 1.0)
    mot = current_ev + progress * (MOT_2035_EV - current_ev)

    fit_meta["mot_path_method"] = (
        "Uses the EECA/CCC logistic curve's normalized progress from the current anchor to 2035, "
        "scaled between the same current EV stock and the MoT 30% of 4.5 million 2035 target. "
        "This preserves S-curve timing and avoids straight-line front-loading."
    )
    return eeca, mot, fit_meta


def first_crossover_year(ev_mw: np.ndarray, solar_mw: np.ndarray) -> int | None:
    crossed = np.where(ev_mw >= solar_mw)[0]
    return int(YEARS[crossed[0]]) if len(crossed) else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Render EV feed-in peak capacity versus small distributed solar capacity.")
    parser.add_argument("--force", action="store_true", help="Accepted for workflow compatibility; this builder always renders.")
    parser.parse_args()

    anchor_date, current_ev, anchor_meta = read_ev_anchor()
    solar = annual_small_solar_capacity()
    eeca_count, mot_count, fit_meta = build_ev_paths(anchor_date, current_ev)

    series = {
        "eeca_current": eeca_count * CURRENT_EFFECTIVE_KW / 1000.0,
        "eeca_future": eeca_count * FUTURE_EFFECTIVE_KW / 1000.0,
        "mot_current": mot_count * CURRENT_EFFECTIVE_KW / 1000.0,
        "mot_future": mot_count * FUTURE_EFFECTIVE_KW / 1000.0,
    }
    crossover = {key: first_crossover_year(values, solar) for key, values in series.items()}

    plot = pd.DataFrame(
        {
            "year": YEARS,
            "small_solar_20pct_mw": np.round(solar, 3),
            "ev_count_eeca_ccc": np.round(eeca_count).astype(int),
            "ev_count_mot": np.round(mot_count).astype(int),
            "ev_feedin_eeca_current_mw": np.round(series["eeca_current"], 3),
            "ev_feedin_eeca_future_mw": np.round(series["eeca_future"], 3),
            "ev_feedin_mot_current_mw": np.round(series["mot_current"], 3),
            "ev_feedin_mot_future_mw": np.round(series["mot_future"], 3),
            "eeca_current_at_or_above_solar": series["eeca_current"] >= solar,
            "eeca_future_at_or_above_solar": series["eeca_future"] >= solar,
            "mot_current_at_or_above_solar": series["mot_current"] >= solar,
            "mot_future_at_or_above_solar": series["mot_future"] >= solar,
        }
    )

    fig, ax = plt.subplots(figsize=(12, 8.5))
    ax.plot(YEARS, solar, color=COLORS["solar"], linewidth=3.3, label="Small distributed solar (<25 kW), 20% saturation case", zorder=4)
    ax.plot(YEARS, series["eeca_current"], color=COLORS["eeca"], linewidth=2.0, linestyle="--", label="EECA / CCC EV path, current-like export")
    ax.plot(YEARS, series["eeca_future"], color=COLORS["eeca"], linewidth=2.8, linestyle="-", label="EECA / CCC EV path, future-ready export")
    ax.plot(YEARS, series["mot_current"], color=COLORS["mot"], linewidth=2.0, linestyle="--", label="MoT EV path, current-like export")
    ax.plot(YEARS, series["mot_future"], color=COLORS["mot"], linewidth=2.8, linestyle="-", label="MoT EV path, future-ready export")

    for key, year in crossover.items():
        if year is None:
            continue
        idx = int(year - START_YEAR)
        ax.scatter([year], [series[key][idx]], s=45, edgecolor="white", linewidth=0.7, zorder=6)
        ax.annotate(
            f"crosses {year}",
            xy=(year, series[key][idx]),
            xytext=(6, 9 if key.startswith("eeca") else -16),
            textcoords="offset points",
            fontsize=8,
            ha="left",
            va="bottom" if key.startswith("eeca") else "top",
        )

    ymax = max(float(solar.max()), *(float(v.max()) for v in series.values()))
    ymax = np.ceil(ymax / 500.0) * 500.0 + 500.0
    ax.set_ylim(0, ymax)
    ax.set_xlim(START_YEAR - 0.15, END_YEAR + 0.15)
    ax.set_xticks(YEARS)
    ax.set_xlabel("Year")
    ax.set_ylabel("Capacity (MW)")
    ax.set_title("Potential EV peak feed-in capacity vs small distributed solar")
    ax.grid(axis="y", alpha=0.20)
    ax.legend(loc="upper left", fontsize=8.5, frameon=True)

    note = (
        "Capacity comparison, not energy: EV feed-in could be a peak-period / evening resource, while solar is primarily daytime generation. "
        "EV export depends on charger adoption, bidirectional capability, standards and customer participation. "
        "EECA/CCC and MoT fleet paths are scenario anchors, not precise forecasts."
    )
    assumptions = (
        f"Export assumptions: current-like = {CURRENT_EXPORT_KW:.0f} kW × {CURRENT_CHARGER_SHARE:.0%} charger adoption × {CURRENT_PARTICIPATION:.0%} participation = {CURRENT_EFFECTIVE_KW:.3f} kW/EV; "
        f"future-ready = {FUTURE_EXPORT_KW:.0f} kW × {FUTURE_CHARGER_SHARE:.0%} × {FUTURE_PARTICIPATION:.0%} = {FUTURE_EFFECTIVE_KW:.2f} kW/EV."
    )
    fig.subplots_adjust(bottom=0.21)
    fig.text(0.06, 0.085, note, fontsize=8, ha="left", va="top", wrap=True)
    fig.text(0.06, 0.045, assumptions, fontsize=8, ha="left", va="top", wrap=True)

    VISUAL_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=180, bbox_inches="tight")
    plt.close(fig)
    plot.to_csv(OUT_CSV, index=False)

    summary = {}
    for key, column in {
        "eeca_current": "ev_feedin_eeca_current_mw",
        "eeca_future": "ev_feedin_eeca_future_mw",
        "mot_current": "ev_feedin_mot_current_mw",
        "mot_future": "ev_feedin_mot_future_mw",
    }.items():
        row_2030 = plot.loc[plot["year"] == 2030].iloc[0]
        row_2035 = plot.loc[plot["year"] == 2035].iloc[0]
        summary[key] = {
            "2030_mw": float(row_2030[column]),
            "2035_mw": float(row_2035[column]),
            "crosses_central_solar_by_2030": crossover[key] is not None and crossover[key] <= 2030,
            "crosses_central_solar_by_2035": crossover[key] is not None and crossover[key] <= 2035,
            "first_crossover_year": crossover[key],
        }

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "png": str(OUT_PNG),
            "plot_data_csv": str(OUT_CSV),
            "manifest_json": str(OUT_JSON),
        },
        "source_files": [str(SOLAR_20), str(EV_ANCHOR)],
        "solar": {
            "source_file": str(SOLAR_20),
            "scope": "Small distributed solar below 25 kW",
            "central_case_saturation_pct": 20,
            "capacity_basis": "Total projected installed small-solar nameplate capacity at each year end",
        },
        "ev_fleet": {
            "current_anchor_date": anchor_date.isoformat(),
            "current_zero_emission_light_vehicles": current_ev,
            "current_anchor_source_name": anchor_meta["source_name"],
            "current_anchor_source_url": anchor_meta["source_url"],
            "current_anchor_definition": anchor_meta["source_definition"],
            "eeca_ccc_2030_ev": EECA_2030_EV,
            "eeca_ccc_2035_share_of_light_fleet": 0.38,
            "mot_2035_share_of_light_fleet": 0.30,
            "assumed_total_light_fleet_2035": TOTAL_LIGHT_FLEET_2035,
            "eeca_ccc_2035_ev": EECA_2035_EV,
            "mot_2035_ev": MOT_2035_EV,
            "path_method": fit_meta,
        },
        "export_assumptions": {
            "current_like": {
                "export_kw_per_participating_ev": CURRENT_EXPORT_KW,
                "dedicated_charger_share": CURRENT_CHARGER_SHARE,
                "participation_share_of_charger_owners": CURRENT_PARTICIPATION,
                "effective_kw_per_ev": CURRENT_EFFECTIVE_KW,
            },
            "future_ready": {
                "export_kw_per_participating_ev": FUTURE_EXPORT_KW,
                "dedicated_charger_share": FUTURE_CHARGER_SHARE,
                "participation_share_of_charger_owners": FUTURE_PARTICIPATION,
                "effective_kw_per_ev": FUTURE_EFFECTIVE_KW,
            },
        },
        "crossover_years_vs_small_solar_20pct": crossover,
        "summary": summary,
        "methodology_notes": [
            "This is a power-capacity comparison, not an energy comparison.",
            "EV export capacity is potentially dispatchable around peak periods but depends on vehicles being plugged in, bidirectional hardware and standards, available state of charge, network permissions and customer participation.",
            "Solar capacity is nameplate capacity and is primarily a daytime resource; equal MW does not imply equal system value or equal annual energy.",
            "The EECA/CCC path is a three-anchor logistic fit. The MoT path uses the same normalized adoption timing and a lower 2035 target to avoid front-loaded straight-line interpolation.",
            "Fleet policy anchors are scenarios, not precise forecasts.",
        ],
    }
    OUT_JSON.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_PNG}")
    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_JSON}")
    print(f"Crossovers: {crossover}")


if __name__ == "__main__":
    main()
