#!/usr/bin/env python3
"""Stage107 / Model G: independent full-field, odds-blind historical form research.

Training, validation and held-out evaluation use only events BEFORE each race day.
All runner features are derived from PRIOR form; no contemporaneous SP, odds,
finishing rank, HPR score from the race being predicted, or future model rankings.

Historical SP ROI is a descriptive benchmark, NOT an executable price backtest.
Only strictly timestamped bookmaker quotes may be used for future value decisions.
The scoring and output are research-only, never writes to app/live model data.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import pathlib
import re
import sqlite3
import tempfile
import unicodedata
from collections import defaultdict, deque

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

VERSION = 'G_FORM_FIRST_2026_11_12_V1'
TRAIN_START, TRAIN_END = '2024-01-01', '2024-12-31'
VALID_START, VALID_END = '2025-01-01', '2025-04-30'
TEST1_START, TEST1_END = '2025-05-01', '2025-08-31'
TEST2_START, TEST2_END = '2025-09-01', '2025-12-31'
HISTORY_START = '2022-01-01'
DEFAULT_BOOK = 'williamhill'
MIN_BETS_PRICE, MAX_BETS_PRICE = 3.5, 21.0 # 5/2 to 20/1; NOT restricted to favourites
MIN_EV = .20
MIN_QUOTE_COVERAGE = .70
MAX_QUOTE_MIN = 120
MIN_PREOFF_MIN = 10
UTC = dt.timezone.utc

FEATURES = [
    'field_size','age','official_rating','weight_lbs','draw_fraction','distance_f',
    'surface_aw','kind_hurdle','kind_chase','kind_flat','handicap',
    'horse_runs','horse_quality5','horse_quality3','horse_trend','horse_win5',
    'horse_last_days','horse_days_sd','horse_rpr3','horse_ts3','horse_comment3',
    'horse_finish_gap3','same_going_quality','same_dist_quality','same_course_quality',
    'same_kind_quality','same_surface_quality','trainer_form60','trainer_n60',
    'jockey_form60','jockey_n60','trainer_changed','sire_trip_quality',
    'sire_ground_quality','sire_n_trip','sire_n_ground',
]


def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().upper()
    s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$', '',s)
    return re.sub('[^A-Z0-9]','',s)


def course_key(s):
    return norm(re.sub(r'\s*\((?:AW|IRE|GB)\)', '',str(s or ''), flags=re.I))


def num(x):
    try:
        f=float(x)
        return f if math.isfinite(f) else None
    except (ValueError,TypeError):return None


def day(s):
    try:return dt.date.fromisoformat(str(s or '')[:10])
    except (TypeError,ValueError):return None


def stamp(s):
    if not s:return None
    try:
        x=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return x.replace(tzinfo=UTC) if x.tzinfo is None else x.astimezone(UTC)
    except (ValueError,TypeError):return None


def off_time(s):
    t=stamp(s)
    if t:return t.strftime('%H:%M')
    x=str(s or '')
    return x[:5] if re.match(r'^\d{2}:\d{2}',x) else ''


def identity(r):
    horse=norm(r.get('horse') or r.get('horse_name'))
    sire=norm(r.get('sire'));dam=norm(r.get('dam'))
    if horse and sire and dam:return 'P|'+horse+'|'+sire+'|'+dam
    ident=str(r.get('horse_source_id') or r.get('horse_id') or '').strip()
    return 'ID|'+ident if ident and horse else ''


def category(r):
    label=' '.join(str(r.get(k) or '') for k in ('race_type','race_name','type')).lower()
    surface=str(r.get('surface') or '').lower()
    if 'chase' in label or 'steeple' in label:kind='chase'
    elif 'hurdle' in label:kind='hurdle'
    elif 'bumper' in label or 'nh flat' in label:kind='nhflat'
    else:kind='flat'
    return kind, ('all-weather' in surface or 'aw' in surface or 'standard' in surface or 'polytrack' in surface or 'tapeta' in surface),int('handicap' in label)


def going_band(s):
    s=str(s or '').lower()
    if 'heavy' in s:return 'heavy'
    if 'soft' in s or 'yield' in s:return 'soft'
    if 'firm' in s:return 'firm'
    if 'standard' in s or 'tapeta' in s or 'polytrack' in s:return 'standard'
    if 'good' in s:return 'good'
    return ''


def beaten_lengths(s):
    if num(s) is not None:return min(75.,max(0.,num(s)))
    s=str(s or '').lower().replace('lengths','').replace('length','').strip()
    look={'nk':.3,'neck':.3,'nse':.05,'nose':.05,'shd':.1,'hd':.2,'head':.2,'dist':40.,'distance':40.}
    if s in look:return look[s]
    s=s.replace('l','').strip()
    # Exact fractions and mixed fractions, not estimates for unsupported text.
    m=re.match(r'^(\d+)\s+(\d+)/(\d+)$',s)
    if m:return min(75.,int(m[1])+int(m[2])/max(1,int(m[3])))
    m=re.match(r'^(\d+)/(\d+)$',s)
    if m:return min(75.,int(m[1])/max(1,int(m[2])))
    return min(75.,float(s)) if re.match(r'^\d+(?:\.\d+)?$',s) else None


def comment_signal(s):
    """Small bounded adjustment to a PAST run, not an LLM inference of today's result."""
    t=str(s or '').lower()
    good=('stayed on','kept on','ran on','finishing well','strong finish','went clear','easily')
    bad=('weakened','no extra','never travelling','not fluent','pulled hard','hampered','tired')
    val=sum(p in t for p in good)-sum(p in t for p in bad)
    return max(-1.,min(1.,val/2))


