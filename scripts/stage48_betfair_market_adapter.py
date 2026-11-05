#!/usr/bin/env python3
"""Stage48: join independently obtained Betfair daily WIN CSV files to Stage2 races.

Research-only; DOES NOT fetch third-party data, use the Betfair API, publish,
change any model, or imply that full pre-off averages were known at T-10.
Public archive documentation: https://promo.betfair.com/betfairsp/prices/

Usage:
 python scripts/stage48_betfair_market_adapter.py \
   --db historical_ukie_staging.sqlite --input-dir betfair_csv --out market_match.csv
 python scripts/stage48_betfair_market_adapter.py --self-test

The output is research data: MORNINGWAP and volumes (available post-11:00 GMT,
if traded) and final pre-off PPWAP are not interchangeable with timestamps.
Full PPWAP, BSP and results must not be exposed to a model predicting at T-10.
"""
import argparse, collections, csv, datetime as dt, math, re, sqlite3, tempfile, unicodedata
from pathlib import Path


def norm(v):
    s=unicodedata.normalize('NFKD',str(v or '').casefold()).encode('ascii','ignore').decode('ascii')
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]',' ',s)).strip()


def decimal(v):
    try:
        x=float(str(v or '').strip())
        return x if math.isfinite(x) and x>1 else None
    except (TypeError, ValueError):return None


def nonneg(v):
    try:
        x=float(str(v or '').strip())
        return x if math.isfinite(x) and x>=0 else None
    except (TypeError,ValueError):return None


def event_day(v):
    value=str(v or '').strip()
    for pattern in (r'^(\d{4}-\d{2}-\d{2})',r'^(\d{2})/(\d{2})/(\d{4})'):
        m=re.match(pattern,value)
        if m:
            if len(m.groups())==1:return m.group(1)
            return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    return None


def source_region(path):
    n=Path(path).name.lower()
    if re.search(r'dwbfpricesukwin\d{8}\.csv$',n):return 'UK'
    if re.search(r'dwbfpricesirewin\d{8}\.csv$',n):return 'IRE'
    return None


def read_market_files(input_dir):
    groups=collections.defaultdict(list);skipped=collections.Counter()
    files=sorted(Path(input_dir).rglob('*.csv'))
    for path in files:
        region=source_region(path)
        if not region:
            skipped['non_win_or_unrecognized_file']+=1;continue
        with path.open(encoding='utf-8-sig',errors='replace',newline='') as f:
            reader=csv.DictReader(f)
            names=set((reader.fieldnames or []))
            required={'EVENT_ID','EVENT_NAME','EVENT_DT','SELECTION_NAME','MORNINGWAP','PPWAP'}
            if not required.issubset(names):
                skipped['bad_header']+=1;continue
            for row in reader:
                day=event_day(row.get('EVENT_DT'))
                horse=norm(row.get('SELECTION_NAME'))
                if not day or not horse:
                    skipped['bad_row']+=1;continue
                key=(region,day,str(row.get('EVENT_ID') or ''),str(row.get('EVENT_DT') or ''),
                     str(row.get('EVENT_NAME') or ''),str(row.get('MENU_HINT') or ''))
                groups[key].append(row)
    return groups,skipped


def historical_races(db,dates):
    """Read only race fields and starter names for the requested days."""
    out=collections.defaultdict(dict)
    if not dates:return out
    con=sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro',uri=True)
    try:
        for day in sorted(dates):
            q='''SELECT r.id,r.race_date,r.course,u.horse_name,u.trainer,u.finish_pos,u.outcome
                   FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
                   WHERE r.race_date=?'''
            for rid,date,course,horse,trainer,pos,outcome in con.execute(q,(day,)):
                n=norm(horse)
                if not n:continue
                x=out[day].setdefault(rid,{'date':date,'course':course,'runners':{}})
                if n in x['runners'] and x['runners'][n]['horse']!=horse:
                    x['colliding_names']=True
                x['runners'][n]={'horse':horse,'trainer':trainer,'position':pos,'outcome':outcome}
    finally:con.close()
    return out


