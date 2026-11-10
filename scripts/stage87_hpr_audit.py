#!/usr/bin/env python3
"""Stage87: HPR evidence-quality audit and matched-date frozen-model comparison.

Reports only: do not change HPR version, stored ratings, live selections,
Model D, Model D(c), or the Stage86 scoring algorithm. Missing identity, beaten
lengths and outcomes remain unknown unless the evidence proves otherwise.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0,str(pathlib.Path(__file__).parent))
import stage86_hpr_engine as hpr


def csv_write(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def csv_read(path):
    if not path.is_file():return []
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def compare(root,e_file,out,start,end):
    e_rows=csv_read(e_file)
    by_e=collections.defaultdict(dict)
    for e in e_rows:
        d=hpr.day(e.get('date'))
        if d is None or not start<=d<=end or str(e.get('rank')) not in ('1','2'):continue
        key=(str(d),hpr.course(e.get('course')),str(e.get('time') or '')[:5])
        if int(e['rank']) in by_e[key]:raise ValueError('Duplicate E rank '+str(key))
        by_e[key][int(e['rank'])]=e
    daily=collections.defaultdict(dict)
    models_seen=collections.Counter()
    for d in (start+dt.timedelta(days=i) for i in range((end-start).days+1)):
        p=root/'data/model-performance'/(str(d)+'.json')
        doc=hpr.read_json(p,{}) or {}
        for model,races in doc.get('models',{}).items():
            for race in races:
                if not race.get('pre_off_valid'):continue
                try:
                    snapshot=dt.datetime.fromisoformat(str(race['snapshot_at']).replace('Z','+00:00'))
                    off=dt.datetime.fromisoformat(str(race['race_time']).replace('Z','+00:00'))
                    if snapshot.tzinfo is None or off.tzinfo is None or snapshot>=off:continue
                except (KeyError,ValueError,TypeError):
                    continue  # no verified point-in-time cutoff, no comparison
                key=(str(d),hpr.course(race.get('course')),str(race.get('time') or '')[:5])
                if key[1] and key[2]:
                    models_seen[model]+=1
                    if key in daily[model]:
                        raise ValueError('Duplicate model snapshots for '+model+' '+str(key))
                    daily[model][key]=race
    # A comparable race is the intersection of exact date, course, off time and
    # verified pre-off rank-1 and rank-2 selections. Absent D(c) is NOT D.
    results=[];excluded=collections.Counter()
    for key,e_by_rank in sorted(by_e.items()):
        if set(e_by_rank)!={1,2}:
            excluded['E_MISSING_TOP_TWO']+=1;continue
        cohorts={'E':e_by_rank}
        d_snapshot=daily.get('D',{}).get(key)
        if d_snapshot is None:
            excluded['D_NO_MATCHED_PREOFF_SNAPSHOT']+=1;continue
        racers=d_snapshot.get('runners',[])
        rankmap={int(z['rank']):z for z in racers if str(z.get('rank')) in ('1','2')}
        if set(rankmap)!={1,2} or len(racers)<2:
            excluded['D_MISSING_TOP_TWO']+=1;continue
        e_horses={hpr.norm(z.get('horse')) for z in e_by_rank.values()}
        d_all={hpr.norm(z.get('horse')) for z in racers}
        if not e_horses<=d_all:
            excluded['HPR_RUNNER_ABSENT_IN_D_SNAPSHOT']+=1;continue
        cohorts['D']=rankmap
        dc_snapshot=(daily.get('D(c)',{}).get(key) or daily.get('DC',{}).get(key))
        if dc_snapshot:
            rankdc={int(z['rank']):z for z in dc_snapshot.get('runners',[]) if str(z.get('rank')) in ('1','2')}
            if set(rankdc)=={1,2}:cohorts['D(c)']=rankdc
            else:excluded['DC_INCOMPLETE_RANKS']+=1
        else:excluded['DC_NO_MATCHED_PREOFF_SNAPSHOT']+=1
        for model,ranks in cohorts.items():
            for n,z in sorted(ranks.items()):
                sp=hpr.number(z.get('sp_decimal'))
                if sp is None or sp<1:sp=None
                if model=='E':
                    is_win=str(z.get('win'))=='1'
                    status='WIN' if is_win else 'LOSS'
                else:
                    status=str(z.get('status') or '').strip().upper()
                    is_win=status=='WON' or hpr.number(z.get('position'))==1
                    if status in ('PENDING','VOID','UNKNOWN','') and not is_win:
                        status='UNVERIFIED'
                if sp is None and model!='E':
                    # Historical model snapshots already carry verified SP,
                    # never substitute the pre-off market price here.
                    sp=hpr.number(z.get('sp_decimal'))
                results.append({'date':key[0],'course':d_snapshot.get('course'),
                    'time':key[2],'race_type':d_snapshot.get('race_type'),'model':model,'rank':n,
                    'horse':z.get('horse'),'win':int(is_win) if status!='UNVERIFIED' else '',
                    'position':z.get('position'),'sp_decimal':sp if sp else '',
                    'return_units':sp if is_win and sp else (0 if sp and status!='UNVERIFIED' else ''),
                    'settled':bool(sp and status!='UNVERIFIED'),'match_basis':'DATE_COURSE_TIME_E_AND_D_PREOFF'})
    csv_write(out/'stage87-exact-race-selections.csv',results,
        ['date','course','time','race_type','model','rank','horse','win','position','sp_decimal','return_units','settled','match_basis'])
    aggregates=[]
    for model in ('D','D(c)','E'):
        for grouping,entries in [('Rank 1',[x for x in results if x['model']==model and x['rank']==1]),
                                 ('Rank 2',[x for x in results if x['model']==model and x['rank']==2]),
                                 ('Combined',[x for x in results if x['model']==model])]:
            settled=[x for x in entries if x['settled']]
            winrows=[x for x in entries if x['win']!='']
            wins=sum(x['win'] for x in winrows)
            returned=sum(float(x['return_units']) for x in settled)
            aggregates.append({'model':model,'group':grouping,'matched_selections':len(entries),
                'wins_verified':wins,'strike_rate_pct':round(100*wins/len(winrows),2) if winrows else None,
                'priced_selections':len(settled),
                'sp_roi_pct':round(100*(returned/len(settled)-1),2) if settled else None})
    csv_write(out/'stage87-matched-performance.csv',aggregates,
              ['model','group','matched_selections','wins_verified','strike_rate_pct','priced_selections','sp_roi_pct'])
    return {'model_preoff_snapshots':dict(models_seen),'model_comparison':aggregates,
            'exclusions':dict(excluded),'comparison_is_same_races':True,
            'dc_status':'UNAVAILABLE_UNLESS_VERIFIED_PREOFF_RANKS_EXIST',
            'caveat':'D and E compare on exact race intersection; D(c) only if separately archived true pre-off ranked snapshots exist. No extrapolation to unmatched races.'}


def audit(root,source_db,out,asof,days,e_file):
    start=asof-dt.timedelta(days=days);end=asof-dt.timedelta(days=1)
    earliest=max(start-dt.timedelta(days=90),dt.date(2026,10,8))
    results,bad=hpr.load_results(root,earliest,end)
    snapshots=hpr.card_dates(root,earliest,end,git_preoff=True)
    cardmaps={d:hpr.by_race_cards(c) for d,c in snapshots.items()}
    cards=[r for daily in snapshots.values() for r in daily]
    profiles,names=hpr.profiles_from_cards(cards)
    for race in results.values():
        for z in race['runners']:
            k=hpr.identity(z)
            if k:profiles.setdefault(k,z);names[hpr.norm(z.get('horse'))].add(k)
    history,hinfo=hpr.historical_matches(source_db,profiles,start)
    recent,oinfo=hpr.result_events(results,cardmaps,profiles,names,earliest,end)
    failure=[];low=[];quality=collections.Counter();outcomes=collections.Counter()
    source_quality=collections.Counter();valid=0;missing_margin=0
    seen=set()
    for e in sorted(history+recent,key=lambda z:(z['date'],z['race_key'],z['horse_key'])):
        signature=(e['horse_key'],e['date'],hpr.course(e['course']))
        if signature in seen:continue
        seen.add(signature)
        q,reason=hpr.run_quality(e)
        quality[reason]+=1
        label=str(e.get('status') or '').upper().strip()
        if q is None:
            outcomes[(reason,label)]+=1
            failure.append({'date':e.get('date'),'course':e.get('course'),
                'horse':e.get('horse'),'horse_key':e.get('horse_key'),'race_key':e.get('race_key'),
                'race_id':e.get('source_race_id'),'finish_pos':e.get('position'),
                'raw_outcome':e.get('status'),'reason':reason,'source':e.get('source'),
                'resolution':'REQUIRES_VERIFIED_FINISH_OR_EXPLICIT_NONFINISHER_EVIDENCE'})
        else:
            valid+=1
            beaten=hpr.parse_beaten(e.get('beaten'))
            source_quality[(e.get('source'),reason)]+=1
            if reason=='FINISHED_NO_MARGIN':
                missing_margin+=1
                low.append({'date':e.get('date'),'course':e.get('course'),
                    'horse':e.get('horse'),'horse_key':e.get('horse_key'),
                    'race_key':e.get('race_key'),'race_id':e.get('source_race_id'),
                    'position':e.get('position'),'raw_beaten':e.get('beaten'),
                    'source':e.get('source'),'action':'LOOKUP_EXACT_RESULT_MARGIN_WITH_SOURCE_ID'})
    csv_write(out/'stage87-rejected-outcomes.csv',failure,
        ['date','course','horse','horse_key','race_key','race_id','finish_pos','raw_outcome','reason','source','resolution'])
    csv_write(out/'stage87-beaten-distance-queue.csv',low,
        ['date','course','horse','horse_key','race_key','race_id','position','raw_beaten','source','action'])
    csv_write(out/'stage87-outcome-reasons.csv',
        [{'reason':reason,'raw_outcome':label,'records':n} for (reason,label),n in outcomes.most_common()],
        ['reason','raw_outcome','records'])
    comp=compare(root,e_file,out,start,end)
    report={'stage':87,'model':'E_HPR_RESEARCH_ONLY','scope':{'as_of':str(asof),'start':str(start),
        'end':str(end),'days':days,'territory':'UK and Ireland'},
        'source_races':len(results),'preoff_days':len(snapshots),'horse_profiles':len(profiles),
        'historical_match_info':hinfo,'result_match_info':oinfo,'invalid_results':bad,
        'matched_historical_events':len(history),'recent_events':len(recent),
        'deduplicated_rated_eligible':valid,'rejected_finishing_records':len(failure),
        'rejected_outcome_reasons':dict(quality),'missing_beaten_distance_on_finished_runs':missing_margin,
        'performance_evidence_by_source':{str(k):v for k,v in source_quality.items()},
        'historical_name_only_skipped_unverified':hinfo.get('name_only_skipped'),
        'comparison':comp,'status':'DIAGNOSTIC_ONLY_NO_RECOVERY_IMPLIED',
        'warning':'All missing data remains unverified. No HPR score revised. No changes to D or D(c).'}
    hpr.save_json(out/'stage87-hpr-audit.json',report)
    print(json.dumps({'matched_historical_events':len(history),
        'rejected_finishing_records':len(failure),'missing_beaten_distance':missing_margin,
        'compared_D_rank1':[v for v in comp['model_comparison'] if v['model']=='D' and v['group']=='Rank 1']},indent=2))
    return report


def self_test():
    with __import__('tempfile').TemporaryDirectory() as td:
        root=pathlib.Path(td)
        data=root/'data/model-performance';data.mkdir(parents=True)
        (data/'2026-11-09.json').write_text(json.dumps({'models':{
            'D':[{'date':'2026-11-09','course':'Ascot','time':'13:00','pre_off_valid':True,'snapshot_at':'2026-11-09T12:00:00+00:00',
                'race_time':'2026-11-09T13:00:00+00:00',
                'runners':[{'horse':'Alpha','rank':1,'position':1,'status':'Won','sp_decimal':3},
                           {'horse':'Beta','rank':2,'position':2,'status':'Lost','sp_decimal':5}]}]}}))
        ef=root/'e.csv'
        csv_write(ef,[{'date':'2026-11-09','course':'Ascot','time':'13:00','rank':1,'horse':'Beta','win':0,'position':2,'sp_decimal':5},
                     {'date':'2026-11-09','course':'Ascot','time':'13:00','rank':2,'horse':'Alpha','win':1,'position':1,'sp_decimal':3}],
                 ['date','course','time','rank','horse','win','position','sp_decimal'])
        x=compare(root,ef,root/'output',dt.date(2026,11,9),dt.date(2026,11,9))
        assert x['model_comparison'][0]['wins_verified']==1
        assert x['model_comparison'][0]['sp_roi_pct']==200.0
        assert next(z for z in x['model_comparison'] if z['model']=='E' and z['group']=='Rank 1')['sp_roi_pct']==-100.0
        assert next(z for z in x['model_comparison'] if z['model']=='D(c)' and z['group']=='Rank 1')['sp_roi_pct'] is None
    print('PASS: matched race Rank1/Rank2, SP ROI and missing D(c) not guessed')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    ap.add_argument('--db',type=pathlib.Path)
    ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage87'))
    ap.add_argument('--e-selections',type=pathlib.Path,default=pathlib.Path('reports/stage86/test/hpr-race-by-race.csv'))
    ap.add_argument('--as-of',type=str,default='')
    ap.add_argument('--days',type=int,default=30)
    a=ap.parse_args()
    if a.self_test:self_test();return
    if not a.db or not a.db.is_file():ap.error('--db requires the historical Stage86 source SQLite')
    asof=hpr.day(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
    if asof is None or not 1<=a.days<=90:ap.error('Invalid as-of or days')
    a.output.mkdir(parents=True,exist_ok=True)
    audit(a.root,a.db,a.output,asof,a.days,a.e_selections)

if __name__=='__main__':main()
