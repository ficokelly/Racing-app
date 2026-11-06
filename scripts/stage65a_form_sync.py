#!/usr/bin/env python3
"""Stage65A: lossless source snapshot, coverage audit and safe append-only import.
No changes to live predictions, frozen models or original historical SQLite.
"""
import argparse,collections,datetime as dt,hashlib,json,re,sqlite3
from pathlib import Path

def norm(s):return re.sub(r'[^a-z0-9]','',str(s or '').casefold())
def main():
 p=argparse.ArgumentParser();p.add_argument('--results',default='data/race-results.json');p.add_argument('--out',default='reports/stage65a');p.add_argument('--since',default='2026-10-08');p.add_argument('--until',default=None);p.add_argument('--base-db',default=None);a=p.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 until=dt.date.fromisoformat(a.until) if a.until else dt.date.today()-dt.timedelta(days=1)
 since=dt.date.fromisoformat(a.since)
 if since>until:raise SystemExit('Invalid date range')
 raw=Path(a.results).read_bytes();payload=json.loads(raw)
 if not isinstance(payload.get('races'),list):raise SystemExit('Invalid results format')
 races=payload['races']; days=collections.defaultdict(list); rejected=[]; accepted=[]
 for r in races:
  day=str(r.get('date') or '')[:10]
  try:d=dt.date.fromisoformat(day)
  except ValueError:rejected.append({'reason':'invalid_date','race':str(r.get('course'))});continue
  if not since<=d<=until:continue
  if r.get('status')!='final':rejected.append({'reason':'not_final','date':day,'course':r.get('course')});continue
  runners=r.get('runners')
  if not isinstance(runners,list) or len(runners)<2:rejected.append({'reason':'insufficient_runners','date':day,'course':r.get('course')});continue
  off=str(r.get('off_dt') or r.get('race_time') or '')
  course=str(r.get('course') or '').strip()
  if not course or not off:rejected.append({'reason':'missing_race_identity','date':day,'course':course});continue
  key=f'{day}|{norm(course)}|{off}'
  if len({norm(x.get('horse')) for x in runners})!=len(runners):rejected.append({'reason':'duplicate_horse','race':key});continue
  if sum(str(x.get('position'))=='1' for x in runners)!=1:rejected.append({'reason':'missing_or_multiple_winner','race':key});continue
  accepted.append((key,r));days[day].append(key)
 # Preserve immutable daily source snapshots, never overwrite different data silently.
 for day in sorted(days):
  rows=[r for k,r in accepted if k.startswith(day+'|')]
  content=json.dumps({'date':day,'source':'repository:data/race-results.json','races':rows},sort_keys=True,ensure_ascii=False,indent=2)+'\n'
  digest=hashlib.sha256(content.encode()).hexdigest()[:12]
  path=out/'snapshots'/day/f'{digest}.json';path.parent.mkdir(parents=True,exist_ok=True)
  if not path.exists():path.write_text(content,encoding='utf-8')
 # Source may be rolling; do not treat days absent from file as proof of no racing.
 dates=[(since+dt.timedelta(days=i)).isoformat() for i in range((until-since).days+1)]
 gaps=[x for x in dates if x not in days]
 summary={'status':'INCOMPLETE' if gaps or rejected else 'SOURCE_PRESENT_NOT_FULLY_VERIFIED',
          'source':'data/race-results.json','source_sha256':hashlib.sha256(raw).hexdigest(),
          'since':str(since),'until':str(until),'source_total_races':len(races),
          'accepted_races_in_window':len(accepted),'source_dates_present':sorted(days),
          'missing_dates_in_source':gaps,'rejected_races':rejected,
          'limitations':['Source presence does not prove full daily race coverage.',
           'No external historical API backfill is attempted; missing dates require verified provider data.',
           'SP and finishing position are not beaten lengths, weights, ratings or in-running comments.',
           'No live database or frozen model is modified.']}
 if a.base_db:
  db=Path(a.base_db)
  if db.exists():
   cx=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
   try:summary['historical_db']={'min_date':cx.execute('select min(race_date) from historical_races').fetchone()[0], 'max_date':cx.execute('select max(race_date) from historical_races').fetchone()[0], 'races':cx.execute('select count(*) from historical_races').fetchone()[0]}
   finally:cx.close()
  else:summary['historical_db_error']='Supplied database not found'
 (out/'stage65a_coverage.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps({k:v for k,v in summary.items() if k not in ('rejected_races','missing_dates_in_source')},indent=2))
 print('Missing dates:',len(gaps),'Rejected races:',len(rejected))
 # No failure: coverage report is a successful audit, not a claim of completeness.
if __name__=='__main__':main()
