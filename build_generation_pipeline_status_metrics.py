from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = Path("data/pipeline/history")
HISTORY_GLOB = "ea_generation_investment_pipeline_*.csv"
STATUS_HISTORY_CSV = Path("data/pipeline/ea_generation_pipeline_status_history.csv")
TRANSITIONS_CSV = Path("data/pipeline/ea_generation_pipeline_status_transitions.csv")
SUMMARY_JSON = Path("data/metadata/ea_generation_pipeline_status_metrics.json")

STATUS_ORDER = ["Other / early-stage", "Actively pursued", "Committed"]
STATUS_RANK = {status: i for i, status in enumerate(STATUS_ORDER)}
ID_CANDIDATES = ("project_id", "project_name", "project", "name")

# Exact project-level history can become useful fairly quickly once a stable project
# identifier is available. Aggregate snapshots need a longer history because net
# movements can mix genuine status changes with additions, withdrawals and re-dating.
MIN_EXACT_SNAPSHOTS = 6
MIN_EXACT_SPAN_MONTHS = 5
MIN_EXACT_PROJECTS = 10
MIN_EXACT_MW = 100.0
MIN_AGGREGATE_SNAPSHOTS = 12
MIN_AGGREGATE_SPAN_MONTHS = 11
MIN_AGGREGATE_PROMOTION_MW = 100.0


def read_snapshot(path: Path) -> dict:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not rows:
        raise RuntimeError(f"No rows in pipeline snapshot: {path}")
    month = str(rows[0].get("snapshot_month") or path.stem.rsplit("_", 1)[-1]).strip()
    captured = str(rows[0].get("captured_date") or "").strip()
    for row in rows:
        if str(row.get("snapshot_month") or month).strip() != month:
            raise RuntimeError(f"Mixed snapshot_month values in {path}")
    return {
        "path": path,
        "month": month,
        "captured_date": captured,
        "fields": fields,
        "rows": rows,
    }


def month_index(month: str) -> int:
    year, mon = month.split("-")[:2]
    return int(year) * 12 + int(mon)


def snapshot_span_months(snapshots: list[dict]) -> int:
    if len(snapshots) < 2:
        return 0
    return month_index(snapshots[-1]["month"]) - month_index(snapshots[0]["month"])


def capacity(row: dict[str, str]) -> float:
    try:
        return float(row.get("capacity_mw") or 0.0)
    except ValueError:
        return 0.0


def status_totals(snapshot: dict) -> dict[tuple[str, str], float]:
    totals = defaultdict(float)
    for row in snapshot["rows"]:
        status = str(row.get("status") or "").strip()
        tech = str(row.get("technology") or "Unknown").strip() or "Unknown"
        if status in STATUS_RANK:
            totals[(tech, status)] += capacity(row)
    return dict(totals)


def timing_totals(snapshot: dict) -> dict[str, float]:
    totals = defaultdict(float)
    for row in snapshot["rows"]:
        status = str(row.get("status") or "").strip()
        if status not in STATUS_RANK:
            continue
        timing = str(row.get("timing") or "").strip().lower()
        year = str(row.get("expected_commissioning_year") or "").strip().lower()
        bucket = "unknown_date" if timing == "unknown" or year == "unknown" else "known_date"
        totals[bucket] += capacity(row)
    return dict(totals)


def detect_stable_id_column(snapshots: list[dict]) -> str | None:
    if not snapshots:
        return None
    for candidate in ID_CANDIDATES:
        if not all(candidate in snap["fields"] for snap in snapshots):
            continue
        valid = True
        for snap in snapshots:
            ids = [str(row.get(candidate) or "").strip() for row in snap["rows"]]
            if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
                valid = False
                break
        if valid:
            return candidate
    return None


def aggregate_history_rows(snapshots: list[dict]) -> list[dict]:
    output = []
    for snap in snapshots:
        totals = status_totals(snap)
        row_counts = defaultdict(int)
        for row in snap["rows"]:
            status = str(row.get("status") or "").strip()
            tech = str(row.get("technology") or "Unknown").strip() or "Unknown"
            if status in STATUS_RANK:
                row_counts[(tech, status)] += 1
        for (tech, status), mw in sorted(totals.items()):
            output.append(
                {
                    "snapshot_month": snap["month"],
                    "captured_date": snap["captured_date"],
                    "technology": tech,
                    "status": status,
                    "capacity_mw": round(mw, 6),
                    "source_rows": row_counts[(tech, status)],
                }
            )
    return output


