#!/usr/bin/env python3
"""Stage109: strictly walk-forward winner-error learning for UK/Irish racing.

A 2023-trained seed scores 2024 without any 2024 outcome in its weights;
2024's missed winners and wrong leaders inform retraining for Jan-Apr 2025.
Predict -> lock -> evaluate -> update at three fixed boundaries. Historical
starting prices are AFTER-RACE descriptive diagnostics, never model features or
claimed available bets. G2's trained-2024 weights remain a frozen comparator.

Read only from the Stage100 exact-ID historical archive / Stage108 G2 source.
Does not modify the Racing Hot Pots app, D, E or G2 workflows.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, html, io, json, math, pathlib, re, shutil
from dataclasses import dataclass
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import HistGradientBoostingClassifier

import stage108_model_g2 as G

VERSION='G3_WALKFORWARD_ERROR_LEARNING_2026_11_13_V1'
SEED_START='2023-01-01'
SEED_END='2023-12-31'
CHECKPOINTS=(
 ('2024_PREQUENTIAL', '2024-01-01','2024-12-31','2023-12-31'),
 ('2025_JAN_APR', '2025-01-01','2025-04-30','2024-12-31'),
 ('2025_MAY_AUG', '2025-05-01','2025-08-31','2025-04-30'),
 ('2025_SEP_DEC', '2025-09-01','2025-12-31','2025-08-31'),
)
FEATURES=list(G.FEATURES)
# Hyperparameters and update policy fixed BEFORE observing 2025 outcomes.
PARAMS=dict(max_iter=140,max_leaf_nodes=13,max_depth=5,learning_rate=.05,
            min_samples_leaf=90,l2_regularization=8,random_state=109)
ERROR_WIN_MULT=1.50     # Prior-period winner was ranked outside top 2.
ERROR_WRONG_TOP_MULT=1.20  # A prior-period losing Rank 1 was preferred.
FANCY_MIN_P=.16
FANCY_FIELD_FACTOR=1.35
FANCY_MIN_PRIOR_RUNS=2
MIN_PRICE=3.5
MAX_PRICE=21.0
MIN_VERIFIED_EV=.20
UTC=dt.timezone.utc


def tidy_data(df):
    """Reject incomplete races; no future result/price is used to rank."""
    df=df[(df.date>=SEED_START)&(df.date<='2025-12-31')].copy()
    if df.empty:raise RuntimeError('No 2023-2025 historical form entries')
    per=df.groupby('race_id').agg(n=('horse','size'),winners=('win','sum'),days=('date','nunique'))
    keep=per[(per.n>=4)&(per.winners==1)&(per.days==1)].index
    df=df[df.race_id.isin(keep)].copy()
    if len(df)<2000:raise RuntimeError('Insufficient complete UK/IE fields')
    if df[FEATURES].isna().all().any():
        bad=df[FEATURES].columns[df[FEATURES].isna().all()].tolist()
        raise RuntimeError('Entirely missing model features: '+str(bad))
    return df


def fitting(df, cutoff, error_weights=None):
    sample=df[(df.date>=SEED_START)&(df.date<=cutoff)].copy()
    if len(sample)<1000 or sample.race_id.nunique()<150:raise RuntimeError('Too little prior data to learn')
    w=np.ones(len(sample),dtype=float)
    if error_weights:
        # The keys are derived ONLY from earlier predictions and outcomes.
        vals=[error_weights.get((str(x.race_id),str(x.identity)),1.) for x in sample.itertuples()]
        w=np.asarray(vals,float)
    model=HistGradientBoostingClassifier(**PARAMS)
    model.fit(G.Xframe(sample),sample.win.astype(int),sample_weight=w)
    return model,{'training_through':cutoff,'training_races':int(sample.race_id.nunique()),
                  'training_horses':len(sample),'weighted_rows':int(np.sum(w!=1)),
                  'winning_weight_mean':round(float(w[sample.win.to_numpy()==1].mean()),4)}


def predict(model, frame, model_name, train_through, period):
    v=frame.copy()
    if v.empty:return v
    if str(v.date.min())<=str(train_through):
        raise AssertionError('Future leak: evaluation date must be STRICTLY later than training cutoff')
    if len(v.race_id.unique())!=len(v.groupby('race_id').date.first()):
        raise AssertionError('Race identities ambiguous')
    v['raw_probability']=model.predict_proba(G.Xframe(v))[:,1]
    v=G.softmax_races(v,1.)  # NO probability calibration, fixed temperature=1
    v=G.ranks(v)
    v['model']=model_name;v['period']=period;v['trained_through']=train_through
    v['fancied']=fancied_mask(v)
    return v


def fancied_mask(ranked):
    """Independent from bookmaker odds, SP or ROI; deliberately no per-day quota."""
    n=ranked.groupby('race_id')['horse'].transform('size').astype(float)
    threshold=np.maximum(FANCY_MIN_P,FANCY_FIELD_FACTOR/n)
    return ((ranked['rank']<=2)&(ranked['win_probability']>=threshold)&
            (ranked['prior_runs'].fillna(0)>=FANCY_MIN_PRIOR_RUNS))


def hard_example_weights(predictions):
    """Use ONLY a COMPLETED period's locked predictions and verified winners."""
    weights={};c=collections.Counter()
    for _,race in predictions.groupby('race_id'):
        leader=race[race['rank']==1]
        winners=race[race.win==1]
        if len(leader)!=1 or len(winners)!=1:continue
        leader=leader.iloc[0];winner=winners.iloc[0]
        if int(winner['rank'])>2:
            weights[(winner.race_id,str(winner.identity))]=ERROR_WIN_MULT
            c['missed_winners_outside_top2']+=1
        if int(leader.win)!=1:
            weights[(leader.race_id,str(leader.identity))]=ERROR_WRONG_TOP_MULT
            c['incorrect_top1']+=1
    return weights,dict(c)


