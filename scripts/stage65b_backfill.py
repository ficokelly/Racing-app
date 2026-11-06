#!/usr/bin/env python3
"""Stage65B: resumable GB/IRE results backfill and evidence audit.
Requires HORSE_RACING_API_KEY for historical date access; never edits production D.
API documentation: https://horseracingapi.org/documentation/results-date
"""
import argparse, datetime as dt, hashlib, json, os, sqlite3, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

BASE='https://api.horseracingapi.org/v1/results/'

def fetch(day,country,key):
    meetings=[];page=1
    while True:
        url=BASE+day+'?'+urllib.parse.urlencode({'country':country,'page':page,'per_page':25})
        req=urllib.request.Request(url,headers={'X-API-Key':key,'Accept':'application/json','User-Agent':'RacingHotPots-Stage65B/1.0'})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req,timeout=40) as response: body=json.load(response)
                break
            except urllib.error.HTTPError as e:
                if e.code in (401,403):raise RuntimeError(f'{country} {day}: API plan/key does not allow historical dates (HTTP {e.code})') from e
                if e.code not in (429,500,502,503,504) or attempt==4:raise
                time.sleep(min(60,3*(attempt+1)))
        if not isinstance(body.get('data'),list):raise ValueError('Unexpected API response shape')
        meetings+=body['data'];meta=body.get('meta') or {}
        nxt=meta.get('next_page')
        if nxt is None:
            total=meta.get('total_pages')
            nxt=page+1 if total is not None and page<int(total) else None
        if not nxt:break
        if int(nxt)<=page or page>=100:raise RuntimeError('Pagination did not converge')
        page=int(nxt);time.sleep(1.1)
    return meetings

def init(cx):
    cx.executescript('''
    CREATE TABLE IF NOT EXISTS source_races(
      provider TEXT NOT NULL, race_id TEXT NOT NULL, race_date TEXT NOT NULL,
      country TEXT NOT NULL, course TEXT, off_time TEXT, race_name TEXT,
      race_type TEXT, race_class TEXT, handicap INTEGER, distance TEXT,
      going TEXT, surface TEXT, runner_count INTEGER, data_json TEXT NOT NULL,
      PRIMARY KEY(provider,race_id));
    CREATE TABLE IF NOT EXISTS source_runs(
      provider TEXT NOT NULL, race_id TEXT NOT NULL, horse_id TEXT NOT NULL,
      horse TEXT, finish_pos INTEGER, status TEXT, distance_beaten TEXT,
      weight TEXT, trainer_id TEXT, trainer TEXT, jockey_id TEXT, jockey TEXT,
      draw INTEGER, age INTEGER, data_json TEXT NOT NULL,
      PRIMARY KEY(provider,race_id,horse_id));
    CREATE TABLE IF NOT EXISTS day_audit(
      provider TEXT NOT NULL, race_date TEXT NOT NULL, country TEXT NOT NULL,
      meetings INTEGER, races INTEGER, runners INTEGER, invalid_races INTEGER,
      fetched_utc TEXT NOT NULL, status TEXT NOT NULL,
      PRIMARY KEY(provider,race_date,country));
    CREATE INDEX IF NOT EXISTS source_races_day ON source_races(race_date,country);
    CREATE INDEX IF NOT EXISTS source_runs_horse ON source_runs(horse_id);
    ''')

def ingest(cx,day,country,meetings):
    races=0;runners=0;invalid=0;ids=set()
    for m in meetings:
        for r in m.get('races',[]):
            rid=str(r.get('race_id') or '')
            if not rid or rid in ids or r.get('result_available') is not True or not isinstance(r.get('finishing_order'),list):
                invalid+=1;continue
            ids.add(rid)
            finish=r['finishing_order']
            if len(finish)<2 or sum(x.get('position')==1 for x in finish)!=1:
                invalid+=1;continue
            cx.execute('''INSERT INTO source_races VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider,race_id) DO UPDATE SET
               data_json=excluded.data_json,runner_count=excluded.runner_count,
               going=excluded.going,race_class=excluded.race_class''',(
               'horseracingapi.org',rid,day,country,r.get('course') or m.get('course'),
               r.get('off_time') or r.get('time'),r.get('name'),r.get('race_type'),
               r.get('class'),int(bool(r.get('handicap'))),r.get('distance'),
               r.get('going') or m.get('going'),r.get('surface') or m.get('surface'),
               len(finish),json.dumps(r,ensure_ascii=False)))
            for i,x in enumerate(finish):
                horse_id=str(x.get('horse_id') or '')
                if not horse_id:horse_id='unknown:'+str(i)+':'+str(x.get('horse') or '')
                cx.execute('''INSERT INTO source_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider,race_id,horse_id) DO UPDATE SET
                   data_json=excluded.data_json,finish_pos=excluded.finish_pos,
                   distance_beaten=excluded.distance_beaten''',(
                   'horseracingapi.org',rid,horse_id,x.get('horse'),x.get('position') if isinstance(x.get('position'),int) else None,
                   x.get('status'),x.get('distance_beaten'),x.get('weight'),
                   x.get('trainer_id'),x.get('trainer'),x.get('jockey_id'),x.get('jockey'),
                   x.get('draw'),x.get('age'),json.dumps(x,ensure_ascii=False)))
            races+=1;runners+=len(finish)
    status='PRESENT_UNVERIFIED' if races and not invalid else 'NEEDS_REVIEW'
    cx.execute('INSERT OR REPLACE INTO day_audit VALUES (?,?,?,?,?,?,?,?,?)',(
      'horseracingapi.org',day,country,len(meetings),races,runners,invalid,
      dt.datetime.now(dt.timezone.utc).isoformat(),status))
    return {'meetings':len(meetings),'races':races,'runners':runners,'invalid':invalid,'status':status}

