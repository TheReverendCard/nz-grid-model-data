from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt

SOSA_WEM = Path("data/sosa/2026/reference_nzwem.csv")
SOSA_DEMAND = Path("data/sosa/2026/medium_demand_winter_energy.csv")
DIST_WEM = Path("data/model/distributed_solar_sosa_winter_energy.csv")
OUT_CSV = Path("data/model/sosa_2035_hydro_energy_equivalence.csv")
OUT_PNG = Path("data/visuals/sosa_2035_hydro_energy_equivalence.png")

YEAR = 2035
STAGES = {
    "stage1": "Stage 1\nexisting + committed",
    "stage2": "Stage 2\n+ consented likely",
    "stage3": "Stage 3\n+ likely consent <2y",
}
SCENARIOS = {
    "sosa_baseline": "SOSA baseline",
    "low_10pct": "10% distributed-solar case",
    "high_30pct": "30% distributed-solar case",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_rows() -> list[dict[str, object]]:
    wem = next(
        row for row in read_csv(SOSA_WEM)
        if int(row["year"]) == YEAR and row["sensitivity"] == "reference"
    )
    demand = next(row for row in read_csv(SOSA_DEMAND) if int(row["year"]) == YEAR)
    effective_demand_gwh = float(demand["effective_nzwem_demand_gwh"])

    margin_pct = {
        "stage1": float(wem["stage1_existing_committed_pct"]),
        "stage2": float(wem["stage2_plus_consented_likely_pct"]),
        "stage3": float(wem["stage3_plus_likely_consent_2y_pct"]),
    }
    stage1_margin = margin_pct["stage1"]
    pipeline_increment = {
        stage: effective_demand_gwh * (margin_pct[stage] - stage1_margin) / 100.0
        for stage in STAGES
    }

    dist_rows = {
        row["scenario"]: row
        for row in read_csv(DIST_WEM)
        if int(row["year"]) == YEAR
    }
    distributed_increment = {
        "sosa_baseline": 0.0,
        "low_10pct": float(dist_rows["low_10pct"]["difference_gwh"]),
        "high_30pct": float(dist_rows["high_30pct"]["difference_gwh"]),
    }

    rows: list[dict[str, object]] = []
    for stage in STAGES:
        for scenario in SCENARIOS:
            pipeline = pipeline_increment[stage]
            distributed = distributed_increment[scenario]
            rows.append(
                {
                    "year": YEAR,
                    "stage": stage,
                    "stage_label": STAGES[stage].replace("\n", " "),
                    "scenario": scenario,
                    "scenario_label": SCENARIOS[scenario],
                    "effective_nzwem_demand_gwh": round(effective_demand_gwh, 3),
                    "sosa_pipeline_winter_energy_increment_vs_stage1_gwh": round(pipeline, 3),
                    "additional_distributed_solar_winter_energy_vs_sosa_gwh": round(distributed, 3),
                    "total_additional_winter_energy_vs_sosa_stage1_gwh": round(pipeline + distributed, 3),
                }
            )
    return rows


def write_rows(rows: list[dict[str, object]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def render_chart(rows: list[dict[str, object]]) -> None:
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    stages = list(STAGES)
    scenarios = list(SCENARIOS)
    x = list(range(len(stages)))
    width = 0.24

    fig, ax = plt.subplots(figsize=(10.5, 6.3))
    for scenario_index, scenario in enumerate(scenarios):
        offset = (scenario_index - 1) * width
        stage_rows = {
            row["stage"]: row
            for row in rows
            if row["scenario"] == scenario
        }
        pipeline = [
            float(stage_rows[stage]["sosa_pipeline_winter_energy_increment_vs_stage1_gwh"])
            for stage in stages
        ]
        distributed = [
            float(stage_rows[stage]["additional_distributed_solar_winter_energy_vs_sosa_gwh"])
            for stage in stages
        ]
        positions = [value + offset for value in x]
        bars = ax.bar(positions, pipeline, width=width, label=SCENARIOS[scenario])
        if scenario != "sosa_baseline":
            ax.bar(positions, distributed, width=width, bottom=pipeline, alpha=0.45)

        totals = [p + d for p, d in zip(pipeline, distributed)]
        for bar, total in zip(bars, totals):
            ax.annotate(
                f"{total / 1000:.1f} TWh" if total >= 1000 else f"{total:.0f} GWh",
                (bar.get_x() + bar.get_width() / 2, total),
                xytext=(0, 6),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=8.5,
            )

    ax.set_xticks(x)
    ax.set_xticklabels([STAGES[stage] for stage in stages])
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Additional Apr-Sep winter energy vs SOSA Stage 1 (GWh)")
    ax.set_title("2035 winter generation available to preserve hydro or displace thermal")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)

    fig.text(
        0.01,
        0.01,
        "Energy-equivalence view, not a one-for-one hydro-storage forecast. SOSA pipeline GWh is derived from NZ winter-energy-margin differences at the same effective demand. The translucent addition is modeled distributed-solar winter generation above SOSA's embedded domestic solar+battery contribution. Actual dispatch can divide this energy between hydro preservation, thermal displacement, inter-island flows and constraints. Batteries add no net seasonal GWh here; their peak value is shown in the capacity analysis. Summer/autumn generation that could improve storage entering winter is excluded.",
        fontsize=7.7,
        wrap=True,
    )
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(OUT_PNG, dpi=180)
    plt.close(fig)


def main() -> None:
    rows = build_rows()
    write_rows(rows)
    render_chart(rows)
    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")
    print(f"Wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
