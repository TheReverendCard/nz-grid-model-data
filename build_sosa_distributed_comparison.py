from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt

NZ_WEM = Path("data/sosa/2026/reference_nzwem.csv")
DIST_WEM = Path("data/model/distributed_solar_sosa_winter_energy.csv")
NI_WCM = Path("data/sosa/2026/reference_niwcm.csv")
DIST_WCM = Path("data/model/distributed_battery_sosa_ni_capacity.csv")
OUT_CSV = Path("data/model/sosa_distributed_margin_comparison.csv")
OUT_DIR = Path("data/visuals")

STAGES = {
    "stage1": "Stage 1: existing + committed",
    "stage2": "Stage 2: + consented, likely",
    "stage3": "Stage 3: + likely consent within 2y",
}
SCENARIOS = {
    "sosa_baseline": "SOSA baseline",
    "low_10pct": "10% distributed-solar case",
    "high_30pct": "30% distributed-solar case",
}
CAPACITY_SENSITIVITY = "constrained_operational_capacity_low_wind_solar"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_rows() -> list[dict[str, object]]:
    wem_base = {
        int(r["year"]): r
        for r in read_csv(NZ_WEM)
        if r["sensitivity"] == "reference" and int(r["year"]) >= 2027
    }
    wem_custom = {(int(r["year"]), r["scenario"]): r for r in read_csv(DIST_WEM)}
    wcm_base = {
        int(r["year"]): r
        for r in read_csv(NI_WCM)
        if r["sensitivity"] == CAPACITY_SENSITIVITY and int(r["year"]) >= 2027
    }
    wcm_custom = {
        (int(r["year"]), r["scenario"]): r
        for r in read_csv(DIST_WCM)
        if r["sensitivity"] == CAPACITY_SENSITIVITY
    }

    rows: list[dict[str, object]] = []
    for year in sorted(wem_base):
        for stage, label in STAGES.items():
            base_col = {
                "stage1": "stage1_existing_committed_pct",
                "stage2": "stage2_plus_consented_likely_pct",
                "stage3": "stage3_plus_likely_consent_2y_pct",
            }[stage]
            adjusted_col = {
                "stage1": "stage1_adjusted_nzwem_pct",
                "stage2": "stage2_adjusted_nzwem_pct",
                "stage3": "stage3_adjusted_nzwem_pct",
            }[stage]
            base = float(wem_base[year][base_col])
            rows.append({
                "metric": "nz_winter_energy_margin",
                "sensitivity": "reference",
                "year": year,
                "stage": stage,
                "stage_label": label,
                "scenario": "sosa_baseline",
                "scenario_label": SCENARIOS["sosa_baseline"],
                "margin": round(base, 4),
                "unit": "pct",
                "delta_from_sosa": 0.0,
            })
            for scenario in ("low_10pct", "high_30pct"):
                value = float(wem_custom[(year, scenario)][adjusted_col])
                rows.append({
                    "metric": "nz_winter_energy_margin",
                    "sensitivity": "reference",
                    "year": year,
                    "stage": stage,
                    "stage_label": label,
                    "scenario": scenario,
                    "scenario_label": SCENARIOS[scenario],
                    "margin": round(value, 4),
                    "unit": "pct",
                    "delta_from_sosa": round(value - base, 4),
                })

    for year in sorted(wcm_base):
        for stage, label in STAGES.items():
            base_col = {
                "stage1": "stage1_existing_committed_mw",
                "stage2": "stage2_plus_consented_likely_mw",
                "stage3": "stage3_plus_likely_consent_2y_mw",
            }[stage]
            adjusted_col = {
                "stage1": "stage1_adjusted_niwcm_mw",
                "stage2": "stage2_adjusted_niwcm_mw",
                "stage3": "stage3_adjusted_niwcm_mw",
            }[stage]
            base = float(wcm_base[year][base_col])
            rows.append({
                "metric": "ni_winter_capacity_margin",
                "sensitivity": CAPACITY_SENSITIVITY,
                "year": year,
                "stage": stage,
                "stage_label": label,
                "scenario": "sosa_baseline",
                "scenario_label": SCENARIOS["sosa_baseline"],
                "margin": round(base, 3),
                "unit": "MW",
                "delta_from_sosa": 0.0,
            })
            for scenario in ("low_10pct", "high_30pct"):
                value = float(wcm_custom[(year, scenario)][adjusted_col])
                rows.append({
                    "metric": "ni_winter_capacity_margin",
                    "sensitivity": CAPACITY_SENSITIVITY,
                    "year": year,
                    "stage": stage,
                    "stage_label": label,
                    "scenario": scenario,
                    "scenario_label": SCENARIOS[scenario],
                    "margin": round(value, 3),
                    "unit": "MW",
                    "delta_from_sosa": round(value - base, 3),
                })
    return rows


