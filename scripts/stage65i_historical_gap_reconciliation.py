#!/usr/bin/env python3
"""Stage65I: independent read-only reconciliation of historical archive vs repository results.
No backfill, model training, file mutation, or promotion. Source matches are candidates only.
"""
import argparse,collections,csv,datetime as dt,json,pathlib,re,sqlite3

def canon(x):
 x=re.sub(r'\([^)]*\)','',str(x or ''))
 return re.sub(r'[^a-z0-9]','',x.casefold())
def valid_day(x):
 try:
  d=dt.date.fromisoformat(str(x)[:10]);return d if 1988<=d.year<=2026 else None
 except ValueError:return None

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--results',default='data/race-results.json');p.add_argument('--out',default='reports/stage65i');p.add_argument('--start',default='2015-01-01');p.add_argument('--end',default='2026-11-06');a=p.parse_args()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 db=pathlib.Path(a.db);results=pathlib.Path(a.results)
 if not db.is_file():raise SystemExit('Missing historical archive')
 cx=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True);cx.execute('pragma query_only=on')
 if cx.execute('pragma quick_check').fetchone()[0]!='ok':raise SystemExit('Archive integrity failure')
 cols={r[1] for r in cx.execute('pragma table_info(historical_runs)')}
 if not {'race_date','course','race_id','horse','finish','race_type','sp','race_class','rpr','ts'}<=cols:raise SystemExit('Unexpected Stage65E schema')
 # Distinct historical race IDs are not treated as recurring-event IDs.
 sql='''SELECT race_date,course,race_id,count(*) AS n,
 sum(CASE WHEN trim(coalesce(rpr,''))<>'' THEN 1 ELSE 0 END) AS rpr_n,
 sum(CASE WHEN trim(coalesce(ts,''))<>'' THEN 1 ELSE 0 END) AS ts_n,
 sum(CASE WHEN trim(coalesce(race_class,''))<>'' THEN 1 ELSE 0 END) AS class_n,
 sum(CASE WHEN trim(coalesce(sp,''))<>'' THEN 1 ELSE 0 END) AS sp_n
 FROM historical_runs WHERE race_date BETWEEN ? AND ? GROUP BY race_date,course,race_id'''
 source_races=[];by_day_course=collections.defaultdict(list);monthly=collections.defaultdict(lambda:collections.Counter())
 for date,course,rid,n,rpr,ts,klass,sp in cx.execute(sql,(a.start,a.end)):
  if not valid_day(date) or not course or not rid:continue
  item={'date':date,'course':course,'race_id':str(rid),'runners':n,'rpr':rpr,'ts':ts,'race_class':klass,'sp':sp}
  source_races.append(item);by_day_course[(date,canon(course))].append(item)
  m=monthly[date[:7]];m['races']+=1;m['runners']+=n;m['rpr']+=rpr;m['ts']+=ts;m['class']+=klass;m['sp']+=sp
 latest=max((x['date'] for x in source_races),default=None)
 # A result is considered a definite race match only when exactly one historical race
 # at the same course/day has an exact normalized runner set. Otherwise unresolved.
 repo=json.loads(results.read_text()) if results.is_file() else {}
 candidates=[];status_counts=collections.Counter();by_month_status=collections.Counter()
 for r in repo.get('races',[]):
  if not isinstance(r,dict) or r.get('status')!='final':continue
  day=str(r.get('date') or '')[:10];course=str(r.get('course') or '')
  if not valid_day(day) or not a.start<=day<=a.end or not course:continue
  runners={canon(z.get('horse')) for z in r.get('runners',[]) if isinstance(z,dict) and z.get('horse') and str(z.get('status') or '').casefold() not in ('non-runner','non runner','nr','withdrawn')}
  pool=by_day_course.get((day,canon(course)),[])
  if not pool:state='NO_SOURCE_COURSE_DAY'
  elif len(pool)==1:state='COURSE_DAY_ONLY_UNVERIFIED'
  else:state='MULTIPLE_RACES_UNRESOLVED'
  candidates.append({'date':day,'course':course,'off_dt':r.get('off_dt') or '', 'repo_runner_count':len(runners),'historical_races_same_course_day':len(pool),'state':state,'after_source_latest':bool(latest and day>latest)})
  status_counts[state]+=1;by_month_status[(day[:7],state)]+=1
 # Narrow second pass: inspect runner identities only for course-days with source races.
 # This avoids falsely matching by time when historical source lacks an off-time column.
 source_horses=collections.defaultdict(set)
 target={(x['date'],canon(x['course'])) for x in candidates if x['historical_races_same_course_day']>0}
 for date,course,rid,horse in cx.execute('SELECT race_date,course,race_id,horse FROM historical_runs WHERE race_date BETWEEN ? AND ?',(a.start,a.end)):
  key=(date,canon(course))
  if key in target and horse:source_horses[(date,canon(course),str(rid))].add(canon(horse))
 for x in candidates:
  if not x['historical_races_same_course_day']:continue
  rr=[r for r in repo.get('races',[]) if isinstance(r,dict) and str(r.get('date') or '')[:10]==x['date'] and str(r.get('course') or '')==x['course'] and str(r.get('off_dt') or '')==x['off_dt'] and r.get('status')=='final']
  if len(rr)!=1:continue
  horses={canon(z.get('horse')) for z in rr[0].get('runners',[]) if isinstance(z,dict) and z.get('horse') and str(z.get('status') or '').casefold() not in ('non-runner','non runner','nr','withdrawn')}
  if not horses:continue
  matches=[v for v in by_day_course[(x['date'],canon(x['course']))] if source_horses[(v['date'],canon(v['course']),v['race_id'])]==horses]
  if len(matches)==1:x['state']='EXACT_RUNNER_SET_MATCH';x['matched_source_race_id']=matches[0]['race_id']
  elif len(matches)>1:x['state']='AMBIGUOUS_EXACT_RUNNER_SET'
  else:x['state']='RUNNER_SET_MISMATCH_OR_UNVERIFIED'
 status_counts=collections.Counter(x['state'] for x in candidates)
 with (out/'repo_race_reconciliation.csv').open('w',newline='') as f:
  fields=['date','course','off_dt','repo_runner_count','historical_races_same_course_day','state','after_source_latest','matched_source_race_id'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(candidates)
 zero_months=[]
 with (out/'monthly_source_coverage.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['month','races','runners','rpr_pct','ts_pct','class_pct','sp_pct'])
  first=dt.date.fromisoformat(a.start);last=dt.date.fromisoformat(a.end);y,m=first.year,first.month
  while (y,m)<=(last.year,last.month):
   key=f'{y:04d}-{m:02d}';z=monthly[key];n=z['runners'];
   if not z['races']:zero_months.append(key)
   w.writerow([key,z['races'],n,*[round(100*z[k]/n,2) if n else None for k in ('rpr','ts','class','sp')]])
   m+=1
   if m==13:y,m=y+1,1
 # Race class may be absent by convention; report gaps, never infer values.
 report={'status':'RESEARCH_ONLY_PROMOTION_BLOCKED','source_latest_date':latest,'source_races_2015_onward':len(source_races),'repository_final_races_checked':len(candidates),'reconciliation_states':dict(status_counts),'repo_final_races_after_source_latest':sum(x['after_source_latest'] for x in candidates),'months_with_zero_source_races':zero_months,'notes':['Repository results are not an independent external completeness census','An absent historical match is not proof a race never occurred','Exact runner set comparison excludes declared non-runners in repository data; historical non-runner conventions may differ','No inferred race IDs, no imputation, no edits to source or live Model D','A full 2015+ external course/day race calendar is still required to establish true completeness']}
 (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));cx.close()
if __name__=='__main__':main()
