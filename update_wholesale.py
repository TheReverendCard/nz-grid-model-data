from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET
import requests

C='https://emidatasets.blob.core.windows.net/publicdata'
DATASETS={
 'generation':{'prefix':'Datasets/Wholesale/Generation/Generation_MD/','suffix':'_Generation_MD.csv','out':Path('data/wholesale/raw/generation'),'start':'199708'},
 'grid_export':{'prefix':'Datasets/Wholesale/Metered_data/Grid_export/','suffix':'_Grid_export.csv','out':Path('data/wholesale/raw/grid_export'),'start':'202401'},
}
META=Path('data/metadata/wholesale_sources.json')
def list_blobs(prefix):
    out=[]; marker=''
    while True:
        p={'restype':'container','comp':'list','prefix':prefix}
        if marker:p['marker']=marker
        r=requests.get(C,params=p,timeout=120);r.raise_for_status();root=ET.fromstring(r.content)
        for b in root.findall('./Blobs/Blob'):
            prop=b.find('Properties');out.append({'name':b.findtext('Name') or '','etag':((prop.findtext('Etag') or '').strip('"') if prop is not None else ''),'last_modified':prop.findtext('Last-Modified') if prop is not None else '','content_length':prop.findtext('Content-Length') if prop is not None else ''})
        marker=root.findtext('NextMarker') or ''
        if not marker:break
    return out
def yyyymm(n):
    s=Path(n).name[:6];return s if s.isdigit() else None
def main():
    prev=json.loads(META.read_text()) if META.exists() else {}; prev_etag={x['blob_name']:x.get('source_etag','') for d in prev.get('datasets',{}).values() for x in d.get('files',[])}; meta={'source':'New Zealand Electricity Authority Azure Blob Storage','datasets':{}}
    for key,cfg in DATASETS.items():
        selected=[b for b in list_blobs(cfg['prefix']) if yyyymm(b['name']) and yyyymm(b['name'])>=cfg['start'] and b['name'].endswith(cfg['suffix'])]; selected.sort(key=lambda b:b['name']); files=[]
        for b in selected:
            out=cfg['out']/Path(b['name']).name; out.parent.mkdir(parents=True,exist_ok=True)
            if not(out.exists() and prev_etag.get(b['name'])==b['etag']):
                u=f"{C}/{quote(b['name'],safe='/()')}";r=requests.get(u,timeout=120);r.raise_for_status();out.write_bytes(r.content);print('Downloaded',out)
            files.append({'blob_name':b['name'],'local_file':str(out),'source_last_modified':b['last_modified'],'source_content_length':b['content_length'],'source_etag':b['etag']})
        meta['datasets'][key]={'prefix':cfg['prefix'],'start_yyyymm':cfg['start'],'file_count':len(files),'files':files};print(key,len(files),'files')
    META.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(meta,indent=2)+'\n'
    if not META.exists() or META.read_text()!=text:META.write_text(text)
    print('Checked at',datetime.now(timezone.utc).isoformat())
if __name__=='__main__':main()