def quality(r,n):
    pos=num(r.get('finish_pos') if 'finish_pos' in r else r.get('position'))
    status=str(r.get('outcome') or r.get('status') or '').lower().strip()
    if pos is None:
        if status in ('pu','f','ur','fell','unseated','pulled up','bd','rr','ro','refused','ran out'):return .02
        return None
    if pos<1 or pos>n or n<2:return None
    q=(n-pos)/(n-1)
    btn=beaten_lengths(r.get('beaten'))
    if btn is not None:q=.70*q+.30*math.exp(-btn/7.)
    return max(0.,min(1.,q))


def median(xs,default=None):
    xs=[v for v in xs if v is not None and math.isfinite(v)]
    return float(np.median(xs)) if xs else default


def mean(xs,default=None):
    xs=[v for v in xs if v is not None and math.isfinite(v)]
    return float(sum(xs)/len(xs)) if xs else default


def horse_bucket():return deque(maxlen=24)
def form_bucket():return deque(maxlen=500)
def sire_bucket():return deque(maxlen=250)

class FormState:
    def __init__(self):
        self.horses=defaultdict(horse_bucket)
        self.trainers=defaultdict(form_bucket)
        self.jockeys=defaultdict(form_bucket)
        self.sires=defaultdict(sire_bucket)
        self.events=0

    def features(self,r,asof,field_size):
        hid=identity(r)
        h=list(self.horses[hid]) if hid else []
        h=[x for x in h if x['date']<asof]
        h5=h[-5:];h3=h[-3:]
        kind,aw,hcap=category(r)
        g=going_band(r.get('going'))
        dist=num(r.get('distance_f'))
        c=course_key(r.get('course'))
        def cond(fn):
            vals=[x['q'] for x in h if x['q'] is not None and fn(x)]
            return (sum(vals[-8:])+1.5)/(len(vals[-8:])+3) if vals else .50
        def recent_rate(store,key):
            if not key:return (.12,0)
            obs=[]
            for x in reversed(store[key]):
                days=(asof-x['date']).days
                if days>60:break
                if days>0:obs.append(x)
            return ((sum(x['won'] for x in obs)+2.4)/(len(obs)+20),len(obs))
        trainer=norm(r.get('trainer'));jockey=norm(r.get('jockey'))
        t_rate,t_n=recent_rate(self.trainers,trainer)
        j_rate,j_n=recent_rate(self.jockeys,jockey)
        sire=norm(r.get('sire'))
        sprior=[x for x in self.sires[sire] if x['date']<asof] if sire else []
        sptrip=[x['q'] for x in sprior if x['q'] is not None and dist is not None and x['dist'] is not None and abs(x['dist']-dist)<=2.0]
        spgoing=[x['q'] for x in sprior if x['q'] is not None and g and x['going']==g]
        def shrink(v):return (sum(v[-250:])+4*.5)/(len(v[-250:])+4) if v else .50
        current_or=num(r.get('official_rating') or r.get('ofr'))
        age=num(r.get('age'))
        weight=num(r.get('weight_lbs') or r.get('lbs'))
        draw=num(r.get('draw'))
        qq=[x['q'] for x in h3 if x['q'] is not None]
        prev=[x['q'] for x in h[:-3][-5:] if x['q'] is not None]
        gap=[x['beaten'] for x in h3 if x['beaten'] is not None]
        scores={
         'field_size':field_size,'age':age,'official_rating':current_or,'weight_lbs':weight,
         'draw_fraction':draw/field_size if draw and field_size>0 else None,
         'distance_f':dist,'surface_aw':int(aw),'kind_hurdle':int(kind=='hurdle'),
         'kind_chase':int(kind=='chase'),'kind_flat':int(kind=='flat'),
         'handicap':hcap,'horse_runs':len(h),'horse_quality5':mean([x['q'] for x in h5]),
         'horse_quality3':mean(qq),'horse_trend':mean(qq,0.5)-mean(prev,0.5),
         'horse_win5':mean([x['won'] for x in h5],0),
         'horse_last_days':min(600,(asof-h[-1]['date']).days) if h else None,
         'horse_days_sd':float(np.std([(h[i]['date']-h[i-1]['date']).days for i in range(1,len(h))])) if len(h)>=3 else None,
         'horse_rpr3':mean([x['rpr'] for x in h3]),
         'horse_ts3':mean([x['ts'] for x in h3]),
         'horse_comment3':mean([x['comment'] for x in h3]),
         'horse_finish_gap3':mean(gap),
         'same_going_quality':cond(lambda x:g and x['going']==g),
         'same_dist_quality':cond(lambda x:dist is not None and x['dist'] is not None and abs(dist-x['dist'])<=2),
         'same_course_quality':cond(lambda x:c and x['course']==c),
         'same_kind_quality':cond(lambda x:x['kind']==kind),
         'same_surface_quality':cond(lambda x:x['aw']==aw),
         'trainer_form60':t_rate,'trainer_n60':t_n,
         'jockey_form60':j_rate,'jockey_n60':j_n,
         'trainer_changed':int(bool(h and trainer and h[-1]['trainer']!=trainer)),
         'sire_trip_quality':shrink(sptrip),'sire_ground_quality':shrink(spgoing),
         'sire_n_trip':len(sptrip),'sire_n_ground':len(spgoing),
        }
        return scores, len(h)

    def add(self,r,d,field_size):
        key=identity(r)
        pos=num(r.get('finish_pos') if 'finish_pos' in r else r.get('position'))
        status=str(r.get('outcome') or r.get('status') or '').lower()
        if status in ('nr','non-runner','nonrunner','withdrawn','scratched','dns'):
            return
        won=int(pos==1)
        kind,aw,_=category(r)
        e={
            'date':d,'q':quality(r,field_size),'won':won,
            'beaten':beaten_lengths(r.get('beaten')),
            'rpr':num(r.get('rpr')),'ts':num(r.get('topspeed')),
            'comment':comment_signal(r.get('race_comment')) if r.get('race_comment') else None,
            'dist':num(r.get('distance_f')),'going':going_band(r.get('going')),
            'kind':kind,'aw':aw,'course':course_key(r.get('course')),
            'trainer':norm(r.get('trainer')),'jockey':norm(r.get('jockey')),
        }
        if key:self.horses[key].append(e)
        if e['trainer']:self.trainers[e['trainer']].append(e)
        if e['jockey']:self.jockeys[e['jockey']].append(e)
        sire=norm(r.get('sire'))
        if sire:self.sires[sire].append(e)
        self.events+=1


