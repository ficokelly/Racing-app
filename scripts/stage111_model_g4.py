#!/usr/bin/env python3
"""Stage111 / G4 specialist versus locked G3, strictly odds-blind research.

2023 only -> genuinely out-of-sample 2024 -> update on 2024 errors ->
Jan-Apr 2025 -> update -> May-Aug -> update -> Sep-Dec. G4 specialists
are trained separately for Flat turf, Flat AW, hurdles and chases; optional
handicap/nonhandicap sub-specialists require enough PRIOR races. NH Flat,
unknown surfaces and sparse populations fall back to the pooled model.

Comparisons use the immutable Stage109 G3 predictions and identical race IDs.
SP is NEVER an input to any model or selector, only a hindsight ROI proxy.
No live app files are ever written.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import html
import json
import pathlib
import shutil
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

import stage108_model_g2 as G
import stage109_model_g3 as G3

VERSION='G4_SPECIALIST_OOS_2026_11_13_V1'
FEATURES=list(G.FEATURES)
CHECKPOINTS=G3.CHECKPOINTS
MIN_DISC_RACES=170
MIN_SUB_RACES=330
# Set before seeing results. Specialists use stronger regularisation than pooled.
SPECIAL_PARAMS={**G3.PARAMS,'max_iter':120,'max_leaf_nodes':9,'max_depth':4,
                'learning_rate':.05,'min_samples_leaf':70,'l2_regularization':14,
                'random_state':111}
BLEND_POOLED=.45
BLEND_DISC=.40
BLEND_TYPE=.15
ALLOWED_GROUPS=('FLAT_TURF','FLAT_AW','HURDLE','CHASE')


def classify(d:pd.DataFrame)->pd.DataFrame:
    """Use only already-recorded pre-race metadata; unknowns never treated as turf."""
    a=d.copy()
    kind=a['discipline'].fillna('unknown').astype(str).str.lower()
    surface=a['surface_class'].fillna('Unknown').astype(str).str.upper()
    grp=np.select([
        (kind=='flat')&(surface=='TURF'),
        (kind=='flat')&(surface=='AW'),
        kind=='hurdle',kind=='chase',
    ],['FLAT_TURF','FLAT_AW','HURDLE','CHASE'],default='POOLED_FALLBACK')
    a['g4_group']=grp
    ct=a['race_class_group'].fillna('Unknown').astype(str)
    ct=ct.where(ct.isin(['Handicap','Non-handicap']),'Unknown')
    a['g4_type']=ct
    a['g4_subgroup']=a['g4_group']+'|'+ct
    for race,rows in a.groupby('race_id',sort=False):
        if rows.g4_group.nunique()!=1 or rows.g4_type.nunique()!=1:
            raise RuntimeError('Contradictory pre-race category inside race '+str(race))
    return a


def get_weights(frame:pd.DataFrame,ledger:dict)->np.ndarray:
    if not ledger:return np.ones(len(frame))
    # Race and horse keys exist ONLY for errors observed before this checkpoint.
    return np.asarray([ledger.get((str(rid),str(ident)),1.)
                       for rid,ident in zip(frame.race_id,frame.identity)],dtype=float)


def fit_one(frame,ledger,is_specialist:bool):
    if frame.win.nunique()!=2:raise ValueError('Training data requires winners and losers')
    params=(SPECIAL_PARAMS if is_specialist else G3.PARAMS)
    model=HistGradientBoostingClassifier(**params)
    model.fit(G.Xframe(frame),frame.win.astype(int),sample_weight=get_weights(frame,ledger))
    return model


def bank_fit(full:pd.DataFrame,cutoff:str,ledger:dict,*,small=False):
    hist=full[(full.date>=G3.SEED_START)&(full.date<=cutoff)].copy()
    if hist.empty or (not small and hist.race_id.nunique()<700):
        raise RuntimeError('Insufficient prior history by '+cutoff)
    if hist['date'].max()>cutoff:raise AssertionError('Lookahead in train rows')
    bank={'pooled':fit_one(hist,ledger,False),'disc':{},'sub':{},
          'trained_through':cutoff,'training_races':int(hist.race_id.nunique()),
          'eligible_group_counts':{},'eligible_sub_counts':{}}
    reqd=12 if small else MIN_DISC_RACES
    reqs=10 if small else MIN_SUB_RACES
    for key,part in hist.groupby('g4_group',sort=True):
        n=part.race_id.nunique()
        bank['eligible_group_counts'][key]=int(n)
        if key not in ALLOWED_GROUPS or n<reqd:continue
        bank['disc'][key]=fit_one(part,ledger,True)
    for key,part in hist.groupby('g4_subgroup',sort=True):
        n=part.race_id.nunique()
        bank['eligible_sub_counts'][key]=int(n)
        disc,cat=key.split('|',1)
        if disc not in bank['disc'] or cat=='Unknown' or n<reqs:continue
        bank['sub'][key]=fit_one(part,ledger,True)
    return bank


def safe_prob(model,frame:pd.DataFrame)->np.ndarray:
    return np.clip(model.predict_proba(G.Xframe(frame))[:,1],1e-5,1-1e-5)


def bank_raw_prob(bank:dict,frame:pd.DataFrame):
    """Return pure specialist and prior-fixed hierarchical mix for each runner."""
    n=len(frame)
    pooled=safe_prob(bank['pooled'],frame)
    pure=pooled.copy();blended=pooled.copy()
    disc_coverage=np.zeros(n,dtype=bool);type_coverage=np.zeros(n,dtype=bool)
    for key,indices in frame.groupby('g4_group',sort=False).indices.items():
        if key not in bank['disc']:continue
        vals=safe_prob(bank['disc'][key],frame.iloc[indices])
        disc_coverage[indices]=True
        pure[indices]=vals
        blended[indices]=BLEND_POOLED*pooled[indices]+(1-BLEND_POOLED)*vals
        for sub,subidx in frame.iloc[indices].groupby('g4_subgroup',sort=False).indices.items():
            if sub not in bank['sub']:continue
            original=np.asarray(indices)[subidx]
            vsub=safe_prob(bank['sub'][sub],frame.iloc[original])
            type_coverage[original]=True
            blended[original]=(BLEND_POOLED*pooled[original]+
                               BLEND_DISC*pure[original]+BLEND_TYPE*vsub)
    return pure,blended,disc_coverage,type_coverage


class BankEstimator:
    """Adapter for G2's paper feature scorer; no market data is read."""
    def __init__(self,bank,mode='BLENDED'):
        self.bank=bank;self.mode=mode
    def predict_proba(self,X):
        # G2 passes numerical features only; derive exactly the same groups
        # from kind flags, surface_aw and handicap indicators.
        frame=X.copy()
        if not hasattr(frame,'columns'):
            frame=pd.DataFrame(frame,columns=FEATURES)
        flat=frame.kind_flat.fillna(0).to_numpy()>0.5
        aw=frame.surface_aw.to_numpy(dtype=float)
        known=frame.surface_known.fillna(0).to_numpy()>0.5
        kind=np.select([flat&known&(aw<.5),flat&known&(aw>.5),
                        frame.kind_hurdle.fillna(0).to_numpy()>.5,
                        frame.kind_chase.fillna(0).to_numpy()>.5],
                       ['FLAT_TURF','FLAT_AW','HURDLE','CHASE'],default='POOLED_FALLBACK')
        hcap=frame.handicap.to_numpy(dtype=float)
        hknown=frame.handicap_known.fillna(0).to_numpy()>.5
        cl=np.where(hknown,np.where(hcap>.5,'Handicap','Non-handicap'),'Unknown')
        frame['g4_group']=kind;frame['g4_type']=cl
        frame['g4_subgroup']=frame.g4_group+'|'+frame.g4_type
        pure,blend,_,_=bank_raw_prob(self.bank,frame)
        pp=blend if self.mode=='BLENDED' else pure
        return np.column_stack([1-pp,pp])


