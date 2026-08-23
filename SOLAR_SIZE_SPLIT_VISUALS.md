# Solar size-split visuals

This repository renders two public-facing solar charts that split Electricity Authority solar observations into the three model populations used by the distributed-generation work:

- **<25 kW** — small/home-scale systems used for ICP adoption S-curves;
- **25 kW to <1 MW** — larger distributed systems modelled separately from household uptake; and
- **>=1 MW** — utility-scale installations, excluded from the generic distributed-adoption model.

The current chart outputs are:

- `data/visuals/distributed_solar_size_split_capacity.png`
- `data/visuals/distributed_solar_size_split_installs.png`
- `data/visuals/distributed_solar_size_split_plot_data.csv`

## Observed history

The size split is derived from the EA street-level Registry extract by `build_solar_size_buckets.py`. Because the repository only began retaining monthly size-bucket snapshots in July 2026, the observed three-bucket history starts there. The charts should not back-cast earlier size splits from the current fleet.

Each new EA source month appends a snapshot to:

`data/distributed_generation/model/solar_size_bucket_history.csv`

As that history grows, large utility connections should appear as discrete step changes while the <25 kW population should normally progress much more smoothly.

## Twelve-month model extension

`build_solar_size_split_charts.py` extends the latest observed snapshot by twelve months.

For the <25 kW population it shows the same fixed-saturation adoption logic used elsewhere in the model:

- **10% saturation** — lower trajectory;
- **20% saturation** — central/midline trajectory; and
- **30% saturation** — upper trajectory.

The 20% curve is fitted independently through the current measured <25 kW penetration using the observed EA all-solar uptake history, scaled by the currently measured small-system share of solar ICPs.

The 25 kW to <1 MW category uses the existing larger-distributed capacity trajectory. Its current provisional growth assumption is documented in `data/distributed_generation/model/distributed_solar_adoption_scenarios.json` and is capped after 2035 until genuine size-bucket history is long enough to replace the proxy.

The >=1 MW category is **not** extrapolated with a generic growth rate. It is held at the latest observed level in these twelve-month charts until project-timed utility additions are explicitly incorporated. This avoids manufacturing smooth utility-scale growth when real additions occur as irregular large projects.

## Rendering cadence

The chart workflow is `.github/workflows/render_solar_size_split_charts.yml`.

It can be run manually and is also scheduled weekly. Once the workflow exists on the repository default branch, a successful `Daily NZ Grid Data Refresh` also triggers a lightweight render check. The check reads `data/metadata/solar_size_split_chart_state.json` and exits before installing plotting dependencies when the current ISO week has already been rendered.

Manual runs can set `force_render=true` to bypass the weekly gate.

## Monthly model archive

Every render also writes a source-month archive under:

`data/visuals/archive/distributed_solar_size_split/YYYY-MM/`

The archive contains the two PNGs and the exact plot-data CSV used for that source month. Re-renders within the same EA source month replace that month's copy; when a new EA source month arrives, a new archive directory is created.

This preserves a monthly record of how both the observed fleet and the forward model looked at the time, allowing later comparison of forecast drift, changes in fitted adoption rates, revisions in EA data, and the effect of large utility-scale additions.