def from_db(conn,first,last):
    """Stream true historical races. No contemporaneous RPR/TS enters features."""
    sql='''SELECT r.id as race_id,r.race_date as date,r.course,r.race_type,r.surface,
       r.distance_f,r.going,r.race_class,u.runner_key,u.horse_name as horse,
       u.horse_source_id,u.sire,u.dam,u.trainer,u.jockey,u.age,u.sex,u.official_rating,
       u.weight_lbs,u.draw,u.finish_pos,u.outcome,u.beaten,u.race_comment,u.rpr,u.topspeed,u.sp_decimal
       FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
       WHERE r.race_date>=? AND r.race_date<=? ORDER BY r.race_date,r.id,u.runner_key'''
    cur=conn.execute(sql,(first,last));key=None;rows=[]
    for row in cur:
        v=dict(zip([x[0] for x in cur.description],row))
        k=(v['date'],v['race_id'])
        if k!=key and rows:
            yield rows;rows=[]
        rows.append(v);key=k
    if rows:yield rows


def collect_historical(db,first=HISTORY_START,last=TEST2_END,limit_races=None):
    """Features for a full historical dataset; same-day updates delayed."""
    path=pathlib.Path(db)
    if not path.is_file():raise FileNotFoundError(path)
    conn=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Historical SQLite integrity failed')
    seen=set(x['name'] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    if not {'historical_races','historical_runs'}<=seen:raise ValueError('Wrong historical DB schema')
    state=FormState();data=[];pending=[];current=None;races=0;ex=collections.Counter()
    for entries in from_db(conn,first,last):
        d=day(entries[0]['date'])
        if d!=current and current is not None:
            for r,n in pending:state.add(r,current,n)
            pending=[]
        current=d;races+=1
        starters=[r for r in entries if str(r.get('outcome') or '').lower() not in ('nr','nonrunner','withdrawn','dns','scratched')]
        n=len(starters);winners=[r for r in starters if num(r.get('finish_pos'))==1]
        if n<4 or len(winners)!=1:
            ex['incomplete_or_nonstandard_races']+=1
            # Can still use older confirmed starts as historical form.
        else:
            for r in starters:
                p,q=state.features(r,d,n)
                if not identity(r):ex['identity_missing']+=1;continue
                if r['date']>=TRAIN_START:
                    data.append({**p,'date':r['date'],'race_id':r['race_id'],
                                 'horse':r['horse'],'course':r['course'],
                                 'race_type':r['race_type'],'win':int(num(r.get('finish_pos'))==1),
                                 'sp_decimal':num(r.get('sp_decimal')),
                                 'identity':identity(r),'prior_runs':q})
        for r in starters:pending.append((r,n))
        if limit_races and races>=limit_races:break
    if current is not None:
        for r,n in pending:state.add(r,current,n)
    conn.close()
    return pd.DataFrame(data),state,dict(ex),races


def Xframe(df):
    # NaNs remain NaNs; HistGradientBoosting has explicit missing-value branches.
    return df.reindex(columns=FEATURES).apply(pd.to_numeric,errors='coerce').astype(float)


def train(df):
    d=df[(df.date>=TRAIN_START)&(df.date<=TRAIN_END)]
    if d.race_id.nunique()<300 or d.win.sum()<200:
        raise RuntimeError(f'Insufficient chronological TRAIN evidence ({d.race_id.nunique()} races)')
    model=HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=13,max_depth=5,
          learning_rate=.05,min_samples_leaf=90,l2_regularization=8,random_state=107)
    model.fit(Xframe(d),d.win.astype(int))
    return model


