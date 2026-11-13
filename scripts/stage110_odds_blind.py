#!/usr/bin/env python3
"""Stage110: exactly one highest-probability selection per eligible race, no odds.

Historical mode replays immutable Stage109 walk-forward predictions from a 2023
seed, 2024 out-of-sample test, and three 2025 retraining checkpoints. Every
eligible race gets rank 1 irrespective of its price or predicted probability.
Starting price is loaded ONLY for post-race settlement summaries; it cannot
change an already-fixed selection. This does not retrain the Stage109 model.

Paper mode loads the same saved trained estimator and previous-day form state.
It does not open any odds file or call any value/fancied/price function.
"""
from __future__ import annotations
import argparse, datetime as dt, gzip, html, io, json, pathlib, sys
import numpy as np
import pandas as pd

PARENT_RUN_ID = 43407036838
VERSION = 'STAGE110_G3_WINNER_FIRST_ODDS_FREE_2026_11_13_V1'
MODEL = 'G3_WALKFORWARD_ERROR_LEARNING_2026_11_13_V1'
PERIODS = ['2024_PREQUENTIAL','2025_JAN_APR','2025_MAY_AUG','2025_SEP_DEC']
NO_PRICE_COLUMNS_FOR_SELECTION = ('sp_decimal','odds','opening_odds','quote_decimal','value_edge')


def save_json(p, payload):
    pathlib.Path(p).write_text(json.dumps(payload,indent=2,ensure_ascii=False,default=str)+'\n',encoding='utf8')


def select_rank1(data):
    # Selection is solely the model's existing probability ranking. We never
    # reference SP or any quote column in this function.
    key=['period','model','race_id']
    if data.empty or not {'rank','win_probability','race_id','period','model'}<=set(data):
        raise ValueError('Historical predictions are missing required model fields')
    x=data.loc[data['rank'].eq(1)].copy()
    if x.duplicated(key).any():raise ValueError('Multiple Rank 1 selections for the same model/race')
    if len(x)!=data.groupby(key).ngroups:
        raise ValueError('One or more eligible races are missing a Rank 1 horse')
    # Assert the highest model win chance wins within each race.
    maxima=data.groupby(key,sort=False)['win_probability'].max().rename('maximum')
    matched=x.join(maxima,on=key)
    if not np.allclose(matched.win_probability,matched.maximum,atol=1e-9,rtol=0):
        raise ValueError('Rank 1 is not the highest probability horse')
    return x.drop(columns='maximum',errors='ignore')


def sp_metrics(frame):
    # Price data are permissible here only after the selection has been locked.
    s=frame.loc[pd.to_numeric(frame['sp_decimal'],errors='coerce').gt(1)].copy()
    n=len(s)
    if not n:return dict(bets=0,wins=0,strike_rate_pct=None,roi_sp_pct=None,return_units=None,
                         profit_units=None,avg_sp_decimal=None,avg_selections_per_day=None)
    s['won']=s.win.astype(int)
    wins=int(s.won.sum())
    ret=float(s.loc[s.won.eq(1),'sp_decimal'].sum())
    return dict(bets=n,wins=wins,strike_rate_pct=round(100*wins/n,2),
                roi_sp_pct=round(100*(ret/n-1),2),return_units=round(ret,2),
                profit_units=round(ret-n,2),avg_sp_decimal=round(float(s.sp_decimal.mean()),2),
                avg_selections_per_day=round(n/s.date.nunique(),2))


