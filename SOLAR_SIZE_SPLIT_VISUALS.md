# Solar size-split visuals

This repository renders two public-facing solar charts that separate Electricity Authority solar data into the three populations used by the distributed-generation model:

- **<25 kW** — small/home-scale systems used for ICP adoption S-curves;
- **25 kW to <1 MW** — larger distributed systems modelled separately from household uptake; and
- **>=1 MW** — utility-scale installations, kept separate because individual projects can add very large blocks of capacity at once.

The live outputs are:

- `data/visuals/distributed_solar_size_split_capacity.png`
- `data/visuals/distributed_solar_size_split_installs.png`
- `data/visuals/distributed_solar_size_split_plot_data.csv`
- `data/metadata/solar_size_split_chart_state.json`

The charts are intentionally designed to tell the distributed/home-solar story without allowing a few very large utility projects to visually overwhelm it. They show roughly three years of history and five years of forward modelling on a long horizontal time axis. Monthly stacked bars are deliberately narrow, with connecting lines retained across the cumulative size-bucket boundaries so gradual small-system growth and lumpy utility-scale additions remain visible.

The y-axis always starts at zero.

## Three provenance periods

The plot-data CSV records a `kind` and `provenance` for every point. The chart therefore distinguishes three different kinds of evidence rather than presenting the entire series as observed:

1. **Modeled historical split** — months before the repository began retaining explicit three-way size snapshots;
2. **Observed size split** — retained Electricity Authority street-level Registry snapshots; and
3. **Modeled future** — the forward distributed-solar scenarios.

The first retained explicit three-way snapshot is currently **July 2026**. That date is stored in the render state as `explicit_split_start_month` and is derived from `data/distributed_generation/model/solar_size_bucket_history.csv`, rather than hard-coded into the plotting logic.

## Modeled history before the explicit split

The Electricity Authority national monthly solar series reaches much further back than the retained street-level size-bucket history. Leaving the chart blank before July 2026 would make it poor for reading the longer household/distributed adoption trend, so `build_solar_size_split_charts.py` reconstructs the earlier three-way split while treating the official monthly national totals as hard constraints.

For every modeled historical month:

- the three category ICP estimates sum back to the official EA national solar ICP total;
- the three category capacity estimates sum back to the official EA national installed-solar MW total; and
- reconciliation fields are written into the exact plot-data CSV so this can be checked directly.

### <25 kW history

The historical <25 kW ICP count uses the same proxy already used by the distributed-solar adoption model: official EA all-solar uptake is scaled by the measured <25 kW share of solar ICPs. For the historical reconstruction the share is anchored to the first explicit street-level size-split snapshot so the modeled history joins that observation cleanly.

The modeled historical <25 kW capacity is then the residual after the larger-distributed and lumpy utility-capacity estimates are removed from the official national MW total. This avoids imposing the latest measured small-system average kW retrospectively on earlier years.

### 25 kW to <1 MW history

Before the explicit split, the 25 kW to <1 MW population is back-cast from the first observed bucket snapshot using the same provisional larger-distributed growth rate already selected by the distributed-solar scenario model. The estimate is constrained by the national monthly totals.

This is a working historical allocation, not an Electricity Authority published size series. It should be replaced by genuine retained size-bucket history as that history accumulates.

### >=1 MW history

Utility-scale solar is intentionally not reconstructed as a smooth generic CAGR.

For the historical MW split, the renderer looks for lumpy national capacity additions by comparing each month's observed increase with a rolling 12-month median new-system-size baseline. Capacity additions above that distributed baseline are treated as candidate utility additions. The resulting step series is then scaled or offset so it lands exactly on the first explicit EA street-level `>=1 MW` observation.

This is a modeled allocation intended to keep large project-sized additions visible while preserving the official monthly national MW total. It is not a claim that the inferred step month is an independently verified project commissioning date.

## Observed street-level split

