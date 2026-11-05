#!/usr/bin/env python3
"""Stage56 research-only: restore race metadata by the exact Stage2 deterministic race ID.
No writes to source DB or live Model D. Outputs review CSV and JSON.
"""
import argparse,csv,json,re,sqlite3,unicodedata,zipfile,io,collections
from pathlib import Path

def normhead(s):return re.sub(r'[^a-z0-9]+','_',str(s).casefold()).strip('_')
def clean(s):return re.sub(r'[^a-zA-Z0-9_-]','',str(s))[:90]
def dateparse(s):
 from datetime import datetime
 s=str(s or '').strip().split('T')[0].split(' ')[0]
 for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%Y/%m/%d','%d.%m.%Y'):
  try:return datetime.strptime(s,fmt).date().isoformat()
  except ValueError:pass
 return ''
def classify(row):
 s=' '.join(str(row.get(k) or '') for k in ('race_name','type','class','pattern','rating_band')).lower()
 code=('chase' if re.search(r'\b(chase|steeplechase)\b',s) else 'hurdle' if 'hurdle' in s else 'nh_flat' if re.search(r'\b(bumper|national hunt flat)\b',s) else 'flat')
 cond=('handicap' if re.search(r'\b(handicap|hcap|h/cap)\b',s) else 'novice' if re.search(r'\bnovices?\b',s) else 'maiden' if 'maiden' in s else 'beginners_chase' if 'beginner' in s and code=='chase' else 'selling' if 'sell' in s else 'claiming' if 'claim' in s else 'unknown')
 grade=re.search(r'\b(group|grade|gr|grp)\s*([123])\b',s)
 quality=('group_'+grade[2] if grade and code=='flat' else 'grade_'+grade[2] if grade else 'listed' if 'listed' in s else 'class_'+re.search(r'\bclass\s*([1-7])\b',s)[1] if re.search(r'\bclass\s*([1-7])\b',s) else 'unknown')
 return code,cond,quality

def source_files(root):
 for p in root.rglob('*'):
  if p.suffix.lower()=='.csv':
   yield str(p),lambda p=p:open(p,encoding='utf-8-sig',errors='replace',newline='')
  elif p.suffix.lower()=='.zip':
   try:
    with zipfile.ZipFile(p) as z:names=[n for n in z.namelist() if n.lower().endswith('.csv')]
    for n in names:
     def opener(p=p,n=n):
      z=zipfile.ZipFile(p)
      return io.TextIOWrapper(z.open(n),encoding='utf-8-sig',errors='replace')
     yield str(p)+'!'+n,opener
   except (zipfile.BadZipFile,OSError):pass

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',type=Path,required=True);ap.add_argument('--source',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 cx=sqlite3.connect('file:'+str(a.db.resolve())+'?mode=ro',uri=True)
 existing={r[0] for r in cx.execute('select id from historical_races')};cx.close()
 matches={};conflicts=set();audit=collections.Counter();files=[]
 for name,opener in source_files(a.source):
  try:
   with opener() as f:
    rd=csv.DictReader(f);fields={normhead(c):c for c in (rd.fieldnames or [])}
    if not all(k in fields for k in ('date','course','race_name')) or not any(k in fields for k in ('race_id','race_no','off')):continue
    files.append({'source':name,'columns':list(fields)})
    for raw in rd:
     audit['source_rows']+=1
     r={k:raw.get(v,'') for k,v in fields.items()}
     d=dateparse(r.get('date'));course=r.get('course','')
     rid=r.get('race_id') or ('R'+r['race_no'] if r.get('race_no') else '') or ('T'+r['off'] if r.get('off') else '')
     if not (d and course and rid):continue
     # EXACT same deterministic ID expression used by import_historical_ukie.py.
     key='kaggle-deltaromeo-ukie:'+d+':'+normhead(course)+':'+clean(rid)
     if key not in existing:continue
     audit['matched_source_runner_rows']+=1
     metadata={k:str(r.get(k) or '').strip() for k in ('race_name','race_id','off','type','class','pattern','rating_band','age_band','sex_rest','dist','going','course','date')}
     if not metadata['race_name']:continue
     identity=tuple(metadata[k] for k in ('race_name','race_id','off','type','class','pattern','rating_band','age_band','sex_rest','dist','going'))
     if key in matches and matches[key][0]!=identity:conflicts.add(key)
     else:matches[key]=(identity,metadata)
  except (OSError,UnicodeError,csv.Error) as e:files.append({'source':name,'error':str(e)})
 cols=['staged_race_id','race_date','course','original_race_id','off_time','race_name','race_type','race_class','pattern','rating_band','age_band','sex_restriction','distance','going','code','conditions','quality','match_status']
 out=a.out/'stage56_verified_race_metadata.csv'
 with out.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
  for key,(_,r) in sorted(matches.items()):
   if key in conflicts:continue
   code,conditions,quality=classify(r)
   w.writerow(dict(staged_race_id=key,race_date=r['date'],course=r['course'],original_race_id=r['race_id'],off_time=r['off'],race_name=r['race_name'],race_type=r['type'],race_class=r['class'],pattern=r['pattern'],rating_band=r['rating_band'],age_band=r['age_band'],sex_restriction=r['sex_rest'],distance=r['dist'],going=r['going'],code=code,conditions=conditions,quality=quality,match_status='EXACT_STAGE2_SOURCE_ID'))
 report={'status':'RESEARCH_ONLY','live_model_unchanged':True,'staged_races':len(existing),'matched_distinct_races':len(matches),'conflicting_metadata_races':len(conflicts),'verified_race_metadata_rows':len(matches)-len(conflicts),'unmatched_staged_races':len(existing)-len(matches),'source_files':files,'audit':dict(audit),'limitations':['Deterministic staged ID validates source race identity, not recurring event identity across years.','Race-condition classification is provisional and requires validation; unknown is not equivalent to open/conditions.','Output is a separate CSV; no database or live prediction changes.']}
 (a.out/'stage56_report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='source_files'},indent=2))
if __name__=='__main__':main()