def main():
    p=argparse.ArgumentParser();p.add_argument('--since',default='2026-10-08');p.add_argument('--until',default=None)
    p.add_argument('--db',default='reports/stage65b/form_results.sqlite');p.add_argument('--out',default='reports/stage65b')
    p.add_argument('--refresh-days',type=int,default=3);a=p.parse_args()
    key=os.environ.get('HORSE_RACING_API_KEY','')
    if not key:raise SystemExit('HORSE_RACING_API_KEY secret is missing: historical backfill cannot proceed')
    since=dt.date.fromisoformat(a.since);until=dt.date.fromisoformat(a.until) if a.until else dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=1)
    if until<since:raise SystemExit('Invalid dates')
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True);db=Path(a.db);db.parent.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect(db);init(cx);cx.commit()
    rows=[];errors=[]
    for i in range((until-since).days+1):
        d=since+dt.timedelta(days=i);day=d.isoformat()
        for country in ('GB','IRE'):
            old=cx.execute('SELECT status FROM day_audit WHERE race_date=? AND country=?',(day,country)).fetchone()
            if old and old[0]=='PRESENT_UNVERIFIED' and (until-d).days>=a.refresh_days:continue
            try:
                meetings=fetch(day,country,key)
                with cx:result=ingest(cx,day,country,meetings)
                rows.append({'date':day,'country':country,**result})
                print(day,country,result,flush=True)
            except Exception as e:
                errors.append({'date':day,'country':country,'error':str(e)})
                print('ERROR',day,country,str(e),flush=True)
                if 'plan/key' in str(e):break
        if errors and 'plan/key' in errors[-1]['error']:break
    expected=(until-since).days+1
    audit=cx.execute('SELECT race_date,country,meetings,races,runners,invalid_races,status FROM day_audit WHERE race_date BETWEEN ? AND ? ORDER BY race_date,country',(str(since),str(until))).fetchall()
    bykey={(x[0],x[1]):x for x in audit};missing=[]
    for i in range(expected):
        day=(since+dt.timedelta(days=i)).isoformat()
        for country in ('GB','IRE'):
            if (day,country) not in bykey or bykey[(day,country)][-1]!='PRESENT_UNVERIFIED':missing.append({'date':day,'country':country})
    coverage={'status':'INCOMPLETE' if missing or errors else 'DATES_PRESENT_NOT_FULLY_VERIFIED',
      'since':str(since),'until':str(until),'source':'horseracingapi.org','fetched':rows,'errors':errors,
      'missing_or_unverified_date_countries':missing,'days_checked':len(audit),
      'total_races':sum(x[3] for x in audit),'total_runners':sum(x[4] for x in audit),
      'limitations':['Date coverage alone does not verify every scheduled race; reconcile against independent meeting calendars.',
      'This API does not provide SP, official rating or all form fields; never substitute null with invented data.',
      'Provider race IDs are not automatically the same as historical_races IDs: identity mapping is required before live D ingestion.',
      'The research database is separate from the live model and must not be swapped into production without verification.']}
    (out/'stage65b_coverage.json').write_text(json.dumps(coverage,indent=2)+'\n')
    cx.close()
    print('STATUS:',coverage['status'],'MISSING:',len(missing),'ERRORS:',len(errors))
    if missing or errors:raise SystemExit(2)
if __name__=='__main__':main()
