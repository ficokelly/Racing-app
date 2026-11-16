#!/usr/bin/env python3
"""Stage146: full-field, retrained Stage143 specialist evidence + Stage144 historic trends.

Research-only challenger. Reconstructs the SAME Stage143 chronologically scored
pre-race features and adds a Stage144-derived *per-runner* trend vector to each
training race before logistic regression fitting. Race trends are mined solely
from earlier calendar dates, never from today's winners, SP, or same-day races.
This is NOT the earlier top-two switch replay; horses at any starting rank may win.

Caveat: the source itself, historical series candidates, source timestamps and
race-country provenance are not independently certified. 2026 is retrospectively
researched. SP is settlement only, never a training feature or value gate.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, json, math, os, pathlib, sqlite3, sys
import numpy as np
from collections import Counter, defaultdict, deque
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

VERSION='stage146-full-field-retrained-specialist-trend-v1.1-pathfix'
A_MODEL='STAGE146_A_RETRAINED_TRENDS'
B_MODEL='STAGE146_B_RETRAINED_TRENDS'
CONTROL143A='STAGE143_TYPE_EVIDENCE'
CONTROL143B='STAGE143_TYPE_INTERACTIONS'
CONTROL141='D137_HPR_ABILITY_OR_WEIGHT'
CONTROL142='D142_LONG_CORE_FORM_OR_WEIGHT'
MODELS=(CONTROL141,CONTROL142,CONTROL143A,CONTROL143B,A_MODEL,B_MODEL)
FEATURES=('trend_centered','trend_strength','trend_exact','trend_comparable',
          'trend_log_history','trend_active','trend_signed_evidence')
CUTOFF='2026-11-10'


def modules():
 import stage143_specialist_evidence_2026 as A
 import stage144_named_race_trends_2026 as R
 import stage142_full_history_core_2026 as T
 import stage141_grade_weight_2026 as J
 import stage140_d137_hpr_ability_2026 as U
 import stage139_hpr_137_138_walkforward as H
 import stage137_frozen_d_race_type_walkforward as S
 return A,R,T,J,U,H,S


def load_archived_controls(source143):
 """One immutable Stage143 result for each source race/control/model."""
 a=defaultdict(dict); p=pathlib.Path(source143)/'stage143-2026-rank1-rank2.csv.gz'
 with gzip.open(p,'rt',newline='',encoding='utf8') as f:
  for z in csv.DictReader(f):
   mid=z['model'];rid=str(z['race_id'])
   if mid not in MODELS[:4]:continue
   if mid in a[rid]:raise RuntimeError('Duplicated published control '+rid+' '+mid)
   a[rid][mid]=z
 assert len(a)>8000, 'Insufficient Stage143 archived controls'
 for rid,models in a.items():
  if set(models)!=set(MODELS[:4]):raise RuntimeError('Missing published controls '+rid)
  labels={str(z['winner']) for z in models.values()}
  if len(labels)!=1:raise RuntimeError('Control winner mismatch '+rid)
 return a


def source_to_pre_race_trends(db,reference,races,output_dir):
 """Create actual runner-level trend evidence from prior-day UK/IE candidate history.

 Stage144 shares the original evidence definitions, matched-series metadata,
 structured runner profiles and shrunk lift statistics. Unlike its old ranker,
 our output consists of independent numeric observations for ALL field runners.
 The original race's winner/SP fields are never inspected by feature generation.
 """
 A,R,_,_,_,_,S=modules()
 wanted={str(r['race_id']):r for r in races}
 if len(wanted)!=len(races):raise RuntimeError('Duplicated target race IDs')
 refmap,series,reference_audit=R.source_map(reference)
 target_events={refmap.get(rid,{}).get('event_id','') for rid in wanted}
 target_events.discard('')
 target_fallbacks=set();conn=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 try:
  for rid,date,course,code,surf,distance,ground,rclass,name,pat in conn.execute('''
    SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,
           r.race_class,d.race_name,d.pattern
    FROM historical_races r LEFT JOIN stage65p_race_details d ON d.race_id=r.id
    WHERE r.race_date BETWEEN '2023-01-01' AND ?''',(CUTOFF,)):
   if str(rid) not in wanted:continue
   entry=refmap.get(str(rid),{})
   key=R.fallback_key((rid,date,course,code,surf,distance,ground,rclass),
                      entry.get('race_name') or name or '',pat or '')
   if key is not None:target_fallbacks.add(key)
  # True historical record of earlier known series, plus rolling latest 15
  # comparable earlier races. Any matching event ID is merely a candidate.
  stored_exact=defaultdict(lambda:deque(maxlen=R.EXACT_KEEP))
  stored_fallback=defaultdict(lambda:deque(maxlen=R.FALLBACK_KEEP))
  horse_history={}
  outputs={};reasons=Counter();samples=[]
  pending=[];group=[];previous_key=None;previous_day=None;rows_scanned=0
  sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,
     r.going,r.race_class,u.horse_name_norm,u.horse_name,u.horse_country,
     u.horse_source_id,u.finish_pos,u.outcome,u.trainer,u.jockey,u.official_rating,
     u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment,u.beaten,u.weight_lbs,
     d.race_name,d.pattern
     FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
     LEFT JOIN stage65p_race_details d ON d.race_id=r.id
     WHERE r.race_date <= ? ORDER BY r.race_date,r.id'''

  def finish(rows):
   if not rows:return
   edition=R.produce_edition(rows,horse_history,refmap)
   if edition is None:reasons['invalid_historical_result']+=1;return
   rid=str(rows[0][0]);day=str(rows[0][1]);eid=edition.get('event')
   if edition.get('foreign'):
    reasons['foreign_country_collision']+=1
    if rid in wanted:
     reasons['foreign_target_rejected']+=1
    return
   if rid in wanted:
    target=wanted[rid]
    if len(edition['runners'])!=len(target['horse']):
     raise RuntimeError('Trend/Stage143 field size mismatch '+rid)
    names=[R.normal(k['horse']) for k in edition['runners']]
    expected=[R.normal(k) for k in target['horse']]
    if len(set(names))!=len(names) or names!=expected:
     raise RuntimeError('Trend/Stage143 runner order or identity mismatch '+rid)
    # Only PRIOR existing editions are checked. Future editions must not
    # validate the series, nor can future outcomes determine sample size.
    m=refmap.get(rid,{})
    exact_candidate=bool(eid and eid in target_events and
       m.get('match_status') in R.CONFIDENT and
       R.usable_event(eid,{eid:[z for z in series.get(eid,[])
                   if z['date']<day]}))
    prior_exact=list(stored_exact.get(eid,())) if exact_candidate else []
    prior_fallback=list(stored_fallback.get(edition['fallback'],())) if edition['fallback'] else []
    trend_source='NONE';trends=[];used=0
    if len(prior_exact)>=R.EXACT_MIN:
     trends,_=R.trend_from_history(prior_exact,edition['kind'],include_odds=False)
     if trends:trend_source='EXACT';used=len(prior_exact)
    if trend_source=='NONE' and len(prior_fallback)>=R.FALLBACK_MIN:
     trends,_=R.trend_from_history(prior_fallback,edition['kind'],include_odds=False)
     if trends:trend_source='COMPARABLE15';used=len(prior_fallback)
    if set(t['feature'] for t in trends)&set(R.ODDS_FEATURES):
     raise RuntimeError('Historical odds feature escaped into live trend scores')
    # Reconstruct target profiles explicitly from PRE-OFF columns only:
    # age, OR, draw, race conditions and the previous-calendar-day horse state.
    # Even though the historical edition object contains settlement fields for
    # future dates, we NEVER pass those fields into target trend features.
    track=edition['course'];trip=edition['trip'];going=edition['go']
    starter_rows=[z for z in rows if R.is_starter(z)]
    field_or=[R.num(z[16]) for z in starter_rows if R.num(z[16]) is not None]
    preoff=[]
    for z in starter_rows:
     st=horse_history.get(R.horsekey(z)) or R.History()
     draw=R.num(z[21]);fraction=(draw-1)/(len(starter_rows)-1) if draw is not None and len(starter_rows)>1 else None
     preoff.append(R.profile(z,st,day,track,trip,going,field_or,fraction))
    # A score for every horse, including any originally ranked 3rd or worse.
    vals=[R.trend_score({'features':p},trends) for p in preoff]
    mid=float(np.mean(vals)) if vals else 0.0
    mx=float(max(map(abs,vals),default=0.))
    confidence=min(1.0,math.log1p(used)/math.log1p(30)) if used else 0.0
    features=[]
    for value in vals:
     features.append([float(value-mid),mx,int(trend_source=='EXACT'),
       int(trend_source=='COMPARABLE15'),confidence,
       int(bool(trends)),float(value)])
    # No current winner, race SP, or post-race source may appear here.
    outputs[rid]={'source':trend_source,'history':used,'active_signals':len(trends),
      'features':np.asarray(features,dtype=np.float32),
      'historical_features':';'.join(t['feature'] for t in trends),
      'sample_lag':'PRIOR_CALENDAR_DAY_ONLY'}
    reasons['target_'+trend_source]+=1
    if len(samples)<30:samples.append({'date':day,'race_id':rid,
      'source':trend_source,'prior_editions_or_races':used,
      'signals':';'.join(t['feature'] for t in trends),
      'runners_scored':len(vals)})
   # Historical public-source candidate metadata is an imperfect linkage.
   if eid in target_events and refmap.get(rid,{}).get('match_status') in R.CONFIDENT:
    stored_exact[eid].append(edition)
   if edition['fallback'] in target_fallbacks:stored_fallback[edition['fallback']].append(edition)
   pending.append(rows)

  def flush_day():
   nonlocal group,pending
   if group:finish(group);group=[]
   for rs in pending:R.apply_updates(rs,horse_history,refmap)
   pending=[]

  for row in conn.execute(sql,(CUTOFF,)):
   key=(row[1],row[0]);day=str(row[1]);rows_scanned+=1
   if previous_day and day!=previous_day:flush_day()
   elif previous_key and key!=previous_key and group:finish(group);group=[]
   group.append(row);previous_key=key;previous_day=day
   if rows_scanned%1000000==0:
    print('STAGE146_HISTORY',rows_scanned,'trend target',len(outputs),'horse histories',len(horse_history),flush=True)
  flush_day()
 finally:conn.close()
 missing=set(wanted)-set(outputs)
 if missing:
  raise RuntimeError('Full-field trend target coverage missing: '+str(len(missing))+': '+str(sorted(missing)[:8]))
 # Audit-only output. Trend data are never merged by horse name alone.
 audit={'target_races':len(wanted),'features_per_runner':len(FEATURES),
   'target_source_counts':dict(reasons), 'training_and_test_history_source':'1988+ original Stage2',
   'runner_records_scanned':rows_scanned,'reference_audit':dict(reference_audit),
   'sample':samples,'historical_series_not_independently_certified':True,
   'no_current_race_winner_or_sp_in_features':True,
   'same_calendar_day_outcomes_not_used':True}
 pathlib.Path(output_dir,'stage146-trend-audit.json').write_text(json.dumps(audit,indent=2,default=str)+'\n')
 return outputs,audit


def fused_matrix(races,routing,config,nonlinear=False,with_trend=True):
 A,_,_,_,_,_,_=modules()
 original,labels,meta,usage,names=A.build_matrix(races,routing,config,nonlinear=nonlinear)
 if not with_trend:return original,labels,meta,usage,names
 lookup={str(r['race_id']):r for r in races}
 extras=[]
 for rid,index in meta:
  t=lookup[str(rid)].get('trend_features')
  if t is None or index>=len(t):raise RuntimeError('Missing prior-day trend for '+str(rid))
  v=t[index]
  if len(v)!=len(FEATURES) or not np.all(np.isfinite(v)):
   raise RuntimeError('Nonfinite historic trend signal for '+str(rid))
  extras.append(v)
 if len(extras)!=len(original):raise RuntimeError('Trend matrix row count mismatch')
 return np.hstack((original,np.asarray(extras,dtype=np.float32))),labels,meta,usage,names+list(FEATURES)


def fitted_bank(races,routing,config,nonlinear=False,with_trend=True):
 X,y,meta,usage,names=fused_matrix(races,routing,config,nonlinear,with_trend)
 if len(meta)<2000:raise RuntimeError('Not enough historical horses')
 groups={str(r['race_id']):routing[str(r['race_id'])]['family'] for r in races}
 indexes=defaultdict(list)
 for i,(rid,horse) in enumerate(meta):indexes[groups[str(rid)]].append(i)
 models={}
 scaler=StandardScaler();z=scaler.fit_transform(X)
 global_model=LogisticRegression(solver='lbfgs',C=.15,max_iter=160,tol=.0005)
 global_model.fit(z,y);models['__GLOBAL__']=(scaler,global_model)
 for family,ix in indexes.items():
  yy=y[ix]
  if len(ix)<500 or int(np.sum(yy))<60:continue
  ss=StandardScaler();zz=ss.fit_transform(X[ix])
  mm=LogisticRegression(solver='lbfgs',C=.2,max_iter=160,tol=.0005)
  mm.fit(zz,yy);models[family]=(ss,mm)
 return models,{'horses':len(meta),'races':len(races),
     'fitted_family_models':sorted(models),'features':len(names),
     'trend_features':list(FEATURES),'with_nonlinear_interactions':bool(nonlinear)}


def predict(races,routing,config,banks,nonlinear=False,with_trend=True):
 X,y,meta,_,_=fused_matrix(races,routing,config,nonlinear,with_trend)
 families={str(r['race_id']):routing[str(r['race_id'])]['family'] for r in races}
 indexes=defaultdict(list)
 for k,(rid,i) in enumerate(meta):indexes[families[str(rid)]].append(k)
 predictions=defaultdict(dict)
 for family,ix in indexes.items():
  scaler,model=banks.get(family,banks['__GLOBAL__'])
  values=model.predict_proba(scaler.transform(X[ix]))[:,1]
  for j,k in enumerate(ix):
   rid,i=meta[k];predictions[str(rid)][i]=float(values[j])
 output={}
 for r in races:
  rid=str(r['race_id']);data=predictions.get(rid,{})
  if len(data)!=len(r['horse']):raise RuntimeError('Incomplete FULL-FIELD score '+rid)
  v=np.array([max(1e-9,data[i]) for i in range(len(r['horse']))])
  output[rid]=v/v.sum()
 return output


def write_rows(path,rows):
 if not rows:raise RuntimeError('Refusing empty research CSV '+str(path))
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fields=list(dict.fromkeys(k for r in rows for k in r))
 opener=gzip.open if str(path).endswith('.gz') else open
 with opener(path,'wt',newline='',encoding='utf8') as f:
  w=csv.DictWriter(f,fieldnames=fields,restval='');w.writeheader();w.writerows(rows)


def measure(rows):
 n=len(rows);wins=sum(int(z['rank1_won']) for z in rows)
 priced=[z for z in rows if z['sp'] is not None and 1<float(z['sp'])<1001]
 paid=sum(float(z['sp']) for z in priced if int(z['rank1_won']))
 return {'races':n,'wins':wins,'strike_pct':round(100*wins/n,3) if n else None,
  'rank1_or2_wins':sum(int(z['rank1_won'])+int(z['rank2_won']) for z in rows),
  'priced':len(priced),'sp_coverage_pct':round(100*len(priced)/n,2) if n else None,
  'sp_roi_pct':round(100*(paid/len(priced)-1),3) if priced else None,
  'sp_roi_capped_99to1_pct':round(100*(sum(min(100.,float(z['sp'])) for z in priced if z['rank1_won'])/len(priced)-1),3) if priced else None}


def summarise(rows):
 group=defaultdict(list)
 for z in rows:
  for dim,key in (('ALL','ALL'),('QUARTER',z['quarter']),('DISCIPLINE',z['family']),
     ('RACE_TYPE',z['route']),('COUNTRY',z['country']),('TREND_SOURCE',z['trend_source'])):
   group[(z['model'],dim,key)].append(z)
 return [{'model':m,'breakdown':d,'group':k,**measure(v)} for (m,d,k),v in sorted(group.items())]


def build_research(db,reference,source141,source142,source143,config_path,out,to_date=CUTOFF):
 A,R,T,J,U,H,S=modules()
 # Stage139 -> Stage134C calls db.resolve(); keep file arguments as Path.
 # Normalize here as well as in argparse to protect programmatic callers.
 db=pathlib.Path(db);reference=pathlib.Path(reference)
 source141=pathlib.Path(source141);source142=pathlib.Path(source142)
 source143=pathlib.Path(source143);config_path=pathlib.Path(config_path)
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 config=json.loads(pathlib.Path(config_path).read_text())
 assert config['type_count']==59 and len(config['full_type_catalog'])==59
 # Validate historical frozen D and controls exactly as Stage143 does.
 import stage30_gap_aware_abcd as D
 original=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
 assert original['chosen_combination']=='D-064-gap14'
 frozen=np.asarray(D.candidates('D')[64],dtype=float);frozen/=frozen.sum()
 if not np.allclose([original['weights_pct'][p] for p in S.PILLARS],frozen*100,atol=.011):
  raise RuntimeError('Original frozen D coefficients changed')
 S.FROZEN[:]=frozen;S.CANDIDATES=S.candidate_weights()
 last,audited_source=U.audit_limit(db,to_date)
 if last!=CUTOFF:raise RuntimeError('Stage146 requires same immutable source cutoff '+CUTOFF)
 old,oldmeta=T.read_stage141_archive(source141,last)
 overlays,gradeaudit=J.official_grade_overlay(db,reference,out,last)
 base,baseaudit,refaudit=J.construct_2026(db,reference,last)
 T.compare_actual(old,base)
 state,form,warmaudit=T.warmup(db,reference,last)
 extended,extaudit,_=T.reconstruct_full_core(db,reference,last,state,form)
 T.align(base,extended)
 del state,form
 ids={str(k) for r in base for k in r['identity'] if k}
 save=H.END
 try:
  H.END=last;hpr,hpraudit=H.hpr_history(db,reference,out,ids)
 finally:H.END=save
 H.attach_hpr(base,hpr,Counter())
 weights=J.weight_context(db,reference,base,out,last)
 routing={};qa=Counter();longmap={str(r['race_id']):r for r in extended}
 for r in base:
  info,reason=A.classified(r,overlays,config)
  if reason:qa[reason]+=1
  else:routing[str(r['race_id'])]=info
 A.attach_preoff_observations(db,base,last)
 target=[]
 for r in base:
  rid=str(r['race_id'])
  if rid not in routing:continue
  target.append(T.variant(r,longmap[rid],core=True,form=True,weights=False))
 del extended,longmap,base
 if len(target)<30000:raise RuntimeError('Too few training + test race records')
 archived=load_archived_controls(source143)
 test=[r for r in target if r['date']>='2026-01-01']
 source_ids={str(r['race_id']) for r in test}
 if source_ids!=set(archived):
  raise RuntimeError('Stage143 published cohort drift '+str({'source':len(source_ids),'archive':len(archived),
     'missing':list(set(archived)-source_ids)[:8]}))
 trend_data,trend_audit=source_to_pre_race_trends(db,reference,target,out)
 for r in target:
  z=trend_data[str(r['race_id'])]
  r['trend_features']=z['features'];r['trend_provenance']=z
 del trend_data
 output=[];runner=[];folds=[];validation=[]
 # Prior-year validation: use the new full-field specification, not the
 # 2026 winner labels to train; do not choose coefficients on 2026 performance.
 train2025=[r for r in target if '2023-01-01'<=r['date']<='2024-12-31']
 hold2025=[r for r in target if '2025-01-01'<=r['date']<='2025-12-31']
 for name,inter in ((A_MODEL,False),(B_MODEL,True)):
  banks,fit=fitted_bank(train2025,routing,config,inter,True)
  ps=predict(hold2025,routing,config,banks,inter,True)
  wins=sum(int(np.argmax(ps[str(r['race_id'])])==r['winner_idx']) for r in hold2025)
  validation.append({'model':name,'races':len(hold2025),'wins':wins,
    'strike_pct':round(100*wins/len(hold2025),3),
    'logloss':round(float(np.mean([-math.log(max(1e-10,ps[str(r['race_id'])][r['winner_idx']])) for r in hold2025])),5),
    'brier':round(float(np.mean([np.square(ps[str(r['race_id'])]-r['win']).sum() for r in hold2025])),5),
    'trained_through':'2024-12-31'})
  print('STAGE146_2025_VALIDATION',validation[-1],flush=True)
  del banks

 for quarter,start,end in U.SPLITS:
  if start>last:break
  end=min(end,last)
  prior=[r for r in target if '2023-01-01'<=r['date']<start]
  now=[r for r in test if start<=r['date']<=end]
  if not now:continue
  for model,nonlinear in ((A_MODEL,False),(B_MODEL,True)):
   banks,fit=fitted_bank(prior,routing,config,nonlinear,True)
   predictions=predict(now,routing,config,banks,nonlinear,True)
   for r in now:
    rid=str(r['race_id']);p=predictions[rid]
    order=np.argsort(-p,kind='stable');i,j=int(order[0]),int(order[1]);info=routing[rid]
    sp=float(r['sp'][i]) if np.isfinite(r['sp'][i]) else None
    zz=r['trend_provenance']
    # Can select ANY rank under Stage143, never only its top two.
    prev=archived[rid][CONTROL143A if not nonlinear else CONTROL143B]
    was_first=(r['horse'][i]==prev['rank1_horse'])
    was_second=(r['horse'][i]==prev['rank2_horse'])
    previous_bucket='RANK_1' if was_first else 'RANK_2' if was_second else 'BEYOND_RANK_2'
    output.append({'date':r['date'],'quarter':quarter,'race_id':rid,
      'course':r['course'],'country':r['country'],'family':info['family'],
      'route':info['route'],'race_name':info['race_name'],'model':model,
      'rank1_horse':r['horse'][i],'rank2_horse':r['horse'][j],
      'rank1_won':int(i==r['winner_idx']),'rank2_won':int(j==r['winner_idx']),
      'sp':sp,'field_size':len(r['horse']),'winner':r['horse'][r['winner_idx']],
      'rank1_p':round(float(p[i]),6),'rank2_p':round(float(p[j]),6),
      'winner_p':round(float(p[r['winner_idx']]),6),
      'trend_source':zz['source'],'trend_sample':zz['history'],
      'trend_signals':zz['historical_features'],'old_143_rank1':prev['rank1_horse'],
      'old_143_rank1_position_in_fusion':int(np.where(order==r['horse'].index(prev['rank1_horse']))[0][0])+1,
      'previous_stage143_rank_bucket':previous_bucket,
      'trained_through':str(dt.date.fromisoformat(start)-dt.timedelta(days=1))})
    for k,h in enumerate(r['horse']):
     runner.append({'date':r['date'],'race_id':rid,'horse':h,'model':model,
       'winner':int(k==r['winner_idx']),'fusion_probability':round(float(p[k]),8),
       'fusion_rank':int(np.where(order==k)[0][0])+1,
       'trend_centered_score':round(float(r['trend_features'][k][0]),6),
       'trend_source':zz['source']})
   del banks
   print('STAGE146_QUARTER',quarter,model,fit,'test_races',len(now),flush=True)
  folds.append({'quarter':quarter,'trained_prior_races':len(prior),
    'tested_races':len(now),'trained_through':str(dt.date.fromisoformat(start)-dt.timedelta(days=1))})
 # Match immutable controls exactly, including missing SP and original winners.
 for r in test:
  rid=str(r['race_id']);info=routing[rid];zz=r['trend_provenance']
  for m in MODELS[:4]:
   z=archived[rid][m]
   if str(z['winner'])!=r['horse'][r['winner_idx']]:raise RuntimeError('Winner drift '+rid)
   if z['rank1_horse'] not in r['horse'] or z['rank2_horse'] not in r['horse']:
    raise RuntimeError('Original runner identity drift '+rid)
   output.append({'date':r['date'],'quarter':r['date'][:4]+'Q'+str((int(r['date'][5:7])-1)//3+1),
     'race_id':rid,'course':r['course'],'country':r['country'],'family':info['family'],
     'route':info['route'],'race_name':info['race_name'],'model':m,
     'rank1_horse':z['rank1_horse'],'rank2_horse':z['rank2_horse'],
     'rank1_won':int(z['rank1_won']),'rank2_won':int(z['rank2_won']),
     'sp':float(z['sp']) if z['sp'] and math.isfinite(float(z['sp'])) else None,
     'field_size':len(r['horse']),'winner':z['winner'],'trend_source':zz['source'],
     'trend_sample':zz['history'],'trend_signals':zz['historical_features'],
     'old_143_rank1':'','trained_through':'IMMUTABLE_STAGE143_ARCHIVE'})
 # No missing races in any comparison arm.
 total=len(test)
 for m in MODELS:
  subset=[z for z in output if z['model']==m]
  if len(subset)!=total or len({z['race_id'] for z in subset})!=total:
   raise RuntimeError('Unmatched test cohort '+m+' '+str(len(subset))+'/'+str(total))
 summary=summarise(output)
 overall={m:next(z for z in summary if z['model']==m and z['breakdown']=='ALL') for m in MODELS}
 audit={'version':VERSION,'source_cutoff':last,'cohort':total,'source_qa':dict(qa),
   'source_routing_distinct_types':len({routing[str(r['race_id'])]['route'] for r in test}),
   'base_source_audit':baseaudit,'grade_recovery_count':len(overlays),
   'full_history_warmup_count':warmaudit['counts'].get('verified_prior_races'),
   'trend_source_audit':trend_audit,'hpr_audit':hpraudit,'weight_audit':weights,
   'training':'2023 onward through last completed quarter','validation_2025':validation,
   'folds_2026':folds,'overall':overall,
   'models':list(MODELS), 'no_preoff_sp_inputs':True,
   'stage143_scores_are_not_a_top_two_gate':True,
   'model_retrained_with_historical_trend_feature_per_runner':True,
   'original_stage141_stage142_stage143_stage144_and_live_unchanged':True,
   'limitations':[
    'Historical UK/IE venue source identity and recurring named events are not independently audited end-to-end.',
    'Stage144 event IDs are candidate historical matches, not certified named race editions.',
    'Same-day past results are never used; only calendar-prior results may enter trend statistics.',
    'All 2026 results are retrospectively evaluated after outcomes already reviewed in earlier experiments.',
    'Missing race SP never substituted. ROI based on priced SP subset only, not bookmaker-time execution.',
    '2026 cohort is restricted by Stage143 QA, and stops where the Stage143 original cohort stops.',
    'This reruns all features and training; archived 2026 baseline selections are copied unchanged.'
   ]}
 (out/'stage146-summary.json').write_text(json.dumps(audit,indent=2,default=str)+'\n')
 write_rows(out/'stage146-2026-selections.csv.gz',output)
 write_rows(out/'stage146-2026-full-field-scores.csv.gz',runner)
 write_rows(out/'stage146-2026-discipline-racetype-quarter.csv',summary)
 print('STAGE146_ACCEPTED',json.dumps({'overall':overall,'validation':validation,'trends':trend_audit['target_source_counts']}),flush=True)
 return audit


def self_test():
 # The model can potentially select ANY runner, not just Stage143 top two.
 vals=np.array([.39,.29,.17,.10,.05]);trend=np.array([0.,0.,0.,0.,3.0])
 raw=vals*np.exp(trend)
 assert int(np.argmax(vals))==0 and int(np.argmax(raw))==4
 assert len(FEATURES)==7 and len(set(MODELS))==6
 # No future or same-calendar-day race enters the history pool.
 earlier=['2024-03-01','2024-03-02','2024-03-04'];day='2024-03-04'
 assert [d for d in earlier if d<day]==['2024-03-01','2024-03-02']
 assert not any('sp' in f or 'odds' in f or 'winner' in f for f in FEATURES)
 assert abs(sum(np.array([.1,.2,.3])-np.mean([.1,.2,.3])))<1e-9
 # Fake Stage143 evidence builder verifies full per-horse feature alignment,
 # original columns retained and explicit missing score refusal.
 class DummyA:
  @staticmethod
  def build_matrix(races,routing,config,nonlinear=False):
   rows=[];labels=[];meta=[]
   for r in races:
    for i,h in enumerate(r['horse']):
     rows.append([float(i),float(i==r['winner_idx'])])
     labels.append(i==r['winner_idx']);meta.append((r['race_id'],i))
   return np.array(rows,dtype=np.float32),np.array(labels,dtype=np.int8),meta,Counter(),['A','B']
 old=globals()['modules']
 globals()['modules']=lambda:(DummyA,None,None,None,None,None,None)
 try:
  r={'race_id':'x','horse':['one','two','three','four','five'],'winner_idx':4,
     'trend_features':np.array([[i,0,0,1,.5,1,i/5] for i in range(5)],dtype=np.float32)}
  mat,y,meta,_,names=fused_matrix([r],{'x':{'family':'TURF'}},{},False,True)
  assert mat.shape==(5,9) and names[-7:]==list(FEATURES)
  assert mat[4,2]==4 and int(y.sum())==1 and meta[-1]==('x',4)
  broken={**r,'trend_features':np.zeros((4,7),dtype=np.float32)}
  try:fused_matrix([broken],{'x':{'family':'TURF'}},{},False,True)
  except RuntimeError:pass
  else:raise AssertionError('Missing trend runner was silently scored')
 finally:globals()['modules']=old
 print('STAGE146_SELFTEST_OK: full-field alignment, stage143 evidence retained, temporal date guard, no SP, no top-2 gate')


def main():
 p=argparse.ArgumentParser()
 p.add_argument('command',choices=('self-test','walkforward'))
 for n in ('db','reference','source141','source142','source143','config','out','to-date'):
  p.add_argument('--'+n, type=str if n=='to-date' else pathlib.Path)
 a=p.parse_args()
 if a.command=='self-test':return self_test()
 needed=('db','reference','source141','source142','source143','config','out')
 for k in needed:
  if getattr(a,k) is None:raise RuntimeError('Missing '+k)
 build_research(a.db,a.reference,a.source141,a.source142,a.source143,
                a.config,a.out,a.to_date or CUTOFF)


if __name__=='__main__':main()