def softmax_races(df,temperature):
    if df.empty:return df.copy()
    out=df.copy()
    pp=np.clip(out.raw_probability.to_numpy(dtype=float),.0001,.9999)
    out['_logit']=(np.log(pp/(1-pp))/temperature)
    mx=out.groupby('race_id')['_logit'].transform('max')
    out['_w']=np.exp(out['_logit']-mx)
    out['win_probability']=out['_w']/out.groupby('race_id')['_w'].transform('sum')
    return out.drop(columns=['_logit','_w'])


def race_logloss(d):
    wins=d[d.win==1]
    if not len(wins):return None
    return float(-np.log(np.clip(wins.win_probability,1e-6,1)).mean())


def roi_at_sp(d):
    # SP was observed after the event and MUST NOT be described as a bettable quote.
    valid=d[(d.sp_decimal>1)&d.sp_decimal.notna()]
    if valid.empty:return None
    return round(float(100*(valid.sp_decimal.where(valid.win==1,0).sum()/len(valid)-1)),2)


def ranks(d):
    if d.empty:return d.copy()
    return d.sort_values(['date','race_id','win_probability','horse'],ascending=[True,True,False,True]).copy().assign(
        rank=lambda t:t.groupby('race_id').cumcount()+1)


def sp_favourite_benchmark(d):
    """Post-race SP favourite comparator ONLY; do not confuse with a pre-off price."""
    fav=[]
    for _,group in d.groupby('race_id'):
        v=group[(group.sp_decimal>1)&group.sp_decimal.notna()]
        if len(v)<4 or len(v)/len(group)<.8:continue
        fav.append(v.sort_values(['sp_decimal','horse']).iloc[0])
    if not fav:return {'matched_races':0,'sp_favourite_roi_pct':None}
    result=pd.DataFrame(fav)
    return {'matched_races':len(result),'sp_favourite_wins':int(result.win.sum()),
            'sp_favourite_strike_pct':round(float(100*result.win.mean()),2),
            'sp_favourite_roi_pct':roi_at_sp(result),
            'explicit_limitation':'SP post-race favourite: hindsight benchmark, not a tradable price'}


def summarize(d,label):
    ranked=ranks(d)
    if ranked.empty:return {'period':label,'races':0,'runners':0}
    return {
       'period':label,'start':str(ranked.date.min()),'end':str(ranked.date.max()),
       'days':int(ranked.date.nunique()),'races':int(ranked.race_id.nunique()),
       'runners':len(ranked),'logloss_per_race':round(race_logloss(ranked),5) if race_logloss(ranked) is not None else None,
       'rank1':slot_summary(ranked[ranked['rank']==1]),
       'rank2':slot_summary(ranked[ranked['rank']==2]),
       'sp_favourite_descriptive':sp_favourite_benchmark(ranked),
    }


