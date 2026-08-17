# JADE renewable impulse-response experiment

## Question

How long can an additional unit of summer renewable generation preserve hydro energy, and how much of that preserved hydro value survives into later autumn and winter weeks?

The experiment is intentionally expressed as a paired JADE counterfactual rather than a hand-built hydro approximation.

## Baseline

Use the latest Electricity Authority published JADE input set and its run configuration as the baseline. Keep demand, hydrological sampling, outages, thermal fleet, hydro constraints, fuel costs, carbon settings, transmission settings, initial reservoir levels, solver settings, and random seeds identical between paired runs.

## Perturbation

Add exactly 1 GWh of fixed renewable generation in one selected summer week through `fixed_stations.csv`.

JADE stores fixed-station values as MW by load block and converts them to MWh using `hours_per_block.csv`. Therefore the pulse should be distributed across PEAK, SHOULDER and OFFPEAK so that:

`sum(extra_MW_block * block_hours) = 1000 MWh`

The default diagnostic pulse should be energy-flat across the week unless a technology-shaped profile is explicitly requested. Later experiments can use solar-shaped or wind-shaped load-block distributions.

## Two experiment modes

### A. Fixed-policy marginal diagnostic

Train the baseline policy once, then simulate the baseline and +1 GWh input cases using the same policy cuts and identical hydrological sample paths/random seed.

Purpose: isolate the short-run dispatch/storage response to one extra GWh under the baseline policy.

Caveat: this is not a fully re-optimised policy for the perturbed system. Results must be labelled as a fixed-policy marginal response.

### B. Retrained paired counterfactual

Train a baseline policy and a separate +1 GWh policy from otherwise identical settings, then simulate both with identical hydrological sample paths/random seed.

Purpose: authoritative estimate of the optimised system response when the extra renewable energy is known to the stochastic hydro scheduler.

This is the preferred result for publication. Fixed-policy mode is useful for testing and intuition.

## Core outputs

For every simulation and subsequent week, calculate paired differences in:

- total stored hydro energy (GWh)
- hydro generation, where available from JADE result variables
- thermal generation (GWh)
- peak-block thermal dispatch (MW)
- spill energy (MWh)
- lost load (MWh/cost indicator)
- marginal water values, where useful
- carbon emissions, where useful

## Primary metrics

### Hydro-preservation survival curve

For pulse week `t0`, define:

`storage_survival(t) = delta_stored_energy(t) / 1 GWh`

Report median and P5-P95 across matched hydrological simulations.

### Winter carryover

Fraction of the original 1 GWh still represented by additional stored hydro at selected calendar checkpoints, initially 1 May, 1 June and 1 July.

### Half-life / median delay

First subsequent week where the median additional stored hydro falls below 0.5 GWh.

This is descriptive rather than a physical decay constant.

### Thermal displacement

Cumulative reduction in thermal generation after the pulse:

`cumulative_thermal_displacement(t) = sum(baseline_thermal_gwh - pulse_thermal_gwh)`

This measures how much of the summer renewable GWh ultimately avoids thermal generation, including deferred effects.

### Spill interaction

Track additional spill. A summer renewable GWh that simply causes extra hydro spill has much lower seasonal-storage value than one that remains stored into the thermal-heavy season.

## Initial pulse weeks

Start with four representative weeks:

- January: ISO week 3
- February: ISO week 7
- March: ISO week 11
- April: ISO week 15

After the method works, expand to every week and render a heat map of pulse week versus later storage survival / thermal displacement.

## Public visual

The intended public-facing chart is a survival curve titled approximately:

**Where does a summer renewable GWh go?**

X-axis: weeks after the pulse.

Y-axis: additional hydro energy still preserved (GWh per 1 GWh renewable pulse).

Show median plus stochastic band, with May/June/July markers. A companion chart can show cumulative thermal generation avoided.

## Provenance and data policy

Raw Electricity Authority JADE inputs remain upstream. The repository should store only:

- experiment configuration/provenance
- small paired-difference tables
- summary statistics
- charts

Do not permanently mirror the full JADE input archive or large cut files.

## Interpretation

The experiment measures system-level intertemporal value, not the travel time of a particular cubic metre of water. Water may move rapidly through cascades once released. The quantity of interest is how long additional renewable generation allows the optimised system to retain hydro-generation value before that value is used, spilled, or otherwise dissipated.
