#!/usr/bin/env python3
"""Model A/B/C/D/Z race-by-race coverage and conservative Model B verified-form fallback.

This script makes an entry for every racecard field. Existing independent model
scores remain authoritative. B's provisional form fallback is not an official
Model B calibrated probability, winner claim or betting selection.
Neither price data nor outcome data enters any ranking.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, math, re
from pathlib import Path
from zoneinfo import ZoneInfo

UTC=dt.timezone.utc
TZ=ZoneInfo('Europe/Dublin')
VERSION='stage38-every-race-assessment-coverage-v1'

def load(p):
 try:return json.loads(Path(p).read_text())
 except (ValueError,OSError):return {}

def dump(p,obj):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n')

def course(s):
 return re.sub('[^a-z0-9]','',re.sub(r'\((?:AW|IRE|GB|IRL)\)','',str(s or ''),flags=re.I).lower()).replace('chelmsfordcity','chelmsford')

def day_of(r):return str(r.get('date') or r.get('race_time') or '')[:10]

def clock(s):
 try:
  d=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
  if not d.tzinfo:d=d.replace(tzinfo=UTC)
  return d.astimezone(TZ).strftime('%H:%M')
 except (ValueError,TypeError):return str(s or '')[:5]

def horse(s):return re.sub('[^a-z0-9]','',re.sub(r'\((?:IRE|GB|FR|USA|GER)\)$','',str(s or ''),flags=re.I).lower())

def nonrunner(r):
 v=re.sub('[^a-z]','',str(r.get('runner_status') or r.get('status') or '').lower())
 return bool(r.get('non_runner') is True or r.get('is_non_runner') is True or str(r.get('number') or '').upper()=='NR' or v in ('nr','nonrunner','scratched','withdrawn'))

def key(r):return (day_of(r),course(r.get('course')),clock(r.get('race_time') or r.get('time')))

def details(summary):
 """Extract only explicitly recorded form counts; never infer missing races."""
 s=str(summary or '')
 o={}
 patterns={
  'career':r'(\d+) wins? and (\d+) top-three finishes? in (\d+) linked prior starts?',
  'recent':r'(\d+) wins? and (\d+) top-three finishes? in the last (\d+) recorded runs?',
  'course':r'at this course, (\d+)/(\d+) wins?',
  'distance':r'at similar distances, (\d+)/(\d+) wins?',
  'rpr':r'average previous-run rpr (\d+(?:\.\d+)?)'
 }
 for k,p in patterns.items():
  m=re.search(p,s,re.I)
  if m:o[k]=tuple(float(t) for t in m.groups())
 return o

def fallback(r,median_rpr):
 if r.get('score') is not None:
  try:
   score=float(r['score'])
   if math.isfinite(score):return score,'PUBLISHED_B_RESEARCH_SCORE',details(r.get('evidence_summary'))
  except (TypeError,ValueError):pass
 if r.get('evidence_type') not in ('VERIFIED_HORSE_FORM_ONLY','VERIFIED_RACE_HISTORY','VERIFIED_HORSE_FORM'):
  return None,'INSUFFICIENT_VERIFIED_FORM',details(r.get('evidence_summary'))
 d=details(r.get('evidence_summary'))
 career=d.get('career')
 if not career or career[2]<1:return None,'NO_VERIFIED_FORM_STARTS',d
 wins,places,n=career
 # No fixed SP/market input and no false winning percentage. Conservative
 # form-only relative strength, not a trained probability.
 score=12*math.log1p(n)+22*(wins+0.6)/(n+3)+8*(places+1)/(n+3)
 if recent:=d.get('recent'):
  w,p,r=recent
  score+=18*(w+.5)/(r+2)+8*(p+.6)/(r+2)
 for typ,boost in (('course',8),('distance',7)):
  if item:=d.get(typ):
   w,r=item
   score+=boost*(w+.6)/(r+3)
 if d.get('rpr') and median_rpr is not None:
  score+=max(-7.,min(7.,(d['rpr'][0]-median_rpr)*.18))
 return round(score,4),'PROVISIONAL_VERIFIED_FORM_ONLY',d

def valid_field(source,starters,prob_key):
 if not source or not source.get('complete_field',True):return False
 a=source.get('runners') or []
 active={horse(x.get('horse')) for x in starters}
 before={horse(x.get('horse')) for x in a}
 if not active or active!=before:return False
 if prob_key:
  v=[x.get(prob_key) for x in a]
  try:return all(x is not None and math.isfinite(float(x)) and float(x)>=0 for x in v) and abs(sum(map(float,v))-1)<.008
  except (ValueError,TypeError):return False
 return True

def build(root,date):
 data=Path(root)/'data'
 cards=load(data/'racecards.json')
 raw=collections.defaultdict(list)
 for r in cards.get('runners',[]):
  if day_of(r)!=date or not r.get('horse') or not r.get('course') or not r.get('race_time'):continue
  raw[key(r)].append(r)
 groups={k:[r for r in rs if not nonrunner(r)] for k,rs in raw.items()}
 groups={k:rs for k,rs in groups.items() if len(rs)>=2}
 b=load(data/'strategy-b-probabilities.json')
 feeds={'A':load(data/'strategy-probabilities.json'),
        'B':b,'C':load(data/'strategy-c-probabilities.json'),
        'D':load(data/'strategy-d-probabilities.json'),
        'Z':load(data/'strategy-z-probabilities.json')}
 indexes={}
 for model,f in feeds.items():
  indexes[model]={key(r):r for r in f.get('races',[]) if r.get('date')==date}
 out={'version':1,'day':date,'generated_at':dt.datetime.now(UTC).isoformat(),
      'model':'B','races':[],'mode':'FORM_FALLBACK_NO_WIN_PROBABILITIES',
      'description':'Published Model B historical scores remain primary; provisional only when verified past horse-form exists. Never a betting selection.'}
 coverage={'version':1,'day':date,'generated_at':out['generated_at'],
           'policy':'Assess every usable field. Missing or stale sources are not fabricated. B uses explicitly provisional verified-form ranking.',
           'races':[],'summary':{}}
 counts=collections.Counter()
 for k,rs in sorted(groups.items()):
  original=indexes['B'].get(k, {})
  seen={horse(r.get('horse')):r for r in original.get('runners',[])}
  form=[]
  for r in rs:
   f=seen.get(horse(r['horse']),{})
   form.append({'horse':r['horse'],'horse_id':r.get('horse_id'),
        'evidence_type':f.get('evidence_type'),'evidence_summary':f.get('evidence_summary'),
        'form_runs':f.get('form_runs'),'score':f.get('score')})
  rpr_values=[details(x.get('evidence_summary')).get('rpr',(None,))[0] for x in form]
  actual=sorted(x for x in rpr_values if x is not None)
  median=actual[len(actual)//2] if actual else None
  for r in form:
   r['research_score'],r['ranking_basis'],r['verified_form']=fallback(r,median)
  ranked=sorted(form,key=lambda r:(r['research_score'] is None,-(r['research_score'] or 0),horse(r['horse'])))
  for j,r in enumerate(ranked):r['rank']=j+1 if r['research_score'] is not None else None
  valid=sum(r['rank'] is not None for r in ranked)
  best=next((r for r in ranked if r['rank']==1),None)
  full=valid==len(rs)
  state='PUBLISHED_RESEARCH_RANKING' if full and all(r['ranking_basis']=='PUBLISHED_B_RESEARCH_SCORE' for r in ranked) else ('PROVISIONAL_FORM_RANKING' if valid>=2 else 'INSUFFICIENT_VERIFIED_FORM')
  b_race={'model':'B','version':1,'date':date,'course':rs[0]['course'],'time':k[2],
    'race_time':rs[0]['race_time'],'race_type':original.get('race_type') or rs[0].get('race_type') or 'unknown',
    'event_name':rs[0].get('race_name'),'field_size':len(rs),'runners':ranked,
    'complete_field':full,'partial_research':not full,'state':state,
    'leader':best['horse'] if best else None,'selection':None,
    'selection_status':'NO_BETTING_SELECTION',
    'verdict':'Independent B research ranked the whole field.' if state=='PUBLISHED_RESEARCH_RANKING' else
       ('A provisional relative-form leader is shown from verified prior runs; named-race trend research is incomplete, so no official B winner probability or betting selection is inferred.' if best else
        'Model B has insufficient verified form to give this race a defensible ranking.'),
    'data_source':'PUBLISHED_B_OR_VERIFIED_FORM_ONLY','missing_rankings':len(rs)-valid,
    'generated_at':out['generated_at']}
  out['races'].append(b_race)
  statuses={}
  active={horse(r['horse']) for r in rs}
  for model in ('A','C','D','Z'):
   r=indexes[model].get(k)
   if model=='Z' and not r:
    statuses[model]='NO_APPLICABLE_SPECIALIST'
   elif not r or feeds[model].get('day')!=date:
    statuses[model]='PENDING_INDEPENDENT_FORECAST'
   elif {horse(x.get('horse')) for x in r.get('runners',[])}!=active:
    statuses[model]='STALE_AFTER_FIELD_CHANGE'
   elif model=='A' and not valid_field(r,rs,'probability'):
    statuses[model]='INCOMPLETE_PROBABILITY_FIELD'
   elif model=='C' and not valid_field(r,rs,'win_probability'):
    statuses[model]='INCOMPLETE_PROBABILITY_FIELD'
   else:statuses[model]='ASSESSED'
  statuses['B']=state
  for model,status in statuses.items():counts[model+':'+status]+=1
  coverage['races'].append({'date':date,'course':rs[0]['course'],'time':k[2],
    'race_time':rs[0]['race_time'],'runners':len(rs), 'models':statuses,
    'B_coverage':{'scored':valid,'total':len(rs),'ranking_basis':state},
    'betting_selection_required':False})
 coverage['summary']={'races':len(groups),'model_status_counts':dict(sorted(counts.items())),
   'model_z_live_strategies':0}
 dump(data/'strategy-b-all-races.json',out)
 dump(data/'model-assessment-coverage.json',coverage)
 return coverage

def self_test():
 f={'horse':'X','evidence_type':'VERIFIED_HORSE_FORM_ONLY',
    'evidence_summary':'2 wins and 3 top-three finishes in 10 linked prior starts; 1 win and 2 top-three finishes in the last 5 recorded runs; at this course, 0/2 wins; at similar distances, 2/4 wins; average previous-run rpr 99.0.'}
 score,basis,parts=fallback(f,94)
 assert score>0 and basis=='PROVISIONAL_VERIFIED_FORM_ONLY' and parts['recent']==(1.,2.,5.)
 assert fallback({'horse':'Y','evidence_type':'HISTORY_UNVERIFIED','evidence_summary':''},None)[0] is None
 assert course('Dundalk (AW)')=='dundalk'
 assert clock('2026-11-03T20:00:00Z')=='20:00'
 assert nonrunner({'number':'NR'})
 print('Stage38 full-field bridge self-test passed')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--repo',default='.');p.add_argument('--day');p.add_argument('--self-test',action='store_true');a=p.parse_args()
 if a.self_test:self_test()
 else:
  day=a.day or dt.datetime.now(TZ).date().isoformat()
  print(json.dumps(build(a.repo,day)['summary'],indent=2))
