from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import requests

LATEST = Path("data/metadata/jade_latest.json")
OUT = Path("data/metadata/jade_run_configuration.json")

INTERESTING_BASENAMES = {
    "run.csv",
    "run.jl",
    "hours_per_block.csv",
    "fixed_stations.csv",
    "hydro_arcs.csv",
    "hydro_stations.csv",
    "reservoirs.csv",
    "reservoir_limits.csv",
    "demand.csv",
    "thermal_stations.csv",
    "outages.csv",
    "transmission_outages.csv",
}


def fetch_text(url: str, max_bytes: int = 131072) -> str:
    headers = {"Range": f"bytes=0-{max_bytes - 1}"}
    response = requests.get(url, headers=headers, timeout=60)
    response.raise_for_status()
    return response.content.decode("utf-8-sig", errors="replace")


def basename(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def csv_preview(text: str, rows: int = 5) -> dict[str, object]:
    parsed = list(csv.reader(io.StringIO(text)))
    return {
        "columns": parsed[0] if parsed else [],
        "sample_rows": parsed[1 : 1 + rows] if len(parsed) > 1 else [],
    }


def main() -> None:
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    candidates = []
    for item in latest.get("input_files", []):
        name = item["name"]
        if basename(name) in INTERESTING_BASENAMES:
            candidates.append(item)

    found = {basename(item["name"]): item for item in candidates}
    payload: dict[str, object] = {
        "source": latest.get("source"),
        "latest_year": latest.get("latest_year"),
        "latest_week": latest.get("latest_week"),
        "purpose": (
            "Capture the Electricity Authority's published JADE run configuration and "
            "input schemas needed to design a reproducible +1 GWh renewable-generation "
            "impulse-response experiment without re-hosting raw model inputs."
        ),
        "files": {},
    }

    files_out: dict[str, object] = {}
    for base in sorted(found):
        item = found[base]
        entry: dict[str, object] = {
            "name": item["name"],
            "source_url": item["source_url"],
            "content_length": item.get("content_length", ""),
        }
        try:
            text = fetch_text(item["source_url"])
            if base.endswith(".csv"):
                entry.update(csv_preview(text))
            elif base.endswith(".jl"):
                entry["text_preview"] = text[:12000]
        except Exception as exc:
            entry["error"] = str(exc)
        files_out[base] = entry

    payload["files"] = files_out
    payload["impulse_experiment_notes"] = {
        "preferred_perturbation_input": "fixed_stations.csv",
        "reason": (
            "JADE treats fixed generation as demand reduction and converts fixed MW to MWh "
            "using the model's hours_per_block.csv durations. A +1 GWh pulse can therefore "
            "be represented without changing demand or hydro constraints."
        ),
        "required_comparison_outputs": [
            "tidy_results.csv: total_storage / reslevel",
            "tidy_results.csv: hydro_disp",
            "tidy_results.csv: thermal_use",
            "tidy_results.csv: spills",
            "tidy_results.csv: prices and mwv (diagnostic)",
        ],
        "method": (
            "Run an otherwise identical baseline and perturbed simulation using the same "
            "policy/cuts, initial state, inflow sequence and random seed; subtract outputs "
            "week by week to measure preserved hydro energy and later thermal displacement."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    if OUT.exists() and OUT.read_text(encoding="utf-8") == text:
        print(f"Unchanged {OUT}")
    else:
        OUT.write_text(text, encoding="utf-8")
        print(f"Wrote {OUT} with {len(files_out)} JADE input/run entries")

    print("JADE impulse-response prerequisites:")
    for base in sorted(files_out):
        print(f"  {base}")


if __name__ == "__main__":
    main()
