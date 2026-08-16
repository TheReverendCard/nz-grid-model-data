from __future__ import annotations
import csv,json
from collections import defaultdict
from pathlib import Path
P=Path('data/public');P.mkdir(parents=True,exist_ok=True)
def read(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(p,rows):
 if not rows:return
 with p.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 print('Wrote',p,len(rows),'rows')
def group(f):
 x=(f or '').lower()
 if 'hydro' in x or 'water' in x:return 'Hydro'
 if 'geo' in x:return 'Geothermal'
 if 'wind' in x:return 'Wind'
 if 'solar' in x or x=='sol':return 'Solar'
 if 'coal' in x:return 'Coal'
 if 'gas' in x:return 'Gas'
 if 'diesel' in x or 'liquid' in x or 'oil' in x:return 'Liquid fuel'
 if 'bio' in x:return 'Biomass/biogas'
 return f or 'Other/unknown'
def main():
 by=defaultdict(float)
 for r in read(Path('data/wholesale/model/generation_daily.csv')):by[(r['date'],group(r['fuel_code']))]+=float(r['generation_mwh'])
 mix=[{'date':d,'fuel':f,'generation_mwh':round(v,3)} for(d,f),v in sorted(by.items())];write(P/'generation_mix_daily.csv',mix);tot=defaultdict(lambda:[0.,0.])
 for(d,f),v in by.items():tot[d][0]+=v;tot[d][1]+=v if f in {'Hydro','Geothermal','Wind','Solar','Biomass/biogas'} else 0
 renew=[{'date':d,'total_generation_mwh':round(t,3),'renewable_generation_mwh':round(r,3),'renewable_pct':round(100*r/t,3) if t else ''} for d,(t,r) in sorted(tot.items())];write(P/'renewable_share_daily.csv',renew)
 q=Path('data/wholesale/model/demand_daily.csv');
 if q.exists():write(P/'demand_daily.csv',read(q))
 sb=defaultdict(lambda:[0.,0.,0.])
 for r in read(Path('data/hydro/model/storage_daily.csv')):
  d=r['date'];sb[d][0]+=float(r.get('active_storage_mm3') or 0);sb[d][1]+=float(r.get('contingent_storage_mm3') or 0);sb[d][2]+=float(r.get('total_usable_storage_mm3') or 0)
 write(P/'hydro_storage_daily.csv',[{'date':d,'active_storage_mm3':round(v[0],3),'contingent_storage_mm3':round(v[1],3),'total_usable_storage_mm3':round(v[2],3)} for d,v in sorted(sb.items())]);s=Path('data/distributed_generation/model/solar_installations_monthly.csv')
 if s.exists():write(P/'solar_installations_monthly.csv',read(s))
 price=P/'wholesale_prices_daily.csv'
 if price.exists():
  a={r['date']:r for r in read(price)};b={r['date']:r for r in renew};write(P/'renewables_vs_price_daily.csv',[{'date':d,'renewable_pct':b[d]['renewable_pct'],'reference_mean_nzd_mwh':a[d]['reference_mean_nzd_mwh']} for d in sorted(set(a)&set(b))])
 (P/'manifest.json').write_text(json.dumps({'datasets':sorted(str(x) for x in P.glob('*.csv')),'note':'Observed/public-data derivatives only; model scenarios are excluded.'},indent=2)+'\n')
if __name__=='__main__':main()
