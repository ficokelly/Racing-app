#!/usr/bin/env python3
"""Research-only 2026-11-05 original frozen D vs metadata-restored D.
No live writes. Historical database ends before replay date; results are settlement only.
"""
import argparse,csv,datetime as dt,json,math,re,sqlite3,subprocess,sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0,'scripts')
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast,live_row,prediction_rows,nonrunner,name,clock,fractional
from stage62_frozen_d_metadata_replay import load

DAY='2026-11-05'  # Set by --day at runtime
def gitfile(rev,path):
    p=subprocess.run(['git','show',f'{rev}:{path}'],capture_output=True,text=True)
    if p.returncode:return None
    try:return json.loads(p.stdout)
    except ValueError:return None

def snapshot():
    # Git history must have a genuine pre-off snapshot; never use present-day cards as substitute.
    commits=subprocess.check_output(['git','rev-list',f'--before={DAY}T12:00:00Z','--max-count=60','HEAD','--','data/racecards.json'],text=True).splitlines()
    for rev in commits:
        d=gitfile(rev,'data/racecards.json')
        if not isinstance(d,dict):continue
        r=[x for x in d.get('runners',[]) if str(x.get('date') or x.get('race_time') or '')[:10]==DAY]
        if len(r)>=50:return rev,r
    raise RuntimeError(f'No archived pre-off {DAY} racecards with >=50 runners; cannot reconstruct reliably')

def copy_metadata(db,mapping):
    lookup,audit=load(mapping)
    cx=sqlite3.connect(db)
    found={r[0]:r[1] or '' for r in cx.execute('select id,race_type from historical_races')}
    updates=[]
    for rid,(typ,_) in lookup.items():
        if rid not in found:continue
        old=found[rid].lower();new=typ.lower()
        if old==new:continue
        def j(s):return 'chase' if 'chase' in s else 'hurdle' if 'hurdle' in s else ''
        if j(old) and j(new) and j(old)!=j(new):continue
        updates.append((typ,rid))
    cx.executemany('update historical_races set race_type=? where id=?',updates)
    cx.commit();cx.close()
    return len(updates),audit