def match_market(key,rows,available):
    """Conservative full-field overlap, never assign a race from one horse alone."""
    region,day,event_id,event_dt,event_name,menu_hint=key
    bfnames={norm(r.get('SELECTION_NAME')) for r in rows if norm(r.get('SELECTION_NAME'))}
    if len(bfnames)<3:return None,'small_or_empty_market'
    possibles=[]
    for rid,race in available.get(day,{}).items():
        course=norm(race['course'])
        if region=='UK' and (' ire' in (' '+course) or 'ireland' in course):continue
        if region=='IRE' and (' usa' in (' '+course) or 'france' in course):continue
        runners=set(race['runners']);shared=len(runners&bfnames)
        coverage=shared/max(1,len(runners)); market_coverage=shared/max(1,len(bfnames))
        if shared<3 or coverage<0.68 or market_coverage<0.68:continue
        # An extra course hint strengthens confidence but is not mandatory: 
        # Betfair MENU_HINT formatting differs across years and courses.
        hint_bonus=.03 if course and course in norm(menu_hint+' '+event_name) else 0
        score=0.5*(coverage+market_coverage)+hint_bonus
        possibles.append((score,shared,rid))
    possibles.sort(reverse=True)
    if not possibles:return None,'insufficient_field_overlap'
    if len(possibles)>1 and possibles[0][0]-possibles[1][0]<.12:
        return None,'ambiguous_same_date_race'
    best=possibles[0]
    return best[2],'matched'


COLUMNS=['race_id','date','course','horse','trainer','position','won','betfair_event_id','betfair_event_dt',
         'betfair_event_name','source_country','morningwap','ppwap','bsp','morning_traded_volume',
         'preoff_traded_volume','average_preoff_shortening_pct','preoff_volume_share',
         'price_data_timing','market_match_confidence']


