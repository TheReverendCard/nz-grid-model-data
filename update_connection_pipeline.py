from __future__ import annotations
import csv,hashlib,json,re
from pathlib import Path
import requests
from openpyxl import load_workbook
URL='https://static.transpower.co.nz/public/uncontrolled_docs/Generation%20and%20energy%20storage%20connection%20pipeline.xlsx';D=Path('data/pipeline');RAW=D/'generation_and_energy_storage_connection_pipeline.xlsx';OUT=D/'transpower_generation_storage_pipeline.csv';META=Path('data/metadata/connection_pipeline_source.json')
def clean(v):return re.sub(r'[^a-z0-9]+','_',str(v or '').strip().lower()).strip('_')
def num(v):
 if v is None or v=='':return ''
 m=re.search(r'-?\d+(?:\.\d+)?',str(v).replace(',',''));return float(m.group()) if m else ''
def find(h,*n):
 for i,x in enumerate(h):
  if any(y in clean(x) for y in n):return i
 return None
def main():
 r=requests.get(URL,timeout=120);r.raise_for_status()
 if not r.content.startswith(b'PK'):raise RuntimeError('Transpower pipeline response is not XLSX')
 D.mkdir(parents=True,exist_ok=True);RAW.write_bytes(r.content);wb=load_workbook(RAW,data_only=True,read_only=True);best=None
 for ws in wb.worksheets:
  for ri,row in enumerate(ws.iter_rows(min_row=1,max_row=min(ws.max_row,30),values_only=True),1):
   vals=[str(v or '') for v in row];j=' '.join(vals).lower()
   if len([v for v in vals if v.strip()])>=4 and ('project' in j or 'connection' in j) and ('mw' in j or 'capacity' in j):best=(ws,ri,list(row));break
  if best:break
 if not best:raise RuntimeError('Could not identify pipeline header row')
 ws,hr,rh=best;headers=[];seen={}
 for i,h in enumerate(rh):
  b=clean(h) or f'column_{i+1}';seen[b]=seen.get(b,0)+1;headers.append(b if seen[b]==1 else f'{b}_{seen[b]}')
 p=find(headers,'project_name','project');tech=find(headers,'technology','generation_type','fuel_type','resource_type','subtype');cap=find(headers,'capacity_mw','maximum_capacity','capacity','mw');stage=find(headers,'stage','status');region=find(headers,'region');loc=find(headers,'location','point_of_connection','poc');cust=find(headers,'customer','developer','proponent');date=find(headers,'need_date','commission','connection_date');out=[]
 for rr in ws.iter_rows(min_row=hr+1,values_only=True):
  vals=list(rr)+[None]*max(0,len(headers)-len(rr))
  if not any(v not in (None,'') for v in vals):continue
  rec={'project_name':vals[p] if p is not None else '','technology':vals[tech] if tech is not None else '','capacity_mw':num(vals[cap]) if cap is not None else '','stage':vals[stage] if stage is not None else '','region':vals[region] if region is not None else '','location':vals[loc] if loc is not None else '','customer_or_developer':vals[cust] if cust is not None else '','expected_connection_or_need_date':vals[date] if date is not None else ''}
  for h,v in zip(headers,vals):rec['raw_'+h]=v if v is not None else ''
  out.append(rec)
 with OUT.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=list(out[0]));w.writeheader();w.writerows(out)
 META.parent.mkdir(parents=True,exist_ok=True);META.write_text(json.dumps({'source':'Transpower','source_url':URL,'sha256':hashlib.sha256(r.content).hexdigest(),'etag':r.headers.get('ETag',''),'last_modified':r.headers.get('Last-Modified',''),'worksheet':ws.title,'header_row':hr,'rows':len(out)},indent=2,default=str)+'\n')
 print('Wrote',OUT,len(out),'rows')
if __name__=='__main__':main()
