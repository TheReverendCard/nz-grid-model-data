# First visual story

The first public release should tell a seasonal story rather than expose model mechanics.

## Chart 1 — Hydro storage through the year

**Question:** Does the future renewable fleet leave more water in storage before and during winter?

- x-axis: week of year
- y-axis: national controllable hydro storage, preferably GWh-equivalent and optionally % of usable range
- observed line: recent historical year
- comparison lines: future fleet under selected historical inflow sequences
- contextual band: historical storage distribution/range where defensible
- annotations: minimum storage date, winter entry storage, selected dry-period events

Preferred professional source: JADE / HSS outputs where the comparison requires dispatch behaviour. Observed storage may come from EA/HMD.

## Chart 2 — What fills demand?

**Question:** Which resources are supplying electricity when hydro would otherwise be drawn down?

- x-axis: week or month
- y-axis: average generation / energy
- stacked areas: geothermal, wind, solar, hydro, battery discharge, thermal
- overlay: demand
- optional hatched area: curtailment or surplus renewable production

Present baseline and future cases as matched panels with identical scales.

## Chart 3 — Hydro preserved by additional renewables

**Question:** Do wind and solar merely change annual generation shares, or do they preserve hydro for later high-value periods?

- x-axis: week or month
- y-axis: difference in hydro generation or storage versus baseline
- positive values: hydro preserved / storage higher than baseline
- optionally decompose by incremental wind, solar and geothermal scenario

The key metric is not instantaneous renewable output alone but whether the storage benefit persists into winter/dry periods.

## Chart 4 — Residual thermal requirement

**Question:** After the renewable build, what job remains for thermal generation?

For each fleet × hydrology scenario show:

- annual thermal generation (GWh)
- peak thermal requirement (MW)
- number of high-thermal-use periods
- minimum hydro storage
- water-value / scarcity indicator where available
- unserved energy if produced by the professional model

This chart should explicitly distinguish low annual thermal energy from thermal capacity retained as insurance.

## Default scenario comparisons

1. Recent observed system
2. EDGS Reference demand + operating/committed generation
3. EDGS Reference demand + committed and actively pursued generation
4. Higher-renewables sensitivity

Each future fleet should be replayed, where the professional model supports it, against a consistent set of historical inflow/weather sequences including representative normal and dry periods.

## Visual design principles

- identical axes when comparing scenarios
- direct labels where possible
- use restrained stacked-area colours and a strong demand/storage line
- annotate the story rather than require the reader to decode a legend
- make observed/modelled/derived status visually explicit
- include source/model/run-date text on every exported figure
- avoid implying precision beyond the underlying professional model output

## Derived-data rule

Every plotted point should be traceable to either:

1. an authoritative upstream dataset/model output; or
2. a small documented transformation stored in this repository.

If JADE, GEM, vSPD, HSS, EA or MBIE already publishes the underlying table, link to it rather than mirror it. Store local CSV/JSON only for joins, calibrated scenarios, chart-ready aggregates, comparison metrics or provenance that do not already exist upstream.
