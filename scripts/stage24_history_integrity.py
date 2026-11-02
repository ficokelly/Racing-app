#!/usr/bin/env python3
"""Racing Hot Pots Stage 24: audited, idempotent historical result extension.

Runs on the temporary SQLite copy downloaded by Actions, NEVER on the saved
historical GitHub artifact. Stage 24 imports only dated, post-archive, verified
results from data/racing-results/YYYY-MM-DD.json. It does not guess SP, trainers,
pedigree, or unidentified runners. Incomplete identity is tracked, not fabricated.
Source-aware canonical pedigree matching is installed in temporary history engine.
No past market/result data is copied into current-race predictor features.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, re, sqlite3, unicodedata
from pathlib import Path

VERSION = 'stage24-2026-11-history-identity-v1'
COUNTRIES = r'GB|IRE|FR|USA|GER|AUS|NZ|ITY|JPN|SAF|CAN|BRZ|ARG|SWE|DEN|NOR|CHI|UAE|HK|POL'
CANONICAL_PATCH = '''
# Stage24: standardise pedigree country suffixes across historical/live APIs.
# Still require matching horse name AND BOTH pedigree fields: never name-only merge.
def _stage24_norm_name(value):
 import re, unicodedata
 text=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode('ascii').strip()
 text=re.sub(r'\\s*\\((?:GB|IRE|FR|USA|GER|AUS|NZ|ITY|JPN|SAF|CAN|BRZ|ARG|SWE|DEN|NOR|CHI|UAE|HK|POL)\\)\\s*$', '',text, flags=re.I)
 return ''.join(ch.lower() for ch in text if ch.isalnum())
def history_key(name,sire=None,dam=None):
 return (_stage24_norm_name(name),_stage24_norm_name(sire),_stage24_norm_name(dam))
'''


def clean(s):
 s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode('ascii').strip()
 s=re.sub(r'\s*\((?:'+COUNTRIES+r')\)\s*$','',s,flags=re.I)
 return ''.join(c.lower() for c in s if c.isalnum())


def safe_number(v):
 if v in (None,'','-'):return None
 try:return float(str(v).replace(',',''))
 except(ValueError,TypeError):return None


def parse_sp(v):
 x=str(v or '').strip().upper().replace(' ','')
 x=re.sub(r'(?:J?F|C)$','',x)
 if x in ('EVS','EVENS','EVEN'):return 2.0
 a=re.fullmatch(r'(\d+)/(\d+)',x)
 if a and int(a[2])>0:return 1+int(a[1])/int(a[2])
 z=safe_number(x)
 return z if z and z>1 else None


def parse_position(value,status=None):
 v=str(value if value is not None else status or '').strip().upper()
 if re.fullmatch(r'\d+(?:ST|ND|RD|TH)?',v):return int(re.match(r'\d+',v)[0]),'FINISHED'
 synonyms={'NONRUNNER':'NR','NON RUNNER':'NR','WITHDRAWN':'NR','FELL':'F','PULLED UP':'PU',
           'UNSEATED RIDER':'UR','BROUGHT DOWN':'BD','DISQUALIFIED':'DSQ','REFUSED':'REF',
           'DID NOT FINISH':'DNF'}
 v=synonyms.get(v,v)
 if v in ('NR','DNS','WD','NONSTARTER'):return None,'NR'
 if v in ('F','PU','UR','BD','DSQ','REF','RR','RO','SU','CO','DNF'):return None,v
 # A vague 'ran' cannot establish a starting position or confirmed DNF.
 return None,'UNKNOWN'


def parse_time(race):
 value=str(race.get('off_dt') or race.get('race_time') or race.get('off') or '')
 match=re.search(r'(\d{2}):(\d{2})',value)
 return match.group(0) if match else None


def race_id(race,day):
 venue=clean(race.get('course'))
 off=parse_time(race)
 if not venue or not off:return None
 return f'rhp-st24:{day}:{venue}:{off}'


def weight_lbs(r):
 v=safe_number(r.get('weight_lbs'))
 if v is not None:return v
 val=str(r.get('weight') or '').strip()
 q=re.match(r'^(\d+)[-\s](\d+)$',val)
 return int(q[1])*14+int(q[2]) if q else None


def read_daily(folder,from_date,to_date):
 today=dt.date.fromisoformat(to_date)
 date=dt.date.fromisoformat(from_date)
 found=[]; gaps=[]
 while date<=today:
  day=date.isoformat();path=Path(folder)/f'{day}.json'
  if not path.exists():gaps.append(day)
  else:
   try:
    content=json.loads(path.read_text())
    races=content.get('results')
    if not isinstance(races,list) or not races:gaps.append(day)
    else:found.append((day,races))
   except (ValueError,OSError):gaps.append(day)
  date+=dt.timedelta(days=1)
 return found,gaps


def columns(con,table):
 return {r[1] for r in con.execute(f'PRAGMA table_info({table})')}


def ingest(con,folder,to_date='2026-11-01',strict=False,base_last=None):
 c_r=columns(con,'historical_races');c_h=columns(con,'historical_runs')
 if not {'id','race_date','course'}<=c_r or not {'race_id','horse_name_norm','finish_pos','outcome'}<=c_h:
  raise RuntimeError('Unexpected staging database schema. Refusing silent import.')
 last=base_last or con.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0]
 first=(dt.date.fromisoformat(last)+dt.timedelta(days=1)).isoformat()
 if first>to_date:return {'archive_last_date':last,'target_date':to_date,'already_current':True,'imported':0}
 sources,gaps=read_daily(folder,first,to_date)
 if strict and gaps:raise RuntimeError('Missing daily results: '+', '.join(gaps))
 audit={'version':VERSION,'archive_last_date':last,'first_target_date':first,'target_date':to_date,
        'source':'data/racing-results daily The Racing API / archived final results',
        'missing_days':gaps,'days':[],'races_added':0,'runs_added':0,'complete_pedigree_runs':0,
        'missing_pedigree_runs':0,'priced_runs':0,'races_with_complete_pedigrees':0,
        'quality':'PARTIAL_ATTRIBUTES_AUDITED_NOT_FULLY_VERIFIED'}
 sr=sorted(c_r);sh=sorted(c_h)
 sql_r='INSERT OR IGNORE INTO historical_races ('+','.join(sr)+') VALUES ('+','.join('?'*len(sr))+')'
 sql_h='INSERT OR IGNORE INTO historical_runs ('+','.join(sh)+') VALUES ('+','.join('?'*len(sh))+')'
 for day,races in sources:
  n=collections.Counter()
  for race in races:
   if not isinstance(race,dict) or race.get('status') not in (None,'final','FINAL'):continue
   if race.get('date',day)!=day:continue
   rid=race_id(race,day)
   rr=race.get('runners')
   if not rid or not isinstance(rr,list) or len(rr)<2:
    n['skipped_incomplete_races']+=1;continue
   dist=safe_number(race.get('dist_f') or race.get('distance_f'))
   ra={'id':rid,'race_date':day,'course':race.get('course'),
       'race_type':race.get('type') or race.get('race_type'), 'surface':race.get('surface'),
       'distance_f':dist,'going':race.get('going'),
       'race_class':race.get('class') or race.get('race_class'),
       'source': 'rhp-stage24-recent-verified-results'}
   con.execute(sql_r, [ra.get(k) for k in sr])
   n['races']+=1
   all_pedigrees=True
   for runner in rr:
    if not isinstance(runner,dict) or not runner.get('horse'):continue
    name=runner.get('horse')
    hnorm=clean(name)
    if not hnorm:continue
    pos,outcome=parse_position(runner.get('position'),runner.get('status'))
    if outcome=='UNKNOWN':n['unknown_outcomes']+=1;continue
    sire=runner.get('sire');dam=runner.get('dam')
    good_pedigree=bool(clean(sire) and clean(dam))
    if not good_pedigree:all_pedigrees=False
    sp=parse_sp(runner.get('sp'))
    runner_key='id:'+str(runner.get('horse_id')) if runner.get('horse_id') else 'name:'+hnorm
    h={'race_id':rid,'runner_key':runner_key,'horse_source_id':runner.get('horse_id'),
       'horse_name':name,'horse_name_norm':hnorm.upper(),
       'horse_country':None,'foaled':None,'finish_pos':pos,'outcome':outcome,
       'trainer':runner.get('trainer'),'jockey':runner.get('jockey'),
       'official_rating':safe_number(runner.get('or') or runner.get('official_rating')),
       'weight_lbs':weight_lbs(runner),'sp_decimal':sp,
       'draw':int(safe_number(runner.get('draw'))) if safe_number(runner.get('draw')) is not None else None,
       'age':int(safe_number(runner.get('age'))) if safe_number(runner.get('age')) is not None else None,
       'sex':runner.get('sex'),'sire':sire,'dam':dam,'rpr':None,'ts':None,
       'comment':None,'beaten':None}
    con.execute(sql_h,[h.get(k) for k in sh]);n['runs']+=1
    n['complete_pedigree_runs' if good_pedigree else 'missing_pedigree_runs']+=1
    if sp:n['priced_runs']+=1
   if all_pedigrees:n['races_with_complete_pedigrees']+=1
  for k in ['races','runs','complete_pedigree_runs','missing_pedigree_runs','priced_runs','races_with_complete_pedigrees']:
   audit[{'races':'races_added','runs':'runs_added'}.get(k,k)]+=n[k]
  audit['days'].append({'date':day,**n})
  con.commit()
 audit['effective_last_date']=con.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0]
 audit['missing_days_count']=len(gaps)
 audit['complete_identity_share_pct']=round(100*audit['complete_pedigree_runs']/max(1,audit['runs_added']),2)
 audit['coverage_verified_through']=to_date if not gaps else min(gaps)
 return audit


def install_identity_patch(filename):
 p=Path(filename)
 if not p.exists():raise FileNotFoundError(f'Missing runtime feature engine {p}')
 contents=p.read_text()
 if 'def history_key(' not in contents:raise RuntimeError('Historical engine signature changed; refusing patch.')
 if '_stage24_norm_name' in contents:return False
 marker="if __name__ == '__main__':"
 if marker in contents:
  contents=contents.replace(marker,'\n'+CANONICAL_PATCH+'\n'+marker,1)
 else:contents+='\n'+CANONICAL_PATCH+'\n'
 p.write_text(contents)
 return True


def audit_today(con,repo,day):
 path=Path(repo)/'data/racecards.json'
 try:cards=json.loads(path.read_text()).get('runners',[])
 except (OSError,ValueError):return []
 wanted=[r for r in cards if r.get('date')==day and clean(r.get('horse')) in {'hardstare','coastalcommand'}]
 output=[]
 for runner in wanted:
  name=clean(runner.get('horse'));sire=clean(runner.get('sire'));dam=clean(runner.get('dam'))
  existing=con.execute('''SELECT h.sire,h.dam,h.horse_name_norm,r.race_date,r.course
    FROM historical_runs h JOIN historical_races r ON r.id=h.race_id
    WHERE h.horse_name_norm=? AND r.race_date<?''',(name.upper(),day)).fetchall()
  canonical=[x for x in existing if clean(x[0])==sire and clean(x[1])==dam and sire and dam]
  others=[x for x in existing if clean(x[0])!=sire or clean(x[1])!=dam]
  output.append({'horse':runner['horse'],'horse_id':runner.get('horse_id'),
      'matching_pedigree_runs':len(canonical),'name_matches_other_or_missing_pedigree':len(others),
      'race_dates':[x[3] for x in sorted(canonical,key=lambda q:q[3],reverse=True)[:25]],
      'identity_status':'CANONICAL_PEDIGREE_MATCHED' if canonical else 'NOT_CONFIRMED',
      'expected_minimum_from_user_screenshots':5 if name=='hardstare' else 4})
 return output


def run(args):
 db=Path(args.db); repo=Path(args.repo)
 if not db.is_file():raise FileNotFoundError(db)
 if args.day:day=args.day
 else:day=(dt.datetime.now(dt.timezone.utc).date()-dt.timedelta(days=1)).isoformat()
 con=sqlite3.connect(db)
 try:
  audit=ingest(con,repo/'data/racing-results',day,args.strict)
  audit['monitored_horses']=audit_today(con,repo,(dt.date.fromisoformat(day)+dt.timedelta(days=1)).isoformat())
 finally:con.close()
 runtime=repo/'scripts/stage16_history_model.py'
 audit['engine_patched']=install_identity_patch(runtime)
 audit['integrity_status']='INCOMPLETE' if audit.get('missing_days') else 'DATES_PRESENT_ATTRIBUTES_PARTIAL'
 bad=[x for x in audit['monitored_horses'] if x['matching_pedigree_runs']<x['expected_minimum_from_user_screenshots']]
 audit['known_form_check']='FAILED' if bad else 'PASSED_OR_NOT_APPLICABLE'
 audit['known_form_failures']=bad
 target=repo/'data/history-integrity-audit.json';target.parent.mkdir(parents=True,exist_ok=True)
 target.write_text(json.dumps(audit,indent=2,sort_keys=True)+'\n')
 print('HISTORY INTEGRITY:',json.dumps({k:audit.get(k) for k in ('archive_last_date','effective_last_date','races_added','runs_added','missing_days','complete_identity_share_pct','monitored_horses','integrity_status','known_form_check')},default=str))
 if args.assert_form and bad:raise RuntimeError('Known horse form still missing; audit was saved: '+str(bad))
 return audit


def self_test():
 assert clean('Passing Glance (GB)')==clean('Passing Glance')
 assert clean('Farewellatmidnight (GB)')==clean('Farewellatmidnight')
 assert clean('Scorpion (IRE)')==clean('Scorpion')
 assert parse_sp('9/4F')==3.25
 assert parse_position('PU')==(None,'PU')
 assert parse_position('3')==(3,'FINISHED')
 import tempfile
 with tempfile.TemporaryDirectory() as td:
  root=Path(td);(root/'data/racing-results').mkdir(parents=True)
  con=sqlite3.connect(root/'history.sqlite')
  con.execute('CREATE TABLE historical_races(id TEXT PRIMARY KEY,race_date TEXT,course TEXT,race_type TEXT,surface TEXT,distance_f REAL,going TEXT,race_class TEXT,source TEXT)')
  con.execute('CREATE TABLE historical_runs(race_id TEXT,runner_key TEXT,horse_source_id TEXT,horse_name TEXT,horse_name_norm TEXT,horse_country TEXT,foaled TEXT,finish_pos INTEGER,outcome TEXT,trainer TEXT,jockey TEXT,official_rating REAL,weight_lbs REAL,sp_decimal REAL,draw INT,age INT,sex TEXT,sire TEXT,dam TEXT,rpr REAL,ts REAL,comment TEXT,beaten TEXT,PRIMARY KEY(race_id,runner_key))')
  con.execute("INSERT INTO historical_races(id,race_date,course) VALUES ('old','2026-10-07','A')")
  race={'date':'2026-10-08','course':'Plumpton','off_dt':'2026-10-08T13:00:00Z','type':'Jumps','runners':[
      {'horse_id':'hrs_1','horse':'Hard Stare','sire':'Passing Glance (GB)','dam':'Farewellatmidnight (GB)','position':1,'age':6},
      {'horse_id':'hrs_2','horse':'Other Horse','sire':'Other (GB)','dam':'Parents (IRE)','position':'PU'}]}
  (root/'data/racing-results/2026-10-08.json').write_text(json.dumps({'results':[race]}))
  a=ingest(con,root/'data/racing-results','2026-10-08',strict=True)
  assert a['races_added']==1 and a['runs_added']==2
  assert a['complete_pedigree_runs']==2
  assert con.execute('select count(*) from historical_races').fetchone()[0]==2
  assert con.execute('select count(*) from historical_runs').fetchone()[0]==2
  assert ingest(con,root/'data/racing-results','2026-10-08')['already_current']
  source=root/'scripts.py';source.write_text('def history_key(name,sire=None,dam=None):return(name,sire,dam)\n')
  assert install_identity_patch(source);assert not install_identity_patch(source)
  ctx={};exec(source.read_text(),ctx)
  assert ctx['history_key']('Hard Stare','Passing Glance (GB)','Farewellatmidnight (GB)')==ctx['history_key']('Hard Stare','Passing Glance','Farewellatmidnight')
  assert ctx['history_key']('Same Name','A','B')!=ctx['history_key']('Same Name','C','D')
  con.close()
 print('Stage24 history integrity unit tests passed')


if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','self-test']);p.add_argument('--repo',default='.');p.add_argument('--db');p.add_argument('--day');p.add_argument('--strict',action='store_true');p.add_argument('--assert-form',action='store_true');args=p.parse_args()
 if args.command=='self-test':self_test()
 else:
  if not args.db:raise SystemExit('--db is required')
  run(args)