def update_error_ledger(ledger,latest):
    new,audit=hard_example_weights(latest)
    # Later snapshots never retroactively overwrite how historical predictions
    # were generated. A key belongs to one fixed prior race only.
    if set(ledger).intersection(new):raise AssertionError('Duplicate historic race evidence')
    ledger.update(new)
    return audit


def sp_summary(r):
    if r.empty:return {'settled':0}
    settled=r[(r.sp_decimal>1)&r.sp_decimal.notna()]
    if settled.empty:return {'settled':0,'roi_sp_proxy_pct':None}
    n=len(settled);wins=int(settled.win.sum())
    returns=float(settled.loc[settled.win==1,'sp_decimal'].sum())
    pnl=(np.where(settled.win==1,settled.sp_decimal-1,-1)).astype(float)
    daily=pd.DataFrame({'date':settled.date,'pnl':pnl}).groupby('date')['pnl'].sum()
    equity=np.r_[0.,np.cumsum(daily.to_numpy())]
    dd=float((np.maximum.accumulate(equity)-equity).max())
    return {'selections':int(len(r)),'settled':n,'wins':wins,
            'strike_pct':round(100*wins/n,2),'roi_sp_proxy_pct':round(100*(returns/n-1),2),
            'avg_sp_decimal':round(float(settled.sp_decimal.mean()),2),
            'max_drawdown_sp_units':round(dd,2),
            'avg_bets_per_active_day':round(n/max(1,int(settled.date.nunique())),2)}


def period_summary(pred):
    out={'races':int(pred.race_id.nunique()),'race_days':int(pred.date.nunique()),
         'rank1':sp_summary(pred[pred['rank']==1]),
         'rank2':sp_summary(pred[pred['rank']==2]),
         'fancied':sp_summary(pred[pred.fancied]),
         'avg_fancied_per_day':round(float(pred.fancied.sum()/max(1,pred.date.nunique())),2),
         'first_date':str(pred.date.min()),'last_date':str(pred.date.max())}
    out['race_logloss']=round(float(G.race_logloss(pred)),5) if G.race_logloss(pred) is not None else None
    winners=pred[pred.win==1]
    out['winners_in_top2']=int((winners['rank']<=2).sum())
    out['winners_outside_top2']=int((winners['rank']>2).sum())
    out['top2_race_winner_coverage_pct']=round(100*out['winners_in_top2']/len(winners),2) if len(winners) else None
    out['rank1_favourite_check_only_sp_hindsight']='No odds were used; SP favourite is only an after-race diagnostic'
    return out


