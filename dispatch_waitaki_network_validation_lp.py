from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

INPUT = Path("data/model/waitaki_network_balance_v4_daily.csv")
ASSETS = Path("data/model/hydro_assets_current.csv")
OUT_DAILY = Path("data/model/waitaki_network_validation_lp_daily.csv")
OUT_SUMMARY = Path("data/model/waitaki_network_validation_lp_summary.json")

MM3_PER_DAY_PER_CUMECS = 0.0864
STATIONS = ("OHA", "OHB", "OHC")


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def station_limits() -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in read_csv(ASSETS):
        code = row.get("site_code", "")
        if code not in STATIONS:
            continue
        if not row.get("generating_capacity_mw") or not row.get("plant_factor_cumecs_per_mw"):
            continue
        capacity = float(row["generating_capacity_mw"])
        pf = float(row["plant_factor_cumecs_per_mw"])
        out[code] = {
            "capacity_mw": capacity,
            "plant_factor_cumecs_per_mw": pf,
            "capacity_flow_m3s": capacity * pf,
        }
    missing = sorted(set(STATIONS) - set(out))
    if missing:
        raise RuntimeError(f"Missing station limits for {missing}")
    return out


def stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "mae": None, "rmse": None, "max_abs": None}
    a = np.asarray(values, dtype=float)
    return {
        "count": int(a.size),
        "mean": round(float(a.mean()), 4),
        "mae": round(float(np.abs(a).mean()), 4),
        "rmse": round(float(np.sqrt(np.mean(a * a))), 4),
        "max_abs": round(float(np.max(np.abs(a))), 4),
    }


