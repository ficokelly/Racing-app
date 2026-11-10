#!/usr/bin/env python3
"""Stage95a: auditable HPR-v2 Rank1/2 out-of-sample benchmark vs unchanged D/E replay.

Scope: pre-11-Oct calibration, 11-23 Oct 2026 held-out comparison where
both Kaggle commentary and historical Stage95 model snapshots exist.
Research only. Does not change any production model or HPR ledger.

This is HPR-v2-only benchmarking, NOT a claim that D/E+comments is implemented.
Original five-model Stage95 rank 1 rows from immutable previous artifact are
reused without recalculating/relabeling them.  Original D/E rank 2 unavailable.
Prices are obtained independently from exact Kaggle race/runner source, never
used for ranking or calibration. A source conflict excludes ROI for that row.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import gzip
import io
import json
import math
import pathlib
import re
import statistics
import tempfile
import unicodedata
import zipfile
from zoneinfo import ZoneInfo

VERSION='stage95a-hpr-v2-heldout-research-v1'
LOCALE=ZoneInfo('Europe/Dublin')
TRAIN_START='2026-09-10'
TRAIN_END='2026-10-10'
TEST_START='2026-10-11'
TEST_END='2026-10-23'
TEMPS=(3.,5.,7.,10.,12.,15.,20.,25.,30.,40.,50.,70.,100.)
THRESHOLDS=(20,25,30,34,40,42,45,50)
NR={'NR','NON RUNNER','NON-RUNNER','WITHDRAWN','SCRATCHED','DNS'}


def norm(v):
 s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode('ascii').upper()
 return re.sub('[^A-Z0-9]','',s)

def venue(v):
 s=re.sub(r'\((?:AW|IRE|GB|UK)\)','',str(v or ''),flags=re.I)
 return norm(s)

def clock(v):
 v=str(v or '').strip()
 if re.fullmatch(r'\d{1,2}:\d{2}',v):
  hh,mm=map(int,v.split(':'))
  return f'{hh:02d}:{mm:02d}' if hh<24 and mm<60 else ''
 return ''

def utc_to_local_clock(date,utc):
 if not clock(utc):return ''
 try:
  t=dt.datetime.fromisoformat(date+'T'+clock(utc)+':00+00:00')
  return t.astimezone(LOCALE).strftime('%H:%M')
 except ValueError:return ''

def position(v):
 s=str(v or '').strip().lower()
 m=re.fullmatch(r'0*(\d+)(?:st|nd|rd|th)?',s)
 return int(m.group(1)) if m and int(m.group(1))>0 else None

def num(v):
 try:
  if v is None or str(v).strip()=='':return None
  f=float(v)
  return f if math.isfinite(f) else None
 except (TypeError,ValueError):return None

def price(v):
 s=str(v or '').strip().upper().replace(' ','')
 if not s or s in ('-','N/A','NR','SCR','NULL'):return None
 if s in ('E','EVS','EVENS','EVEN'):return 2.
 m=re.fullmatch(r'(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)',s)
 if m and float(m.group(2))>0:return 1+float(m.group(1))/float(m.group(2))
 if re.fullmatch(r'\d+(?:\.\d+)?',s):
  x=float(s)
  # Bare 3.5 is ambiguous (could be decimal or fractional); exclude.
  return None
 return None

def zip_csv(path,member):
 with zipfile.ZipFile(path) as z:
  with z.open(member) as raw:
   if member.endswith('.gz'):
    with gzip.open(raw,'rt',encoding='utf-8-sig',newline='') as data:
     yield from csv.DictReader(data)
   else:
    with io.TextIOWrapper(raw,encoding='utf-8-sig',newline='') as data:
     yield from csv.DictReader(data)

def zip_json(path,member):
 with zipfile.ZipFile(path) as z:return json.loads(z.read(member))

def load_hpr_races(stage92,stage94):
 rows92={};dups92=set()
 for r in zip_csv(stage92,'stage92-per-run-quality.csv.gz'):
  d=r.get('date','')
  if not TRAIN_START<=d<=TEST_END:continue
  k=(d,venue(r.get('course')),str(r.get('source_race_id')),r.get('horse_identity'),norm(r.get('horse')))
  if k in rows92:dups92.add(k)
  else:rows92[k]=r
 groups=collections.defaultdict(list)
 missing=collections.Counter()
 for r in zip_csv(stage94,'stage94-hpr-v2-research.csv.gz'):
  d=r.get('date','')
  if not TRAIN_START<=d<=TEST_END:continue
  k=(d,venue(r.get('course')),str(r.get('source_race_id')),r.get('horse_identity'),norm(r.get('horse')))
  s=rows92.get(k)
  if not s or k in dups92:
   missing['missing_or_duplicate_stage92']+=1
   continue
  if s['off']!=r['off']:
   missing['off_clock_conflict']+=1
   continue
  if num(r.get('stage92_quality'))!=num(s.get('quality_score')):
   missing['completed_quality_conflict']+=1
   continue
  if not r.get('horse_identity','').startswith('PEDIGREE_SHA256_'):
   missing['no_candidate_pedigree_identity']+=1
   continue
  rac=(d,venue(r.get('course')),str(r.get('source_race_id')),clock(r.get('off')))
  groups[rac].append((r,s))
 good=[]
 for race,entries in sorted(groups.items()):
  if not race[2] or not race[3] or len(entries)<3:
   missing['no_usable_unique_race']+=1;continue
  horses=[norm(r['horse']) for r,s in entries]
  if len(set(horses))!=len(horses):
   missing['duplicate_horses_in_race']+=1;continue
  # Require all starters, including PU/F/UR, and one verified winner.
  observed={int(s['observed_starter_count']) for r,s in entries if str(s.get('observed_starter_count') or '').isdigit()}
  if len(observed)!=1 or next(iter(observed))!=len(entries):
   missing['unverified_complete_field']+=1;continue
  # Stage92 exports the verified starter count but not original nonfinish codes.
  # A nonrunner would make the observed starter count differ from the row count.
  winners=[i for i,(_,s) in enumerate(entries) if position(s.get('position'))==1]
  if len(winners)!=1:
   missing['no_unique_winner']+=1;continue
  valid=[i for i,(r,s) in enumerate(entries) if num(r.get('pre_race_hpr_v2')) is not None]
  if len(valid)<2 or len(valid)/len(entries)<.70:
   missing['hpr_coverage_below_70_percent']+=1;continue
  if len(valid)<len(entries):
   missing['races_with_neutral_missing_hpr']+=1
   rating=[num(entries[i][0].get('pre_race_hpr_v2')) for i in valid]
   neutral=statistics.median(rating)
   all_scores=[num(row.get('pre_race_hpr_v2')) if num(row.get('pre_race_hpr_v2')) is not None else neutral for row,src in entries]
   top2=sorted(range(len(entries)), key=lambda i:(-all_scores[i],norm(entries[i][0]['horse'])))[:2]
   if any(i not in valid for i in top2):
    missing['neutral_missing_hpr_in_top_two']+=1
    continue
  good.append({'key':race,'entries':entries,'winner_idx':winners[0],'rated_idxs':valid})
 return good,dict(missing)

def scores(race):
 vals=[num(race['entries'][i][0]['pre_race_hpr_v2']) for i in race['rated_idxs']]
 middle=statistics.median(vals)
 return [num(r.get('pre_race_hpr_v2')) if num(r.get('pre_race_hpr_v2')) is not None else middle for r,s in race['entries']]

def softmax(sc,temperature):
 m=max(sc)
 v=[math.exp((x-m)/temperature) for x in sc]
 tot=sum(v)
 return [x/tot for x in v]

def choose_temp(races):
 logloss=[]
 for t in TEMPS:
  loss=[]
  for race in races:
   p=softmax(scores(race),t)
   loss.append(-math.log(max(1e-12,p[race['winner_idx']])))
  logloss.append((sum(loss)/len(loss),t))
 return min(logloss)[1],dict((str(t),round(err,5)) for err,t in logloss)

def sp_map(dataset_dir):
 by=collections.defaultdict(list)
 total=0
 for file in sorted(pathlib.Path(dataset_dir).rglob('*.csv')):
  if file.name.lower().startswith(('readme','metadata')):continue
  with file.open('r',encoding='utf-8-sig',errors='replace',newline='') as fh:
   rd=csv.DictReader(fh)
   if not {'date','course','race_id','horse','pos','sp'}.issubset(set(rd.fieldnames or [])):continue
   for r in rd:
    d=str(r.get('date') or '')[:10]
    if not TEST_START<=d<=TEST_END:continue
    k=(d,venue(r.get('course')),str(r.get('race_id')),norm(r.get('horse')))
    by[k].append((str(r.get('sp') or ''),price(r.get('sp')),position(r.get('pos'))))
    total+=1
 resolved={}
 for k,rows in by.items():
  # Do not guess between duplicates / ambiguous SP formats.
  if len(rows)==1:resolved[k]=rows[0]
 return resolved, {'source_rows_in_window':total,'unique_price_keys':len(resolved),'ambiguous_keys':len(by)-len(resolved)}

def settle(k,horse,won,finish,prices):
 p=prices.get((k[0],k[1],k[2],norm(horse)))
 if p is None:return ('',None,'MISSING_SOURCE_ROW')
 raw,dec,pos=p
 if pos!=finish:return (raw,None,'FINISH_POSITION_CONFLICT')
 if dec is None:return (raw,None,'MISSING_OR_AMBIGUOUS_SP')
 return (raw,dec,'VERIFIED_KAGGLE_SOURCE')

def metrics(rows):
 n=len(rows);wins=sum(int(r['won']) for r in rows)
 priced=[r for r in rows if r.get('sp_decimal') is not None]
 profit=sum(float(r['sp_decimal'])-1 if r['won'] else -1 for r in priced)
 return dict(selections=n,winners=wins,strike_rate_pct=round(100*wins/n,2) if n else None,
             priced=len(priced),unpriced=n-len(priced),profit_1u=round(profit,3),
             roi_pct=round(100*profit/len(priced),2) if priced else None,
             avg_win_probability_pct=round(statistics.mean(float(r['win_probability_pct']) for r in rows),2) if rows else None)

def write_csv(path,rows,cols):
 with path.open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rows)

def compare(stage95, hold, prices):
 # Existing Stage95 baseline is frozen Rank 1 for five models only.
 # Baseline probabilities for this date were fitted on an overlapping training
 # range; DO NOT compare baseline calibration vs HPR holdout calibration.
 baseline=collections.defaultdict(list)
 for r in zip_csv(stage95,'output/stage95-all30-all-model-selections.csv'):
  d=r.get('date','')
  if not TEST_START<=d<=TEST_END:continue
  key=(d,venue(r.get('course')),utc_to_local_clock(d,r.get('time')))
  baseline[key].append(r)
 matched=[];unmatched=collections.Counter()
 for race in hold:
  d,course,rid,off=race['key'];k=(d,course,off)
  group=baseline.get(k,[])
  if not group:
   unmatched['no_baseline_race']+=1;continue
  if len(group)!=5 or len(set(x['model'] for x in group))!=5:
   unmatched['missing_five_baselines']+=1;continue
  names={norm(s['horse']):(i,s) for i,(r,s) in enumerate(race['entries'])}
  if len(names)!=len(race['entries']):
   unmatched['duplicate_name']+=1;continue
  new=[]
  for base in group:
   hit=names.get(norm(base['horse']))
   if hit is None:
    unmatched['baseline_horse_not_in_source_field']+=1;new=[];break
   i,s=hit;actual=position(s.get('position'))
   source_sp,source_dec,src=settle(race['key'],s['horse'],actual==1,actual,prices)
   # Ranking is unaltered from the original saved baseline.
   new.append({'date':d,'time_utc':base['time'],'time_local':off,'course':base['course'],
    'source_race_id':rid,'model':base['model'],'rank':1,'horse':base['horse'],
    'win_probability_pct':base['win_probability_pct'],'finish':s.get('position') or 'UNPLACED_OR_UNFINISHED',
    'won':int(actual==1),'sp':source_sp,'sp_decimal':source_dec,'price_status':src,
    'probability_provenance':'BASELINE_CALIBRATION_TRAINING_DATES_NOT_FAIR_HOLDOUT',
    'source':'UNCHANGED_STAGE95_FIVE_MODEL_REPLAY'})
  if new:
   hk=d,course,rid,off
   matched.append((hk,new))
 return matched,dict(unmatched)

def run(a):
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 for p in (a.stage92_zip,a.stage94_zip,a.stage95_zip):
  if not pathlib.Path(p).is_file():raise RuntimeError('Missing required immutable artifact '+str(p))
 s92=zip_json(a.stage92_zip,'stage92-summary.json')
 s94=zip_json(a.stage94_zip,'stage94-summary.json')
 s95=zip_json(a.stage95_zip,'output/stage95-summary.json')
 if (s92.get('version')!='stage92-performance-quality-research-v1' or
     s94.get('version')!='hpr-v2-research-2026-11-10' or
     s95.get('version')!='stage95-v1-2026-11-10'):
  raise RuntimeError('Pinned research source version changed')
 allr,rejected=load_hpr_races(a.stage92_zip,a.stage94_zip)
 train=[r for r in allr if TRAIN_START<=r['key'][0]<=TRAIN_END]
 held=[r for r in allr if TEST_START<=r['key'][0]<=TEST_END]
 if len(train)<200 or len(held)<50:
  raise RuntimeError('Not enough complete pre-race history/heldout fields')
 t,losses=choose_temp(train)
 prices,ps=sp_map(a.pedigree_source) if a.pedigree_source else ({},{'status':'NOT_PROVIDED_ALL_ROI_UNAVAILABLE'})
 selections=[]; bykey={}
 for race in held:
  k=race['key']; sc=scores(race);prob=softmax(sc,t)
  if abs(sum(prob)-1)>1e-8:raise RuntimeError('Win probabilities not coherent')
  order=sorted(race['rated_idxs'],key=lambda i:(-sc[i],norm(race['entries'][i][0]['horse'])))
  current=[]
  for rank,i in enumerate(order[:2],1):
   r,s=race['entries'][i];actual=position(s.get('position'))
   sp,dec,price_status=settle(k,r['horse'],actual==1,actual,prices)
   row={'date':k[0],'course':r['course'],'time_local':k[3],
    'source_race_id':k[2],'model':'HPR_V2_ONLY','rank':rank,'horse':r['horse'],
    'win_probability_pct':round(100*prob[i],3),'finish':s.get('position') or 'UNPLACED_OR_UNFINISHED',
    'won':int(actual==1),'sp':sp,'sp_decimal':dec,'price_status':price_status,
    'pre_race_hpr_v2':r.get('pre_race_hpr_v2'),'coverage_fraction':round(len(race['rated_idxs'])/len(race['entries']),3),
    'race_field_size':len(race['entries']),'research_only':True}
   current.append(row);selections.append(row)
  bykey[k]=current
 matched,miss=compare(a.stage95_zip,held,prices)
 complete=[];matched_keys=set()
 for key,base in matched:
  matched_keys.add(key)
  complete.extend(base)
  for row in bykey[key]:complete.append(row)
 stats=[];thresholds=[]
 for label,seq in [('HPR_ALL_HOLDOUT',selections),('MATCHED_COMPARISON',complete)]:
  groups=sorted({(x['model'],int(x['rank'])) for x in seq})
  combinations=[(model,rank) for model,rank in groups]
  for model in sorted({x['model'] for x in seq}):
   if (model,1) in groups and (model,2) in groups:
    combinations.append((model,'1+2'))
  for model,rank in combinations:
   subset=[x for x in seq if x['model']==model and (rank=='1+2' or int(x['rank'])==rank)]
   stats.append({'scope':label,'model':model,'rank':rank,**metrics(subset)})
   for threshold in THRESHOLDS:
    hits=[x for x in subset if float(x['win_probability_pct'])>=threshold]
    thresholds.append({'scope':label,'model':model,'rank':rank,'min_probability_pct':threshold,**metrics(hits)})
 fields=['date','course','time_local','source_race_id','model','rank','horse','win_probability_pct',
   'finish','won','sp','sp_decimal','price_status','pre_race_hpr_v2','coverage_fraction','race_field_size',
   'time_utc','probability_provenance','source','research_only']
 write_csv(out/'stage95a-hpr-rank1-rank2.csv',selections,fields)
 write_csv(out/'stage95a-matched-models.csv',complete,fields)
 write_csv(out/'stage95a-scorecard.csv',stats,['scope','model','rank',*metrics([])])
 write_csv(out/'stage95a-probability-thresholds.csv',thresholds,['scope','model','rank','min_probability_pct',*metrics([])])
 report={'stage':'95a','version':VERSION,'research_only':True,
  'training_window':{'start':TRAIN_START,'end':TRAIN_END,'races':len(train)},
  'heldout_window':{'start':TEST_START,'end':TEST_END,'races':len(held),'matched_existing_stage95_races':len(matched)},
  'hpr_temperature':t,'training_logloss_by_temperature':losses,
  'old_d_e_baseline':'Immutable earlier Stage95 five-model historical Rank1 replay only',
  'stage95_source_model_names':s95.get('selected_models'),
  'sp_source':ps,'hpr_field_exclusions':rejected,'model_match_exclusions':miss,
  'scorecard':stats,'safety':{
   'no_outcome_leakage':True,'training_before_holdout':True,'source_history_ends':'2026-10-23',
   'no_original_models_changed':True,'odds_not_used_in_predictions':True,
   'full_d_e_comment_enhanced_variants_implemented':False,'d_e_baseline_rank2_available':False},
  'limitations':[
   'This Stage95a benchmark tests HPR-v2-only Rank1/2, NOT yet a D/E+comment model.',
   'Earlier five-model D/E baseline has Rank1 only. Rank2 needs all-runner replay.',
   'Existing baseline probability calibration included 11-23 October training outcomes; it is NOT out-of-sample for probability calibration. Only HPR-v2 calibration here is pre-11-Oct.',
   'ROI uses verified numeric Kaggle fractional SP only. Missing/ambiguous SP excluded, never assumed losers.',
   'HPR-v2 needs at least 70% pre-race field ratings and two rated runners; missing runners receive neutral median for probability denominator, and races are excluded if an unrated runner would enter the top two.',
   'These are research probabilities from temperature-fitted HPR score differences, not verified forecast quality.',
   'Races after Oct23 have no complete Kaggle comment history; never claim a fair full November comparison.',
   'Kaggle commentary and model training usage require a source-licence check.',
  ]}
 (out/'stage95a-summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
 print(json.dumps({'stage':'95a','training_races':len(train),'heldout_races':len(held),
  'matched_baseline_races':len(matched),'hpr_temperature':t,
  'models':stats,'match_rejections':miss},indent=2),flush=True)
 if not selections:raise RuntimeError('No HPR selections; refusing false success')
 return report


def self_test():
 assert utc_to_local_clock('2026-10-12','15:28')=='16:28'
 assert utc_to_local_clock('2026-11-09','15:28')=='15:28'
 assert price('11/8')==2.375 and price('EVS')==2 and price('') is None
 assert price('3.5') is None
 assert position('7th')==7 and position('PU') is None
 p=softmax([70,60,40],10)
 assert abs(sum(p)-1)<1e-10 and p[0]>p[1]>p[2]
 assert metrics([{'won':1,'sp_decimal':4.,'win_probability_pct':40},
                 {'won':0,'sp_decimal':2.,'win_probability_pct':20}])['roi_pct']==100.
 # Missing SP must be excluded from both numerator and denominator.
 assert metrics([{'won':0,'sp_decimal':None,'win_probability_pct':20}])['roi_pct'] is None
 print('PASS Stage95a: UTC/local alignment, exact-SP parsing, probability mass, no missing-price distortion')


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--stage92-zip',type=pathlib.Path)
 p.add_argument('--stage94-zip',type=pathlib.Path)
 p.add_argument('--stage95-zip',type=pathlib.Path)
 p.add_argument('--pedigree-source',type=pathlib.Path,help='Pinned Kaggle v129 CSV path; used ONLY for post-race SP settlement')
 p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage95a/output'))
 p.add_argument('--self-test',action='store_true')
 a=p.parse_args()
 if a.self_test:self_test();return
 if not all((a.stage92_zip,a.stage94_zip,a.stage95_zip)):
  p.error('All three source artifact ZIPs are required')
 if a.pedigree_source and not a.pedigree_source.is_dir():p.error('Kaggle source must be directory')
 run(a)

if __name__=='__main__':main()