def historical(args):
    p=pathlib.Path(args.predictions)
    if not p.is_file():raise FileNotFoundError(p)
    frame=pd.read_csv(p,compression='gzip' if p.suffix=='.gz' else 'infer',low_memory=False)
    if set(frame.period.dropna().unique())!=set(PERIODS):
        raise ValueError('Parent file is not exactly the original 2024/2025 learning schedule')
    # Don't re-run learning, calibrate probabilities, or apply a price/probability filter.
    # Physically DROP every price, the race outcome, and the old Fancied gate
    # before choosing anything. Rejoin settlement information ONLY AFTER
    # the model's one-per-race choice has been locked.
    protected=[*NO_PRICE_COLUMNS_FOR_SELECTION,'win','fancied']
    oddsless=frame.drop(columns=protected,errors='ignore')
    ranked=select_rank1(oddsless)
    matching=['period','model','race_id','rank']
    settlement=frame[matching+['sp_decimal','win']].copy()
    if settlement.duplicated(matching).any():raise ValueError('Ambiguous settlement records')
    ranked=ranked.merge(settlement,on=matching,how='left',validate='one_to_one')
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    selected_columns=['period','model','trained_through','date','race_id','course','race_name',
                      'discipline','region','surface_class','race_class_group','horse','identity',
                      'rank','win_probability','prior_runs','win','sp_decimal']
    ranked=ranked[[k for k in selected_columns if k in ranked.columns]].copy()
    ranked['selection_reason']='HIGHEST_MODEL_WIN_PROBABILITY__NO_ODDS_OR_THRESHOLDS'
    ranked.to_csv(out/'stage110-every-race-rank1.csv',index=False)

    def group_summary(columns,path):
        records=[]
        for key,group in ranked.groupby(columns,sort=False,dropna=False):
            if not isinstance(key,tuple):key=(key,)
            records.append(dict(zip(columns,key))|{'races_with_rank1':len(group),**sp_metrics(group)})
        pd.DataFrame(records).to_csv(out/path,index=False)
        return records
    summary=group_summary(['period','model'],'stage110-period-model-results.csv')
    by_discipline=group_summary(['period','model','discipline','surface_class'],
                                'stage110-discipline-surface-results.csv')
    by_type=group_summary(['period','model','discipline','race_class_group'],
                          'stage110-race-type-results.csv')
    by_day=group_summary(['period','model','date'],'stage110-daily-results.csv')
    # Retrospective price-bucket stats are diagnostics ONLY, not selection filters.
    prices=ranked[pd.to_numeric(ranked.sp_decimal,errors='coerce').gt(1)].copy()
    prices['sp_group_hindsight']=pd.cut(prices.sp_decimal,[1,2.0,3.5,6.,11.,21.,float('inf')],
        labels=['shorter_than_Evens_or_Evens','above_Evens_to_5_2','above_5_2_to_5_1',
                'above_5_1_to_10_1','above_10_1_to_20_1','above_20_1'],include_lowest=True)
    rows=[]
    for key,g in prices.groupby(['period','model','sp_group_hindsight'],observed=True):
        rows.append(dict(zip(['period','model','sp_group_hindsight'],key))|sp_metrics(g))
    pd.DataFrame(rows).to_csv(out/'stage110-price-bands-settlement-only.csv',index=False)
    # Diagnose winners overlooked by Rank1, without changing or filtering prior selections.
    missed=frame[(frame.win==1)&(frame['rank']>1)].copy()
    mcols=['period','model','date','race_id','course','race_name','discipline',
           'race_class_group','horse','rank','win_probability','sp_decimal']
    missed[[c for c in mcols if c in missed.columns]].to_csv(
        out/'stage110-winners-missed-by-rank1.csv',index=False)

    audit={'version':VERSION,'source_workflow_run':PARENT_RUN_ID,
           'training_policy':'Original Stage109 G3 2023 seed; learn from 2024, Jan-Apr 2025, May-Aug 2025; frozen within each evaluated block.',
           'selection_policy':'One Rank 1 (highest model-predicted win chance) per eligible race. No odds, no value filter, no min probability, no daily quota.',
           'retraining_performed_in_this_replay':False,
           'identical_frozen_predictions_used':True,
           'sp_usage':'POSTRACE ROI settlement ONLY, never selection, training or ranking',
           'selected_races_total_across_model_variants':len(ranked),
           'periods':PERIODS,'groups':summary,
           'restrictions':'Same Stage109 completeness requirements: verified fields and 4+ runners; not every actual race is eligible.',
           'warning':'Negative ROI on historical SP is not proof of any achievable price advantage.'}
    save_json(out/'stage110-odds-blind-audit.json',audit)
    notes=['# Model G3 — rank-one every eligible race, absolutely no odds-based filtering','',"""
Test reuses the **original locked Stage109 predictions** (not a newly trained, backfit model).
Original learning: train on 2023 -> predict 2024 -> learn -> predict Jan–Apr 2025 -> learn -> predict May–Aug -> learn -> predict Sep–Dec.
One horse per eligible race, selected only because it has the model's **highest predicted win probability**. No odds threshold, value rule, minimum model probability or arbitrary daily quota.
**Starting prices are joined only after the predictions to calculate hypothetical win ROI.** Historical SPs are not guaranteed achievable pre-off prices.
""",'## Headline results','', '| Period | Model | Settled | Winners | Win % | SP ROI |','|---|---|---:|---:|---:|---:|']
    for r in summary:
        notes.append(f"| {r['period']} | {r['model']} | {r['bets']:,} | {r['wins']:,} | {r['strike_rate_pct']:.2f}% | {r['roi_sp_pct']:+.2f}% |")
    notes+=['','## How to read the files','','- `stage110-every-race-rank1.csv`: One horse per eligible race and model snapshot.','- `stage110-period-model-results.csv`: Rank1 by training/test period and model.','- `stage110-discipline-surface-results.csv`: Flat/Chase/Hurdle/NH Flat and AW/Turf.','- `stage110-race-type-results.csv`: Handicaps and non-handicaps.','- `stage110-daily-results.csv`: All daily Rank1 returns.','- `stage110-price-bands-settlement-only.csv`: Ex-post SP distribution, NOT a betting filter.','- `stage110-winners-missed-by-rank1.csv`: Winners ranked 2+.','','This is a research-only replay; it does not write to any existing app model.']
    (out/'README.md').write_text('\n'.join(notes)+'\n',encoding='utf8')
    print(json.dumps({'selection_count':len(ranked),'summary':summary},indent=2,default=str))


