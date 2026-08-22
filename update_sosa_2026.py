from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path

import requests

OUT_DIR = Path("data/sosa/2026")
RAW_XLSX = OUT_DIR / "2026_sosa_final_supplementary_data.xlsx"
WINTER_CSV = OUT_DIR / "medium_demand_winter_energy.csv"
PEAK_CSV = OUT_DIR / "medium_demand_winter_peak.csv"
WEM_CSV = OUT_DIR / "reference_nzwem.csv"
NIWCM_CSV = OUT_DIR / "reference_niwcm.csv"
META_JSON = OUT_DIR / "sources.json"

SOURCE_URL = (
    "https://static.transpower.co.nz/public/bulk-upload/documents/"
    "2026%20SOSA%20-%20Final%20Supplementary%20Data%20-%20Final%20Version.xlsx"
)

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def col_letters(ref: str) -> str:
    match = re.match(r"([A-Z]+)", ref)
    if not match:
        raise ValueError(ref)
    return match.group(1)


def download() -> dict[str, str]:
    response = requests.get(SOURCE_URL, timeout=120)
    response.raise_for_status()
    RAW_XLSX.parent.mkdir(parents=True, exist_ok=True)
    if not RAW_XLSX.exists() or RAW_XLSX.read_bytes() != response.content:
        RAW_XLSX.write_bytes(response.content)
        print(f"Wrote {RAW_XLSX} ({len(response.content):,} bytes)")
    else:
        print(f"Unchanged {RAW_XLSX}")
    return {
        "url": response.url,
        "etag": response.headers.get("ETag", ""),
        "last_modified": response.headers.get("Last-Modified", ""),
        "content_length": response.headers.get("Content-Length", str(len(response.content))),
    }


def shared_strings(z: zipfile.ZipFile) -> list[str]:
    import xml.etree.ElementTree as ET

    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{{{NS_MAIN}}}t")) for si in root]


def sheet_paths(z: zipfile.ZipFile) -> dict[str, str]:
    import xml.etree.ElementTree as ET

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.iter(f"{{{NS_PKG_REL}}}Relationship")}
    out = {}
    for sheet in wb.iter(f"{{{NS_MAIN}}}sheet"):
        rid = sheet.attrib[f"{{{NS_REL}}}id"]
        target = rel_map[rid].lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        out[sheet.attrib["name"]] = target
    return out


def read_sheet(z: zipfile.ZipFile, path: str, strings: list[str]) -> list[dict[str, object]]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(z.read(path))
    rows: list[dict[str, object]] = []
    for row in root.iter(f"{{{NS_MAIN}}}row"):
        record: dict[str, object] = {}
        for cell in row.findall(f"{{{NS_MAIN}}}c"):
            ref = cell.attrib.get("r", "")
            col = col_letters(ref)
            typ = cell.attrib.get("t")
            value_node = cell.find(f"{{{NS_MAIN}}}v")
            inline = cell.find(f"{{{NS_MAIN}}}is")
            value: object = ""
            if typ == "inlineStr" and inline is not None:
                value = "".join(t.text or "" for t in inline.iter(f"{{{NS_MAIN}}}t"))
            elif value_node is not None:
                raw = value_node.text or ""
                if typ == "s":
                    value = strings[int(raw)]
                else:
                    try:
                        value = float(raw)
                    except ValueError:
                        value = raw
            record[col] = value
        if record:
            rows.append(record)
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise RuntimeError(f"No rows produced for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {path} ({len(rows)} rows)")


