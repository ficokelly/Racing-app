#!/usr/bin/env python3
"""Research-only Stage54: recover source race names, classify conditions, audit identity.
Never modifies historical DB or live Model D. Ambiguous source joins are not accepted.
"""
import argparse,csv,json,re,sqlite3,unicodedata,zipfile,io
from pathlib import Path
from collections import defaultdict,Counter

def norm(s):
 s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
 return re.sub(r'\s+',' ',re.sub('[^a-z0-9]+',' ',s)).strip()
def header(s):return norm(s).replace(' ','_')
def pick(row,*keys):return next((row[k] for k in keys if k in row and str(row[k]).strip()),'')
def date(s):
 s=str(s).strip().split('T')[0].split(' ')[0]
 from datetime import datetime
 for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%Y/%m/%d'):
  try:return datetime.strptime(s,fmt).strftime('%Y-%m-%d')
  except ValueError:pass
 return ''
def classify(name,typ,klass,surface):
 s=norm(' '.join(str(v or '') for v in (name,typ,klass)))
 discipline=('chase' if re.search(r'\b(chase|steeplechase)\b',s) else 'hurdle' if 'hurdle' in s else 'nh_flat' if re.search(r'\b(bumper|national hunt flat)\b',s) else 'flat_aw' if norm(surface) in ('aw','all weather','allweather','synthetic') else 'flat_turf')
 conditions=('handicap' if re.search(r'\b(handicap|hcap|h\s*cap)\b',s) else 'maiden' if 'maiden' in s else 'novice' if re.search(r'\b(novice|novices|novices)\b',s) else 'beginners_chase' if 'beginner' in s and discipline=='chase' else 'seller' if 'seller' in s or 'selling' in s else 'claimer' if 'claimer' in s or 'claiming' in s else 'conditions_or_open')
 grade=re.search(r'\b(?:grade|group|gr|grp)\s*([123])\b',s)
 quality=('grade_'+grade.group(1) if grade and discipline in ('hurdle','chase','nh_flat') else 'group_'+grade.group(1) if grade else 'listed' if 'listed' in s else 'class_'+re.search(r'\bclass\s*([1-7])\b',s).group(1) if re.search(r'\bclass\s*([1-7])\b',s) else 'unknown')
 return discipline,conditions,quality

def sources(root):
 for p in root.rglob('*'):
  if p.suffix.lower()=='.csv':
   try:
    with p.open(encoding='utf-8-sig',errors='replace',newline='') as f:yield str(p),csv.DictReader(f)
   except Exception as e:print('SKIP',p,e,flush=True)
  elif p.suffix.lower()=='.zip':
   try:
    with zipfile.ZipFile(p) as z:
     for name in z.namelist():
      if name.lower().endswith('.csv'):
       with z.open(name) as f:
        reader=csv.DictReader(io.TextIOWrapper(f,encoding='utf-8-sig',errors='replace'))
        yield str(p)+'!'+name,reader
   except Exception as e:print('SKIP',p,e,flush=True)

def main(a):
 out=a.output;out.mkdir(parents=True,exist_ok=True)
 con=sqlite3.connect('file:'+str(a.db.resolve())+'?mode=ro',uri=True)
 cols={r[1] for r in con.execute('pragma table_info(historical_races)')}
 req={'id','race_date','course','race_type','race_class','distance_f'}
 if not req<=cols:raise SystemExit('Missing '+str(req-cols))
 sql='select id,race_date,course,race_type,race_class,distance_f,'+('surface' if 'surface' in cols else "''")+' from historical_races'
 rows=con.execute(sql).fetchall();con.close()
 # Source match uses day/course, then distance if necessary. NEVER assert exact on date/course alone.
 index=defaultdict(list)
 for rid,day,course,typ,klass,dist,surf in rows:index[(str(day)[:10],norm(course))].append((rid,typ,klass,dist,surf))
 observed=Counter(); matches=defaultdict(set); source_examples=[]; source_files=[]
 for path,reader in sources(a.source_dir):
  fields={header(f):f for f in (reader.fieldnames or [])};source_files.append({'file':path,'columns':list(fields)})
  ncol=next((fields[k] for k in ('race_name','race_title','event_name','race_description','racecard_name','race_title_full') if k in fields),None)
  if not ncol:continue
  for original in reader:
   row={header(k):v for k,v in original.items() if k}
   name=str(row.get(header(ncol)) or '').strip()
   if not name:continue
   day=date(pick(row,'race_date','racedate','date','race_day','event_date','meeting_date','race_datetime'))
   course=norm(pick(row,'course','racecourse','venue','track','race_track','location','course_name'))
   if not day or not course:continue
   observed['named_source_rows']+=1
   ids=index.get((day,course),[])
   if not ids:observed['no_course_date_match']+=1;continue
   # Match source race identifier where the staged DB retains it; otherwise only unique course/date is safe.
   if len(ids)==1:matches[ids[0][0]].add(name);observed['unique_course_date']+=1
   else:
    observed['multiple_races_same_day_course']+=1
    if len(source_examples)<30:source_examples.append({'date':day,'course':course,'name':name,'candidate_races':len(ids)})
 result=[];groups=defaultdict(list);counts=Counter()
 for rid,day,course,typ,klass,dist,surf in rows:
  names=matches.get(rid,set());name=next(iter(names)) if len(names)==1 else ''
  disc,cond,quality=classify(name,typ,klass,surf)
  status='SOURCE_NAME_UNIQUE_COURSE_DATE' if name else 'AMBIGUOUS_SOURCE_NAMES' if len(names)>1 else 'NO_VERIFIED_NAME'
  counts[status]+=1
  result.append(dict(race_id=rid,race_date=day,course=course,race_name=name,discipline=disc,race_conditions=cond,quality=quality,distance_f=dist,match_status=status,recurring_event_id=''))
  if name:groups[(norm(course),disc,cond,quality,norm(name))].append((rid,str(day)[:4]))
 # Candidate identity groups, never promoted to verified recurring events.
 candidates=[]
 for key,items in groups.items():
  years=Counter(y for _,y in items)
  if len(years)<2:continue
  candidates.append(dict(course=key[0],discipline=key[1],conditions=key[2],quality=key[3],name_key=key[4],editions=len(items),years=len(years),same_year_collisions=sum(v-1 for v in years.values() if v>1),status='UNVERIFIED_NAME_CANDIDATE'))
 with (out/'stage54_classified_races.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(result[0]));w.writeheader();w.writerows(result)
 with (out/'stage54_recurring_candidates.csv').open('w',newline='') as f:
  fields=['course','discipline','conditions','quality','name_key','editions','years','same_year_collisions','status'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(candidates)
 report=dict(status='RESEARCH_ONLY',live_model_unchanged=True,db_races=len(rows),source_files=source_files,source_audit=dict(observed),classification=dict(counts),recurring_name_candidates=len(candidates),verified_recurring_events=0,ambiguity_examples=source_examples,limitations=['Names joined only when course and date identify a single race. Multiple races on the same card need original source race ID, off-time and/or distance to match safely.','Original race identity mapping and recovered source files must be supplied if not present in downloaded source.','Race type derived from fields available in staging; unknown conditions must not be treated as open races.','Recurring name groups are candidates, not independently verified events.','No historical trends or horse predictions produced at this stage.'])
 (out/'stage54_report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('source_files','ambiguity_examples')},indent=2),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--db',required=True,type=Path);p.add_argument('--source-dir',required=True,type=Path);p.add_argument('--output',type=Path,default=Path('reports/stage54'));main(p.parse_args())