def main() -> None:
    rows = list(read_csv(INPUT))
    if len(rows) < 300:
        raise RuntimeError(f"Expected a full validation year, found {len(rows)} rows")
    limits = station_limits()
    n = len(rows)

    q0 = 0
    dev0 = 3 * n
    state0 = 9 * n
    state_abs0 = 12 * n
    nv = 18 * n

    def qidx(s: int, t: int) -> int:
        return q0 + s * n + t

    def dpos(s: int, t: int) -> int:
        return dev0 + (2 * s) * n + t

    def dneg(s: int, t: int) -> int:
        return dev0 + (2 * s + 1) * n + t

    def sidx(node: int, t: int) -> int:
        return state0 + node * n + t

    def spos(node: int, t: int) -> int:
        return state_abs0 + (2 * node) * n + t

    def sneg(node: int, t: int) -> int:
        return state_abs0 + (2 * node + 1) * n + t

    c = np.zeros(nv)
    for s in range(3):
        c[dev0 + (2 * s) * n : dev0 + (2 * s + 2) * n] = 1.0 / n
    for node in range(3):
        c[state_abs0 + (2 * node) * n : state_abs0 + (2 * node + 2) * n] = 0.002 / n

    Aeq: list[np.ndarray] = []
    beq: list[float] = []

    obs_cols = ["OHA_turbine_flow_m3s", "OHB_turbine_flow_m3s", "OHC_turbine_flow_m3s"]
    for s, col in enumerate(obs_cols):
        for t, row in enumerate(rows):
            a = np.zeros(nv)
            a[qidx(s, t)] = 1.0
            a[dpos(s, t)] = -1.0
            a[dneg(s, t)] = 1.0
            Aeq.append(a)
            beq.append(float(row[col]))

    for t, row in enumerate(rows):
        prev = (t - 1) % n
        pki = float(row["PKI_balance_release_m3s"])
        ohu = float(row["OHU_release_next_delta_m3s"])
        spill_rth = float(row["RTH_spill_m3s"])

        a = np.zeros(nv)
        a[sidx(0, t)] = 1.0
        a[sidx(0, prev)] = -1.0
        a[qidx(0, t)] = MM3_PER_DAY_PER_CUMECS
        Aeq.append(a)
        beq.append(MM3_PER_DAY_PER_CUMECS * (pki + ohu))

        a = np.zeros(nv)
        a[sidx(1, t)] = 1.0
        a[sidx(1, prev)] = -1.0
        a[qidx(0, t)] = -MM3_PER_DAY_PER_CUMECS
        a[qidx(1, t)] = MM3_PER_DAY_PER_CUMECS
        Aeq.append(a)
        beq.append(-MM3_PER_DAY_PER_CUMECS * spill_rth)

        a = np.zeros(nv)
        a[sidx(2, t)] = 1.0
        a[sidx(2, prev)] = -1.0
        a[qidx(1, t)] = -MM3_PER_DAY_PER_CUMECS
        a[qidx(2, t)] = MM3_PER_DAY_PER_CUMECS
        Aeq.append(a)
        beq.append(0.0)

        for node in range(3):
            a = np.zeros(nv)
            a[sidx(node, t)] = 1.0
            a[spos(node, t)] = -1.0
            a[sneg(node, t)] = 1.0
            Aeq.append(a)
            beq.append(0.0)

    bounds: list[tuple[float | None, float | None]] = [(0.0, None)] * nv
    for s, code in enumerate(STATIONS):
        cap = limits[code]["capacity_flow_m3s"]
        for t in range(n):
            bounds[qidx(s, t)] = (0.0, cap)
    for node in range(3):
        for t in range(n):
            bounds[sidx(node, t)] = (None, None)

    result = linprog(
        c,
        A_eq=np.vstack(Aeq),
        b_eq=np.asarray(beq),
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        raise RuntimeError(f"Waitaki validation LP failed: {result.message}")

    x = result.x
    output = []
    deviations: dict[str, list[float]] = {s: [] for s in STATIONS}
    states: dict[str, list[float]] = {"junction": [], "ruataniwha": [], "ohb_ohc_transit": []}
    state_names = list(states)
    total_gen_observed = 0.0
    total_gen_lp = 0.0

    for t, row in enumerate(rows):
        item: dict[str, object] = {"date": row["date"]}
        for s, code in enumerate(STATIONS):
            obs = float(row[obs_cols[s]])
            q = float(x[qidx(s, t)])
            deviation = q - obs
            deviations[code].append(deviation)
            item[f"{code}_observed_flow_m3s"] = round(obs, 6)
            item[f"{code}_lp_flow_m3s"] = round(q, 6)
            item[f"{code}_flow_error_m3s"] = round(deviation, 6)
            mw_per_cumec = 1.0 / limits[code]["plant_factor_cumecs_per_mw"]
            total_gen_observed += obs * mw_per_cumec * 24.0
            total_gen_lp += q * mw_per_cumec * 24.0
        for node, name in enumerate(state_names):
            value = float(x[sidx(node, t)])
            states[name].append(value)
            item[f"{name}_latent_storage_mm3"] = round(value, 6)
        output.append(item)

    latent_summary = {}
    for name, values in states.items():
        arr = np.asarray(values)
        latent_summary[name] = {
            "min_mm3": round(float(arr.min()), 4),
            "max_mm3": round(float(arr.max()), 4),
            "range_mm3": round(float(arr.max() - arr.min()), 4),
            "mean_abs_mm3": round(float(np.abs(arr).mean()), 4),
        }

    summary = {
        "status": "waitaki_explicit_network_validation_lp",
        "year": 2024,
        "solver": "scipy.optimize.linprog HiGHS",
        "success": bool(result.success),
        "objective": round(float(result.fun), 6),
        "network": [
            "Pukaki storage-balance release + storage-timed Lake Ohau release -> Ohau A",
            "Ohau A -> Lake Ruataniwha -> Ohau B",
            "Ohau B -> Ohau C",
        ],
        "station_capacity_limits": limits,
        "station_flow_error_m3s": {code: stats(vals) for code, vals in deviations.items()},
        "latent_storage": latent_summary,
        "generation": {
            "observed_OHA_OHB_OHC_gwh": round(total_gen_observed / 1000.0, 3),
            "lp_OHA_OHB_OHC_gwh": round(total_gen_lp / 1000.0, 3),
            "difference_percent": round((total_gen_lp / total_gen_observed - 1.0) * 100.0, 4),
        },
        "interpretation": [
            "This is a validation LP, not a forecasting dispatch policy.",
            "Observed PKI/OHA storage timing is used to test the explicit junction and serial Ohau network.",
            "Latent storage states represent unresolved daily canal, Ruataniwha and transit timing; their required ranges are diagnostics, not assumed physical reservoir capacities.",
            "A credible validation result should reproduce observed station flows with small errors and modest latent-storage ranges while respecting turbine capacity bounds."
        ],
    }

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    with OUT_DAILY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    OUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_DAILY} and {OUT_SUMMARY}")
    print(json.dumps(summary["station_flow_error_m3s"], indent=2))
    print(json.dumps(summary["latent_storage"], indent=2))


if __name__ == "__main__":
    main()
