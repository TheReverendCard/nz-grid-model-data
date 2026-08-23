from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

DASHBOARD_URL = "https://www.ea.govt.nz/data-and-insights/charts-and-dashboards/generation-investment-pipeline/"
SNAPSHOT = Path("data/pipeline/ea_generation_investment_pipeline_current.csv")
META = Path("data/metadata/ea_generation_pipeline_source.json")
TRANPOWER_META = Path("data/metadata/connection_pipeline_source.json")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def previous_meta() -> dict[str, object]:
    if not META.exists():
        return {}
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def snapshot_info() -> dict[str, object]:
    if not SNAPSHOT.exists():
        raise RuntimeError(f"Missing required EA pipeline snapshot: {SNAPSHOT}")
    lines = SNAPSHOT.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise RuntimeError(f"EA pipeline snapshot has no data rows: {SNAPSHOT}")
    header = lines[0].split(",")
    first = lines[1].split(",")
    row = dict(zip(header, first))
    return {
        "path": str(SNAPSHOT),
        "snapshot_month": row.get("snapshot_month", ""),
        "captured_date": row.get("captured_date", ""),
        "sha256": sha256_bytes(SNAPSHOT.read_bytes()),
        "rows": len(lines) - 1,
    }


def transpower_info() -> dict[str, object]:
    if not TRANPOWER_META.exists():
        return {"metadata_available": False}
    try:
        meta = json.loads(TRANPOWER_META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"metadata_available": False}
    return {
        "metadata_available": True,
        "source_url": meta.get("source_url", ""),
        "last_modified": meta.get("last_modified", ""),
        "sha256": meta.get("sha256", ""),
        "rows": meta.get("rows", ""),
        "note": "Freshness cross-check only. Transpower connection stages are not substituted for EA investment-pipeline statuses.",
    }


def main() -> None:
    previous = previous_meta()
    response = requests.get(
        DASHBOARD_URL,
        timeout=120,
        headers={"User-Agent": "nz-grid-model-data/1.0 public-data-source-check"},
    )
    response.raise_for_status()
    html = response.content
    page_hash = sha256_bytes(html)

    text = response.text
    embeds = sorted(set(re.findall(r'https://app\.sigmacomputing\.com/embed/[^"\'<> ]+', text)))
    sigma = {"detected": bool(embeds), "urls": embeds[:5]}
    if embeds:
        try:
            embed_response = requests.get(
                embeds[0].replace("&amp;", "&"),
                timeout=120,
                headers={"User-Agent": "nz-grid-model-data/1.0 public-data-source-check"},
            )
            sigma.update({
                "http_status": embed_response.status_code,
                "sha256": sha256_bytes(embed_response.content),
                "content_type": embed_response.headers.get("Content-Type", ""),
            })
        except requests.RequestException as exc:
            sigma["fetch_error"] = str(exc)

    snapshot = snapshot_info()
    prior_page_hash = str(previous.get("ea_dashboard_page_sha256") or "")
    dashboard_changed = bool(prior_page_hash and prior_page_hash != page_hash)

    payload = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Electricity Authority Generation Investment Pipeline",
        "dashboard_url": response.url,
        "ea_dashboard_page_sha256": page_hash,
        "dashboard_changed_since_previous_check": dashboard_changed,
        "sigma_embed": sigma,
        "retained_snapshot": snapshot,
        "transpower_connection_pipeline_cross_check": transpower_info(),
        "status_policy": "Use EA Committed / Actively pursued / Other definitions for chart status. Do not infer those statuses from Transpower connection stages.",
        "freshness_policy": "The monthly workflow checks both the EA dashboard endpoint and the independently updated Transpower connection pipeline before rendering. If the EA dashboard fingerprint changes, review/refresh the retained EA status snapshot rather than silently substituting Transpower stages.",
    }

    META.parent.mkdir(parents=True, exist_ok=True)
    META.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Checked EA generation pipeline dashboard: {response.status_code}")
    print(f"EA snapshot: {snapshot['snapshot_month']} ({snapshot['rows']} rows)")
    if dashboard_changed:
        print("WARNING: EA dashboard page fingerprint changed since the previous check; review the retained EA snapshot.")


if __name__ == "__main__":
    main()