def diagnostics(pred,train_reference,cap=None):
    """Explain WHERE winners differed; NOT causal proof or future features."""
    errors=[];fcols=list(FEATURES)
    sig=train_reference[fcols].std(numeric_only=True).replace(0,np.nan)
    for _,r in pred.groupby('race_id'):
        w=r[r.win==1];leader=r[r['rank']==1]
        if len(w)!=1 or len(leader)!=1:continue
        w=w.iloc[0];l=leader.iloc[0]
        if w.horse==l.horse:continue
        deltas=[]
        for f in fcols:
            a,b=G.num(w[f]),G.num(l[f]);sd=G.num(sig.get(f))
            if a is None or b is None or not sd:continue
            change=(a-b)/sd
            if math.isfinite(change):deltas.append((abs(change),f,round(change,2)))
        deltas.sort(reverse=True)
        errors.append(dict(period=w.period,model=w.model,date=w.date,course=w.course,
                           race_id=w.race_id,discipline=w.discipline,race_class_group=w.race_class_group,
                           winner=w.horse,winner_actual_rank=int(w['rank']),
                           winner_prior_runs=int(w.prior_runs),winner_est_win_pct=round(100*w.win_probability,2),
                           wrongly_preferred=l.horse,wrong_pick_est_win_pct=round(100*l.win_probability,2),
                           wrong_pick_sp=l.sp_decimal,winner_sp=w.sp_decimal,
                           largest_feature_differences='; '.join(f'{f} {v:+.2f} SD' for _,f,v in deltas[:6])))
    errors.sort(key=lambda x:(x['period'],-x['wrong_pick_est_win_pct'],x['date']))
    return pd.DataFrame(errors if cap is None else errors[:cap])


FEATURE_GROUPS={
 'race_type_surface': ['field_size','surface_aw','surface_known','kind_hurdle','kind_chase',
                       'kind_flat','kind_nhflat','handicap','handicap_known','is_maiden','is_novice',
                       'is_nursery','is_graded','is_ireland'],
 'ratings_handicap_mark_age_weight': ['official_rating','age','weight_lbs','draw_fraction'],
 'prior_form_trend_margins': ['horse_runs','horse_quality5','horse_quality3','horse_trend',
                             'horse_win5','horse_last_days','horse_days_sd','horse_finish_gap3'],
 'ratings_speed_comments': ['horse_rpr3','horse_ts3','horse_comment3'],
 'course_ground_distance': ['distance_f','same_going_quality','same_dist_quality',
                            'same_course_quality','same_kind_quality','same_surface_quality'],
 'trainer_jockey': ['trainer_form60','trainer_n60','jockey_form60','jockey_n60','trainer_changed'],
 'breeding_sire': ['sire_trip_quality','sire_ground_quality','sire_n_trip','sire_n_ground'],
}

def feature_group_2024_audit(seed,frame,limit_races=400):
    """2024 OOS diagnostic; never tunes hyperparameters using 2025 outcomes.

    This does not establish causality: permuting correlated factors is a rough
    sensitivity audit, not permission to apply hindsight weights.
    """
    ids=np.array(sorted(frame.race_id.unique()))
    if not len(ids):return []
    rng=np.random.default_rng(2024)
    chosen=set(rng.choice(ids,size=min(limit_races,len(ids)),replace=False))
    sample=frame[frame.race_id.isin(chosen)].copy()
    x=G.Xframe(sample)
    def loss(inputs):
        tmp=sample[['race_id','win']].copy()
        tmp['raw_probability']=seed.predict_proba(inputs)[:,1]
        return float(G.race_logloss(G.softmax_races(tmp,1.)))
    base=loss(x);results=[]
    for family,features in FEATURE_GROUPS.items():
        alternatives=[]
        for repeat in range(2):
            altered=x.copy()
            perm=np.random.default_rng(2024+repeat*101+len(family)).permutation(len(altered))
            altered[features]=x[features].iloc[perm].to_numpy()
            alternatives.append(loss(altered))
        results.append({'feature_group':family,'2024_oos_races':len(chosen),
                        '2024_baseline_logloss':round(base,5),
                        'after_group_shuffle_logloss':round(float(np.mean(alternatives)),5),
                        'loss_increase_if_scrambled':round(float(np.mean(alternatives)-base),5),
                        'interpretation':'Positive loss increase suggests the group contributed predictive information; negative does not prove irrelevance.'})
    return results


def refit_for_paper(df,error_ledger):
    """Allowed after 2025 year-end: train up to Dec 2025 for 2026 paper use."""
    return fitting(df,'2025-12-31',error_ledger)


