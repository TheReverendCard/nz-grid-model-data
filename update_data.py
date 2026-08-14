import io
import os
import pandas as pd
import cloudscraper

# Create scraper instance to bypass Cloudflare bot checks
scraper = cloudscraper.create_scraper()

def fetch_and_clean(url, filename, skip_comments=False):
    print(f"Fetching {filename} from {url}...")
    try:
        response = scraper.get(url)
        
        if response.status_code == 200:
            text = response.text.strip()
            
            # Check if response returned HTML or empty content
            if not text:
                print(f"Warning: Empty response received for {filename}")
                return
            if "<html" in text.lower() or "<!doctype" in text.lower():
                print(f"Warning: Cloudflare HTML challenge returned for {filename}")
                return
                
            comment_char = '#' if skip_comments else None
            df = pd.read_csv(io.StringIO(text), comment=comment_char)
            
            if not df.empty:
                df.to_csv(filename, index=False)
                print(f"Success: Saved {filename} ({len(df)} rows)")
            else:
                print(f"Warning: Parsed dataframe was empty for {filename}")
        else:
            print(f"HTTP Error {response.status_code} for {filename}")
            
    except Exception as e:
        print(f"Error fetching/parsing {filename}: {e}")

# 1. Hydro Storage Data
hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
fetch_and_clean(hydro_url, "hydro_storage.csv", skip_comments=True)

# 2. Generation Investment Pipeline
pipeline_url = "https://www.emi.ea.govt.nz/Wholesale/Download/DataReport/CSV/ProposedGenerationFleet"
fetch_and_clean(pipeline_url, "generation_pipeline.csv", skip_comments=True)