def slot_summary(t):
    if t.empty:return {}
    valid=t[t.sp_decimal.notna()&(t.sp_decimal>1)]
    days=valid.date.nunique()
    byday=valid.assign(pnl=np.where(valid.win==1,valid.sp_decimal-1,-1)).groupby('date')['pnl'].sum()
    dd=np.cumsum(byday.to_numpy(dtype=float))
    max_drawdown=float((np.maximum.accumulate(np.r_[0.,dd])[1:]-dd).max()) if len(dd) else None
    bymonth=valid.assign(pnl=np.where(valid.win==1,valid.sp_decimal-1,-1),
                        month=valid.date.str.slice(0,7)).groupby('month')['pnl'].agg(['sum','count'])
    return {'selections':len(t),'settled_sp':len(valid),'wins':int(valid.win.sum()),
            'strike_pct':round(float(100*valid.win.mean()),2) if len(valid) else None,
            'sp_proxy_roi_pct':roi_at_sp(valid),
            'avg_sp_decimal':round(float(valid.sp_decimal.mean()),2) if len(valid) else None,
            'positive_calendar_months':int((bymonth['sum']>0).sum()),
            'worst_day_units':round(float(byday.min()),2) if len(byday) else None,
            'max_peak_to_trough_drawdown_units':round(max_drawdown,2) if max_drawdown is not None else None,
            'bettable_roi_verified':False}


def write_json(path,obj):
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,indent=2,default=str,ensure_ascii=False)+'\n')


def backtest(args):
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    df,state,ex,races=collect_historical(args.db,last=TEST2_END)
    if df.empty:raise RuntimeError('No historical pre-race observations')
    model=train(df)
    df['raw_probability']=model.predict_proba(Xframe(df))[:,1]
    val=df[(df.date>=VALID_START)&(df.date<=VALID_END)].copy()
    if val.race_id.nunique()<150:raise RuntimeError('Validation too small for calibration')
    temps=[.6,.8,1.,1.25,1.5,2.,2.5]
    losses={str(t):race_logloss(softmax_races(val,t)) for t in temps}
    temp=min(temps,key=lambda t:losses[str(t)])
    df=softmax_races(df,temp)
    groups={'TRAIN_DIAGNOSTIC':df[(df.date>=TRAIN_START)&(df.date<=TRAIN_END)],
            'VALIDATION_CALIBRATION':df[(df.date>=VALID_START)&(df.date<=VALID_END)],
            'HOLDOUT_MAY_AUG':df[(df.date>=TEST1_START)&(df.date<=TEST1_END)],
            'HOLDOUT_SEP_DEC':df[(df.date>=TEST2_START)&(df.date<=TEST2_END)]}
    audit={'model':VERSION,'source':'original recovered historical SQL, strict prior-day form',
           'features':FEATURES,'pricing':'historical SP is retrospective; no executable historical profit claim',
           'race_counts_loaded':races,'eligibility_exclusions':ex,
           'temperature_chosen_on_validation_only':temp,'validation_logloss_grid':losses,
           'splits':{k:summarize(v,k) for k,v in groups.items()},
           'promotion':'RESEARCH_ONLY; MUST PASS prospective bookmaker-price tests before real bets',
           'no_live_changes':True}
    for k in ('HOLDOUT_MAY_AUG','HOLDOUT_SEP_DEC'):
        if audit['splits'][k]['races']<150:
            raise RuntimeError(f'{k} insufficient independent heldout dates; refuse to show success')
    joblib.dump({'version':VERSION,'estimator':model,'temperature':temp,'features':FEATURES,
                 'trained_through':TRAIN_END,'last_historical_date':str(df.date.max())},out/'stage107-frozen-form-model.joblib')
    # Compact saved pre-race feature state lets tomorrow's score-only workflow run
    # without downloading the large historical source a second time.
    joblib.dump({'version':VERSION,'asof_after_date':TEST2_END,'state':state},
                out/'stage107-prior-form-state.joblib',compress=3)
    write_json(out/'stage107-backtest-audit.json',audit)
    rank_df=ranks(pd.concat([groups['HOLDOUT_MAY_AUG'],groups['HOLDOUT_SEP_DEC']]))
    rank_df[rank_df['rank']<=2].to_csv(out/'stage107-holdout-rank1-rank2.csv',index=False)
    day_df=rank_df[rank_df['rank']<=2].copy()
    day_df['sp_proxy_pnl_units']=np.where(day_df.win==1,day_df.sp_decimal-1,-1)
    day_df.loc[day_df.sp_decimal.isna(),'sp_proxy_pnl_units']=np.nan
    grouped=day_df.groupby(['date','race_type','rank'],dropna=False).agg(
        selections=('race_id','count'),wins=('win','sum'),
        settled_sp=('sp_proxy_pnl_units','count'),net_sp_proxy_units=('sp_proxy_pnl_units','sum'))
    grouped['sp_proxy_roi_pct']=100*grouped.net_sp_proxy_units/grouped.settled_sp.replace(0,np.nan)
    grouped.reset_index().to_csv(out/'stage107-holdout-race-type-daily.csv',index=False)
    (out/'README.md').write_text('MODEL G: independent historical form-only research. '+
         'Historical SP returns are diagnostic only; see stage107-backtest-audit.json. '+
         'No future outcome was used in training, and odds do not affect rankings.\n')
    print(json.dumps({'races':races,'temperature':temp,'test_1':audit['splits']['HOLDOUT_MAY_AUG'],
                      'test_2':audit['splits']['HOLDOUT_SEP_DEC']},indent=2))


