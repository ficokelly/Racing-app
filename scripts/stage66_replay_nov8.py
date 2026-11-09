#!/usr/bin/env python3
"""Research-only 8 Nov 2026 A/B/C replay. Uses pre-off git racecards and prior-date training."""
import collections,csv,datetime as dt,json,pathlib,subprocess,sys
from fractions import Fraction
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage65p_race_type_c_only as C
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast,live_row,prediction_rows,name,clock,nonrunner,time_utc

DAY='2026-11-08'; UTC=dt.timezone.utc

def git(*args):return subprocess.run(['git',*args],check=True,capture_output=True,text=True).stdout

def history(path):
    lines=git('log','--all','--format=%H %ct','--',path).splitlines()
    return sorted((int(z.split()[1]),z.split()[0]) for z in lines if len(z.split())==2)

def git_json(sha,path):
    try:return json.loads(git('show',sha+':'+path))
    except Exception:return {}

def odds(sp):
    if not sp:return None
    try:return 1+float(Fraction(str(sp).replace(' ','').upper())) if '/' in str(sp) else (1.0 if str(sp).upper()=='EVS' else float(sp))
    except Exception:return None

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--updated',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    results=json.loads(pathlib.Path('data/race-results.json').read_text()).get('races',[])
    races=[r for r in results if r.get('date')==DAY and r.get('status')=='final']
    if not races:raise RuntimeError('No final results for Nov 8 in data/race-results.json')
    card_history=history('data/racecards.json');live_history=history('data/strategy-d-probabilities.json')
    cfg=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert cfg['chosen_combination']=='D-064-gap14' and abs(float(cfg['selection_margin'])-.14)<1e-9
    weights=[float(cfg['weights_pct'][k])/100 for k in OLD.METRICS]
    X,y,years,counts,_=C.build_training(a.updated)
    for yr in (2024,2025):
        n=sum(v for (z,cat),v in counts.items() if z==yr)
        good=sum(v for (z,cat),v in counts.items() if z==yr and cat!='unknown')
        if n==0 or good/n<.60:raise RuntimeError(f'Model C insufficient categories {yr}: {good}/{n}')
    tr=years==2024
    scaler=StandardScaler().fit(X[tr]);model=LogisticRegression(C=.1,max_iter=500).fit(scaler.transform(X[tr]),y[tr])
    pasts={};countries={}
    for key,db in [('A',a.baseline),('B',a.updated)]:
        p=LivePast({});p.scan_until(db,DAY);pasts[key]=p
        unique={}
        for h,c in p.last:
            if h not in unique:unique[h]=c
            elif unique[h]!=c:unique[h]=None
        countries[key]=unique
    rows=[];cache={};live_cache={};prepared=[]
    for r in sorted(races,key=lambda z:z.get('off_dt') or ''):
        off=time_utc(r.get('off_dt')); course=r.get('course','');tm=clock(r.get('off_dt'))
        item={'date':DAY,'time':tm,'course':course,'status':'','race_type':'','discipline':'','a_source':'RECONSTRUCTED_ORIGINAL','a_rank1':'','a_rank2':'','b_rank1':'','b_rank2':'','c_rank1':'','c_rank2':'','winner':'','winner_sp':'','snapshot_time_utc':''}
        finish={name(z.get('horse')):z for z in r.get('runners',[]) if z.get('horse')}
        winners=[z for z in r.get('runners',[]) if z.get('position')==1]
        if len(winners)!=1 or not off:item['status']='INVALID_RESULT';rows.append(item);continue
        item['winner']=winners[0]['horse'];item['winner_sp']=winners[0].get('sp') or ''
        available=[(ts,sha) for ts,sha in card_history if ts<off.timestamp()]
        if not available:item['status']='NO_PREOFF_CARD';rows.append(item);continue
        ts,sha=available[-1];item['snapshot_time_utc']=dt.datetime.fromtimestamp(ts,UTC).isoformat()
        if sha not in cache:cache[sha]=git_json(sha,'data/racecards.json')
        rs=[z for z in cache[sha].get('runners',[]) if str(z.get('date') or z.get('race_time') or '')[:10]==DAY and name(z.get('course'))==name(course) and clock(z.get('race_time'))==tm and not nonrunner(z)]
        if len(rs)<2 or len({name(z['horse']) for z in rs})!=len(rs):item['status']='INCOMPLETE_PREOFF_CARD';rows.append(item);continue
        result_field={name(z['horse']) for z in r.get('runners',[]) if z.get('horse') and str(z.get('status','')).lower() not in ('scratched','non-runner','nr')}
        pre_field={name(z['horse']) for z in rs}
        if pre_field!=result_field:item['status']='FIELD_MISMATCH';rows.append(item);continue
        e={'date':DAY,'course':course,'time':tm,'runners':rs}
        prepared.append((item,e,rs,finish,off,pre_field))
    features=C.live_features(a.updated,DAY,[e for _,e,_,_,_,_ in prepared]) if prepared else {}
    for item,e,rs,finish,off,pre_field in prepared:
        course=e['course'];tm=e['time']
        mat,infos=features[(DAY,name(course),tm)]
        item['race_type']=C.category(rs[0].get('race_name') or rs[0].get('race_type') or rs[0].get('type'))
        item['discipline']=infos[0]['discipline']
        ds={}
        for key in ('A','B'):
            scored=pasts[key].score([live_row(z,DAY,'REPLAY|'+DAY+'|'+course+'|'+tm,countries[key]) for z in rs],live=True)
            if scored is None:break
            scores,order,_=prediction_rows(scored,weights);ds[key]=np.asarray(scores,dtype=float)
            item[key.lower()+'_rank1']=rs[int(order[0])]['horse'];item[key.lower()+'_rank2']=rs[int(order[1])]['horse']
        if len(ds)!=2:item['status']='UNSCORABLE';rows.append(item);continue
        learned=model.predict_proba(scaler.transform(mat))[:,1]
        b=ds['B'];dz=(b-b.mean())/(b.std()+1e-8);lz=(learned-learned.mean())/(learned.std()+1e-8)
        combined=.75*dz+.25*lz;rank=sorted(range(len(rs)),key=lambda i:(-combined[i],i))
        item['c_rank1']=rs[rank[0]]['horse'];item['c_rank2']=rs[rank[1]]['horse']
        # Use genuine pre-off live A only if snapshot timestamp precedes this race and field matches.
        snapshots=[(t,s) for t,s in live_history if t<off.timestamp()]
        if snapshots:
            t,s=snapshots[-1]
            if s not in live_cache:live_cache[s]=git_json(s,'data/strategy-d-probabilities.json')
            doc=live_cache[s]
            if doc.get('day')==DAY:
                rr=next((x for x in doc.get('races',[]) if name(x.get('course'))==name(course) and str(x.get('time'))==tm),None)
                if rr:
                    ranked=sorted((z for z in rr.get('runners',[]) if isinstance(z.get('rank'),int)),key=lambda z:z['rank'])
                    if len(ranked)>=2 and {name(z.get('horse')) for z in ranked}==pre_field:
                        item['a_rank1'],item['a_rank2']=ranked[0]['horse'],ranked[1]['horse'];item['a_source']='VERIFIED_PREOFF_LIVE'
        item['status']='COMPARED'
        for key in ('a','b','c'):
            for k in (1,2):
                horse=item[f'{key}_rank{k}'];settle=finish.get(name(horse),{})
                won=int(settle.get('position')==1)
                sp=odds(settle.get('sp')) if won else 0.0
                item[f'{key}_rank{k}_win']=won
                item[f'{key}_rank{k}_return']=sp if sp is not None else ''
        rows.append(item)
    cols=list(dict.fromkeys(k for x in rows for k in x))
    with (out/'nov8_race_by_race.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    valid=[x for x in rows if x['status']=='COMPARED' and all(isinstance(x.get(f'{v}_rank{k}_return'),(int,float)) for v in 'abc' for k in (1,2))]
    stats=[]
    groups={'Overall':valid}
    for typ in sorted({x['race_type'] for x in valid}):groups['Type: '+typ]=[x for x in valid if x['race_type']==typ]
    for disc in sorted({x['discipline'] for x in valid}):groups['Discipline: '+disc]=[x for x in valid if x['discipline']==disc]
    for course in sorted({x['course'] for x in valid}):groups['Track: '+course]=[x for x in valid if x['course']==course]
    for group,rr in groups.items():
        for v in 'abc':
            for ranks,label in [([1],'Rank 1'),([2],'Rank 2'),([1,2],'Combined')]:
                bets=len(rr)*len(ranks);wins=sum(x[f'{v}_rank{k}_win'] for x in rr for k in ranks);ret=sum(x[f'{v}_rank{k}_return'] for x in rr for k in ranks)
                stats.append({'group':group,'model':v.upper(),'rank':label,'races':len(rr),'bets':bets,'wins':wins,'strike_rate_pct':round(100*wins/bets,2) if bets else '', 'profit_units':round(ret-bets,2),'sp_roi_pct':round(100*(ret-bets)/bets,2) if bets else ''})
    with (out/'nov8_summary.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(stats[0]) if stats else ['group','model','rank']);w.writeheader();w.writerows(stats)
    (out/'nov8_audit.json').write_text(json.dumps({'date':DAY,'total_final_races':len(races),'compared':len(valid),'status_counts':dict(collections.Counter(x['status'] for x in rows)),'model_a_sources':dict(collections.Counter(x['a_source'] for x in valid)),'no_postoff_racecards':True,'training_through':'2025-12-31','history_cutoff_exclusive':DAY,'note':'Model A reconstructed when verified pre-off live snapshot unavailable; compare separately from verified live model performance.'},indent=2)+'\n')
    print('8 November replay:',len(valid),'compared of',len(races),'final races',flush=True)
    print(json.dumps([x for x in stats if x['group']=='Overall'],indent=2),flush=True)
if __name__=='__main__':main()