def evaluate(df,frozen=None,allow_small=False):
    """All choices of parameters are fixed before any 2025 comparison."""
    periods={p:df[(df.date>=start)&(df.date<=end)].copy() for p,start,end,_ in CHECKPOINTS}
    first=periods['2024_PREQUENTIAL']
    seed,seed_audit=fitting(df,SEED_END)
    seed_predictions=predict(seed,first,'SEED_2023','2023-12-31','2024_PREQUENTIAL')
    ledger={};first_errors=update_error_ledger(ledger,seed_predictions)
    forecast=[seed_predictions]
    training=[{'for':'2024_PREQUENTIAL','mode':'seed_2023',**seed_audit}]
    summaries={'2024_PREQUENTIAL':{'SEED_2023':period_summary(seed_predictions)}}
    model_2024=None
    for name,start,end,train_end in CHECKPOINTS[1:]:
        # Strictly prior period's results only; all historical features were
        # constructed on prior days by Stage108's independent form engine.
        uniform,u_meta=fitting(df,train_end)
        focused,f_meta=fitting(df,train_end,ledger)
        if name=='2025_JAN_APR':model_2024=focused
        src=periods[name]
        if frozen is not None:
            base=predict(frozen,src,'FROZEN_G2_2024','2024-12-31',name)
            forecast.append(base)
        elif name=='2025_JAN_APR':
            # An uncalibrated original 2024-only G2 baseline when exact bundle
            # is unavailable; paired comparison labels distinguish this.
            orig=df[(df.date>='2024-01-01')&(df.date<='2024-12-31')]
            bm=HistGradientBoostingClassifier(**{**PARAMS,'random_state':107})
            bm.set_params(**{'max_iter':140,'max_leaf_nodes':13,'max_depth':5,'learning_rate':.05,
                             'min_samples_leaf':90,'l2_regularization':8})
            bm.fit(G.Xframe(orig),orig.win.astype(int))
            frozen=bm
            forecast.append(predict(frozen,src,'FROZEN_G2_2024','2024-12-31',name))
        else:
            forecast.append(predict(frozen,src,'FROZEN_G2_2024','2024-12-31',name))
        p_uniform=predict(uniform,src,'ROLLING_UNIFORM',train_end,name)
        p_focus=predict(focused,src,'ROLLING_MISSED_WINNER',train_end,name)
        forecast.extend([p_uniform,p_focus]);training.extend([
            {'for':name,'mode':'uniform',**u_meta},
            {'for':name,'mode':'missed_winner',**f_meta}])
        summaries[name]={m:period_summary(x) for m,x in [
            ('FROZEN_G2_2024',forecast[-3]),('ROLLING_UNIFORM',p_uniform),
            ('ROLLING_MISSED_WINNER',p_focus)]}
        # ONLY now, after selections for this complete period are evaluated,
        # promote verified winner/loss feedback to future training weights.
        error_audit=update_error_ledger(ledger,p_focus)
        summaries[name]['learned_after_period_for_next_period']=error_audit
        if not allow_small and src.race_id.nunique()<500:raise RuntimeError('Insufficient distinct forward races in '+name)
    return forecast,ledger,training,summaries,seed_audit