def flat_cards(obj):
    if isinstance(obj,list):
        for x in obj:yield from flat_cards(x)
    elif isinstance(obj,dict):
        if obj.get('horse') and obj.get('course') and (obj.get('date') or obj.get('race_time')):yield obj
        elif obj.get('course') and isinstance(obj.get('runners'),list):
            for x in obj['runners']:
                if isinstance(x,dict):yield {**{k:v for k,v in obj.items() if k!='runners'},**x}
        else:
            for v in obj.values():
                if isinstance(v,(dict,list)):yield from flat_cards(v)


def load_recent_hpr(state,db_path,earliest,asof):
    """Use prior-only *observed runs* in HPR revisions, not HPR's scored predictions."""
    if not db_path:return {'hpr_db':'not_provided','added':0}
    p=pathlib.Path(db_path)
    if not p.exists():return {'hpr_db':'missing','added':0}
    conn=sqlite3.connect('file:'+str(p.resolve())+'?mode=ro',uri=True)
    sql='''SELECT h.payload,h.race_date FROM hpr_ratings h JOIN
       (SELECT horse_key,race_key,model_version,MAX(revision) AS rev FROM hpr_ratings
        WHERE race_date>? AND race_date<? GROUP BY horse_key,race_key,model_version) z
       ON z.horse_key=h.horse_key AND z.race_key=h.race_key
       AND z.model_version=h.model_version AND z.rev=h.revision
       ORDER BY h.race_date,h.race_key'''
    groups=defaultdict(list)
    for payload,rdate in conn.execute(sql,(earliest,asof)):
        x=json.loads(payload)
        if not str(x.get('horse_key','')).startswith('P|'):continue
        if x.get('position') is None and str(x.get('status','')).lower() not in ('f','fell','pu','pulled up','ur','unseated','bd'):
            continue
        groups[rdate].append(x)
    conn.close();added=0
    for d in sorted(groups):
        for x in groups[d]:
            key=x['horse_key'].split('|')
            if len(key)<4:continue
            r={'horse':x.get('horse'),'sire':key[2],'dam':key[3],
               'course':x.get('course'),'race_type':x.get('race_type'),'surface':x.get('surface'),
               'distance_f':x.get('distance_f'),'going':x.get('going'),'trainer':x.get('trainer'),
               'jockey':x.get('jockey'),'position':x.get('position'),'status':x.get('status'),
               'beaten':x.get('beaten'),'official_rating':x.get('official_rating')}
            # identity() reconstructs exactly the P|horse|sire|dam tuple.
            if identity(r)!=x['horse_key']:continue
            state.add(r,day(d),int(num(x.get('field_size')) or 0));added+=1
    return {'hpr_db':'read','added':added,'range':f'{earliest}..{asof}'}


def get_target_state(history_db,hpr_db,target,state_path=None):
    if state_path:
        packed=joblib.load(state_path)
        if packed['version']!=VERSION or packed['asof_after_date']>=target:
            raise RuntimeError('Saved historical form state is not strictly before target date')
        state=packed['state'];latest=packed['asof_after_date'];n=None;ex={}
    else:
        if not history_db:raise RuntimeError('Pass either --state or --db')
        db=sqlite3.connect('file:'+str(pathlib.Path(history_db).resolve())+'?mode=ro',uri=True)
        latest=db.execute('SELECT MAX(race_date) FROM historical_races WHERE race_date<?',(target,)).fetchone()[0]
        db.close()
        if not latest:raise RuntimeError('Historical database has no prior records')
        _,state,ex,n=collect_historical(history_db,last=latest)
    more=load_recent_hpr(state,hpr_db,latest,target)
    return state,{'historic_last_day':latest,'historic_races':n,
                  'missing_identities':ex.get('identity_missing',0),'recent':more,
                  'saved_historical_state_used':bool(state_path)}