def run(args):
    groups,bad=read_market_files(args.input_dir)
    unique_days={k[1] for k in groups}
    if len(unique_days)>45:
        raise SystemExit("Process up to 45 days per invocation (roughly one month) to bound historical joins. Split longer archives by month.")
    available=historical_races(args.db,unique_days)
    data=[];audit=collections.Counter(bad);seen_ids=set()
    for k,rows in sorted(groups.items()):
        rid,status=match_market(k,rows,available);audit[status]+=1
        if rid is None:continue
        if rid in seen_ids:
            audit['duplicate_market_for_race']+=1
            continue
        seen_ids.add(rid)
        race=available[k[1]][rid]
        if race.get('colliding_names'):
            audit['name_collision_in_historical_race']+=1;continue
        # Official exchange result labels provide an independent settlement check
        # when the file supplies WIN_LOSE. Reject ambiguous runner identities.
        conflicting=[]
        for rr in rows:
            src=str(rr.get('WIN_LOSE') or '').strip()
            matched=race['runners'].get(norm(rr.get('SELECTION_NAME')))
            if matched and src in ('0','1') and int(src)!=int(str(matched['position'])=='1'):
                conflicting.append(rr.get('SELECTION_NAME'))
        if conflicting:
            audit['market_historical_winner_disagreement']+=1;continue
        total_pp=sum(nonneg(r.get('PPTRADEDVOL')) or 0 for r in rows)
        for row in rows:
            n=norm(row.get('SELECTION_NAME'))
            h=race['runners'].get(n)
            if h is None:
                audit['runner_name_unmatched']+=1;continue
            morn=decimal(row.get('MORNINGWAP'))
            pre=decimal(row.get('PPWAP'))
            vol=nonneg(row.get('PPTRADEDVOL'))
            shortening=100*(1-pre/morn) if morn and pre else None
            # Neither below is an early pre-off snapshot: PPWAP is an ENTIRE
            # pre-off weighted average and BSP is an at-the-off settlement.
            res=dict(race_id=rid,date=k[1],course=race['course'],horse=h['horse'],trainer=h['trainer'],
                position=h['position'],won=int(str(h['position'])=='1'),betfair_event_id=k[2],
                betfair_event_dt=k[3],betfair_event_name=k[4],source_country=k[0],
                morningwap=morn,ppwap=pre,bsp=(None if decimal(row.get('BSP'))==1001 else decimal(row.get('BSP'))),
                morning_traded_volume=nonneg(row.get('MORNINGTRADEDVOL')),
                preoff_traded_volume=vol,average_preoff_shortening_pct=round(shortening,3) if shortening is not None else None,
                preoff_volume_share=round(vol/total_pp,5) if vol is not None and total_pp>0 else None,
                price_data_timing='FULL_PREOFF_AVERAGES_RESEARCH_ONLY_NOT_T_MINUS_10_SNAPSHOTS',
                market_match_confidence='FULL_FIELD_NAME_OVERLAP')
            data.append(res)
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    with Path(args.out).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader();w.writerows(data)
    audit['output_matched_runners']=len(data)
    audit['source_files_seen']=len(list(Path(args.input_dir).rglob('*.csv')))
    return dict(audit)


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp)
        conn=sqlite3.connect(tmp/'history.sqlite')
        conn.execute('CREATE TABLE historical_races(id TEXT, race_date TEXT, course TEXT)')
        conn.execute('CREATE TABLE historical_runs(race_id TEXT,horse_name TEXT,trainer TEXT,finish_pos TEXT,outcome TEXT)')
        for race_id, horses in [('r1',['A Horse','B Horse','C Horse','D Horse']),
                                ('r2',['One','Two','Three','Four'])]:
            conn.execute('INSERT INTO historical_races VALUES(?,?,?)',(race_id,'2025-07-01','Ascot'))
            for i,name in enumerate(horses):
                conn.execute('INSERT INTO historical_runs VALUES(?,?,?,?,?)',
                             (race_id,name,'Trainer X','1' if i==0 else str(i+1),'finished'))
        conn.commit();conn.close()
        file=tmp/'dwbfpricesukwin01072025.csv'
        fields=['EVENT_ID','EVENT_NAME','EVENT_DT','MENU_HINT','SELECTION_NAME','MORNINGWAP','PPWAP',
                'BSP','MORNINGTRADEDVOL','PPTRADEDVOL']
        with file.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for event,names in [(1,['A Horse','B Horse','C Horse','D Horse']),
                                (2,['One','Two','Three','Four'])]:
                for i,h in enumerate(names):
                    w.writerow(dict(EVENT_ID=event,EVENT_NAME='Test',EVENT_DT=f'2025-07-01 1{event}:00:00',
                         MENU_HINT='Ascot',SELECTION_NAME=h,MORNINGWAP=9 if i==0 else 7,
                         PPWAP=4.5 if i==0 else 10,BSP=4.2 if i==0 else 11,
                         MORNINGTRADEDVOL=200,PPTRADEDVOL=500))
        out=tmp/'matched.csv';args=argparse.Namespace(input_dir=tmp,db=tmp/'history.sqlite',out=out)
        audit=run(args)
        records=list(csv.DictReader(out.open()))
        assert len(records)==8 and audit['matched']==2,audit
        assert {r['race_id'] for r in records}=={'r1','r2'}
        assert all(r['price_data_timing'].startswith('FULL_PREOFF') for r in records)
        assert abs(float(next(r['average_preoff_shortening_pct'] for r in records if r['horse']=='A Horse'))-50)<.01
        assert sum(int(r['won']) for r in records)==2
        assert all(r['trainer']=='Trainer X' for r in records)
    print('PASS: two separate same-day same-track races correctly resolved; 8 runner records joined; shortening + trainer + winners preserved; no actual external CSV accessed')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--input-dir');p.add_argument('--db');p.add_argument('--out')
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test()
    else:
        if not (a.db and a.input_dir and a.out):p.error('requires --db, --input-dir and --out')
        print(run(a))
