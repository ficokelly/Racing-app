#!/usr/bin/env python3
"""Recover original race names and identifiers from the Kaggle CSV files.
No fuzzy matches or invented race identities; output is a source-faithful mapping.
"""
import argparse,csv,json,re,collections
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--out',default='race_identity_audit');a=p.parse_args()
 root=Path(a.source);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 paths=sorted(root.rglob('*.csv'))
 required={'date','course','race_id','race_name'}
 count=collections.Counter();sources=[];seen={};conflicts=[];gold=[]
 with (out/'original_race_names.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['date','course','source_race_id','race_name','off','race_type','distance','race_class','source_file'])
  for path in paths:
   try:
    with path.open(encoding='utf-8-sig',newline='',errors='replace') as src:
     r=csv.DictReader(src)
     if not required.issubset(set(r.fieldnames or [])):
      sources.append({'file':str(path.relative_to(root)),'status':'skipped','columns':r.fieldnames});continue
     n=0
     for row in r:
      d=(row.get('date') or '').strip();course=(row.get('course') or '').strip();rid=(row.get('race_id') or '').strip();name=(row.get('race_name') or '').strip()
      if not (d and course and rid):count['missing_keys']+=1;continue
      key=(d,rid);identity=(course,name)
      if key in seen:
       if seen[key]!=identity and len(conflicts)<200:conflicts.append({'date':d,'race_id':rid,'first':seen[key],'other':identity})
       count['duplicate_runner_rows']+=1;continue
      seen[key]=identity
      w.writerow([d,course,rid,name,row.get('off',''),row.get('type',''),row.get('dist',''),row.get('class',''),str(path.relative_to(root))]);n+=1
      count['unique_races']+=1
      if name:count['named_races']+=1
      else:count['unnamed_races']+=1
      if 'CHELTENHAM' in course.upper() and re.search(r'\bGOLD\s+CUP\b',name,re.I):gold.append({'date':d,'course':course,'race_id':rid,'race_name':name})
     sources.append({'file':str(path.relative_to(root)),'status':'processed','unique_races_added':n})
   except (OSError,UnicodeError) as e:sources.append({'file':str(path),'status':'error','error':str(e)})
 (out/'cheltenham_gold_cup_candidates.json').write_text(json.dumps(gold,indent=2,ensure_ascii=False))
 report={'status':'complete','source':'Original Kaggle CSVs, no inferred matching','counts':dict(count),'files':sources,'conflicting_keys_sample':conflicts,'cheltenham_gold_cup_name_candidates':len(gold),'limitations':['Gold Cup candidates are name-based and must be verified against official annual results.','Other recurring races are not yet assigned canonical identities.','Source race IDs must be reconciled against Stage 2 SQLite IDs before joining.','Duplicate (date,race_id) rows are assumed to be runner records from the same race; conflicting names are reported.']}
 (out/'race_name_audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False))
 print(json.dumps({'counts':dict(count),'gold_cup_candidates':len(gold),'conflicts':len(conflicts)},indent=2))
if __name__=='__main__':main()
