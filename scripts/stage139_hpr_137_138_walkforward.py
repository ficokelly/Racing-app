#!/usr/bin/env python3
"""Stage 139: true chronological HPR-v3.1 reconstruction in BOTH Stage 137/138.

Four paper challengers, paired with original archived Stage 137 and Stage 138:
    D137_HPR_ABILITY: append HPR evidence inside original D Ability signal (no pillar 9)
    D137_HPR_NINTH: keep original Stage137 ML, add independent, explicitly weighted
        HPR ranking dimension; weight learned from strictly earlier OOF races.
    D138_HPR_ABILITY: HPR feature trained only inside the Ability learner; final
        prediction is still precisely 8 weighted D pillars.
    D138_HPR_NINTH: original 8 Stage138 pillar learners and 9th independent HPR
        score; weight learned from strictly earlier OOF races.

Base HPR = exactly Stage86 HPR-v0 score_event and Stage134C v3.1 reconstruction,
using Stage133 authentic UK/IE class parser and Stage131 class/weight rules.
Pre-race HPR uses ONLY previous-calendar-date historic event scores (not the
outcome/price/comment of the target race). Source historical archive read-only;
no live HPR store or app writes. 2023 training; Q4 2023 prequential calibration;
8 2024-25 strictly sequential test quarters. Prices ONLY for later settlement.
Research on previously viewed periods is not a new prospective validation.
"""
from __future__ import annotations

import argparse
import collections
from collections import defaultdict
import csv
import datetime as dt
import gzip
import gc
import json
import math
import pathlib
import sqlite3
import sys

import numpy as np

import stage137_frozen_d_race_type_walkforward as S
import stage138_integrated_eight_pillars as T

VERSION = 'stage139-hprv31-137-138-four-arms-2026-11-15-v1'
START_HPR = None  # Resolved dynamically from the full archive; no fixed-year truncation.
END = '2025-12-31'
INIT_CAL = ('2023-10-01','2023-12-31')
MODEL_NAMES = ('D137_HPR_ABILITY','D137_HPR_NINTH',
               'D138_HPR_ABILITY','D138_HPR_NINTH')
# Fixed BEFORE inspecting any Stage139 test outcomes. An HPR rating point is
# not directly comparable with a D pillar. Normalize within exact current field.
ABILITY_LIFT = 0.35
NINTH_GRID = (0.,0.10,0.25,0.40,0.60,0.90)
MIN_HPR_RATED_FIELD = 2
MIN_OOF_GROUP = 90


def num(x):
    try:
        z=float(x)
        return z if math.isfinite(z) else None
    except (ValueError,TypeError):
        return None


def norm_hpr(arr):
    """Full-field, comparable relative HPR score; absent HPR is neutral.

    For a field with just 0/1 HPR-rated horses we cannot infer relative
    quality. Never fabricate an absolute low HPR for an unrated horse.
    """
    a=np.asarray(arr,dtype=float)
    good=np.isfinite(a) & (a>=0) & (a<=100)
    z=np.zeros(len(a),dtype=np.float32)
    if good.sum()>=MIN_HPR_RATED_FIELD:
        v=a[good];cen=float(v.mean());scale=max(12.,float(v.std()))
        z[good]=np.clip((v-cen)/scale,-2.5,2.5)
    return z,int(good.sum())