def score_card_rows(state,model,temperature,cards,cutoff):
    grouped=defaultdict(list)
    for x in flat_cards(cards):
        date=day(x.get('date') or x.get('race_time'))
        if not date or date<cutoff.date():continue
        race_time=stamp(x.get('race_time'))
        if race_time is None or race_time<=max(cutoff+dt.timedelta(minutes=MIN_PREOFF_MIN),dt.datetime.now(UTC)+dt.timedelta(minutes=5)):continue
        if str(x.get('race_status') or '').lower() in ('result','finished','cancelled','abandoned'):continue
        k=f'{date}|{course_key(x.get("course"))}|{race_time.strftime("%H:%M")}'
        grouped[k].append(x)
    scored=[];rejected=[]
    for k,entries in sorted(grouped.items()):
        n=len(entries)
        if n<4:rejected.append({'race':k,'reason':'FIELD_TOO_SMALL'});continue
        hh=[]
        for r in entries:
            if str(r.get('number') or '').upper()=='NR' or str(r.get('race_status') or '').lower() in ('nr','non-runner','scratched'):
                continue
            feat,prior=state.features(r,day(r.get('date') or r.get('race_time')),n)
            hh.append({**feat,'date':str(day(r.get('date') or r.get('race_time'))),
                       'race_id':k,'course':r.get('course'),'off_utc':off_time(r.get('race_time')),
                       'race_type':r.get('race_name') or r.get('type') or '',
                       'horse':r.get('horse'),'prior_runs':prior,'identity':identity(r)})
        if len(hh)<4:continue
        coverage=sum(bool(z['identity']) and z['prior_runs']>0 for z in hh)/len(hh)
        if coverage<.55:rejected.append({'race':k,'reason':f'PRIOR_FORM_COVERAGE_{coverage:.2f}'});continue
        tmp=pd.DataFrame(hh)
        tmp['raw_probability']=model.predict_proba(Xframe(tmp))[:,1]
        tmp=softmax_races(tmp,temperature)
        scored.extend(tmp.to_dict('records'))
    return pd.DataFrame(scored),rejected


def match_market(scored,odds,book,cutoff):
    if scored.empty:return scored,[]
    audit=[];index=defaultdict(list)
    for r in odds.get('prices',[]):
        if norm(r.get('bookmaker'))!=norm(book):continue
        t=stamp(r.get('race_time'))
        quote=stamp(r.get('bookmaker_updated_at') or r.get('collected_at'))
        collected=stamp(r.get('collected_at') or odds.get('collected_at'))
        if not t or not quote or not collected or quote>cutoff or collected>cutoff:continue
        age_quote=(cutoff-quote).total_seconds()/60
        age_collected=(cutoff-collected).total_seconds()/60
        if not(0<=age_quote<=MAX_QUOTE_MIN and 0<=age_collected<=MAX_QUOTE_MIN):continue
        o=num(r.get('decimal_odds'))
        if o is None or o<=1:continue
        k=f'{t.date()}|{course_key(r.get("course"))}|{t.strftime("%H:%M")}|{norm(r.get("horse"))}'
        index[k].append((collected,o,quote))
    slate=[]
    for key,g in scored.groupby('race_id'):
        prices=[]
        for _,r in g.iterrows():
            kk=f'{key}|{norm(r.horse)}';v=index.get(kk,[])
            prices.append(max(v,key=lambda x:x[0])[1] if v else None)
        coverage=sum(x is not None for x in prices)/len(prices)
        if coverage<MIN_QUOTE_COVERAGE:
            audit.append({'race':key,'reason':'INSUFFICIENT_EXACT_BOOK_QUOTES','coverage':round(coverage,3)})
            continue
        frame=g.copy();frame['quote_decimal']=prices
        fav=min(x for x in prices if x is not None)
        frame['value_edge']=frame.win_probability*frame.quote_decimal-1
        frame['is_favourite']=frame.quote_decimal<=fav+1e-9
        good=frame[(frame.quote_decimal.between(MIN_BETS_PRICE,MAX_BETS_PRICE)) &
                   (frame.value_edge>=MIN_EV) & (frame.win_probability>=.065) &
                   (frame.identity!='') & (frame.prior_runs>=2)].copy()
        if good.empty:
            audit.append({'race':key,'reason':'NO_VERIFIABLE_VALUE','coverage':round(coverage,3)})
            continue
        best=good.sort_values(['value_edge','win_probability'],ascending=[False,False]).iloc[0].to_dict()
        best['quote_coverage']=round(coverage,3);best['price_used']='exact '+book+' as of cutoff'
        slate.append(best)
    return pd.DataFrame(slate),audit


