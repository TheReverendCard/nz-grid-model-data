from __future__ import annotations

import json
from pathlib import Path

import update_data as hmd

FLOW_OUT_DIR = Path("data/hydro/raw/inflows")
SPILL_OUT_DIR = Path("data/hydro/raw/spill")
META = Path("data/metadata/waitaki_validation_flow_sources.json")

FLOW_FILES = {
    "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv": "Actual Pukaki inflow including Tekapo for the separate-Tekapo simulation",
    "SI_OHU_Actual_LakeOhau(ExclDiverted)_Outflow_98614(6).csv": "Actual Lake Ohau outflow excluding diverted flow",
}
SPILL_FILES = {
    "SI_RTH_Spill_LakeRuataniwha.csv": "Ruataniwha spill bypassing Ohau B and C to Lake Benmore",
}


def fetch_one(blobs, lookup, filename: str, output_dir: Path) -> tuple[dict[str, str], int, Path]:
    blob = lookup.get(filename)
    if blob is None:
        raise RuntimeError(f"Could not uniquely locate HMD validation series: {filename}")
    path = output_dir / filename
    rows = hmd.download_file(hmd.blob_url(blob["name"]), path)
    return blob, rows, path


def main() -> None:
    blobs = hmd.list_blobs(hmd.HMD_ROOT)
    lookup = hmd.blob_by_basename(blobs)
    flow_index = {row["FileName"]: row for row in hmd.read_index("FileIndex_Flows.csv")}
    spill_index = {row["FileName"]: row for row in hmd.read_index("FileIndex_Spill.csv")}

    records = []
    for filename, purpose in FLOW_FILES.items():
        if filename not in flow_index:
            raise RuntimeError(f"HMD flow index no longer contains required Waitaki validation series: {filename}")
        blob, rows, path = fetch_one(blobs, lookup, filename, FLOW_OUT_DIR)
        meta = flow_index[filename]
        records.append(
            hmd.source_record(
                blob,
                path,
                rows,
                series_type="waitaki_validation_flow",
                purpose=purpose,
                site_code=meta.get("SiteCode", ""),
                direction=meta.get("Direction", ""),
                flow_type=meta.get("FlowType", ""),
                description=meta.get("Description", ""),
            )
        )

    for filename, purpose in SPILL_FILES.items():
        if filename not in spill_index:
            raise RuntimeError(f"HMD spill index no longer contains required Waitaki validation series: {filename}")
        blob, rows, path = fetch_one(blobs, lookup, filename, SPILL_OUT_DIR)
        meta = spill_index[filename]
        records.append(
            hmd.source_record(
                blob,
                path,
                rows,
                series_type="waitaki_validation_spill",
                purpose=purpose,
                site_code=meta.get("SiteCode", ""),
                description=meta.get("Description", ""),
            )
        )

    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(
        json.dumps(
            {
                "source": "New Zealand Electricity Authority Hydrological Modelling Dataset",
                "purpose": "Observed-series validation of explicit Waitaki routing; not forecasting assumptions.",
                "series": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {META}; fetched {len(records)} Waitaki validation series")


if __name__ == "__main__":
    main()