def score(frame,bank,period,train_through,*,small=False):
    if frame.empty:raise RuntimeError('No races to score')
    if str(frame.date.min())<=train_through:
        raise AssertionError('Evaluation period overlaps training: '+period)
    pure,blended,seen_disc,seen_type=bank_raw_prob(bank,frame)
    results={}
    for label,raw in [('G4_SPECIALIST',pure),('G4_BLENDED',blended)]:
        p=frame.copy()
        p['raw_probability']=raw
        p=G.ranks(G.softmax_races(p,1.))
        p['period']=period;p['model']=label;p['trained_through']=train_through
        p['disc_specialist_available']=seen_disc
        p['type_specialist_available']=seen_type
        results[label]=p
        if not np.allclose(p.groupby('race_id').win_probability.sum(),1,atol=1e-5):
            raise AssertionError('Race probabilities not normalised')
        if not small and len(p[p['rank']==1])!=p.race_id.nunique():
            raise AssertionError('Not exactly one top pick per race')
    return results


def baseline_load(path):
    cols=['period','model','date','race_id','horse','identity','rank','win_probability','win','sp_decimal']
    d=pd.read_csv(path,compression='infer',usecols=cols,low_memory=False)
    d=d[((d.period=='2024_PREQUENTIAL')&(d.model=='SEED_2023'))|
        ((d.period!='2024_PREQUENTIAL')&(d.model=='ROLLING_MISSED_WINNER'))]
    if d.empty:raise RuntimeError('Empty / incorrect locked G3 baseline')
    if d.duplicated(['period','race_id','identity']).any():
        raise RuntimeError('G3 original benchmark has duplicated horse identities')
    return d