def paper(args):
    cutoff=stamp(args.cutoff) if args.cutoff else dt.datetime.now(UTC)
    if not cutoff:raise ValueError('Invalid cutoff')
    date=args.date or cutoff.date().isoformat()
    if day(date)<cutoff.date():raise RuntimeError('Refuse to create retrospective paper selections')
    model_obj=joblib.load(args.model)
    if model_obj['version']!=VERSION or model_obj['features']!=FEATURES:
        raise RuntimeError('Wrong/future model version; fail closed')
    cards=json.loads(pathlib.Path(args.cards).read_text())
    odds=json.loads(pathlib.Path(args.odds).read_text())
    state,audit=get_target_state(args.db,args.hpr,date,args.state)
    scored,skips=score_card_rows(state,model_obj['estimator'],model_obj['temperature'],cards,cutoff)
    scored=scored[scored.date==date].copy() if not scored.empty else scored
    picks,price_skips=match_market(scored,odds,args.bookmaker,cutoff)
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    if not scored.empty:ranks(scored).to_csv(out/'stage107-paper-all-rankings.csv',index=False)
    if not picks.empty:picks.to_csv(out/'stage107-paper-value-selections.csv',index=False)
    meta={'model':VERSION,'paper_only':True,'as_of_utc':cutoff.isoformat(),
          'target_date':date,'bookmaker':args.bookmaker,'ranked_races':scored.race_id.nunique() if not scored.empty else 0,
          'verified_value_selections':len(picks),'form_history':audit,
          'race_exclusions':skips,'price_exclusions':price_skips,
          'warning':'No selections is valid; do not substitute SP, future quotes, favourites or unverified names.'}
    write_json(out/'stage107-paper-audit.json',meta)
    print(json.dumps(meta,indent=2))


def self_test():
    # Check that adding a future win or same-day result cannot change pre-off features.
    s=FormState();r={'horse':'Test Horse','sire':'Test Sire','dam':'Test Dam','trainer':'T1',
                     'jockey':'J1','course':'Ascot','distance_f':16,'going':'Good','race_type':'Handicap Hurdle'}
    a,_=s.features(r,dt.date(2025,2,1),8)
    s.add({**r,'position':1,'beaten':0,'race_comment':'stayed on'},dt.date(2025,2,1),8)
    b,_=s.features(r,dt.date(2025,2,1),8)
    assert a==b,'Same-day lookahead detected'
    c,_=s.features(r,dt.date(2025,2,2),8)
    assert c['horse_runs']==1 and c['horse_quality3'] is not None
    assert beaten_lengths('2 1/2L')==2.5 and beaten_lengths('nk')==.3
    assert identity({'horse':'Same','sire':'S','dam':'D'})!=identity({'horse':'Same','sire':'S2','dam':'D'})
    assert not identity({'horse':'No pedigree'})
    # Explicit quote cutoff safety: never use odds captured after decision.
    df=pd.DataFrame([{'race_id':'2026-11-13|ASCOT|12:00','horse':'Test Horse',
       'win_probability':.2,'prior_runs':3,'identity':'P|TESTHORSE|TESTSIRE|TESTDAM'}])
    future={'prices':[{'race_time':'2026-11-13T12:00:00+00:00','course':'Ascot',
      'horse':'Test Horse','bookmaker':'williamhill','decimal_odds':10,
      'collected_at':'2026-11-13T10:50:00+00:00'}]}
    got,_=match_market(df,future,'williamhill',stamp('2026-11-13T10:40:00+00:00'))
    assert got.empty,'Future-quote lookahead detected'
    print('Stage107 self-test passed: same-day/future results and post-cutoff quote excluded')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('self-test')
    b=sub.add_parser('backtest');b.add_argument('--db',required=True);b.add_argument('--out',required=True)
    s=sub.add_parser('paper');s.add_argument('--db');s.add_argument('--state');s.add_argument('--model',required=True)
    s.add_argument('--hpr');s.add_argument('--cards',required=True);s.add_argument('--odds',required=True)
    s.add_argument('--date');s.add_argument('--cutoff');s.add_argument('--bookmaker',default=DEFAULT_BOOK)
    s.add_argument('--out',required=True)
    args=p.parse_args()
    if args.cmd=='self-test':self_test()
    elif args.cmd=='backtest':backtest(args)
    elif args.cmd=='paper':paper(args)

if __name__=='__main__':main()
