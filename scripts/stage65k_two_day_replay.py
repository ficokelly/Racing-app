#!/usr/bin/env python3
"""Research-only, conservative 5/6 Nov Model D replay; never writes production files."""
import argparse,collections,csv,datetime as dt,json,pathlib,subprocess,sys
from zoneinfo import ZoneInfo
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from stage38_model_dz_live import LivePast,live_row,prediction_rows,nonrunner,name,clock,time_utc
import stage30_gap_aware_abcd as OLD
UTC=dt.timezone.utc
DAYS=('2026-11-05','2026-11-06')
def git(*args):return subprocess.run(['git',*args],capture_output=True,text=True,check=True).stdout
def snapshots():
    # A git commit is admissible only when its committer timestamp precedes race off.
    lines=git('log','--all','--format=%H %ct','--','data/racecards.json').splitlines()
    return sorted([(int(x.split()[1]),x.split()[0]) for x in lines])
def card_at(commit):
    try:return json.loads(git('show',commit+':data/racecards.json'))
    except Exception:return {}
def performance():
    out={}
    for day in DAYS:
        p=pathlib.Path('data/model-performance')/(day+'.json')
        if not p.exists():continue
        doc=json.loads(p.read_text())
        for race in doc.get('models',{}).get('D',[]):
            key=(day,name(race.get('course')),str(race.get('time') or clock(race.get('race_time'))))
            out[key]=race
    return out
