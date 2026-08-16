from __future__ import annotations

import json
from pathlib import Path

import update_data as hmd

OUT_DIR = Path("data/hydro/raw/inflows")
META = Path("data/metadata/waitaki_validation_flow_sources.json")
FILES = {
    "SI_PKI_Actual_LakePukaki_Inflow_98614(2).csv": "Actual Pukaki inflow including Tekapo for the separate-Tekapo simulation",
    "SI_OHU_Actual_LakeOhau(ExclDiverted)_Outflow_98614(6).csv": "Actual Lake Ohau outflow excluding diverted flow",
}


def main() -> None:
    blobs = hmd.list_blobs(hmd.HMD_ROOT)
    lookup = hmd.blob_by_basename(blobs)
    flow_index = {row["FileName"]: row for row in hmd.read_index("FileIndex_Flows.csv")}

    records = []
    for filename, purpose in FILES.items():
        if filename not in flow_index:
            raise RuntimeError(f"HMD flow index no longer contains required Waitaki validation series: {filename}")
        blob = lookup.get(filename)
        if blob is None:
            raise RuntimeError(f"Could not uniquely locate HMD validation flow file: {filename}")
        path = OUT_DIR / filename
        rows = hmd.download_file(hmd.blob_url(blob["name"]), path)
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
    print(f"Wrote {META}; fetched {len(records)} Waitaki validation flow series")


if __name__ == "__main__":
    main()
