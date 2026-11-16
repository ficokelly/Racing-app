#!/usr/bin/env python3
"""Stage141 — research-only EXACT pattern-tier recovery and weight-aware 2026 model test.

Reuses Stage140 original D137+HPR Ability (NOT live) and compares it with
isolated grade, weight, and grade+weight enhancements. Original source/weights,
prior model predictions and historical database remain read only. Pattern tiers
are recovered from Stage65P's exact-ID source fields, not inferred from Class 1.
Weight history is generated strictly in prior calendar date order. Missing
jockey claims, WFA, overall beaten margin and horse runs remain unknown.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, json, math, pathlib, re, sqlite3, sys
from collections import defaultdict
import numpy as np
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H
import stage140_d137_hpr_ability_2026 as U
import stage134c_full_field_hpr_replay as C
import stage108_model_g2 as G
import race_strength_lookup as R

VERSION='stage141-2026-grade-or-weight-winning-mark-v2.1-missing-or-guard'
BASE='D137_HPR_ABILITY_UNCHANGED'
GRADE='D137_HPR_ABILITY_GRADE'
OR_ONLY='D137_HPR_ABILITY_OR'
WEIGHT='D137_HPR_ABILITY_WEIGHT'
GRADE_WEIGHT='D137_HPR_ABILITY_GRADE_WEIGHT'
OR_WEIGHT='D137_HPR_ABILITY_OR_WEIGHT'
ALL='D137_HPR_ABILITY_GRADE_OR_WEIGHT'
MODELS=(BASE,GRADE,OR_ONLY,WEIGHT,GRADE_WEIGHT,OR_WEIGHT,ALL)
W_FEATURES=(
 'weight_today_rel_field','weight_today_rel_top','weight_today_top_indicator',
 'weight_delta_last','weight_delta_last3','weight_delta_best_form','weight_delta_minus_or_delta',
 'weight_last3_rel_field','weight_last3_rel_winner','weight_last3_weighted_form',
 'weight_last3_carried','weight_same_code_prior_count','weight_own_highweight_form',
 'weight_own_lowweight_form','weight_prior_class_known','weight_today_known',
 'weight_vs_last_handicap_winning_weight',
)
OR_FEATURES=(
 'or_vs_field_mean','or_vs_highest','or_weight_equiv_vs_field',
 'or_weight_equiv_vs_best','or_weight_equiv_vs_worst',
 'or_known_field_fraction','or_weight_equiv_mean_known',
 'or_change_last','or_change_last3_mean','or_peak_since_last_handicap_win',
 'or_drop_from_peak_since_win','or_last_handicap_win_mark',
 'or_change_from_last_handicap_win_mark','or_at_last_handicap_win_mark',
 'or_below_last_handicap_win_mark','or_days_since_last_handicap_win',
 'or_runs_since_last_handicap_win','or_same_code_handicap_wins',
 'or_recent_form_quality3','or_recent_form_quality5','or_near_winning_mark_good_form',
 'or_last_winning_mark_known','or_relative_weight_minus_rating_diff',
)
GRADE_PATTERN=re.compile(r'(?i)^\s*(?:\(?\s*)?(GRADE|GROUP|GRP|GRADED|GR|G)\s*[-:._ ]?\s*(1|2|3|I|II|III)\s*(?:\)?)\s*$')
TITLE_GRADE=re.compile(r'\b(?:grade|group|grp|gr)\s*[-:._ ]?\s*(1|2|3|I|II|III)\b',re.I)


def number(v):
 try:
  f=float(v)
  return f if math.isfinite(f) else None
 except (ValueError,TypeError):return None

def avg(values):
 a=[float(z) for z in values if z is not None and math.isfinite(float(z))]
 return sum(a)/len(a) if a else None

def grade_from_pattern(pattern,discipline):
 """Only explicit grade labels; 'Class 1', numeric '1' and ambiguous fields NEVER treated as Grade 1."""
 z=str(pattern or '').strip()
 m=GRADE_PATTERN.fullmatch(z)
 if not m:return None
 typ,num=m.groups();num={'I':1,'II':2,'III':3}.get(num.upper()) or int(num)
 typ=typ.upper()
 is_nh=discipline in ('HURDLE','CHASE','NH_FLAT')
 is_flat=discipline in ('FLAT','AW')
 if typ in ('GROUP','GRP') and not is_flat:return None
 if typ in ('GRADE','GRADED') and not is_nh:return None
 if not is_nh and not is_flat:return None
 return ('Grade ' if is_nh else 'Group ')+str(num)


def official_grade_overlay(db,reference,out,last):
 """Exact join: Stage2 race ID + source date/course + Stage4 reference date/course.

    For any source conflict, leave unchanged and write to the review queue.
    This does not modify historical tables or the canonical reference mapping.
 """
 ref,audit=G.load_reference(reference,first='1988-01-01',last=last)
 cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 cx.row_factory=sqlite3.Row
 needed={'stage65p_race_details','historical_races'}
 available={row[0] for row in cx.execute('SELECT name FROM sqlite_master WHERE type="table"')}
 if not needed<=available:raise RuntimeError('Source pattern column not available; cannot recover safely')
 details={};counts=collections.Counter();conflicts=[];samples=[];patterns=collections.Counter();yearly=collections.Counter()
 sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.race_class,
               d.race_name,d.pattern,d.source_file
          FROM historical_races r JOIN stage65p_race_details d ON d.race_id=r.id
          WHERE r.race_date BETWEEN '2022-01-01' AND ?'''
 for row in cx.execute(sql,(last,)):
  rid=str(row['id']);refrow=ref.get(rid)
  if not refrow:counts['no_exact_reference']+=1;continue
  date,course,rt,refname,country=refrow
  if date!=row['race_date'] or G.course_key(course)!=G.course_key(row['course']):
   counts['reference_identity_conflict']+=1;continue
  if country not in ('GB','IRELAND'):continue
  kind=R.discipline(row['race_type'],str(refname)+' '+str(row['race_name']),row['surface'])
  original=R.parse_ranked_tier({'race_date':date,'jurisdiction':country,
           'race_type':row['race_type'],'race_name':refname,'race_class':row['race_class'],
           'surface':row['surface']})
  # If full source name has a VERIFIED grade label, use it as another explicit
  # source. Never accept an unlabeled 'Class 1' as a graded race.
  full_title=str(row['race_name'] or '').strip()
  candidate=grade_from_pattern(row['pattern'],kind)
  if not candidate:
   m=TITLE_GRADE.search(full_title)
   if m:
    typ=re.search(r'\b(grade|group|grp|gr)\b',full_title[m.start():m.end()],re.I)
    if typ: candidate=grade_from_pattern(typ.group(1)+' '+m.group(1),kind)
  p=str(row['pattern'] or '').strip()
  if p:patterns[(p,kind)]+=1
  if not candidate:
   counts['no_explicit_recoverable_grade']+=1;continue
  if original['classification_code'] not in ('UNKNOWN',f'GB_CLASS_1',f'GB_CLASS_2',f'GB_CLASS_3',f'GB_CLASS_4',f'GB_CLASS_5',f'GB_CLASS_6'):
   expected=f'NH_GRADE_{candidate[-1]}' if candidate.startswith('Grade') else f'FLAT_GROUP_{candidate[-1]}'
   if original['classification_code']!=expected:
    counts['existing_specific_tier_conflict']+=1
    if len(conflicts)<150:conflicts.append({'race_id':rid,'date':date,'course':course,
     'ref_title':refname,'source_pattern':p,'existing_tier':original['classification_code'],
     'pattern_grade':candidate,'reason':'EXISTING_SPECIFIC_TIER_CONFLICT'})
    continue
  existing=TITLE_GRADE.search(refname)
  if existing:
   # If present in reference, it must agree with source evidence.
   reference_grade=grade_from_pattern(refname[existing.start():existing.end()],kind)
   if reference_grade!=candidate:
    counts['reference_grade_conflict']+=1
    if len(conflicts)<150:conflicts.append({'race_id':rid,'date':date,'course':course,
     'ref_title':refname,'source_pattern':p,'reference_grade':reference_grade,
     'source_grade':candidate,'reason':'REFERENCE_CONFLICT'})
    continue
   counts['already_labelled']+=1
   continue
  new_title=str(refname).strip()+f' ({candidate})'
  reparsed=R.parse_ranked_tier({'race_date':date,'jurisdiction':country,
        'race_name':new_title,'race_type':row['race_type'], 'race_class':row['race_class'],
        'surface':row['surface']})
  if reparsed['classification_code']=='UNKNOWN' or reparsed['classification_code']==original['classification_code']:
   counts['unrecoverable_due_to_other_tier_constraints']+=1
   if len(conflicts)<150:conflicts.append({'race_id':rid,'date':date,'course':course,
    'ref_title':refname,'source_pattern':p,'reason':'INAPPLICABLE_OR_UNRESOLVED',
    'recovered_grade':candidate,'parsed_tier':reparsed['classification_code']})
   continue
  details[rid]={'race_id':rid,'date':date,'course':course,'original_title':refname,
    'enriched_title':new_title,'source_name':full_title,'source_pattern':p,
    'source_file':str(row['source_file'] or ''),'previous_tier':original['classification_code'],
    'verified_tier':reparsed['classification_code'],'evidence':'STAGE65P_EXPLICIT_PATTERN_OR_SOURCE_TITLE'}
  counts['recovered_explicit_grades']+=1
  yearly[(date[:4],kind,reparsed['classification_code'])]+=1
  if len(samples)<30:samples.append(details[rid])
 cx.close()
 out.mkdir(parents=True,exist_ok=True)
 with (out/'stage141-exact-grade-overlay.csv').open('w',newline='',encoding='utf8') as f:
  fields=['race_id','date','course','original_title','enriched_title','source_name','source_pattern','source_file','previous_tier','verified_tier','evidence']
  w=csv.DictWriter(f,fields);w.writeheader();w.writerows(details.values())
 if conflicts:
  with (out/'stage141-grade-review.csv').open('w',newline='',encoding='utf8') as f:
   fields=sorted(set(k for c in conflicts for k in c));w=csv.DictWriter(f,fields);w.writeheader();w.writerows(conflicts)
 report={'status':'RESEARCH_EXACT_SOURCE_ONLY','source':'immutable Stage2 + Stage65P + Stage4',
 'recovered_count':len(details),'counts':dict(counts),'sample':samples,
 'pattern_frequency':[{ 'pattern':p,'kind':k,'races':n} for (p,k),n in patterns.most_common(80)],
 'recovered_by_year_kind':[{ 'year':y,'discipline':k,'tier':t,'races':n} for (y,k,t),n in sorted(yearly.items())],
 'no_heuristic_inference_from_class1':True,'source_original_unchanged':True,
 'unverified_not_filled':True,'reference_audit':audit}
 (out/'stage141-grade-audit.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
 print('STAGE141_GRADE',json.dumps({'recovered_count':len(details),'counts':dict(counts)}),flush=True)
 return details,report


def construct_2026(db,reference,last,grade_overlay=None):
 original=S.race_catalogue
 if grade_overlay is not None:
  def cat(e,region,name,starters):
   row=grade_overlay.get(str(e[0]))
   return original(e,region,row['enriched_title'] if row else name,starters)
  S.race_catalogue=cat
 try:races,audit,refaudit=U.extend_stage137_only_for_2026(db,reference,last)
 finally:S.race_catalogue=original
 if grade_overlay is not None:
  for r in races:
   z=grade_overlay.get(str(r['race_id']))
   if z:r['race_name']=z['enriched_title']
 return races,audit,refaudit


def _code(e,ref):
 if not ref:return 'UNKNOWN'
 raw=(str(e[0].get('race_type') or '')+' '+str(ref[3])).lower()
 if 'national hunt flat' in raw or 'nh flat' in raw or 'bumper' in raw or 'inh flat' in raw:return 'NH_FLAT'
 if 'chase' in raw or 'steeple' in raw:return 'CHASE'
 if 'hurdle' in raw:return 'HURDLE'
 if 'flat' in raw or 'handicap' in raw or 'maiden' in raw or 'stakes' in raw:return 'FLAT'
 return 'UNKNOWN'


def weight_context(db,reference,races,out,last):
 """Prior-day weight/OR evidence, actual winning mark and hypothetical net merit.

    A 1-point OR difference corresponds to a theoretical 1lb handicap difference,
    not an official probability or race-specific performance figure.  In pattern
    races, net merit = (OR - recorded carried lb) relative to current opponents.
    Recorded carried weight is kept distinct from official rating and actual
    jockey claims; unknown WFA/claims are NOT invented.

    Prior winning-mark evidence is restricted to VERIFIED previous handicap wins
    of the same code and only becomes visible on the next calendar date.
 """
 ref,_=G.load_reference(reference,first='1988-01-01',last=last)
 race_targets={str(r['race_id']):r for r in races}
 wanted={str(x) for r in races for x in r['identity'] if x}
 records=defaultdict(list);pending=[];prev_date=None
 stats=collections.Counter();coverage=collections.Counter();perrace={}
 def publish():
  nonlocal pending
  for k,rec in pending:records[k].append(rec)
  pending=[]
 for entries in C.stream_archive(db,'1988-01-01',last):
  date=str(entries[0]['date']);rid=str(entries[0]['race_id']);match=ref.get(rid)
  if date!=prev_date:
   publish();prev_date=date
  if not C.race_identity(entries[0],match):continue
  starters=[e for e in entries if C.eligible_starter(e)]
  if len(starters)<2:continue
  n=len(starters)
  weights=[number(e['weight_lbs']) for e in starters]
  ors=[number(e['official_rating']) for e in starters]
  weights=[w if w is not None and 70<=w<=230 else None for w in weights]
  ors=[v if v is not None and 1<=v<=210 else None for v in ors]
  valid_weight=[z for z in weights if z is not None]
  valid_or=[z for z in ors if z is not None]
  wavg=avg(valid_weight) if len(valid_weight)>=3 else None
  wmax=max(valid_weight) if len(valid_weight)>=3 else None
  ormean=avg(valid_or) if len(valid_or)>=3 and len(valid_or)/n>=.5 else None
  ormax=max(valid_or) if len(valid_or)>=3 and len(valid_or)/n>=.5 else None
  # Avoid a net comparison unless both rating and carried-weight coverage are
  # sufficient. The merit is a DIFFERENCE, not an absolute lb ability rating.
  net_values=[v-w for v,w in zip(ors,weights) if v is not None and w is not None]
  netmean=avg(net_values) if len(net_values)>=3 and len(net_values)/n>=.5 else None
  netmax=max(net_values) if len(net_values)>=3 and len(net_values)/n>=.5 else None
  netmin=min(net_values) if len(net_values)>=3 and len(net_values)/n>=.5 else None
  finish=[number(e['finish_pos']) for e in starters]
  winnerweights=[w for w,pos in zip(weights,finish) if pos==1 and w is not None]
  winner_w=winnerweights[0] if len(winnerweights)==1 else None
  disc=_code(entries,match)
  title=str(match[3] or '')
  # Exact handicap keyword in the race TITLE, not implied by rating bands or
  # 'Class 1'.  A titled 'novice handicap' is still a handicap.
  is_handicap=bool(re.search(r'\bhandicaps?\b|\bh\.?\s*cap\b|\bnursery\b',title,re.I))
  keys=[C.verified_horse_key(e) for e in starters]
  freq=collections.Counter(keys)
  if rid in race_targets:
   r=race_targets[rid]
   indices={k:i for i,k in enumerate(keys) if k and freq[k]==1}
   if not all(k in indices for k in r['identity']):
    raise RuntimeError('Stage141 full-field weight/OR identity mismatch '+rid)
   weight_feat=[];or_feat=[]
   for k in r['identity']:
    i=indices[k];e=starters[i];cur=weights[i];or_now=ors[i]
    prev=records[k]  # All records were published on an EARLIER date.
    same=[p for p in prev if p['code']==disc]
    weighted=[p for p in same if p['weight'] is not None]
    last=weighted[-1] if weighted else None
    recent=weighted[-3:]
    good=sorted((p for p in weighted if p['quality'] is not None),key=lambda p:p['quality'],reverse=True)[:3]
    delta=cur-last['weight'] if cur is not None and last else None
    dlast3=cur-avg([p['weight'] for p in recent]) if cur is not None and recent else None
    dbest=cur-avg([p['weight'] for p in good]) if cur is not None and good else None
    or_change=or_now-last['or'] if or_now is not None and last and last['or'] is not None else None
    gap_change=delta-or_change if delta is not None and or_change is not None else None
    performance=[p['quality']*p['relative_field'] for p in recent if p['quality'] is not None and p['relative_field'] is not None]
    high=[p['quality'] for p in weighted if p['relative_field'] is not None and p['relative_field']>=3 and p['quality'] is not None]
    low=[p['quality'] for p in weighted if p['relative_field'] is not None and p['relative_field']<=-3 and p['quality'] is not None]
    lastwin=next((p for p in reversed(same) if p['won_handicap'] and p['or'] is not None),None)
    winmark=lastwin['or'] if lastwin else None
    winwt=lastwin['weight'] if lastwin else None
    # Only earlier starts following the previous handicap victory are eligible
    # to determine the peak mark and runs since the win.
    postwin=[p for p in same if lastwin and p['date']>lastwin['date']]
    peak=max((p['or'] for p in postwin if p['or'] is not None),default=winmark)
    last3=same[-3:]
    last5=same[-5:]
    recent_quality3=avg([p['quality'] for p in last3])
    recent_quality5=avg([p['quality'] for p in last5])
    # Unknown historic OR must NOT be treated as zero or subtracted from today's OR.
    recent_or_mean=avg([p['or'] for p in last3])
    near=(int(abs(or_now-winmark)<=2) if or_now is not None and winmark is not None else None)
    # Qualified composite: last winning mark, >=2 non-winning runs since that
    # win, competitive *recent actual finished results*, not a blind signal.
    recover=(int(near==1 and len(postwin)>=2 and recent_quality3 is not None and recent_quality3>=.65)
             if near is not None and lastwin else None)
    rel_w=cur-wavg if cur is not None and wavg is not None else None
    rel_or=or_now-ormean if or_now is not None and ormean is not None else None
    net=or_now-cur if or_now is not None and cur is not None else None
    vals=[rel_w,
       (cur-wmax if cur is not None and wmax is not None else None),
       (int(abs(cur-wmax)<.1) if cur is not None and wmax is not None else None),
       delta,dlast3,dbest,gap_change,
       avg([p['relative_field'] for p in recent]),
       avg([p['relative_winner'] for p in recent]),avg(performance),
       avg([p['weight'] for p in recent]),len(weighted),avg(high),avg(low),
       sum(p['or'] is not None for p in recent),int(cur is not None),
       (cur-winwt if cur is not None and winwt is not None else None)]
    oval=[rel_or,
       (or_now-ormax if or_now is not None and ormax is not None else None),
       (net-netmean if net is not None and netmean is not None else None),
       (net-netmax if net is not None and netmax is not None else None),
       (net-netmin if net is not None and netmin is not None else None),
       len(valid_or)/n if n else None,
       int(net is not None and netmean is not None),
       (or_now-last3[-1]['or'] if or_now is not None and last3 and last3[-1]['or'] is not None else None),
       (or_now-recent_or_mean if or_now is not None and recent_or_mean is not None else None),
       peak,
       (peak-or_now if peak is not None and or_now is not None else None),
       winmark,
       (or_now-winmark if or_now is not None and winmark is not None else None),
       near,
       (max(0,winmark-or_now) if or_now is not None and winmark is not None else None),
       ((dt.date.fromisoformat(date)-dt.date.fromisoformat(lastwin['date'])).days if lastwin else None),
       (len(postwin) if lastwin else None),
       sum(p['won_handicap'] for p in same),
       recent_quality3,recent_quality5,recover,int(lastwin is not None),
       (rel_w-rel_or if rel_w is not None and rel_or is not None else None)]
    assert len(vals)==len(W_FEATURES) and len(oval)==len(OR_FEATURES)
    weight_feat.append([x if x is not None else np.nan for x in vals])
    or_feat.append([x if x is not None else np.nan for x in oval])
    coverage['evaluated_runner_rows']+=1
    if cur is not None:coverage['current_weight_known']+=1
    if last:coverage['prior_weight_known']+=1
    if or_now is not None:coverage['current_or_known']+=1
    if net is not None and netmean is not None:coverage['current_net_advantage_known']+=1
    if lastwin:coverage['prior_handicap_winning_mark_known']+=1
    if recover==1:coverage['return_to_winning_mark_with_form']+=1
    if near==1:coverage['at_last_winning_mark']+=1
    if wavg is not None:coverage['field_average_known']+=1
    if cur is not None and last and or_change is not None:coverage['weight_and_mark_change_known']+=1
   perrace[rid]=(np.asarray(weight_feat,dtype=np.float32),np.asarray(or_feat,dtype=np.float32))
   r['weight_today_lbs']=np.asarray([weights[indices[k]] if weights[indices[k]] is not None else np.nan for k in r['identity']],dtype=np.float32)
   r['official_rating_today']=np.asarray([ors[indices[k]] if ors[indices[k]] is not None else np.nan for k in r['identity']],dtype=np.float32)
  # Results are NOT published to per-horse form until next calendar day.
  for k,e,w,orv,pos in zip(keys,starters,weights,ors,finish):
   if not k or freq[k]!=1 or k not in wanted:continue
   qq=(n-pos)/(n-1) if pos is not None and 1<=pos<=n else None
   rec={'date':date,'code':disc,'weight':w,'or':orv,
       'relative_field':w-wavg if w is not None and wavg is not None else None,
       'relative_winner':w-winner_w if w is not None and winner_w is not None else None,
       'quality':qq,
       'won_handicap':int(bool(pos==1 and is_handicap))}
   pending.append((k,rec));stats['historic_target_runs_considered']+=1
  stats['verified_source_races']+=1
 publish()
 if set(perrace)!=set(race_targets):
  missing=list(set(race_targets)-set(perrace))
  raise RuntimeError('Stage141 weight/OR context missing '+str(len(missing))+' races; e.g. '+str(missing[:5]))
 for r in races:
  w,o=perrace[str(r['race_id'])]
  r['weight_features']=w;r['or_features']=o
 report={'status':'RESEARCH_PRE_RACE_OR_AND_WEIGHT_HISTORY','earliest_scanned':'1988-01-01',
   'max_prior_runs_per_horse':None,'verified_history_not_proof_of_complete_career':True,
   'weight_feature_names':list(W_FEATURES),'or_feature_names':list(OR_FEATURES),
   'counts':dict(stats),'coverage':dict(coverage),
   'unknown_claims_or_wfa_not_imputed':True,'raw_btn_not_assumed_overall_margin':True,
   'net_merit_is_theoretical_not_probability':True,
   'handicap_winning_mark_requires_prior_explicit_handicap_win':True,
   'unknown_last_win_mark_not_interpreted_as_never_won':True,
   'train_period_data_leakage_guard':'previous calendar day only'}
 (out/'stage141-weight-audit.json').write_text(json.dumps(report,indent=2)+'\n')
 print('STAGE141_OR_WEIGHT',json.dumps({'coverage':dict(coverage),'source_races':stats['verified_source_races']}),flush=True)
 return report


def copy_race(r,weights=False,official=False):
 x={**r,'xf':r['xf'].copy()}
 # Keep the immutable parent model x and training label exactly unchanged.
 if weights:x['xf']=np.hstack([x['xf'],r['weight_features']]).astype(np.float32)
 if official:x['xf']=np.hstack([x['xf'],r['or_features']]).astype(np.float32)
 return x


def evaluate(base,graded,last,out,archive):
 check=U.verified_2025_regression(None,base,archive)
 byid={str(r['race_id']):r for r in graded}
 if set(byid)!={str(r['race_id']) for r in base}:
  raise RuntimeError('Grade-overlay changed cohort identities')
 # Full original HPR v3.1 stays fixed; only tier + historical weight evidence
 # are ablated. A later experiment may specifically re-rate HPR with grades.
 allrows=[];full=[];folds=[]
 for q,start,end in U.SPLITS:
  if last<start:break
  end=min(end,last)
  training=[r for r in base if '2023-01-01'<=r['date']<start]
  testing=[r for r in base if start<=r['date']<=end]
  if not testing:continue
  if len(testing)<100:raise RuntimeError('Too few 2026 races in '+q)
  arms={BASE:training,
       GRADE:[byid[str(x['race_id'])] for x in training],
       OR_ONLY:[copy_race(x,official=True) for x in training],
       WEIGHT:[copy_race(x,weights=True) for x in training],
       GRADE_WEIGHT:[copy_race(byid[str(x['race_id'])],weights=True) for x in training],
       OR_WEIGHT:[copy_race(x,weights=True,official=True) for x in training],
       ALL:[copy_race(byid[str(x['race_id'])],weights=True,official=True) for x in training]}
  banks={name:H.l137_ml_banks(tr,ability=True) for name,tr in arms.items()}
  for r in testing:
   rid=str(r['race_id'])
   score={}
   for name in MODELS:
    src=byid[rid] if name in (GRADE,GRADE_WEIGHT,ALL) else r
    if name in (WEIGHT,GRADE_WEIGHT,OR_WEIGHT,ALL) or name in (OR_ONLY,OR_WEIGHT,ALL):
     src=copy_race(src,weights=name in (WEIGHT,GRADE_WEIGHT,OR_WEIGHT,ALL),
                   official=name in (OR_ONLY,OR_WEIGHT,ALL))
    prob,bank=S.predict_bank(banks[name],H.hpr_ability_137(src))
    if not np.all(np.isfinite(prob)) or len(prob)!=len(r['horse']):raise RuntimeError('Invalid scoring '+rid)
    order=np.argsort(-prob,kind='stable');i,j=map(int,order[:2]);sp=number(r['sp'][i])
    score[name]=prob
    allrows.append({'quarter':q,'date':r['date'],'race_id':rid,'meeting':r['course'],
     'race_name':src['race_name'],'discipline':r['kind'],'broad':r['broad'],
     'subtype':src['subtype'],'official_tier':src['official_tier'],'model':name,
     'rank1_horse':r['horse'][i],'rank2_horse':r['horse'][j],
     'rank1_won':int(i==r['winner_idx']),'rank2_won':int(j==r['winner_idx']),
     'sp':sp,'probability_score_rank1':float(prob[i]),'winner':r['horse'][r['winner_idx']],
     'field_size':len(r['horse']),'hpr':number(r['hpr_preoff'][i]),
     'weight_today_lbs':float(r['weight_today_lbs'][i]) if np.isfinite(r['weight_today_lbs'][i]) else '',
     'weight_vs_field':float(r['weight_features'][i,0]) if np.isfinite(r['weight_features'][i,0]) else '',
     'weight_change_last':float(r['weight_features'][i,3]) if np.isfinite(r['weight_features'][i,3]) else '',
     'or_net_edge_vs_field':float(r['or_features'][i,2]) if np.isfinite(r['or_features'][i,2]) else '',
     'or_last_winning_mark':float(r['or_features'][i,11]) if np.isfinite(r['or_features'][i,11]) else '',
     'or_change_since_last_win':float(r['or_features'][i,12]) if np.isfinite(r['or_features'][i,12]) else '',
     'or_near_last_win_good_form':float(r['or_features'][i,20]) if np.isfinite(r['or_features'][i,20]) else '',
     'bank':bank})
   for i,h in enumerate(r['horse']):
    full.append({'date':r['date'],'race_id':rid,'horse':h,'winning':int(i==r['winner_idx']),
      'preoff_hpr':float(r['hpr_preoff'][i]) if np.isfinite(r['hpr_preoff'][i]) else '',
      'weight_vs_field':float(r['weight_features'][i,0]) if np.isfinite(r['weight_features'][i,0]) else '',
      'weight_change_last':float(r['weight_features'][i,3]) if np.isfinite(r['weight_features'][i,3]) else '',
      'or_net_edge_vs_field':float(r['or_features'][i,2]) if np.isfinite(r['or_features'][i,2]) else '',
      'or_last_winning_mark':float(r['or_features'][i,11]) if np.isfinite(r['or_features'][i,11]) else '',
      'or_near_last_win_good_form':float(r['or_features'][i,20]) if np.isfinite(r['or_features'][i,20]) else '',
      **{name+'_score':float(score[name][i]) for name in MODELS}})
  folds.append({'quarter':q,'trained_prior_races':len(training),'evaluated_races':len(testing),
                'trained_through':(dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat()})
  print('STAGE141_QUARTER',json.dumps(folds[-1]),flush=True)
  del banks
 if not allrows:raise RuntimeError('No 2026 predictions')
 summary=[];groups=defaultdict(list)
 for r in allrows:
  for dim,group in [('ALL','ALL'),('QUARTER',r['quarter']),('DISCIPLINE',r['discipline']),
                    ('BROAD',r['broad']),('RACE_TYPE',r['subtype']),('TIER',r['official_tier'])]:
   groups[(r['model'],dim,group)].append(r)
 for (name,dim,group),xx in sorted(groups.items()):
  settled=[r for r in xx if r['sp'] is not None and 1<float(r['sp'])<1001]
  ret=sum(float(r['sp']) for r in settled if r['rank1_won'])
  summary.append({'model':name,'breakdown':dim,'group':group,'races':len(xx),
    'wins':sum(r['rank1_won'] for r in xx),'strike_pct':round(100*sum(r['rank1_won'] for r in xx)/len(xx),3),
    'rank1_or2_wins':sum(r['rank1_won']+r['rank2_won'] for r in xx),
    'priced':len(settled),'sp_coverage_pct':round(100*len(settled)/len(xx),2),
    'sp_roi_pct':round(100*(ret/len(settled)-1),3) if settled else None})
 U.store_rows(out/'stage141-2026-rank1-rank2.csv.gz',allrows,True)
 U.store_rows(out/'stage141-2026-runner-scores.csv.gz',full,True)
 U.store_rows(out/'stage141-2026-discipline-racetype.csv',summary)
 return {name:next(r for r in summary if r['model']==name and r['breakdown']=='ALL') for name in MODELS},folds,check


def research(db,reference,archived,out,max_date=None):
 # Original Stage140 regression requires this EXACT archived Stage30 D recipe;
 # Stage137's rounded constants are only a printed approximation.
 import stage30_gap_aware_abcd as D
 orig=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
 if orig['chosen_combination']!='D-064-gap14':raise RuntimeError('Original D identity changed')
 exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
 if not np.allclose([orig['weights_pct'][p] for p in S.PILLARS],exact*100,atol=.011):
  raise RuntimeError('Archived D weight percentages no longer match')
 S.FROZEN[:]=exact
 S.CANDIDATES=S.candidate_weights()
 last,source_date_audit=U.audit_limit(db,max_date)
 out.mkdir(parents=True,exist_ok=True)
 overlay,grade_audit=official_grade_overlay(db,reference,out,last)
 if len(overlay)<20:
  raise RuntimeError('Fewer than 20 exact Grade/Group recoveries: inspect stage141-grade-audit.json source pattern values before training')
 base,ba,refaud=construct_2026(db,reference,last)
 grade,ga,_=construct_2026(db,reference,last,overlay)
 target={k for r in base for k in r['identity'] if k}
 old_end=H.END
 try:
  H.END=last
  hprdb,hpr_audit=H.hpr_history(db,reference,out,target)
 finally:H.END=old_end
 hc=collections.Counter();H.attach_hpr(base,hprdb,hc);H.attach_hpr(grade,hprdb,collections.Counter())
 weightaudit=weight_context(db,reference,base,out,last)
 weight_by_rid={str(r['race_id']):r for r in base}
 for r in grade:
  parent=weight_by_rid[str(r['race_id'])]
  r['weight_features']=parent['weight_features']
  r['or_features']=parent['or_features']
  r['weight_today_lbs']=parent['weight_today_lbs']
  r['official_rating_today']=parent['official_rating_today']
 overall,folds,regression=evaluate(base,grade,last,out,archived)
 report={'version':VERSION,'status':'RESEARCH_ONLY_GRADE_WEIGHT_ABLATION',
   'source_cutoff':last,'source_date_audit':source_date_audit,
   'models':list(MODELS),'original_frozen_D_unchanged':True,
   'original_stage137_unchanged':True,'original_stage139_hpr_unchanged':True,
   'live_predictions_modified':False,'live_database_modified':False,
   'recovered_explicit_grade_count':len(overlay),'grade_audit':grade_audit,
   'weight_audit':weightaudit,'hpr_audit':hpr_audit,'hpr_coverage':dict(hc),
   'baseline_guard':regression,'overall':overall,'folds':folds,
   'limits':['Only explicit source pattern/name Grade/Group labels recovered; Class 1 alone never implies G1.',
     'Original HPR-v3.1 stays fixed so grade, OR and weight feature effects can be isolated; HPR itself is not re-rated in this stage.',
     'The historic weight source may not record jockey claims, weight-for-age or overweight corrections; these are unknown.',
     'Without verified overall beaten margins and running speed, results are not official BHA pounds-per-length performance figures.',
     'Previously known 2026 results mean this is a retrospective experiment; prospective testing is still required.',
     'SP ROI only describes priced subset, not executable prices.']}
 (out/'stage141-summary.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
 print('STAGE141_OVERALL',json.dumps(overall),flush=True)
 return report


def self_test():
 assert grade_from_pattern('G1','HURDLE')=='Grade 1'
 assert grade_from_pattern('G2','FLAT')=='Group 2'
 assert grade_from_pattern('Grade 3','CHASE')=='Grade 3'
 assert grade_from_pattern('Group 1','FLAT')=='Group 1'
 assert grade_from_pattern('1','HURDLE') is None
 assert grade_from_pattern('Class 1','HURDLE') is None
 assert grade_from_pattern('Grade 1','FLAT') is None
 assert grade_from_pattern('Group 1','CHASE') is None
 assert grade_from_pattern('Listed','FLAT') is None
 assert grade_from_pattern('Gr 2','NH_FLAT')=='Grade 2'
 assert len(W_FEATURES)==17 and len(set(W_FEATURES))==len(W_FEATURES)
 assert len(OR_FEATURES)==23 and len(set(OR_FEATURES))==len(OR_FEATURES)
 assert H.ABILITY_LIFT==.35 and len(S.PILLARS)==8
 print('STAGE141 SELFTEST PASS: explicit grade, OR net merit, winning mark and prior carried weight, frozen HPR')


def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test')
 a=sub.add_parser('audit');a.add_argument('--db',required=True,type=pathlib.Path)
 a.add_argument('--reference',required=True,type=pathlib.Path)
 a.add_argument('--out',required=True,type=pathlib.Path)
 a.add_argument('--to-date',default='2026-11-10')
 b=sub.add_parser('walkforward');b.add_argument('--db',required=True,type=pathlib.Path)
 b.add_argument('--reference',required=True,type=pathlib.Path)
 b.add_argument('--stage137',required=True,type=pathlib.Path)
 b.add_argument('--out',required=True,type=pathlib.Path)
 b.add_argument('--to-date')
 v=p.parse_args()
 if v.cmd=='self-test':self_test()
 elif v.cmd=='audit':official_grade_overlay(v.db,v.reference,v.out,v.to_date)
 else:research(v.db,v.reference,v.stage137,v.out,v.to_date)

if __name__=='__main__':main()