def key_of(r):return (str(r.get('date') or r.get('race_time') or '')[:10],name(r.get('course')),clock(r.get('race_time')))
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--corrected',required=True);ap.add_argument('--out',default='reports/stage65k');a=ap.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    cfg=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert cfg['chosen_combination']=='D-064-gap14' and abs(float(cfg['selection_margin'])-.14)<1e-9
    weights=[float(cfg['weights_pct'][k])/100 for k in OLD.METRICS]
    perf=performance();history=snapshots();cache={};items=[]
    for k,r in sorted(perf.items()):
        day,course,tm=k;off=time_utc(r.get('race_time'))
        entry={'date':day,'course':r.get('course'),'time':tm,'pre_off_valid':bool(r.get('pre_off_valid')),'baseline_rank1':'','baseline_rank2':'','corrected_rank1':'','corrected_rank2':'','rank1_changed':'','rank2_changed':'','status':'','winner':'','baseline_rank1_win':'','corrected_rank1_win':'','baseline_rank2_win':'','corrected_rank2_win':'','baseline_rank1_return':'','corrected_rank1_return':'','baseline_rank2_return':'','corrected_rank2_return':''}
        orig=sorted([x for x in r.get('runners',[]) if isinstance(x.get('rank'),int)],key=lambda x:x['rank'])
        if len(orig)<2 or not off or not r.get('pre_off_valid'):
            entry['status']='NO_VERIFIED_PREOFF_BASELINE';items.append(entry);continue
        entry['baseline_rank1'],entry['baseline_rank2']=orig[0]['horse'],orig[1]['horse']
        ts=int(off.timestamp());eligible=[(t,sha) for t,sha in history if t<ts]
        if not eligible:entry['status']='NO_PREOFF_GIT_CARD_SNAPSHOT';items.append(entry);continue
        sha=eligible[-1][1]
        if sha not in cache:cache[sha]=card_at(sha)
        cards=cache[sha]
        runners=[z for z in cards.get('runners',[]) if key_of(z)==k and not nonrunner(z)]
        if len(runners)<2 or len({name(z['horse']) for z in runners})!=len(runners):
            entry['status']='MISSING_COMPLETE_PREOFF_CARD';items.append(entry);continue
        if {name(z['horse']) for z in runners}!={name(z['horse']) for z in r.get('runners',[]) if z.get('horse') and str(z.get('status','')).lower() not in ('non-runner','nr')}:
            entry['status']='FIELD_CHANGED_OR_UNVERIFIED';items.append(entry);continue
        entry['snapshot_commit']=sha;entry['snapshot_time_utc']=dt.datetime.fromtimestamp(eligible[-1][0],UTC).isoformat()
        entry['_runners']=runners;entry['status']='READY';items.append(entry)
    ready=[e for e in items if e['status']=='READY']
    for day in DAYS:
        target=[e for e in ready if e['date']==day]
        if not target:continue
        for variant,db in [('baseline',a.baseline),('corrected',a.corrected)]:
            past=LivePast({});info=past.scan_until(db,day)
            unique={}
            for h,c in past.last:
                if h not in unique:unique[h]=c
                elif unique[h]!=c:unique[h]=None
            for e in target:
                rs=e['_runners'];rows=[live_row(z,day,'STAGE65K|'+day+'|'+e['course']+'|'+e['time'],unique) for z in rs]
                race=past.score(rows,live=True)
                if race is None:e['status']='UNSCORABLE_'+variant.upper();continue
                scores,order,gap=prediction_rows(race,weights)
                e[variant+'_rank1']=rs[int(order[0])]['horse'];e[variant+'_rank2']=rs[int(order[1])]['horse'];e[variant+'_score_gap']=round(float(gap),6)
                e[variant+'_source_latest']=info['last_result_date']
    for e in items:
        if e['status']!='READY':continue
        if not all(e.get(v+'_rank1') and e.get(v+'_rank2') for v in ('baseline','corrected')):
            e['status']='UNSCORABLE';continue
        e['rank1_changed']=int(name(e['baseline_rank1'])!=name(e['corrected_rank1']))
        e['rank2_changed']=int(name(e['baseline_rank2'])!=name(e['corrected_rank2']))
        original=perf[(e['date'],name(e['course']),e['time'])]
        byhorse={name(z['horse']):z for z in original.get('runners',[])}
        if not all(name(e[v+'_rank'+str(n)]) in byhorse for v in ('baseline','corrected') for n in (1,2)):
            e['status']='NO_MATCHED_SETTLEMENT';continue
        for variant in ('baseline','corrected'):
            for n in (1,2):
                h=byhorse[name(e[variant+'_rank'+str(n)])]
                won=str(h.get('status','')).lower()=='won' or h.get('position')==1
                sp=h.get('sp_decimal')
                e[variant+'_rank'+str(n)+'_win']=int(won)
                e[variant+'_rank'+str(n)+'_return']=float(sp) if won and isinstance(sp,(int,float)) else 0.
        e['status']='COMPARED'
    cols=['date','course','time','status','pre_off_valid','snapshot_commit','snapshot_time_utc','baseline_rank1','corrected_rank1','rank1_changed','baseline_rank2','corrected_rank2','rank2_changed','baseline_score_gap','corrected_score_gap','baseline_source_latest','corrected_source_latest','baseline_rank1_win','corrected_rank1_win','baseline_rank2_win','corrected_rank2_win','baseline_rank1_return','corrected_rank1_return','baseline_rank2_return','corrected_rank2_return']
    with (out/'race_by_race.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(items)
    groups=collections.defaultdict(list)
    for e in items:
        if e['status']!='COMPARED':continue
        groups['combined'].append(e);groups[e['date']].append(e)
        # Course/discipline inferred from immutable race metadata, not post-race results.
        race=perf[(e['date'],name(e['course']),e['time'])]
        kind=str(race.get('race_type') or 'unknown').lower()
        if kind in ('jumps','national hunt','nh'):kind='National Hunt'
        elif kind in ('aw','all-weather','all weather'):kind='All-Weather'
        elif kind in ('flat','turf','flat turf'):kind='Flat Turf'
        else:kind='Unclassified'
        groups[kind].append(e)
    stats=[]
    for group,arr in sorted(groups.items()):
        for variant in ('baseline','corrected'):
            for rank in (1,2,'combined'):
                ranks=(1,2) if rank=='combined' else (rank,)
                bets=sum(len(ranks) for _ in arr)
                wins=sum(int(e[variant+'_rank'+str(n)+'_win']) for e in arr for n in ranks)
                gross=sum(float(e[variant+'_rank'+str(n)+'_return']) for e in arr for n in ranks)
                stats.append({'group':group,'version':variant,'rank':rank,'races':len(arr),'bets':bets,'wins':wins,'strike_rate_pct':round(100*wins/bets,3) if bets else None,'sp_roi_pct':round(100*(gross/bets-1),3) if bets else None})
    with (out/'comparison_summary.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['group','version','rank','races','bets','wins','strike_rate_pct','sp_roi_pct']);w.writeheader();w.writerows(stats)
    status=dict(collections.Counter(e['status'] for e in items))
    report={'research_only':True,'live_model_changed':False,'dates':DAYS,'race_status_counts':status,'compared':len(groups.get('combined',[])),'rank1_changes':sum(e['rank1_changed'] for e in groups.get('combined',[])),'rank2_changes':sum(e['rank2_changed'] for e in groups.get('combined',[])),'warning':'A missing pre-off Git snapshot is excluded, never inferred from final race results. Original Stage2 reconstruction is not proof of live production history. Small two-day sample.'}
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
