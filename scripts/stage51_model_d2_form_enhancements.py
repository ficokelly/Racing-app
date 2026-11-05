#!/usr/bin/env python3
"""Stage51 RESEARCH ONLY: add date-safe derived evidence to Stage50 D2.
Never imports or writes live model configuration. No external website data is
claimed unless a historical, as-of-dated CSV is explicitly supplied.
"""
import argparse,csv,datetime as dt,json,math,sqlite3
from collections import defaultdict
from pathlib import Path
import numpy as np
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast
from stage50_model_d2_optimise import SQL,metrics

METRICS=OLD.METRICS
FEATURES=['draw_bias','jump_index','trainer_specialism','ability_class']

def load(db):
    past=LivePast({}); out=[];pending=[];group=[];last_day=None;last_id=None
    # Counters are updated only after the entire calendar day's forecasts.
    stats=defaultdict(lambda:[0,0]); horse_jump=defaultdict(lambda:[0,0,0]); horse_class=defaultdict(lambda:[0,0]);
    def n(x):
        try:return float(x)
        except (TypeError,ValueError):return None
    def key_course(g):return OLD.track(g[0][2])
    def lane(draw,field):
        d=n(draw)
        if d is None or d<1:return None
        return 'inside' if d<=field/3 else 'outside' if d>2*field/3 else 'middle'
    def typ(g):
        t=OLD.normal(g[0][3]);return 'hurdle' if 'hurdle' in t else 'chase' if 'chase' in t or 'steeple' in t else 'nh_flat' if any(v in t for v in ('bumper','nhflat','national hunt flat')) else 'nh_other'
    def features(g,r):
        starters=[z for z in g if OLD.started(z)];field=len(starters);kind=r['kind'];place=key_course(g)
        dist=OLD.distband(g[0][5]);ground=OLD.going(g[0][6]);classval=n(g[0][7]);sub=typ(g)
        res=np.zeros((field,4),dtype=np.float32)
        for i,z in enumerate(starters):
            trainer=OLD.normal(z[14]);horse=(OLD.normal(z[8]),OLD.normal(z[10]))
            if kind!='jumps':
                ln=lane(z[21],field)
                if ln:
                    k=('draw',place,dist,ground,ln);a,b=stats[k];aa,bb=stats[('draw_all',place,dist,ground)]
                    if a>=25 and aa>=75:res[i,0]=np.clip((b+2)/(a+8)-(bb+2)/(aa+8),-.35,.35)
            if kind=='jumps' and sub in ('hurdle','chase'):
                starts,errors,completed=horse_jump[(horse,sub)]
                if starts>=2:res[i,1]=np.clip((completed-errors*.7+2)/(starts+4)-.5,-.5,.5)
            a,b=stats[('trainer_course_kind',trainer,place,kind)];aa,bb=stats[('trainer_kind',trainer,kind)]
            if a>=8 and aa>=20:res[i,2]=np.clip((b+2)/(a+12)-(bb+2)/(aa+12),-.35,.35)
            # Only pre-race official mark and prior class-conditioned results; no post-race speed ratings.
            if classval is not None:
                a,b=horse_class[(horse,int(classval))]
                if a>=2:res[i,3]=np.clip((b+1)/(a+5)-.2,-.3,.4)
        return res
    def finish(g):
        if not g:return
        if key_course(g) not in OLD.TRACKS:return
        if str(g[0][1])>='2025-01-01':
            starters=[z for z in g if OLD.started(z)]
            if len(starters)>=2 and sum(z[12]==1 for z in starters)==1:
                r=past.score(g,live=True)
                if r is not None:
                    sp=np.asarray(r['sp'],dtype=float);sp[~np.isfinite(sp)|(sp<1.01)|(sp>5000)]=np.nan
                    out.append(dict(date=r['date'],year=r['year'],kind=r['kind'],subtype=typ(g) if r['kind']=='jumps' else r['kind'],
                                    course=r['course'],x=np.asarray(r['x'][:,:8],dtype=np.float32),extra=features(g,r),sp=sp,winner=r['winning_index'],n=len(sp)))
        pending.append(g)
    def update(g):
        if key_course(g) not in OLD.TRACKS:return
        starters=[z for z in g if OLD.started(z)];kind=OLD.style(g[0]);sub=typ(g);place=key_course(g);dist=OLD.distband(g[0][5]);ground=OLD.going(g[0][6]);classval=n(g[0][7]);field=len(starters)
        for z in starters:
            win=int(z[12]==1);trainer=OLD.normal(z[14]);horse=(OLD.normal(z[8]),OLD.normal(z[10]))
            for k in [('trainer_course_kind',trainer,place,kind),('trainer_kind',trainer,kind)]:stats[k][0]+=1;stats[k][1]+=win
            if kind!='jumps':
                ln=lane(z[21],field)
                if ln:
                    for k in [('draw',place,dist,ground,ln),('draw_all',place,dist,ground)]:stats[k][0]+=1;stats[k][1]+=win
            if kind=='jumps' and sub in ('hurdle','chase'):
                note=OLD.normal(z[22]);j=horse_jump[(horse,sub)];j[0]+=1
                if any(q in note for q in ('fell','unseated','blunder','mistake','pecked','jumped poorly')):j[1]+=1
                if z[12] is not None and isinstance(z[12],int) and z[12]>0:j[2]+=1
            if classval is not None:
                j=horse_class[(horse,int(classval))];j[0]+=1;j[1]+=win
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

