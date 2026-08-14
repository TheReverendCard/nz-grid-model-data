import urllib.request
import urllib.error
import pandas as pd
import os

def fetch_ea_dataset(url, filename, is_hydro=False):
    print(f"\n--- Fetching {filename} ---")
    
    # Forge headers to look like a standard Firefox user clicking a link on the EA site
    req = urllib.request.Request(
        url, 
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Referer': 'https://www.emi.ea.govt.nz/'
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            
        print(f"HTTP Status: {response.status}")
        print(f"Downloaded Size: {len(content)} bytes")
        
        if len(content) == 0:
            print("FAILED: Still 0 bytes. The EA server is silently dropping the connection from GitHub's IP.")
            return

        # Save the raw content temporarily
        temp_path = f"temp_{filename}"
        with open(temp_path, 'wb') as f:
            f.write(content)
            
        # Let Pandas clean up the metadata rows
        comment_char = '#' if is_hydro else None
        df = pd.read_csv(temp_path, comment=comment_char)
        
        if not df.empty:
            df.to_csv(filename, index=False)
            print(f"SUCCESS: Cleaned and saved {filename} ({len(df)} rows)")
        else:
            print(f"FAILED: Pandas parsed the file but found no data.")
            
        os.remove(temp_path)
        
    except urllib.error.HTTPError as e:
        print(f"HTTP ERROR: {e.code} - {e.reason}. (The EA server is explicitly blocking GitHub Actions).")
    except Exception as e:
        print(f"ERROR: {e}")

# 1. Hydro Storage Data
hydro_url = "https://www.emi.ea.govt.nz/Environment/Download/DataReport/CSV/3UN1KD?DateFrom=20200101&RegionCode=NZ"
fetch_ea_dataset(hydro_url, "hydro_storage.csv", is_hydro=True)

# 2. Generation Investment Pipeline
pipeline_url = "https://www.emi.ea.govt.nz/Wholesale/Download/DataReport/CSV/ProposedGenerationFleet"
fetch_ea_dataset(pipeline_url, "generation_pipeline.csv", is_hydro=False)