def latest_research_report(out,df,forecasts,ledger,training,summaries,source_audit,reference_audit):
    allpred=pd.concat(forecasts,ignore_index=True)
    cols=['period','model','trained_through','date','race_id','course','race_name','discipline','region',
          'surface_class','race_class_group','horse','identity','rank','win_probability','fancied',
          'prior_runs','win','sp_decimal']+FEATURES
    allpred.reindex(columns=list(dict.fromkeys(cols))).to_csv(out/'stage109-all-horses-historical.csv.gz',index=False,compression='gzip')
    allpred[allpred.fancied].reindex(columns=list(dict.fromkeys(cols))).to_csv(out/'stage109-fancied-historical.csv',index=False)
    g=allpred[allpred.period!='2024_PREQUENTIAL'].copy()
    records=[]
    for (period,model,discipline,hcap),group in g[g['rank']==1].groupby(['period','model','discipline','race_class_group']):
        records.append({'period':period,'model':model,'discipline':discipline,'race_type':hcap,
                        **sp_summary(group)})
    pd.DataFrame(records).to_csv(out/'stage109-by-race-type.csv',index=False)
    missed=diagnostics(allpred,df[df.date<='2023-12-31'])
    missed.to_csv(out/'stage109-missed-winners-and-features.csv.gz',index=False,compression='gzip')
    feature_avail={str(k):round(float(df[k].notna().mean()*100),1) for k in FEATURES}
    report={'version':VERSION,'scope':'UK and Ireland exact race IDs, historical pre-race horse-form only',
            'initial_seed_training':'2023-01-01 to 2023-12-31',
            '2024_selections':'Out-of-time 2024 selections from model trained exclusively on 2023',
            'fixed_update_schedule':'End 2024; end Apr 2025; end Aug 2025; end Dec 2025 for future paper',
            'periods':summaries,'training_history':training,'feature_coverage_pct':feature_avail,
            'seed_results_feedback':summaries['2024_PREQUENTIAL']['SEED_2023'],
            'error_policy':{'missed_winner_weight':ERROR_WIN_MULT,'wrong_top1_weight':ERROR_WRONG_TOP_MULT,
                            'fixed_before_2025':True,'total_weighted_cases':len(ledger)},
            'price_policy':'NO bookmaker odds or SP in model inputs or fancied selection criteria; SP hindsight diagnostic only',
            'unverified_2025_bookmaker_roi':'NOT AVAILABLE: historical SP is a result, not pre-race bookmaker evidence',
            'holdout_note':'May-Aug and Sep-Dec are sequential forward tests after preceding blocks are used for learning; not static frozen-model holdouts',
            'feature_inventory':'Only fields materialised in verified Stage100/Stage108 historical feed. No imaginary unrestricted online access.',
            'source_audit':source_audit,'reference_audit':reference_audit,
            'no_live_model_changes':True,'promotion':'RESEARCH_ONLY'}
    G.write_json(out/'stage109-walkforward-audit.json',report)
    return report


def html_view(out,allpred):
    # Offline mobile-friendly review; no external scripts or services.
    show=['period','model','date','course','horse','rank','win_probability','fancied','win','sp_decimal']
    recent=allpred[allpred.period.isin(['2025_MAY_AUG','2025_SEP_DEC'])][show]
    recent=recent[(recent['rank']<=2)|recent['fancied']].tail(450).copy()
    def table(tab):
        v=recent if tab=='all' else recent[recent.fancied]
        tags=[]
        for x in v.itertuples(index=False):
            row=['<td>'+html.escape(str(d))+'</td>' for d in [x.period,x.model,x.date,x.course,x.horse,x.rank,
                  f'{x.win_probability*100:.1f}%', ('Yes' if x.fancied else 'No'),x.win,x.sp_decimal]]
            tags.append('<tr>'+''.join(row)+'</tr>')
        header=''.join('<th>'+html.escape(i)+'</th>' for i in show)
        return '<div class="sc"><table><thead><tr>'+header+'</tr></thead><tbody>'+''.join(tags)+'</tbody></table></div>'
    content='''<!doctype html><html lang="en"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Model G3 walk-forward audit</title><style>body{font:15px -apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#172036;margin:16px;background:#fff}
    .tabs{display:flex;gap:8px;margin:15px 0}.tabs button{background:#eee;border:0;border-radius:8px;padding:12px;font-weight:600}
    .tabs button.on{background:#183954;color:#fff}.sc{overflow-x:auto}table{border-collapse:collapse;white-space:nowrap;font-size:12px}
    td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}th{position:sticky;top:0;background:#eff2f5}</style>
    <h2>G3 historical selections</h2><p>SP is hindsight only. The lists below are pre-race odds-blind model rankings.</p>
    <div class="tabs"><button class="on" onclick="show('all',this)">All horses</button><button onclick="show('fancied',this)">G2/G3 Fancied</button></div>
    <section id="all">'''+table('all')+'</section><section id="fancied" hidden>'+table('fancied')+'''</section>
    <script>function show(t,b){document.querySelectorAll('section').forEach(x=>x.hidden=x.id!==t);
    document.querySelectorAll('button').forEach(x=>x.classList.remove('on'));b.classList.add('on')}</script></html>'''
    (out/'stage109-selection-view.html').write_text(content,encoding='utf8')


