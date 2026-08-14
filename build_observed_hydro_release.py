from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

STORAGE = Path("data/hydro/model/storage_daily.csv")
INFLOW = Path("data/hydro/model/inflows_daily.csv")
SPILL = Path("data/hydro/model/spill_daily.csv")
OUT = Path("data/model/observed_hydro_release_daily.csv")
SUMMARY = Path("data/model/observed_hydro_release_summary.json")

# Storage-site code -> corresponding headwater inflow code where HMD uses a different code.
INFLOW_CODE = {
    "HWE": "HWE",
    "MAN": "MAN",
    "OHA": "OHU",
    "PKI": "PKI",
    "TAU": "TAU",
    "TKA": "TEK",
    "TPO": "TPO",
    "WKA": "WKA",
}

# These are the major reservoirs with directly matched storage + headwater inflow series.
SITES = set(INFLOW_CODE)


def f(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def main() -> None:
    storage: dict[tuple[str, str], float] = {}
    reservoir_name: dict[str, str] = {}
    with STORAGE.open(encoding="utf-8-sig", newline="") as h:
        for r in csv.DictReader(h):
            site = r["site_code"]
            if site not in SITES:
                continue
            v = f(r.get("total_storage_mm3"))
            if v is None:
                continue
            storage[(r["date"], site)] = v
            reservoir_name[site] = r.get("reservoir") or site

    inflow_by_key: dict[tuple[str, str], float] = {}
    reverse_inflow = {v: k for k, v in INFLOW_CODE.items()}
    with INFLOW.open(encoding="utf-8-sig", newline="") as h:
        for r in csv.DictReader(h):
            src = r["site_code"]
            site = reverse_inflow.get(src)
            if site is None:
                continue
            v = f(r.get("volume_mm3_day"))
            if v is not None:
                inflow_by_key[(r["date"], site)] = v

    # HMD spill files are labelled Spill/release. Sum all rows for a reservoir/day because
    # some storages (notably Tekapo) have multiple bypass/release paths.
    spill_release: dict[tuple[str, str], float] = defaultdict(float)
    with SPILL.open(encoding="utf-8-sig", newline="") as h:
        for r in csv.DictReader(h):
            site = r["site_code"]
            if site not in SITES:
                continue
            v = f(r.get("volume_mm3_day"))
            if v is not None:
                spill_release[(r["date"], site)] += v

    dates = sorted({d for d, _ in storage})
    rows: list[dict[str, object]] = []
    by_site: dict[str, list[float]] = defaultdict(list)
    negative_by_site: dict[str, int] = defaultdict(int)

    previous_storage: dict[str, float] = {}
    previous_date: dict[str, str] = {}
    for date_s in dates:
        for site in sorted(SITES):
            key = (date_s, site)
            s = storage.get(key)
            qin = inflow_by_key.get(key)
            if s is None or qin is None:
                continue

            prev = previous_storage.get(site)
            prev_date = previous_date.get(site)
            previous_storage[site] = s
            previous_date[site] = date_s
            if prev is None:
                continue

            # Only use consecutive calendar days; gaps would make a one-day delta invalid.
            if (datetime.fromisoformat(date_s) - datetime.fromisoformat(prev_date)).days != 1:
                continue

            delta = s - prev
            total_outflow = qin - delta
            recorded = spill_release.get(key, 0.0)
            residual = total_outflow - recorded
            if residual < 0:
                negative_by_site[site] += 1
            by_site[site].append(residual)

            rows.append({
                "date": date_s,
                "site_code": site,
                "reservoir": reservoir_name.get(site, site),
                "storage_mm3": round(s, 6),
                "storage_change_mm3_day": round(delta, 6),
                "headwater_inflow_mm3_day": round(qin, 6),
                "implied_total_outflow_mm3_day": round(total_outflow, 6),
                "hmd_spill_release_mm3_day": round(recorded, 6),
                "residual_controlled_release_mm3_day": round(residual, 6),
            })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date", "site_code", "reservoir", "storage_mm3", "storage_change_mm3_day",
        "headwater_inflow_mm3_day", "implied_total_outflow_mm3_day",
        "hmd_spill_release_mm3_day", "residual_controlled_release_mm3_day",
    ]
    with OUT.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    site_summary = {}
    for site in sorted(by_site):
        vals = by_site[site]
        site_summary[site] = {
            "reservoir": reservoir_name.get(site, site),
            "days": len(vals),
            "negative_residual_days": negative_by_site[site],
            "negative_residual_fraction": round(negative_by_site[site] / len(vals), 6) if vals else None,
            "mean_residual_controlled_release_mm3_day": round(sum(vals) / len(vals), 6) if vals else None,
            "min_residual_controlled_release_mm3_day": round(min(vals), 6) if vals else None,
            "max_residual_controlled_release_mm3_day": round(max(vals), 6) if vals else None,
        }

    summary = {
        "definition": "Observed one-day reservoir water balance: implied total outflow = headwater inflow - change in observed storage. Residual controlled release = implied total outflow - HMD spill/release series.",
        "purpose": "Calibration diagnostic for future synthetic hydro dispatch. It is not assumed to be turbine flow without further validation.",
        "sites": site_summary,
        "important_cautions": [
            "The HMD spill series is labelled spill/release and can include regulated outflow or bypass flows, not only wasted spill.",
            "Daily storage/inflow timing and measurement conventions can create negative residuals even when the physical daily balance is valid at finer resolution.",
            "Te Anau/Manapouri and branched Waitaki routing require system-level interpretation before residual release is converted to generation.",
            "This diagnostic intentionally preserves negative residuals instead of clipping them so data-alignment problems remain visible."
        ],
        "next_step": "Use observed release distributions and storage trajectories as calibration targets for a synthetic daily dispatcher, then validate simulated annual hydro generation and minimum storage against historical years."
    }
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(rows)} rows)")
    print(f"Wrote {SUMMARY}")


if __name__ == "__main__":
    main()
