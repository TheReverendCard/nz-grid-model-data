from __future__ import annotations
import csv,io,json
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET
import requests
C='https://emidatasets.blob.core.windows.net/publicdata';PREFIX='Datasets/Wholesale/DispatchAndPricing/FinalEnergyPrices/';MONTH=PREFIX+'ByMonth/';START='202401';NODES=('OTA2201','HAY2201','BEN2201');D=Path('data/prices');PARTS=D/'monthly';OUT=Path('data/public/wholesale_prices_daily.csv');META=Path('data/metadata/price_sources.json')
def blobs(prefix):
 r=requests.get(C,params={'restype':'container','comp':'list','prefix':prefix},timeout=120);r.raise_for_status();root=ET.fromstring(r.content);out=[]
 for b in root.findall('.//Blob'):
  n=b.findtext('Name') or '';p=b.find('Properties');out.append({'name':n,'etag':((p.findtext('Etag') or '').strip('"') if p is not None else '')})
 return out
def parse(content):
 reader=csv.DictReader(io.StringIO(content.decode('utf-8-sig')));by=defaultdict(list)
 for r in reader:
  d=(r.get('TradingDate') or r.get('Trading_date') or '').strip();n=(r.get('PointOfConnection') or r.get('Node') or '').strip();p=(r.get('DollarsPerMegawattHour') or r.get('Price') or '').strip()
  if d and n in NODES and p!='':by[(d,n)].append(float(p))
 rows=[]
 for d in sorted({x for x,_ in by}):
  v={n:(sum(by[(d,n)])/len(by[(d,n)]) if by[(d,n)] else None) for n in NODES};present=[x for x in v.values() if x is not None];rows.append({'date':d,'otahuhu_nzd_mwh':round(v['OTA2201'],4) if v['OTA2201'] is not None else '','haywards_nzd_mwh':round(v['HAY2201'],4) if v['HAY2201'] is not None else '','benmore_nzd_mwh':round(v['BEN2201'],4) if v['BEN2201'] is not None else '','reference_mean_nzd_mwh':round(sum(present)/len(present),4) if present else ''})
 return rows
def main():
 PARTS.mkdir(parents=True,exist_ok=True);prev=json.loads(META.read_text()) if META.exists() else {};old=prev.get('etags',{});etags={};monthly=[b for b in blobs(MONTH) if Path(b['name']).name[:6].isdigit() and Path(b['name']).name[:6]>=START and b['name'].endswith('_FinalEnergyPrices.csv')]
 for b in monthly:
  ym=Path(b['name']).name[:6];part=PARTS/f'{ym}.csv';etags[b['name']]=b['etag']
  if part.exists() and old.get(b['name'])==b['etag']:continue
  r=requests.get(f"{C}/{quote(b['name'],safe='/')}",timeout=180);r.raise_for_status();rows=parse(r.content)
  with part.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 combined=[];monthly_ym=set()
 for p in sorted(PARTS.glob('*.csv')):
  with p.open(encoding='utf-8',newline='') as f:
   for r in csv.DictReader(f):combined.append(r);monthly_ym.add(r['date'].replace('-','')[:6])
 daily=[b for b in blobs(PREFIX) if '/' not in b['name'].removeprefix(PREFIX) and Path(b['name']).name[:8].isdigit() and Path(b['name']).name[:6]>=START];chosen={}
 for b in daily:
  name=Path(b['name']).name;day=name[:8];ym=day[:6]
  if ym in monthly_ym:continue
  final=name.endswith('_FinalEnergyPrices.csv');cur=chosen.get(day)
  if cur is None or(final and not cur[0]):chosen[day]=(final,b)
 for day,(_,b) in sorted(chosen.items()):
  r=requests.get(f"{C}/{quote(b['name'],safe='/')}",timeout=120);r.raise_for_status();combined.extend(parse(r.content));etags[b['name']]=b['etag']
 dedup={r['date']:r for r in combined};rows=[dedup[d] for d in sorted(dedup)];OUT.parent.mkdir(parents=True,exist_ok=True)
 with OUT.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 META.parent.mkdir(parents=True,exist_ok=True);META.write_text(json.dumps({'source':'Electricity Authority final energy prices','reference_nodes':NODES,'start_yyyymm':START,'etags':etags},indent=2)+'\n');print('Wrote',OUT,len(rows),'days')
if __name__=='__main__':main()