def walkforward(args):
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    ref,ra=G.load_reference(args.reference,first='2022-01-01',last='2025-12-31')
    # Critical: create the 2023 seed features. Stage108 by default emits only 2024+.
    before=G.TRAIN_START
    try:
        G.TRAIN_START=SEED_START
        df,state,audit,n=G.collect_historical(args.db,first='2022-01-01',last='2025-12-31',reference=ref)
    finally:G.TRAIN_START=before
    df=tidy_data(df)
    frozen=None
    if args.frozen:
        bundle=joblib.load(args.frozen)
        if bundle.get('version')!=G.VERSION or bundle.get('features')!=FEATURES:
            raise RuntimeError('Frozen G2 artifact version or feature mismatch')
        frozen=bundle['estimator']
        shutil.copyfile(args.frozen,out/'stage109-frozen-g2-original.joblib')
    forecast,ledger,training,summaries,seedmeta=evaluate(df,frozen=frozen)
    report=latest_research_report(out,df,forecast,ledger,training,summaries,audit,ra)
    seed,_=fitting(df,SEED_END)
    sensitivity=feature_group_2024_audit(seed,df[(df.date>='2024-01-01')&(df.date<='2024-12-31')])
    pd.DataFrame(sensitivity).to_csv(out/'stage109-2024-feature-group-audit.csv',index=False)
    report['2024_out_of_sample_feature_group_sensitivity']=sensitivity
    G.write_json(out/'stage109-walkforward-audit.json',report)
    # 2025 season can now be used for a later prospective 2026 paper model.
    future,meta=refit_for_paper(df,ledger)
    joblib.dump({'version':VERSION,'estimator':future,'temperature':1.,'features':FEATURES,
                 'trained_through':'2025-12-31','feedback_policy':'fixed_hard_example_weights',
                 'audit':meta},out/'stage109-future-form-model.joblib')
    joblib.dump({'version':VERSION,'asof_after_date':'2025-12-31','state':state},
                out/'stage109-prior-form-state.joblib',compress=3)
    allpred=pd.concat(forecast,ignore_index=True)
    html_view(out,allpred)
    summary=['# Stage109: G3 winner-error learning — research only','',
            '2024 selections came from a 2023 seed, not from a model trained on 2024 outcomes.',
            '2025 learning checkpoints are strictly after each completed evaluation period.',
            'No odds or SP were ever used in candidate ranking or Fancied selection.','',
            '| Period | Model | Rank 1 wins/bets | ROI at SP (hindsight only) | Fancied bets | Fancied SP ROI |',
            '|---|---|---:|---:|---:|---:|']
    for p,models in summaries.items():
        for model,v in models.items():
            if not isinstance(v,dict) or 'rank1' not in v:continue
            r=v['rank1'];f=v['fancied']
            summary.append(f"| {p} | {model} | {r.get('wins')}/{r.get('settled')} | {r.get('roi_sp_proxy_pct')}% | {f.get('settled')} | {f.get('roi_sp_proxy_pct')}% |")
    summary.extend(['','Do not treat SP proxy as achievable bookmaker odds; no value strategy can be approved without pre-off quotes.',
                    'G2 Fancied and G3 Fancied are horse-win-chance shortlists, NOT bets.'])
    (out/'README.md').write_text('\n'.join(summary)+'\n',encoding='utf8')
    print('\n'.join(summary))


