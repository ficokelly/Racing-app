#!/usr/bin/env python3
"""Stage90: inspect why pre-off declared horses have no eligible prior HPR.

READ ONLY. Never joins source history using name alone; potential name matches
are review leads, not historical identity. No source/ratings/app modifications.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, re, sqlite3, sys, tempfile
from zoneinfo import ZoneInfo
SCRIPTS=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPTS))
import stage86_hpr_engine as hpr
import stage89_hpr_gap_triage as stage89

FIELDS=['priority','classification','date','course','time','horse','horse_key','horse_id','sire','dam',
        'card_form','last_run_days','days_since_source_cutoff','source_name_candidate_runs',
        'source_exact_id_runs','source_exact_pedigree_runs','source_strong_distinct_races',
        'source_strong_valid_outcomes','source_strong_unresolved_outcomes','source_latest_matched_prior_date',
        'recent_verified_unrated_runs','source_name_only_review_count','source_example_race_id','reason']
CATEGORIES={
 'EXACT_HISTORY_UNRATED':'P1',
 'RECENT_VERIFIED_OUTCOME_UNRATED':'P1',
 'HISTORY_NAME_ONLY_UNVERIFIED':'P2',
 'NO_MATCHED_PRIOR_HISTORY':'P3',
 'MISSING_PREOFF_IDENTITY':'P1',
}

def csv_write(p,rows,fields=FIELDS):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def conn_ro(p):
 c=sqlite3.connect('file:'+str(pathlib.Path(p).resolve())+'?mode=ro',uri=True)
 c.row_factory=sqlite3.Row
 if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database failed integrity check')
 return c

def source_rows_by_name(c,normalized):
 return [dict(r) for r in c.execute('''SELECT r.id AS race_id,r.race_date,r.course,r.race_type,
     u.horse_name,u.horse_source_id,u.sire,u.dam,u.finish_pos,u.outcome,u.beaten
     FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
     WHERE u.horse_name_norm=? ORDER BY r.race_date DESC''',(normalized,))]

def status_of(row):
 pos=hpr.number(row.get('finish_pos'))
 if pos is not None and pos>=1:return 'FINISH_POSITION_VERIFIED'
 state=str(row.get('outcome') or '').lower().strip()
 if state in hpr.UNFINISHED:return 'EXPLICIT_NONFINISHER'
 return 'UNRESOLVED_OUTCOME'

def source_match(row,card):
 """Strong matches use IDs or both parents; name alone never strong."""
 cid=str(card.get('horse_id') or '').strip()
 rid=str(row.get('horse_source_id') or '').strip()
 sire=hpr.norm(card.get('sire'));dam=hpr.norm(card.get('dam'))
 row_sire=hpr.norm(row.get('sire'));row_dam=hpr.norm(row.get('dam'))
 contradicts_pedigree=bool(sire and dam and row_sire and row_dam and (sire,dam)!=(row_sire,row_dam))
 stable_id=bool(cid and rid and cid==rid and not contradicts_pedigree)
 pedigree=bool(sire and dam and row_sire==sire and row_dam==dam)
 return stable_id,pedigree

def classify_card(c,card,when,source_cache,recent):
 horse=str(card.get('horse') or '')
 n=hpr.norm(horse);key=hpr.identity(card)
 if n not in source_cache:source_cache[n]=source_rows_by_name(c,n)
 rows=[r for r in source_cache[n] if str(r.get('race_date') or '')<str(when)]
 match=[(r,*source_match(r,card)) for r in rows]
 strong=[r for r,exact,ped in match if exact or ped]
 exact=sum(bool(exact) for _,exact,_ in match)
 pedigree=sum(bool(ped) for _,_,ped in match)
 good=sum(status_of(r)!='UNRESOLVED_OUTCOME' for r in strong)
 unmatched=len(rows)-len(strong)
 recent_valid=[e for e in recent.get(key,[]) if e['date']<str(when)
               and e.get('race_preoff_field_verified') and hpr.run_quality(e)[0] is not None] if key else []
 if not key:status='MISSING_PREOFF_IDENTITY';note='Pre-off horse does not provide an ID or exact pedigree.'
 elif strong:status='EXACT_HISTORY_UNRATED';note='Prior source matches ID/pedigree but has no pre-race HPR; investigate scoring and provenance.'
 elif recent_valid:status='RECENT_VERIFIED_OUTCOME_UNRATED';note='Prior verified recent result with same stable identity was not rated.'
 elif unmatched:status='HISTORY_NAME_ONLY_UNVERIFIED';note='Source has name-only candidates; corroborate identity with ID/pedigree before recovery.'
 else:status='NO_MATCHED_PRIOR_HISTORY';note='No prior source candidate; may be true debut or missing coverage, not verified debut.'
 source_last=max((r['race_date'] for r in strong),default='')
 last=hpr.number(card.get('last_run'))
 cutoff=dt.date(2026,10,7)
 gap=(when-dt.timedelta(days=int(round(last)))-cutoff).days if last is not None and 0<=last<=36500 else ''
 return dict(priority=CATEGORIES[status],classification=status,date=str(when),course=card.get('course'),
             time=hpr.off(card.get('off_dt') or card.get('race_time') or card.get('off')),
             horse=horse,horse_key=key,horse_id=card.get('horse_id'),sire=card.get('sire'),dam=card.get('dam'),
             card_form=card.get('form'),last_run_days=card.get('last_run'),days_since_source_cutoff=gap,
             source_name_candidate_runs=len(rows),source_exact_id_runs=exact,
             source_exact_pedigree_runs=pedigree,source_strong_distinct_races=len({r['race_id'] for r in strong}),
             source_strong_valid_outcomes=good,
             source_strong_unresolved_outcomes=len(strong)-good,
             source_latest_matched_prior_date=source_last,
             recent_verified_unrated_runs=len(recent_valid),
             source_name_only_review_count=unmatched,
             source_example_race_id=str(strong[0]['race_id'] if strong else rows[0]['race_id'] if rows else ''),
             reason=note)

def run(root,db,ratings,out,asof,days):
 start=asof-dt.timedelta(days=days);end=asof-dt.timedelta(days=1)
 earliest=max(start-dt.timedelta(days=90),dt.date(2026,10,8))
 results,bad=hpr.load_results(root,earliest,end)
 dated=hpr.card_dates(root,earliest,end,git_preoff=True)
 cardmaps={date:hpr.by_race_cards(rows) for date,rows in dated.items()}
 cards=[x for y in dated.values() for x in y]
 profiles,names=hpr.profiles_from_cards(cards)
 for r in results.values():
  for z in r['runners']:
   k=hpr.identity(z)
   if k:profiles.setdefault(k,z)
 recent,_=hpr.result_events(results,cardmaps,profiles,names,earliest,end)
 bykey=collections.defaultdict(list)
 for x in recent:bykey[x['horse_key']].append(x)
 with conn_ro(db) as source,conn_ro(ratings) as hdb:
  race_rows,reasons,runner_reasons=stage89.race_rows(results,cards,hdb,start,end,hpr)
  card_lookup=collections.defaultdict(list)
  for x in cards:card_lookup[hpr.race_key(x)].append(x)
  targets=[];seen=set();namecache={};priority_races=set()
  for race in race_rows:
   if race['status']!='INSUFFICIENT_FULL_FIELD_HPR':continue
   priority_races.add(race['source_race_key'])
   when=hpr.day(race['date'])
   live_result=results.get(race['source_race_key'],{})
   starters={hpr.norm(z.get('horse')) for z in live_result.get('runners',[])}
   for card in card_lookup.get(race['source_race_key'],[]):
    if str(card.get('race_status') or '').lower() in hpr.STOP:continue
    if hpr.norm(card.get('horse')) not in starters:continue
    key=hpr.identity(card)
    if key and hpr.previous_ratings(hdb,key,str(when)):continue
    sig=(str(when),hpr.race_key(card),hpr.norm(card.get('horse')))
    if sig in seen:continue
    seen.add(sig)
    targets.append(classify_card(source,card,when,namecache,bykey))
 counts=collections.Counter(x['classification'] for x in targets)
 targets.sort(key=lambda x:(x['priority'], -int(x['source_strong_distinct_races'] or 0),x['date'],str(x['horse'])))
 csv_write(out/'stage90-missing-horse-source-diagnostics.csv',targets)
 safe=[x for x in targets if x['classification'] in ('EXACT_HISTORY_UNRATED','RECENT_VERIFIED_OUTCOME_UNRATED')]
 csv_write(out/'stage90-verified-recovery-investigation-queue.csv',safe)
 guide={'stage':90,'mode':'RESEARCH_ONLY','scope':{'start':str(start),'end':str(end),'days':days},
   'baseline_full_field':reasons,'baseline_missing_runner_causes':runner_reasons,
   'priority_races':len(priority_races),'missing_runner_instances_in_excluded_races':len(targets),
   'classifications':dict(counts),'strong_identity_recovery_candidates':len(safe),
   'automatically_recovered_hpr':0,
   'notes':['A matching name alone is NEVER accepted as the same horse.',
      'Exactly matched source history does not automatically qualify for HPR: verify race/finish evidence.',
      'No historical source candidate does not prove a genuine debut.',
      'The source SQLite imported here ends on 2026-10-07; more recent results use separate verified archives.',
      'Frozen D, D(c), daily persistent HPR archives and app are unchanged.']}
 (out/'stage90-summary.json').write_text(json.dumps(guide,indent=2,sort_keys=True)+'\n')
 print(json.dumps(guide,indent=2));return guide

def self_test():
 with tempfile.TemporaryDirectory() as td:
  p=pathlib.Path(td)/'source.sqlite'
  with sqlite3.connect(p) as c:
   c.executescript('''CREATE TABLE historical_races(id TEXT,race_date TEXT,course TEXT,race_type TEXT);
     CREATE TABLE historical_runs(race_id TEXT,horse_name TEXT,horse_name_norm TEXT,
     horse_source_id TEXT,sire TEXT,dam TEXT,finish_pos INTEGER,outcome TEXT,beaten TEXT);''')
   c.execute('INSERT INTO historical_races VALUES (?,?,?,?)',('r1','2026-10-01','Ascot','Hurdle'))
   c.execute('INSERT INTO historical_runs VALUES (?,?,?,?,?,?,?,?,?)',('r1','Alpha','ALPHA','h123','Sire','Dam',2,'FINISHED','1L'))
  with conn_ro(p) as db:
   cache={};now=dt.date(2026,11,9)
   card={'horse':'Alpha','course':'Ascot','horse_id':'h123','sire':'Sire','dam':'Dam','form':'2','last_run':'39'}
   a=classify_card(db,card,now,cache,{})
   assert a['classification']=='EXACT_HISTORY_UNRATED' and a['source_strong_valid_outcomes']==1
   card['horse_id']='different';card['sire']='';card['dam']=''
   b=classify_card(db,card,now,cache,{})
   assert b['classification']=='HISTORY_NAME_ONLY_UNVERIFIED' and b['source_name_only_review_count']==1
   card['horse']='NoNamedSource';card['horse_id']='unknown'
   assert classify_card(db,card,now,cache,{})['classification']=='NO_MATCHED_PRIOR_HISTORY'
 print('PASS: Stage90 exact ID/pedigree checks, source-name-only exclusions, no guessed debut')

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--self-test',action='store_true')
 p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
 p.add_argument('--db',type=pathlib.Path)
 p.add_argument('--ratings',type=pathlib.Path)
 p.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage90'))
 p.add_argument('--as-of',default='');p.add_argument('--days',type=int,default=30)
 a=p.parse_args()
 if a.self_test:self_test();return
 if not 1<=a.days<=90:p.error('--days must be 1 to 90')
 if not a.db or not a.db.is_file() or not a.ratings or not a.ratings.is_file():p.error('Both --db and --ratings required')
 asof=hpr.day(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
 if not asof:p.error('Invalid --as-of date')
 a.output.mkdir(parents=True,exist_ok=True)
 run(a.root,a.db,a.ratings,a.output,asof,a.days)

if __name__=='__main__':main()