def write_rows(rows: list[dict[str, object]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def stage2_series(rows: list[dict[str, object]], metric: str):
    selected = [r for r in rows if r["metric"] == metric and r["stage"] == "stage2"]
    years = sorted({int(r["year"]) for r in selected})
    series = {
        scenario: [float(next(r["margin"] for r in selected if int(r["year"]) == y and r["scenario"] == scenario)) for y in years]
        for scenario in SCENARIOS
    }
    return years, series


def energy_chart(rows: list[dict[str, object]]) -> None:
    years, series = stage2_series(rows, "nz_winter_energy_margin")
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for scenario, label in SCENARIOS.items():
        ax.plot(years, series[scenario], marker="o", linewidth=2.2, label=label)
    ax.axhline(0, linewidth=1)
    ax.set_ylabel("NZ winter energy margin (%)")
    ax.set_xlabel("Winter")
    ax.set_title("Stage 2 winter energy margin: SOSA baseline vs distributed solar")
    ax.set_xticks(years)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.text(
        0.01,
        0.01,
        "Stage 2 adds consented projects considered likely to be committed. Custom cases replace only SOSA's embedded domestic solar+battery winter-energy contribution; batteries add no net seasonal GWh.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "sosa_stage2_winter_energy_margin_comparison.png", dpi=180)
    plt.close(fig)


def capacity_chart(rows: list[dict[str, object]]) -> None:
    years, series = stage2_series(rows, "ni_winter_capacity_margin")
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for scenario, label in SCENARIOS.items():
        ax.plot(years, series[scenario], marker="o", linewidth=2.2, label=label)
    ax.axhline(0, linewidth=1)
    ax.set_ylabel("North Island winter capacity margin (MW)")
    ax.set_xlabel("Winter")
    ax.set_title("Stage 2 NI capacity margin under constrained capacity + low wind/solar")
    ax.set_xticks(years)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)

    y2035 = {scenario: series[scenario][-1] for scenario in SCENARIOS}
    ax.annotate(
        f"2035 SOSA: {y2035['sosa_baseline']:.0f} MW\n10% case: {y2035['low_10pct']:.0f} MW\n30% case: {y2035['high_30pct']:.0f} MW",
        xy=(2035, y2035["high_30pct"]),
        xytext=(2031.7, max(series["high_30pct"]) * 0.55),
        arrowprops={"arrowstyle": "->"},
        fontsize=9,
    )
    fig.text(
        0.01,
        0.01,
        "Capacity comparison uses SOSA's constrained-operational-capacity + low-wind/solar sensitivity. Custom battery support uses the EA Solar-with-battery registered connection-power proxy, 60% SOSA BESS peak credit, NI allocation based on SOSA's embedded NI share, and delta-only accounting.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(OUT_DIR / "sosa_stage2_ni_capacity_margin_comparison.png", dpi=180)
    plt.close(fig)


def stress_test_2035_chart(rows: list[dict[str, object]]) -> None:
    selected = [
        r for r in rows
        if r["metric"] == "ni_winter_capacity_margin"
        and int(r["year"]) == 2035
        and r["sensitivity"] == CAPACITY_SENSITIVITY
    ]
    stages = list(STAGES)
    scenarios = list(SCENARIOS)
    x = list(range(len(stages)))
    width = 0.24

    fig, ax = plt.subplots(figsize=(10.5, 6))
    for offset_index, scenario in enumerate(scenarios):
        offset = (offset_index - 1) * width
        values = [
            float(next(r["margin"] for r in selected if r["stage"] == stage and r["scenario"] == scenario))
            for stage in stages
        ]
        bars = ax.bar([v + offset for v in x], values, width=width, label=SCENARIOS[scenario])
        for bar, value in zip(bars, values):
            va = "bottom" if value >= 0 else "top"
            dy = 12 if value >= 0 else -12
            ax.annotate(
                f"{value:.0f}",
                (bar.get_x() + bar.get_width() / 2, value),
                xytext=(0, dy),
                textcoords="offset points",
                ha="center",
                va=va,
                fontsize=9,
            )

    ax.axhline(0, linewidth=1.4)
    ax.set_xticks(x)
    ax.set_xticklabels(["Stage 1\nexisting + committed", "Stage 2\n+ consented likely", "Stage 3\n+ likely consent <2y"])
    ax.set_ylabel("North Island winter capacity margin (MW)")
    ax.set_title("2035 NI capacity stress test: how much pipeline is enough?")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.text(
        0.01,
        0.01,
        "Transpower SOSA constrained-operational-capacity + low-wind/solar sensitivity. Negative values indicate a capacity deficit. Distributed cases are delta adjustments to SOSA, not standalone dispatch-model reruns.",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(OUT_DIR / "sosa_2035_ni_capacity_stress_test.png", dpi=180)
    plt.close(fig)


def main() -> None:
    rows = build_rows()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_rows(rows)
    energy_chart(rows)
    capacity_chart(rows)
    stress_test_2035_chart(rows)
    print(f"Wrote {OUT_CSV} ({len(rows)} rows)")
    print(f"Wrote comparison charts to {OUT_DIR}")


if __name__ == "__main__":
    main()
