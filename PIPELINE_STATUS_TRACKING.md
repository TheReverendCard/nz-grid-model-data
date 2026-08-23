# Generation-pipeline status tracking

This repository retains one Electricity Authority Generation Investment Pipeline snapshot per month under `data/pipeline/history/` and uses those snapshots to learn how the published project-status mix changes over time.

The purpose is to eventually replace arbitrary assumptions such as “perhaps 15% of early-stage projects will become committed” with an empirical measure derived from the repository's own retained history.

## Builder

`build_generation_pipeline_status_metrics.py`

The monthly generation-pipeline workflow runs this builder after copying the current EA snapshot into the monthly history directory and before rendering the public chart.

Outputs:

- `data/pipeline/ea_generation_pipeline_status_history.csv`
- `data/pipeline/ea_generation_pipeline_status_transitions.csv`
- `data/metadata/ea_generation_pipeline_status_metrics.json`

The chart renderer reads the JSON summary and automatically changes its bottom interpretation note when enough history exists to report a status-transition measure responsibly.

## Two tracking modes

### 1. Exact project-identity mode

If every retained snapshot contains a stable unique project identifier such as `project_id`, `project_name`, `project`, or `name`, the builder follows individual projects through time.

For projects first observed as `Other / early-stage`, it records whether they are later observed as `Committed`.  The preferred empirical figure is capacity-weighted:

`capacity of those early-stage projects later seen as Committed / capacity of all projects first seen as early-stage`

A project-count percentage is retained as a secondary diagnostic.

The figure is not shown immediately.  The default display gate requires at least:

- 6 monthly snapshots;
- 5 months of observation history;
- 10 projects first observed as `Other / early-stage`;
- 100 MW of early-stage project exposure; and
- at least one observed conversion to `Committed`.

When those conditions are met, the chart may state the observed historical conversion percentage and its observation window.  It remains an empirical historical measure, not a guarantee that the same proportion of today's pipeline will eventually proceed.

### 2. Aggregate net-flow proxy mode

The retained EA snapshot currently available in this repository is aggregated by status, technology and commissioning year rather than by named project.  That means a fall in early-stage solar MW and a simultaneous rise in committed solar MW cannot prove that the same solar projects changed status.

Until stable project identities are available, the builder therefore calculates a deliberately conservative proxy by technology.  Between each pair of monthly snapshots it compares:

- net capacity leaving `Other / early-stage` and `Actively pursued`; and
- simultaneous net capacity entering `Committed`.

Only the smaller simultaneous quantity is counted as a possible promotion-to-committed flow.  This avoids assuming that all additions, withdrawals or commissioning-date changes are status transitions.

The aggregate proxy is retained from the first two snapshots onward, but it is not annotated on the chart until at least:

- 12 monthly snapshots;
- 11 months of observation history;
- 100 MW of conservatively matched promotion; and
- promotion signals in at least two snapshot intervals.

Even after that gate is met, the value is labelled a **net status-flow proxy per monthly snapshot interval**.  It must not be presented as an eventual project-completion probability and must not be used as a “likely pipeline” layer.

## Automatic upgrade path

The builder checks the snapshot schema on every run.  If a future EA snapshot source gives the repository stable project-level identifiers and the retained history supports them, the tracker automatically switches from aggregate proxy mode to exact project-identity mode.

This allows the repository to begin collecting useful evidence immediately without overstating what the current aggregate data can prove.

## Current-stock ratios

The JSON summary also records descriptive current-stock ratios such as committed MW as a percentage of current `Other / early-stage` MW.  These are useful context, but they are not transition probabilities.  A value near 15%, for example, would mean only that current committed capacity is about 15% as large as the selected lower-confidence stock; it would not mean that 15% of current early-stage projects are expected to become committed.
