#!/usr/bin/env python3
"""Stage143: Frozen D, four Weight/OR/Grade combinations, eight-pillar specialist optimisation.

Pure research. Original Frozen D and Stage137/141 models and databases are immutable.
Grade is recovered ONLY from explicit Stage65P source/verified event names.
Historic OR and actual recorded carried weight are separate, and any theoretical
OR-minus-weight merit is used ONLY in a variant containing BOTH signals.

Every new signal is mapped INTO one of eight named D pillars, never a 9th pillar.
Prior comments, position, beaten data and running conditions are source-limited.
First-observed GB/IE starts are *not* verified world-wide debutants.

Weight candidate grids are predetermined and selected from PREVIOUS race results
within discipline and race type. Evaluations in 2026 are chronological OOS; all
prediction inputs are pre-race and odds blind, SP only for retrospective ROI.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,json,math,pathlib,re,sqlite3
from collections import defaultdict,deque
import numpy as np

VERSION='stage143-frozen-d-eight-pillar-discipline-racetype-weightgrid-v1'
FROZEN='FROZEN_D_EXACT'
FORM='D_FORM_DEBUT_EIGHT_PILLARS'
W_OR='D_FORM_WEIGHT_OR'
W_G='D_FORM_WEIGHT_GRADE'
O_G='D_FORM_OR_GRADE'
ALL='D_FORM_WEIGHT_OR_GRADE'
ARMS=(FORM,W_OR,W_G,O_G,ALL)
OBJECTIVES=('SR','ROI')
MODELS=(FROZEN,)+tuple(a+'__'+mode for a in ARMS for mode in OBJECTIVES)
DISCIPLINES=('AW','FLAT','FLAT_UNKNOWN','HURDLE','CHASE','NH_FLAT')
MIN_SUBTYPE=350
MIN_GRADED=300
MIN_DISCIPLINE=650
MIN_BROAD=900
GRID_SEED=143037
# Fixed prior to seeing 2026 labels. Only optimisation is in 8-pillar shares.
SRC_BONUS={'quality':.23,'trend':.11,'beaten':.08,'comment':.10,'nlp':.08,
           'rivals':.06,'jumping':.17,'interference':.11,'trainer_debut':.16,
           'jockey_debut':.08,'sire_debut':.11,'dam_debut':.07,
           'weight_form':.20,'weight_relative':.08,'or_relative':.19,
           'winning_mark':.12,'net_merit':.22,'grade_context':.20}
PILLARS=('Ability','Trainer','Jockey','Course','Draw','Distance','Ground','Jumping')


def num(v):
 try:
  f=float(v)
  return f if math.isfinite(f) else None
 except (ValueError,TypeError):return None


def strict_center(a,n,min_known=2,denominator=1.0):
 """Within-field comparable signal. Unknown = neutral, never zero ability."""
 z=np.asarray(a,dtype=float)
 good=np.isfinite(z)
 if len(z)!=n:raise ValueError('Wrong length of signal array')
 out=np.zeros(n,dtype=np.float64)
 if good.sum()>=min_known:
  v=z[good]
  out[good]=np.clip((v-np.mean(v))/max(float(denominator),float(np.std(v)),1e-6),-1.5,1.5)
 return out


def field_feature(r,name,grade=False):
 import stage137_frozen_d_race_type_walkforward as S
 a=r['xf'][:,S.FEATURES.index(name)]
 return np.asarray(a,dtype=float)


def field_special(r,domain,name):
 import stage141_grade_weight_2026 as J
 keys=J.W_FEATURES if domain=='weight' else J.OR_FEATURES
 a=r['weight_features'] if domain=='weight' else r['or_features']
 return np.asarray(a[:,keys.index(name)],dtype=float)


def candidate_grid(frozen):
 """Deterministic ~280 candidate weight configurations, no extra dimension."""
 frozen=np.asarray(frozen,dtype=float)
 assert frozen.shape==(8,) and np.all(frozen>=0)
 frozen=frozen/frozen.sum()
 rng=np.random.default_rng(GRID_SEED)
 pool=[frozen.copy(),np.ones(8)/8]
 for i in range(8):
  for factor in (.2,.45,1.8,3.5):
   v=frozen.copy();v[i]*=factor;pool.append(v/v.sum())
 for strength in (10.,30.,80.):
  for z in rng.dirichlet(frozen*strength,size=76):
   pool.append(z)
 return np.asarray(pool,dtype=np.float32)


def masked_grid(grid,discipline):
 """No Draw in National Hunt, no Jumping in Flat; eight columns retained."""
 w=np.asarray(grid,dtype=float).copy()
 if discipline in ('HURDLE','CHASE','NH_FLAT'):w[:,4]=0
 else:w[:,7]=0
 w/=w.sum(axis=1)[:,None]
 return w.astype(np.float32)


def fixed_weight(frozen,discipline):
 return masked_grid(np.asarray(frozen,dtype=float)[None,:],discipline)[0]


def geo(r):
 if r['kind'] in ('HURDLE','CHASE','NH_FLAT'):return r['kind']
 return r['broad'] if r['broad'] in ('AW','FLAT','FLAT_UNKNOWN') else 'FLAT_UNKNOWN'


def stratify(r):
 """Subcategory -> discipline-specific grade -> discipline -> broader group."""
 disc=geo(r);sub=str(r['subtype']);tier=str(r['official_tier']);kind=r['kind']
 keys=[('RACE_TYPE',disc+':'+sub)]
 if ('GRADE_' in tier or 'GROUP_' in tier or 'LISTED' in tier):
  cat='GRADED_OR_LISTED'
  keys.append(('GRADE_LEVEL',disc+':'+tier))
  keys.append(('GRADE_GROUP',disc+':'+cat))
 keys.append(('DISCIPLINE',disc))
 if disc in ('HURDLE','CHASE','NH_FLAT'):keys.append(('BROAD','NH'))
 else:keys.append(('BROAD','FLAT'))
 return keys


def support_threshold(level):
 return {'RACE_TYPE':MIN_SUBTYPE,'GRADE_LEVEL':MIN_GRADED,'GRADE_GROUP':MIN_GRADED,
         'DISCIPLINE':MIN_DISCIPLINE,'BROAD':MIN_BROAD}[level]


def strong_form_pillars(r,graded,debut):
 """Adds source-supported form, comments and first-recorded starts to 8 pillars.

    - Ability: finish/adjusted-beaten, improvement and corroborated next-outs
    - Trainer/Jockey: first recorded starts, split age 3 and discipline
    - Jumping: past mistakes/jumping notes (NH only)
    - Distance: trip-normalised recorded beaten margin (not official margin)
    - Course, Draw and Ground: preserved ORIGINAL D scoring (no fake signal).
 """
 n=len(r['horse']);bonus=np.zeros((n,8),dtype=np.float64)
 def c(name,scale=1,min_known=2):
  return strict_center(field_feature(graded,name),n,min_known,scale)
 # The original Frozen D eight pillar values are separately retained intact.
 bonus[:,0]+=SRC_BONUS['quality']*c('horse_form_quality3',.25)
 bonus[:,0]+=SRC_BONUS['trend']*c('horse_form_trend',.2)
 bonus[:,0]-=SRC_BONUS['beaten']*c('horse_beaten3',5.)
 bonus[:,0]+=SRC_BONUS['comment']*c('horse_comment_phrase3',.25)
 bonus[:,0]+=SRC_BONUS['nlp']*c('horse_comment_nlp3',.2)
 bonus[:,0]+=SRC_BONUS['rivals']*c('prior_rivals_nextout_quality3',.2)
 bonus[:,5]-=.05*c('horse_beaten_per_mile3',4.)
 if r['kind'] in ('HURDLE','CHASE'):
  bonus[:,7]+=SRC_BONUS['jumping']*c('horse_jumping_comment3',.3)
  bonus[:,7]-=SRC_BONUS['interference']*c('horse_interference3',.4)
 if debut is not None:
  if len(debut)!=n:raise RuntimeError('Debutant feature alignment changed')
  # Missing history neutral. These are first recorded GB/IE starts only.
  bonus[:,1]+=SRC_BONUS['trainer_debut']*strict_center(debut[:,0],n,2,.35)
  bonus[:,1]+=.07*strict_center(debut[:,1],n,2,.35)
  bonus[:,2]+=SRC_BONUS['jockey_debut']*strict_center(debut[:,2],n,2,.35)
  bonus[:,0]+=SRC_BONUS['sire_debut']*strict_center(debut[:,3],n,2,.35)
  bonus[:,0]+=SRC_BONUS['dam_debut']*strict_center(debut[:,4],n,2,.35)
 return bonus


def additive(r,graded,debut,weight=False,official=False,grade=False):
 n=len(r['horse'])
 out=np.asarray(r['x8'],dtype=float).copy()
 out+=strong_form_pillars(r,graded,debut)
 def ww(name,den=8):return strict_center(field_special(r,'weight',name),n,2,den)
 def oo(name,den=8):return strict_center(field_special(r,'or',name),n,2,den)
 if weight:
  # Historical carried weight and best previous run; NOT assumed to be final
  # adjusted post-claim weight when original source lacks jockey allowance.
  out[:,0]-=SRC_BONUS['weight_form']*ww('weight_delta_best_form')
  out[:,0]-=.10*ww('weight_delta_last3')
  out[:,0]-=SRC_BONUS['weight_relative']*ww('weight_today_rel_field')
 if official:
  # Relative OR, last winning handicap mark, recent competitive finishing form.
  out[:,0]+=SRC_BONUS['or_relative']*oo('or_vs_field_mean',12)
  near=field_special(r,'or','or_near_winning_mark_good_form')
  # True only with verified last-winning OR AND >=2 subsequent runs +
  # competitive recent finishes. Other runners receive NEUTRAL unknown.
  out[:,0]+=SRC_BONUS['winning_mark']*strict_center(near,n,2,.4)
 if official and weight:
  # Only paired Weight+OR arm gets counterfactual handicap net merit.
  out[:,0]+=SRC_BONUS['net_merit']*oo('or_weight_equiv_vs_field',12)
 if grade:
  # Past grade strength based on actual prior runs, not guessed G1=Class1.
  # All runners in same current race share its level, so grade level itself
  # is not a horse-specific merit boost.
  tier=field_feature(graded,'past_vs_current_tier_delta')
  out[:,0]-=SRC_BONUS['grade_context']*strict_center(tier,n,2,12)
  # Competitiveness of recent performance against historical opposition:
  comp=field_feature(graded,'past_opposition_median3')
  out[:,0]+=.10*strict_center(comp,n,2,15.)
 if not np.all(np.isfinite(out)):raise RuntimeError('Nonfinite pillar scores')
 return out.astype(np.float32)


def first_recorded_history(db,reference,races,last,out):
 """Rolling source-only debut evidence; first OBSERVED ≠ certified first ever.

 All observations released only when advancing to following calendar date.
 Trainer/Jockey first-observed 3yo rates, sire/dam first-observed 3yo rates,
 and trainer first-observed race-code rates; 5y for trainer, 12y breeding.
 """
 import stage137_frozen_d_race_type_walkforward as S
 import stage108_model_g2 as G
 import stage30_gap_aware_abcd as D
 ref,_=G.load_reference(reference,first='1988-01-01',last=last)
 needed={str(r['race_id']):r for r in races}
 seen=set();history=defaultdict(deque);pending=[];previous_day=None;seen_debut=[]
 stats=collections.Counter();outputs={}
 def norm(s):return D.normal(s)
 def key(typ,name,age,kind):return (typ,norm(name),int(age),kind)
 def current_rate(k,day,years=5):
  seq=history.get(k)
  if not seq:return None
  cutoff=(dt.date.fromisoformat(day)-dt.timedelta(days=365*years)).isoformat()
  while seq and seq[0][0]<cutoff:seq.popleft()
  n=len(seq)
  if n<8:return None
  w=sum(z[1] for z in seq)
  # Hierarchical base 10% win-rate, strongly shrunk when small.
  return (w+2.5)/(n+25.)
 def publish():
  nonlocal pending
  for k,age,kind,trainer,jockey,sire,dam,win,day in pending:
   if k:seen.add(k)
   if age in (2,3,4) and win is not None:
    for typ,v,span in [('TRAINER',trainer,5),('JOCKEY',jockey,5),('SIRE',sire,12),('DAM',dam,12)]:
     if v:history[key(typ,v,age,kind)].append((day,win))
    if trainer:history[key('TRAINER_CODE',trainer,age,'ALL')].append((day,win))
  pending=[]
 for entries in S.read_races(db,first='1988-01-01',last=last):
  day=str(entries[0][1]);rid=str(entries[0][0])
  if previous_day and day!=previous_day:publish()
  previous_day=day
  rf=ref.get(rid)
  if not rf or rf[0]!=day or D.track(rf[1])!=D.track(entries[0][2]) or rf[4] not in ('GB','IRELAND'):
   continue
  starters=[x for x in entries if S.is_starter(x)]
  if len(starters)<2:continue
  kind=('CHASE' if 'chase' in str(entries[0][3]).lower() or 'chase' in str(rf[3]).lower() else
        'HURDLE' if 'hurdle' in str(entries[0][3]).lower() or 'hurdle' in str(rf[3]).lower() else
        'NH_FLAT' if 'nh flat' in str(rf[3]).lower() or 'bumper' in str(rf[3]).lower() else 'FLAT')
  first=[];freq=collections.Counter(S.ident(x) for x in starters)
  for row in starters:
   k=S.ident(row);age=num(row[17]);age=int(age) if age is not None and age==int(age) else None
   if k and freq[k]==1 and k not in seen and age in (2,3,4):
    trainer,jockey,sire,dam=row[14],row[15],row[18],row[19]
    if trainer and age==3:stats['first_recorded_3yo']+=1
    win=int(row[12]==1) if row[12] is not None else None
    first.append((k,age,kind,trainer,jockey,sire,dam,win,day))
  target=needed.get(rid)
  if target:
   ix={S.ident(x):x for x in starters if S.ident(x) and freq[S.ident(x)]==1}
   if not set(target['identity']).issubset(ix):raise RuntimeError('Debut target missing runner identity: '+rid)
   vals=[]
   for k in target['identity']:
    row=ix[k];age=num(row[17]);age=int(age) if age is not None and age==int(age) else None
    if age not in (2,3,4) or k in seen:
     vals.append([np.nan]*5);continue
    trainer,jockey,sire,dam=row[14],row[15],row[18],row[19]
    def get(typ,name,age,kind,span):
     if not name:return np.nan
     v=current_rate(key(typ,name,age,kind),day,span)
     return v if v is not None else np.nan
    vals.append([get('TRAINER',trainer,age,kind,5),
      get('TRAINER_CODE',trainer,age,'ALL',5),
      get('JOCKEY',jockey,age,kind,5),
      get('SIRE',sire,age,kind,12),
      get('DAM',dam,age,kind,12)])
    stats['target_first_recorded_starts']+=1
   outputs[rid]=np.asarray(vals,dtype=np.float32)
  pending.extend(first)
  stats['eligible_prior_races']+=1
 publish()
 if set(outputs)!=set(needed):raise RuntimeError('Debut feature coverage/cohort mismatch: '+str(len(set(needed)-set(outputs))))
 for r in races:r['debut_features']=outputs[str(r['race_id'])]
 report={'first_source_date':'1988-01-01','through_date':last,'matched_races':len(needed),
         'counts':dict(stats),'first_recorded_not_certified_debut':True,
         'historic_identity_minimum': 'horse+sire+dam or horse source ID',
         'trainer_3yo_firststart_record_prior_only':True,
         'missing_trainer_sire_dam_neutral':True}
 (out/'stage143-firstrecorded-debut-audit.json').write_text(json.dumps(report,indent=2)+'\n')
 print('STAGE143_DEBUT_AUDIT',json.dumps(report['counts']),flush=True)
 return report


def optimise_by_race_type(train,arm,grid,reference_weights):
 """Calculate both historical-SR and prior capped-SP-ROI selections in ONE pass.

 Two predetermined objectives are trained on the same prior cohort. Today's
 bookmaker prices are never supplied to the prediction score calculation.
 """
 store={};n=grid.shape[0]
 masks={d:masked_grid(grid,d) for d in DISCIPLINES}
 for r in train:
  discipline=geo(r)
  w=masks[discipline]
  ix=np.argmax(r['scores'][arm]@w.T,axis=0)
  wins=(ix==r['winner_idx']).astype(float)
  sp=np.asarray(r['sp'],dtype=float)[ix]
  priced=np.isfinite(sp)&(sp>1)&(sp<1001)
  paid=np.where(priced & (wins>0),np.minimum(sp,21.),0.)
  for level,group in stratify(r):
   key=(level,group,discipline)
   if key not in store:store[key]=[0,np.zeros(n,dtype=float),np.zeros(n,dtype=float),np.zeros(n,dtype=float)]
   store[key][0]+=1;store[key][1]+=wins
   store[key][2]+=paid;store[key][3]+=priced
 banks={mode:{} for mode in OBJECTIVES}
 for (level,group,discipline),(count,wins,paid,priced) in store.items():
  if count<support_threshold(level):continue
  combo=masks[discipline]
  parent=fixed_weight(reference_weights,discipline)
  penalty=.012*np.square(combo-parent).sum(axis=1)+.002*np.square(combo).sum(axis=1)
  for mode in OBJECTIVES:
   if mode=='SR':objective=(wins+1.)/(count+2.)-penalty
   elif mode=='ROI':
    # Prior capped SP only, never future prices. Strike rate supports
    # the ROI objective to guard against extreme outsider portfolios.
    roi=paid/np.maximum(priced,1.)-1.
    objective=roi+.25*(wins/count)-3*penalty
    objective=np.where(priced>=max(125,.75*count),objective,-1e9)
   else:raise ValueError('Unknown objective '+str(mode))
   best=int(np.argmax(objective))
   banks[mode][(level,group)]={'candidate_id':best,'weights':combo[best],
          'training_n':count,'training_wins':int(wins[best]),
          'training_sr_pct':round(100*wins[best]/count,3),
          'training_capped_sp_roi_pct':round(100*(paid[best]/priced[best]-1),3) if priced[best] else None,
          'weight_selection_objective':mode}
 return banks


def choose_bank(banks,r,frozen):
 for level,group in stratify(r):
  x=banks.get((level,group))
  if x:return x['weights'],level,group,x
 d=geo(r)
 return fixed_weight(frozen,d),'FROZEN_BACKOFF',d,{'candidate_id':0,'training_n':0,'training_wins':None,'training_sr_pct':None}


def metric(rows):
 n=len(rows);win=sum(x['rank1_won'] for x in rows)
 priced=[x for x in rows if x['sp'] is not None and 1<float(x['sp'])<1001]
 gross=sum(x['sp'] for x in priced if x['rank1_won'])
 return {'races':n,'wins':win,'strike_pct':round(100*win/n,3) if n else None,
         'rank1_or2_winners':sum(x['rank1_won']+x['rank2_won'] for x in rows),
         'priced':len(priced),'sp_coverage_pct':round(100*len(priced)/n,2) if n else 0,
         'sp_roi_pct':round(100*(gross/len(priced)-1),3) if priced else None}


def dump_csv(path,rows):
 if not rows:raise RuntimeError('Empty output '+str(path))
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 opener=gzip.open if path.suffix=='.gz' else open
 with opener(path,'wt',encoding='utf8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]),extrasaction='ignore');w.writeheader();w.writerows(rows)


def quarterly_eval(base,grade,archived,frozen,grid,out,last):
 import stage140_d137_hpr_ability_2026 as U
 import stage137_frozen_d_race_type_walkforward as S
 import stage141_grade_weight_2026 as J
 proof=U.verified_2025_regression(None,base,archived)
 ix_grade={str(x['race_id']):x for x in grade}
 if set(ix_grade)!={str(x['race_id']) for x in base}:
  raise RuntimeError('Grade overlay changed race cohort')
 bmap={str(x['race_id']):x for x in base}
 if len(bmap)!=len(base):raise RuntimeError('Duplicate race ID in source')
 for r in base:
  g=ix_grade[str(r['race_id'])]
  if g['identity']!=r['identity'] or g['winner_idx']!=r['winner_idx'] or g['horse']!=r['horse']:
   raise RuntimeError('Grade-variant horse/order mismatch')
  if not np.allclose(g['x8'],r['x8'],equal_nan=True):raise RuntimeError('Grade overlay altered D!')
  r['subtype']=g['subtype'];r['official_tier']=g['official_tier'];r['race_name']=g['race_name']
  r['scores']={FROZEN:np.asarray(r['x8'],dtype=np.float32)}
  signals=strong_form_pillars(r,g,r.get('debut_features'))
  r['scores'][FORM]=(r['x8']+signals).astype(np.float32)
  for arm,w,o,gr in ((W_OR,True,True,False),(W_G,True,False,True),
                     (O_G,False,True,True),(ALL,True,True,True)):
   r['scores'][arm]=additive(r,g,r.get('debut_features'),w,o,gr)
  for k,z in r['scores'].items():
   if z.shape!=r['x8'].shape:raise RuntimeError('Lost 8-pillar alignment '+k)
 rows=[];winners=[];weights=[];folds=[]
 runner_path=out/'stage143-all-runners-2026.csv.gz'
 with gzip.open(runner_path,'wt',encoding='utf8',newline='') as f:
  runner_fields=['date','quarter','race_id','horse','winner','discipline','race_type','model','score','rank',
    'weight_lbs','official_rating','winmark','weight_change','or_net_merit','recorded_first_start',
    *('pillar_'+x for x in PILLARS)]
  writer=csv.DictWriter(f,runner_fields);writer.writeheader()
  for q,start,end in U.SPLITS:
   if last<start:break
   end=min(end,last)
   training=[r for r in base if '2023-01-01'<=r['date']<start]
   testing=[r for r in base if start<=r['date']<=end]
   if not testing:continue
   banks={arm:optimise_by_race_type(training,arm,grid,frozen) for arm in ARMS}
   fold={'quarter':q,'train_last_date':(dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat(),
         'training_races':len(training),'testing_races':len(testing),
         'weight_banks_count':{a+'__'+mode:len(banks[a][mode]) for a in ARMS for mode in OBJECTIVES}}
   folds.append(fold)
   for arm,bank_by_mode in banks.items():
    for mode,bank in bank_by_mode.items():
     for (level,group),z in bank.items():
      weights.append({'quarter':q,'model':arm+'__'+mode,'objective':mode,'level':level,'group':group,
        'candidate_id':z['candidate_id'],'training_races':z['training_n'],
        'training_wins':z['training_wins'],'training_sr_pct':z['training_sr_pct'],
        'training_capped_sp_roi_pct':z['training_capped_sp_roi_pct'],
        **{p+'_weight_pct':round(100*float(w),3) for p,w in zip(PILLARS,z['weights'])}})
   for r in testing:
    disc=geo(r);n=len(r['horse']);rid=str(r['race_id']);winner=int(r['winner_idx'])
    for model in MODELS:
     if model==FROZEN:
      arm=FROZEN;mode='ORIGINAL'
      # Match Stage30's original jumps/Flat classification, even where a
      # later race-type parser assigns a different discipline (e.g. bumper).
      frozen_code='CHASE' if r['d_original_style']=='jumps' else 'FLAT'
      w=fixed_weight(frozen,frozen_code);level='ORIGINAL_FROZEN';group=frozen_code;z={'candidate_id':-1,'training_n':0}
     else:
      arm,mode=model.rsplit('__',1)
      w,level,group,z=choose_bank(banks[arm][mode],r,frozen)
     p=r['scores'][arm];val=p@w
     order=np.argsort(-val,kind='stable');one,two=map(int,order[:2]);sp=num(r['sp'][one])
     rows.append({'quarter':q,'date':r['date'],'race_id':rid,'course':r['course'],
       'race_name':r['race_name'],'broad':r['broad'],'discipline':r['kind'],
       'discipline_specialist':disc,'race_type':r['subtype'],'tier':r['official_tier'],
       'model':model,'optimisation_objective':mode,'rank1_horse':r['horse'][one],'rank2_horse':r['horse'][two],
       'rank1_won':int(one==winner),'rank2_won':int(two==winner),
       'sp':sp,'winner':r['horse'][winner],'field_size':n,
       'selected_weight_level':level,'selected_weight_group':group,
       'selected_candidate_id':z['candidate_id'],'training_group_races':z['training_n'],
       'rank1_score':float(val[one]),
       **{pname+'_weight_pct':round(100*float(v),3) for pname,v in zip(PILLARS,w)}})
     for rank,i in enumerate(order,1):
      wf=r['weight_features'];of=r['or_features'];deb=r.get('debut_features')
      writer.writerow({'date':r['date'],'quarter':q,'race_id':rid,'horse':r['horse'][i],
        'winner':int(i==winner),'discipline':disc,'race_type':r['subtype'],
        'model':model,'score':round(float(val[i]),6),'rank':rank,
        'weight_lbs':num(r['weight_today_lbs'][i]),
        'official_rating':num(r['official_rating_today'][i]),
        'winmark':num(of[i,J.OR_FEATURES.index('or_last_handicap_win_mark')]),
        'weight_change':num(wf[i,J.W_FEATURES.index('weight_delta_last')]),
        'or_net_merit':num(of[i,J.OR_FEATURES.index('or_weight_equiv_vs_field')]),
        'recorded_first_start':int(deb is not None and np.isfinite(deb[i]).any()),
        **{'pillar_'+pname:round(float(p[i,j]),5) for j,pname in enumerate(PILLARS)}})
   print('STAGE143_QUARTER',json.dumps(fold),flush=True)
 if not rows:raise RuntimeError('No 2026 picks')
 grp=defaultdict(list)
 for row in rows:
  for dim,key in [('ALL','ALL'),('QUARTER',row['quarter']),('DISCIPLINE',row['discipline_specialist']),
      ('RACE_TYPE',row['discipline_specialist']+':'+row['race_type']),
      ('GRADE',row['tier']),('MONTH',row['date'][:7]),
      ('REGIME',row['selected_weight_level']+':'+row['selected_weight_group'])]:
   grp[(row['model'],dim,key)].append(row)
 summary=[{'model':m,'breakdown':dim,'group':key,**metric(xx)} for (m,dim,key),xx in sorted(grp.items())]
 overall={m:next(x for x in summary if x['model']==m and x['breakdown']=='ALL') for m in MODELS}
 if {z['races'] for z in overall.values()}!={9794}:
  raise RuntimeError('Expected Stage141 2026 matched cohort 9794, got '+str(overall))
 dump_csv(out/'stage143-2026-rank1-rank2.csv.gz',rows)
 dump_csv(out/'stage143-2026-discipline-racetype.csv',summary)
 dump_csv(out/'stage143-learned-eight-pillar-weights.csv',weights)
 return overall,folds,proof,{'rows':len(rows),'type_counts':dict(collections.Counter(z['race_type'] for z in rows if z['model']==FROZEN))}


def research(db,reference,archived,out,to_date='2026-11-10'):
 import stage30_gap_aware_abcd as D
 import stage137_frozen_d_race_type_walkforward as S
 import stage140_d137_hpr_ability_2026 as U
 import stage141_grade_weight_2026 as J
 orig=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
 if orig['chosen_combination']!='D-064-gap14':raise RuntimeError('Frozen D source was replaced')
 exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
 if not np.allclose([orig['weights_pct'][p] for p in PILLARS],exact*100,atol=.011):
  raise RuntimeError('Frozen D exact pillar weights changed')
 S.FROZEN[:]=exact
 last,date_audit=U.audit_limit(db,to_date)
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 overlay,grade_audit=J.official_grade_overlay(db,reference,out,last)
 if len(overlay)<100:raise RuntimeError('No verified Grade/Group source overlay')
 base,construct_audit,reference_audit=J.construct_2026(db,reference,last)
 graded,graded_audit,_=J.construct_2026(db,reference,last,overlay)
 w_audit=J.weight_context(db,reference,base,out,last)
 d_audit=first_recorded_history(db,reference,base,last,out)
 grid=candidate_grid(exact)
 overall,folds,proof,meta=quarterly_eval(base,graded,archived,exact,grid,out,last)
 report={'version':VERSION,'research_only':True,'live_model_changed':False,
  'frozen_D_unchanged':True,'frozen_D_id':'D-064-gap14',
  'original_gap_gate_not_applied':'Research ranks horses in all cohort races; original selection abstention gap14 gate excluded for paired comparisons',
  'eight_pillars_only':list(PILLARS),'new_extra_pillar_count':0,
  'model_arms':list(MODELS),'pair_combinations':[W_OR,W_G,O_G],
  'full_three_signal_model':ALL,'form_and_debut_common_control':FORM,
  'start_of_or_weight_and_firstrecorded_history':'1988-01-01',
  'frozen_core_lookback_start':'2022-01-01',
  'full_core_lookback_separately_tested_in_stage142':True,
  'source_cutoff':last,'source_date_audit':date_audit,
  'official_grade_recovery_count':len(overlay),
  'grade_audit_counts':grade_audit['counts'],'weight_audit':w_audit['coverage'],
  'first_recorded_audit':d_audit,'pillar_grid_candidates':len(grid),
  'weight_optimisation_objective':'SR and separately capped historical-SP ROI, strictly previous races',
  'betting_prices_used_as_current_race_predictor':False,
  'historical_sp_used_only_for_roi_optimisation_and_settlement':True,
  'weight_objectives':['SR (strike rate)','ROI (capped historical SP, strictly prior races)'],
  'trained_on_prior_calendar_quarters_only':True,
  'hierarchical_race_type_backoff':True,'minimum_train_races_per_group':{
   'race_type':MIN_SUBTYPE,'grade':MIN_GRADED,'discipline':MIN_DISCIPLINE,'broad':MIN_BROAD},
  'model_wins_overall':overall,'folds':folds,'original_2025_frozen_source_proof':proof,
  'cohort_audit':meta,'limits':[
  'Stage143 uses 2022+ original D core history; the separate Stage142 full-1988-core test is not silently mixed with this ablation.',
  '1988+ used for historic weight, OR, last winning mark and first recorded starts; missing runs are not invented.',
  'First observed UK/Irish start is not proven first-ever global debut; career identity may be incomplete.',
  'Recorded weight does not guarantee verification of jockey claims, WFA or penalties.',
  'Beaten column may be preceding-horse margin, not total winner distance; no official pounds-per-length is inferred.',
  'Comments interpreted by Stage137 NLP and explicit signals, not by a generative language model.',
  'Winner and SP known retrospectively, so this is not an untouched prospective 2026 validation.',
  'Historical SP is only a comparison metric and not an executable bookmaker price.',
  'Different model arms share some frozen D pre-existing OR signal; tests only isolate the NEW additions.',
  ]}
 (out/'stage143-summary.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
 print('STAGE143_OVERALL',json.dumps(overall),flush=True)
 return report


def self_test():
 f=np.asarray([.14,.09,.06,.18,.12,.11,.25,.05]);a=candidate_grid(f)
 assert a.shape[1]==8 and a.shape[0]>250 and np.allclose(a.sum(axis=1),1)
 assert np.allclose(masked_grid(a,'CHASE')[:,4],0) and np.allclose(masked_grid(a,'AW')[:,7],0)
 assert all(x in PILLARS for x in ('Ability','Trainer','Jumping'))
 assert len({W_OR,W_G,O_G,ALL})==4 and len(MODELS)==11 and OBJECTIVES==('SR','ROI')
 assert abs(fixed_weight(f,'CHASE').sum()-1)<1e-5
 a=strict_center([np.nan,120,110,100],4,2,15)
 assert a[0]==0 and abs(sum(a))<1e-6
 assert np.all(strict_center([np.nan,np.nan,5],3)==0)
 # Different race types qualify for specialist backoff only on prior sample.
 rx={'kind':'CHASE','broad':'NH','subtype':'CHASE_HANDICAP','official_tier':'UNKNOWN'}
 assert stratify(rx)[0]==('RACE_TYPE','CHASE:CHASE_HANDICAP')
 b={("DISCIPLINE","CHASE"):{'weights':fixed_weight(f,'CHASE'),'training_n':950,'candidate_id':5}}
 w,level,group,_=choose_bank(b,rx,f)
 assert level=='DISCIPLINE' and group=='CHASE' and np.allclose(w[4],0)
 assert min(support_threshold(x) for x in ('RACE_TYPE','DISCIPLINE','GRADE_LEVEL'))>=300
 print('STAGE143 SELFTEST PASS: eight pillars, candidate grid, type-specific backoff, missing neutral')


def main():
 parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test')
 p=sub.add_parser('walkforward');p.add_argument('--db',type=pathlib.Path,required=True)
 p.add_argument('--reference',type=pathlib.Path,required=True)
 p.add_argument('--stage137',type=pathlib.Path,required=True)
 p.add_argument('--out',type=pathlib.Path,required=True)
 p.add_argument('--to-date',default='2026-11-10')
 args=parser.parse_args()
 if args.cmd=='self-test':self_test()
 else:research(args.db,args.reference,args.stage137,args.out,args.to_date)

if __name__=='__main__':main()
