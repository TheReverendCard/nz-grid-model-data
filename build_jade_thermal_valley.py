from pathlib import Path
import json
import numpy as np
import pandas as pd

MODEL = Path('data/jade/model')
OUT = MODEL

security = pd.read_csv(MODEL / 'weekly_security_simulations.csv')
thermal = pd.read_csv(MODEL / 'weekly_thermal_dispatch_simulations.csv')

keys = ['simulation', 'calendar_year', 'calendar_week']
df = security.merge(thermal, on=keys, how='inner', validate='one_to_one')
if len(df) != len(security) or len(df) != len(thermal):
    raise RuntimeError(f'Join mismatch: security={len(security)}, thermal={len(thermal)}, joined={len(df)}')

# Basic thermal-activation flags.
df['thermal_positive'] = df['thermal_generation_gwh'] > 1e-9

# Remove the deterministic seasonal/week effect before measuring the hydro relationship.
# Each value is expressed relative to that calendar week's mean across stochastic simulations.
week_keys = ['calendar_year', 'calendar_week']
for col in ['stored_energy_gwh', 'thermal_generation_gwh', 'thermal_peak_block_mw']:
    df[f'{col}_within_week'] = df[col] - df.groupby(week_keys)[col].transform('mean')

# Same-week storage vs thermal relationship.
def corr(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
        return np.nan
    return float(np.corrcoef(a[ok], b[ok])[0, 1])

metrics = {
    'rows': int(len(df)),
    'simulations': int(df['simulation'].nunique()),
    'weeks': int(df[week_keys].drop_duplicates().shape[0]),
    'raw_storage_vs_thermal_gwh_correlation': corr(df['stored_energy_gwh'], df['thermal_generation_gwh']),
    'within_week_storage_vs_thermal_gwh_correlation': corr(df['stored_energy_gwh_within_week'], df['thermal_generation_gwh_within_week']),
    'raw_storage_vs_peak_thermal_mw_correlation': corr(df['stored_energy_gwh'], df['thermal_peak_block_mw']),
    'within_week_storage_vs_peak_thermal_mw_correlation': corr(df['stored_energy_gwh_within_week'], df['thermal_peak_block_mw_within_week']),
}

# Lead relationship: does relatively low storage in a given simulation/week associate with more thermal
# over the following 1-4 weeks?  Shift within each simulation in stage order.
df = df.sort_values(['simulation', 'stage']).reset_index(drop=True)
for lead in [1, 2, 3, 4]:
    future = df.groupby('simulation')['thermal_generation_gwh'].shift(-lead)
    # Remove week effect from the future thermal observation as well.
    future_week_mean = future.groupby([df['calendar_year'], df['calendar_week']]).transform('mean')
    df[f'thermal_gwh_lead_{lead}_within_week'] = future - future_week_mean
    metrics[f'within_week_storage_vs_thermal_gwh_lead_{lead}_correlation'] = corr(
        df['stored_energy_gwh_within_week'], df[f'thermal_gwh_lead_{lead}_within_week']
    )

# Storage bins. Fixed-width bins are easier to interpret and reproduce than quantile bins.
width = 500.0
max_edge = float(np.ceil(df['stored_energy_gwh'].max() / width) * width)
edges = np.arange(0.0, max_edge + width, width)
df['storage_bin'] = pd.cut(df['stored_energy_gwh'], bins=edges, right=False, include_lowest=True)

bins = (
    df.groupby('storage_bin', observed=True)
      .agg(
          observations=('simulation', 'size'),
          simulation_count=('simulation', 'nunique'),
          storage_mean_gwh=('stored_energy_gwh', 'mean'),
          thermal_positive_share=('thermal_positive', 'mean'),
          thermal_generation_mean_gwh=('thermal_generation_gwh', 'mean'),
          thermal_generation_median_gwh=('thermal_generation_gwh', 'median'),
          thermal_peak_mean_mw=('thermal_peak_block_mw', 'mean'),
          thermal_peak_median_mw=('thermal_peak_block_mw', 'median'),
      )
      .reset_index()
)
bins['storage_bin'] = bins['storage_bin'].astype(str)

# Per-week cross-simulation correlations show when hydro scarcity matters most, rather than averaging
# the entire seasonal trajectory into one coefficient.
week_rows = []
for (year, week), g in df.groupby(week_keys):
    week_rows.append({
        'calendar_year': int(year),
        'calendar_week': int(week),
        'simulation_count': int(g['simulation'].nunique()),
        'storage_mean_gwh': float(g['stored_energy_gwh'].mean()),
        'thermal_generation_mean_gwh': float(g['thermal_generation_gwh'].mean()),
        'thermal_positive_share': float(g['thermal_positive'].mean()),
        'storage_vs_thermal_gwh_correlation': corr(g['stored_energy_gwh'], g['thermal_generation_gwh']),
        'storage_vs_peak_thermal_mw_correlation': corr(g['stored_energy_gwh'], g['thermal_peak_block_mw']),
    })
week_corr = pd.DataFrame(week_rows).sort_values(['calendar_year', 'calendar_week'])

# Compact joined table for further charting/audit.
joined_cols = keys + [
    'stage', 'stored_energy_gwh', 'thermal_generation_gwh', 'thermal_peak_block_mw',
    'thermal_positive', 'stored_energy_gwh_within_week',
    'thermal_generation_gwh_within_week', 'thermal_peak_block_mw_within_week'
]
df[joined_cols].to_csv(OUT / 'thermal_valley_simulations.csv', index=False)
bins.to_csv(OUT / 'thermal_valley_storage_bins.csv', index=False)
week_corr.to_csv(OUT / 'thermal_valley_week_correlations.csv', index=False)

provenance = {
    'method': 'Derived join of published JADE stochastic simulation outputs already normalized in this repository.',
    'join_keys': keys,
    'seasonality_control': 'Within-week demeaning across simulations; this removes the common calendar-week level before correlation.',
    'interpretation_warning': 'Association is not proof that low storage alone causes thermal dispatch. JADE also responds to demand, inflows, outages, fuel costs, transmission and forward-looking water values.',
    'source_files': [
        'data/jade/model/weekly_security_simulations.csv',
        'data/jade/model/weekly_thermal_dispatch_simulations.csv'
    ],
    'metrics': metrics,
}
with open(OUT / 'thermal_valley_metrics.json', 'w', encoding='utf-8') as f:
    json.dump(provenance, f, indent=2)

print(json.dumps(metrics, indent=2))
print(f'Wrote {len(df):,} joined simulation-weeks and {len(bins)} storage bins.')
