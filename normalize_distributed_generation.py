from __future__ import annotations
import csv, io, json
from datetime import datetime
from pathlib import Path
D=Path('data/distributed_generation');M=D/'model';FILES={'all_all':D/'installed_dg_trends_solar_all.csv','all_res':D/'installed_dg_trends_solar_residential.csv','solar_all':D/'installed_dg_trends_solar_without_battery_all.csv','solar_res':D/'installed_dg_trends_solar_without_battery_residential.csv'};OUT=M/'solar_installations_monthly.csv'
def read(p):
 lines=p.read_text(encoding='utf-8-sig').splitlines();i=next(i for i,l in enumerate(lines) if l.lower().startswith('month end,'));res={}
 for r in csv.DictReader(io.StringIO('\n'.join(lines[i:]))):
  if not r.get('Month end'):continue
  d=datetime.strptime(r['Month end'],'%d/%m/%Y').date().isoformat();res[d]={'icp_count':int(float(r['ICP count'])),'uptake_pct':float(r['ICP uptake rate (%)']),'capacity_mw':float(r['Total capacity installed (MW)']),'avg_kw':float(r['Avg. capacity installed (kW)']),'new_installations':int(float(r['ICP count - new installations'])),'avg_new_kw':float(r['Avg. capacity - new installations (kW)'])}
 return res
def sub(a,b):return {k:a[k]-b[k] for k in ('icp_count','capacity_mw','new_installations')}
def row(d,s,b,x,derived=False):return {'month_end':d,'segment':s,'battery':b,'icp_count':round(x['icp_count']),'installed_capacity_mw':round(x['capacity_mw'],6),'new_installations':round(x['new_installations']),'new_install_capacity_mw':round(x.get('new_installations',0)*x.get('avg_new_kw',0)/1000,6) if not derived else '','average_capacity_kw':round(x.get('avg_kw',0),6) if not derived else '','average_new_install_capacity_kw':round(x.get('avg_new_kw',0),6) if not derived else '','derived_by_subtraction':str(derived).lower()}
def main():
 x={k:read(v) for k,v in FILES.items()};dates=sorted(set.intersection(*(set(v) for v in x.values())));rows=[]
 for d in dates:
  aa,ar,sa,sr=x['all_all'][d],x['all_res'][d],x['solar_all'][d],x['solar_res'][d];rb=sub(ar,sr);na=sub(aa,ar);ns=sub(sa,sr);nb=sub(na,ns);tb=sub(aa,sa)
  rows += [row(d,'residential','without_battery',sr),row(d,'residential','with_battery',rb,True),row(d,'non_residential','without_battery',ns,True),row(d,'non_residential','with_battery',nb,True),row(d,'all','without_battery',sa),row(d,'all','with_battery',tb,True)]
 OUT.parent.mkdir(parents=True,exist_ok=True)
 with OUT.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 (M/'solar_installations_method.json').write_text(json.dumps({'caveat':'Solar+Batteries was added to the registry in November 2023. Historic classifications may be inconsistent and corrections are unlikely to be backdated.','method':'Battery categories are derived as Solar (All) minus Solar (without battery). Non-residential is all ICPs minus residential.'},indent=2)+'\n')
 print('Wrote',OUT,len(rows),'rows')
if __name__=='__main__':main()
