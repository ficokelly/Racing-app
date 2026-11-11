#!/usr/bin/env python3
"""Stage102: date-locked, price-aware ROI-first machine-learning challengers.

Research only. Compares eight Stage101 / E V2 research model variants with
eight new, separately fitted ML re-rankers. 30-day walk-forward test:
2026-10-13..2026-11-11; warm-up 2026-10-08..12. Historical race outcomes
are never included in features of their own race or exposed to the scorer
before the next UTC calendar day. A complete-field SP oracle is included
SEPARATELY from realistic timestamped bookmaker-price evaluation.

NO production file, live model, betting instruction or historical database write.
A/B Stage101 feature parity remains unverified. E75 variants are prototypes.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, re, statistics, subprocess, sys, warnings
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage101_all_models_compare as S101

WARMUP_START='2026-10-08'
TEST_START='2026-10-13'
TEST_END='2026-11-11'
BASE_MODELS=(
 S101.REPLAY_A,S101.REPLAY_B,S101.REPLAY_C,
 'D_ORIGINAL_REPLAY','D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR',
 'E_V2_STAGE92'  # supplementary: Stage92 had already used some Oct26 outcomes.
)
PRICE_MODES=('PREOFF_TMINUS5','PREOFF_MORNING0945','SP_ORACLE')
POLICIES=('BASE_RANK1','BASE_RANK2','ML_PROB_RANK1','ML_PROB_RANK2','ML_VALUE_RANK1','ML_VALUE_RANK2')
THRESHOLDS=(.05,.10,.15,.20,.30,.40)
ODDS_CAPS=(8.,15.,25.)
MIN_PRIOR_RACES=25
MIN_PRIOR_WINNERS=8
MAX_QUOTE_AGE_MIN=180
UTC=dt.timezone.utc


def timestamp(x):
 if x is None:return None
 try:
  d=dt.datetime.fromisoformat(str(x).strip().replace('Z','+00:00'))
  if d.tzinfo is None:return None
  return d.astimezone(UTC)
 except (ValueError,TypeError):return None


def clock(v):return S101.clock(v)
def norm(s):return S101.norm(s)
def course(v):
 return norm(re.sub(r'\s*\((?:AW|IRE|GB|UK)\)', '', str(v or ''), flags=re.I))

def fval(x):return S101.as_float(x)

def intish(x,default=0):
 try:return int(float(x))
 except (ValueError,TypeError):return default

def write_csv(path,rows,fields):return S101.write_csv(path,rows,fields)

def ml_features(card, base_p, odds, field_size, discipline, race_type):
 """Only before-off card + model rank + observed price; NEVER outcome/SP except oracle odds."""
 rating=next((fval(card.get(k)) for k in ('official_rating','or','ofr') if fval(card.get(k)) is not None),None)
 age=fval(card.get('age'));wt=fval(card.get('weight_lbs'))
 p=max(.0001,min(.9999,float(base_p)))
 implied=1/odds if odds is not None else 1/max(2,field_size)
 return [
  math.log(p/(1-p)), math.log(max(1.01,float(odds))) if odds is not None else 0.,
  math.log(max(.001,implied)),float(odds is None),
  math.log(max(2,field_size)),float((age or 0)/10.),float((rating or 0)/100.),float((wt or 0)/150.),
  float(discipline=='Jumps'),float(discipline=='AW'),
  float('Handicap' in race_type),float('Hurdle' in race_type),float('Chase' in race_type),
  float('Bumper' in race_type),float('Maiden' in race_type),float('Novice' in race_type)
 ]


def off_at(entry):
 # Race time in Stage101 is UTC (race times originate from ISO race_time fields).
 t=clock(entry['cards'][0].get('race_time')) or entry['time']
 return timestamp(entry['date']+'T'+t+':00+00:00')


def decision_at(entry,mode):
 off=off_at(entry)
 if not off:return None
 if mode=='PREOFF_TMINUS5':return off-dt.timedelta(minutes=5)
 if mode=='PREOFF_MORNING0945':return timestamp(entry['date']+'T09:45:00+00:00')
 return None


def result_map(entry):return {norm(r.get('horse')):r for r in entry['result'].get('runners',[])}


def quote_object(raw, origin):
 if not isinstance(raw,dict):return None
 collected=timestamp(raw.get('collected_at'))
 updated=timestamp(raw.get('bookmaker_updated_at'))
 off=timestamp(raw.get('race_time'))
 price=fval(raw.get('decimal_odds'))
 if not collected or not off or price is None or not (1.01<price<=1000):return None
 if raw.get('stale') is True:return None
 if updated is not None and (updated>collected+dt.timedelta(minutes=2) or updated>off):return None
 name=norm(raw.get('horse'));bk=str(raw.get('bookmaker') or '').lower().replace(' ','').replace('_','')
 if not name or not bk:return None
 return {'key':(off.date().isoformat(),course(raw.get('course')),off.strftime('%H:%M'),name),
  'collected':collected,'updated':updated,'price':price,'book':bk,'origin':origin,'committed':None}


class PriceArchive:
 def __init__(self,root):
  self.root=pathlib.Path(root);self.by_key=collections.defaultdict(list)
  self.audit=collections.Counter()

 def add_snapshot(self,obj,origin,committed=None):
  snapshots=obj if isinstance(obj,list) else [obj]
  for snap in snapshots:
   if not isinstance(snap,dict):continue
   for row in snap.get('prices',[]):
    q=quote_object(row,origin)
    if not q:self.audit['bad_or_stale_quotes']+=1;continue
    if q['book']!='williamhill':continue  # Only prices actually available on our chosen bookmaker.
    if not (WARMUP_START<=q['key'][0]<=TEST_END):continue
    q['committed']=committed
    self.by_key[q['key']].append(q)
    self.audit['accepted_quotes']+=1

 def ingest_json(self,file):
  try:self.add_snapshot(json.loads(file.read_text(encoding='utf-8')),file.name)
  except (OSError,ValueError) as e:self.audit['unreadable_files']+=1;print('Skip unreadable odds file',file,e,flush=True)

 def ingest_git_day(self,day):
  """Recover 11-Nov historical quotes if missing from dated archive.

  Commit timestamp proves the repository already possessed the snapshot.
  Reject retrospectively collected data irrespective of in-file timestamps.
  """
  cmd=['git','log','--all','--format=%H %ct',
       '--after='+day+' 00:00:00 +0000',
       '--before='+day+' 23:59:59 +0000','--','data/latest-odds.json']
  try:lines=subprocess.run(cmd,cwd=self.root,check=True,capture_output=True,text=True,timeout=90).stdout.splitlines()
  except (subprocess.SubprocessError,OSError):return
  for i,line in enumerate(lines[:280]):
   try:
    sha,epoch=line.split()[:2];commit=dt.datetime.fromtimestamp(int(epoch),tz=UTC)
    raw=subprocess.run(['git','show',sha+':data/latest-odds.json'],cwd=self.root,check=True,capture_output=True,text=True,timeout=15).stdout
    data=json.loads(raw)
    collected=timestamp(data.get('collected_at'))
    if not collected or collected>commit+dt.timedelta(seconds=60) or collected<commit-dt.timedelta(hours=1):continue
    self.add_snapshot(data,'git_'+day+'_'+sha[:10],committed=commit);self.audit['git_snapshots_verified']+=1
   except (ValueError,subprocess.SubprocessError,json.JSONDecodeError,OSError):self.audit['git_snapshot_skipped']+=1
  print('Stage102 recovered',self.audit['git_snapshots_verified'],'git-verified',day,'odds snapshots',flush=True)

 def load(self):
  directory=self.root/'data/odds-archive'
  for file in sorted(directory.glob('*.json')):
   if not WARMUP_START<=file.name[:10]<=TEST_END:continue
   self.ingest_json(file)
   self.audit['archive_files_scanned']+=1
  # In contrast to current latest-odds.json, immutable git snapshots
  # can establish the historical as-of time for the last test day.
  self.ingest_git_day(TEST_END)
  for key,arr in self.by_key.items():arr.sort(key=lambda r:(r['collected'],r['updated'] or r['collected']))
  print('Stage102 odds audit',dict(self.audit),'race-runner keys',len(self.by_key),flush=True)
  return self

 def get(self,entry,horse,mode):
  at=decision_at(entry,mode)
  off=off_at(entry)
  # Reject pre-09:45 races from 09:45-collected card; both price modes
  # must be AFTER the verified frozen card existed.
  morning=timestamp(entry['date']+'T09:45:00+00:00')
  if not at or not off or at<morning or at>=off:return None
  key=(entry['date'],course(entry['course']),off.strftime('%H:%M'),norm(horse))
  observed=self.by_key.get(key,())
  valid=[q for q in observed if morning-dt.timedelta(days=1)<=q['collected']<=at
         and (at-q['collected']).total_seconds()<=MAX_QUOTE_AGE_MIN*60
         and (q['updated'] is None or q['updated']<=at)
         and (q['committed'] is None or q['committed']<=at)]
  if not valid:return None
  # William Hill only: executable price comparison should not borrow
  # unavailable bookmaker odds and pretend user could obtain them.
  wh=[q for q in valid if q['book']=='williamhill']
  if not wh:return None
  best=max(wh,key=lambda q:(q['collected'],q['updated'] or q['collected']))
  return best


def sp_field(entry):
 """SP ORACLE: refuse partial-field SP, to prevent selection/coverage bias."""
 actual=result_map(entry);values=[]
 for card in entry['cards']:
  r=actual.get(norm(card.get('horse')))
  if not r:return None
  dv=S101.sp_decimal(r.get('sp_decimal')) or S101.sp_decimal(r.get('sp')) or S101.sp_decimal(r.get('starting_price'))
  if dv is None:return None
  values.append(dv)
 return values


def base_probs(spec,n):
 p=spec.get('probabilities')
 if p is not None and len(p)==n:
  arr=np.asarray(p,dtype=float)
 elif spec.get('scores') is not None and len(spec['scores'])==n:
  v=np.asarray(spec['scores'],dtype=float)
  v=v-v.max();arr=np.exp(np.clip(v,-70,5));arr=arr/arr.sum()
 else:return None
 if len(arr)!=n or not np.isfinite(arr).all() or arr.sum()<=0:return None
 return np.clip(arr/arr.sum(),1e-8,.9999999)


def stage92_reference():
 """Original archived E V2 estimates; RETROSPECTIVE, NOT A BLIND TEST."""
 files=list((ROOT/'reports/stage102/reference-e92').rglob('stage92-race-probabilities.csv'))
 if not files:return {}
 by=collections.defaultdict(list)
 with files[0].open(encoding='utf-8-sig',newline='') as f:
  for row in csv.DictReader(f):
   d=str(row.get('date') or '')[:10];t=clock(row.get('time'))
   if WARMUP_START<=d<=TEST_END and t:
    by[(d,norm(row.get('course')),t)].append(row)
 return by


def gather_candidates(entries,original,de,archive):
 """Prepares dates and feature rows. Winners are kept in SEALED settlement,
    never in a scoring feature. Per-race SP is used only in SP_ORACLE.
 """
 allby=collections.defaultdict(list);rejects=[];counts=collections.Counter();e92=stage92_reference()
 for entry in entries:
  rk=entry['key'];cards=entry['cards'];size=len(cards)
  candidate={**original.get(rk,{}),**de.get(rk,{})}
  prior=e92.get(rk,[])
  if prior and len(prior)==size and {norm(z.get('horse')) for z in prior}=={norm(c.get('horse')) for c in cards}:
   byhorse={norm(z['horse']):z for z in prior}
   try:
    eprobs=[float(byhorse[norm(c['horse'])]['win_probability_pct'])/100 for c in cards]
    if all(0<x<1 for x in eprobs) and .995<=sum(eprobs)<=1.005:
     candidate['E_V2_STAGE92']={'scores':eprobs,'probabilities':eprobs}
   except (KeyError,ValueError,TypeError):pass
  winning=result_map(entry)
  for model in BASE_MODELS:
   spec=candidate.get(model)
   if spec is None:
    rejects.append((entry['date'],entry['course'],entry['time'],model,'MISSING_ORIGINAL_MODEL_VECTOR'));continue
   p=base_probs(spec,size)
   if p is None:
    rejects.append((entry['date'],entry['course'],entry['time'],model,'MISSING_FULL_FIELD_SCORE'));continue
   base_order=sorted(range(size),key=lambda i:(-float(p[i]),i))
   for mode in PRICE_MODES:
    if mode=='SP_ORACLE':
     full=sp_field(entry)
     if full is None:
      counts['sp_oracle_incomplete_field']+=1;continue
     prices=[{'price':v,'collected':None,'book':'SP_ORACLE','origin':'POST_OFF_ONLY'} for v in full]
    else:
     prices=[archive.get(entry,c.get('horse'),mode) for c in cards]
     total=sum(q is not None for q in prices)
     if total<max(2,math.ceil(.70*size)):
      counts['incomplete_preoff_'+mode]+=1;continue
    # Features contain NO finishing, SP or winner columns in the live-price path.
    features=[ml_features(c,p[i],prices[i]['price'] if prices[i] else None,
                          size,entry['discipline'],entry['race_type']) for i,c in enumerate(cards)]
    # Settlement is sealed; not available to ML until the date has passed.
    actual=[winning.get(norm(c.get('horse'))) for c in cards]
    if any(x is None for x in actual):raise RuntimeError('Field became mismatched after strict Stage101 verification')
    y=[intish(x.get('position'))==1 for x in actual]
    if sum(y)!=1:raise RuntimeError('Exactly one verified winner required')
    sp=[S101.sp_decimal(x.get('sp_decimal')) or S101.sp_decimal(x.get('sp')) or S101.sp_decimal(x.get('starting_price')) for x in actual]
    allby[(entry['date'],model,mode)].append({
      'entry':entry,'model':model,'mode':mode,'features':np.asarray(features,dtype=float),
      'base_p':p,'base_order':base_order,'quotes':prices,
      'sealed_y':y,'sealed_sp':sp,'date':entry['date'],
    })
    counts['records_'+mode]+=1
 return allby,rejects,counts


def fit_challenger(previous):
 """Do not train on current/race-day outcomes."""
 if len(previous)<MIN_PRIOR_RACES:return None,'INSUFFICIENT_PRIOR_RACES'
 if sum(sum(r['sealed_y']) for r in previous)<MIN_PRIOR_WINNERS:return None,'INSUFFICIENT_PRIOR_WINNERS'
 xx=np.concatenate([r['features'] for r in previous],axis=0)
 yy=np.asarray([int(y) for r in previous for y in r['sealed_y']],dtype=int)
 if len(set(yy))!=2:return None,'ONE_CLASS_TRAINING'
 try:
  clf=make_pipeline(StandardScaler(),LogisticRegression(C=.15,max_iter=240,solver='lbfgs'))
  clf.fit(xx,yy)
  return clf,'TRAINED_WITH_PAST_RACES_ONLY'
 except Exception as e:return None,'TRAIN_FAILURE_'+type(e).__name__


def probabilities(model,record):
 x=np.asarray(record['features'],dtype=float)
 p=model.predict_proba(x)[:,1]
 if not np.isfinite(p).all() or p.sum()<=0:raise RuntimeError('Invalid model probabilities')
 pp=np.clip(p,1e-6,1.0);pp=pp/pp.sum()
 if abs(float(pp.sum())-1)>1e-8:raise RuntimeError('Probabilities not whole-field normalized')
 return pp


def evaluated_candidate(record,p):
 """One prediction per race; no settlement read at selection time."""
 values=[]
 for i,q in enumerate(record['quotes']):
  if q is None:continue
  ev=float(p[i])*q['price']-1.
  values.append((ev,-float(p[i]),i))
 values.sort(reverse=True)
 return values


def choose_policy(previous_predictions):
 """Prior *out-of-sample* decisions, ROI primary; downside/coverage penalized.

 No current/future outcomes, no in-sample fitted outcomes, no strike-rate target.
 Conservative fallback if previous decisions are too few for a useful policy.
 """
 fallback=(.15,15.)
 candidates=[]
 for threshold in THRESHOLDS:
  for cap in ODDS_CAPS:
   vals=[];days=set();win=0
   for r in previous_predictions:
    eligible=[x for x in r['choices'] if x['ev']>=threshold and 1.01<x['odds']<=cap]
    if not eligible:continue
    chosen=max(eligible,key=lambda x:(x['ev'],x['probability']))
    vals.append(chosen['profit']);days.add(r['date']);win+=chosen['won']
   if len(vals)<22 or len(days)<4 or win<3:continue
   roi=statistics.mean(vals)
   uncertainty=(statistics.pstdev(vals)/math.sqrt(len(vals))) if len(vals)>1 else 100
   # ROI first, then one-sided safety bound and less fragile selections.
   score=roi-1.0*uncertainty-6/len(vals)
   candidates.append((score,len(vals),threshold,cap))
 if not candidates:return (*fallback,'FIXED_CONSERVATIVE_NO_PRIOR_OOS_POLICY')
 best=max(candidates,key=lambda x:(x[0],x[1]))
 if best[0]<=0:return (*fallback,'LOWER_CONFIDENCE_ROI_NOT_POSITIVE_USE_FIXED')
 return (best[2],best[3],'TRAINED_ONLY_PRIOR_OOS_RISK_ADJUSTED_ROI')


def single_result(r,pick,policy,params,train_races,train_last,notes=''):
 """Settlement performed only AFTER all predictions for this date are sealed."""
 i=pick['i'];price=pick['price'];y=int(r['sealed_y'][i]);sp=r['sealed_sp'][i]
 meta=r['entry'];quote=r['quotes'][i]
 return {'date':meta['date'],'off_utc':meta['time'],'course':meta['course'],
   'discipline':meta['discipline'],'race_type':meta['race_type'],'model':r['model'],
   'price_mode':r['mode'],'policy':policy,'rank':pick.get('rank',''),
   'horse':meta['cards'][i].get('horse'),'pre_off_bookmaker':quote.get('book') if quote else '',
   'quote_timestamp':quote['collected'].isoformat() if quote and quote['collected'] else '',
   'quote_source':quote.get('origin','') if quote else '',
   'decision_timestamp':decision_at(meta,r['mode']).isoformat() if r['mode']!='SP_ORACLE' else 'AFTER_OFF_SP_ORACLE',
   'quote_age_min':round((decision_at(meta,r['mode'])-quote['collected']).total_seconds()/60,2)
        if quote and quote['collected'] and r['mode']!='SP_ORACLE' else '',
   'observed_odds_decimal':price,'predicted_win_pct':round(100*float(pick.get('probability',0)),3) if 'probability' in pick else '',
   'predicted_expected_roi_pct':round(100*float(pick.get('ev',0)),2) if 'ev' in pick else '',
   'winner':y,'finish_position':result_map(meta).get(norm(meta['cards'][i].get('horse')),{}).get('position',''),
   'sp_decimal':sp if sp else '',
   'profit_at_quote_units':round(price-1 if y else -1.,5),
   'profit_at_sp_units':round(sp-1 if y else -1.,5) if sp else '',
   'prior_training_races':train_races,'train_last_date':train_last,
   'policy_source':params,'limitations':notes}


def run_walkforward(grouped):
 """Group by model/price mode; predict a FULL UTC date before revealing it.

 The sealed_Y and sealed_SP properties are read only in settlement after all
 decisions for that entire day; time/date leakage assertions are explicit.
 """
 results=[];audit=[];accuracy=[]
 groups={(m,pm) for day,m,pm in grouped}
 for model,mode in sorted(groups):
  history=[];prior_oos=[]
  days=sorted({day for day,m,pm in grouped if m==model and pm==mode})
  for day in days:
   today=grouped[(day,model,mode)]
   # All prior training items must have a STRICTLY earlier date.
   assert all(x['date']<day for x in history)
   estimator,status=fit_challenger(history)
   threshold,cap,src=choose_policy(prior_oos)
   today_pred=[]
   for rec in today:
    if estimator is None:
     today_pred.append((rec,None,[]));continue
    p=probabilities(estimator,rec)
    # Choice objects are pure; y and SP are not accessed until settlement.
    choices=[]
    for ev,_,i in evaluated_candidate(rec,p):
     q=rec['quotes'][i]
     choices.append({'ev':ev,'i':i,'odds':q['price'],'probability':float(p[i])})
    today_pred.append((rec,p,choices))
   # Prediction/selection stage: NO sealed outcomes read here.
   pending=[]
   for rec,p,choices in today_pred:
    rec_d=rec['entry'];base_i=rec['base_order']
    for rank,i in enumerate(base_i[:2],1):
     if rec['quotes'][i] is None:continue
     q=rec['quotes'][i]
     pending.append((rec,{'i':i,'rank':rank,'price':q['price']},f'BASE_RANK{rank}',None))
    if p is None:continue
    p_order=sorted(range(len(p)),key=lambda i:(-float(p[i]),i))
    for rank,i in enumerate(p_order[:2],1):
     if rec['quotes'][i] is None:continue
     q=rec['quotes'][i]
     pending.append((rec,{'i':i,'rank':rank,'price':q['price'],'probability':float(p[i]),
                      'ev':float(p[i])*q['price']-1},f'ML_PROB_RANK{rank}',None))
    qualified=[v for v in choices if v['ev']>=threshold and 1.01<v['odds']<=cap]
    for rank,v in enumerate(qualified[:2],1):
     pending.append((rec,{'i':v['i'],'rank':rank,'price':v['odds'],
                          'probability':v['probability'],'ev':v['ev']},f'ML_VALUE_RANK{rank}',None))
   # SEAL all predictions, THEN settle outcomes and update prior ML policy history.
   daily_oos=[]
   for rec,p,choices in today_pred:
    if p is None:continue
    true_idx=next(i for i,v in enumerate(rec['sealed_y']) if v)
    accuracy.append({'date':day,'model':model,'price_mode':mode,'course':rec['entry']['course'],
      'race_type':rec['entry']['race_type'],'discipline':rec['entry']['discipline'],
      'field_size':len(p),'predicted_winner_pct':round(100*float(p[true_idx]),5),
      'multiclass_log_loss':round(-math.log(max(1e-12,float(p[true_idx]))),6),
      'multiclass_brier':round(sum((float(q)-int(i==true_idx))**2 for i,q in enumerate(p)),6)})
    settled=[]
    for v in choices:
     i=v['i'];won=int(rec['sealed_y'][i]);price=v['odds']
     settled.append({**v,'won':won,'profit':price-1 if won else -1.})
    daily_oos.append({'date':day,'choices':settled})
   for rec,pick,policy,_ in pending:
    row=single_result(rec,pick,policy,src,len(history),max((x['date'] for x in history),default=''),
      notes=('SP_AFTER_OFF_NOT_EXECUTABLE' if mode=='SP_ORACLE' else 'TIMESTAMPED_WILLIAM_HILL_QUOTE_RESEARCH'))
    row['trained']=bool(estimator)
    row['gate_ev_pct']=round(100*threshold,1)
    row['gate_max_decimal_odds']=cap
    results.append(row)
   audit.append({'date':day,'model':model,'price_mode':mode,'races':len(today),
                 'past_training_races':len(history),'fit_status':status,
                 'eligible_ml_races':sum(p is not None for _,p,_ in today_pred),
                 'pre_off_coverage_races':len(today),
                 'roi_gate_min_ev_pct':round(threshold*100,2),'roi_gate_max_decimal_odds':cap,
                 'gate_basis':src,'past_oos_races_for_gate':len(prior_oos)})
   # Earlier outcomes can only affect the NEXT calendar date.
   history.extend(today)
   prior_oos.extend(daily_oos)
 return results,audit,accuracy


def summarise_one(rows):
 n=len(rows);wins=sum(int(r['winner']) for r in rows)
 w=[r for r in rows if r['profit_at_quote_units']!='']
 profit=sum(float(r['profit_at_quote_units']) for r in w)
 sp=[r for r in rows if r['profit_at_sp_units']!='']
 sprofit=sum(float(r['profit_at_sp_units']) for r in sp)
 return {'selections':n,'wins':wins,'strike_rate_pct':round(100*wins/n,2) if n else '',
  'priced_at_decision':len(w),'quote_profit_units':round(profit,4),
  'roi_at_observed_odds_pct':round(100*profit/len(w),2) if w else '',
  'sp_priced':len(sp),'sp_roi_pct':round(100*sprofit/len(sp),2) if sp else ''}


def aggregate(rows,groups):
 by=collections.defaultdict(list)
 for r in rows:by[tuple(r[x] for x in groups)].append(r)
 return [dict(zip(groups,key),**summarise_one(vals)) for key,vals in sorted(by.items())]


def paired_compare(rows):
 """Compare baseline and ML only on same price-available race keys.
    ROI counts differ when ML abstains; show those differences transparently.
 """
 by=collections.defaultdict(dict)
 for row in rows:
  if row['date']<TEST_START:continue
  key=(row['date'],row['course'],row['off_utc'],row['model'],row['price_mode'])
  by[key][row['policy']]=row
 pairs=[]
 for key,found in by.items():
  if 'BASE_RANK1' not in found or 'ML_VALUE_RANK1' not in found:continue
  base=found['BASE_RANK1'];ml=found['ML_VALUE_RANK1']
  pairs.extend([{**base,'cohort_side':'BASE_RANK1'}, {**ml,'cohort_side':'ML_VALUE_RANK1'}])
 return aggregate(pairs,['model','price_mode','cohort_side']),pairs


def summary_output(rows,audit,accuracy,issues,counts,priceaudit,out):
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 final=[r for r in rows if TEST_START<=r['date']<=TEST_END]
 if not final:raise RuntimeError('No Stage102 outputs; refusing successful empty run')
 result_fields=list(rows[0]);summary_keys=list(summarise_one([]))
 write_csv(out/'stage102-per-selection.csv',rows,result_fields)
 write_csv(out/'stage102-30day-overall.csv',aggregate(final,['model','price_mode','policy']),['model','price_mode','policy',*summary_keys])
 write_csv(out/'stage102-30day-by-discipline.csv',aggregate(final,['model','price_mode','policy','discipline']),['model','price_mode','policy','discipline',*summary_keys])
 write_csv(out/'stage102-30day-by-race-type.csv',aggregate(final,['model','price_mode','policy','discipline','race_type']),['model','price_mode','policy','discipline','race_type',*summary_keys])
 write_csv(out/'stage102-30day-by-date.csv',aggregate(final,['model','price_mode','policy','date']),['model','price_mode','policy','date',*summary_keys])
 # Rank 1/Rank 2/combined: two equal win stakes when both value ranks pass;
 # otherwise combined counts the actual qualifying bets only.
 ranking=[]
 for r in final:
  strategy,_,rank=r['policy'].rpartition('_RANK')
  if rank not in ('1','2'):continue
  ranking.append({**r,'strategy':strategy,'rank_view':'Rank '+rank})
  ranking.append({**r,'strategy':strategy,'rank_view':'Combined'})
 for filename,gcols in (
    ('stage102-rank1-rank2-combined.csv',['model','price_mode','strategy','rank_view']),
    ('stage102-rank1-rank2-combined-by-discipline.csv',['model','price_mode','strategy','rank_view','discipline']),
    ('stage102-rank1-rank2-combined-by-race-type.csv',['model','price_mode','strategy','rank_view','discipline','race_type'])):
  write_csv(out/filename,aggregate(ranking,gcols),[*gcols,*summary_keys])
 write_csv(out/'stage102-training-and-coverage.csv',audit,list(audit[0]))
 write_csv(out/'stage102-probability-accuracy-per-race.csv',accuracy,list(accuracy[0]) if accuracy else ['date','model','price_mode','course','race_type','discipline','field_size','predicted_winner_pct','multiclass_log_loss','multiclass_brier'])
 accuracy_final=[r for r in accuracy if r['date']>=TEST_START]
 accuracy_groups=collections.defaultdict(list)
 for rec in accuracy_final:accuracy_groups[(rec['model'],rec['price_mode'])].append(rec)
 acc_agg=[]
 for (model,mode),vals in sorted(accuracy_groups.items()):
  acc_agg.append({'model':model,'price_mode':mode,'races':len(vals),
    'mean_log_loss':round(statistics.mean(x['multiclass_log_loss'] for x in vals),5),
    'mean_brier':round(statistics.mean(x['multiclass_brier'] for x in vals),5)})
 write_csv(out/'stage102-probability-accuracy-summary.csv',acc_agg,['model','price_mode','races','mean_log_loss','mean_brier'])
 bands=[]
 for model,mode in sorted({(z['model'],z['price_mode']) for z in final}):
  for policy in ('ML_VALUE_RANK1','ML_PROB_RANK1'):
   possible=[r for r in final if r['model']==model and r['price_mode']==mode and r['policy']==policy]
   for threshold in (20,25,30,34,40,42,45,50):
    sel=[x for x in possible if x['predicted_win_pct']!='' and float(x['predicted_win_pct'])>=threshold]
    bands.append({'model':model,'price_mode':mode,'policy':policy,'min_win_probability_pct':threshold,**summarise_one(sel)})
 write_csv(out/'stage102-win-probability-bands.csv',bands,['model','price_mode','policy','min_win_probability_pct',*summary_keys])
 write_csv(out/'stage102-exclusions.csv',issues,['date','course','time','model','reason'])
 same,pairs=paired_compare(final)
 write_csv(out/'stage102-strict-baseline-vs-ML-pairs.csv',same,['model','price_mode','cohort_side',*summary_keys])
 write_csv(out/'stage102-paired-individual-selections.csv',pairs,[*result_fields,'cohort_side'])
 model_counts=collections.Counter((r['model'],r['price_mode'],r['policy']) for r in final)
 fitted=sum(z['eligible_ml_races'] for z in audit if z['date']>=TEST_START)
 if fitted==0:raise RuntimeError('No ML trained on any 30-day test race. Insufficient prior-data coverage: inspect Stage102 audit')
 note=[
  'PRIMARY: pre-off odds were verified <= selection-time UTC; selected William Hill quotes only. NOT guaranteed executable.',
  'The candidate probabilities and ROI thresholds are fitted with records from STRICTLY earlier calendar dates; no target-day label enters training or policy calibration.',
  'ROI is primary: highest expected profit at locked observed price with historical-only risk/coverage gate. Strike rate is reported but not a tuning target.',
  'SP_ORACLE intentionally gives historical final SP to the model but NOT the winner; SP is unavailable at time a live model selects and MUST NOT count as predictive live evidence.',
  'The SP oracle rejects any race without SP for ALL verified starters; this can substantially reduce coverage.',
  'Odds archive ends 10 November for most files; 11 November recovery is attempted using date-verified Git history snapshots.',
  'Pre-off observations must be no older than 180 minutes, not stale, captured no later than 5 minutes before post or 09:45 morning cutoff.',
  'Only William Hill is eligible for the executable-like primary ROI. No invented prices, no SP substitution when live quote is absent.',
  'Both the base model vectors and candidate features are from 09:45-before-off verified cards; T-5 quote scenario assumes unchanged card model scores between morning and the later quote.',
  'The first 5 days are warm-up only. Earliest test dates may be omitted until enough earlier labelled races are available.',
  'Original A/B reconstruction feature parity is unverified; original C is a historical rerun. E75 are research formulas, not independent live models.',
  'Stage92 E V2 archival research probabilities may have been trained on 2026 October outcomes; its test is a supplementary DIAGNOSTIC, not an independent holdout.',
  'Large returns from longshots or a small training sample do not prove a positive out-of-sample betting edge.',
  'The 30-day research test may be exploratory if its modelling choices were informed by previous examination of these dates. Prospective confirmation is necessary.',
  'Nothing in this workflow commits source code, modifies the app, replaces model weights or places bets.'
 ]
 report={'version':'stage102-v1-roi-first-locked-odds','window':{'warmup':WARMUP_START+'..2026-10-12',
   'test':TEST_START+'..'+TEST_END},'source_stage100_run':43089770857,
   'base_model_names':BASE_MODELS,'price_modes':PRICE_MODES,'risk_objective':'ROI LOWER CONFIDENCE with coverage penalty, NEVER target-day outcomes',
   'coverage':dict(counts),'price_audit':dict(priceaudit),'training_eligible_test_races':fitted,
   'model_summary':aggregate(final,['model','price_mode','policy']),
   'probability_accuracy':acc_agg,
   'strict_pair_summary':same,'exclusions':len(issues),'notes':note}
 (out/'stage102-audit.json').write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
 ml=[z for z in report['model_summary'] if z['policy']=='ML_VALUE_RANK1' and z['price_mode']=='PREOFF_TMINUS5']
 base=[z for z in report['model_summary'] if z['policy']=='BASE_RANK1' and z['price_mode']=='PREOFF_TMINUS5']
 lines=['# Stage102: ROI-first ML — 30-day UK/IE walk-forward',
  '',f'**Window:** {TEST_START} to {TEST_END}. **ML-scored test races:** {fitted}.',
  '', '## Primary pre-off (5 minutes before off) results',
  '', '| Model | Original R1 bets | Original R1 ROI | ML value R1 bets | ML value R1 ROI |',
  '|---|---:|---:|---:|---:|']
 by_base={r['model']:r for r in base};by_ml={r['model']:r for r in ml}
 for m in BASE_MODELS:
  b=by_base.get(m,{});x=by_ml.get(m,{})
  bf=str(b.get('roi_at_observed_odds_pct','N/A'));xf=str(x.get('roi_at_observed_odds_pct','N/A'))
  lines.append(f"| {m} | {b.get('selections',0)} | {bf+('%' if bf!='N/A' else '')} | {x.get('selections',0)} | {xf+('%' if xf!='N/A' else '')} |")
 lines += ['', '**Warning:** the ORIGINAL R1 and ML value bets have DIFFERENT coverage. Strict shared-race paired metrics are in `stage102-strict-baseline-vs-ML-pairs.csv`.',
  '', '## Caveats', '', *['- '+n for n in note]]
 (out/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 print('\n'.join(lines[:22]),flush=True)


def run(args):
 S101.audit_inputs(args.db,args.mapping)
 entries,issues,coverage=S101.collect_entries(WARMUP_START,TEST_END)
 if len(entries)<100:raise RuntimeError(f'Only {len(entries)} verified pre-off races over 35 days')
 abc=S101.card_models_original(args.db,args.mapping,entries,issues)
 de=S101.date_rows_de(args.db,args.ratings,entries,issues)
 # Stage101 V1 stored the frozen D research vector as D_ORIGINAL, while
 # the published report called it D_ORIGINAL_REPLAY. Map the name only;
 # do not touch its scores, weights or the production frozen model.
 for each in de.values():
  if 'D_ORIGINAL' in each and 'D_ORIGINAL_REPLAY' not in each:
   each['D_ORIGINAL_REPLAY']=each['D_ORIGINAL']
 prices=PriceArchive(ROOT).load()
 group,rejections,counts=gather_candidates(entries,abc,de,prices)
 issues.extend({'date':d,'course':c,'time':t,'model':m,'reason':why} for d,c,t,m,why in rejections)
 rows,audit,accuracy=run_walkforward(group)
 if not rows:raise RuntimeError('No matched modeled/priced races')
 summary_output(rows,audit,accuracy,issues,counts,prices.audit,args.output)


def self_test():
 s={'collected_at':'2026-11-01T12:40:00+00:00','bookmaker_updated_at':'2026-11-01T12:38:00Z',
     'race_time':'2026-11-01T13:00:00Z','course':'Lingfield','horse':'Test Runner (IRE)',
     'bookmaker':'williamhill','decimal_odds':5.,'stale':False}
 q=quote_object(s,'test');assert q['key']==('2026-11-01','LINGFIELD','13:00','TESTRUNNER')
 root=pathlib.Path(__file__).resolve().parent
 a=PriceArchive(root)
 a.add_snapshot({'prices':[s]},'test')
 entry={'date':'2026-11-01','course':'Lingfield (AW)','time':'13:00',
        'cards':[{'race_time':'2026-11-01T13:00:00Z','horse':'Test Runner'}]}
 assert a.get(entry,'Test Runner','PREOFF_TMINUS5')['price']==5.
 late=dict(s,collected_at='2026-11-01T13:05:00+00:00',bookmaker_updated_at='2026-11-01T13:03:00Z')
 b=PriceArchive(root);b.add_snapshot({'prices':[late]},'late')
 assert b.get(entry,'Test Runner','PREOFF_TMINUS5') is None
 assert decision_at(entry,'PREOFF_TMINUS5')<off_at(entry)
 assert timestamp('2026-11-01T13:05:00+00:00')>decision_at(entry,'PREOFF_TMINUS5')
 features=ml_features({'age':7,'official_rating':123},.23,5.0,10,'Jumps','Handicap Hurdle')
 assert len(features)==16 and all(math.isfinite(x) for x in features)
 assert sum(np.exp(np.array([0.,1.,2.]))/sum(np.exp(np.array([0.,1.,2.]))))==1
 assert choose_policy([])[:2]==(.15,15.)
 # No field's finishing position or SP is read by the ML feature builder.
 feature_fake=ml_features({'age':7,'official_rating':123,'position':1,'sp_decimal':100.0},.23,5,10,'Jumps','Handicap Hurdle')
 assert feature_fake==features
 assert len([x for x in ['PREOFF_TMINUS5','PREOFF_MORNING0945'] if x in PRICE_MODES])==2
 print('PASS Stage102: 09:45 and T-5 decision times, future odds blocked, no outcome/SP in pre-off features, ROI-first policy')


def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test')
 compare_parser=sub.add_parser('compare');compare_parser.add_argument('--db',required=True);compare_parser.add_argument('--mapping',required=True)
 compare_parser.add_argument('--ratings',required=True);compare_parser.add_argument('--output',default='reports/stage102/output')
 a=p.parse_args()
 if a.cmd=='self-test':self_test()
 else:run(a)

if __name__=='__main__':main()
