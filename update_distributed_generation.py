from __future__ import annotations
import hashlib, json
from pathlib import Path
import requests
D=Path('data/distributed_generation'); META=Path('data/metadata/distributed_generation_sources.json'); URL='https://www.emi.ea.govt.nz/Retail/Download/DataReport/CSV/GUEHMT'; BASE={'DateFrom':'20130901','Show':'CapacityAvg','_rsdr':'ALL','_si':'v|4'}
REPORTS={'solar_all_all':({'FuelType':'solar_all'},D/'installed_dg_trends_solar_all.csv'),'solar_all_residential':({'FuelType':'solar_all','MarketSegment':'Res'},D/'installed_dg_trends_solar_residential.csv'),'solar_without_battery_all':({'FuelType':'solar'},D/'installed_dg_trends_solar_without_battery_all.csv'),'solar_without_battery_residential':({'FuelType':'solar','MarketSegment':'Res'},D/'installed_dg_trends_solar_without_battery_residential.csv')}
REGION_URL='https://emidatasets.blob.core.windows.net/publicdata/Datasets/Retail/SolarInstallations/SolarInstallationsByRegion.csv'; REGION=D/'solar_installations_by_region.csv'
def fetch(url,params=None):
 r=requests.get(url,params=params,timeout=120);r.raise_for_status()
 if not r.content:raise RuntimeError(f'Empty response from {r.url}')
 return r.content,{'url':r.url,'etag':r.headers.get('ETag',''),'last_modified':r.headers.get('Last-Modified','')}
def write(p,c):
 p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists() and p.read_bytes()==c:print('Unchanged',p);return
 p.write_bytes(c);print('Wrote',p,len(c),'bytes')
def rec(label,p,c,h):return {'description':label,'local_file':str(p),'request_url':h['url'],'sha256':hashlib.sha256(c).hexdigest(),'etag':h['etag'],'last_modified':h['last_modified']}
def main():
 meta={'source':'New Zealand Electricity Authority EMI','datasets':{}}
 for key,(extra,p) in REPORTS.items():
  q=dict(BASE);q.update(extra);c,h=fetch(URL,q)
  if b'Month end' not in c[:4096] and b'Month End' not in c[:4096]:raise RuntimeError(f'{key} did not return expected CSV')
  write(p,c);meta['datasets'][key]=rec(key,p,c,h)
 c,h=fetch(REGION_URL);write(REGION,c);meta['datasets']['solar_installations_by_region']=rec('Solar installations by region',REGION,c,h)
 write(META,(json.dumps(meta,indent=2,sort_keys=True)+'\n').encode())
if __name__=='__main__':main()