def normalize_workbook() -> None:
    with zipfile.ZipFile(RAW_XLSX) as z:
        strings = shared_strings(z)
        paths = sheet_paths(z)
        winter = read_sheet(z, paths["Winter energy demand"], strings)
        peak = read_sheet(z, paths["Winter peak demand (H100)"], strings)
        results = read_sheet(z, paths["Results data"], strings)

    winter_rows = []
    for row in winter:
        if row.get("B") != "Medium Demand" or row.get("C") != "NZ_WEM":
            continue
        year = int(float(row["A"]))
        if not 2026 <= year <= 2035:
            continue
        solar_battery_gwh = float(row.get("E", 0.0)) + float(row.get("K", 0.0))
        demand_excl_dr_gwh = float(row.get("I", 0.0)) + float(row.get("O", 0.0))
        effective_demand_gwh = min(0.98 * demand_excl_dr_gwh, demand_excl_dr_gwh - 335.0)
        winter_rows.append(
            {
                "year": year,
                "sosa_domestic_solar_battery_gwh": round(solar_battery_gwh, 6),
                "demand_excl_demand_response_gwh": round(demand_excl_dr_gwh, 6),
                "effective_nzwem_demand_gwh": round(effective_demand_gwh, 6),
            }
        )
    write_csv(WINTER_CSV, winter_rows)

    peak_rows = []
    for row in peak:
        if row.get("B") != "Medium Demand" or row.get("C") != "NI-WCM":
            continue
        year = int(float(row["A"]))
        if not 2026 <= year <= 2035:
            continue
        peak_rows.append(
            {
                "year": year,
                "si_solar_battery_peak_contribution_mw": round(float(row.get("E", 0.0)), 6),
                "ni_solar_battery_peak_contribution_mw": round(float(row.get("K", 0.0)), 6),
                "nz_solar_battery_peak_contribution_mw": round(float(row.get("E", 0.0)) + float(row.get("K", 0.0)), 6),
                "si_peak_demand_excl_demand_response_mw": round(float(row.get("I", 0.0)), 6),
                "ni_peak_demand_excl_demand_response_mw": round(float(row.get("O", 0.0)), 6),
            }
        )
    write_csv(PEAK_CSV, peak_rows)

    wem_rows = []
    wem_wanted = {
        "Medium Demand Growth+Ref Gas Supply": "reference",
        "Medium Demand Growth+Ref Gas Supply+Low Wind & Solar": "low_wind_solar",
    }
    for row in results:
        if row.get("B") != "NZWEM_PERC" or row.get("C") not in wem_wanted:
            continue
        year = int(float(row["A"]))
        if not 2026 <= year <= 2035:
            continue
        wem_rows.append(
            {
                "year": year,
                "sensitivity": wem_wanted[str(row["C"])],
                "stage1_existing_committed_pct": float(row["D"]),
                "stage2_plus_consented_likely_pct": float(row["E"]),
                "stage3_plus_likely_consent_2y_pct": float(row["F"]),
            }
        )
    write_csv(WEM_CSV, wem_rows)

    niwcm_rows = []
    niwcm_wanted = {
        "Medium Demand Growth+Ref Gas Supply": "reference",
        "Medium Demand Growth+Ref Gas Supply+Constrained Operational Capacity+Low Wind & Solar": "constrained_operational_capacity_low_wind_solar",
    }
    for row in results:
        if row.get("B") != "NIWCM" or row.get("C") not in niwcm_wanted:
            continue
        year = int(float(row["A"]))
        if not 2026 <= year <= 2035:
            continue
        niwcm_rows.append(
            {
                "year": year,
                "sensitivity": niwcm_wanted[str(row["C"])],
                "stage1_existing_committed_mw": float(row["D"]),
                "stage2_plus_consented_likely_mw": float(row["E"]),
                "stage3_plus_likely_consent_2y_mw": float(row["F"]),
            }
        )
    write_csv(NIWCM_CSV, niwcm_rows)


def main() -> None:
    headers = download()
    normalize_workbook()
    META_JSON.write_text(
        json.dumps(
            {
                "source": "Transpower New Zealand System Operator, 2026 Security of Supply Assessment supplementary data",
                "url": headers["url"],
                "etag": headers["etag"],
                "last_modified": headers["last_modified"],
                "winter_energy_period": "April through September",
                "winter_peak_method": "H100 winter peak demand; medium-demand solar+battery contribution normalized separately for North and South Islands.",
                "normalized_files": [str(WINTER_CSV), str(PEAK_CSV), str(WEM_CSV), str(NIWCM_CSV)],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {META_JSON}")


if __name__ == "__main__":
    main()