def aggregate_transition_rows(snapshots: list[dict]) -> list[dict]:
    """Build conservative status-flow proxies from aggregate snapshots.

    Current EA snapshots in this repository do not contain project identities. We
    therefore avoid pretending that a decline in one status is definitely the same
    project as a rise in another. For each technology, this method only matches the
    smaller of simultaneous net lower-confidence exits and net committed gains.
    """
    output = []
    for previous, current in zip(snapshots, snapshots[1:]):
        prev = status_totals(previous)
        curr = status_totals(current)
        techs = sorted({tech for tech, _ in prev} | {tech for tech, _ in curr})
        for tech in techs:
            po = prev.get((tech, "Other / early-stage"), 0.0)
            pa = prev.get((tech, "Actively pursued"), 0.0)
            pc = prev.get((tech, "Committed"), 0.0)
            co = curr.get((tech, "Other / early-stage"), 0.0)
            ca = curr.get((tech, "Actively pursued"), 0.0)
            cc = curr.get((tech, "Committed"), 0.0)

            early_loss = max(po - co, 0.0)
            higher_gain = max((ca + cc) - (pa + pc), 0.0)
            matched_early = min(early_loss, higher_gain)

            prev_lower = po + pa
            curr_lower = co + ca
            lower_loss = max(prev_lower - curr_lower, 0.0)
            committed_gain = max(cc - pc, 0.0)
            matched_committed = min(lower_loss, committed_gain)

            output.append(
                {
                    "from_snapshot": previous["month"],
                    "to_snapshot": current["month"],
                    "technology": tech,
                    "previous_other_mw": round(po, 6),
                    "current_other_mw": round(co, 6),
                    "previous_actively_pursued_mw": round(pa, 6),
                    "current_actively_pursued_mw": round(ca, 6),
                    "previous_committed_mw": round(pc, 6),
                    "current_committed_mw": round(cc, 6),
                    "previous_lower_confidence_mw": round(prev_lower, 6),
                    "net_lower_confidence_exit_mw": round(lower_loss, 6),
                    "net_committed_gain_mw": round(committed_gain, 6),
                    "matched_other_to_higher_proxy_mw": round(matched_early, 6),
                    "matched_to_committed_proxy_mw": round(matched_committed, 6),
                    "committed_promotion_proxy_pct_of_prior_lower": (
                        round(100.0 * matched_committed / prev_lower, 6)
                        if prev_lower > 0
                        else 0.0
                    ),
                }
            )
    return output