def paper(args):
    import joblib
    import stage108_model_g2 as G
    import stage109_model_g3 as G3
    cutoff=G.stamp(args.cutoff) if args.cutoff else dt.datetime.now(dt.timezone.utc)
    if cutoff is None:raise ValueError('Invalid time cutoff')
    target=args.date or cutoff.date().isoformat()
    if target<cutoff.date().isoformat():raise ValueError('Cannot create retrospectively backdated paper selections')
    model=joblib.load(args.model)
    if model.get('version')!=G3.VERSION or model.get('features')!=G3.FEATURES or model.get('trained_through')!='2025-12-31':
        raise ValueError('Wrong frozen G3 model / training cutoff')
    state_obj=joblib.load(args.state)
    if state_obj.get('version')!=G3.VERSION or state_obj.get('asof_after_date','')>=target:
        raise ValueError('Saved form state is not strictly earlier than selected race date')
    state=state_obj['state']
    recent={}
    if args.hpr:
        recent=G.load_recent_hpr(state,args.hpr,state_obj['asof_after_date'],target)
    cards=json.loads(pathlib.Path(args.cards).read_text(encoding='utf8'))
    allr,rejected=G.score_card_rows(state,model['estimator'],1.,cards,cutoff)
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    if not allr.empty:
        allr=G.ranks(allr.loc[allr.date==target].copy())
        if allr.empty:allr=pd.DataFrame(columns=['date','race_id','horse','rank','win_probability'])
    else:allr=pd.DataFrame(columns=['date','race_id','horse','rank','win_probability'])
    top=allr[allr['rank']==1].copy()
    if not allr.empty and (top.race_id.nunique()!=allr.race_id.nunique() or top.duplicated('race_id').any()):
        raise ValueError('Invalid one-horse-per-race rank1 output')
    cols=['date','course','off_utc','race_id','race_type','discipline','region','surface_class',
          'race_class_group','horse','rank','win_probability','prior_runs']
    top=top.reindex(columns=cols)
    allr=allr.reindex(columns=cols)
    top.to_csv(out/'stage110-paper-one-pick-per-race.csv',index=False)
    allr.to_csv(out/'stage110-paper-all-horses.csv',index=False)
    audit={'version':VERSION,'target_date':target,'asof_utc':cutoff.isoformat(),
           'model_trained_through':model['trained_through'],'eligible_races':int(top.race_id.nunique()),
           'all_runners_scored':len(allr),'excluded_races':rejected,
           'recent_form_restore':recent,'uses_bookmaker_odds':False,'uses_value_filter':False,
           'uses_probability_cutoff':False,'uses_fancied_shortlist':False,
           'selection_rule':'Exactly one highest-probability model horse in every eligible race, no odds'}
    save_json(out/'stage110-paper-audit.json',audit)
    head=['date','course','off_utc','race_type','horse','win_probability','prior_runs']
    lines=['<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"/>',
        '<meta charset="utf-8"/><title>G3 Winner First — No Odds</title>',
        '<style>body{font:15px system-ui,sans-serif;margin:15px;color:#142432}h1{font-size:22px}p{color:#536579}table{border-collapse:collapse;width:100%}th,td{padding:9px 7px;border-bottom:1px solid #ddd;text-align:left}th{background:#edf1f4;position:sticky;top:0}td:nth-child(6){font-weight:650}div{overflow-x:auto}</style></head><body>',
        '<h1>G3 — Best chance in every eligible race</h1>',
        f'<p>Paper only. Decision: {html.escape(cutoff.isoformat())}. No bookmaker prices, no thresholds, no value bets.</p><div><table>',
        '<tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in head)+'</tr>']
    for r in top.sort_values(['date','off_utc','course']).to_dict('records'):
        values=[r.get(k,'') for k in head]
        values[5]=f'{100*float(values[5]):.1f}%' if pd.notna(values[5]) else ''
        lines.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in values)+'</tr>')
    lines.append('</table></div></body></html>')
    (out/'stage110-paper-mobile.html').write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps(audit,indent=2,default=str))


