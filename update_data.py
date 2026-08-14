import io
import os
import pandas as pd
import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

def fetch_and_clean(url, filename, skip_comments=False):
    print(f"Fetching {filename}...")
    try:
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            text = response.text.strip()
            
            if not text or "<html" in text.lower() or "<!doctype" in text.lower():
                print(f"Warning: Invalid CSV response (HTML or empty) for {filename}")
                return
                
            comment_char = '#' if skip_comments else None
            df = pd.read_csv(io.StringIO(text), comment=comment_char)
            
            if not df.empty:
                df.to_csv(filename, index=False)
                print(f"Success: Saved {filename} ({len(df)} rows)")
            else:
                print(f"Warning: Dataframe empty for {filename}")
        else:
            print(f"HTTP {response.status_code} for {filename}")
            
    except Exception as e:
        print(f"Error processing {filename}: {e}")

# 1. Hydro Storage Data
hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
fetch_and_clean(hydro_url, "hydro_storage.csv", skip_comments=True)

# 2. Generation Investment Pipeline (Direct Azure Storage feed)
sas_token = "?sv=2021-10-04&si=publicdata&sr=c&sig=f034UWz1xmMbk89jd76zY0M%2BwycFDhhumejUrjqlfIw%3D"
pipeline_url = f"https://emidatasets.blob.core.windows.net/publicdata/Datasets/Wholesale/Generation/GenerationFleet/Proposed/20240912_GenerationInvestmentPipeline.csv{sas_token}"
fetch_and_clean(pipeline_url, "generation_pipeline.csv", skip_comments=False)