def metric_enhanced(races,w,alpha,which):
    wins=[0,0];bets=[0,0];ret=[0.,0.];covered=0
    for r in races:
        active=np.asarray(w,dtype=float).copy();active[4 if r['kind']=='jumps' else 7]=0;active/=max(active.sum(),1e-9)
        scores=r['x']@active+ r['extra']@alpha
        inds=np.argsort(-scores,kind='stable')[:2]
        covered+=int(r['winner'] in inds)
        for k,i in enumerate(inds):
            wins[k]+=int(i==r['winner'])
            if np.isfinite(r['sp'][i]):
                bets[k]+=1
                if i==r['winner']:ret[k]+=float(r['sp'][i])
    idx=[0] if which=='rank1' else [1] if which=='rank2' else [0,1]
    b=sum(bets[k] for k in idx);winners=sum(wins[k] for k in idx);returned=sum(ret[k] for k in idx)
    return {'races':len(races),'bets':b,'wins':winners,'strike_pct':round(100*winners/len(races),3) if races else None,
            'roi_pct':round(100*(returned/b-1),3) if b else None,'profit_units':round(returned-b,3)}

def main(args):
    source=json.loads(Path(args.stage50).read_text())
    if source.get('status')!='RESEARCH_ONLY' or not source.get('live_model_unchanged'):raise RuntimeError('Stage50 summary not verified')
    cfg=json.loads(Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    if cfg['chosen_combination']!='D-064-gap14':raise RuntimeError('Frozen D config changed')
    frozen=np.array([cfg['weights_pct'][k]/100 for k in METRICS])
    races=load(args.db);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    if not {2025,2026}.issubset({r['year'] for r in races}):raise RuntimeError('Both years required')
    # Fixed, predeclared search. One new signal at a time, then pairs, then all.
    alphas=[('no_enhancement',np.zeros(4))]
    for i,name in enumerate(FEATURES):
        for a in (.05,.15,.3,.6):
            v=np.zeros(4);v[i]=a;alphas.append((f'{name}_{a}',v))
    for i in range(4):
        for j in range(i+1,4):
            for a in (.1,.3):
                v=np.zeros(4);v[i]=a;v[j]=a;alphas.append((f'{FEATURES[i]}+{FEATURES[j]}_{a}',v))
    for a in (.1,.3):alphas.append((f'all_{a}',np.ones(4)*a))
    results=[]
    for kind in ['all','flat','aw','jumps','hurdle','chase','nh_flat']:
        subset=[r for r in races if kind=='all' or r['kind']==kind or r['subtype']==kind]
        train=[r for r in subset if r['year']==2025];test=[r for r in subset if r['year']==2026]
        if len(train)<100 or len(test)<70:continue
        for target in ('rank1','rank2','both'):
            info=source.get('results',{}).get(f'{kind}|{target}')
            if not info:continue
            weights=np.array([info['weights_pct'][k]/100 for k in METRICS])
            baseline=metric_enhanced(test,weights,np.zeros(4),target)
            trials=[(metric_enhanced(train,weights,a,target)['roi_pct'],label,a) for label,a in alphas]
            _,label,a=max(trials,key=lambda z:z[0]);t=metric_enhanced(test,weights,a,target)
            results.append({'discipline':kind,'strategy':target,'selected_2025':label,'2025_races':len(train),'2026_races':len(test),
                            '2025_roi_pct':max(trials,key=lambda z:z[0])[0],'2026_roi_pct':t['roi_pct'],
                            '2026_strike_pct':t['strike_pct'],'2026_bets':t['bets'],'2026_profit_units':t['profit_units'],
                            '2026_stage50_roi_pct':baseline['roi_pct'],'2026_stage50_strike_pct':baseline['strike_pct'],
                            '2026_roi_change_pp':round(t['roi_pct']-baseline['roi_pct'],3)})
            print(kind,target,label,'2026 ROI',t['roi_pct'],'baseline',baseline['roi_pct'],flush=True)
    if not results:raise RuntimeError('No eligible comparisons; check Stage50 artifact')
    with (out/'stage51_enhanced_2025_2026.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    (out/'stage51_summary.json').write_text(json.dumps({'status':'RESEARCH_ONLY','live_model_unchanged':True,
       'features_tested':FEATURES,'note':'Historical source-only proxy features. No external website, proprietary speed figure or true pace data is represented as present.',
       'method':'2025 select enhancement strength, 2026 compare to Stage50 same race sample; retrospective 2026 already inspected.',
       'results':results},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True,type=Path);p.add_argument('--stage50',required=True);p.add_argument('--output',default='reports/stage51');main(p.parse_args())