def self_test():
    sample=pd.DataFrame([
        {'period':'2024_PREQUENTIAL','model':'SEED_2023','race_id':'r1','rank':1,'win_probability':.1,'horse':'Longshot','sp_decimal':45.,'win':1,'date':'2024-01-01'},
        {'period':'2024_PREQUENTIAL','model':'SEED_2023','race_id':'r1','rank':2,'win_probability':.05,'horse':'Fav','sp_decimal':2.,'win':0,'date':'2024-01-01'},
    ])
    assert select_rank1(sample).iloc[0].horse=='Longshot'
    assert sp_metrics(select_rank1(sample))['roi_sp_pct']==4400.
    assert NO_PRICE_COLUMNS_FOR_SELECTION==('sp_decimal','odds','opening_odds','quote_decimal','value_edge')
    print('STAGE110 SELF-TEST PASSED: highest probability is selected without odds or minimum probability')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    s=p.add_subparsers(dest='cmd',required=True)
    s.add_parser('self-test')
    h=s.add_parser('historical');h.add_argument('--predictions',required=True);h.add_argument('--out',required=True)
    q=s.add_parser('paper');q.add_argument('--model',required=True);q.add_argument('--state',required=True)
    q.add_argument('--cards',required=True);q.add_argument('--date',default='')
    q.add_argument('--cutoff',default='');q.add_argument('--hpr',default='');q.add_argument('--out',required=True)
    a=p.parse_args()
    if a.cmd=='self-test':self_test()
    elif a.cmd=='historical':historical(a)
    else:paper(a)
if __name__=='__main__':main()
