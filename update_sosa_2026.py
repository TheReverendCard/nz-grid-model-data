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


def load_previous_metadata() -> dict[str, object]:
    if not META_JSON.exists():
        return {}
    try:
        return json.loads(META_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_text_if_changed(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
        return False
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return True


def download() -> tuple[dict[str, str], bool]:
    previous = load_previous_metadata()
    request_headers: dict[str, str] = {}
    if RAW_XLSX.exists():
        etag = str(previous.get("etag") or "")
        last_modified = str(previous.get("last_modified") or "")
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified

    response = requests.get(
        SOURCE_URL,
        headers=request_headers,
        timeout=120,
    )

    if response.status_code == 304 and RAW_XLSX.exists():
        print(f"Unchanged {RAW_XLSX} (HTTP 304)")
        return {
            "url": str(previous.get("url") or SOURCE_URL),
            "etag": str(previous.get("etag") or ""),
            "last_modified": str(previous.get("last_modified") or ""),
            "content_length": str(RAW_XLSX.stat().st_size),
        }, False

    response.raise_for_status()
    RAW_XLSX.parent.mkdir(parents=True, exist_ok=True)
    changed = not RAW_XLSX.exists() or RAW_XLSX.read_bytes() != response.content
    if changed:
        RAW_XLSX.write_bytes(response.content)
        print(f"Wrote {RAW_XLSX} ({len(response.content):,} bytes)")
    else:
        print(f"Unchanged {RAW_XLSX} (byte-identical response)")

    return {
        "url": response.url,
        "etag": response.headers.get("ETag", ""),
        "last_modified": response.headers.get("Last-Modified", ""),
        "content_length": response.headers.get("Content-Length", str(len(response.content))),
    }, changed


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
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.iter(f"{{{NS_PKG_REL}}}Relationship")
    }
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
                value = "".join(
                    t.text or "" for t in inline.iter(f"{{{NS_MAIN}}}t")
                )
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
        rows.append(record)
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise RuntimeError(f"No rows to write for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_if_changed(path, buffer.getvalue())


def normalize_workbook() -> None:
    with zipfile.ZipFile(RAW_XLSX) as z:
        strings = shared_strings(z)
        sheets = sheet_paths(z)
        new_supply = read_sheet(z, sheets["New Supply"], strings)
        results = read_sheet(z, sheets["Results"], strings)

    winter_rows = []
    peak_rows = []
    for row in new_supply:
        if not isinstance(row.get("A"), (int, float)):
            continue
        year = int(float(row["A"]))
        if not 2026 <= year <= 2035:
            continue
        # Workbook columns used by the project's normalized SOSA tables.
        winter_rows.append(
            {
                "year": year,
                "effective_nzwem_demand_gwh": float(row.get("L", 0) or 0),
                "sosa_domestic_solar_battery_gwh": float(row.get("M", 0) or 0),
            }
        )
        peak_rows.append(
            {
                "year": year,
                "ni_peak_demand_mw": float(row.get("N", 0) or 0),
                "si_peak_demand_mw": float(row.get("O", 0) or 0),
                "ni_domestic_solar_battery_mw": float(row.get("P", 0) or 0),
                "si_domestic_solar_battery_mw": float(row.get("Q", 0) or 0),
            }
        )
    write_csv(WINTER_CSV, winter_rows)
    write_csv(PEAK_CSV, peak_rows)

    wem_rows = []
    wem_wanted = {
        "Medium Demand Growth+Ref Gas Supply": "reference",
        "Medium Demand Growth+Ref Gas Supply+Low Wind & Solar": "low_wind_solar",
    }
    for row in results:
        if row.get("B") != "NZWEM" or row.get("C") not in wem_wanted:
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
    headers, workbook_changed = download()
    normalized = [WINTER_CSV, PEAK_CSV, WEM_CSV, NIWCM_CSV]
    if workbook_changed or any(not path.exists() for path in normalized):
        normalize_workbook()
    else:
        print("SOSA workbook unchanged; skipped normalization")

    metadata = {
        "source": "Transpower New Zealand System Operator, 2026 Security of Supply Assessment supplementary data",
        "url": headers["url"],
        "etag": headers["etag"],
        "last_modified": headers["last_modified"],
        "winter_energy_period": "April through September",
        "winter_peak_method": "H100 winter peak demand; medium-demand solar+battery contribution normalized separately for North and South Islands.",
        "normalized_files": [str(path) for path in normalized],
    }
    write_text_if_changed(META_JSON, json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