def exact_project_metrics(snapshots: list[dict], id_column: str) -> dict:
    tracks = defaultdict(list)
    for snap in snapshots:
        for row in snap["rows"]:
            status = str(row.get("status") or "").strip()
            if status not in STATUS_RANK:
                continue
            project_id = str(row.get(id_column) or "").strip()
            tracks[project_id].append(
                {
                    "month": snap["month"],
                    "status": status,
                    "capacity_mw": capacity(row),
                }
            )

    eligible = []
    converted = []
    for project_id, observations in tracks.items():
        observations.sort(key=lambda item: month_index(item["month"]))
        first = observations[0]
        if first["status"] != "Other / early-stage":
            continue
        record = {
            "project_id": project_id,
            "first_month": first["month"],
            "first_capacity_mw": first["capacity_mw"],
            "latest_month": observations[-1]["month"],
            "latest_status": observations[-1]["status"],
            "ever_committed": any(
                item["status"] == "Committed" for item in observations[1:]
            ),
        }
        eligible.append(record)
        if record["ever_committed"]:
            converted.append(record)

    denominator_mw = sum(item["first_capacity_mw"] for item in eligible)
    converted_mw = sum(item["first_capacity_mw"] for item in converted)
    count_pct = 100.0 * len(converted) / len(eligible) if eligible else 0.0
    capacity_pct = 100.0 * converted_mw / denominator_mw if denominator_mw else 0.0

    span = snapshot_span_months(snapshots)
    ready = (
        len(snapshots) >= MIN_EXACT_SNAPSHOTS
        and span >= MIN_EXACT_SPAN_MONTHS
        and len(eligible) >= MIN_EXACT_PROJECTS
        and denominator_mw >= MIN_EXACT_MW
        and len(converted) >= 1
    )
    reasons = []
    if len(snapshots) < MIN_EXACT_SNAPSHOTS:
        reasons.append(f"need at least {MIN_EXACT_SNAPSHOTS} snapshots")
    if span < MIN_EXACT_SPAN_MONTHS:
        reasons.append(f"need at least {MIN_EXACT_SPAN_MONTHS} months of history")
    if len(eligible) < MIN_EXACT_PROJECTS:
        reasons.append(
            f"need at least {MIN_EXACT_PROJECTS} projects first observed as Other / early-stage"
        )
    if denominator_mw < MIN_EXACT_MW:
        reasons.append(
            f"need at least {MIN_EXACT_MW:g} MW of early-stage project exposure"
        )
    if not converted:
        reasons.append("no early-stage project has yet been observed reaching Committed")

    return {
        "mode": "exact_project_identity",
        "id_column": id_column,
        "eligible_projects": len(eligible),
        "eligible_capacity_mw": round(denominator_mw, 6),
        "converted_projects": len(converted),
        "converted_capacity_mw": round(converted_mw, 6),
        "converted_project_count_pct": round(count_pct, 6),
        "converted_capacity_pct": round(capacity_pct, 6),
        "display_ready": ready,
        "display_pct": round(capacity_pct, 1) if ready else None,
        "display_label": (
            f"{capacity_pct:.1f}% of project MW first observed as Other / early-stage has reached Committed"
            if ready
            else None
        ),
        "not_ready_reasons": reasons,
    }


def aggregate_proxy_metrics(snapshots: list[dict], transitions: list[dict]) -> dict:
    matched = sum(float(row["matched_to_committed_proxy_mw"]) for row in transitions)
    exposure = sum(float(row["previous_lower_confidence_mw"]) for row in transitions)
    detected_intervals = sum(
        1 for row in transitions if float(row["matched_to_committed_proxy_mw"]) > 0
    )
    interval_rate = 100.0 * matched / exposure if exposure else 0.0
    span = snapshot_span_months(snapshots)
    annotation_ready = (
        len(snapshots) >= MIN_AGGREGATE_SNAPSHOTS
        and span >= MIN_AGGREGATE_SPAN_MONTHS
        and matched >= MIN_AGGREGATE_PROMOTION_MW
        and detected_intervals >= 2
    )
    reasons = []
    if len(snapshots) < MIN_AGGREGATE_SNAPSHOTS:
        reasons.append(f"need at least {MIN_AGGREGATE_SNAPSHOTS} snapshots")
    if span < MIN_AGGREGATE_SPAN_MONTHS:
        reasons.append(f"need at least {MIN_AGGREGATE_SPAN_MONTHS} months of history")
    if matched < MIN_AGGREGATE_PROMOTION_MW:
        reasons.append(
            f"need at least {MIN_AGGREGATE_PROMOTION_MW:g} MW of conservatively matched net promotion"
        )
    if detected_intervals < 2:
        reasons.append("need promotion signals in at least two snapshot intervals")

    return {
        "mode": "aggregate_net_flow_proxy",
        "matched_to_committed_proxy_mw": round(matched, 6),
        "lower_confidence_exposure_mw_intervals": round(exposure, 6),
        "promotion_proxy_pct_per_snapshot_interval": round(interval_rate, 6),
        "intervals_with_detected_promotion": detected_intervals,
        "annotation_ready": annotation_ready,
        "annotation_pct": round(interval_rate, 2) if annotation_ready else None,
        "annotation_label": (
            f"{interval_rate:.2f}% net promotion proxy per monthly snapshot interval"
            if annotation_ready
            else None
        ),
        # Aggregate snapshots cannot support a true eventual project-conversion
        # probability. Do not turn this proxy into a 'likely pipeline' layer.
        "projection_ready": False,
        "not_ready_reasons": reasons,
        "caveat": (
            "Current EA snapshots are aggregated rather than project-identified. This proxy only matches "
            "net lower-confidence capacity exits with simultaneous net committed gains within the same "
            "technology; it is conservative but cannot prove that the same projects changed status."
        ),
    }


