"""Prepare a temporary JADE workspace for a paired +1 GWh renewable impulse test.

Raw EA JADE inputs and policy files are downloaded only into a temporary working
folder used by the manual GitHub Actions workflow. They are not committed.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

LATEST = Path("data/metadata/jade_latest.json")


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def relative_from_inputs(name: str) -> Path:
    marker = "/Inputs/"
    if marker not in name:
        raise ValueError(f"Not an Inputs path: {name}")
    return Path(name.split(marker, 1)[1])


def find_input(meta: dict, basename: str) -> dict:
    for item in meta["input_files"]:
        if Path(item["name"]).name == basename:
            return item
    raise KeyError(f"Input not found: {basename}")


def find_output(meta: dict, suffix: str) -> dict:
    for item in meta["output_files"]:
        if item["name"].endswith(suffix):
            return item
    raise KeyError(f"Output not found: {suffix}")


def pulse_row(hours_file: Path, year: int, week: int, node: str, energy_mwh: float) -> list[str]:
    with hours_file.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.DictReader(line for line in f if not line.lstrip().startswith("%"))
        for row in rows:
            if int(row["YEAR"]) == year and int(row["WEEK"]) == week:
                blocks = ["PEAK", "SHOULDER", "OFFPEAK"]
                total_hours = sum(float(row[b]) for b in blocks)
                if total_hours <= 0:
                    raise ValueError("Pulse week has no modelled hours")
                flat_mw = energy_mwh / total_hours
                return ["IMPULSE_RENEWABLE", node, str(year), str(week)] + [f"{flat_mw:.12f}"] * 3
    raise KeyError(f"No hours_per_block row for {year} week {week}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", default=".jade_impulse")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--node", choices=["NI", "SI", "HAY"], default="NI")
    p.add_argument("--energy-mwh", type=float, default=1000.0)
    args = p.parse_args()

    meta = json.loads(LATEST.read_text(encoding="utf-8"))
    root = Path(args.workdir)
    if root.exists():
        shutil.rmtree(root)
    (root / "Input" / "baseline").mkdir(parents=True)
    (root / "Input" / "pulse").mkdir(parents=True)

    # Preserve the EA Inputs subtree exactly beneath each experiment directory.
    for item in meta["input_files"]:
        rel = relative_from_inputs(item["name"])
        base_dest = root / "Input" / "baseline" / rel
        download(item["source_url"], base_dest)
        pulse_dest = root / "Input" / "pulse" / rel
        pulse_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_dest, pulse_dest)

    hours_rel = relative_from_inputs(find_input(meta, "hours_per_block.csv")["name"])
    fixed_rel = relative_from_inputs(find_input(meta, "fixed_stations.csv")["name"])
    pulse_fixed = root / "Input" / "pulse" / fixed_rel
    row = pulse_row(root / "Input" / "pulse" / hours_rel, args.year, args.week, args.node, args.energy_mwh)
    with pulse_fixed.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)

    # Download the published trained policy into a neutral cache. The Julia runner
    # places a copy beside each model only at runtime.
    policy = root / "published_policy"
    policy.mkdir(parents=True, exist_ok=True)
    for suffix in ["/cuts.json", "/rundata.json"]:
        item = find_output(meta, suffix)
        download(item["source_url"], policy / Path(item["name"]).name)

    manifest = {
        "ea_latest_year": meta["latest_year"],
        "ea_latest_week": meta["latest_week"],
        "pulse_year": args.year,
        "pulse_week": args.week,
        "pulse_node": args.node,
        "pulse_energy_mwh": args.energy_mwh,
        "pulse_fixed_row": row,
        "method": "fixed-policy marginal diagnostic",
        "warning": "Uses the EA published policy for both cases; this is not a retrained optimum for the perturbed system.",
    }
    (root / "experiment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
