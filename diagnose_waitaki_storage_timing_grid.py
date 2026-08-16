from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
INFLOWS = Path("data/hydro/model/inflows_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
GENERATION = Path("data/wholesale/model/generation_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT = Path("data/model/waitaki_storage_timing_grid.json")

YEAR = 2024
MM3_PER_DAY_PER_CUMECS = 0.0864
PKI_ACTUAL_FILE = "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv"
OHU_NATURAL_FILE = "SI_OHU_Natural_LakeOhau_Inflow_98614(3).csv"
PKI_SPILL_FILE = "SI_PKI_Spill_LakePukaki.csv"


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def source_series(path: Path, filename: str) -> dict[str, float]:
    out = {}
    for row in read_csv(path):
        if row.get("source_file") == filename and row.get("date", "").startswith(f"{YEAR}-"):
            out[row["date"]] = float(row["flow_m3s"])
    return out


def storage_series(site_code: str) -> dict[str, float]:
    out = {}
    for row in read_csv(STORAGE):
        if row.get("site_code") == site_code and row.get("date", "").startswith(f"{YEAR}-"):
            raw = row.get("total_storage_mm3") or row.get("active_storage_mm3") or row.get("storage_mm3")
            if raw not in (None, ""):
                out[row["date"]] = float(raw)
    return out


def site_spill(site_code: str) -> dict[str, float]:
    out = defaultdict(float)
    for row in read_csv(SPILL):
        if row.get("site_code") == site_code and row.get("date", "").startswith(f"{YEAR}-"):
            out[row["date"]] += float(row.get("flow_m3s") or 0.0)
    return dict(out)


def station_factor(code: str) -> float:
    for row in read_csv(ASSETS):
        if row.get("site_code") == code and row.get("plant_factor_cumecs_per_mw"):
            return float(row["plant_factor_cumecs_per_mw"])
    raise RuntimeError(f"Missing plant factor for {code}")


def station_flow(code: str) -> dict[str, float]:
    pf = station_factor(code)
    daily = defaultdict(float)
    for row in read_csv(GENERATION):
        if row.get("site_code") == code and row.get("date", "").startswith(f"{YEAR}-"):
            daily[row["date"]] += float(row["generation_mwh"])
    return {d: mwh / 24.0 * pf for d, mwh in daily.items()}


def stats(vals: list[float]) -> dict[str, float | int]:
    return {
        "count": len(vals),
        "mean": round(sum(vals) / len(vals), 3),
        "mae": round(sum(abs(v) for v in vals) / len(vals), 3),
        "rmse": round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3),
        "max_abs": round(max(abs(v) for v in vals), 3),
    }


def main() -> None:
    pki_storage = storage_series("PKI")
    ohu_storage = storage_series("OHA")
    pki_actual = source_series(INFLOWS, PKI_ACTUAL_FILE)
    ohu_natural = source_series(INFLOWS, OHU_NATURAL_FILE)
    pki_spill = source_series(SPILL, PKI_SPILL_FILE)
    ohu_spill = site_spill("OHA")
    oha = station_flow("OHA")

    dates = sorted(set(pki_storage) & set(ohu_storage) & set(pki_actual) & set(ohu_natural) & set(pki_spill) & set(oha))
    if len(dates) < 300:
        raise RuntimeError(f"Insufficient overlap: {len(dates)}")

    pki_delta = {}
    ohu_delta = {}
    for i in range(1, len(dates)):
        d = dates[i]
        prev = dates[i - 1]
        pki_delta[d] = pki_storage[d] - pki_storage[prev]
        ohu_delta[d] = ohu_storage[d] - ohu_storage[prev]

    offsets = {-1: "previous", 0: "same_day", 1: "next"}
    results = []
    for pki_off, pki_label in offsets.items():
        for ohu_off, ohu_label in offsets.items():
            residuals = []
            state = 0.0
            states = []
            for i in range(2, len(dates) - 1):
                d = dates[i]
                pki_delta_date = dates[i + pki_off]
                ohu_delta_date = dates[i + ohu_off]
                if pki_delta_date not in pki_delta or ohu_delta_date not in ohu_delta:
                    continue
                pki_release = pki_actual[d] - pki_delta[pki_delta_date] / MM3_PER_DAY_PER_CUMECS - pki_spill[d]
                ohu_release = ohu_natural[d] - ohu_delta[ohu_delta_date] / MM3_PER_DAY_PER_CUMECS - ohu_spill.get(d, 0.0)
                residual = pki_release + ohu_release - oha[d]
                residuals.append(residual)
                state += residual * MM3_PER_DAY_PER_CUMECS
                states.append(state)
            s = stats(residuals)
            state_range = max(states) - min(states)
            results.append({
                "pki_storage_delta_timing": pki_label,
                "ohu_storage_delta_timing": ohu_label,
                **s,
                "implied_unbounded_junction_storage_range_mm3": round(state_range, 3),
                "score": round(s["mae"] + 0.05 * state_range + 2.0 * abs(s["mean"]), 3),
            })

    results.sort(key=lambda r: r["score"])
    summary = {
        "status": "waitaki_pukaki_ohau_storage_timing_grid",
        "year": YEAR,
        "purpose": "Test whether daily Pukaki and Lake Ohau storage timestamp conventions explain the remaining upstream-Ohau-A balance noise and large latent junction buffer.",
        "ranking": results,
        "best": results[0],
        "interpretation": [
            "The preferred combination should reduce MAE/RMSE and, especially, the cumulative implied junction-storage range.",
            "A large implied junction-storage range indicates unresolved accounting or timestamp mismatch and should not be treated as physical canal storage.",
            "This diagnostic changes no dispatch assumptions; it only tests daily source/storage alignment against observed Ohau A flow."
        ],
    }
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    for row in results[:5]:
        print(row)


if __name__ == "__main__":
    main()
