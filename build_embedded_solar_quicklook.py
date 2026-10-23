#!/usr/bin/env python3
"""Rough gross embedded PV from Himawari irradiance. Python 3.10+, stdlib only.

The estimates are illustrative, not metered output or a confidence interval.
Run: python build_embedded_solar_quicklook.py --date 2026-10-21
"""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

API = "https://satellite-api.open-meteo.com/v1/archive"
# Installed embedded capacity, after excluding dispatched farms (Elenz Sep 2026).
# Use dated capacity assumptions until the EA GXP segmentation is integrated.
CAPACITY_MW = {"NI": 574.0, "SI": 287.0}
CAPACITY_DATE = "2026-09-30"
# Geographical samples, not individually weighted by registered rooftop MW.
POINTS = [
    ("Kaitaia", "NI", -35.114, 173.263),
    ("Whangarei", "NI", -35.731, 174.324),
    ("Auckland", "NI", -36.848, 174.763),
    ("Hamilton", "NI", -37.783, 175.279),
    ("Wellington", "NI", -41.286, 174.776),
    ("Nelson", "SI", -41.271, 173.285),
    ("Christchurch", "SI", -43.532, 172.636),
    ("Dunedin", "SI", -45.878, 170.503),
]
# Arbitrary sensitivity factors for horizontal-irradiance proxy, NOT fitted.
FACTORS = {"low": 0.70, "central": 0.80, "high": 0.85}

def download(day, lat, lon):
    query = urlencode({
        "latitude": lat, "longitude": lon,
        "start_date": day, "end_date": day,
        "hourly": "shortwave_radiation",
        "models": "satellite_radiation_seamless",
        "temporal_resolution": "native", "timezone": "Pacific/Auckland",
    })
    req = Request(API + "?" + query,
                  headers={"User-Agent": "NZGrid-EmbeddedSolarQuicklook/0.1"})
    with urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    hourly = payload.get("hourly", {})
    timestamps = hourly.get("time", [])
    radiation = hourly.get("shortwave_radiation", [])
    if not timestamps or len(timestamps) != len(radiation):
        raise ValueError("Missing or mismatched satellite series: " + str(payload.get("reason")))
    return dict(zip(timestamps, radiation))

def estimate(day, observations):
    timestamps = sorted({
        t for points in observations.values() for t in points if t.startswith(day)
    })
    if not timestamps:
        raise ValueError("No observations for requested NZ local date")
    rows = []
    for t in timestamps:
        means = {}
        for island in ("NI", "SI"):
            vals = []
            for name, where, _, _ in POINTS:
                if where != island:
                    continue
                value = observations.get(name, {}).get(t)
                if isinstance(value, (int, float)) and 0 <= value <= 1600:
                    vals.append(float(value))
            means[island] = sum(vals) / len(vals) if vals else None
        powers = {}
        for scenario, factor in FACTORS.items():
            powers[scenario] = None
            if all(means[isl] is not None for isl in ("NI", "SI")):
                powers[scenario] = round(sum(
                    CAPACITY_MW[isl] * min(means[isl] / 1000.0, 1.0) * factor
                    for isl in ("NI", "SI")
                ), 3)
        rows.append({
            "time_nz": t,
            "ghi_ni_wm2": round(means["NI"], 2) if means["NI"] is not None else None,
            "ghi_si_wm2": round(means["SI"], 2) if means["SI"] is not None else None,
            **{"embedded_mw_" + key: value for key, value in powers.items()},
        })
    valid = sum(r["embedded_mw_central"] is not None for r in rows)
    # Himawari native data are ten-minute preceding-interval averages.
    # Missing intervals are excluded, NOT assumed to have zero generation.
    energy = {
        name: round(sum(r["embedded_mw_" + name] * (10.0 / 60)
                        for r in rows if r["embedded_mw_" + name] is not None), 2)
        for name in FACTORS
    }
    summary = {
        "date": day, "status": "illustrative, not measured",
        "source": "JMA/JAXA Himawari via Open-Meteo Satellite Radiation API",
        "installed_embedded_mw": CAPACITY_MW, "capacity_as_of": CAPACITY_DATE,
        "capacity_source": "Elenz estimated embedded PV, September 2026",
        "factors": FACTORS, "samples_returned": len(rows),
        "valid_ten_minute_samples": valid,
        "coverage_fraction_normal_day": round(valid / 144, 4),
        "full_day_coverage_acceptable": valid >= 135,
        "daily_gross_embedded_mwh_for_valid_intervals": energy,
        "warning": "GHI and assumed system factors are only rough proxies for solar AC output. "
                   "Incomplete days contain partial energy only. "
                   "Sensitivity bounds are not statistical confidence intervals. "
                   "Exports and self-consumption are not separately estimated.",
    }
    return rows, summary

def main():
    yesterday = (datetime.now(ZoneInfo("Pacific/Auckland")).date()
                 - timedelta(days=1)).isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=yesterday)
    parser.add_argument("--out", type=Path,
                        default=Path("data/model/embedded_solar_quicklook"))
    args = parser.parse_args()
    date.fromisoformat(args.date)
    measurements = {}
    for name, island, lat, lon in POINTS:
        print("Fetching", name, island)
        measurements[name] = download(args.date, lat, lon)
    rows, summary = estimate(args.date, measurements)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = "embedded_solar_quicklook_" + args.date
    with (args.out / (stem + ".csv")).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out / (stem + ".json")).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("Wrote", stem, "; coverage", summary["coverage_fraction_normal_day"])
    if not summary["full_day_coverage_acceptable"]:
        print("WARNING: partial/incomplete day; do not quote as full-day MWh")
    print("Sampled interval MWh:", summary["daily_gross_embedded_mwh_for_valid_intervals"])

if __name__ == "__main__":
    main()
