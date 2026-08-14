import pandas as pd
import requests
import io
import os

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def fetch_and_clean(url, filename, skip_comments=False):
    print(f"Fetching {filename}...")
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        # If skip_comments is True, Pandas ignores lines starting with '#'
        comment_char = '#' if skip_comments else None
        try:
            df = pd.read_csv(io.StringIO(response.text), comment=comment_char)
            df.to_csv(filename, index=False)
            print(f"Success: Saved {filename}")
        except Exception as e:
            print(f"Error parsing CSV data for {filename}: {e}")
    else:
        print(f"HTTP Error {response.status_code} for {filename}")

# 1. Hydro Storage Data
hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
fetch_and_clean(hydro_url, "hydro_storage.csv", skip_comments=True)

# 2. Generation Investment Pipeline
pipeline_url = "https://www.emi.ea.govt.nz/Wholesale/Download/DataReport/CSV/ProposedGenerationFleet"
fetch_and_clean(pipeline_url, "generation_pipeline.csv", skip_comments=True)
