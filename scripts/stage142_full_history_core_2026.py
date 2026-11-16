#!/usr/bin/env python3
"""Stage142: pre-2022 full archive warm-up ablation for original D137 pillars/form.

Controls: immutable Stage141 paper selection files (137+HPR Ability, 137+HPR+OR+Weight).
Challengers: same research model trained on 2023-through-prior-quarter races,
with extended 1988-onward history used for eight original D pillars, recent
form context, or both. HPR-v3.1 and weight/OR history are identical across arms.
No original source, archived predictions, weights, or live app modifications.
Historical data are previous-CALENDAR-day only. No price in prediction inputs.
A 1988 archive start does NOT certify every horse's entire career is present.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,hashlib,json,math,pathlib,sqlite3,sys
from collections import defaultdict
import numpy as np
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H
import stage140_d137_hpr_ability_2026 as U
import stage141_grade_weight_2026 as J

VERSION='stage142-2026-full-history-eight-pillars-v1'
FIRST='1988-01-01';BASE_START='2022-01-01';WARM_END='2021-12-31'
CONTROL_BASE='D137_HPR_ABILITY_UNCHANGED'
CONTROL_WEIGHT='D137_HPR_ABILITY_OR_WEIGHT'
CORE='D142_LONG_CORE_HPR'
FORM='D142_LONG_FORM_HPR'
BOTH='D142_LONG_CORE_FORM_HPR'
BOTH_WEIGHT='D142_LONG_CORE_FORM_OR_WEIGHT'
CONTROLS=(CONTROL_BASE,CONTROL_WEIGHT)
NEW_MODELS=(CORE,FORM,BOTH,BOTH_WEIGHT)
ALL_MODELS=CONTROLS+NEW_MODELS
# Form-derived inputs only. Current class, handicap, competition and physical
# pre-race inputs are NOT rewritten by the lookback ablation.
FORM_FEATURES=('horse_form_quality3','horse_form_trend','horse_beaten3',
 'horse_beaten_per_mile3','horse_comment_phrase3','horse_comment_nlp3',
 'horse_nlp_coverage','horse_jumping_comment3','horse_interference3',
 'horse_runs','last_run_days','past_grade_strength3','past_known_tier_fraction',
 'past_opposition_median3','same_code_form3','maiden_to_handicap',
 'novice_to_handicap','beginner_to_handicap','last3_maiden_fraction',
 'last3_novice_fraction','past_vs_current_tier_delta',
 'prior_rivals_nextout_quality3','prior_rivals_nextout_winrate3',
 'prior_rivals_nextout_count3','prior_rivals_nextout_coverage3',
 'form_quality_confident')


def number(v):
 try:
  z=float(v)
  return z if math.isfinite(z) else None
 except (ValueError,TypeError):return None


def target_identities(db,last):
 """Retain pre-2022 Form only for horses declared 2022-end-of-evaluation.

 Original eight-pillar aggregate D state still includes ALL verified historic
 runners, even when they have no present-day descendants in the target set.
 """
 import stage108_model_g2 as G
 con=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 target=set();modern=defaultdict(set);n=0
 import stage30_gap_aware_abcd as D
 try:
  sql='''SELECT DISTINCT u.horse_source_id,u.horse_name,u.horse_name_norm,u.horse_country,u.sire,u.dam
    FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
    WHERE r.race_date BETWEEN ? AND ?'''
  for src,horse,horse_norm,horse_country,sire,dam in con.execute(sql,(BASE_START,last)):
   k=G.identity({'horse':horse,'horse_source_id':src,'sire':sire,'dam':dam})
   if k:
    target.add(k)
    modern[(D.normal(horse_norm),D.normal(horse_country))].add(k)
   n+=1
 finally:con.close()
 if len(target)<10000:raise RuntimeError('Too few 2022+ target horse identities')
 return target,modern,{'unique_2022plus_horse_identity_count':len(target),
       'modern_name_country_identity_groups':len(modern),
       'source_2022plus_distinct_rows':n}



class LeanHistoricForm(S.Form):
 """Exact Stage137 Form.add for historical target horses; retain last 8.

 Old runners not seen in the 2022+ cohort are discarded from horse Form only,
 NEVER from eight-pillar Past stats or verified field/opposition outcomes.
 Previous opponents' first subsequent outing is retained for target races.
 Superclass Form.add runs during modern period with NO pruning.
 """
 def __init__(self,wanted):
  super().__init__()
  self.wanted=wanted;self.warmup=True
  self.pending_nextout=defaultdict(list)
  self.history_target_runs=0
  self.historic_form_races=0

 def add(self,rows,asof,info,field_or):
  if not self.warmup:
   # Complete legitimate previous-run 'nextout' evidence for races before 2022.
   self._complete_watches(rows,asof)
   return super().add(rows,asof,info,field_or)
  from stage108_model_g2 import quality
  starters=[z for z in rows if S.is_starter(z)]
  keys=[S.ident(z) for z in starters]
  has_target=any(k in self.wanted for k in keys)
  self._complete_watches(rows,asof)
  # Run the UNCHANGED Form.add for the full actual field, then bound storage.
  result=super().add(rows,asof,info,field_or)
  for k in keys:
   if k and k not in self.wanted:self.h.pop(k,None)
  if has_target:
   self.historic_form_races+=1
   self.history_target_runs+=sum(k in self.wanted for k in keys)
   rid=str(rows[0][0])
   for k in keys:
    if k:self.pending_nextout[k].append(rid)
  else:self.race_opponents.pop(str(rows[0][0]),None)
  return [] # Older comments do NOT train Stage137's frozen 2022+ NLP.

 def _complete_watches(self,rows,asof):
  from stage108_model_g2 import quality
  starters=[z for z in rows if S.is_starter(z)]
  for r in starters:
   k=S.ident(r)
   if not k:continue
   watch=self.pending_nextout.pop(k,())
   if not watch:continue
   q=quality({'finish_pos':r[12],'outcome':r[13],'beaten':r[23]},len(starters))
   entry={'date':asof,'quality':q,'won':int(S.fnum(r[12])==1)}
   for old in watch:
    self.nextout.setdefault((old,k),entry)

 def finish_warmup(self):
  active={p['race_id'] for seq in self.h.values() for p in seq}
  self.race_opponents={k:v for k,v in self.race_opponents.items() if k in active}
  self.nextout={k:v for k,v in self.nextout.items() if k[0] in active}
  self.pending_nextout=defaultdict(list,{k:[rid for rid in a if rid in active]
                              for k,a in self.pending_nextout.items()
                              if any(rid in active for rid in a)})
  self.warmup=False
  return {'target_horses_with_previous_form':len(self.h),
          'prior_target_runner_records_seen':self.history_target_runs,
          'historical_target_races_seen':self.historic_form_races,
          'retained_pre2022_form_races':len(self.race_opponents),
          'retained_nextout_records':len(self.nextout),
          'pending_nextout_horses':len(self.pending_nextout)}


def quarantine_historic_name_collision(r,modern):
 """Keep legacy name+country D identity only for verified same-modern horse."""
 import stage30_gap_aware_abcd as D
 key=(D.normal(r[8]),D.normal(r[10]))
 ids=modern.get(key)
 if not ids or (len(ids)==1 and S.ident(r) in ids):
  return r,False
 z=list(r)
 orig=S.ident(r) or (str(r[0])+str(r[8]))
 z[8]='historic_no_exact_match_'+hashlib.sha256(orig.encode('utf8')).hexdigest()[:20]
 return tuple(z),True


def warmup(db,reference,last,min_verified_prior=200000):
 """Preload source-identical State30 Past and Form through end of 2021.

 Daily updates are only published AFTER all races for that date have closed;
 all historical data are from actual verified UK/IE race IDs and source fields.
 """
 import stage30_gap_aware_abcd as D
 import stage108_model_g2 as G
 wanted,modern,target_audit=target_identities(db,last)
 ref,refaudit=G.load_reference(reference,first=FIRST,last=WARM_END)
 state=D.Past({});form=LeanHistoricForm(wanted)
 counts=collections.Counter();pending=[];today=None
 def publish():
  nonlocal pending
  for entries,old_for_d,info,fieldor in pending:
   state.update(old_for_d)
   form.add(entries,entries[0][1],info,fieldor)
  pending=[]
 for entries in S.read_races(db,first=FIRST,last=WARM_END):
  day=str(entries[0][1]);rid=str(entries[0][0])
  if today and day!=today:publish()
  today=day
  exact=ref.get(rid)
  if not exact:counts['not_in_verified_reference']+=1;continue
  rd,venue,rt,title,country=exact
  if rd!=day or D.track(entries[0][2])!=D.track(venue):
   counts['reference_conflict']+=1;continue
  if country not in ('GB','IRELAND') or D.track(entries[0][2]) not in D.TRACKS:
   counts['not_verified_ukie_course']+=1;continue
  starters=[r for r in entries if S.is_starter(r)]
  if len(starters)<2:counts['small_field']+=1;continue
  info,_,_,_=S.race_catalogue(entries[0],country,title,starters)
  fieldor=S.fnum(info.get('preoff_opposition_median_or'))
  # Stage30's legacy horse identity is name + country, so a 1991 horse
  # named identically to a 2025 horse must not be treated as the SAME horse.
  # For old rows without an EXACT modern full identity, alias ONLY the
  # Stage30 name key; original runner rows, sire/dam/trainer and Form stay real.
  old_for_d=[]
  for r in entries:
   row,quarantined=quarantine_historic_name_collision(r,modern)
   old_for_d.append(row)
   counts['historic_name_collision_rows_quarantined']+=int(quarantined)
  pending.append((entries,old_for_d,info,fieldor))
  counts['verified_prior_races']+=1;counts['verified_prior_starters']+=len(starters)
  counts['earliest_source_year_'+day[:4]]+=1
  if counts['verified_prior_races']%20000==0:
   print('STAGE142_PRESEED',counts['verified_prior_races'],day,
         'stats',len(state.s),'target_form_horses',len(form.h),flush=True)
 publish()
 form_audit=form.finish_warmup()
 if counts['verified_prior_races']<min_verified_prior:
  raise RuntimeError('Unexpectedly sparse pre-2022 UK/IE source; refuse misleading full history')
 report={'full_history_from':FIRST,'full_history_warmup_through':WARM_END,
 'source_earliest_checked':'1988-01-01','histories_have_no_max_run_count_in_pillar_stats':True,
 'recent_form_continues_to_use_last3_to_last8_real_starts':True,
 'retains_original_2022plus_NLP_supervision':True,
 'prior_calendar_day_only':True,'original_D_and_stage137_untouched':True,
 'eight_pillar_state_keys':len(state.s),'counts':dict(counts),
 'form':form_audit,'target':target_audit,'reference_audit':refaudit,
 'limitations':['Old race comments are preserved as historic form, but original Stage137 NLP learner remains trained from 2022 onward, isolating its existing training schedule.',
 'Not every horse has complete archived career history; first observed racing appearance is not verified career debut.',
 'The 8-run form deque retains the latest available actual starts; original 8 pillar cumulative statistics use all verified prior starts.',
 'Name-only old-horse collisions are quarantined when exact verified identity cannot be matched to 2022+ horses.',
 'Historical source labels before 2022 remain as originally parsed; Grade recovery is not silently included in this lookback ablation.']}
 return state,form,report


def reconstruct_full_core(db,reference,last,state,form):
 """Use the exact Stage137 scorer and same modern source dates, preseed state.

 Patches only fresh instance creation inside the ephemeral research process;
 Stage140's own 2026 year guard still checks original score source.
 """
 import stage30_gap_aware_abcd as D
 cls=D.Past;old_init=cls.__init__;old_form=S.Form
 called=[0]
 def initialize(self,mapping):
  if mapping:raise RuntimeError('Unexpected historical mapping in Stage142')
  called[0]+=1
  self.__dict__=state.__dict__
 try:
  cls.__init__=initialize
  S.Form=lambda:form
  races,audit,reference_audit=U.extend_stage137_only_for_2026(db,reference,last)
 finally:
  cls.__init__=old_init;S.Form=old_form
 if called[0]!=1:raise RuntimeError('Stage137 preseed factory not invoked exactly once')
 return races,audit,reference_audit


def align(base,extended):
 before={str(r['race_id']):r for r in base}
 after={str(r['race_id']):r for r in extended}
 if set(before)!=set(after):
  raise RuntimeError(f'Model cohort drift: baseline={len(before)}, extended={len(after)}; missing={list(set(before)-set(after))[:3]}')
 count=collections.Counter();changed=collections.Counter();earliest=[]
 for rid,old in before.items():
  new=after[rid]
  for col in ('date','country','broad','group','subtype','winner_idx'):
   if old[col]!=new[col]:raise RuntimeError(f'Unsafe cohort/metadata drift {rid} {col}')
  if old['identity']!=new['identity'] or old['horse']!=new['horse'] or not np.array_equal(old['win'],new['win']):
   raise RuntimeError('Horse result/order or identity drift: '+rid)
  if not np.allclose(old['sp'],new['sp'],equal_nan=True):
   raise RuntimeError('Starting prices changed: '+rid)
  if new['xf'].shape!=old['xf'].shape or new['x8'].shape!=old['x8'].shape:
   raise RuntimeError('Original feature shape drift: '+rid)
  count['races']+=1;count['runner_rows']+=len(old['horse'])
  if old['date']>='2026-01-01':
   for i,p in enumerate(S.PILLARS):
    changed[p]+=int(np.sum(~np.isclose(old['xf'][:,i],new['xf'][:,i],equal_nan=True)))
   changed['form_runs']+=int(np.sum(~np.isclose(old['xf'][:,S.FEATURES.index('horse_runs')],new['xf'][:,S.FEATURES.index('horse_runs')],equal_nan=True)))
 return {'matched_races':count['races'],'matched_runner_rows':count['runner_rows'],
         '2026_changes_by_pillar':dict(changed),'same_2026_runners_and_results':True,
         'same_original_50_input_feature_schema':True}


def variant(base,extended,core=False,form=False,weights=False):
 result={**base,'xf':np.array(base['xf'],copy=True)}
 if core:
  result['x8']=extended['x8']
  result['xf'][:,:8]=extended['xf'][:,:8]
  result['xf'][:,S.FEATURES.index('frozen_d_score')]=extended['xf'][:,S.FEATURES.index('frozen_d_score')]
 if form:
  for k in FORM_FEATURES:
   result['xf'][:,S.FEATURES.index(k)]=extended['xf'][:,S.FEATURES.index(k)]
 if weights:
  result=J.copy_race(result,weights=True,official=True)
 return result


def read_stage141_archive(folder,last):
 folder=pathlib.Path(folder)
 meta=json.loads((folder/'stage141-summary.json').read_text())
 if meta['source_cutoff']!=last or not meta['baseline_guard']['2025_q4_baseline_exact_match']:
  raise RuntimeError('Stage141 reference date or verified frozen baseline mismatch')
 if not meta['original_stage137_unchanged'] or not meta['original_stage139_hpr_unchanged']:
  raise RuntimeError('Stage141 source did not protect model identity')
 p=folder/'stage141-2026-rank1-rank2.csv.gz'
 seen={};result=[]
 with gzip.open(p,'rt',newline='',encoding='utf8') as f:
  for z in csv.DictReader(f):
   if z['model'] not in CONTROLS:continue
   k=(z['model'],str(z['race_id']))
   if k in seen:raise RuntimeError('Duplicate Stage141 reference prediction: '+str(k))
   seen[k]=True
   result.append({'quarter':z['quarter'],'date':z['date'],'race_id':z['race_id'],
      'course':z['meeting'],'broad':z['broad'],'discipline':z['discipline'],
      'subtype':z['subtype'],'official_tier':z['official_tier'],'model':z['model'],
      'rank1_horse':z['rank1_horse'],'rank2_horse':z['rank2_horse'],
      'rank1_won':int(z['rank1_won']),'rank2_won':int(z['rank2_won']),
      'sp':number(z['sp']),'winner':z['winner'],'bank':z['bank'],
      'field_size':int(z['field_size']),'source':'STAGE141_IMMUTABLE_ARTIFACT'})
 expected=int(meta['overall'][CONTROL_BASE]['races'])
 if len(result)!=2*expected or len(result)<5000:
  raise RuntimeError('Stage141 control cohort incomplete '+str((len(result),expected)))
 return result,meta


def compare_actual(reference,base):
 cohort={str(r['race_id']):r for r in base if r['date']>='2026-01-01'}
 for z in reference:
  rid=str(z['race_id']);r=cohort.get(rid)
  if r is None:raise RuntimeError('Missing 2026 race from source: '+rid)
  if str(r['date'])!=z['date'] or str(r['course'])!=z['course']:
   raise RuntimeError('Archived 2026 date/course mismatch: '+rid)
  if z['winner']!=r['horse'][int(r['winner_idx'])]:raise RuntimeError('Winner drift '+rid)
  if z['rank1_horse'] not in r['horse'] or z['rank2_horse'] not in r['horse']:
   raise RuntimeError('Archived runner identity drift '+rid)
  if bool(z['rank1_won'])!=(z['rank1_horse']==z['winner']):raise RuntimeError('Wrong prior win flag '+rid)
  if bool(z['rank2_won'])!=(z['rank2_horse']==z['winner']):raise RuntimeError('Wrong prior second win flag '+rid)
  if z['field_size']!=len(r['horse']):raise RuntimeError('Source field size drift '+rid)
 if len(cohort)!=len(reference)//len(CONTROLS):
  raise RuntimeError('2026 source cohort differs from Stage141 original')
 return {'stage141_controls_are_exact_same_2026_field_and_winners':True,
         '2026_control_races':len(cohort)}


def metric(rs):
 n=len(rs);wins=sum(int(x['rank1_won']) for x in rs)
 priced=[x for x in rs if x['sp'] is not None and 1<float(x['sp'])<1001]
 winnings=sum(float(z['sp']) for z in priced if z['rank1_won'])
 return {'races':n,'wins':wins,'strike_pct':round(100*wins/n,3) if n else None,
         'rank1_or2_wins':sum(int(z['rank1_won'])+int(z['rank2_won']) for z in rs),
         'priced':len(priced),'sp_coverage_pct':round(100*len(priced)/n,2) if n else None,
         'sp_roi_pct':round(100*(winnings/len(priced)-1),3) if priced else None}


def summarize(rs):
 groups=defaultdict(list)
 for z in rs:
  for d,key in [('ALL','ALL'),('QUARTER',z['quarter']),('YEAR',z['date'][:4]),
                ('MONTH',z['date'][:7]),('BROAD',z['broad']),('DISCIPLINE',z['discipline']),
                ('RACE_TYPE',z['subtype']),('TIER',z['official_tier'])]:
   groups[(z['model'],d,key)].append(z)
 return [{'model':a,'breakdown':b,'group':c,**metric(grp)} for (a,b,c),grp in sorted(groups.items())]


def evaluate(base,extended,archived,out,last):
 bylong={str(r['race_id']):r for r in extended}
 bybase={str(r['race_id']):r for r in base}
 output=list(archived);folds=[]
 runner_path=out/'stage142-2026-all-runners.csv.gz'
 full_handle=gzip.open(runner_path,'wt',encoding='utf8',newline='')
 runner_fields=['quarter','date','race_id','course','broad','discipline','subtype',
   'model','horse','winning','prediction_score','prediction_rank','hpr_preoff',
   'or_today','recorded_weight_lbs','or_weight_net_advantage',
   'horse_prior_starts_baseline_recent8','horse_prior_starts_extended_recent8']+[f'baseline_{k}' for k in S.PILLARS]+[f'extended_{k}' for k in S.PILLARS]
 runner_writer=csv.DictWriter(full_handle,runner_fields)
 runner_writer.writeheader()
 for q,start,end in U.SPLITS:
  if last<start:break
  end=min(end,last)
  training=[r for r in base if '2023-01-01'<=r['date']<start]
  testing=[r for r in base if start<=r['date']<=end]
  if not testing:continue
  if len(testing)<100:raise RuntimeError('Too few 2026 races in quarter '+q)
  specs={CORE:(True,False,False),FORM:(False,True,False),
         BOTH:(True,True,False),BOTH_WEIGHT:(True,True,True)}
  banks={}
  for key,(c,f,w) in specs.items():
   rows=[variant(z,bylong[str(z['race_id'])],c,f,w) for z in training]
   banks[key]=H.l137_ml_banks(rows,ability=True)
   del rows
  for r in testing:
   rid=str(r['race_id']);extra=bylong[rid]
   for key,(c,f,w) in specs.items():
    x=variant(r,extra,c,f,w)
    probs,bank=S.predict_bank(banks[key],H.hpr_ability_137(x))
    if len(probs)!=len(r['horse']) or not np.all(np.isfinite(probs)):
     raise RuntimeError('Model scoring error '+rid)
    order=np.argsort(-probs,kind='stable');i,j=map(int,order[:2]);sp=number(r['sp'][i]);hpr=number(r['hpr_preoff'][i])
    for rank,idx in enumerate(order,1):
     weight_row=r['weight_today_lbs'][idx]
     or_row=r['official_rating_today'][idx]
     net=r['or_features'][idx,J.OR_FEATURES.index('or_weight_equiv_vs_field')]
     runner_writer.writerow({'quarter':q,'date':r['date'],'race_id':rid,
       'course':r['course'],'broad':r['broad'],'discipline':r['kind'],
       'subtype':r['subtype'],'model':key,'horse':r['horse'][idx],
       'winning':int(idx==r['winner_idx']),'prediction_score':float(probs[idx]),
       'prediction_rank':rank,
       'hpr_preoff':number(r['hpr_preoff'][idx]),
       'or_today':number(or_row),'recorded_weight_lbs':number(weight_row),
       'or_weight_net_advantage':number(net),
       'horse_prior_starts_baseline_recent8':number(r['xf'][idx,S.FEATURES.index('horse_runs')]),
       'horse_prior_starts_extended_recent8':number(extra['xf'][idx,S.FEATURES.index('horse_runs')]),
       **{f'baseline_{col}':float(r['x8'][idx,n]) for n,col in enumerate(S.PILLARS)},
       **{f'extended_{col}':float(extra['x8'][idx,n]) for n,col in enumerate(S.PILLARS)}})
    output.append({'quarter':q,'date':r['date'],'race_id':rid,'course':r['course'],
      'broad':r['broad'],'discipline':r['kind'],'subtype':r['subtype'],
      'official_tier':r['official_tier'],'model':key,
      'rank1_horse':r['horse'][i],'rank2_horse':r['horse'][j],
      'rank1_won':int(i==r['winner_idx']),'rank2_won':int(j==r['winner_idx']),
      'sp':sp,'winner':r['horse'][r['winner_idx']],'field_size':len(r['horse']),
      'score_rank1':float(probs[i]),'score_rank2':float(probs[j]),
      'hpr_rank1':hpr,'bank':bank,'source':'STAGE142_SEQUENTIAL_RESEARCH'})
  folds.append({'quarter':q,'trained_through':(dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat(),
   'races_in_quarter':len(testing),'train_races':len(training)})
  print('STAGE142_QUARTER',json.dumps(folds[-1]),flush=True)
  del banks
 full_handle.close()
 if not folds:raise RuntimeError('No 2026 evaluation folds')
 cohort={k for k in bybase if bybase[k]['date']>='2026-01-01'}
 bymodel=defaultdict(set)
 for z in output:
  key=str(z['race_id'])
  if key in bymodel[z['model']]:raise RuntimeError('Duplicated model race '+str(z['model']))
  bymodel[z['model']].add(key)
 if set(bymodel)!=set(ALL_MODELS) or any(ids!=cohort for ids in bymodel.values()):
  raise RuntimeError('Models have different race/runner cohorts')
 table=summarize(output)
 U.store_rows(out/'stage142-2026-rank1-rank2.csv.gz',output,True)
 U.store_rows(out/'stage142-2026-discipline-racetype.csv',table)
 overall={m:next(z for z in table if z['model']==m and z['breakdown']=='ALL') for m in ALL_MODELS}
 return overall,folds


def research(db,reference,stage137,stage141,out,max_date=None):
 import stage30_gap_aware_abcd as D
 # Original Stage141 identity/weights/provenance check must remain exact.
 original=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
 if original['chosen_combination']!='D-064-gap14':raise RuntimeError('Frozen D identity changed')
 exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
 if not np.allclose([original['weights_pct'][p] for p in S.PILLARS],exact*100,atol=.011):
  raise RuntimeError('Frozen D weights mismatch vs archived Stage30')
 S.FROZEN[:]=exact;S.CANDIDATES=S.candidate_weights()
 if H.ABILITY_LIFT!=.35:raise RuntimeError('Original HPR Ability coefficient changed')
 last,source_audit=U.audit_limit(db,max_date)
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 prior,archmeta=read_stage141_archive(stage141,last)
 # Same 2022-onward construction as Stage141: no new features on control side.
 base,base_audit,ref_audit=J.construct_2026(db,reference,last)
 check=U.verified_2025_regression(None,base,stage137)
 control_check=compare_actual(prior,base)
 state,form,historical_audit=warmup(db,reference,last)
 extended,extended_audit,long_ref_audit=reconstruct_full_core(db,reference,last,state,form)
 same=align(base,extended)
 del state,form
 target={k for r in base for k in r['identity'] if k}
 old_end=H.END
 try:
  H.END=last
  hpr_source,hpr_audit=H.hpr_history(db,reference,out,target)
 finally:H.END=old_end
 h1=collections.Counter();h2=collections.Counter()
 H.attach_hpr(base,hpr_source,h1)
 H.attach_hpr(extended,hpr_source,h2)
 if dict(h1)!=dict(h2):raise RuntimeError('The HPR provenance/coverage changed with lookback')
 # Reconstruct Stage141 OR and carried-weight history once, and match horse order.
 weight_audit=J.weight_context(db,reference,base,out,last)
 byid={str(r['race_id']):r for r in base}
 for r in extended:
  old=byid[str(r['race_id'])]
  for k in ('weight_features','or_features','weight_today_lbs','official_rating_today'):
   r[k]=old[k]
 # Frozen 2026 controls come ONLY from the verified 2026 Stage141 artifact.
 overall,folds=evaluate(base,extended,prior,out,last)
 summary={'version':VERSION,'status':'RESEARCH_ONLY_FULL_HISTORY_LOOKBACK_ABLATION',
  'source_cutoff':last,'first_available_historical_year':'1988',
  'source_audit':source_audit,'models':list(ALL_MODELS),
  '2025_original_stage137_exact_reproduction':check,
  '2026_immutable_stage141_cohort':control_check,
  'stage141_source_version':archmeta['version'],
  'stage141_controls_provenance':'stage141-grade-and-weight-43782077626',
  'full_history':historical_audit,'matched_and_feature_differences':same,
  'base_source_audit':base_audit,'long_source_audit':extended_audit,
  'hpr_audit':hpr_audit,'hpr_coverage':dict(h1),
  'weight_audit':weight_audit,'folds':folds,'overall':overall,
  'frozen_D_unchanged':True,'stage137_unchanged':True,'stage139_hpr_unchanged':True,
  'stage141_reference_unchanged':True,'live_predictions_modified':False,'live_database_modified':False,
  'interpretation':['Original eight D pillar statistics warm up on all prior exact GB/IE archive rows since 1988; no maximum past-run count.',
    'The extended Form evidence retains latest 8 actual observed horse starts; first-up, trend and same-code comparisons adapt using those starts.',
    'Original supervised NLP learning remains from 2022 onward for both arms to isolate historical source extension.',
    'HPR v3.1 and Stage141 actual weight / OR history are identical and already full-history, so no artificial difference is introduced to them.',
    'Neither archived winner nor starting prices enter same-race predictors.',
    'All 2026 evaluations are retrospective comparisons; 2026 outcomes were known to researchers.',
    'SP ROI is historical priced-subset benchmark; bookmaker-time prices are not independently verified.',
    'Historical data completeness varies; observed first runs are not proof of career debut.']}
 (out/'stage142-summary.json').write_text(json.dumps(summary,indent=2,default=str)+'\n')
 print('STAGE142_OVERALL',json.dumps(overall),flush=True)
 return summary


def self_test():
 assert FIRST=='1988-01-01' and WARM_END<'2022-01-01'
 assert len(S.PILLARS)==8 and H.ABILITY_LIFT==.35
 assert 'or_weight_equiv_vs_field' in J.OR_FEATURES
 assert len(J.W_FEATURES)==17 and len(J.OR_FEATURES)==23
 assert len(FORM_FEATURES)==len(set(FORM_FEATURES))
 assert set(FORM_FEATURES).issubset(S.FEATURES)
 assert set(ALL_MODELS)==set(CONTROLS+NEW_MODELS)
 # Confirm copying pre-race features never changes baseline or preoff HPR arrays.
 a={'xf':np.zeros((2,len(S.FEATURES)),dtype=np.float32),'x8':np.zeros((2,8),dtype=np.float32)}
 b={'xf':a['xf'].copy(),'x8':a['x8'].copy()}
 b['xf'][:,0]=[.4,.2];b['xf'][:,S.FEATURES.index('horse_runs')]=[5.,3.]
 b['xf'][:,S.FEATURES.index('frozen_d_score')]=[.1,.2]
 v=variant(a,b,core=True,form=False)
 assert np.allclose(v['xf'][:,0],[.4,.2])
 assert np.allclose(v['xf'][:,S.FEATURES.index('horse_runs')],0)
 assert np.allclose(a['xf'],0)
 q=variant(a,b,core=False,form=True)
 assert np.allclose(q['xf'][:,0],0)
 assert np.allclose(q['xf'][:,S.FEATURES.index('horse_runs')],[5,3])
 c=variant(a,b,core=True,form=True)
 assert np.allclose(c['xf'][:,0],[.4,.2])
 assert np.allclose(c['xf'][:,S.FEATURES.index('horse_runs')],[5,3])
 import stage30_gap_aware_abcd as D
 row=[None]*25;row[0]='old';row[8]='Example Horse';row[9]='Example Horse';row[10]='GB'
 row[11]='sourceid1';row[18]='One Sire';row[19]='One Dam'
 verified=S.ident(tuple(row))
 key=(D.normal(row[8]),D.normal(row[10]))
 same,changed=quarantine_historic_name_collision(tuple(row),{key:{verified}})
 assert not changed and same[8]==row[8]
 other,changed=quarantine_historic_name_collision(tuple(row),{key:{'SOME_OTHER_EXACT_HORSE'}})
 assert changed and other[8]!=row[8] and other[9]==row[9]
 assert metric([{'rank1_won':1,'rank2_won':0,'sp':3.0},
               {'rank1_won':0,'rank2_won':1,'sp':2.0}])['sp_roi_pct']==50.0
 print('STAGE142_SELFTEST_OK - immutable baselines, 8 pillars, form-only ablation, OR-weight reuse')


def main():
 p=argparse.ArgumentParser()
 p.add_argument('command',choices=('self-test','walkforward'))
 p.add_argument('--db',type=pathlib.Path)
 p.add_argument('--reference',type=pathlib.Path)
 p.add_argument('--stage137',type=pathlib.Path)
 p.add_argument('--stage141',type=pathlib.Path)
 p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage142/research'))
 p.add_argument('--to-date')
 a=p.parse_args()
 if a.command=='self-test':self_test()
 else:
  for v in (a.db,a.reference,a.stage137,a.stage141):
   if v is None:raise SystemExit('Missing one or more input files for Stage142')
  research(a.db,a.reference,a.stage137,a.stage141,a.out,a.to_date)

if __name__=='__main__':main()
