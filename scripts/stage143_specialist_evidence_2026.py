#!/usr/bin/env python3
"""Stage143: research-only specialist-evidence race selection test.

Unlike Stage137/141, Stage143 does NOT simply weight the same eight pillars.
It gates a typed evidence library within each of 59 route definitions; absent
source signals are missing, never simulated. Five discipline-shared classifiers
learn from historical pre-race signal constructions, with an explicit nonlinear
interaction ablation. Frozen D / D(c) / G and all Stage137-142 files unchanged.

Caution: source venue-country identity cannot be independently proven for all
archive rows. Candidate venue/surface quarantine is auditable, not a blanket
source-integrity certification. 2026 is retrospective, not untouched validation.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, json, math, pathlib, re, sys
from collections import Counter, defaultdict
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H
import stage140_d137_hpr_ability_2026 as U
import stage141_grade_weight_2026 as J
import stage142_full_history_core_2026 as T

VERSION='stage143-v1-specialist-evidence-hierarchical'
CONTROL='D137_HPR_ABILITY_OR_WEIGHT'
HISTORIC_CONTROL='D142_LONG_CORE_FORM_OR_WEIGHT'
SPEC='STAGE143_TYPE_EVIDENCE'
INT='STAGE143_TYPE_INTERACTIONS'
MODELS=(CONTROL,HISTORIC_CONTROL,SPEC,INT)
SIG_NAMES=()
FIELD_IDS={s:i for i,s in enumerate(S.FEATURES)}
WEIGHT_IDS={s:i for i,s in enumerate(J.W_FEATURES)}
OR_IDS={s:i for i,s in enumerate(J.OR_FEATURES)}
TRAIN_FROM='2023-01-01';EVAL_FROM='2026-01-01';SOURCE_CUTOFF='2026-11-10'
# Classification for superficially plausible flat events without verified
# synthetic/turf material. Mixed-use courses intentionally do not get assigned.
TURF_ONLY={x.lower() for x in ('Bath','Beverley','Brighton','Chester','Epsom','Goodwood','Hamilton','Newmarket','Nottingham','Pontefract','Redcar','Ripon','Salisbury','Thirsk','Windsor','Yarmouth','York','Curragh','Bellewstown','Ballinrobe','Gowran Park','Killarney','Listowel','Navan','Roscommon','Tipperary','Tramore','Sligo','Down Royal')}
# This is a cautious trigger list, not a complete registry of all homonyms.
HOMONYM_COURSES={'ascot','newcastle','perth','warwick','hamilton'}
UNAVAILABLE={'speed_adjusted','pace_run_style','draw_pace',
 'hunter_pts','cross_country','jockey_claim','restricted_conditions','seller_claimer'}


def val(a,i):
 """Read a genuine pre-race array slot, retaining missingness."""
 try:
  z=float(a[i]);return z if math.isfinite(z) else None
 except (IndexError,TypeError,ValueError,KeyError):return None


def xf(r,horse,k):return val(r['xf'][horse],FIELD_IDS[k]) if k in FIELD_IDS else None

def of(r,horse,k):
 a=r.get('or_features')
 return val(a[horse],OR_IDS[k]) if a is not None and k in OR_IDS else None

def wf(r,horse,k):
 a=r.get('weight_features')
 return val(a[horse],WEIGHT_IDS[k]) if a is not None and k in WEIGHT_IDS else None


def mean(*vs):
 items=[v for v in vs if v is not None]
 return sum(items)/len(items) if items else None


def negate(x):return -x if x is not None else None

def clip(x,b=8):return max(-b,min(b,x)) if x is not None else None


def logistic_interval(v,scale):
 if v is None:return None
 return math.tanh(v/scale)


def positive(x):return max(0,x) if x is not None else None


def pre(r,i,k):
 try:return r['preoff_meta'][i].get(k)
 except (KeyError,IndexError,TypeError):return None


def source_rate(counter,k,min_n=15):
 v=counter.get(k)
 if not v or v[1]<min_n:return None
 # Simple, explicit sample-size shrinkage. This is previous-day evidence,
 # not a pre-trained final probability or a claim of statistical calibration.
 return (v[0]+2)/(v[1]+12)


def attach_preoff_observations(db,races,last):
 """Derive code-specific trainer, jockey, sire, course, going, trip, surface.

 Uses only verified source **previous-calendar-day** starts for each date.
 No same-day winners, future race comments, odds or field results enter features.
 Missing pedigree, exact AW material or unobserved starts remain null.
 """
 from stage108_model_g2 import quality
 wanted={str(r['race_id']):r for r in races}
 trainer=defaultdict(lambda:[0.,0]);jockey=defaultdict(lambda:[0.,0]);sire=defaultdict(lambda:[0.,0]);horse_hist=defaultdict(lambda:collections.deque(maxlen=12));draw_hist=defaultdict(lambda:[0.,0.,0])
 audit=Counter();pending=[];today=None
 def update():
  nonlocal pending
  for k,tk,jk,sk,dk,record in pending:
   horse_hist[k].append(record)
   if dk:
    v=draw_hist[dk];v[0]+=record['won'];v[1]+=record['expected_win'];v[2]+=1
   for bank,ind in ((trainer,tk),(jockey,jk),(sire,sk)):
    if ind and ind[0]:
     bucket=bank[ind];bucket[0]+=record['won'];bucket[1]+=1
  pending=[]
 for entries in S.read_races(db,first='2022-01-01',last=last):
  day=str(entries[0][1]);rid=str(entries[0][0])
  if today and day!=today:update()
  today=day
  tgt=wanted.get(rid)
  starters=[z for z in entries if S.is_starter(z)]
  if len(starters)<2:continue
  raw_surface=str(entries[0][4] or '').lower().strip()
  material=raw_surface if any(x in raw_surface for x in ('tapeta','polytrack','fibresand')) else None
  going=str(entries[0][6] or '').lower().strip()
  dist_bucket=round(float(entries[0][5])/2)*2 if S.fnum(entries[0][5]) is not None else None
  distance=S.fnum(entries[0][5])
  code=str(entries[0][3] or '').upper()
  if 'CHASE' in code or 'STEEPLE' in code:code='CHASE'
  elif 'HURDLE' in code:code='HURDLE'
  elif 'NATIONAL HUNT FLAT' in code or 'NH FLAT' in code or 'BUMPER' in code:code='NH_FLAT'
  else:code='FLAT'
  if code=='FLAT' and (material is not None or raw_surface in ('standard','standard to slow','slow','all weather','aw')):code='AW'
  observations=[]
  for z in starters:
   key=S.ident(z)
   if not key:observations.append(None);continue
   old=list(horse_hist[key]);this_age=S.fnum(z[17]);draw=S.fnum(z[21]);
   dbucket=min(2,int(3*(draw-1)/max(1,len(starters)))) if draw is not None and draw>0 else None
   dkey=(str(entries[0][2]).strip().lower(),dist_bucket,raw_surface,dbucket) if dbucket is not None and dist_bucket is not None and code in ('FLAT','AW') else None
   ds=draw_hist.get(dkey) if dkey else None
   draw_bias=math.log((ds[0]+2)/(ds[1]+2)) if ds and ds[2]>=30 else None
   def histmean(test):
    vals=[x['quality'] for x in old if test(x) and x['quality'] is not None]
    return sum(vals)/len(vals) if len(vals)>=2 else None
   sm=histmean(lambda x: bool(material) and x['material']==material)
   gm=histmean(lambda x: bool(going) and x['going']==going)
   dm=histmean(lambda x: distance is not None and x['distance'] is not None and abs(x['distance']-distance)<=max(1.,distance*.12))
   cm=histmean(lambda x: x['course']==str(entries[0][2]).strip().lower())
   tr=(str(z[14] or '').strip().lower(),code)
   jo=(str(z[15] or '').strip().lower(),code)
   si=(str(z[18] or '').strip().lower(),code)
   observations.append({'age':this_age,'draw':draw,'field_size':len(starters),
     'surface_material':material,'horse_surface_quality':sm,'draw_context_bias':draw_bias,
     'race_going':going,'race_distance_f':distance,
     'horse_going_quality':gm,'horse_distance_quality':dm,'horse_course_quality':cm,
     'trainer_code_rate':source_rate(trainer,tr),
     'jockey_code_rate':source_rate(jockey,jo),
     'sire_code_rate':source_rate(sire,si,25),
     'code':code,'prior_observed_starts':len(old),
     'prior_last_material':old[-1]['material'] if old else None})
   qual=quality({'finish_pos':z[12],'outcome':z[13],'beaten':z[23]},len(starters))
   q=float(qual) if qual is not None and math.isfinite(float(qual)) else None
   record={'date':day,'quality':q,'won':int(S.fnum(z[12])==1),
    'material':material,'going':going,'distance':distance,'course':str(entries[0][2]).strip().lower()}
   record['expected_win']=1/len(starters)
   pending.append((key,tr,jo,si,dkey,record))
  if tgt:
   # Data source and frozen scorer must agree on the exact horse sequence.
   if len(observations)!=len(tgt['horse']) or [S.ident(x) for x in starters]!=tgt['identity']:
    audit['runner_identity_or_order_disagreement']+=1
   else:
    tgt['preoff_meta']=observations
    audit['races_with_preoff_metadata']+=1
    audit['prior_surface_evidence_runners']+=sum(x is not None and x.get('horse_surface_quality') is not None for x in observations)
    audit['prior_trainer_code_rate_runners']+=sum(x is not None and x.get('trainer_code_rate') is not None for x in observations)
    audit['prior_sire_code_rate_runners']+=sum(x is not None and x.get('sire_code_rate') is not None for x in observations)
 update()
 audit['evaluated_target_races']=len(wanted)
 audit['missing_metadata_races']=sum('preoff_meta' not in r for r in races)
 return dict(audit)


def derive(r,i,s):
 """Only backed source inputs. A proposed but unverified feature is NULL.

 Source data is prior-day historical, without contemporary odds or outcomes.
 Every returned quantity has a stable mapping in Stage137/141 input arrays.
 """
 if s in UNAVAILABLE:return None
 if s=='form_adjusted':
  return mean(xf(r,i,'horse_form_quality3'),negate(xf(r,i,'horse_beaten_per_mile3')),
              xf(r,i,'horse_comment_nlp3'))
 if s=='real_ability':return val(r.get('hpr_z',[]),i)
 if s=='class_relative':return mean(negate(xf(r,i,'past_vs_current_tier_delta')),
                                   xf(r,i,'past_grade_strength3'),xf(r,i,'relative_official_rating'))
 if s=='handicap_value':
  return mean(of(r,i,'or_near_winning_mark_good_form'),of(r,i,'or_relative_weight_minus_rating_diff'),
              of(r,i,'or_weight_equiv_vs_field'))
 if s=='winning_mark':
  return mean(of(r,i,'or_near_winning_mark_good_form'),
              of(r,i,'or_at_last_handicap_win_mark'),of(r,i,'or_below_last_handicap_win_mark'))
 if s=='weight_penalty':
  # Truly observed current physical weight and historical delta; never call
  # this a verified winner penalty, rider claim or age allowance.
  return mean(negate(wf(r,i,'weight_today_rel_field')),
              negate(wf(r,i,'weight_delta_last3')))
 if s=='fitness_cycle':return mean(logistic_interval(xf(r,i,'last_run_days'),35),xf(r,i,'horse_form_trend'))
 if s=='progression':return mean(xf(r,i,'horse_form_trend'),xf(r,i,'same_code_form3'))
 if s=='going_fit':return mean(pre(r,i,'horse_going_quality'),xf(r,i,'Ground')) if r.get('broad')!='AW' else None
 if s=='distance_stamina':return mean(pre(r,i,'horse_distance_quality'),xf(r,i,'Distance'),negate(xf(r,i,'horse_beaten_per_mile3')))
 if s=='surface_affinity':return pre(r,i,'horse_surface_quality') if r.get('broad')=='AW' else None
 if s=='draw_context_bias':return pre(r,i,'draw_context_bias') if r.get('broad') in ('AW','FLAT','FLAT_UNKNOWN') else None
 if s=='course_profile':return mean(pre(r,i,'horse_course_quality'),xf(r,i,'Course'))
 if s in ('jockey_conditional','jockey_steeple','jockey_flat'):
  return mean(pre(r,i,'jockey_code_rate'),xf(r,i,'Jockey'))
 if s in ('stable_profile','stable_progression'):
  return mean(pre(r,i,'trainer_code_rate'),xf(r,i,'Trainer'))
 if s in ('pedigree_flat','pedigree_jumps'):
  return pre(r,i,'sire_code_rate')
 if s=='juvenile_maturity':
  age=pre(r,i,'age')
  return mean(xf(r,i,'horse_form_trend'),logistic_interval(pre(r,i,'prior_observed_starts'),3)) if age is not None and age<=4 else None
 if s=='veteran':
  age=pre(r,i,'age')
  return mean(xf(r,i,'horse_form_trend'),negate(logistic_interval(xf(r,i,'last_run_days'),80))) if age is not None and age>=10 else None
 if s=='jumping_quality':
  return mean(xf(r,i,'Jumping'),xf(r,i,'horse_jumping_comment3'),
              negate(xf(r,i,'horse_interference3')))
 if s=='jumping_transition':return mean(xf(r,i,'same_code_form3'),xf(r,i,'horse_jumping_comment3'),
                                        negate(xf(r,i,'beginner_to_handicap')))
 if s=='field_opposition':return mean(xf(r,i,'prior_rivals_nextout_quality3'),
                                      xf(r,i,'prior_rivals_nextout_winrate3'))
 if s=='bumper_progression':return mean(xf(r,i,'same_code_form3'),xf(r,i,'horse_form_trend'))
 if s=='older_horse_cycle':return mean(xf(r,i,'horse_form_trend'),
                                      negate(logistic_interval(xf(r,i,'last_run_days'),70)))
 if s=='classified_net':return of(r,i,'or_relative_weight_minus_rating_diff')
 if s=='graded_quality':return mean(xf(r,i,'past_grade_strength3'),
                                    negate(xf(r,i,'past_vs_current_tier_delta')),
                                    xf(r,i,'past_opposition_median3'))
 return None


def classified(r,overlays,config):
 """Parse orthogonal class, eligibility and surface; return route or reason."""
 name=r.get('race_name') or ''
 overlay=overlays.get(str(r['race_id']))
 if overlay:name=overlay['enriched_title']
 lower=name.lower();tier=(overlay or {}).get('verified_tier') or r.get('official_tier') or 'UNKNOWN'
 disc=r['kind'];broad=r.get('broad')
 course=str(r['course']).strip().lower();date=r['date']
 if disc in ('AW','FLAT'):
  if broad=='AW':family='AW';surface='EXPLICIT_AW_CLASSIFIER'
  elif broad=='FLAT':family='TURF';surface='EXPLICIT_TURF_CLASSIFIER'
  elif broad=='FLAT_UNKNOWN' and course in TURF_ONLY:
   family='TURF';surface='TURF_ONLY_COURSE_INFERENCE'
  else:return None,'UNRESOLVED_FLAT_SURFACE'
 else:family=disc;surface='NOT_APPLICABLE'
 # Same-name foreign courses require independent source identity. Until
 # independently matched, quarantine homonymous Flat events rather than
 # contaminating specialists with unverifiable region assignments.
 if family in ('TURF','AW') and course in ('ascot','hamilton'):
  return None,'HOMONYM_FLAT_COURSE_JURISDICTION_UNVERIFIED'
 if family=='TURF' and course in ('newcastle','perth','warwick'):
  return None,'GEOGRAPHICALLY_INCOMPATIBLE_UK_FLAT_SURFACE'
 flags={
  'handicap':bool(re.search(r'\bhandicap\b|\bnursery\b',lower)) or str(r.get('subtype','')).endswith('HANDICAP'),
  'maiden':bool(re.search(r'\bmaiden\b',lower)),
  'novice':bool(re.search(r'\bnovice\b',lower)),
  'beginner':bool(re.search(r'\bbeginners?\b',lower)),
  'juvenile':bool(re.search(r'\bjuvenile\b',lower)),
  'graded':tier.startswith(('NH_GRADE_','FLAT_GROUP_')),
  'listed':tier.endswith('LISTED') or bool(re.search(r'\blisted\b',lower)),
  'rated':bool(re.search(r'\brated\b',lower)),
  'seller':bool(re.search(r'\bsell(?:ing|er)\b|\bclaim(?:ing|er)\b',lower)),
  'restricted':bool(re.search(r'\bauction\b|\brestricted\b|\bsales?\b',lower)),
 }
 group_match=re.search(r'(?:FLAT_GROUP_|NH_GRADE_)([123])',tier)
 if group_match:grade=group_match.group(1)
 else:
  m=re.search(r'\b(?:group|grade|grp|gr)\s*[- :]*([123])\b',lower)
  grade=m.group(1) if m and ((family in ('AW','TURF') and 'group' in m.group(0)) or family in ('HURDLE','CHASE')) else None
 # For missing exact grade, Class 1 must never be interpreted as G1.
 if family in ('TURF','AW'):
  if flags['handicap']:
   typ='NURSERY' if 'nursery' in lower else 'HANDICAP'
  elif grade:typ=('GROUP_'+grade) if family=='TURF' else 'LISTED_PATTERN'
  elif flags['listed']:typ='LISTED' if family=='TURF' else 'LISTED_PATTERN'
  elif flags['seller']:typ='SELLING_CLAIMING'
  elif flags['restricted']:typ='AUCTION_RESTRICTED_SALES'
  elif 'classified' in lower:typ='CLASSIFIED'
  elif flags['maiden']:typ='MAIDEN'
  elif flags['novice']:typ='NOVICE'
  elif 'conditions' in lower or 'allowance' in lower:typ='CONDITIONS'
  else:typ='OTHER_OPEN'
 elif family=='HURDLE':
  if grade:typ='GRADE_'+grade
  elif flags['listed']:typ='LISTED'
  elif flags['handicap']:typ='NOVICE_HANDICAP' if flags['novice'] else 'HANDICAP'
  elif flags['juvenile']:typ='JUVENILE'
  elif flags['seller']:typ='CLAIMING_SELLING'
  elif 'academy' in lower or 'introductory' in lower:typ='ACADEMY_INTRODUCTORY'
  elif flags['maiden']:typ='MAIDEN'
  elif flags['novice']:typ='NOVICE'
  elif flags['rated']:typ='RATED'
  elif 'conditions' in lower:typ='CONDITIONS'
  else:typ='OTHER_OPEN'
 elif family=='CHASE':
  if grade:typ='GRADE_'+grade
  elif flags['listed']:typ='LISTED'
  elif flags['handicap']:typ='NOVICE_HANDICAP' if flags['novice'] else 'HANDICAP'
  elif 'cross country' in lower or 'cross-country' in lower:typ='CROSS_COUNTRY'
  elif flags['seller']:typ='CLAIMING_SELLING'
  elif 'hunter' in lower:typ='HUNTER'
  elif 'veterans' in lower or 'veteran' in lower:typ='VETERANS'
  elif flags['beginner']:typ='RATED_BEGINNERS' if flags['rated'] else 'BEGINNERS'
  elif 'graduation' in lower:typ='GRADUATION'
  elif flags['maiden']:typ='MAIDEN'
  elif flags['novice']:typ='NOVICE'
  elif flags['rated'] or 'conditions' in lower:typ='CONDITIONS_RATED'
  else:typ='OTHER_OPEN'
 else:
  if flags['maiden']:typ='MAIDEN'
  elif 'winners of one' in lower or 'winners of 1' in lower:typ='WINNERS_ONE'
  elif 'winners of two' in lower or 'winners of 2' in lower:typ='WINNERS_TWO'
  elif flags['graded'] or flags['listed']:typ='PATTERN'
  else:typ='OPEN'
 route=family+'_'+typ
 ids={x['race_type_id'] for x in config['full_type_catalog']}
 if route not in ids:return None,'ROUTE_NOT_IN_59_CATALOG:'+route
 return {'route':route,'family':family,'surface_evidence':surface,'flags':flags,
         'race_name':name,'tier':tier,'homonym_course_name':course in HOMONYM_COURSES},None


def build_matrix(records,routing,config,nonlinear=False):
 """Per-race typed evidence. Each library signal has an explicit missing flag."""
 names=list(config['feature_library'])
 ncols=len(names)
 # Five stable cross-signal interactions; do not fabricate missing pace/speed.
 inter=('mark_and_form','jumping_and_ground','trainer_and_progression',
        'class_and_weight','hpr_and_competition') if nonlinear else ()
 X=[];meta=[];labels=[];audit=Counter()
 for r in records:
  rid=str(r['race_id']);info=routing.get(rid)
  if info is None:continue
  route=next(z for z in config['full_type_catalog'] if z['race_type_id']==info['route'])
  active=set(route['candidate_signals_prioritized_not_fitted'])
  # Orthogonal modifiers: a graded novice or graded handicap is NOT judged
  # with graded performance only. Dynamic flags are applied inside each
  # evidence gate; unverified conditions are never silently inferred.
  flags=info['flags']
  if flags['handicap']:active.update(('handicap_value','winning_mark','weight_penalty'))
  if flags['novice']:active.update(('progression','stable_progression'))
  if flags['beginner']:active.update(('jumping_transition','jumping_quality'))
  if flags['juvenile']:active.update(('juvenile_maturity','progression'))
  if flags['maiden']:active.update(('stable_profile','jockey_flat' if info['family'] in ('TURF','AW') else 'jockey_conditional'))
  if flags['graded']:active.update(('graded_quality','class_relative','weight_penalty'))
  for i,h in enumerate(r['horse']):
   age=pre(r,i,'age');trip=pre(r,i,'race_distance_f');going=str(pre(r,i,'race_going') or '')
   active_runner=set(active)
   if age is not None and age<=3 and info['family'] in ('TURF','AW'):active_runner.add('juvenile_maturity')
   if trip is not None and trip>=14:active_runner.add('distance_stamina')
   if any(x in going for x in ('soft','heavy','yielding')) and info['family'] in ('TURF','HURDLE','CHASE','NH_FLAT'):active_runner.add('going_fit')
   if info['family']=='AW' and pre(r,i,'prior_last_material') not in (None,pre(r,i,'surface_material')):active_runner.add('surface_affinity')
   vals={k:derive(r,i,k) if k in active_runner else None for k in names}
   # Zero for not-selected/unknown is a neutral contribution; a distinct
   # observed indicator is appended, so null is not treated as bad form.
   nums=[clip(vals[k]) if vals[k] is not None else 0. for k in names]
   obs=[int(vals[k] is not None) for k in names]
   audit['runner_rows']+=1
   for k in active_runner:
    audit['used_'+k]+=int(vals[k] is not None)
    audit['missing_'+k]+=int(vals[k] is None)
   if nonlinear:
    def mul(a,b):
     x,y=vals.get(a),vals.get(b)
     return clip(x*y) if x is not None and y is not None else 0.
    nums.extend([mul('winning_mark','form_adjusted'),
                 mul('jumping_quality','going_fit') if info['family'] in ('CHASE','HURDLE') else 0.,
                 mul('stable_profile','progression'),mul('graded_quality','weight_penalty'),
                 mul('real_ability','field_opposition')])
    obs.extend([int(vals.get('winning_mark') is not None and vals.get('form_adjusted') is not None),
      int(info['family'] in ('CHASE','HURDLE') and vals.get('jumping_quality') is not None and vals.get('going_fit') is not None),
      int(vals.get('stable_profile') is not None and vals.get('progression') is not None),
      int(vals.get('graded_quality') is not None and vals.get('weight_penalty') is not None),
      int(vals.get('real_ability') is not None and vals.get('field_opposition') is not None)])
   # Additional cross-context gates, not a global eight-pillar reweight.
   X.append(nums+obs)
   labels.append(int(i==r['winner_idx']))
   meta.append((rid,i))
 return np.asarray(X,dtype=np.float32),np.asarray(labels,dtype=np.int8),meta,audit,names+list(inter)


def fit_bank(races,routing,config,nonlinear):
 """Pooled discipline-shared learning; 59 route-specific feature gates."""
 X,y,meta,audit,names=build_matrix(races,routing,config,nonlinear)
 if len(X)<2000:raise RuntimeError('Stage143 insufficient training rows '+str(len(X)))
 groups={r['race_id']:routing[str(r['race_id'])]['family'] for r in races if str(r['race_id']) in routing}
 ids=defaultdict(list)
 for i,(rid,horse) in enumerate(meta):ids[groups[rid]].append(i)
 models={}
 # Whole-cohort fallback for any genuinely under-observed discipline.
 fallback_scaler=StandardScaler();fbx=fallback_scaler.fit_transform(X)
 fallback=LogisticRegression(solver='lbfgs',C=0.15,max_iter=160,tol=0.0005)
 fallback.fit(fbx,y);models['__GLOBAL__']=(fallback_scaler,fallback)
 for family,indexes in ids.items():
  sub=X[indexes];lab=y[indexes]
  if len(indexes)<500 or int(np.sum(lab,dtype=np.int64))<60:continue
  scaler=StandardScaler()
  x=scaler.fit_transform(sub)
  model=LogisticRegression(solver='lbfgs',C=0.2,max_iter=160,tol=0.0005)
  model.fit(x,lab)
  models[family]=(scaler,model)
 if not models:raise RuntimeError('No fitted discipline banks')
 return models,{'trained_horses':len(X),'trained_winners':int(np.sum(y,dtype=np.int64)),
                'discipline_runners':{k:len(v) for k,v in ids.items()},
                'fitted_disciplines':sorted(models),'columns':X.shape[1]}


def score_races(races,routing,config,models,nonlinear):
 X,y,meta,_,_=build_matrix(races,routing,config,nonlinear)
 groups={str(r['race_id']):routing[str(r['race_id'])]['family'] for r in races if str(r['race_id']) in routing}
 score=defaultdict(dict)
 ids=defaultdict(list)
 for k,(rid,i) in enumerate(meta):ids[groups[rid]].append(k)
 for family,ix in ids.items():
  scale,model=models.get(family,models['__GLOBAL__'])
  p=model.predict_proba(scale.transform(X[ix]))[:,1]
  for j,k in enumerate(ix):
   rid,i=meta[k];score[rid][i]=float(p[j])
 output={}
 for r in races:
  rid=str(r['race_id']);observed=score.get(rid)
  if not observed or len(observed)!=len(r['horse']):continue
  raw=np.array([observed[i] for i in range(len(r['horse']))],dtype=float)
  # Conditional winner probabilities; enforce one winner per race.
  p=np.maximum(raw,1e-8)/max(1e-8,float(raw.sum()))
  output[rid]=p
 return output


def load_controls(path,models):
 data=defaultdict(dict)
 with gzip.open(path,'rt',newline='',encoding='utf8') as f:
  for z in csv.DictReader(f):
   if z['model'] in models:
    mid=str(z['race_id']);name=z['model']
    if mid in data[name]:raise RuntimeError('Duplicate historic reference '+name+' '+mid)
    data[name][mid]={'date':z['date'],'race_id':mid,'course':z['course'] if 'course' in z else z.get('meeting'),
      'rank1_horse':z['rank1_horse'],'rank2_horse':z['rank2_horse'],
      'rank1_won':int(z['rank1_won']),'rank2_won':int(z['rank2_won']),
      'sp':T.number(z['sp']),'source':'IMMUTABLE_ARCHIVE'}
 return data


def metric(rows):
 n=len(rows);wins=sum(r['rank1_won'] for r in rows)
 priced=[z for z in rows if z['sp'] is not None and 1<z['sp']<1001]
 return {'races':n,'wins':wins,'sr_pct':round(100*wins/n,3) if n else None,
  'rank1_or2_wins':sum(z['rank1_won']+z['rank2_won'] for z in rows),
  'priced':len(priced),'sp_roi_pct':round(100*(sum(z['sp'] for z in priced if z['rank1_won'])/len(priced)-1),3) if priced else None}


def summarize(rows):
 groups=defaultdict(list)
 for z in rows:
  for typ,k in [('ALL','ALL'),('QUARTER',z['quarter']),('DISCIPLINE',z['family']),
      ('RACE_TYPE',z['route']),('COUNTRY',z['country']),('TIER',z['tier']),('MONTH',z['date'][:7]),
      ('FIELD_SIZE',('SMALL' if z['field_size']<=7 else 'LARGE' if z['field_size']>=14 else 'MEDIUM')),
      ('SURFACE_PROVENANCE',z['source_surface'])]:
   groups[(z['model'],typ,k)].append(z)
 return [{'model':m,'breakdown':typ,'group':group,**metric(zz)} for (m,typ,group),zz in sorted(groups.items())]


def write_csv(path,rows):
 if not rows:return
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fields=[];seen=set()
 for r in rows:
  for k in r:
   if k not in seen:fields.append(k);seen.add(k)
 with (gzip.open(path,'wt',newline='',encoding='utf8') if str(path).endswith('.gz') else path.open('w',newline='',encoding='utf8')) as f:
  w=csv.DictWriter(f,fieldnames=fields,restval='');w.writeheader();w.writerows(rows)


def research(db,ref,source137,source141,source142,config,out,last):
 config=json.loads(pathlib.Path(config).read_text());out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 assert config['type_count']==59 and len(config['full_type_catalog'])==59
 assert len(config['orthogonal_modifiers'])==21 and len(config['original_pillars'])==8
 import stage30_gap_aware_abcd as D
 original=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
 if original['chosen_combination']!='D-064-gap14':raise RuntimeError('Frozen D identity mismatch')
 exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
 if not np.allclose([original['weights_pct'][p] for p in S.PILLARS],exact*100,atol=.011):raise RuntimeError('D pillar drift')
 S.FROZEN[:]=exact;S.CANDIDATES=S.candidate_weights()
 last,source_audit=U.audit_limit(db,last)
 archive,control_metadata=T.read_stage141_archive(source141,last)
 overlays,grade_audit=J.official_grade_overlay(db,ref,out,last)
 base,baseaudit,refaudit=J.construct_2026(db,ref,last)
 T.compare_actual(archive,base)
 state,form,warmaudit=T.warmup(db,ref,last)
 long,longaudit,_=T.reconstruct_full_core(db,ref,last,state,form)
 T.align(base,long)
 del state,form
 target={str(i) for r in base for i in r['identity'] if i}
 prior_end=H.END
 try:
  H.END=last;hpr_data,hpraudit=H.hpr_history(db,ref,out,target)
 finally:H.END=prior_end
 coverage=Counter();H.attach_hpr(base,hpr_data,coverage)
 weightaudit=J.weight_context(db,ref,base,out,last)
 routing={};qa=Counter();quarantine=[];routecounts=Counter()
 longmap={str(r['race_id']):r for r in long}
 for r in base:
  info,reason=classified(r,overlays,config)
  if reason:
   qa[reason]+=1
   if len(quarantine)<2500:quarantine.append({'date':r['date'],'race_id':r['race_id'],
       'course':r['course'],'race_name':r['race_name'],'reason':reason})
  else:
   routing[str(r['race_id'])]=info;routecounts[info['route']]+=1
 qa['total_races']=len(base);qa['routed_races']=len(routing)
 metaaudit=attach_preoff_observations(db,base,last)
 if metaaudit['races_with_preoff_metadata']<len(base)*.90:
  raise RuntimeError('Stage143 preoff metadata join coverage too low '+str(metaaudit))
 # Preserve 1988+ original pillar/form state from Stage142 and the EXACT same
 # Stage141 HPR + OR/weight contexts and matched 2026 field/winner identities.
 enhanced=[]
 for r in base:
  rid=str(r['race_id'])
  if rid not in routing:continue
  rr=T.variant(r,longmap[rid],core=True,form=True,weights=False)
  enhanced.append(rr)
 del long,longmap
 in2026=[r for r in enhanced if EVAL_FROM<=r['date']<=last]
 if len(in2026)<4500:raise RuntimeError('Too few routed 2026 source races: '+str(len(in2026)))
 if len(routecounts)<18:raise RuntimeError('Too few distinct routed types '+str(len(routecounts)))
 archived142=load_controls(pathlib.Path(source142)/'stage142-2026-rank1-rank2.csv.gz',{HISTORIC_CONTROL})
 archived141={str(z['race_id']):z for z in archive if z['model']==CONTROL}
 allrows=[];folds=[];validation=[]
 def evalset(test,models,modelid,source):
  pred=score_races(test,routing,config,models,modelid==INT)
  for r in test:
   rid=str(r['race_id']);p=pred.get(rid)
   if p is None:continue
   order=np.argsort(-p,kind='stable');i,j=map(int,order[:2]);sp=T.number(r['sp'][i])
   info=routing[rid]
   allrows.append({'quarter':r['date'][:4]+'Q'+str((int(r['date'][5:7])-1)//3+1),
      'date':r['date'],'race_id':rid,'course':r['course'],'race_name':info['race_name'],'country':r['country'],
      'family':info['family'],'route':info['route'],'tier':info['tier'],
      'source_surface':info['surface_evidence'],'model':modelid,'rank1_horse':r['horse'][i],
      'rank2_horse':r['horse'][j],'rank1_won':int(i==r['winner_idx']),
      'rank2_won':int(j==r['winner_idx']),'sp':sp,'winner':r['horse'][r['winner_idx']],
      'field_size':len(r['horse']),'rank1_p':round(float(p[i]),6),'rank2_p':round(float(p[j]),6),
      'winning_horse_p':round(float(p[r['winner_idx']]),6),
      'brier_sum':round(float(np.square(p-r['win']).sum()),6),
      'source':source})
  return len(pred)
 # Preregistered temporal holdout 2025, never fit on 2026 before this check.
 pre2025=[r for r in enhanced if TRAIN_FROM<=r['date']<='2024-12-31']
 val2025=[r for r in enhanced if '2025-01-01'<=r['date']<='2025-12-31']
 for modelid in (SPEC,INT):
  bank,fit=fit_bank(pre2025,routing,config,nonlinear=modelid==INT)
  output=score_races(val2025,routing,config,bank,nonlinear=modelid==INT)
  good=[r for r in val2025 if str(r['race_id']) in output]
  validation.append({'model':modelid,'train_through':'2024-12-31','validated_2025_races':len(good),
   'wins':sum(int(np.argmax(output[str(r['race_id'])])==r['winner_idx']) for r in good),
   'mean_winner_logloss':round(float(np.mean([-math.log(max(1e-9,output[str(r['race_id'])][r['winner_idx']])) for r in good])),5) if good else None,
   'mean_race_brier':round(float(np.mean([np.square(output[str(r['race_id'])]-r['win']).sum() for r in good])),5) if good else None})
  if len(good)<1000:raise RuntimeError('Too little true 2025 validation '+modelid)
  print('STAGE143_2025_VALIDATION',validation[-1],flush=True)
 for q,start,end in U.SPLITS:
  if start>last:break
  end=min(end,last)
  train=[r for r in enhanced if TRAIN_FROM<=r['date']<start]
  test=[r for r in in2026 if start<=r['date']<=end]
  if len(test)<100:continue
  for modelid in (SPEC,INT):
   bank,fit=fit_bank(train,routing,config,modelid==INT)
   n=evalset(test,bank,modelid,'STAGE143_NEW_OUT_OF_QUARTER_FIT')
   if n!=len(test):raise RuntimeError('Incomplete full-field predictions '+q+' '+modelid+f' {n}/{len(test)}')
   print('STAGE143_QUARTER',q,modelid,n,fit,flush=True)
  folds.append({'quarter':q,'train_through':str(dt.date.fromisoformat(start)-dt.timedelta(days=1)),
                'train_races':len(train),'test_races':len(test)})
 # Immutable archive controls are filtered to exactly the new QA cohort.
 for r in in2026:
  rid=str(r['race_id']);info=routing[rid];q=r['date'][:4]+'Q'+str((int(r['date'][5:7])-1)//3+1)
  for modelid,z in [(CONTROL,archived141.get(rid)),
                    (HISTORIC_CONTROL,archived142[HISTORIC_CONTROL].get(rid))]:
   if z is None:raise RuntimeError('Missing immutable control '+modelid+' '+rid)
   if z['rank1_horse'] not in r['horse'] or z['rank2_horse'] not in r['horse']:
    raise RuntimeError('Unmatched control horse '+rid)
   win=r['horse'][r['winner_idx']]
   if z['rank1_won']!=int(z['rank1_horse']==win):raise RuntimeError('Original control winner drift '+rid)
   allrows.append({'quarter':q,'date':r['date'],'race_id':rid,'course':r['course'],'race_name':info['race_name'],'country':r['country'],
    'family':info['family'],'route':info['route'],'tier':info['tier'],
    'source_surface':info['surface_evidence'],'model':modelid,
    'rank1_horse':z['rank1_horse'],'rank2_horse':z['rank2_horse'],
    'rank1_won':z['rank1_won'],'rank2_won':z['rank2_won'],
    'sp':z['sp'],'winner':win,'field_size':len(r['horse']),'source':'FROZEN_ARCHIVE_UNCHANGED'})
 # Assert exact same race list across all arms. No survivor selection.
 cohorts={m:{z['race_id'] for z in allrows if z['model']==m} for m in MODELS}
 if len({frozenset(x) for x in cohorts.values()})!=1:
  raise RuntimeError('Model cohort mismatch '+str({m:len(x) for m,x in cohorts.items()}))
 sums=summarize(allrows)
 overall={m:next(z for z in sums if z['model']==m and z['breakdown']=='ALL') for m in MODELS}
 signal_usage=Counter()
 for r in enhanced:
  if r['date']<'2026-01-01':continue
  info=routing[str(r['race_id'])];route=next(z for z in config['full_type_catalog'] if z['race_type_id']==info['route'])
  for s in route['candidate_signals_prioritized_not_fitted']:
   for i in range(len(r['horse'])):signal_usage[(s,'known' if derive(r,i,s) is not None else 'missing')]+=1
 usage=[{'signal':s,'known_runners':signal_usage[s,'known'],'missing_runners':signal_usage[s,'missing']} for s in config['feature_library']]
 write_csv(out/'stage143-2026-rank1-rank2.csv.gz',allrows)
 write_csv(out/'stage143-2026-discipline-racetype.csv',sums)
 write_csv(out/'stage143-quarantine-review.csv',quarantine)
 write_csv(out/'stage143-feature-coverage.csv',usage)
 report={'version':VERSION,'status':'RESEARCH_ONLY_NOT_LIVE','source_cutoff':last,
  'original_D_stage137_stage141_stage142_live_and_G_models_unchanged':True,
  'source_cohort':len(base),'routed_test_2026':len(in2026),'quarantined_or_unknown_test_2026':len([r for r in base if r['date']>='2026-01-01' and str(r['race_id']) not in routing]),
  'routing_qa':dict(qa),'routing_2022plus':dict(routecounts),'2025_temporal_validation':validation,
  'model_folds_2026':folds,'overall':overall,'historical_warmup':warmaudit,
  'source_audit':source_audit,'2025_archived_controls_provenance':control_metadata.get('version'),
  'grade_audit':grade_audit,'hpr_audit':hpraudit,'weight_audit':weightaudit,'preoff_observation_audit':metaaudit,
  'feature_coverage':usage,'known_unimplemented_signals':sorted(UNAVAILABLE),
  'limitations':['A 59-route catalog does not mean 59 independent models: discipline pooled model with route-specific evidence gates.',
     'Exact pre-race source timestamps are not independently established for all upstream history.',
     'Foreign same-name track contamination has a targeted quarantine, not a full external jurisdiction reconciliation.',
     'Turf-only course inference is labelled; uncertain mixed-venue Flat surface is excluded.',
     'Pace/draw intersection, reliable speed, verified penalties/claims and point-to-point unavailable: excluded, not filled. Surface-specific, sire and code-specific trainer stats require observed previous-day evidence and shrinkage.',
     'All 2026 tests are post-hoc research despite chronological training. 2025 was also viewed in earlier model studies.',
     'Historical SP ROI applies only to separately priced selected horses; not actual bookmaker-time prices.',
     'No wager or live app recommendation is activated.']}
 (out/'stage143-summary.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
 print('STAGE143_ACCEPTED',json.dumps({'overall':overall,'qa':dict(qa),'validation':validation}),flush=True)
 return report


def self_test(config):
 config=json.loads(pathlib.Path(config).read_text())
 assert config['type_count']==59 and len(config['full_type_catalog'])==59
 assert len(config['orthogonal_modifiers'])==21
 assert set(config['original_pillars'])==set(S.PILLARS)
 assert all(len(x['candidate_signals_prioritized_not_fitted'])>=4 for x in config['full_type_catalog'])
 assert set(k for a in config['full_type_catalog'] for k in a['candidate_signals_prioritized_not_fitted'])<=set(config['feature_library'])
 def r(name,disc,broad,tier='UNKNOWN',date='2026-04-01',course='Newmarket',subtype=''):
  return {'race_id':name,'date':date,'race_name':name,'course':course,
          'kind':disc,'broad':broad,'official_tier':tier,'subtype':subtype}
 for row,expected in [
  (r('Novice Handicap Chase','CHASE','NH'),'CHASE_NOVICE_HANDICAP'),
  (r('Grade 1 Novice Chase','CHASE','NH','NH_GRADE_1'),'CHASE_GRADE_1'),
  (r('Class 6 Nursery','FLAT','FLAT'),'TURF_NURSERY'),
  (r('INH Flat Race winners of one','NH_FLAT','NH'),'NH_FLAT_WINNERS_ONE'),
  (r('Class 4 Maiden Hurdle','HURDLE','NH'),'HURDLE_MAIDEN'),
  (r('Group 1 Flat race','FLAT','FLAT','FLAT_GROUP_1'),'TURF_GROUP_1'),
 ]:
  got,err=classified(row,{},config)
  assert err is None and got['route']==expected,(row,got,err)
 bad,reason=classified(r('Australian Ascot Flat','FLAT','FLAT',date='2026-01-01',course='Ascot'),{},config)
 assert bad is None and reason=='HOMONYM_FLAT_COURSE_JURISDICTION_UNVERIFIED'
 unknown,reason=classified(r('Unknown mixed track','FLAT','FLAT_UNKNOWN',course='Lingfield'),{},config)
 assert unknown is None and reason=='UNRESOLVED_FLAT_SURFACE'
 assert derive({'xf':np.zeros((1,len(S.FEATURES)))},0,'speed_adjusted') is None
 assert derive({'xf':np.zeros((1,len(S.FEATURES)))},0,'draw_pace') is None
 print('STAGE143_SELFTEST_OK routes=59 flags=21 no inferred speed/pace/surface, frozen sources read-only')


def main():
 p=argparse.ArgumentParser()
 p.add_argument('action',choices=['self-test','walkforward'])
 p.add_argument('--db',type=pathlib.Path);p.add_argument('--reference',type=pathlib.Path)
 p.add_argument('--stage137',type=pathlib.Path);p.add_argument('--stage141',type=pathlib.Path)
 p.add_argument('--stage142',type=pathlib.Path)
 p.add_argument('--config',type=pathlib.Path,default=pathlib.Path('scripts/stage143_race_type_specialist_config.json'))
 p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage143/research'))
 p.add_argument('--to-date',default=SOURCE_CUTOFF)
 a=p.parse_args()
 if a.action=='self-test':self_test(a.config);return
 for k in (a.db,a.reference,a.stage137,a.stage141,a.stage142):
  if k is None:raise SystemExit('Missing required immutable source '+str(k))
 research(a.db,a.reference,a.stage137,a.stage141,a.stage142,a.config,a.out,a.to_date)
if __name__=='__main__':main()
