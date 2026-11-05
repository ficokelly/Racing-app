#!/usr/bin/env python3
"""Stage52 research-only: prior race winner profiles -> D2 eight-pillar ranking.
No writes to live app. No post-race evidence enters same-day forecasts.
Exact recurring race matches require an explicit validated event-id mapping.
"""
import argparse,csv,datetime as dt,json,math,sqlite3
from collections import defaultdict
from pathlib import Path
import numpy as np
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast
from stage50_model_d2_optimise import SQL,METRICS

def num(x):
    try:return float(x)
    except (TypeError,ValueError):return None

def subtype(g):
    t=OLD.normal(g[0][3]);k=OLD.style(g[0])
    if k!='jumps':return k
    if 'hurdle' in t:return 'hurdle'
    if 'chase' in t or 'steeple' in t:return 'chase'
    if any(v in t for v in ('bumper','nhflat','national hunt flat')):return 'nh_flat'
    return 'nh_other'

def fieldbin(n):return (n//3)*3

def profile(z,g,history):
    h=(OLD.normal(z[8]),OLD.normal(z[10]));last=history.get(h)
    age=num(z[17]);mark=num(z[16]);day=dt.date.fromisoformat(str(g[0][1])[:10])
    layoff=(day-last).days if last else None
    return [age,mark,layoff]

def bucket(v,kind):
    if v is None:return None
    if kind==0:return int(v)
    if kind==1:return int(v//5)*5
    return min(365,int(v//30)*30)

def load(db,mapping):
    past=LivePast({});out=[];pending=[];group=[];last_day=None;last_id=None
    # Winner and starter distributions, all observed strictly before prediction day.
    hist=defaultdict(lambda:[defaultdict(int) for _ in range(3)])
    latest={}; counts=defaultdict(int)
    def keys(g):
        course=OLD.track(g[0][2]);kind=subtype(g);distance=OLD.distband(g[0][5]);ground=OLD.going(g[0][6]);n=sum(OLD.started(z) for z in g)
        event=mapping.get(str(g[0][0]));month=int(str(g[0][1])[5:7]);
        # Exact mapping only if supplied by user-reviewed historical race identity.
        exact=('exact',event) if event else None
        comparable=('similar',course,kind,distance,ground,fieldbin(n))
        fallback=('broad',kind,distance,ground,fieldbin(n))
        return [k for k in (exact,comparable,fallback) if k]
    def trend(g):
        starters=[z for z in g if OLD.started(z)];n=len(starters);v=np.zeros((n,3),dtype=np.float32)
        kk=keys(g)
        # Prefer exact editions with 8+ historic winners; otherwise similar conditions
        # with >=25, then broad with >=60. No winner-profile claim below minimum.
        chosen=None
        for k,minimum in zip(kk,([8,25,60] if kk[0][0]=='exact' else [25,60])):
            if counts[k]>=minimum:chosen=k;break
        if chosen is None:return v,'insufficient_history',0
        distributions=hist[chosen]
        for i,z in enumerate(starters):
            p=profile(z,g,latest)
            for j,value in enumerate(p):
                b=bucket(value,j)
                if b is None:continue
                wins=distributions[j].get(('win',b),0)
                starts=distributions[j].get(('start',b),0)
                totalwin=sum(vv for (t,_),vv in distributions[j].items() if t=='win')
                totalstart=sum(vv for (t,_),vv in distributions[j].items() if t=='start')
                if starts>=12 and totalstart>=80:
                    base=(totalwin+2)/(totalstart+8)
                    posterior=(wins+base*30)/(starts+30)
                    v[i,j]=float(np.clip(math.log(max(.001,posterior)/max(.001,base)),-1.,1.))
        return v,chosen[0],counts[chosen]
    def finish(g):
        if not g or OLD.track(g[0][2]) not in OLD.TRACKS:return
        if str(g[0][1])>='2025-01-01':
            starters=[z for z in g if OLD.started(z)]
            if len(starters)>=2 and sum(z[12]==1 for z in starters)==1:
                r=past.score(g,live=True)
                if r is not None:
                    x=np.asarray(r['x'][:,:8],dtype=np.float32)
                    sp=np.asarray(r['sp'],dtype=float);sp[~np.isfinite(sp)|(sp<1.01)|(sp>5000)]=np.nan
                    tr,level,sample=trend(g)
                    out.append(dict(date=r['date'],year=r['year'],kind=r['kind'],subtype=subtype(g),course=r['course'],x=x,trend=tr,level=level,history_winners=sample,sp=sp,winner=r['winning_index'],n=len(sp)))
        pending.append(g)
    def update(g):
        if OLD.track(g[0][2]) not in OLD.TRACKS:return
        starters=[z for z in g if OLD.started(z)]
        if len(starters)<2:return
        winners=[z for z in starters if z[12]==1]
        if len(winners)==1:
            for k in keys(g):
                counts[k]+=1
                for z in starters:
                    p=profile(z,g,latest)
                    for j,value in enumerate(p):
                        b=bucket(value,j)
                        if b is not None:
                            hist[k][j][('start',b)]+=1
                            if z[12]==1:hist[k][j][('win',b)]+=1
        day=dt.date.fromisoformat(str(g[0][1])[:10])
        for z in starters:latest[(OLD.normal(z[8]),OLD.normal(z[10]))]=day
        past.update(g)
    cx=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
    for row in cx.execute(SQL,('2026-11-03',)):
        day,rid=row[1],row[0]
        if last_day is not None and day!=last_day:
            finish(group);group=[]
            for g in pending:update(g)
            pending=[]
        elif group and rid!=last_id:finish(group);group=[]
        group.append(row);last_day=day;last_id=rid
    finish(group);cx.close()
    return out

def score(r,w,alpha):
    active=np.array(w,dtype=float);active[4 if r['kind']=='jumps' else 7]=0
    active/=max(active.sum(),1e-9)
    # Trend signal is placed INSIDE Ability, rather than a ninth pillar.
    base=r['x'].copy();base[:,0]+=r['trend']@np.asarray(alpha)
    return np.argsort(-(base@active),kind='stable')[:2]

def metric(rows,w,alpha,target):
    bets=wins=0;returns=0.;covered=0
    for r in rows:
        indices=score(r,w,alpha);covered+=int(r['winner'] in indices)
        for i in ([0] if target=='rank1' else [1] if target=='rank2' else [0,1]):
            j=indices[i];wins+=int(j==r['winner'])
            if np.isfinite(r['sp'][j]):
                bets+=1
                if j==r['winner']:returns+=float(r['sp'][j])
    return dict(races=len(rows),bets=bets,wins=wins,coverage_pct=round(100*covered/len(rows),3) if rows else None,strike_pct=round(100*wins/len(rows),3) if rows else None,profit_units=round(returns-bets,3),roi_pct=round(100*(returns/bets-1),3) if bets else None)

def main(a):
    source=json.loads(Path(a.stage50).read_text())
    assert source.get('status')=='RESEARCH_ONLY' and source.get('live_model_unchanged')
    config=json.loads(Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert config['chosen_combination']=='D-064-gap14'
    frozen=np.array([config['weights_pct'][k]/100 for k in METRICS])
    mapping={}
    if a.mapping:
        with open(a.mapping,newline='') as f:
            for z in csv.DictReader(f):
                if z.get('race_id') and z.get('recurring_event_id'):mapping[z['race_id']]=z['recurring_event_id']
    races=load(a.db,mapping)
    if not {2025,2026}.issubset({r['year'] for r in races}):raise RuntimeError('2025 and 2026 data required')
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    trials=[('no_trends',[0.,0.,0.])]
    for j,name in enumerate(('age','official_rating','layoff')):
        for strength in (.2,.5,1.):
            v=[0.,0.,0.];v[j]=strength;trials.append((name+'_'+str(strength),v))
    for strength in (.2,.5,1.):trials.append(('all_'+str(strength),[strength]*3))
    results=[]
    for kind in ('all','flat','aw','jumps','hurdle','chase','nh_flat'):
        subset=[r for r in races if kind=='all' or r['kind']==kind or r['subtype']==kind]
        train=[r for r in subset if r['year']==2025];test=[r for r in subset if r['year']==2026]
        if len(train)<100 or len(test)<70:continue
        for target in ('rank1','rank2','both'):
            info=source.get('results',{}).get(f'{kind}|{target}')
            if not info:continue
            weights=np.array([info['weights_pct'][k]/100 for k in METRICS]);baseline=metric(test,weights,[0,0,0],target)
            # Select on 2025 only. Keep baseline candidate and require 80% SP coverage.
            candidates=[]
            for name,alpha in trials:
                m=metric(train,weights,alpha,target)
                if m['bets'] >= len(train)*(.8 if target!='both' else 1.6):candidates.append((m['roi_pct'],name,alpha,m))
            if not candidates:continue
            _,label,alpha,train_result=max(candidates,key=lambda x:x[0]);test_result=metric(test,weights,alpha,target)
            frozen_result=metric(test,frozen,[0,0,0],target)
            results.append(dict(discipline=kind,strategy=target,chosen_2025=label,train_2025_races=len(train),test_2026_races=len(test),
                train_2025_roi_pct=train_result['roi_pct'],test_2026_strike_pct=test_result['strike_pct'],test_2026_roi_pct=test_result['roi_pct'],
                test_2026_profit_units=test_result['profit_units'],test_2026_bets=test_result['bets'],
                d2_2026_roi_pct=baseline['roi_pct'],frozen_d_2026_roi_pct=frozen_result['roi_pct'],
                change_vs_d2_pp=round(test_result['roi_pct']-baseline['roi_pct'],3),
                races_with_historical_trend=sum(r['level']!='insufficient_history' for r in test)))
            print(kind,target,label,test_result['roi_pct'],'baseline',baseline['roi_pct'],flush=True)
    if not results:raise RuntimeError('No eligible Stage52 comparisons')
    with (out/'stage52_trends_vs_d2.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
    (out/'stage52_summary.json').write_text(json.dumps(dict(status='RESEARCH_ONLY',live_model_unchanged=True,actual_exact_race_mapping_entries=len(mapping),
        caveats=['Exact race editions are only used with explicitly provided recurring-event mapping; otherwise similar-condition historical races.',
        'This first-stage trend experiment measures winner-profile age, official rating and days-since-last-run; it is NOT the full thousands-of-signals trends system.',
        'Historical winner and starter profiles are built only from prior racing days; sparse groups are not asserted as trends.',
        'Trend signal enters the Ability pillar only; other seven pillars and frozen live model are unchanged.',
        '2026 has been inspected in earlier research and is not a virgin out-of-sample period.'],results=results),indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True,type=Path);p.add_argument('--stage50',required=True);p.add_argument('--mapping',type=Path);p.add_argument('--output',default='reports/stage52');main(p.parse_args())
