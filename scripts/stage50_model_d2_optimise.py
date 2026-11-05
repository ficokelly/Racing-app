#!/usr/bin/env python3
"""Stage50 research-only D2: historical replay, 2025 development, 2026 evaluation."""
import argparse,csv,datetime as dt,json,sqlite3,math
from collections import defaultdict
from pathlib import Path
import numpy as np
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast

METRICS=OLD.METRICS
SQL='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
 u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
 u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment
 FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
 WHERE r.race_date <= ? ORDER BY r.race_date,r.id'''

def load(db):
    past=LivePast({});out=[];pending=[];group=[];last_date=None;last_id=None;count=0
    def finish(g):
        if not g:return
        if OLD.track(g[0][2]) not in OLD.TRACKS:return
        if str(g[0][1])>='2025-01-01':
            starters=[r for r in g if OLD.started(r)]
            if len(starters)>=2 and sum(r[12]==1 for r in starters)==1:
                race=past.score(g,live=True)
                if race is not None:
                    x=np.asarray(race['x'][:,:8],dtype=np.float32)
                    sp=np.asarray(race['sp'],dtype=float)
                    sp[~np.isfinite(sp)|(sp<1.01)|(sp>5000)]=np.nan
                    typ=OLD.normal(g[0][3]);kind=race['kind']
                    sub=('hurdle' if 'hurdle' in typ else 'chase' if any(t in typ for t in ('chase','steeple')) else 'nh_flat' if any(t in typ for t in ('bumper','nhflat','national hunt flat')) else 'nh_other') if kind=='jumps' else kind
                    out.append({'date':race['date'],'year':race['year'],'kind':kind,'subtype':sub,'course':race['course'],'x':x,'sp':sp,'winner':race['winning_index'],'n':len(sp)})
        pending.append(g)
    cx=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
    for row in cx.execute(SQL,('2026-11-03',)):
        day,rid=row[1],row[0]
        if last_date is not None and day!=last_date:
            finish(group);group=[]
            for g in pending:past.update(g)
            pending=[]
        elif group and rid!=last_id:
            finish(group);group=[]
        group.append(row);last_date=day;last_id=rid;count+=1
        if count%1000000==0:print('Scanned',count,'rows; scored',len(out),'races',flush=True)
    finish(group);cx.close()
    print('Scored',len(out),'races, with previous-day-only features',flush=True)
    return out

def metrics(races,w):
    wins=[0,0];bets=[0,0];returns=[0.,0.];top2=0;priced_races=0
    for r in races:
        active=w.copy();active[4 if r['kind']=='jumps' else 7]=0
        active/=max(active.sum(),1e-9)
        scores=r['x']@active
        inds=np.argsort(-scores,kind='stable')[:2]
        if int(r['winner']) in inds:top2+=1
        if all(np.isfinite(r['sp'][inds])):priced_races+=1
        for k,i in enumerate(inds):
            if int(i)==r['winner']:wins[k]+=1
            if np.isfinite(r['sp'][i]):
                bets[k]+=1
                if int(i)==r['winner']:returns[k]+=float(r['sp'][i])
    n=len(races)
    def obj(b,ret,win):return {'bets':b,'wins':win,'strike_pct':round(100*win/n,3) if n else None,'profit_units':round(ret-b,3),'roi_pct':round(100*(ret/b-1),3) if b else None}
    return {'races':n,'top2_winners':top2,'top2_coverage_pct':round(100*top2/n,3) if n else None,'both_priced_races':priced_races,'rank1':obj(bets[0],returns[0],wins[0]),'rank2':obj(bets[1],returns[1],wins[1]),'both':obj(sum(bets),sum(returns),top2)}

def candidates(base,count,seed):
    rng=np.random.default_rng(seed)
    yield 'frozen_D',base.copy()
    yield 'equal',np.ones(8)/8
    for k in range(8):
        w=np.zeros(8);w[k]=1;yield f'only_{METRICS[k]}',w
    for i in range(count):
        # Half near frozen D, half broad search; deterministic and reproducible.
        alpha=base*([20,60,150][i%3])+.5 if i%2==0 else np.ones(8)*[.8,2,5][i%3]
        yield f'D2_{i:04d}',rng.dirichlet(alpha)

def main(args):
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    config=json.loads(Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    if config['chosen_combination']!='D-064-gap14':raise RuntimeError('Frozen D config mismatch')
    frozen=np.array([config['weights_pct'][k]/100 for k in METRICS],dtype=float)
    races=load(args.db)
    if not {2025,2026}.issubset({r['year'] for r in races}):raise RuntimeError('2025 and 2026 must both exist')
    rows=[];best={}
    for kind in ['all','flat','aw','jumps','hurdle','chase','nh_flat']:
        subset=[r for r in races if kind=='all' or r['kind']==kind or r['subtype']==kind]
        train=[r for r in subset if r['year']==2025];test=[r for r in subset if r['year']==2026]
        if len(train)<args.min_train or len(test)<args.min_test:
            print('Skipping',kind,'train/test',len(train),len(test),flush=True);continue
        # Pick independent winners for each objective, strictly on 2025 data.
        leaders={};frozen_train=metrics(train,frozen);frozen_test=metrics(test,frozen)
        for label,w in candidates(frozen,args.candidates,args.seed):
            m=metrics(train,w)
            for target in ['rank1','rank2','both']:
                s=m[target]
                # No winner-only or tiny-coverage strategies: all eligible races must be scored.
                if s['bets']<len(train)*.8:continue
                objective=s['roi_pct']
                if target not in leaders or objective>leaders[target][0]:leaders[target]=(objective,label,w.copy(),m)
        for target,(objective,label,w,mtrain) in leaders.items():
            mtest=metrics(test,w)
            row={'discipline':kind,'objective':target,'candidate':label,'train_2025_races':len(train),'test_2026_races':len(test),
                 'train_2025_strike_pct':mtrain[target]['strike_pct'],'train_2025_roi_pct':mtrain[target]['roi_pct'],
                 'test_2026_strike_pct':mtest[target]['strike_pct'],'test_2026_roi_pct':mtest[target]['roi_pct'],
                 'test_2026_bets':mtest[target]['bets'],'test_2026_profit_units':mtest[target]['profit_units'],
                 'frozen_2026_strike_pct':frozen_test[target]['strike_pct'],'frozen_2026_roi_pct':frozen_test[target]['roi_pct'],
                 'test_roi_improvement_pp':round(mtest[target]['roi_pct']-frozen_test[target]['roi_pct'],3),
                 'weights_json':json.dumps(dict(zip(METRICS,np.round(w*100,3))))}
            rows.append(row);best[f'{kind}|{target}']={'selected_by_2025':label,'weights_pct':dict(zip(METRICS,np.round(w*100,3).tolist())),
                'train_2025':mtrain,'test_2026':mtest,'frozen_2025':frozen_train,'frozen_2026':frozen_test}
            print(kind,target,label,'2025 ROI',row['train_2025_roi_pct'],'2026 ROI',row['test_2026_roi_pct'],'frozen 2026',row['frozen_2026_roi_pct'],flush=True)
    with (out/'stage50_d2_2025_train_2026_test.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'stage50_d2_summary.json').write_text(json.dumps({'status':'RESEARCH_ONLY','live_model_unchanged':True,'candidate_count':args.candidates+10,
      'training_year':2025,'evaluation_year':2026,'source_limit':'2026-10-07 expected; actual depends on SQLite',
      'selection_note':'2025 already inspected during previous research; 2026 is a temporal comparison, not a fully untouched holdout.',
      'sp_note':'SP used only for historical settlement and weight optimisation, not predictive input. This creates overfitting risk.',
      'results':best},indent=2)+'\n')
    print('Saved',len(rows),'discipline/objective comparisons',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True,type=Path);p.add_argument('--output',default='reports/stage50');p.add_argument('--candidates',type=int,default=300);p.add_argument('--seed',type=int,default=5049);p.add_argument('--min-train',type=int,default=100);p.add_argument('--min-test',type=int,default=70)
    main(p.parse_args())