def write_csv(path,rows,compressed=False):
    if not rows:raise RuntimeError('Refusing empty output '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    open_=gzip.open if compressed else open
    with open_(path,'wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),extrasaction='ignore')
        w.writeheader();w.writerows(rows)


def hpr_history(db,reference,out,target_horses):
    """Build full-field, EXACT-source HPR-v3.1 preoff rows (read-only source).

    Adapts the full-field engine of Stage134C, rather than reusing its sparse
    Rank1-only outputs. Every verified starter receives a historical pre-off
    HPR if it has same-code previous-day form. The last Q result is never used
    when scoring that Q race. All rating events update at calendar-day boundary.
    """
    import stage134c_full_field_hpr_replay as C
    import stage86_hpr_engine as H
    import stage133_class_taxonomy_hpr_replay as CT
    import stage108_model_g2 as G

    # Use the actual oldest archive result date, not the recent-form cutoffs
    # retained by frozen Stage137 (2022) and Stage138 (2015).
    source=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    try:
        oldest=source.execute("SELECT MIN(race_date) FROM historical_races WHERE race_date<=?",(END,)).fetchone()[0]
        if not oldest or oldest>'2000-01-01':
            raise RuntimeError('Long historical archive unavailable: '+str(oldest))
        yearly_source=dict(source.execute("SELECT SUBSTR(race_date,1,4), COUNT(*) FROM historical_races WHERE race_date<=? GROUP BY SUBSTR(race_date,1,4)",(END,)))
    finally: source.close()
    refs,refaudit=G.load_reference(reference,first=oldest,last=END)
    if len(target_horses)<1000:raise RuntimeError('Too few identified target runners for historical HPR build')
    print('Stage139 HPR full-history start',oldest,'year bins',len(yearly_source),
          'target horse identities',len(target_horses),flush=True)
    target_db=out/'stage139-preoff-hpr-v31.sqlite'
    out.mkdir(parents=True,exist_ok=True)
    if target_db.exists():target_db.unlink()
    con=sqlite3.connect(target_db)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    con.execute('''CREATE TABLE hpr_preoff (
        race_id TEXT NOT NULL, horse_key TEXT NOT NULL, date TEXT NOT NULL,
        code TEXT NOT NULL, preoff_hpr REAL NOT NULL, prior_same_code_runs INTEGER NOT NULL,
        PRIMARY KEY (race_id,horse_key))''')
    # Per-horse event histories are only updated AFTER the full race date has passed.
    # Original score_event checks all historical form conditions; do not truncate.
    history=defaultdict(list)
    pending=[];current_day=None;counts=collections.Counter()
    sample=[];insert=[];year_counts=collections.Counter();target_year_counts=collections.Counter()

    def publish_day():
        nonlocal pending
        for key,value in pending:history[key].append(value)
        pending=[]

    def save_batch():
        nonlocal insert
        if insert:
            con.executemany('INSERT INTO hpr_preoff VALUES (?,?,?,?,?,?)',insert)
            insert=[]

    try:
        for entries in C.stream_archive(db,oldest,END):
            d=str(entries[0]['date']);rid=str(entries[0]['race_id'])
            if current_day is not None and d!=current_day:
                publish_day()
            current_day=d
            rf=refs.get(rid)
            if not C.race_identity(entries[0],rf):
                counts['race_no_exact_reference']+=1
                continue
            country=str(rf[4]).upper();title=str(rf[3]);rtype=str(rf[2])
            title_only=CT.parse(title,rtype,country)
            stored_class=str(entries[0].get('race_class') or '').strip()
            parsed=CT.parse(title,f'{rtype} {stored_class}',country)
            # The same historical safety condition as Stage134C.
            if (title_only['explicit_uk_class'] is not None and
                parsed['explicit_uk_class'] is not None and stored_class and
                (recorded:=CT.parse('',stored_class,country)['explicit_uk_class']) is not None and
                title_only['explicit_uk_class']!=recorded):
                counts['class_metadata_conflict']+=1
                continue
            code=parsed['race_code']
            starters=[e for e in entries if C.eligible_starter(e)]
            if len(starters)<2:
                counts['small_field']+=1;continue
            keys=[C.verified_horse_key(e) for e in starters]
            key_counts=collections.Counter(k for k in keys if k)
            # Avoid losing a valid target horse's earlier run simply because
            # an unrelated competitor in the same race has no verifiable ID.
            # All runners still contribute to the recorded field and OR mean.
            if not all(keys):counts['fields_with_missing_horse_ids']+=1
            if len(set(k for k in keys if k))!=len([k for k in keys if k]):
                counts['fields_with_duplicate_horse_ids']+=1
            # Identity proof: target IDs must agree EXACTLY with Stage137/138.
            if d>='2023-01-01':
                for key,e in zip(keys,starters):
                    if not key or key not in target_horses:continue
                    g=S.ident((None,None,None,None,None,None,None,None,None,e['horse'],None,
                               e.get('horse_source_id'),None,None,None,None,None,None,e.get('sire'),e.get('dam')))
                    if g!=key:
                        raise RuntimeError(f'Historic HPR identity mismatch: {key} != {g}')
            fieldor,fieldweight,known_or,known_weight=C.field_context(starters)
            pts=C.class_points(parsed)
            counts['races_verified']+=1
            year_counts[d[:4]]+=1
            for key,e in zip(keys,starters):
                if not key or key_counts[key]!=1:
                    counts['skipped_unverifiable_runner_identity']+=1
                    continue
                # Only target horses need their HPR histories reconstructed.
                # Their entire career is retained, without a maximum number
                # of starts or a rolling-calendar cutoff. All opposition OR/
                # weights are still read from the FULL historical field.
                if key not in target_horses:
                    counts['historic_non_target_runner_rows']+=1
                    continue
                counts['historic_target_runner_rows']+=1
                target_year_counts[d[:4]]+=1
                past=history.get(key,())
                same=[z for z in past if z['code']==code]
                if same and code!='UNKNOWN' and d>='2023-01-01':
                    avg=C.average_last3(same,'new')
                    pre=C.current_conditions_hpr(same,avg,e,pts)
                    if pre is not None:
                        insert.append((rid,key,d,code,round(float(pre),3),len(same)))
                        counts['rated_preoff_runner_rows']+=1
                elif d>='2023-01-01':
                    counts['no_usable_prior_same_code']+=1
                if d>='2023-01-01':counts['total_preoff_runner_rows']+=1
                event={'horse_key':key,'horse':e['horse'],'race_key':'HIST|'+rid,
                  'date':d,'course':e['course'],'race_type':title+' '+rtype,
                  'surface':e.get('surface'),'distance_f':e.get('distance_f'),
                  'going':e.get('going'),'race_class':e.get('race_class'),
                  'trainer':e.get('trainer'),'jockey':e.get('jockey'),
                  'weight_lbs':e.get('weight_lbs'),'official_rating':e.get('official_rating'),
                  'draw':e.get('draw'),'position':e.get('finish_pos'),
                  'status':e.get('outcome'),'beaten':e.get('beaten'),
                  'field_size':len(starters),'field_or':fieldor if known_or>=max(2,math.ceil(len(starters)*.5)) else None,
                  'source':'HISTORICAL_EXACT_ARCHIVE_PREVIOUS_DAY'}
                try:
                    old_score,status=H.score_event(event,[p['v0_event'] for p in past])
                    if old_score is None:counts['unscored_'+str(status)]+=1;continue
                    now_hpr=C.v31(old_score,event,pts,fieldor,fieldweight)
                except (TypeError,ValueError,KeyError,ZeroDivisionError) as exc:
                    counts['hpr_scoring_exception']+=1
                    if len(sample)<30:sample.append({'date':d,'race':rid,'horse':key,'error':str(exc)[:150]})
                    continue
                # Keep ONLY what Stage86's future score_event and Stage134C's
                # current-condition calculation need. Do not drop earlier form.
                v0_keys=('date','trainer','jockey','course','distance_f','going',
                         'race_type','surface','draw','field_size')
                keep={x:old_score.get(x) for x in v0_keys}
                keep['hpr']=old_score['hpr']
                record={'date':d,'code':code,'new':float(now_hpr),
                        'class_points':pts,'weight_lbs':e.get('weight_lbs'),
                        'official_rating':e.get('official_rating'),'v0_event':keep}
                pending.append((key,record))
                counts['historic_runs_hpr_scored']+=1
            if len(insert)>=15000:save_batch()
            if counts['races_verified']%10000==0:
                print('HPR verified races',counts['races_verified'],
                      'prior-date preoff rows',counts['rated_preoff_runner_rows'],flush=True)
        if pending:publish_day()
        save_batch();con.commit()
        rowcount=con.execute('SELECT COUNT(*) FROM hpr_preoff').fetchone()[0]
        if rowcount!=counts['rated_preoff_runner_rows']:
            raise RuntimeError('HPR preoff cache mismatch')
        if rowcount<50000:raise RuntimeError('HPR reconstruction unexpectedly sparse')
        meta={'version':VERSION,'engine':'Stage86 HPR-v0 + Stage134C HPR-v3.1',
              'hpr_source':'historical exact UK/IE full fields, prior calendar days only',
              'hpr_v31_reconstructed_not_sparse_persisted_ledger':True,
              'history_start':oldest,'historical_source_yearly_races':yearly_source,
              'verified_reference_races_by_year':dict(year_counts),
              'target_horse_identity_count':len(target_horses),
              'target_horse_historical_runs_by_year':dict(target_year_counts),
              'unbounded_previous_runs_per_target_horse':True,
              'reference_audit':refaudit,
              'counts':dict(counts),'rows':rowcount,
              'hpr_score_range':[0,100],
              'missing_hpr_policy':'UNKNOWN_NEUTRAL, no invented score for debutant or no same-code prior',
              'warnings':['Not a new NLP-adjusted HPR; original v3.1 calculation retained.',
                'Post-race scoring of historical performances uses official result data only AFTER the race date.',
                'Reconstructed HPR may differ from the originally persisted sparse live HPR ledger.',
                'A first OBSERVED run is not proof of a career debut; archive completeness varies by year.',
                'A horse with an identified race is retained even if an unrelated rival has missing historical identity.',
                'Stage137 and Stage138 archived base models have their original independent lookback rules; ONLY the HPR overlay uses this all-available-years history.',
                'HPR still requires a prior SAME-CODE run; first-time chasers and true debutants are correctly left unrated by the existing HPR v3.1 formula.'],
              'sample_errors':sample}
        (out/'stage139-hpr-source-audit.json').write_text(json.dumps(meta,indent=2)+'\n')
        print('STAGE139_HPR_CACHE',rowcount,'rows; available previous-day ratings',flush=True)
        return target_db,meta
    finally:
        con.close()


def attach_hpr(races,source,stats):
    """Join HPR by exact race ID + verified runner identity, no fuzzy names."""
    db=sqlite3.connect('file:'+str(pathlib.Path(source).resolve())+'?mode=ro',uri=True)
    db.execute('PRAGMA query_only=1')
    for r in races:
        rid=str(r['race_id'])
        got={key:(float(h),int(n)) for key,h,n in db.execute(
             'SELECT horse_key,preoff_hpr,prior_same_code_runs FROM hpr_preoff WHERE race_id=?',(rid,))}
        a=np.array([got.get(str(k),(float('nan'),0))[0] for k in r['identity']],dtype=np.float32)
        z,known=norm_hpr(a)
        r['hpr_preoff']=a;r['hpr_z']=z;r['hpr_known']=np.isfinite(a).astype(np.float32)
        r['hpr_prior_same_code']=np.array([got.get(str(k),(None,0))[1] for k in r['identity']],dtype=np.int16)
        stats['races']+=1;stats['runners']+=len(a);stats['rated_runners']+=known
        if known>=2:stats['races_comparable_hpr']+=1
        if known==len(a):stats['races_full_hpr']+=1
    db.close()
    if stats['rated_runners']<20000:
        raise RuntimeError('HPR match rate unreasonably low; refuse misleading comparison')


def hpr_ability_137(r):
    # HPR feeds ONLY the existing Ability score. It is NOT an extra feature.
    a=r['xf'].copy()
    ix=S.FEATURES.index('Ability')
    a[:,ix]+=ABILITY_LIFT*r['hpr_z']
    return {**r,'xf':a}


def standardized_logit(p):
    q=np.clip(np.asarray(p,dtype=float),1e-6,1-1e-6)
    z=np.log(q/(1-q));z-=z.mean()
    return z/max(.30,float(z.std()))


def select_gamma(evidence,r):
    """Ninth weight chosen only from PREVIOUSLY unseen OOF predictions."""
    for group in S.hierarchy(r):
        rows=[z for z in evidence if group in S.hierarchy(z['race'])]
        if len(rows)<MIN_OOF_GROUP:continue
        wins=np.zeros(len(NINTH_GRID),dtype=np.float64)
        for z in rows:
            p=z['base'];hp=z['race']['hpr_z']
            for i,g in enumerate(NINTH_GRID):
                pick=int(np.argmax((1-g)*p+g*hp))
                wins[i]+=pick==z['race']['winner_idx']
        objectives=wins/len(rows)-.006*np.asarray(NINTH_GRID)**2
        best=int(np.argmax(objectives))
        return NINTH_GRID[best],group,len(rows),int(wins[best])
    return .0,'NONE_NO_OOF',0,0


def l137_ml_banks(train,ability=False):
    # Identical model parameters and race-type fallbacks as Stage137. If
    # ability=True, only D's original Ability feature differs.
    rows=[hpr_ability_137(x) if ability else x for x in train]
    return S.train_banks(rows)


def ability_models_138(train):
    """One extra HPR observation within Ability learner, no ninth output."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    by=defaultdict(list)
    for r in train:
        for grp in dict.fromkeys(('ALL',r['broad'],r['group'])):by[grp].append(r)
    result={}
    for grp,rs in by.items():
        if grp!='ALL' and len(rs)<T.MIN_PILLAR_RACES:continue
        y=np.concatenate([r['win'] for r in rs])
        if len(set(y.tolist()))!=2:continue
        xx=np.vstack([np.column_stack((T.pillar_input(r,'Ability'),
                                       r['hpr_preoff'],r['hpr_known'])) for r in rs])
        c=HistGradientBoostingClassifier(max_iter=52,max_leaf_nodes=10,
          max_depth=3,min_samples_leaf=120,learning_rate=.05,
          l2_regularization=12,early_stopping=False,random_state=138)
        c.fit(xx,y);result[grp]=c
    if 'ALL' not in result:raise RuntimeError('Stage138 Ability + HPR cannot be fitted')
    return result


def predict_138(fitted,ability_banks,r):
    """Return original eight updated scores AND original eight baseline scores."""
    old,info=T.predict_eight(fitted,r)
    combined=old.copy()
    for group in dict.fromkeys((r['group'],r['broad'],'ALL')):
        if group not in ability_banks:continue
        c=ability_banks[group]
        xx=np.column_stack((T.pillar_input(r,'Ability'),r['hpr_preoff'],r['hpr_known']))
        v=np.clip(c.predict_proba(xx)[:,1],1e-5,1-1e-5)
        q=np.log(v/(1-v));q-=q.mean()
        combined[:,0]=np.clip(q/max(.30,float(q.std())),-4,4)
        return combined,old,group
    raise RuntimeError('Missing HPR Ability fallback')


def old_predictions_csv(path,names):
    import pandas as pd
    data=pd.read_csv(path,compression='infer',low_memory=False)
    data=data[data.model.isin(names)]
    result={}
    for item in data.to_dict('records'):
        key=(str(item['race_id']),str(item['model']))
        if key in result:raise RuntimeError('Duplicate archived model / race '+str(key))
        result[key]=item
    return result


def score_row(r,model,order,pillar_weight='',evidence='',hpr_importance=None):
    i,j=int(order[0]),int(order[1]);v=num(r['sp'][i]);n=len(r['horse'])
    return {'date':r['date'],'period':str(r['date'])[:4]+'Q'+str((int(r['date'][5:7])-1)//3+1),
      'race_id':str(r['race_id']),'course':r['course'],'country':r['country'],
      'broad':r['broad'],'discipline':r['kind'],'subtype':r['subtype'],
      'race_type':r['type'],'official_tier':r['official_tier'],
      'model':model,'horse':r['horse'][i],'second_horse':r['horse'][j],
      'won':int(i==r['winner_idx']),'rank2_won':int(j==r['winner_idx']),
      'winner':r['horse'][r['winner_idx']],'sp':v,'runner_count':n,
      'hpr_rank1':round(float(r['hpr_preoff'][i]),3) if np.isfinite(r['hpr_preoff'][i]) else '',
      'hpr_rank2':round(float(r['hpr_preoff'][j]),3) if np.isfinite(r['hpr_preoff'][j]) else '',
      'hpr_rated_count':int(r['hpr_known'].sum()),'hpr_rated_pct':round(100*r['hpr_known'].mean(),2),
      'hpr_9th_weight':hpr_importance if hpr_importance is not None else '',
      'weight_group':pillar_weight,'hpr_training_source':evidence}


def test_137(races,out):
    """2023 training, quarter-fold refits, separate OOF HPR pillar tuning."""
    records=[];folds=[];coverage=collections.Counter()
    # Initial held-out Q4 2023 for a ninth-pillar weight without same-period tuning.
    train0=[r for r in races if '2023-01-01'<=r['date']<'2023-10-01']
    cal=[r for r in races if INIT_CAL[0]<=r['date']<=INIT_CAL[1]]
    baseline=l137_ml_banks(train0)
    prev=[]
    for r in cal:
        pp,_=S.predict_bank(baseline,r)
        prev.append({'race':r,'base':standardized_logit(pp),'origin':'2023Q4_OOF'})
    del baseline
    for q,start,end in S.PERIODS:
        training=[r for r in races if '2023-01-01'<=r['date']<start]
        testing=[r for r in races if start<=r['date']<=end]
        baseline=l137_ml_banks(training)
        ability=l137_ml_banks(training,ability=True)
        weights={}
        for r in testing:
            key=(r['subtype'],r['group'],r['broad'])
            if key not in weights:weights[key]=select_gamma(prev,r)
        next_ledger=[]
        for r in testing:
            ab,_=S.predict_bank(ability,hpr_ability_137(r))
            pp,_=S.predict_bank(baseline,r)
            p=standardized_logit(pp)
            g,source,training_n,win=weights[(r['subtype'],r['group'],r['broad'])]
            nine=(1-g)*p+g*r['hpr_z']
            records.append(score_row(r,MODEL_NAMES[0],np.argsort(-ab,kind='stable'),
                         evidence='HPR_v31_INSIDE_D_ABILITY'))
            records.append(score_row(r,MODEL_NAMES[1],np.argsort(-nine,kind='stable'),
                         pillar_weight=source,evidence='HPR_v31_INDEPENDENT_9',hpr_importance=g))
            next_ledger.append({'race':r,'base':p,'origin':q})
        prev.extend(next_ledger)
        folds.append({'period':q,'model':'D137','trained_races':len(training),
               'tested_races':len(testing),'ninth_prior_oof_races':len(prev)-len(testing),
               'chosen_ninth_weights_by_group':{str(k):float(v[0]) for k,v in weights.items()}})
        print('Stage139 Stage137',q,'completed',len(testing),'races; past OOF',len(prev),flush=True)
        del baseline,ability
        gc.collect()
    write_csv(out/'stage139-137-fourarm-results.csv.gz',records,True)
    return records,folds


def test_138(races,out):
    records=[];folds=[]
    first=[r for r in races if '2023-01-01'<=r['date']<'2023-10-01']
    cal=[r for r in races if INIT_CAL[0]<=r['date']<=INIT_CAL[1]]
    model,_=T.fit_pillars(first)
    abl=ability_models_138(first)
    prev_base=[];prev_ability=[]
    for r in cal:
        ab,old,_=predict_138(model,abl,r)
        prev_base.append({'race':r,'scores':old,'base':old@S.mask_weights(S.FROZEN,r['broad']),'origin':'2023Q4_OOF'})
        prev_ability.append({'race':r,'scores':ab,'origin':'2023Q4_OOF'})
    del model,abl
    for q,start,end in S.PERIODS:
        training=[r for r in races if '2023-01-01'<=r['date']<start]
        testing=[r for r in races if start<=r['date']<=end]
        base,_=T.fit_pillars(training)
        abank=ability_models_138(training)
        decisions={}
        for r in testing:
            k=(r['subtype'],r['group'],r['broad'])
            if k in decisions:continue
            w_ab,meta_ab=T.weights_for(prev_ability,r)
            w9,meta9=T.weights_for(prev_base,r)
            # Gamma tuned separately from the 8-pillar weights using the SAME
            # earlier OOF base scores; base row gets actual w9, not prior
            # snapshot weights. This is a valid independent 9th pillar.
            ninth_evidence=[{'race':z['race'],'base':z['scores']@w9}
                            for z in prev_base]
            g,source,n_prior,wins=select_gamma(ninth_evidence,r)
            decisions[k]=(w_ab,w9,g,source,meta_ab,meta9)
        next_base=[];next_ab=[]
        for r in testing:
            ab,old,ability_src=predict_138(base,abank,r)
            k=(r['subtype'],r['group'],r['broad'])
            w_ab,w9,g,source,m_ab,m9=decisions[k]
            vals_ab=ab@w_ab
            vals9=(1-g)*(old@w9)+g*r['hpr_z']
            records.append(score_row(r,MODEL_NAMES[2],np.argsort(-vals_ab,kind='stable'),
                                     pillar_weight=str(m_ab.get('group')),
                                     evidence='HPR_v31_INSIDE_ABILITY_LEARNER'))
            records.append(score_row(r,MODEL_NAMES[3],np.argsort(-vals9,kind='stable'),
                                     pillar_weight=source,evidence='HPR_v31_AS_PILLAR_9',
                                     hpr_importance=g))
            next_base.append({'race':r,'scores':old,'origin':q})
            next_ab.append({'race':r,'scores':ab,'origin':q})
        folds.append({'period':q,'model':'D138','trained_races':len(training),
               'tested_races':len(testing),'prior_oof_races':len(prev_base),
               'ninth_weighting':{str(k):float(v[2]) for k,v in decisions.items()},
               'ability_eight_weights':{str(k):dict(zip(S.PILLARS,map(float,v[0]))) for k,v in decisions.items()},
               'ninth_eight_weights':{str(k):dict(zip(S.PILLARS,map(float,v[1]))) for k,v in decisions.items()}})
        prev_base.extend(next_base);prev_ability.extend(next_ab)
        print('Stage139 Stage138',q,'completed',len(testing),'races; OOF',len(prev_base),flush=True)
        del base,abank
        gc.collect()
    write_csv(out/'stage139-138-fourarm-results.csv.gz',records,True)
    return records,folds


def comparison(rows,archive137,archive138,out):
    a=old_predictions_csv(archive137,['FROZEN_D','D_RACETYPE_NLP'])
    b=old_predictions_csv(archive138,['FROZEN_D','D_138_EIGHT_PILLARS'])
    target_rids={str(p['race_id']) for p in rows if p['model']==MODEL_NAMES[0]}
    for model in MODEL_NAMES[1:]:
        cohort={str(p['race_id']) for p in rows if p['model']==model}
        if cohort!=target_rids:raise RuntimeError('Different Stage139 race cohorts for '+model)
    allrows=list(rows)
    templates={str(p['race_id']):p for p in rows if p['model']==MODEL_NAMES[0]}
    if len(templates)!=len(target_rids):raise RuntimeError('Duplicate or missing Stage137 challenger races')
    for rid in sorted(target_rids):
        for model,src in [('FROZEN_D',a),('D_RACETYPE_NLP',a),('D_138_EIGHT_PILLARS',b)]:
            obj=src.get((rid,model))
            if not obj:raise RuntimeError(f'Missing original historical baseline {rid} {model}')
            other=a.get((rid,'FROZEN_D'))
            if other and model=='FROZEN_D':
                ref=b.get((rid,'FROZEN_D'))
                if (str(obj['horse']),int(obj['won']))!=(str(ref['horse']),int(ref['won'])):
                    raise RuntimeError('FROZEN_D Stage137/Stage138 archive disagreement '+rid)
            template=templates[rid]
            old={**template,'model':model,'horse':obj['horse'],
                 'second_horse':obj['second_horse'],'won':int(obj['won']),
                 'rank2_won':int(obj['rank2_won']),'sp':num(obj['sp']),
                 'hpr_rank1':'','hpr_rank2':'','hpr_9th_weight':'',
                 'weight_group':'ARCHIVED_UNCHANGED','hpr_training_source':'NONE'}
            allrows.append(old)
    expected=len(target_rids)
    for model in (*MODEL_NAMES,'FROZEN_D','D_RACETYPE_NLP','D_138_EIGHT_PILLARS'):
        if sum(z['model']==model for z in allrows)!=expected:
            raise RuntimeError(f'Dropped historical paired races {model}')
    write_csv(out/'stage139-all-7-models-rank1-rank2.csv.gz',allrows,True)
    return allrows


def settle(rows):
    n=len(rows);won=sum(int(p['won']) for p in rows)
    priced=[p for p in rows if num(p.get('sp')) is not None and 1.<num(p['sp'])<1001.]
    ret=sum(float(p['sp']) for p in priced if int(p['won']))
    cap=sum(min(float(p['sp']),21.) for p in priced if int(p['won']))
    second=sum(int(p['rank2_won']) for p in rows)
    return {'races':n,'rank1_winners':won,'rank1_strike_pct':round(100*won/n,3) if n else None,
      'rank2_winners':second,'rank1_or_2_winners':won+second,
      'priced':len(priced),'settlement_price_coverage_pct':round(100*len(priced)/n,2) if n else None,
      'sp_roi_pct':round(100*(ret/len(priced)-1),3) if priced else None,
      'sp_capped20to1_roi_pct':round(100*(cap/len(priced)-1),3) if priced else None}


def summarize(allrows,out):
    by=defaultdict(list)
    for p in allrows:
        for group,val in [('ALL','ALL'),('YEAR',p['date'][:4]),('QUARTER',p['period']),
                          ('BROAD',p['broad']),('DISCIPLINE',p['discipline']),
                          ('RACE_TYPE',p['subtype']),('OFFICIAL_TIER',p['official_tier']),
                          ('COUNTRY',p['country']),('COURSE',p['course'])]:
            by[(p['model'],group,val)].append(p)
    summary=[{'model':m,'breakdown':b,'group':g,**settle(rs)}
             for (m,b,g),rs in sorted(by.items())]
    write_csv(out/'stage139-period-racetype-results.csv',summary)
    return summary


def run(args):
    import stage30_gap_aware_abcd as D
    frozen=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert frozen['chosen_combination']=='D-064-gap14'
    weights=np.asarray(D.candidates('D')[64],dtype=float);weights/=weights.sum()
    assert np.allclose([frozen['weights_pct'][k] for k in S.PILLARS],weights*100,atol=.011)
    S.FROZEN[:]=weights;S.CANDIDATES=S.candidate_weights()
    args.out.mkdir(parents=True,exist_ok=True)
    # Construct exact unchanged Stage137 cohort first. Reconstruct the full
    # archive only for horses that can enter these model comparisons; this
    # avoids caching 1988-2010 horses with no possible target race after 2023.
    r137,a137,ref137=S.construct(args.db,args.reference)
    target_horses={str(k) for r in r137 for k in r['identity']}
    preoff,meta=hpr_history(args.db,args.reference,args.out,target_horses)
    stats137=collections.Counter();stats138=collections.Counter()
    attach_hpr(r137,preoff,stats137)
    # A full archive may contain older races from 1988 onward but not every
    # career start; never label a runner as a confirmed debutant based solely
    # on missing recorded history. The originals remain unchanged.
    # Fail before expensive training if the historical baseline drifts.
    test={str(r['race_id']) for r in r137 if r['date']>='2024-01-01'}
    archived=old_predictions_csv(args.stage137,['FROZEN_D'])
    if test!={rid for rid,m in archived}:
        raise RuntimeError('Stage137 exact paired 2024-25 cohort mismatch')
    for r in r137:
        if r['date']<'2024-01-01':continue
        vals=r['x8']@S.mask_weights(S.FROZEN,'NH' if r['d_original_style']=='jumps' else 'FLAT')
        idx=int(np.argmax(vals));src=archived[(str(r['race_id']),'FROZEN_D')]
        if str(r['horse'][idx])!=str(src['horse']):
            raise RuntimeError('Stage137 baseline scorer mismatch '+str(r['race_id']))
    print('Stage139 Stage137 exact 25509-race baseline verified',flush=True)
    results137,fold137=test_137(r137,args.out)
    del r137;gc.collect()
    r138,a138,ref138=T._load_and_construct(args.db,args.reference)
    T.early_baseline_guard(r138,args.stage137)
    attach_hpr(r138,preoff,stats138)
    results138,fold138=test_138(r138,args.out)
    del r138;gc.collect()
    results=results137+results138
    allrows=comparison(results,args.stage137,args.stage138,args.out)
    summary=summarize(allrows,args.out)
    overall={m:next(z for z in summary if z['model']==m and z['breakdown']=='ALL')
             for m in ('FROZEN_D','D_RACETYPE_NLP','D_138_EIGHT_PILLARS',*MODEL_NAMES)}
    n=overall['FROZEN_D']['races']
    if n<20000 or any(z['races']!=n for z in overall.values()):
        raise RuntimeError('Missing races / invalid full-field comparison')
    report={'version':VERSION,'status':'PAPER_ONLY_FROZEN_PARENTS_UNCHANGED',
      'models':list(overall),'training_year':2023,'evaluation_quarters':[x[0] for x in S.PERIODS],
      'matched_races':n,'HPR_formula':'Stage86 original v0 and Stage134C full-field v3.1 class/weight/OR',
      'HPR_not_new_NLP_adjusted':True,'HPR_previous_calendar_dates_only':True,
      'HPR_from_exact_horse_and_race_ids':True,'HPR_missing_is_neutral':True,
      'seven_models_all_same_exact_races':True,
      'hpr_ability_fixed_lift':ABILITY_LIFT,'hpr_ninth_gamma_grid':list(NINTH_GRID),
      'stage137_source_audit':a137,'stage138_source_audit':a138,
      'hpr_stage137_coverage':dict(stats137),'hpr_stage138_coverage':dict(stats138),
      'hpr_reconstruction_audit':meta,'yearly_overall':overall,
      'folds':fold137+fold138,'live_models_modified':False,
      'caveats':['Stage137 is a single 50-feature learner, so its ninth-pillar challenger is an independently weighted HPR ranking score, not a nine-tree-per-pillar model.',
        'Stage138 ninth-pillar model has exactly nine scoring components and separately optimizes ninth weight on prior out-of-fold results.',
        'HPR v3.1 is reconstructed from prior UK/Irish racing results, not lifted from the historical live HPR ledger.',
        'HPR v3.1 can overlap with the existing Ability, Course, Distance and Ground evidence; compare in-Ability versus independent pillar to measure this.',
        'All weights/learners are selected using only previous quarterly outcomes. No future-date rating information enters earlier predictions.',
        'HPR original v3.1 gives nonfinishers low observed-performance scores; it is NOT the new incident-aware NLP rating.',
        'Previously seen 2024-25 performance periods; not pristine unseen prospective validation.',
        'Historic SP ROI uses retrospective SP, not executable pre-off bookmaker prices.',
        'The original frozen D recipe itself was historically chosen using 2024 performance data.']}
    (args.out/'stage139-summary.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print('STAGE139_OVERALL',json.dumps(overall),flush=True)
    return report


def self_test():
    assert len(S.PILLARS)==8 and len(MODEL_NAMES)==4
    assert START_HPR is None  # full archive minimum, never hardcoded 2015
    assert norm_hpr([65,80,float('nan')])[1]==2
    assert list(norm_hpr([65,80,float('nan')])[0])[-1]==0
    assert not norm_hpr([float('nan'),70.])[0].any()
    assert (1-NINTH_GRID[-1])>0 and ABILITY_LIFT>0
    x=[{'date':'2024-01-01','period':'2024Q1','model':'X','won':1,'sp':5.,'rank2_won':0},
       {'date':'2024-01-01','period':'2024Q1','model':'X','won':0,'sp':5.,'rank2_won':1}]
    assert settle(x)['rank1_strike_pct']==50 and settle(x)['sp_roi_pct']==150.
    # Same OOF method for an independent ninth pillar must never need final SP.
    hpr_r={'race_id':'A','subtype':'CHASE_HANDICAP','group':'CHASE','broad':'NH',
           'hpr_z':np.array([2.,-1.]),'winner_idx':0}
    evidence=[{'race':hpr_r,'base':np.array([-1.,1.])}]*100
    g,src,n,w=select_gamma(evidence,hpr_r)
    assert g>0 and n==100 and src=='CHASE_HANDICAP'
    assert set(T.PILLAR_FEATURES)==set(S.PILLARS)
    print('STAGE139 SELFTEST PASS: true HPR confidence, 8 vs 9 inputs, OOF 9th search, settlement')


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test')
    q=sub.add_parser('walkforward')
    q.add_argument('--db',type=pathlib.Path,required=True)
    q.add_argument('--reference',type=pathlib.Path,required=True)
    q.add_argument('--stage137',type=pathlib.Path,required=True)
    q.add_argument('--stage138',type=pathlib.Path,required=True)
    q.add_argument('--out',type=pathlib.Path,required=True)
    args=p.parse_args()
    if args.command=='self-test':return self_test()
    return run(args)


if __name__=='__main__':main()