`build_solar_size_buckets.py` derives explicit size buckets from the EA street-level Registry extract and appends each retained source-month snapshot to:

`data/distributed_generation/model/solar_size_bucket_history.csv`

Those retained points are marked `observed` in the chart CSV. The source itself is street-level aggregated data, so the bucket classification remains an estimate at the individual-installation level, but it is a direct calculation from the contemporary Registry extract rather than a historical back-cast.

As the archive grows, the observed portion of the chart should lengthen and progressively replace the modeled historical allocation.

## Five-year model extension

`build_solar_size_split_charts.py` extends the latest observed size snapshot by **60 months**.

### <25 kW future adoption

The <25 kW population uses the same fixed-saturation adoption logic as the existing distributed-solar model:

- **10% saturation** — lower case;
- **20% saturation** — independently fitted midpoint; and
- **30% saturation** — upper case.

The 10% and 30% cases come from the existing distributed-solar scenario output. The 20% curve is fitted independently through the latest measured small-system penetration using observed EA uptake history. It is **not** constructed by averaging the 10% and 30% curves.

The stacked bars use the 20% midpoint as their central future path, while the chart overlays the 10%, 20%, and 30% <25 kW trajectories so the adoption range remains visible in the same S-curve style used by the project's earlier solar adoption charts.

### 25 kW to <1 MW future

The 25 kW to <1 MW category continues to use the existing provisional larger-distributed trajectory from `data/distributed_generation/model/distributed_solar_adoption_scenarios.csv`.

That trajectory remains subject to the existing guardrail applied by `cap_larger_distributed_solar_growth.py`: the selected provisional growth path is used through 2035 and capacity is held flat thereafter until genuine size-bucket history supports a revised trajectory. The current five-year chart horizon remains inside that guardrail, but the renderer deliberately consumes the already-guardrailed scenario series rather than recreating a separate assumption.

### >=1 MW future

The `>=1 MW` category is **not** extrapolated with a generic growth rate.

Until a project-timed future utility-solar series is explicitly incorporated into this chart builder, both the latest observed utility MW total and estimated utility ICP count are held flat through the five-year projection. This makes the absence of a utility-project forecast explicit rather than manufacturing smooth utility growth that real projects will not follow.

## Rendering cadence

The chart workflow is:

`.github/workflows/render_solar_size_split_charts.yml`

It is intentionally separate from the daily data refresh so matplotlib/scipy/numpy setup does not slow the fast source-check workflow.

The workflow behavior is:

- **manual dispatch:** always renders and calls the renderer with `--force`;
- **pushes to the chart renderer/docs/workflow on `main`:** render immediately with `--force`; and
- **scheduled run:** checks the weekly state before installing plotting dependencies and renders only when the current ISO week has not already been completed.

The renderer keeps its own weekly-state check as a second safety layer. This combination avoids the earlier failure mode where a manual workflow could finish successfully without actually producing charts, while also avoiding expensive plotting dependency setup for a scheduled weekly run that is genuinely not due.

Scheduled rendering is currently once per week. Manual and relevant code-change renders may occur additionally when explicitly requested.

## Monthly model archive

Every successful render also writes a source-month archive under:

`data/visuals/archive/distributed_solar_size_split/YYYY-MM/`

Each source month retains:

- `distributed_solar_size_split_capacity.png`
- `distributed_solar_size_split_installs.png`
- `distributed_solar_size_split_plot_data.csv`

The CSV is the exact data used to draw that archived pair of figures, including provenance, official historical totals, reconciliation fields, the independently fitted 20% trajectory, and the 10%/30% scenario bounds.

Re-renders within the same EA source month replace that month's copy. When a new EA source month arrives, a new archive directory is created.

This archive is intended to show how the interpretation and forward model evolve over time: changes in observed size buckets, utility-scale step additions, fitted household adoption, revisions in EA data, and later changes to modelling assumptions can all be compared against the chart that was current for each source month.