def current_status_summary(snapshot: dict) -> dict:
    totals = status_totals(snapshot)
    by_status = defaultdict(float)
    by_tech = defaultdict(float)
    for (tech, status), mw in totals.items():
        by_status[status] += mw
        by_tech[tech] += mw
    other = by_status.get("Other / early-stage", 0.0)
    active = by_status.get("Actively pursued", 0.0)
    committed = by_status.get("Committed", 0.0)
    timing = timing_totals(snapshot)
    return {
        "by_status_mw": {
            status: round(by_status.get(status, 0.0), 6) for status in STATUS_ORDER
        },
        "by_technology_mw": {
            tech: round(mw, 6) for tech, mw in sorted(by_tech.items())
        },
        "known_date_mw": round(timing.get("known_date", 0.0), 6),
        "unknown_date_mw": round(timing.get("unknown_date", 0.0), 6),
        "committed_as_pct_of_other_capacity": (
            round(100.0 * committed / other, 6) if other else None
        ),
        "committed_as_pct_of_lower_confidence_capacity": (
            round(100.0 * committed / (other + active), 6)
            if other + active
            else None
        ),
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    paths = sorted(HISTORY_DIR.glob(HISTORY_GLOB))
    if not paths:
        raise RuntimeError(f"No monthly EA pipeline snapshots found under {HISTORY_DIR}")
    snapshots = [read_snapshot(path) for path in paths]
    snapshots.sort(key=lambda snap: month_index(snap["month"]))

    history_rows = aggregate_history_rows(snapshots)
    transition_rows = aggregate_transition_rows(snapshots)
    id_column = detect_stable_id_column(snapshots)

    if id_column:
        empirical = exact_project_metrics(snapshots, id_column)
    else:
        empirical = aggregate_proxy_metrics(snapshots, transition_rows)

    current = current_status_summary(snapshots[-1])
    summary = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Monthly retained Electricity Authority Generation Investment Pipeline snapshots",
        "first_snapshot_month": snapshots[0]["month"],
        "latest_snapshot_month": snapshots[-1]["month"],
        "snapshot_count": len(snapshots),
        "observation_span_months": snapshot_span_months(snapshots),
        "stable_project_id_column": id_column,
        "current": current,
        "empirical_conversion": empirical,
        "display_policy": {
            "exact_project_conversion": (
                "When stable project identifiers are available, show the capacity-weighted share of projects "
                "first observed as Other / early-stage that are later observed as Committed, after minimum "
                "history/sample thresholds are met."
            ),
            "aggregate_fallback": (
                "With aggregate-only snapshots, calculate and retain a conservative net status-flow proxy. "
                "It may be annotated after at least 12 monthly snapshots, but it must not be presented as an "
                "eventual project completion probability or used as a 'likely pipeline' layer."
            ),
        },
    }

    history_fields = [
        "snapshot_month",
        "captured_date",
        "technology",
        "status",
        "capacity_mw",
        "source_rows",
    ]
    transition_fields = [
        "from_snapshot",
        "to_snapshot",
        "technology",
        "previous_other_mw",
        "current_other_mw",
        "previous_actively_pursued_mw",
        "current_actively_pursued_mw",
        "previous_committed_mw",
        "current_committed_mw",
        "previous_lower_confidence_mw",
        "net_lower_confidence_exit_mw",
        "net_committed_gain_mw",
        "matched_other_to_higher_proxy_mw",
        "matched_to_committed_proxy_mw",
        "committed_promotion_proxy_pct_of_prior_lower",
    ]
    write_csv(STATUS_HISTORY_CSV, history_rows, history_fields)
    write_csv(TRANSITIONS_CSV, transition_rows, transition_fields)
    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(
        f"Wrote status tracker from {len(snapshots)} snapshot(s), "
        f"{snapshots[0]['month']} to {snapshots[-1]['month']}; mode={empirical['mode']}"
    )
    if empirical.get("display_ready"):
        print(f"Exact conversion display ready: {empirical['display_label']}")
    elif empirical.get("annotation_ready"):
        print(f"Aggregate proxy annotation ready: {empirical['annotation_label']}")
    else:
        print("Conversion display not yet mature; continuing to accumulate monthly history.")


if __name__ == "__main__":
    main()
