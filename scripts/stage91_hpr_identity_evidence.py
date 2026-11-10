#!/usr/bin/env python3
"""Stage91: rank safe-to-investigate historical identity candidates for HPR.

Research only. No name-only match is ever promoted to a verified horse identity.
Racecard age, sex and reported last-run date are corroboration leads, NOT an
identity guarantee. Also isolate the four pedigree matches with unusable results.
Never writes to source DB, HPR archive, frozen D/D(c), or live selections.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, sqlite3, sys, tempfile
from zoneinfo import ZoneInfo
SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPTS))
import stage86_hpr_engine as hpr

COLS=['priority','review_class','target_date','target_course','target_time','horse','horse_key',
 'current_age','current_sex','last_run_days','expected_last_run_date','candidate_source_race_id',
 'candidate_date','candidate_course','candidate_finish_pos','candidate_outcome',
 'candidate_age','candidate_sex','candidate_sire','candidate_dam','stable_id_exact',
 'pedigree_exact','age_compatible','sex_compatible','last_run_date_exact',
 'source_name_matches','plausible_age_matches','age_sex_matches','exact_last_run_matches',
 'recovery_status','notes']
UNRES=['target_date','target_course','horse','horse_key','candidate_source_race_id',
       'candidate_date','candidate_course','candidate_finish_pos','candidate_outcome',
       'candidate_beaten','source_evidence_status','action']


def read_csv(path):
 with open(path,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))

def write_csv(path, rows, cols):
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rows)

def normsex(x):
 s=str(x or '').strip().lower()
 m={'g':'gelding','gelding':'gelding','gld':'gelding','h':'horse','horse':'horse','c':'colt','colt':'colt',
    'f':'filly','filly':'filly','m':'mare','mare':'mare','r':'rig','rig':'rig'}
 return m.get(s,'')

def age_compatible(card_age, source_age, target_year, source_year):
 a=hpr.number(card_age); b=hpr.number(source_age)
 if a is None or b is None:return None
 if not (1<=a<=35 and 1<=b<=35):return None
 # Thoroughbred racing age increases every January 1. Research-only signal.
 return round(a)==round(b)+(target_year-source_year)

def dtstr(s):
 try:return dt.date.fromisoformat(str(s or '')[:10])
 except (ValueError,TypeError):return None

def int_days(x):
 v=hpr.number(x)
 return int(round(v)) if v is not None and 0<=v<=36500 else None

def match_evidence(card,source,target):
 source_day=dtstr(source['race_date']);target_year=target.year
 last=int_days(card.get('last_run'));expected=target-dt.timedelta(days=last) if last is not None else None
 age=age_compatible(card.get('age'),source.get('age'),target_year,source_day.year) if source_day else None
 sexcard=normsex(card.get('sex'));sexsrc=normsex(source.get('sex'))
 sex_equal=sexcard==sexsrc if sexcard and sexsrc else None
 cid=str(card.get('horse_id') or '');sid=str(source.get('horse_source_id') or '')
 a=hpr.norm(card.get('sire'));b=hpr.norm(card.get('dam'))
 sa=hpr.norm(source.get('sire'));sb=hpr.norm(source.get('dam'))
 contradiction=bool(a and b and sa and sb and (a,b)!=(sa,sb))
 exact_id=bool(cid and sid and cid==sid and not contradiction)
 pedigree=bool(a and b and sa==a and sb==b)
 return {'age_compatible':age,'sex_compatible':sex_equal,
   'last_run_date_exact':expected==source_day if expected and source_day else None,
   'stable_id_exact':exact_id,'pedigree_exact':pedigree,'expected_last_run_date':str(expected or ''),
   'source_day':source_day}

def classify(rows):
 # This does NOT validate identity. Every non-strong path is a REVIEW LEAD.
 if any(e['stable_id_exact'] or e['pedigree_exact'] for _,e in rows):return 'STRONG_IDENTITY_REVIEW'
 if any(e['last_run_date_exact'] and e['age_compatible'] is True and e['sex_compatible'] is True for _,e in rows):return 'DATE_AGE_SEX_REVIEW'
 if any(e['last_run_date_exact'] and e['age_compatible'] is True for _,e in rows):return 'DATE_AGE_REVIEW'
 if any(e['last_run_date_exact'] for _,e in rows):return 'DATE_ONLY_REVIEW'
 if any(e['age_compatible'] is True and e['sex_compatible'] is True for _,e in rows):return 'AGE_SEX_REVIEW'
 if any(e['age_compatible'] is True for _,e in rows):return 'AGE_ONLY_REVIEW'
 return 'NO_SUPPORTING_SIGNALS'

def source_conn(path):
 p=pathlib.Path(path).resolve();con=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True)
 con.row_factory=sqlite3.Row
 assert con.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 return con

def source_rows(con,name):
 return [dict(r) for r in con.execute('''SELECT r.id AS race_id,r.race_date,r.course,
  u.horse_name_norm,u.horse_source_id,u.sire,u.dam,u.finish_pos,u.outcome,u.beaten,u.age,u.sex
  FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
  WHERE u.horse_name_norm=? ORDER BY r.race_date DESC''',(name,))]

def run(root,db,triage,out,asof,days):
 start=asof-dt.timedelta(days=days);end=asof-dt.timedelta(days=1)
 cases=read_csv(triage)
 if not cases:raise RuntimeError('Stage90 diagnostics empty; cannot infer absence of gaps')
 cards=hpr.card_dates(root,start,end,git_preoff=True)
 index=collections.defaultdict(list)
 for group in cards.values():
  for c in group:index[(str(hpr.day(c.get('date') or c.get('race_time'))),hpr.course(c.get('course')),
                         hpr.off(c.get('off_dt') or c.get('race_time') or c.get('off')),hpr.norm(c.get('horse')))].append(c)
 results=[];unresolved=[];distribution=collections.Counter();candidate_stats=collections.Counter();unique=collections.defaultdict(set)
 missing_snapshot=0;ambiguous_snapshot=0;checked=0
 with source_conn(db) as con:
  cache={}
  for t in cases:
   status=t['classification']
   if status not in ('HISTORY_NAME_ONLY_UNVERIFIED','EXACT_HISTORY_UNRATED'):continue
   date=dtstr(t['date'])
   if date is None or not start<=date<=end:continue
   k=(t['date'],hpr.course(t['course']),str(t['time'])[:5],hpr.norm(t['horse']))
   cardlist=index.get(k,[])
   if not cardlist:missing_snapshot+=1;continue
   if len(cardlist)!=1:ambiguous_snapshot+=1;continue
   card=cardlist[0];checked+=1
   n=hpr.norm(card.get('horse'))
   if n not in cache:cache[n]=source_rows(con,n)
   candidates=[z for z in cache[n] if z['race_date']<t['date']]
   checks=[(z,match_evidence(card,z,date)) for z in candidates]
   eligible=[]
   for z,e in checks:
    if e['age_compatible'] is False or e['sex_compatible'] is False:continue
    eligible.append((z,e))
   distinct={z['race_id'] for z,e in eligible}
   typ=classify(eligible)
   distribution[typ]+=1;unique[typ].add(t['horse_key'])
   agecount=sum(e['age_compatible'] is True for z,e in checks)
   agesexcount=sum(e['age_compatible'] is True and e['sex_compatible'] is True for z,e in checks)
   datedcount=sum(e['last_run_date_exact'] for z,e in checks)
   candidate_stats['historical_name_rows_scanned']+=len(candidates)
   candidate_stats['rejected_age_or_sex_contradictions']+=len(checks)-len(eligible)
   # One row per promising candidate, bounded to avoid huge output.
   sorted_candidates=sorted(eligible,key=lambda p:(not bool(p[1]['stable_id_exact'] or p[1]['pedigree_exact']),
          not bool(p[1]['last_run_date_exact']),not bool(p[1]['age_compatible']),-int(p[0]['race_date'].replace('-',''))))
   if status=='EXACT_HISTORY_UNRATED':
    for z,e in sorted_candidates:
     if not (e['stable_id_exact'] or e['pedigree_exact']):continue
     pos=hpr.number(z.get('finish_pos'));outcome=str(z.get('outcome') or '').lower().strip()
     known=pos is not None and pos>=1 or outcome in hpr.UNFINISHED
     if not known:
      unresolved.append({'target_date':t['date'],'target_course':t['course'],'horse':t['horse'],
       'horse_key':t['horse_key'],'candidate_source_race_id':z['race_id'],
       'candidate_date':z['race_date'],'candidate_course':z['course'],
       'candidate_finish_pos':z['finish_pos'],'candidate_outcome':z['outcome'],
       'candidate_beaten':z['beaten'],'source_evidence_status':'EXACT_IDENTITY_UNRESOLVED_FINISH',
       'action':'VERIFY_EXACT_RESULT_FROM_AUTHORISED_SOURCE;DO_NOT_GUESS'})
   for z,e in sorted_candidates[:5]:
    # Only exact identity can ever qualify for promotion, and that still
    # requires independently verified finishing outcome. No records scored here.
    results.append({'priority':t['priority'],'review_class':typ,'target_date':t['date'],
      'target_course':t['course'],'target_time':t['time'],'horse':t['horse'],'horse_key':t['horse_key'],
      'current_age':card.get('age'),'current_sex':card.get('sex'),'last_run_days':card.get('last_run'),
      'expected_last_run_date':e['expected_last_run_date'],'candidate_source_race_id':z['race_id'],
      'candidate_date':z['race_date'],'candidate_course':z['course'],'candidate_finish_pos':z['finish_pos'],
      'candidate_outcome':z['outcome'],'candidate_age':z['age'],'candidate_sex':z['sex'],
      'candidate_sire':z['sire'],'candidate_dam':z['dam'],'stable_id_exact':e['stable_id_exact'],
      'pedigree_exact':e['pedigree_exact'],'age_compatible':e['age_compatible'],
      'sex_compatible':e['sex_compatible'],'last_run_date_exact':e['last_run_date_exact'],
      'source_name_matches':len(candidates),'plausible_age_matches':agecount,'age_sex_matches':agesexcount,
      'exact_last_run_matches':datedcount,
      'recovery_status':'UNVERIFIED_RESEARCH_LEAD',
      'notes':'Name/date/age/sex corroboration is not proof of identity; do not score without stable ID or exact pedigree and verified finish.'})
 write_csv(out/'stage91-identity-evidence-queue.csv',results,COLS)
 write_csv(out/'stage91-exact-identity-outcomes-needing-review.csv',unresolved,UNRES)
 summary={'stage':91,'mode':'READ_ONLY_IDENTITY_EVIDENCE_TRIAGE',
  'window':{'start':str(start),'end':str(end),'days':days},
  'stage90_cases':len(cases),'cases_inspected':checked,'snapshots_missing':missing_snapshot,
  'snapshots_ambiguous':ambiguous_snapshot,'source_evidence_classes':dict(distribution),
  'unique_horses_by_review_class':{k:len(v) for k,v in unique.items()},
  'candidate_stats':dict(candidate_stats),'candidate_queue_rows':len(results),
  'exact_identity_unresolved_outcome_rows':len(unresolved),'verified_auto_recoveries':0,
  'limitations':['Racing horse names may repeat across decades; exact name alone is never an ID.',
                 'Age, sex, last-run date are research leads, not validated identities.',
                 'Historical source cutoff 2026-10-07; never treat a later absence as proof of debut.',
                 'No HPR scores, raw results, models D/D(c), or live selections altered.']}
 (out/'stage91-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps(summary,indent=2))
 return summary

def self_test():
 assert normsex('G')=='gelding' and normsex('filly')=='filly'
 a=dt.date(2026,10,20)
 card={'horse':'A','horse_id':'hrs123','age':'5','sex':'gelding','last_run':'10','sire':'S','dam':'D'}
 good={'race_date':'2026-10-10','age':5,'sex':'g','horse_source_id':'x','sire':'','dam':''}
 e=match_evidence(card,good,a)
 assert e['last_run_date_exact'] and e['age_compatible'] and e['sex_compatible']
 assert not e['stable_id_exact'] and not e['pedigree_exact']
 assert classify([(good,e)])=='DATE_AGE_SEX_REVIEW'
 old=dict(good,race_date='1993-11-15',age=5)
 assert match_evidence(card,old,a)['age_compatible'] is False
 assert classify([(good,match_evidence(card,good,a))])!='STRONG_IDENTITY_REVIEW'
 card2=dict(card,horse_id='x')
 assert match_evidence(card2,good,a)['stable_id_exact']
 card3=dict(card,horse_id='different')
 exact=dict(good,sire='S',dam='D')
 assert match_evidence(card3,exact,a)['pedigree_exact']
 with tempfile.TemporaryDirectory() as td:
  p=pathlib.Path(td)/'test.csv';write_csv(p,[{'horse':'A','priority':'P1'}],['horse','priority'])
  assert read_csv(p)[0]['horse']=='A'
 print('PASS: Stage91 age/date/sex review leads never promoted to exact identity; old same-name horse excluded')

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--self-test',action='store_true')
 p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
 p.add_argument('--db',type=pathlib.Path)
 p.add_argument('--stage90-csv',type=pathlib.Path)
 p.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage91'))
 p.add_argument('--as-of',type=str,default='')
 p.add_argument('--days',type=int,default=30)
 a=p.parse_args()
 if a.self_test:self_test();return
 if not (a.db and a.db.is_file() and a.stage90_csv and a.stage90_csv.is_file()):p.error('Require --db and --stage90-csv')
 if not 1<=a.days<=90:p.error('Days must be 1..90')
 asof=hpr.day(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
 if asof is None:p.error('Invalid as-of')
 a.output.mkdir(parents=True,exist_ok=True)
 run(a.root,a.db,a.stage90_csv,a.output,asof,a.days)

if __name__=='__main__':main()
