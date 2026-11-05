#!/usr/bin/env python3
"""Read-only discovery of original named race records and lost race identity metadata."""
import argparse,csv,json,zipfile,sqlite3,re,collections,io
from pathlib import Path

def normal(s):return re.sub(r'[^a-z0-9]+','_',str(s).lower()).strip('_')
def scan_csv(f,origin):
 try:
  sample=f.readline().decode('utf-8-sig','replace')
  dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|')
  fields=next(csv.reader([sample],dialect=dialect))
  fields=[normal(x) for x in fields]
  names=[x for x in fields if any(y in x for y in ('race_name','race_title','race_description','event_name','racecard_name'))]
  ids=[x for x in fields if x in ('race_id','raceid','race_number','race_no','off','off_time','race_time','time')]
  return dict(file=origin,columns=fields[:90],name_columns=names,identity_columns=ids,has_names=bool(names))
 except Exception as e:return dict(file=origin,error=str(e)[:180])
def main(a):
 a.output.mkdir(parents=True,exist_ok=True)
 found=[];count=0
 for p in a.source.rglob('*'):
  if not p.is_file():continue
  count+=1
  if p.suffix.lower() in ('.csv','.tsv'):
   with p.open('rb') as f:found.append(scan_csv(f,str(p)))
  elif p.suffix.lower()=='.zip':
   try:
    with zipfile.ZipFile(p) as z:
     for i in z.infolist():
      if i.filename.lower().endswith(('.csv','.tsv')):
       with z.open(i) as f:found.append(scan_csv(f,str(p)+'!'+i.filename))
   except Exception as e:found.append(dict(file=str(p),error=str(e)))
  elif p.suffix.lower() in ('.parquet','.json','.jsonl','.sqlite','.db'):
   found.append(dict(file=str(p),format=p.suffix,requires_format_specific_inspection=True))
 dbinfo={}
 if a.db and a.db.exists():
  cx=sqlite3.connect('file:'+str(a.db.resolve())+'?mode=ro',uri=True)
  tables=[r[0] for r in cx.execute("select name from sqlite_master where type='table'")]
  dbinfo={'tables':{t:[r[1] for r in cx.execute('pragma table_info("'+t.replace('"','')+'")')] for t in tables},'race_id_examples':[r[0] for r in cx.execute('select id from historical_races limit 5')] if 'historical_races' in tables else []}
  cx.close()
 report={'status':'RESEARCH_ONLY','live_model_unchanged':True,'files_seen':count,'csv_sources':len(found),'sources_with_race_name_columns':sum(bool(z.get('has_names')) for z in found),'sources':found,'staging_database':dbinfo,'conclusion':'Race names cannot be reconstructed from class/course/date alone. Locate original named source, earlier artifact or alternate licensed historical results feed.'}
 (a.output/'stage55_source_discovery.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 with (a.output/'stage55_source_inventory.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['file','has_names','name_columns','identity_columns','format']);
  for x in found:w.writerow([x.get('file'),x.get('has_names'),';'.join(x.get('name_columns',[])),';'.join(x.get('identity_columns',[])),x.get('format','csv')])
 print(json.dumps({k:v for k,v in report.items() if k not in ('sources','staging_database')},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--db',type=Path);p.add_argument('--output',type=Path,default=Path('reports/stage55'));main(p.parse_args())
