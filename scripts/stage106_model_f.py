#!/usr/bin/env python3
"""Stage106 Model F: as-of value-overlay research, NEVER touches frozen D/E.

Historical fixture: Stage104 PREOFF MORNING 09:45 Ireland D_C (reconstructed)
base rank 1/2 + timestamped William Hill prices and the same-race favourite.
The training, validation and sealed holdout periods are disjoint, ordered dates.

IMPORTANT: Research-only. Stage104 D_C is a reconstruction, and exact parity
with the live D(c) scorer is not independently established. No ROI guarantee.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import pathlib
import re
import sys
import zipfile
from collections import defaultdict

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MODEL = "F_VALUE_DISAGREEMENT_V1"
TRAIN_END = "2026-11-01"
VAL_END = "2026-11-04"
HOLDOUT_END = "2026-11-07"
PRICE_MIN = 3.50  # decimal, 5/2 fractional
PRICE_MAX = 18.00 # decimal, 17/1 fractional
MIN_EDGE = 0.08  # +8% predicted EV needed after 2 percentage-point uncertainty haircut
MIN_PRICE_GAP = 1.10 # must not be the favourite; >=10% longer odds than fav
MAX_AGE_MIN = 120
FEATURES = ["log_price", "rank1", "handicap", "hurdle", "chase", "aw", "log_fav_gap", "wh_premium"]


def normal(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def utc(value):
    if not value or pd.isna(value):
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def day_of(ts):
    return str(ts)[:10]


def race_key(course, time):
    t = utc(time)
    if not t:
        return None
    clean_course = re.sub(r"\s*\((aw|ire|uk)\)", "", str(course), flags=re.I)
    return f"{t.strftime('%Y-%m-%d')}|{normal(clean_course)}|{t.strftime('%H:%M')}"


def race_type_flags(race_type):
    r = str(race_type or "").lower()
    return {
        "handicap": float("handicap" in r),
        "hurdle": float("hurdle" in r),
        "chase": float("chase" in r or "steeplechase" in r),
        "aw": float("all-weather" in r or " aw" in r or "(aw)" in r),
    }


def feature_frame(rows):
    d = pd.DataFrame(rows).copy()
    if d.empty:
        return pd.DataFrame(columns=FEATURES)
    for col in ("price", "fav_price", "median_price", "rank"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d["log_price"] = np.log(d["price"].clip(lower=1.01))
    d["rank1"] = (d["rank"] == 1).astype(float)
    d["log_fav_gap"] = np.log((d["price"] / d["fav_price"].clip(lower=1.01)).clip(.1, 100)).clip(0, 3)
    d["wh_premium"] = np.log((d["price"] / d["median_price"].clip(lower=1.01)).clip(.5, 2)).clip(-.5, .5)
    for name in ("handicap", "hurdle", "chase", "aw"):
        d[name] = d["race_type"].map(lambda t: race_type_flags(t)[name])
    return d[FEATURES].fillna(0.0).astype(float)


def read_history(file_path):
    p = pathlib.Path(file_path)
    if p.suffix == '.zip':
        with zipfile.ZipFile(p) as z:
            target = next((x for x in z.namelist() if x.endswith('stage104-all-ranked-selections.csv')), None)
            if not target:
                raise RuntimeError('Archive lacks the Stage104 per-selection CSV')
            raw = io.BytesIO(z.read(target))
            return pd.read_csv(raw, low_memory=False)
    return pd.read_csv(p, low_memory=False)


def historical_candidates(file_path):
    raw = read_history(file_path)
    required = {"date", "race_key", "model", "strategy", "price_scenario", "rank", "horse", "winner", "race_type", "morning_wh_price_decimal", "selection_cutoff_utc", "wh_quote_timestamp", "feature_quote_newest_utc"}
    missing = required - set(raw.columns)
    if missing:
        raise RuntimeError('Missing source columns: '+str(sorted(missing)))
    raw = raw[(raw.model == 'D_C') & (raw.price_scenario == 'MORNING_0945_IRELAND')]
    # Resolve a timestamped market favourite for a PAIR-OF-RACES baseline.
    favs = raw[(raw.strategy == 'MARKET_FAVOURITE') & (raw['rank'] == 1)][["race_key", "horse", "morning_wh_price_decimal", "winner"]]
    favs = favs.drop_duplicates('race_key').rename(columns={"horse":"fav_horse", "morning_wh_price_decimal":"fav_price", "winner":"fav_won"})
    base = raw[(raw.strategy == 'BASE') & (raw['rank'].isin([1,2]))].drop_duplicates(['race_key','rank']).copy()
    base = base.merge(favs,on='race_key',how='inner',validate='many_to_one')
    errors=[]; out=[]
    for _,x in base.iterrows():
        price = pd.to_numeric(x.get('morning_wh_price_decimal'),errors='coerce')
        median = pd.to_numeric(x.get('feature_price_median'),errors='coerce')
        favourite = pd.to_numeric(x.get('fav_price'),errors='coerce')
        cutoff=utc(x['selection_cutoff_utc'])
        quote=utc(x['wh_quote_timestamp'])
        feat=utc(x['feature_quote_newest_utc'])
        off=utc(str(x['date'])+'T'+str(x['off_utc'])+'+00:00')
        if not all((cutoff,quote,feat,off)) or quote>cutoff or feat>cutoff or cutoff>=off:
            errors.append((x['race_key'], 'LOOKAHEAD_OR_MISSING_TIMESTAMP'))
            continue
        if not np.isfinite(price) or price<=1 or not np.isfinite(median) or median<=1 or not np.isfinite(favourite) or favourite<=1:
            errors.append((x['race_key'], 'INVALID_PRICE'))
            continue
        age=(cutoff-quote).total_seconds()/60
        if age<0 or age>MAX_AGE_MIN:
            errors.append((x['race_key'], 'PRICE_STALE'))
            continue
        p={
          'date':x['date'],'race_key':x['race_key'],'course':x['course'],
          'off_utc':x['off_utc'],'race_type':x['race_type'],'discipline':x.get('discipline',''),
          'horse':x['horse'],'rank':int(x['rank']),'price':float(price),
          'median_price':float(median),'fav_price':float(favourite),
          'fav_horse':x['fav_horse'],'fav_won':int(x['fav_won']),
          'winner':int(x['winner']),'price_age_minutes':round(age,2),
          'bookmaker_count_horse':int(x.get('bookmaker_count_horse') or 0),
          'coverage':float(x.get('runner_price_coverage') or 0),
          'source':'Stage104 immutable 09:45 Ireland pre-off WH quotes; historical D_C RECONSTRUCTION',
          'source_cutoff':cutoff.isoformat(),
        }
        out.append(p)
    # Remove races with both BASE choices missing before model fitting or settlement.
    if not out:
        raise RuntimeError('No valid pre-off candidates; cannot train or report ROI')
    if len(errors)>5:
        print(f'Excluded {len(errors)} unsafe rows; first errors: {errors[:5]}',file=sys.stderr)
    return pd.DataFrame(out), errors


def fit_model(d):
    if d['race_key'].nunique()<55 or d['winner'].sum()<8:
        raise RuntimeError('Not enough chronological training evidence')
    mod=make_pipeline(StandardScaler(), LogisticRegression(C=.2, solver='liblinear',max_iter=500,random_state=106))
    mod.fit(feature_frame(d.to_dict('records')),d['winner'].astype(int))
    return mod


def predict_rows(d, model):
    if d.empty: return d.copy()
    s=d.copy()
    s['estimated_win_probability']=model.predict_proba(feature_frame(s.to_dict('records')))[:,1]
    # A fixed probability uncertainty buffer; NOT a true 95% confidence bound.
    s['conservative_win_probability']=np.maximum(0,s.estimated_win_probability-0.02)
    s['conservative_expected_roi']=s.conservative_win_probability*s.price-1
    s['odds_gap_to_fav']=s.price/s.fav_price
    s['candidate_ok']=(s.price.between(PRICE_MIN,PRICE_MAX)&
                      (s.odds_gap_to_fav>=MIN_PRICE_GAP)&
                      (s.bookmaker_count_horse>=3)&
                      (s.coverage>=.70)&
                      (s.conservative_expected_roi>=MIN_EDGE))
    return s


def select_races(scored):
    s=scored[scored.candidate_ok].copy()
    if s.empty: return s
    # One selection maximum per race; no arbitrary daily quota.
    return (s.sort_values(['conservative_expected_roi','estimated_win_probability','horse'],ascending=[False,False,True])
             .drop_duplicates('race_key').sort_values(['date','off_utc','course']).reset_index(drop=True))


def pnl(d):
    if d.empty: return 0.0
    return float(np.where(d.winner==1,d.price-1,-1).sum())


def roi_summary(d):
    n=len(d)
    if not n:return {'bets':0,'races':0,'wins':0,'strike_rate_pct':None,'profit_units':0,'roi_pct':None,'avg_decimal_odds':None,'active_days':0,'bets_per_active_day':0,'worst_day_units':None}
    profit=pnl(d)
    daily = d.assign(_profit=np.where(d.winner==1,d.price-1,-1)).groupby('date')['_profit'].sum()
    return {'bets':n,'races':int(d.race_key.nunique()),'wins':int(d.winner.sum()),
            'strike_rate_pct':round(100*float(d.winner.mean()),2),'profit_units':round(profit,2),
            'roi_pct':round(100*profit/n,2),'avg_decimal_odds':round(float(d.price.mean()),2),
            'active_days':int(d.date.nunique()),'bets_per_active_day':round(n/d.date.nunique(),2),
            'worst_day_units':round(float(daily.min()),2)}


def boot_date_ci(d,seed=106,repetitions=1200):
    if d.empty:return {'p_roi_positive':None,'roi_low_5pct':None,'roi_high_95pct':None}
    group=[g for _,g in d.groupby('date')]
    random=np.random.default_rng(seed)
    rois=[]
    for _ in range(repetitions):
        draws=random.integers(0,len(group),len(group))
        total_n=sum(len(group[i]) for i in draws)
        total_p=sum(pnl(group[i]) for i in draws)
        rois.append(total_p/total_n*100 if total_n else -100)
    return {'p_roi_positive':round(float(np.mean(np.array(rois)>0)),3),
            'roi_low_5pct':round(float(np.quantile(rois,.05)),1),
            'roi_high_95pct':round(float(np.quantile(rois,.95)),1)}


def save_csv(d,path):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    d.to_csv(path,index=False)


def run_backtest(args):
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    d,errors=historical_candidates(args.input)
    if d.date.min()>'2026-10-25' or d.date.max()<HOLDOUT_END:
        raise RuntimeError('Not the sealed 14-day input sample expected; refuse to move the split')
    train=d[d.date<=TRAIN_END]
    val=d[(d.date>TRAIN_END)&(d.date<=VAL_END)]
    test=d[(d.date>VAL_END)&(d.date<=HOLDOUT_END)]
    if min(train.date.nunique(),val.date.nunique(),test.date.nunique())<3:
        raise RuntimeError('Date split lacks at least 3 racing days per period')
    model=fit_model(train)
    joblib.dump({'model':model,'features':FEATURES,'model_name':MODEL,'trained_through':TRAIN_END,'source_sha_or_run':'Stage104 run 43128767572','parity':'RESEARCH RECONSTRUCTION; NOT CERTIFIED LIVE'},out/'stage106-fitted-calibrator.joblib')
    summaries={}; bydate=[]; bytype=[]; allsel=[]; same_race=[]
    for name,chunk in [('TRAIN_DIAGNOSTIC',train),('VALIDATION',val),('SEALED_HOLDOUT',test)]:
        scores=predict_rows(chunk,model)
        selected=select_races(scores)
        selected=selected.assign(split=name,profit_units=np.where(selected.winner==1,selected.price-1,-1))
        allsel.append(selected)
        stats=roi_summary(selected)
        stats.update(boot_date_ci(selected))
        summaries[name]=stats
        # A fair market benchmark: only those EXACT same races, identical date and book.
        fav=selected[['date','race_key','fav_horse','fav_price','fav_won']].drop_duplicates('race_key').copy()
        fav['horse']=fav.fav_horse;fav['price']=fav.fav_price;fav['winner']=fav.fav_won
        fav_stats=roi_summary(fav)
        summaries[name]['paired_favourite_roi_pct']=fav_stats['roi_pct']
        summaries[name]['paired_favourite_bets']=fav_stats['bets']
        for date,rows in selected.groupby('date'):
            bydate.append({'split':name,'date':date,**roi_summary(rows)})
        for rt,rows in selected.groupby('race_type'):
            bytype.append({'split':name,'race_type':rt,**roi_summary(rows)})
        for _,r in selected.iterrows():
            same_race.append({'split':name,'date':r.date,'race_key':r.race_key,
                              'selected':r.horse,'model_profit':r.profit_units,
                              'favourite':r.fav_horse,'favourite_profit':r.fav_price-1 if r.fav_won else -1})
    selections=pd.concat(allsel,ignore_index=True) if allsel else pd.DataFrame()
    save_csv(selections,out/'stage106-every-selection.csv')
    # Full-priced viable research slate, NOT a list of bets or a daily quota.
    slate=predict_rows(d,model)
    slate=slate[slate.price.between(PRICE_MIN,PRICE_MAX)&(slate.price/slate.fav_price>=MIN_PRICE_GAP)].copy()
    slate["reason_not_selected"] = np.where(slate.candidate_ok,"RACE_COMPETITION_OR_SELECTED", "INSUFFICIENT_VERIFIED_EXPECTED_VALUE_OR_COVERAGE")
    save_csv(slate,out/'stage106-nonfavourite-research-slate.csv')
    save_csv(pd.DataFrame(bydate),out/'stage106-by-day.csv')
    save_csv(pd.DataFrame(bytype),out/'stage106-by-race-type.csv')
    save_csv(pd.DataFrame(same_race),out/'stage106-vs-favourite-paired.csv')
    total_candidates=d.race_key.nunique()
    valid=summaries['VALIDATION'];held=summaries['SEALED_HOLDOUT']
    # Transparent no-live decision gate. No post-hoc relaxing when it fails.
    reasons=[]
    if valid['bets']<12: reasons.append('VALIDATION_FEWER_THAN_12_BETS')
    if held['bets']<16: reasons.append('HOLDOUT_FEWER_THAN_16_BETS')
    if valid['roi_pct'] is None or valid['roi_pct']<=0: reasons.append('VALIDATION_NOT_POSITIVE')
    if held['roi_pct'] is None or held['roi_pct']<=0: reasons.append('HOLDOUT_NOT_POSITIVE')
    if held['roi_pct'] is not None and held['paired_favourite_roi_pct'] is not None and held['roi_pct']<=held['paired_favourite_roi_pct']:
        reasons.append('DID_NOT_BEAT_SAME_RACE_FAVOURITE')
    if held['bets_per_active_day']<4: reasons.append('TOO_FEW_PICKS_PER_ACTIVE_DAY')
    # Source parity gate is ALWAYS required before promoting to live regardless of ROI.
    reasons.append('LIVE_D_C_PARITY_WITH_STAGE104_RECONSTRUCTION_NOT_VERIFIED')
    report={'model':MODEL,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'source':'Stage104 run 43128767572, fixed as-of D_C research replay',
            'date_window':'2026-10-25 through 2026-11-07: 14 days',
            'splits':{'train':'2026-10-25..'+TRAIN_END,'validation':'2026-11-02..'+VAL_END,'sealed_holdout':'2026-11-05..'+HOLDOUT_END},
            'parameters':{'minimum_odds':PRICE_MIN,'maximum_odds':PRICE_MAX,'min_ev':MIN_EDGE,
                          'fav_gap':MIN_PRICE_GAP,'price_age_limit_min':MAX_AGE_MIN,'form_rank_candidates':'D_C top2 only',
                          'no_daily_quota':True,'minimum_independent_books':3,'min_race_quote_coverage':.7},
            'coverage':{'candidate_rows':len(d),'unique_races':total_candidates,'timestamp_or_price_rejections':len(errors)},
            'results':summaries,'promotion_status':'RESEARCH_ONLY_NO_AUTOMATIC_LIVE',
            'live_gate_passed':not reasons,'blockers':reasons,
            'warnings':['Train, validation, and holdout labels are NOT given to the scorer except for fitting train. Holdout not used to pick settings.',
                        'This is a NEW strategy, tested after many previous failed trials. Do not treat the 14-day period as independent of the broader research programme.',
                        'D_C history is a reconstruction and differs from live D(c); feature parity must be demonstrated before live betting.',
                        'Profits use timestamped William Hill morning quotes, not verified executable Novibet prices.',
                        'No historical HPR v2 feature is smuggled into this model; missing features remain missing.',
                        'Positive short-sample ROI is not proof of future positive ROI. No result is guaranteed.']}
    (out/'stage106-audit.json').write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    readme=['# Model F — Multi-day audited backtest', '',
            'Status: **RESEARCH ONLY. NO LIVE APPROVAL.**','',
            'Source: Stage104 immutable morning quotes and reconstructed D(c) top-two research rankings.','',
            '| Split | Bets | Wins | ROI at observed WH odds | Fav ROI on those races | Average odds |',
            '|---|---:|---:|---:|---:|---:|']
    for label in summaries:
        r=summaries[label];readme.append(f"| {label} | {r['bets']} | {r['wins']} | {r['roi_pct']}% | {r['paired_favourite_roi_pct']}% | {r['avg_decimal_odds']} |")
    readme+=['','**Promotion blockers:** '+', '.join(reasons),
             '','This workflow does not modify Model D, D(c), E or app selections.',
             '','The expected value estimate is a research score and not a calibrated guarantee.']
    (out/'README.md').write_text('\n'.join(readme)+'\n',encoding='utf-8')
    print('\n'.join(readme))
    return 0


def live_candidates(dcraces, odds, chosen_day, asof, bookmaker):
    # Only store quoted runners with strictly pre-decision timestamps.
    quotes=defaultdict(list)
    all_rows=odds.get('prices',[])
    for p in all_rows:
        off=utc(p.get('race_time'))
        collected=utc(p.get('bookmaker_updated_at') or p.get('collected_at'))
        if not off or not collected or off.date().isoformat()!=chosen_day:
            continue
        if collected>asof or collected>off-dt.timedelta(minutes=5) or (asof-collected).total_seconds()>MAX_AGE_MIN*60 or collected>off:
            continue
        if p.get('stale'):
            continue
        val=p.get('decimal_odds')
        try: val=float(val)
        except (TypeError,ValueError):continue
        if not 1.01<=val<=1000:continue
        k=race_key(p.get('course'),p.get('race_time'))
        quotes[(k,normal(p.get('horse')))].append((normal(p.get('bookmaker')),val,collected))
    rows=[]; skips=defaultdict(int)
    for race in dcraces.get('races',[]):
        if race.get('date')!=chosen_day:continue
        off=utc(race.get('race_time'))
        if not off or off<=asof+dt.timedelta(minutes=5):
            skips['RACE_OFF_OR_TOO_SOON']+=1;continue
        if utc(race.get('published_at')) and utc(race.get('published_at'))>asof:
            skips['PREDICTIONS_AFTER_DECISION']+=1;continue
        k=race_key(race.get('course'),race.get('race_time'))
        runners=race.get('runners') or []
        if len(runners)<3:
            skips['SMALL_OR_MISSING_FIELD']+=1;continue
        byhorse={}
        for r in runners:
            q=quotes.get((k,normal(r.get('horse'))),[])
            # Unavailable or stale preferred bookmaker is NOT replaced by eventual SP.
            elig=[v for bk,v,ts in q if bk==normal(bookmaker)]
            dedup={}
            for bk,v,ts in sorted(q,key=lambda x:x[2]):dedup[bk]=v
            if not elig: continue
            byhorse[normal(r.get('horse'))]=(float(elig[-1]),float(np.median(list(dedup.values()))),len(dedup))
        coverage=len(byhorse)/len(runners)
        if coverage < .70:
            skips['NO_70_PERCENT_FIELD_PRICE_COVERAGE']+=1;continue
        fav_price=min(x[0] for x in byhorse.values())
        for r in runners:
            if r.get('rank') not in (1,2):continue
            info=byhorse.get(normal(r.get('horse')))
            if not info:skips['NO_PREFERRED_PRICE_FOR_TOP2']+=1;continue
            price,median,num_books=info
            if num_books<3:
                skips['FEWER_THAN_3_BOOKS_ON_HORSE']+=1;continue
            # A handicap flag from the race title is essential; generic 'Hurdle' isn't enough.
            typ=f"{race.get('race_type','')} {race.get('race_name','')}".strip()
            rows.append({'date':chosen_day,'race_key':k,'course':race.get('course'),
                         'off_utc':off.strftime('%H:%M'),'race_type':typ,'discipline':'',
                         'horse':r.get('horse'),'rank':int(r['rank']),'price':price,
                         'median_price':median,'fav_price':fav_price,
                         'fav_horse':'market favourite (not settled)','fav_won':0,
                         'winner':0,'bookmaker_count_horse':num_books,'coverage':coverage,
                         'source':'LIVE D(c) as-of-ranked, timestamped bookmaker prices',
                         'source_cutoff':asof.isoformat()})
    return pd.DataFrame(rows),dict(skips)


def run_live(args):
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    asof=utc(args.asof) if args.asof else dt.datetime.now(dt.timezone.utc)
    chosen_day=args.date or (asof+dt.timedelta(days=1)).date().isoformat()
    if chosen_day<asof.date().isoformat():
        raise SystemExit('Refusing live scoring of past dates: use backtest/verified preoff snapshots')
    dcr=json.loads(pathlib.Path(args.dc).read_text())
    odds=json.loads(pathlib.Path(args.odds).read_text())
    bundle=joblib.load(args.calibrator)
    if bundle.get('model_name')!=MODEL or bundle.get('features')!=FEATURES:
        raise RuntimeError('Wrong model artifact / unsafe feature-schema mismatch')
    rows,skips=live_candidates(dcr,odds,chosen_day,asof,args.bookmaker)
    if rows.empty:
        selected=pd.DataFrame(columns=['date','course','off_utc','race_type','horse','rank','price','estimated_win_probability','conservative_expected_roi'])
    else:
        selected=select_races(predict_rows(rows,bundle['model']))
    save_csv(selected,out/'stage106-tomorrow-paper-selections.csv')
    if rows.empty:
        slate=pd.DataFrame(columns=["date","course","off_utc","horse","rank","price","conservative_expected_roi"])
    else:
        slate=predict_rows(rows,bundle["model"])
        slate=slate[slate.price.between(PRICE_MIN,PRICE_MAX)&(slate.price/slate.fav_price>=MIN_PRICE_GAP)]
    save_csv(slate,out/'stage106-tomorrow-all-nonfavourite-candidates.csv')
    audit={'model':MODEL,'date':chosen_day,'decision_asof_utc':asof.isoformat(),
           'chosen_bookmaker':args.bookmaker,'paper_only':True,
           'LIVE_APPROVED':False,'candidate_rows':len(rows),'selections':len(selected),'excluded':skips,
           'trained_through':bundle['trained_through'],
           'warning':'Not an approval for real-money wagering. Research replay D(c) versus live D(c) parity unverified.'}
    (out/'stage106-live-audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit,indent=2))
    return 0




def run_diagnostic(args):
    """Independent earlier NH check. SP is settlement ONLY, never price input."""
    z=zipfile.ZipFile(args.input) if str(args.input).endswith('.zip') else None
    if z:
        fname=next((n for n in z.namelist() if n.endswith('stage97-all-settled-rank1-rank2.csv')),None)
        if not fname: raise RuntimeError('Stage97 diagnostic CSV missing')
        d=pd.read_csv(io.BytesIO(z.read(fname)),low_memory=False)
    else:
        d=pd.read_csv(args.input,low_memory=False)
    d=d[(d['settlement_status']=='PRICED_AND_SETTLED')&(d['rank'].isin([1,2]))].copy()
    d['sp_decimal']=pd.to_numeric(d['sp_decimal'],errors='coerce')
    d=d[(d['sp_decimal']>1)&(d['won'].isin([0,1]))]
    d['profit']=np.where(d.won==1,d.sp_decimal-1,-1)
    d['period']=np.where(d.date<='2026-11-05','EARLY_DIAGNOSTIC','LATE_DIAGNOSTIC')
    group=[]
    for (model,rank,period),g in d.groupby(['model','rank','period']):
        group.append({'model':model,'rank':int(rank),'period':period,'bets':len(g),'wins':int(g.won.sum()),
                      'roi_at_sp_pct':round(100*g.profit.mean(),2),
                      'actual_price_at_decision_known':False,'racing_days':g.date.nunique(),
                      'warning':'POST_RACE_SP_IS_SETTLEMENT_ONLY_NOT_PRE_OFF_AVAILABLE_PRICE'})
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    save_csv(pd.DataFrame(group),out/'stage106-independent-stage97-sp-diagnostic.csv')
    print('Independent Stage97 SP-only diagnostic; NOT a valid pre-off quote ROI test:')
    for row in group:
        if row['model'] in ('D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR') and row['rank']==1:
            print(row)
    return 0

def self_test():
    assert race_key("Galway (IRE)","2026-11-13T13:00:00Z")==race_key("Galway","2026-11-13T13:00:00Z")
    assert race_key('Market Rasen','2026-11-13T15:00:00Z')=='2026-11-13|marketrasen|15:00'
    assert race_type_flags('Novices Handicap Chase')['handicap']==1
    assert utc('2026-11-12T10:25:00Z')<utc('2026-11-12T10:40:00Z')
    print('Stage106 self test PASS')
    return 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    b=sub.add_parser('backtest');b.add_argument('--input',required=True);b.add_argument('--out',required=True)
    s=sub.add_parser('score');s.add_argument('--dc',required=True);s.add_argument('--odds',required=True)
    s.add_argument('--calibrator',required=True);s.add_argument('--out',required=True)
    s.add_argument('--date',default='');s.add_argument('--asof',default='');s.add_argument('--bookmaker',default='williamhill')
    f=sub.add_parser('diagnostic');f.add_argument('--input',required=True);f.add_argument('--out',required=True)
    sub.add_parser('self-test')
    args=p.parse_args()
    return {'backtest':run_backtest,'score':run_live,'diagnostic':run_diagnostic,'self-test':lambda a:self_test()}[args.cmd](args)

if __name__=='__main__':sys.exit(main())
