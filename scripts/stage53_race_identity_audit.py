#!/usr/bin/env python3
"""Stage53 research-only historical recurring-race identity audit.
Never asserts exact identity from conditions alone. No app/model writes.
"""
import argparse,csv,json,re,sqlite3,unicodedata
from collections import defaultdict,Counter
from pathlib import Path

STOP={'the','a','an','of','and','at','in','for','stakes','handicap','race','sponsored','by','bet','novices','novice','hurdle','chase','flat','group','grade','listed','class'}
SPONSORS={'sky','coral','betfair','paddy','power','ladbrokes','unibet','betfred','william','hill','jcb','ryanair','ballymore','turners','brown','advisory','sporting','life','itv','bet365'}
def norm(s):
 s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
 return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9]+',' ',s)).strip()
def titlekey(s):
 words=[w for w in norm(s).split() if w not in STOP|SPONSORS and not w.isdigit()]
 return ' '.join(words)
def kind(s):
 s=norm(s)
 for k,words in [('chase',('chase','steeple')),('hurdle',('hurdle',)),('bumper',('bumper','national hunt flat')),('handicap',('handicap',)),('flat',('flat',))]:
  if any(x in s for x in words):return k
 return s or 'unknown'
def main(a):
 db=sqlite3.connect(f'file:{a.db.resolve()}?mode=ro',uri=True)
 cols={r[1] for r in db.execute('pragma table_info(historical_races)')}
 needed={'id','race_date','course','race_type','distance_f','race_class'}
 if not needed<=cols:raise SystemExit('Missing columns: '+str(needed-cols))
 namecol=next((c for c in ('race_name','name','title','event_name','race_title') if c in cols),None)
 extras=[c for c in ('surface','going') if c in cols]
 select=['id','race_date','course','race_type','distance_f','race_class']+([namecol] if namecol else [])+extras
 rows=db.execute('select '+','.join('"'+x+'"' for x in select)+' from historical_races where race_date < ? order by race_date,id',(a.end,)).fetchall()
 db.close()
 records=[];groups=defaultdict(list)
 for raw in rows:
  z=dict(zip(select,raw));day=str(z['race_date'])[:10];course=norm(z['course']);k=kind(z['race_type']);cl=norm(z['race_class']);dist=z['distance_f']
  try:dist=float(dist)
  except (TypeError,ValueError):dist=None
  name=z.get(namecol) if namecol else None;key=titlekey(name) if name else ''
  # Candidate key is NOT proof of recurring identity; it is explicitly flagged for review.
  if key and len(key)>=5:
   match_type='NAME_CANDIDATE_UNVERIFIED';gkey=('name',course,k,key)
  else:
   match_type='CONDITIONS_ONLY_NOT_EXACT';gkey=('conditions',course,k,round(dist*2)/2 if dist is not None else 'unknown',cl)
  z.update(year=day[:4],course_normalized=course,kind=k,name_key=key,match_type=match_type,group_key='|'.join(map(str,gkey)))
  records.append(z);groups[gkey].append(z)
 out=a.output;out.mkdir(parents=True,exist_ok=True)
 candidates=[];mapping=[];summary=Counter();years=defaultdict(set)
 for key,rr in groups.items():
  byyear=Counter(x['year'] for x in rr);distinct=len(byyear)
  if distinct<2:continue
  isname=key[0]=='name'
  dists=[float(x['distance_f']) for x in rr if x['distance_f'] is not None]
  dist_range=round(max(dists)-min(dists),2) if dists else None
  collision=sum(n-1 for n in byyear.values() if n>1)
  status=('REVIEW_NAME_MATCH' if isname and collision==0 and (dist_range is None or dist_range<=1.5) else 'AMBIGUOUS_REVIEW') if isname else 'SIMILAR_CONDITIONS_ONLY'
  groupid='candidate_'+str(len(candidates)+1).zfill(6)
  candidates.append(dict(candidate_id=groupid,match_status=status,course=rr[0]['course'],race_type=rr[0]['race_type'],race_name_example=rr[0].get(namecol,'') if namecol else '',normalized_name_key=rr[0]['name_key'],editions=len(rr),distinct_years=distinct,first_year=min(byyear),last_year=max(byyear),same_year_collisions=collision,distance_range_f=dist_range,race_ids=';'.join(str(x['id']) for x in rr[:20])))
  summary[status]+=1
  for x in rr:
   mapping.append(dict(race_id=x['id'],race_date=x['race_date'],candidate_id=groupid,match_status=status,recurring_event_id='',race_name=x.get(namecol,'') if namecol else ''))
 summary.update(total_races=len(records),candidate_groups=len(candidates),candidate_races=len(mapping),exact_verified_events=0,has_race_name_column=int(bool(namecol)),unmatched_races=len(records)-len(mapping))
 def write(name,data,fields):
  with (out/name).open('w',newline='',encoding='utf-8') as f:
   w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)
 write('stage53_race_identity_candidates.csv',candidates,['candidate_id','match_status','course','race_type','race_name_example','normalized_name_key','editions','distinct_years','first_year','last_year','same_year_collisions','distance_range_f','race_ids'])
 write('stage53_mapping_review.csv',mapping,['race_id','race_date','candidate_id','match_status','recurring_event_id','race_name'])
 (out/'stage53_audit.json').write_text(json.dumps(dict(status='RESEARCH_ONLY',live_model_unchanged=True,source_columns=sorted(cols),race_name_column=namecol,summary=dict(summary),limitations=['No recurring event is automatically certified as exact. Human/independent verification is required.','When source lacks race names, course/type/distance/class can identify comparable conditions only, never the same named annual event.','Same-year collisions and distance changes are flagged for review.','Source date availability may not reach 1988; coverage must be checked.','The mapping_review recurring_event_id column is intentionally blank; Stage52 must not treat candidates as verified.']),indent=2)+'\n')
 print(json.dumps(dict(summary=dict(summary),race_name_column=namecol),indent=2),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--end',default='2026-11-06');p.add_argument('--output',type=Path,default=Path('reports/stage53'));main(p.parse_args())