def predict(db,cards):
    conf=json.loads(Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert conf['chosen_combination']=='D-064-gap14' and float(conf['selection_margin'])==.14
    weights=[float(conf['weights_pct'][k])/100 for k in OLD.METRICS]
    hist=LivePast({});coverage=hist.scan_until(db,DAY)
    grouped=defaultdict(list)
    for r in cards:
        if not r.get('horse') or not r.get('course') or not r.get('race_time') or nonrunner(r):continue
        grouped[(name(r['course']),clock(r['race_time']))].append(r)
    output={}
    for (course,t),runners in sorted(grouped.items()):
        if len(runners)<2 or len({name(x['horse']) for x in runners})!=len(runners):continue
        rows=[live_row(r,DAY,f'STAGE63|{DAY}|{course}|{t}') for r in runners]
        result=hist.score(rows,live=True)
        if result is None:continue
        scores,order,gap=prediction_rows(result,weights)
        ranks=[{'horse':runners[int(i)]['horse'],'rank':j+1,'score':round(float(scores[int(i)]),6)} for j,i in enumerate(order)]
        output[(course,t)]={'course':runners[0]['course'],'time':t,'kind':result['kind'],'gap':gap,'selected':gap>=.14,'ranks':ranks}
    return output,coverage

def results_index(results):
    d=defaultdict(list)
    for r in results.get('races',[]):
        if r.get('date')!=DAY or r.get('status')!='final':continue
        t=clock(r.get('off_dt') or r.get('race_time') or '')
        if t:d[(name(r.get('course')),t)].append(r)
    return d

def settle(race,horse):
    if not race:return None,None
    hits=[x for x in race.get('runners',[]) if name(x.get('horse'))==name(horse)]
    if len(hits)!=1:return None,None
    x=hits[0]
    if name(x.get('status')) in ('nr','nonrunner','non runner','withdrawn','scratched'):return 'NR',None
    try:pos=int(x.get('position'))
    except (ValueError,TypeError):return None,None
    return pos,fractional(x.get('sp'))

def report(rows,prefix,rank):
    sub=[x for x in rows if x[f'{prefix}_rank{rank}_pos'] not in ('','NR') and x[f'{prefix}_rank{rank}_sp']!='']
    wins=sum(x[f'{prefix}_rank{rank}_pos']=='1' for x in sub)
    ret=sum(float(x[f'{prefix}_rank{rank}_sp']) for x in sub if x[f'{prefix}_rank{rank}_pos']=='1')
    return {'priced_stakes':len(sub),'winners':wins,'strike_rate_pct':round(100*wins/len(sub),2) if sub else None,'gross_units':round(ret,3),'roi_pct':round(100*(ret/len(sub)-1),2) if sub else None}

def main(args):
    args.out.mkdir(parents=True,exist_ok=True)
    rev,cards=snapshot()
    results=json.loads(Path('data/race-results.json').read_text())
    baseline,basecoverage=predict(args.db,cards)
    # Patch a research copy only.
    import shutil
    patched=args.out/'metadata_research.sqlite';shutil.copy2(args.db,patched)
    updated,audit=copy_metadata(patched,args.mapping)
    revised,revcoverage=predict(patched,cards)
    patched.unlink()
    settled=results_index(results)
    records=[]
    for key in sorted(set(baseline)|set(revised),key=lambda k:(k[1],k[0])):
        b=baseline.get(key);n=revised.get(key)
        matches=settled.get(key,[])
        race=matches[0] if len(matches)==1 else None
        x={'date':DAY,'course':(b or n)['course'],'time':key[1],'result_match':len(matches),
           'original_selection':bool(b and b['selected']),'metadata_selection':bool(n and n['selected']),
           'rank1_changed':bool(b and n and name(b['ranks'][0]['horse'])!=name(n['ranks'][0]['horse'])),
           'rank2_changed':bool(b and n and name(b['ranks'][1]['horse'])!=name(n['ranks'][1]['horse']))}
        for label,r in [('original',b),('metadata',n)]:
            for rank in (1,2):
                horse=r['ranks'][rank-1]['horse'] if r else ''
                pos,sp=settle(race,horse)
                x.update({f'{label}_rank{rank}':horse,f'{label}_rank{rank}_pos':str(pos) if pos is not None else '',
                          f'{label}_rank{rank}_sp':sp if sp is not None else ''})
        records.append(x)
    with (args.out/'stage63_race_by_race.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=records[0].keys());w.writeheader();w.writerows(records)
    metrics={label:{'rank1':report(records,label,1),'rank2':report(records,label,2)} for label in ('original','metadata')}
    changed=[x for x in records if x['rank1_changed'] or x['rank2_changed']]
    summary={'date':DAY,'status':'RESEARCH_ONLY','snapshot_commit':rev,'snapshot_runners':len(cards),
       'original_races':len(baseline),'metadata_races':len(revised),'settled_result_races':sum(x['result_match']==1 for x in records),
       'historical_races_metadata_patched':updated,'rank1_changed':sum(x['rank1_changed'] for x in records),
       'rank2_changed':sum(x['rank2_changed'] for x in records),'changes':changed,'metrics':metrics,
       'historical_source_coverage':basecoverage,'limitations':['Uses one pre-off Git snapshot, not every intraday ranking.','Only source-supported race_type restoration; no new trend scoring.','SP-only unit-stake ROI; no bookmaker odds or deductions.','Unmatched/NR/unpriced horses excluded from priced ROI; counts shown.','Historical database must predate 2026-11-05; research only.']}
    (args.out/'stage63_summary.json').write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps({k:v for k,v in summary.items() if k!='changes'},indent=2,default=str))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--mapping',type=Path,required=True);p.add_argument('--day',choices=['2026-11-04','2026-11-05'],default='2026-11-05');p.add_argument('--out',type=Path,default=Path('reports/stage63'));a=p.parse_args();DAY=a.day;main(a)