def validate_matched(period,g4,g3):
    """Strict paired comparison: no claiming improvement on different races."""
    old=g3[g3.period==period]
    if old.empty:raise RuntimeError('Baseline lacks period '+period)
    ids=set(g4.race_id.unique())
    old_ids=set(old.race_id.unique())
    if ids!=old_ids:
        raise RuntimeError(f'Exact race mismatch {period}: G4-only {len(ids-old_ids)} G3-only {len(old_ids-ids)}')
    a=g4[['race_id','identity','win']].copy()
    b=old[['race_id','identity','win']].copy()
    if a.duplicated(['race_id','identity']).any() or b.duplicated(['race_id','identity']).any():
        raise RuntimeError('Duplicate horse identity within race')
    check=a.merge(b,on=['race_id','identity'],how='outer',indicator=True,suffixes=('_g4','_g3'))
    if not (check._merge=='both').all() or not (check.win_g4==check.win_g3).all():
        raise RuntimeError('Horse IDs or winners disagree with immutable G3 baseline')
    if (g4.groupby('race_id').win.sum()!=1).any():
        raise RuntimeError('More or fewer than one verified winner in a race')
    return old.copy()


def summary(picks):
    head=picks[picks['rank']==1]
    eligible=head[head.sp_decimal.notna() & (head.sp_decimal>1)]
    n=len(eligible);w=int(eligible.win.sum())
    ret=float(eligible.loc[eligible.win==1,'sp_decimal'].sum())
    profit=ret-n
    return {'races':int(head.race_id.nunique()),'settled':n,'wins':w,
            'strike_pct':round(100*w/n,2) if n else None,
            'roi_sp_pct':round(100*profit/n,2) if n else None,
            'profit_per_1u':round(profit,2),'average_bets_per_day':round(n/max(1,eligible.date.nunique()),2),
            'winner_top2_pct':round(100*(picks[(picks.win==1)&(picks['rank']<=2)].shape[0])/max(1,picks[picks.win==1].shape[0]),2),
            'race_logloss':round(float(G.race_logloss(picks)),5) if G.race_logloss(picks) is not None else None}


def run_experiment(df,baseline,*,small=False):
    df=classify(G3.tidy_data(df))
    baseline=baseline.copy()
    periods={p:df[(df.date>=start)&(df.date<=end)].copy() for p,start,end,_ in CHECKPOINTS}
    ledger={};outputs=[];metrics=[];training=[]
    for period,start,end,train_end in CHECKPOINTS:
        frame=periods[period]
        if frame.empty:raise RuntimeError('Missing historical test period '+period)
        bank=bank_fit(df,train_end,ledger,small=small)
        if not bank['disc'] and not small:raise RuntimeError('Zero discipline specialists trained')
        scored=score(frame,bank,period,train_end,small=small)
        original=validate_matched(period,scored['G4_BLENDED'],baseline)
        # The G3 original is rechecked on the same races, including winner identity.
        if original['rank'].dtype=='object':original['rank']=pd.to_numeric(original['rank'])
        baseline_sum=summary(original)
        records=[{'period':period,'model':'G3_ORIGINAL',**baseline_sum}]
        for label,p in scored.items():
            records.append({'period':period,'model':label,**summary(p)})
            outputs.append(p)
        metrics.extend(records)
        training.append({'period':period,'trained_through':train_end,
                         'prior_train_races':bank['training_races'],
                         'available_discipline_models':sorted(bank['disc']),
                         'available_type_models':sorted(bank['sub']),
                         'prior_group_races':bank['eligible_group_counts'],
                         'prior_type_races':bank['eligible_sub_counts'],
                         'feedback_examples_applied':len(ledger)})
        # Only G4_BLENDED's locked predictions determine which *past* examples
        # receive extra training attention for the NEXT checkpoint.
        new,_=G3.hard_example_weights(scored['G4_BLENDED'])
        if ledger.keys()&new.keys():raise RuntimeError('Period overlap in feedback ledger')
        ledger.update(new)
    return pd.concat(outputs,ignore_index=True),pd.DataFrame(metrics),training,ledger,df


