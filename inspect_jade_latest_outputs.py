from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import requests

LATEST = Path("data/metadata/jade_latest.json")
OUT = Path("data/metadata/jade_latest_output_schemas.json")
DISPATCH_OUT = Path("data/metadata/jade_dispatch_candidates.json")


def fetch_sample(url: str, max_bytes: int = 65536) -> str:
    headers = {"Range": f"bytes=0-{max_bytes - 1}"}
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.content.decode("utf-8-sig", errors="replace")


def classify(columns: list[str], name: str) -> list[str]:
    joined = " ".join(c.lower() for c in columns)
    n = name.lower()
    tags: list[str] = []
    if any(k in joined for k in ["storage", "reservoir", "volume"]) or "storage" in n:
        tags.append("storage")
    if any(k in joined for k in ["generation", "dispatch", "mw", "mwh"]) or any(k in n for k in ["generation", "dispatch"]):
        tags.append("generation")
    if "thermal" in joined or "thermal" in n:
        tags.append("thermal")
    if any(k in joined for k in ["lost load", "lost_load", "unserved", "shortage"]) or "lostload" in n.lower():
        tags.append("lost_load")
    if any(k in joined for k in ["water value", "water_value", "marginal value"]) or "water" in n and "value" in n:
        tags.append("water_value")
    if any(k in joined for k in ["price", "cost", "dual"]) or "cost" in n:
        tags.append("price_or_cost")
    if any(k in joined for k in ["flow", "inflow", "outflow"]) or "flow" in n:
        tags.append("flows")
    return sorted(set(tags))


def dispatch_score(columns: list[str], name: str) -> tuple[int, list[str]]:
    """Rank tables that may contain actual power/energy dispatch rather than costs/flows."""
    n = name.lower()
    cols = [c.strip().lower() for c in columns]
    joined = " ".join(cols)
    reasons: list[str] = []
    score = 0

    if all(period in cols for period in ["peak", "shoulder", "offpeak"]):
        score += 5
        reasons.append("has PEAK/SHOULDER/OFFPEAK dispatch-period columns")
    if any(k in joined for k in ["mw", "mwh", "gwh", "generation", "dispatch", "energy"]):
        score += 5
        reasons.append("schema contains power/energy terminology")
    if any(k in n for k in ["generation", "dispatch", "thermal", "energy", "power"]):
        score += 4
        reasons.append("filename contains generation/dispatch/thermal/energy/power")
    if any(k in n for k in ["thermalcost", "cost.csv", "futurecost", "presentcost", "summedcost", "totalcost", "lostloadcost", "flowlbcost", "flowubcost", "contingentstoragecost"]):
        score -= 8
        reasons.append("cost/penalty table, not physical dispatch")
    if "/naturalflows/" in n or "/spillflows/" in n or "adjusted_inflows" in n:
        score -= 6
        reasons.append("hydrological flow table")
    if "spilledenergy" in n:
        score -= 2
        reasons.append("physical energy but spill rather than generation")

    return score, reasons


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        print(f"Unchanged {path}")
    else:
        path.write_text(text, encoding="utf-8")
        print(f"Wrote {path}")


def main() -> None:
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    rows = []
    for item in latest.get("output_files", []):
        name = item["name"]
        if not name.lower().endswith(".csv"):
            continue
        url = item["source_url"]
        try:
            text = fetch_sample(url)
            reader = csv.reader(io.StringIO(text))
            parsed = list(reader)
            columns = parsed[0] if parsed else []
            sample_rows = parsed[1:4] if len(parsed) > 1 else []
            score, reasons = dispatch_score(columns, name)
            rows.append({
                "name": name,
                "source_url": url,
                "columns": columns,
                "sample_rows": sample_rows,
                "tags": classify(columns, name),
                "dispatch_candidate_score": score,
                "dispatch_candidate_reasons": reasons,
                "content_length": item.get("content_length", ""),
            })
        except Exception as exc:
            rows.append({
                "name": name,
                "source_url": url,
                "columns": [],
                "sample_rows": [],
                "tags": [],
                "dispatch_candidate_score": -99,
                "dispatch_candidate_reasons": ["sample fetch failed"],
                "error": str(exc),
                "content_length": item.get("content_length", ""),
            })

    payload = {
        "source": latest.get("source"),
        "latest_year": latest.get("latest_year"),
        "latest_week": latest.get("latest_week"),
        "csv_output_count": len(rows),
        "outputs": rows,
        "by_tag": {
            tag: [r["name"] for r in rows if tag in r.get("tags", [])]
            for tag in ["storage", "generation", "thermal", "lost_load", "water_value", "price_or_cost", "flows"]
        },
        "note": "Schema/sample inspection only. Raw JADE outputs remain at the Electricity Authority; this file exists to identify chart-relevant upstream tables reproducibly.",
    }
    write_json(OUT, payload)

    ranked = sorted(
        [r for r in rows if r.get("dispatch_candidate_score", -99) > 0],
        key=lambda r: (-r["dispatch_candidate_score"], r["name"]),
    )
    dispatch_payload = {
        "source": latest.get("source"),
        "latest_year": latest.get("latest_year"),
        "latest_week": latest.get("latest_week"),
        "purpose": "Identify authoritative JADE physical generation/dispatch output for thermal GWh/MW visualisation without guessing from filenames.",
        "candidate_count": len(ranked),
        "candidates": [
            {
                "score": r["dispatch_candidate_score"],
                "name": r["name"],
                "source_url": r["source_url"],
                "columns": r["columns"],
                "sample_rows": r["sample_rows"],
                "reasons": r["dispatch_candidate_reasons"],
            }
            for r in ranked
        ],
    }
    write_json(DISPATCH_OUT, dispatch_payload)

    for tag, names in payload["by_tag"].items():
        print(f"{tag}: {len(names)}")
        for name in names[:12]:
            print(f"  {name}")

    print(f"dispatch candidates: {len(ranked)}")
    for row in ranked[:20]:
        print(f"  {row['dispatch_candidate_score']:>2} {row['name']} :: {', '.join(row['dispatch_candidate_reasons'])}")


if __name__ == "__main__":
    main()
