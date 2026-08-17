"""Summarize paired JADE fixed-policy impulse simulations into small derived tables."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

WORK = Path(".jade_impulse")
OUT = Path("data/jade/impulse")


def one(path: Path) -> Path:
    matches = list(path)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one match, got {len(matches)}: {matches}")
    return matches[0]


def find_case(case: str, simdir: str) -> Path:
    matches = list((WORK / "Output" / case).glob(f"*/{simdir}"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {case} simulation directory, got {matches}")
    return matches[0]


def read_storage(path: Path) -> dict[tuple[int, int], float]:
    out = {}
    with (path / "StoredEnergy.csv").open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out[(int(r["simulation"]), int(r[" stage"]))] = float(r[" energy_GWh"])
    return out


def read_calendar(path: Path) -> dict[tuple[int, int], tuple[int, int]]:
    out = {}
    with (path / "tidy_results.csv").open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (int(r["simulation"]), int(r["stage"]))
            if key not in out:
                out[key] = (int(r["year"]), int(r["week"]))
    return out


def hours_table() -> dict[tuple[int, int, str], float]:
    candidates = list((WORK / "Input" / "baseline").glob("data_files/*/hours_per_block.csv"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one hours_per_block.csv, got {candidates}")
    out = {}
    with candidates[0].open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(line for line in f if not line.lstrip().startswith("%"))
        for r in rows:
            y, w = int(r["YEAR"]), int(r["WEEK"])
            for b in ("PEAK", "SHOULDER", "OFFPEAK"):
                out[(y, w, b)] = float(r[b])
    return out


def read_thermal(path: Path, calendar: dict[tuple[int, int], tuple[int, int]], hours: dict) -> dict[tuple[int, int], float]:
    mw = defaultdict(float)
    with (path / "tidy_results.csv").open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["type"] != "thermal_use":
                continue
            key = (int(r["simulation"]), int(r["stage"]))
            year, week = calendar[key]
            # For non-price two-dimensional JADE result arrays, the second key is
            # written into the tidy_results 'demand' column; thermal_use uses station,block.
            block = r["demand"].strip().upper()
            if block not in {"PEAK", "SHOULDER", "OFFPEAK"}:
                raise RuntimeError(f"Unexpected thermal-use load block: {block!r}")
            mw[key] += float(r["value"]) * hours[(year, week, block)] / 1000.0
    return dict(mw)


def pct(values, q):
    return float(np.percentile(np.asarray(values, dtype=float), q))


def main() -> None:
    baseline = find_case("baseline", "impulse_baseline")
    pulse = find_case("pulse", "impulse_pulse")
    cal = read_calendar(baseline)
    hrs = hours_table()
    bs = read_storage(baseline)
    ps = read_storage(pulse)
    bt = read_thermal(baseline, cal, hrs)
    pt = read_thermal(pulse, cal, hrs)

    exp = json.loads((WORK / "experiment.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    cumulative = defaultdict(float)
    for key in sorted(cal, key=lambda k: (k[0], k[1])):
        sim, stage = key
        year, week = cal[key]
        thermal_avoided = bt.get(key, 0.0) - pt.get(key, 0.0)
        cumulative[sim] += thermal_avoided
        rows.append({
            "simulation": sim,
            "stage": stage,
            "calendar_year": year,
            "calendar_week": week,
            "delta_stored_energy_gwh": ps[key] - bs[key],
            "thermal_generation_avoided_gwh": thermal_avoided,
            "cumulative_thermal_avoided_gwh": cumulative[sim],
        })

    detail = OUT / "fixed_policy_impulse_simulations.csv"
    with detail.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    grouped = defaultdict(list)
    for r in rows:
        grouped[(r["stage"], r["calendar_year"], r["calendar_week"])].append(r)

    summary = []
    for (stage, year, week), vals in sorted(grouped.items()):
        ds = [v["delta_stored_energy_gwh"] for v in vals]
        ct = [v["cumulative_thermal_avoided_gwh"] for v in vals]
        summary.append({
            "stage": stage,
            "calendar_year": year,
            "calendar_week": week,
            "simulation_count": len(vals),
            "storage_survival_p05_gwh_per_gwh": pct(ds, 5) / (exp["pulse_energy_mwh"] / 1000),
            "storage_survival_median_gwh_per_gwh": pct(ds, 50) / (exp["pulse_energy_mwh"] / 1000),
            "storage_survival_p95_gwh_per_gwh": pct(ds, 95) / (exp["pulse_energy_mwh"] / 1000),
            "cumulative_thermal_avoided_p05_gwh": pct(ct, 5),
            "cumulative_thermal_avoided_median_gwh": pct(ct, 50),
            "cumulative_thermal_avoided_p95_gwh": pct(ct, 95),
        })

    summary_path = OUT / "fixed_policy_impulse_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader(); w.writerows(summary)

    provenance = {
        **exp,
        "status": "diagnostic only",
        "paired_simulations": len({r["simulation"] for r in rows}),
        "derived_files": [str(detail), str(summary_path)],
        "interpretation": "Storage survival is pulse minus baseline stored energy, normalized by pulse GWh. Thermal avoided is baseline minus pulse thermal generation under the same published policy and matched simulation seed.",
    }
    (OUT / "fixed_policy_impulse_provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