def report_outputs(out,allpred,metrics,training,ledger,baseline):
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    report={
        'model':VERSION,'scope':'Verified UK IE pre-race horse-form, no odds inputs',
        'predictions':'Exactly one Rank 1 per complete >=4 runner race; all prices allowed',
        'schedule':'Train on 2023 -> 2024 -> update -> Jan-Apr 2025 -> update -> May-Aug -> update -> Sep-Dec',
        'loss_function':'sklearn binomial classification; each race probabilities renormalised',
        'fixed_blend':{'pooled':BLEND_POOLED,'discipline':BLEND_DISC,'race_type':BLEND_TYPE},
        'missing_specialist_fallback':'Pooled when unseen/sparse; no invented turf classification',
        'specialists':list(ALLOWED_GROUPS),'minimum_prior_races':{'discipline':MIN_DISC_RACES,'type':MIN_SUB_RACES},
        'train_checkpoints':training,'total_past_mistakes_weighted':len(ledger),
        'SP_limitation':'Starting price known after race ONLY; not achievable pre-race ROI',
        'tests_frozen_before_results':True,'writes_to_live_app':False,
        'periods':metrics.to_dict('records'),
    }
    G.write_json(out/'stage111-g4-audit.json',report)
    metrics.to_csv(out/'stage111-period-head-to-head.csv',index=False)
    sels=allpred[allpred['rank']<=2].copy()
    keys=['period','model','date','course','race_id','race_name','horse','identity','rank','win_probability',
          'discipline','surface_class','race_class_group','g4_group','g4_type','trained_through',
          'disc_specialist_available','type_specialist_available','win','sp_decimal']
    sels.reindex(columns=keys).to_csv(out/'stage111-ranked1-and2.csv.gz',index=False,compression='gzip')
    # Include G3's original rank 1 selection on exactly the same race keys.
    b=baseline[baseline['rank']==1][['period','race_id','horse','win','sp_decimal']].rename(columns={'horse':'g3_rank1','win':'g3_won','sp_decimal':'g3_sp_decimal'})
    g4=allpred[(allpred.model=='G4_BLENDED')&(allpred['rank']==1)].copy()
    paired=g4.merge(b,on=['period','race_id'],validate='one_to_one')
    paired['changed_from_g3']=(paired.horse!=paired.g3_rank1)
    paired['g4_better_than_g3']=(paired.win>paired.g3_won)
    paired['g3_better_than_g4']=(paired.win<paired.g3_won)
    # Paired SP ROI uses EXACTLY the same settled races for both models.
    common=paired[(paired.sp_decimal>1)&(paired.g3_sp_decimal>1)].copy()
    common['g4_return']=common.win*common.sp_decimal
    common['g3_return']=common.g3_won*common.g3_sp_decimal
    comparable=[]
    for period,part in common.groupby('period'):
        n=len(part)
        comparable.append({'period':period,'matched_settled_races':n,
                           'g3_wins':int(part.g3_won.sum()),'g4_wins':int(part.win.sum()),
                           'g3_roi_sp_pct':round(100*(part.g3_return.sum()/n-1),2),
                           'g4_roi_sp_pct':round(100*(part.g4_return.sum()/n-1),2),
                           'g4_minus_g3_roi_points':round(100*(part.g4_return.sum()-part.g3_return.sum())/n,2)})
    pd.DataFrame(comparable).to_csv(out/'stage111-paired-settled-roi.csv',index=False)
    paired.reindex(columns=['period','date','course','race_id','race_name','discipline','surface_class',
                            'race_class_group','horse','g3_rank1','win','g3_won','sp_decimal','g3_sp_decimal',
                            'win_probability','g4_group','g4_type','disc_specialist_available',
                            'type_specialist_available','changed_from_g3',
                            'g4_better_than_g3','g3_better_than_g4']).to_csv(out/'stage111-race-by-race-paired.csv.gz',index=False,compression='gzip')
    # The G3 baseline receives the EXACT SAME pre-race classification as G4.
    categories=allpred[allpred.model=='G4_BLENDED'][['period','race_id','discipline','surface_class','race_class_group']].drop_duplicates(['period','race_id'])
    baseline_with_category=baseline.merge(categories,on=['period','race_id'],validate='many_to_one')
    baseline_with_category['model']='G3_ORIGINAL'
    compared=pd.concat([allpred,baseline_with_category],ignore_index=True)
    rows=[]
    for (period,model,discipline,surface,cat),p in compared.groupby(['period','model','discipline','surface_class','race_class_group'],dropna=False):
        rows.append({'period':period,'model':model,'discipline':discipline,'surface':surface,
                     'race_category':cat,**summary(p)})
    pd.DataFrame(rows).to_csv(out/'stage111-by-disc-surface-racetype.csv',index=False)
    dif=paired.groupby('period').agg(races=('race_id','size'),changed=('changed_from_g3','sum'),
            g4_extra_winners=('g4_better_than_g3','sum'),g3_extra_winners=('g3_better_than_g4','sum')).reset_index()
    dif.to_csv(out/'stage111-win-swaps-versus-g3.csv',index=False)
    text=['# Stage111: G4 specialist vs G3 — strictly paired out-of-time experiment','',
          '**All rankings were made without bookmaker odds or SP.** The displayed ROI is post-race SP only.','',
          '| Period | Model | Wins / settled | Strike | SP ROI |',
          '|---|---|---:|---:|---:|']
    for x in metrics.itertuples():
        text.append(f'| {x.period} | {x.model} | {x.wins} / {x.settled} | {x.strike_pct}% | {x.roi_sp_pct}% |')
    text.extend(['','### Locked paired win-swap comparison vs G3','',
                '| Period | Races | Different top picks | G4 extra winners | G3 extra winners |',
                '|---|---:|---:|---:|---:|'])
    for x in dif.itertuples():text.append(f'| {x.period} | {x.races} | {x.changed} | {x.g4_extra_winners} | {x.g3_extra_winners} |')
    text.extend(['','### Identically settled G3 vs G4 blended comparison','',
                 '| Period | Common settled races | G3 SP ROI | G4 SP ROI | Difference |',
                 '|---|---:|---:|---:|---:|'])
    for x in comparable:
        text.append(f"| {x['period']} | {x['matched_settled_races']} | {x['g3_roi_sp_pct']}% | {x['g4_roi_sp_pct']}% | {x['g4_minus_g3_roi_points']:+.2f} pp |")
    text.extend(['','Do not promote a model on SP proxy alone. Validate separately on prospective immutable picks and available pre-off prices.',
                 'No changes were made to existing G2/G3 models or the Racing Hot Pots app.'])
    (out/'README.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return report


def historical(args):
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    reference,refaudit=G.load_reference(args.reference,first='2022-01-01',last='2025-12-31')
    # Reuse exactly the G3 feature-engine / verified historical source.
    prev=G.TRAIN_START
    try:
        G.TRAIN_START=G3.SEED_START
        df,state,source_audit,_=G.collect_historical(args.db,first='2022-01-01',last='2025-12-31',reference=reference)
    finally:G.TRAIN_START=prev
    baseline=baseline_load(args.baseline)
    full,metric,train,ledger,df=run_experiment(df,baseline)
    report=report_outputs(out,full,metric,train,ledger,baseline)
    # Refit only after the last 2025 test is fully over. Paper scoring is separate.
    paper_bank=bank_fit(df,'2025-12-31',ledger)
    joblib.dump({'version':VERSION,'trained_through':'2025-12-31','features':FEATURES,
                 'bank':paper_bank,'policy':'research_only_odds_blind','source_audit':source_audit},
                out/'stage111-paper-specialists.joblib',compress=3)
    joblib.dump({'version':VERSION,'asof_after_date':'2025-12-31','state':state},
                out/'stage111-prior-form-state.joblib',compress=3)
    report.update({'source_audit':source_audit,'reference_audit':refaudit})
    G.write_json(out/'stage111-g4-audit.json',report)
    print((out/'README.md').read_text())


def mobile_view(out,allhorses,rank1):
    def tab(fr):
        rows=[]
        cols=['model','date','course','off_utc','horse','rank','win_probability','discipline','surface_class','race_class_group']
        for r in fr.head(600).to_dict('records'):
            cells=[]
            for c in cols:
                v=r.get(c,'')
                if c=='win_probability' and G.num(v) is not None:v=f'{v*100:.1f}%'
                cells.append('<td>'+html.escape(str(v))+'</td>')
            rows.append('<tr>'+''.join(cells)+'</tr>')
        h=''.join('<th>'+c.replace('_',' ').title()+'</th>' for c in cols)
        return '<div class="scroll"><table><thead><tr>'+h+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>'
    body='''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta charset="UTF-8"><title>G4 odds-blind selections</title>
    <style>body{font:15px -apple-system,BlinkMacSystemFont,Arial,sans-serif;background:#fff;color:#14243a;padding:12px}
    button{padding:11px;border-radius:7px;margin:5px;border:1px solid #ccc;background:#eee}.scroll{overflow-x:auto}
    table{border-collapse:collapse;font-size:12px;white-space:nowrap}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}
    th{background:#eee;position:sticky;top:0}</style></head><body><h2>G4 — odds-blind paper selections</h2>
    <p>No bookmaker odds used. Separate from the live app. Compare G3, pure specialist and blended specialist.</p>
    <button onclick="show('r')">Rank 1 per race</button><button onclick="show('a')">All horses</button>
    <section id="r">'''+tab(rank1)+'''</section><section id="a" hidden>'''+tab(allhorses)+'''</section>
    <script>function show(x){for(const id of ['r','a'])document.getElementById(id).hidden=id!==x}</script></body></html>'''
    (pathlib.Path(out)/'stage111-paper-mobile.html').write_text(body,encoding='utf-8')


def paper(args):
    cutoff=G.stamp(args.cutoff) if args.cutoff else dt.datetime.now(dt.timezone.utc)
    if cutoff is None:raise RuntimeError('Invalid decision clock')
    target=args.date or cutoff.date().isoformat()
    if target<cutoff.date().isoformat():raise RuntimeError('Retrospective paper scoring is not allowed')
    packed=joblib.load(args.model)
    if packed.get('version')!=VERSION or packed.get('features')!=FEATURES or packed.get('trained_through')>='2026-01-01':
        raise RuntimeError('Wrong G4 bundle or future-training leak')
    past=joblib.load(args.state)
    if past.get('version')!=VERSION or past.get('asof_after_date')>=target:raise RuntimeError('Form state is not prior to race day')
    state=past['state'];recent={}
    if args.hpr:recent=G.load_recent_hpr(state,args.hpr,past['asof_after_date'],target)
    cards=json.loads(pathlib.Path(args.cards).read_text())
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    frames=[];ex={}
    for label,mode in [('G4_SPECIALIST','PURE'),('G4_BLENDED','BLENDED')]:
        pred,rejected=G.score_card_rows(state,BankEstimator(packed['bank'],mode),1.,cards,cutoff)
        if not pred.empty:
            pred=G.ranks(pred[pred.date==target].copy())
            pred['model']=label;frames.append(pred)
        ex[label]=rejected
    if args.g3:
        prior=joblib.load(args.g3)
        if prior.get('version')!=G3.VERSION:raise RuntimeError('G3 version mismatch')
        pred,rejected=G.score_card_rows(state,prior['estimator'],1.,cards,cutoff)
        if not pred.empty:
            pred=G.ranks(pred[pred.date==target].copy())
            pred['model']='G3_ORIGINAL';frames.append(pred)
        ex['G3_ORIGINAL']=rejected
    if frames:
        allhorse=pd.concat(frames,ignore_index=True)
        top=allhorse[allhorse['rank']==1].copy()
    else:
        allhorse=pd.DataFrame(columns=['model','date','race_id','horse','rank','win_probability'])
        top=allhorse.copy()
    allhorse.to_csv(out/'stage111-paper-all-horses.csv',index=False)
    top.to_csv(out/'stage111-paper-rank1-per-race.csv',index=False)
    mobile_view(out,allhorse,top)
    report={'asof_utc':cutoff.isoformat(),'date':target,'version':VERSION,
            'rank1_per_model':{str(k):int(v) for k,v in top.model.value_counts().items()} if len(top) else {},
            'race_exclusions':ex,'recent_form':recent,'odds_input':'NONE',
            'status':'PAPER_ONLY_NOT_LIVE'}
    G.write_json(out/'stage111-paper-audit.json',report)
    print(json.dumps(report,indent=2,default=str))


def self_test():
    import random
    rng=np.random.default_rng(111)
    dates=[('2023-02-01',15),('2023-10-01',15),('2024-03-01',15),('2024-09-01',15),
           ('2025-03-01',15),('2025-07-01',15),('2025-10-01',15)]
    rows=[]
    for day,n in dates:
        for cat in ALLOWED_GROUPS:
            for i in range(n):
                rid=f'{day}|{cat}|{i}'
                for h in range(6):
                    f={c:float(rng.normal(0,1)) for c in FEATURES}
                    f['field_size']=6;f['horse_runs']=5
                    f.update(kind_flat=int(cat.startswith('FLAT')),kind_hurdle=int(cat=='HURDLE'),
                             kind_chase=int(cat=='CHASE'),kind_nhflat=0,
                             surface_aw=int(cat=='FLAT_AW'),surface_known=1,
                             handicap=int(i%2==0),handicap_known=1)
                    rows.append({**f,'date':day,'race_id':rid,'horse':f'Horse {h}',
                      'identity':f'{rid}|{h}','win':int(h==(i%6)),
                      'sp_decimal':float(2+h),'course':'Test','race_name':'Test Handicap',
                      'discipline':'flat' if cat.startswith('FLAT') else cat.lower(),
                      'surface_class':'AW' if cat=='FLAT_AW' else 'Turf',
                      'race_class_group':'Handicap' if i%2==0 else 'Non-handicap'})
    frame=classify(pd.DataFrame(rows))
    bank=bank_fit(frame,'2023-12-31',{},small=True)
    assert set(bank['disc'])==set(ALLOWED_GROUPS)
    assert set(bank['sub'])
    pred=score(frame[frame.date=='2024-03-01'],bank,'2024_PREQUENTIAL','2023-12-31',small=True)
    pure=pred['G4_SPECIALIST'];mix=pred['G4_BLENDED']
    assert not pure.empty and len(pure[pure['rank']==1])==60
    assert len(mix[mix['rank']==1])==60
    assert mix.win_probability.notna().all()
    assert G.race_logloss(mix)>0
    baseline=mix.copy();baseline['model']='SEED_2023';baseline['period']='2024_PREQUENTIAL'
    validate_matched('2024_PREQUENTIAL',mix,baseline)
    weights,_=G3.hard_example_weights(mix)
    updated=bank_fit(frame,'2024-12-31',weights,small=True)
    out=score(frame[frame.date=='2025-03-01'],updated,'2025_JAN_APR','2024-12-31',small=True)
    assert len(out['G4_BLENDED'][out['G4_BLENDED']['rank']==1])==60
    raw=BankEstimator(updated).predict_proba(G.Xframe(frame[frame.date=='2025-03-01'].head(6)))
    assert raw.shape==(6,2) and np.allclose(raw.sum(axis=1),1)
    assert {'sp_decimal','odds','opening_odds','market_price','bookmaker'} .isdisjoint(FEATURES)
    print('Stage111 G4 self-test OK: discipline & race-type specialists, prior-only updates, paired comparisons, one top per race, odds-blind, 2026-compatible scorer')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('self-test')
    h=sub.add_parser('historical');h.add_argument('--db',required=True);h.add_argument('--reference',required=True)
    h.add_argument('--baseline',required=True);h.add_argument('--out',required=True)
    q=sub.add_parser('paper');q.add_argument('--model',required=True);q.add_argument('--state',required=True)
    q.add_argument('--g3');q.add_argument('--hpr');q.add_argument('--cards',required=True)
    q.add_argument('--date');q.add_argument('--cutoff');q.add_argument('--out',required=True)
    args=p.parse_args()
    if args.cmd=='self-test':self_test()
    elif args.cmd=='historical':historical(args)
    else:paper(args)


if __name__=='__main__':main()
