#!/usr/bin/env python3
"""Racing Hot Pots Model D(c): publish race-type-aware research rankings.

The original frozen D and the 2024/2025-trained C form model remain unchanged.
No SP, result, or post-off prices from target races inform their rankings.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, pathlib, sys
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import stage65p_race_type_c_only as C
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast, live_row, prediction_rows, name, clock, nonrunner, time_utc

IE=ZoneInfo('Europe/Dublin'); UTC=dt.timezone.utc
ROOT=pathlib.Path(__file__).resolve().parent.parent
DATA=ROOT/'data'
FEED=DATA/'strategy-dc-probabilities.json'
SCHEDULE=DATA/'strategy-dc-schedule.json'

def read(path, default):
    try:return json.loads(pathlib.Path(path).read_text())
    except (FileNotFoundError,ValueError):return default

def write(path,obj):
    pathlib.Path(path).parent.mkdir(parents=True,exist_ok=True)
    pathlib.Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')

def fields_for(day):
    cards=read(DATA/'racecards.json',{})
    groups=collections.defaultdict(list)
    for r in cards.get('runners',[]):
        if not isinstance(r,dict) or str(r.get('date') or r.get('race_time') or '')[:10]!=day:continue
        if not all(r.get(k) for k in ('horse','course','race_time')) or nonrunner(r):continue
        groups[(name(r['course']),clock(r['race_time']))].append(r)
    groups={k:v for k,v in groups.items() if len(v)>=2 and len({name(z['horse']) for z in v})==len(v)}
    return [{'date':day,'course':rs[0]['course'],'time':t,'runners':rs} for (_place,t),rs in sorted(groups.items(),key=lambda x:(x[0][1],x[0][0]))]

def c_classifier(db):
    """Reuse unchanged 2024-trained C coefficients across daily selection slots.

    The Stage69 cache is keyed by this script and the feature-engine source in
    GitHub Actions. The historical 2024/2025 input is immutable dataset v129;
    2026 result backfills cannot change the fixed training split.
    """
    import joblib
    saved=ROOT/'reports/stage68/base/c_classifier_2024.joblib'
    if saved.is_file():
        obj=joblib.load(saved)  # Our own GitHub Actions cache, never user input.
        valid=(isinstance(obj,dict) and obj.get('format')=='rhp-dc-c-trained-v1'
               and obj.get('features')==list(C.FEATURES)
               and obj.get('training_year')==2024 and obj.get('validation_year')==2025
               and obj.get('training_rows',0)>=100 and obj.get('validation_rows',0)>=100)
        if valid:
            scaler,model=obj.get('scaler'),obj.get('model')
            if getattr(scaler,'n_features_in_',None)==len(C.FEATURES) and getattr(model,'n_features_in_',None)==len(C.FEATURES):
                print('Stage69: reusing fixed C classifier fitted on 2024 (no retraining)',flush=True)
                return scaler,model
        raise RuntimeError('Cached C research classifier failed provenance/feature validation')
    X,y,years,counts,_=C.build_training(db)
    tr=(years==2024); va=(years==2025)
    if tr.sum()<100 or va.sum()<100 or y[tr].sum()<10:
        raise RuntimeError('Insufficient time-separated C training samples')
    for yr in (2024,2025):
        total=sum(v for (year,cat),v in counts.items() if year==yr)
        classified=sum(v for (year,cat),v in counts.items() if year==yr and cat!='unknown')
        if not total or classified/total<.60:
            raise RuntimeError('Race type coverage too low: '+str(yr))
    scaler=StandardScaler().fit(X[tr])
    model=LogisticRegression(C=.1,max_iter=500).fit(scaler.transform(X[tr]),y[tr])
    saved.parent.mkdir(parents=True,exist_ok=True)
    joblib.dump({'format':'rhp-dc-c-trained-v1','features':list(C.FEATURES),
                 'training_year':2024,'validation_year':2025,
                 'training_rows':int(tr.sum()),'validation_rows':int(va.sum()),
                 'scaler':scaler,'model':model},saved)
    print('Stage69: fitted and cached race-type C classifier',int(tr.sum()),'training rows',flush=True)
    return scaler,model


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',required=True)
    p.add_argument('--day',required=True)
    p.add_argument('--slot',default='manual')
    a=p.parse_args();dt.date.fromisoformat(a.day)
    if not pathlib.Path(a.db).is_file():raise SystemExit('No validated historical database; refuse publication')
    entries=fields_for(a.day)
    if not entries:raise SystemExit('No complete racecard fields for '+a.day+'; refuse publication')
    scaler,model=c_classifier(a.db)
    # Import the exact frozen D weights; DO NOT retrain D on 2025 outcomes.
    audit=read(ROOT/'reports/stage30/stage30-summary.json',{})
    frozen=audit.get('model_results',{}).get('D',{})
    if frozen.get('chosen_combination')!='D-064-gap14':
        raise SystemExit('Frozen D weights changed; refuse publication')
    gate=float(frozen['selection_margin'])
    if abs(gate-0.14)>1e-9:raise SystemExit('Frozen D research gate changed')
    weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
    past=LivePast({});history_info=past.scan_until(a.db,a.day)
    unique={}
    for h,country in past.last:
        if h not in unique:unique[h]=country
        elif unique[h]!=country:unique[h]=None
    feats=C.live_features(a.db,a.day,entries)
    now=dt.datetime.now(UTC)
    existing=read(FEED,{})
    # Retain prior immutable pre-off snapshots for other dates, even when racecards roll.
    retained={}
    day_floor=(dt.date.fromisoformat(a.day)-dt.timedelta(days=2)).isoformat()
    for r in existing.get('races',[]):
        if isinstance(r,dict) and r.get('date','')>=day_floor:
            retained[(r.get('date'),name(r.get('course')),r.get('time'))]=r
    new_count=0
    for e in entries:
        rs=e['runners'];off=time_utc(rs[0]['race_time'])
        key=(a.day,name(e['course']),e['time'])
        if not off or off <= now+dt.timedelta(minutes=2):continue
        matrix,infos=feats[(a.day,name(e['course']),e['time'])]
        original=past.score([live_row(z,a.day,'D-C|'+a.day+'|'+name(e['course'])+'|'+e['time'],unique) for z in rs],live=True)
        if original is None:continue
        d_scores,_,_=prediction_rows(original,weights)
        learned=model.predict_proba(scaler.transform(matrix))[:,1]
        dz=(d_scores-d_scores.mean())/(d_scores.std()+1e-8)
        lz=(learned-learned.mean())/(learned.std()+1e-8)
        combined=.75*dz+.25*lz
        order=sorted(range(len(rs)),key=lambda i:(-float(combined[i]),i))
        top,second=order[:2]
        # Preserve the frozen D research-only 0.14 gap rule, and require C to
        # agree with D's own top runner. It is NOT a validated betting edge.
        d_order=sorted(range(len(rs)),key=lambda i:-float(d_scores[i]))
        agrees=top==d_order[0]
        frozen_gap=float(d_scores[d_order[0]]-d_scores[d_order[1]])
        selected=bool(agrees and frozen_gap>=gate)
        runners=[]
        for rank,i in enumerate(order,1):
            z=rs[i]
            runners.append({'horse':z['horse'],'horse_id':z.get('horse_id'),'rank':rank,
                'research_score':round(float(combined[i]),6),
                'original_d_score':round(float(d_scores[i]),6),
                'race_type_score':round(float(learned[i]),6),
                'race_category':infos[i].get('category')})
        best=runners[0];next_best=runners[1]
        v={'model':'D(c)','model_version':'2026-11-09-stage68-race-type-aware-v1',
           'date':a.day,'course':e['course'],'time':e['time'],'race_time':rs[0]['race_time'],
           'race_type':str(rs[0].get('type') or rs[0].get('race_type') or ''),
           'race_name':str(rs[0].get('race_name') or ''),
           'field_size':len(rs),'runners':runners,'selection':best if selected else None,
           'selection_status':'RESEARCH_ONLY' if selected else 'NO_SELECTION',
           'decision':{'leader':best['horse'],'second':next_best['horse'],
                       'gap':round(float(combined[top]-combined[second]),6),
                       'original_d_gap':round(frozen_gap,6),'minimum_gap':gate,
                       'frozen_d_agreement':agrees,'metric':'75% D zscore + 25% race-type C zscore'},
           'complete_field':True,'published_at':now.isoformat(),
           'verdict':f"Hot Pots ranks {best['horse']} ahead of {next_best['horse']} using race-type-aware form and the frozen D evidence. "+
                     ('Research shortlist gate met; profitability not established.' if selected else 'No research shortlist qualified.'),
           'selection_policy':'RESEARCH_ONLY_UNPROVEN_EDGE'}
        retained[key]=v;new_count+=1
    if new_count<1:raise SystemExit('No fresh pre-off C scores generated; preserve prior predictions')
    rows=sorted(retained.values(),key=lambda x:(x['date'],x['race_time'],x['course']))
    feed={'model':'D(c)','model_version':'2026-11-09-stage68-race-type-aware-v1','day':a.day,
          'generated_at':now.isoformat(),'state':'READY','races':rows,
          'research_shortlists':sum(r.get('selection_status')=='RESEARCH_ONLY' for r in rows),
          'historical_data_through':history_info.get('last_result_date'),
          'method':'Frozen D factors 75%; race-type-trained C features 25% (z-score combination)',
          'selection_policy':'Research only. C leader must agree with frozen D and D gap >= 0.14; unproven ROI.',
          'warning':'Research scores are NOT winning probabilities, betting advice, or proven profitable selections.'}
    write(FEED,feed)
    schedule=read(SCHEDULE,{'runs':{}})
    schedule.setdefault('runs',{})[a.day+'|'+a.slot]={'generated_at':now.isoformat(),'races':new_count}
    # Cap scheduler audit, retain 14 days.
    keep=(now.astimezone(IE).date()-dt.timedelta(days=14)).isoformat()
    schedule['runs']={k:v for k,v in schedule['runs'].items() if k[:10]>=keep}
    write(SCHEDULE,schedule)
    print('Model D(c) published',new_count,'races for',a.day,'slot',a.slot,'research shortlist',sum(x['selection_status']=='RESEARCH_ONLY' for x in rows))

if __name__=='__main__':main()
