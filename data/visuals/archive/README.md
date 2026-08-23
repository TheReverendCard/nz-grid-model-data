# Monthly generation pipeline archive

The current generation-pipeline chart lives in `data/visuals/generation_pipeline_monthly_latest.png`, with its exact plotted data in `data/visuals/generation_pipeline_monthly_plot_data.csv`.

When a later Electricity Authority investment-pipeline snapshot replaces the current snapshot, the render script copies the prior PNG and plot-data CSV into this folder using the prior `YYYY-MM` snapshot month before rendering the new current chart.

The monthly workflow validates the PNG signature, dimensions and minimum file size, and checks that the plot-data CSV contains the detailed columns produced by the current renderer before it will publish either artifact.

August 2026 is the first retained monthly snapshot, so there is intentionally no July 2026 archive chart.