def _paper_dashboard(out, tables):
    """Portable iPhone-friendly interactive HTML, all locally generated."""
    tabs=[];sections=[]
    columns=['model','date','course','off_utc','horse','rank','win_probability','prior_runs','quote_decimal','value_edge']
    for key,title,frame in tables:
        tabs.append(f'<button onclick="show(\'{key}\',this)">{html.escape(title)}</button>')
        header=''.join('<th>'+html.escape(k.replace('_',' ').title())+'</th>' for k in columns)
        values=[]
        if not frame.empty:
            for _,r in frame.head(1200).iterrows():
                cells=[]
                for col in columns:
                    val=r.get(col,'')
                    if col=='win_probability' and G.num(val) is not None:val=f'{100*float(val):.1f}%'
                    elif col=='value_edge' and G.num(val) is not None:val=f'{100*float(val):+.1f}%'
                    elif pd.isna(val):val=''
                    cells.append('<td>'+html.escape(str(val))+'</td>')
                values.append('<tr>'+''.join(cells)+'</tr>')
        if not values:values.append('<tr><td colspan="10">No qualifying horses in the current snapshot</td></tr>')
        sections.append(f'<section id="{key}" hidden><div class="scroll"><table><thead><tr>{header}</tr></thead><tbody>{"".join(values)}</tbody></table></div></section>')
    doc='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Model G3 | Paper research</title><style>
        body{font:15px -apple-system,BlinkMacSystemFont,Arial,sans-serif;margin:16px;color:#17202a}
        .row{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}button{border:1px solid #ccd5e0;border-radius:9px;padding:12px;background:#fff;color:#17202a;font-weight:650}
        button.on{background:#163d58;color:white}.scroll{overflow-x:auto}table{border-collapse:collapse;white-space:nowrap;font-size:12px}
        th,td{padding:9px;border-bottom:1px solid #ddd;text-align:left}th{background:#eee;position:sticky;top:0}
        p{line-height:1.5;color:#52606e}</style></head><body><h2>Odds-blind racing research</h2><p>All Horses, G2 Fancied, G3 Fancied, and independently price-qualified research. Fancied means stronger model-estimated win chance, not guaranteed profit.</p><div class="row">'''+"".join(tabs)+'''</div>'''+"".join(sections)+'''<script>
        function show(id,button){document.querySelectorAll('section').forEach(x=>x.hidden=(x.id!==id));document.querySelectorAll('button').forEach(x=>x.classList.remove('on'));button.classList.add('on')}
        document.querySelector('button').click();</script></body></html>'''
    (out/'stage109-paper-mobile-views.html').write_text(doc,encoding='utf8')


def paper(args):
    cutoff=G.stamp(args.cutoff) if args.cutoff else dt.datetime.now(UTC)
    if cutoff is None:raise RuntimeError('Invalid decision timestamp')
    target=args.date or cutoff.date().isoformat()
    if target<cutoff.date().isoformat():raise RuntimeError('Retrospective paper scoring forbidden')
    model=joblib.load(args.model)
    if model.get('version')!=VERSION or model.get('features')!=FEATURES or model.get('trained_through')>='2026-01-01':
        raise RuntimeError('G3 model version/date mismatch')
    g2=joblib.load(args.g2_frozen)
    if g2.get('version')!=G.VERSION or g2.get('features')!=FEATURES:
        raise RuntimeError('Original G2 artifact was not the frozen verified model')
    state_obj=joblib.load(args.state)
    if state_obj.get('version')!=VERSION or state_obj.get('asof_after_date')>=target:
        raise RuntimeError('Future/incorrect form state')
    state=state_obj['state'];recent={}
    if args.hpr:recent=G.load_recent_hpr(state,args.hpr,state_obj['asof_after_date'],target)
    cards=json.loads(pathlib.Path(args.cards).read_text())
    odds=json.loads(pathlib.Path(args.odds).read_text())
    scored,rejected=G.score_card_rows(state,model['estimator'],1.,cards,cutoff)
    baseline,baseline_reject=G.score_card_rows(state,g2['estimator'],1.,cards,cutoff)
    def tidy(frame,name):
        if frame.empty:return pd.DataFrame(columns=['model','date','race_id','horse','win_probability','fancied'])
        frame=G.ranks(frame[frame.date==target].copy())
        if frame.empty:return frame.assign(model=pd.Series(dtype=str),fancied=pd.Series(dtype=bool))
        frame['model']=name;frame['fancied']=fancied_mask(frame)
        return frame
    scored=tidy(scored,'G3_LEARNED');baseline=tidy(baseline,'G2_FROZEN')
    picks,miss=G.match_market(scored,odds,args.bookmaker,cutoff)
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    allhorses=pd.concat([baseline,scored],ignore_index=True)
    g2_fancied=baseline[baseline.fancied].copy()
    g3_fancied=scored[scored.fancied].copy()
    allhorses.to_csv(out/'stage109-paper-all-horses.csv',index=False)
    g2_fancied.to_csv(out/'stage109-paper-g2-fancied.csv',index=False)
    g3_fancied.to_csv(out/'stage109-paper-g3-fancied.csv',index=False)
    if not picks.empty:picks.to_csv(out/'stage109-paper-price-qualified.csv',index=False)
    else:pd.DataFrame(columns=['date','race_id','horse','win_probability','quote_decimal','value_edge']).to_csv(out/'stage109-paper-price-qualified.csv',index=False)
    _paper_dashboard(out,[('all','All horses',allhorses),('g2','G2 Fancied',g2_fancied),
                           ('g3','G3 Fancied',g3_fancied),('value','Price-qualified',picks)])
    report={'version':VERSION,'asof_utc':cutoff.isoformat(),'target_day':target,'bookmaker':args.bookmaker,
            'ranked_races_g3':int(scored.race_id.nunique()) if not scored.empty else 0,
            'ranked_races_g2':int(baseline.race_id.nunique()) if not baseline.empty else 0,
            'g2_fancied_horses':len(g2_fancied),'g3_fancied_horses':len(g3_fancied),
            'quoted_value_opportunities':len(picks),'form_state_source':recent,
            'race_exclusions_g3':rejected,'race_exclusions_g2':baseline_reject,'quote_exclusions':miss,
            'note':'Odds-free independent full-field rank and chance-based G2/G3 Fancied. Value requires exact pre-off prices. Paper only.'}
    G.write_json(out/'stage109-paper-audit.json',report)
    print(json.dumps(report,indent=2,default=str))


def self_test():
    G.self_test()
    # synthetic fixture asserts no intra-year lookahead and that a 2024 error
    # may only affect the learning weights AFTER the 2024 selection was locked.
    rng=np.random.default_rng(109);items=[]
    for year,nr in [(2023,220),(2024,220),(2025,300)]:
        for k in range(nr):
            d=dt.date(year,1,1)+dt.timedelta(days=k%340)
            rid=f'{year}-{k:04d}';winner=k%6
            for j in range(6):
                row={f:(float(rng.normal(0,1)) if f not in ('field_size','prior_runs') else (6 if f=='field_size' else 3)) for f in FEATURES}
                row.update(date=d.isoformat(),race_id=rid,horse=f'H{year}_{k}_{j}',
                           identity=f'I|{year}|{k}|{j}',win=int(j==winner),sp_decimal=6.,
                           course='Test',race_name='Test Handicap',discipline='flat',
                           race_class_group='Handicap',surface_class='Turf',region='GB',prior_runs=3)
                items.append(row)
    df=tidy_data(pd.DataFrame(items))
    seed,meta=fitting(df,'2023-12-31')
    pred=predict(seed,df[df.date.str.startswith('2024')],'SEED_2023','2023-12-31','2024_PREQUENTIAL')
    assert len(pred) and pred.date.min()>'2023-12-31'
    wt,a=hard_example_weights(pred)
    assert len(wt)>0 and a['incorrect_top1']>0
    frozen,_=fitting(df,'2024-12-31')
    try:predict(frozen,df[df.date.str.startswith('2024')],'LEAK','2024-12-31','bad')
    except AssertionError:pass
    else:raise AssertionError('Temporal cutoff leak not caught')
    v=predict(frozen,df[df.date.str.startswith('2025')],'FROZEN','2024-12-31','2025_JAN_APR')
    assert len(v) and v.groupby('race_id').win_probability.sum().between(.99999,1.00001).all()
    assert not v.fancied.isna().any()
    assert {'sp_decimal','opening_odds','odds','market_price'}.isdisjoint(FEATURES)
    allpred,ledger,trainmeta,sums,seedaudit=evaluate(df,frozen=frozen,allow_small=True)
    assert set(sums)=={'2024_PREQUENTIAL','2025_JAN_APR','2025_MAY_AUG','2025_SEP_DEC'}
    assert 'FROZEN_G2_2024' in sums['2025_SEP_DEC']
    assert 'ROLLING_MISSED_WINNER' in sums['2025_SEP_DEC']
    assert len(ledger)>0 and len(trainmeta)>=7
    feature_audit=feature_group_2024_audit(seed,df[df.date.str.startswith('2024')],limit_races=25)
    assert len(feature_audit)==len(FEATURE_GROUPS) and all('loss_increase_if_scrambled' in x for x in feature_audit)
    print('Stage109 self-test passed: prequential 2024, locked 2025, missed winners, strictly prior-date, odds-blind fancied, all-race normalisation')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('self-test')
    b=sub.add_parser('walk-forward');b.add_argument('--db',required=True)
    b.add_argument('--reference',required=True);b.add_argument('--frozen');b.add_argument('--out',required=True)
    s=sub.add_parser('paper');s.add_argument('--model',required=True);s.add_argument('--g2-frozen',required=True);s.add_argument('--state',required=True)
    s.add_argument('--cards',required=True);s.add_argument('--odds',required=True)
    s.add_argument('--hpr');s.add_argument('--date');s.add_argument('--cutoff');s.add_argument('--bookmaker',default='williamhill')
    s.add_argument('--out',required=True)
    args=p.parse_args()
    if args.cmd=='self-test':self_test()
    elif args.cmd=='walk-forward':walkforward(args)
    else:paper(args)
if __name__=='__main__':main()
