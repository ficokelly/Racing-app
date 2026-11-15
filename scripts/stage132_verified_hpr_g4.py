#!/usr/bin/env python3
"""Stage132: verified HPR-v3.1 evidence recovery + 2026 G4 comparison (paper-only).

No writes to live app, original HPR, original model weights, or historical archives.
No odds or target-race finishing data in model inputs. Uses race-ID-exact
joins for recovery, preserving unknown evidence rather than fabricating it.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import sqlite3
import statistics
import sys
from collections import defaultdict

import stage131_class_weight_hpr as S

V='HPR-v3.1-class-weight-exact-evidence-2026-11-15-research'
B=S.BASE_HPR_VERSION


def norm(x):
    import unicodedata
    s=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().upper()
    return re.sub(r'[^A-Z0-9]','',s)


def load_original(store, daily=None):
    if not store.is_file():raise FileNotFoundError(store)
    db=sqlite3.connect('file:'+str(store.resolve())+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Damaged source HPR')
    rows=db.execute('''SELECT r.payload FROM hpr_ratings r JOIN
      (SELECT horse_key,race_key,model_version,MAX(revision) rev FROM hpr_ratings
       WHERE model_version=? GROUP BY horse_key,race_key,model_version) q
      ON q.horse_key=r.horse_key AND q.race_key=r.race_key
      AND q.model_version=r.model_version AND q.rev=r.revision''',(B,))
    data={(e['horse_key'],e['race_key']):e for (s,) in rows if (e:=json.loads(s))}
    db.close()
    counts=collections.Counter()
    if daily and daily.is_dir():
        for path in sorted(daily.glob('20??-??-??.json')):
            for e in json.loads(path.read_text()).get('ratings',[]):
                if e.get('model_version')!=B:continue
                key=(e.get('horse_key'),e.get('race_key'))
                if not all(key):continue
                if key not in data:data[key]=e;counts['added']+=1
                elif e.get('source_digest')!=data[key].get('source_digest'):
                    # A conflicting daily file is not authoritative over canonical DB.
                    counts['conflicting_daily_sources_not_overwritten']+=1
                else:counts['already_present']+=1
    return list(data.values()),dict(counts)


def archive_exact_meta(dbpath, needed, reference):
    """Enrich ONLY existing HIST|race_id events using exact race IDs, date,
    course; full-field rating and weight stats from pre-race runner attributes.
    Reference names may reveal graded/class status missing in archive metadata.
    """
    if not dbpath.is_file():raise FileNotFoundError(dbpath)
    conn=sqlite3.connect('file:'+str(dbpath.resolve())+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Damaged historical DB')
    import stage108_model_g2 as G
    ref,audit=G.load_reference(reference,first='1988-01-01',last='2026-12-31')
    result={};stats=collections.Counter()
    for i in range(0,len(needed),450):
        group=needed[i:i+450]
        query='''SELECT r.id rid,r.race_date date,r.course course,r.race_class race_class,
            r.race_type race_type,u.official_rating orating,u.weight_lbs weight,u.outcome outcome
            FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
            WHERE r.id IN ('''+','.join('?' for _ in group)+') ORDER BY r.id'''
        field=defaultdict(list)
        for x in conn.execute(query,group):field[str(x['rid'])].append(dict(x))
        for rid,rows in field.items():
            r=rows[0]
            rated=[S.num(x['orating']) for x in rows if x['orating'] is not None and str(x['outcome'] or '').lower() not in ('nr','nonrunner','withdrawn','dns')]
            rated=[x for x in rated if x is not None and 0<x<190]
            weights=[S.num(x['weight']) for x in rows if str(x['outcome'] or '').lower() not in ('nr','nonrunner','withdrawn','dns')]
            weights=[x for x in weights if x is not None and 80<x<220]
            meta=ref.get(rid) or ref.get(int(rid)) if str(rid).isdigit() else ref.get(rid)
            name=(meta[3] if meta and len(meta)>3 else '')
            # Avoid using vague race names from an unrelated date/course.
            if meta and (str(meta[0])!=str(r['date']) or norm(meta[1])!=norm(r['course'])):
                name='';stats['reference_identity_mismatch']+=1
            result[str(rid)]={'date':str(r['date']),'course':str(r['course']),
                'race_class':r['race_class'],'race_type':r['race_type'],'race_name':name,
                'field_or':S.avg(rated) if len(rated)>=3 and len(rated)>=math.ceil(len(rows)*.5) else None,
                'field_weight':S.avg(weights) if len(weights)>=3 and len(weights)>=math.ceil(len(rows)*.5) else None,
                'rated_count':len(rated),'weight_count':len(weights),'full_runners':len(rows),
                'matched_named_reference':bool(name)}
            stats['verified_archive_race_ids']+=1
            if name:stats['with_verified_named_reference']+=1
    conn.close()
    return result,dict(stats)


def class_and_field(event,fullfield,archive):
    evidence={'class_source':'RECORDED' if S.strength(event)[0] else 'UNKNOWN',
              'field_source':'HPR_EXACT_RACE_GROUP','warnings':[]}
    record=dict(event)
    key=str(record.get('race_key') or '')
    ar=archive.get(key[5:]) if key.startswith('HIST|') else None
    if ar:
        if str(ar['date'])!=str(record.get('date')) or norm(ar['course'])!=norm(record.get('course')):
            evidence['warnings'].append('ARCHIVE_ID_DATE_COURSE_CONFLICT')
            ar=None
        else:
            evidence['field_source']='FULL_ARCHIVE_EXACT_RACE_ID'
            # Better class context from fully verified historical race title.
            if not S.strength(record)[0] and ar.get('race_name'):
                record['race_name']=ar['race_name'];evidence['class_source']='VERIFIED_NAMED_RACE_REFERENCE'
            if not S.strength(record)[0] and ar.get('race_class'):
                record['race_class']=ar['race_class'];evidence['class_source']='VERIFIED_HISTORICAL_RACE_FIELD'
            if not S.strength(record)[0]:evidence['class_source']='UNKNOWN'
    group=fullfield[(str(record.get('date')),key)]
    valid_or=[S.num(x.get('official_rating')) for x in group]
    valid_or=[x for x in valid_or if x is not None and 0<x<190]
    valid_wt=[S.num(x.get('weight_lbs')) for x in group]
    valid_wt=[x for x in valid_wt if x is not None and 80<x<220]
    local_or=S.avg(valid_or) if len(valid_or)>=3 else None
    local_wt=S.avg(valid_wt) if len(valid_wt)>=3 else None
    mean_or=ar['field_or'] if ar and ar['field_or'] is not None else local_or
    mean_wt=ar['field_weight'] if ar and ar['field_weight'] is not None else local_wt
    if mean_or is None:evidence['warnings'].append('OPPOSITION_OR_UNVERIFIED')
    if mean_wt is None:evidence['warnings'].append('FIELD_WEIGHT_UNVERIFIED')
    evidence['known_opponent_or_count']=ar['rated_count'] if ar else len(valid_or)
    evidence['known_field_weight_count']=ar['weight_count'] if ar else len(valid_wt)
    return record,mean_or,mean_wt,evidence


def rebuild(store,history_db,reference,out,daily_dir=None):
    out.mkdir(parents=True,exist_ok=True)
    events,daily=load_original(store,daily_dir)
    if not events:raise RuntimeError('No original HPR events')
    byfield=defaultdict(list)
    for e in events:byfield[(str(e.get('date')),str(e.get('race_key')))].append(e)
    needed=sorted({str(e['race_key'])[5:] for e in events if str(e.get('race_key')).startswith('HIST|')})
    archive,source_audit=archive_exact_meta(history_db,needed,reference)
    scored=[];per=defaultdict(list);counts=collections.Counter();recoveries=[]
    for e in sorted(events,key=lambda x:(str(x.get('date')),str(x.get('race_key')),str(x.get('horse_key')))):
        row,opp,meanwt,provenance=class_and_field(e,byfield,archive)
        old_class,old_points=S.strength(e)
        class_label,points=S.strength(row)
        raw=S.num(row.get('raw_performance'))
        if raw is None:raise RuntimeError('Missing source raw HPR quality: '+str(S.safe_key(e)))
        # Historical original HPR v0 already adds a Class1..6 bonus.
        # Undo only the known bonus, retain the pre-existing field OR component.
        klass_delta=(points-S.old_class_bonus(e)) if points is not None else 0.
        opposition=S.clamp((opp-105.)*.11,-4.,4.) if opp is not None else 0.
        reweight=S.relative(row.get('weight_lbs'),meanwt)
        weight_adjust=S.clamp(reweight*.2,-3.,3.) if reweight is not None else 0.
        uplift=klass_delta+opposition+weight_adjust
        old_context=S.num(row.get('pillar_composite'))
        if old_context is None:old_context=raw
        rating=round(S.clamp(.65*S.clamp(raw+uplift,0.,100.)+.35*S.clamp(old_context+uplift,0.,100.),0.,100.),2)
        previous=[x for x in per[str(row['horse_key'])] if x['date']<str(row['date'])]
        hpr={**row,'hpr':rating,'model_version':V,'original_hpr_v0':S.num(e.get('hpr')),
             'class_label':class_label or 'UNKNOWN','class_points':points,
             'class_delta_from_v0':round(klass_delta,3),
             'opposition_mean_or':round(opp,3) if opp is not None else None,
             'opposition_adjustment':round(opposition,3),
             'carried_weight_vs_field_lbs':round(reweight,3) if reweight is not None else None,
             'weight_adjustment':round(weight_adjust,3),'previous_hpr31_count':len(previous),
             'verified_evidence':provenance,'source_sha_v0':e.get('source_digest')}
        scored.append(hpr)
        per[str(row['horse_key'])].append({'date':str(row['date']),'hpr':rating})
        if points is None:counts['class_unknown']+=1
        if opp is None:counts['opponent_or_unknown']+=1
        if reweight is None:counts['field_weight_unknown']+=1
        if class_label and not old_class:counts['class_recovered']+=1
        if str(e.get('race_key')).startswith('HIST|') and provenance['field_source']=='FULL_ARCHIVE_EXACT_RACE_ID':
            counts['archived_runs_linked']+=1
        if class_label and not old_class:
            recoveries.append({'date':row['date'],'race_key':row['race_key'],'horse':row['horse'],
                'recovered_class':class_label,'source':provenance['class_source']})
    assert len(scored)==len(events)
    dest=out/'stage132-hpr-v3_1-research.sqlite'
    if dest.exists():dest.unlink()
    con=sqlite3.connect(dest)
    con.executescript('''CREATE TABLE hpr_ratings (
      horse_key TEXT NOT NULL,race_key TEXT NOT NULL,race_date TEXT NOT NULL,
      model_version TEXT NOT NULL,revision INTEGER NOT NULL,source_sha TEXT NOT NULL,
      hpr REAL NOT NULL,confidence TEXT NOT NULL,payload TEXT NOT NULL,
      inserted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
      PRIMARY KEY(horse_key,race_key,model_version,revision));
      CREATE INDEX hpr_horse_date ON hpr_ratings(horse_key,race_date);
      CREATE INDEX hpr_race_date ON hpr_ratings(race_date);
      CREATE TABLE hpr_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
    con.execute('INSERT INTO hpr_metadata VALUES (?,?)',('version',V))
    def prepared(x):
        blob=json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        return (x['horse_key'],x['race_key'],str(x['date']),V,1,hashlib.sha256(blob.encode()).hexdigest(),x['hpr'],x.get('confidence') or 'PARTIAL',blob)
    con.executemany('''INSERT INTO hpr_ratings(horse_key,race_key,race_date,model_version,revision,source_sha,hpr,confidence,payload)
       VALUES (?,?,?,?,?,?,?,?,?)''',(prepared(e) for e in scored))
    con.commit()
    assert con.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert con.execute('SELECT COUNT(*) FROM hpr_ratings').fetchone()[0]==len(scored)
    con.close()
    import csv
    with (out/'stage132-exact-class-recovery-sample.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['date','race_key','horse','recovered_class','source']);w.writeheader();w.writerows(recoveries[:3000])
    original_unknown=sum(S.strength(e)[1] is None for e in events)
    original_field=collections.Counter()
    for k,group in byfield.items():
        n_or=sum(S.num(z.get('official_rating')) is not None and 0<S.num(z.get('official_rating'))<190 for z in group)
        n_w=sum(S.num(z.get('weight_lbs')) is not None and 80<S.num(z.get('weight_lbs'))<220 for z in group)
        original_field['or_unknown']+=len(group) if n_or<3 else 0
        original_field['weight_unknown']+=len(group) if n_w<3 else 0
    audit={'version':V,'original_hpr_version':B,'total_events':len(events),
           'distinct_horses':len(per),'original_class_unknown':original_unknown,
           'class_recovered':counts['class_recovered'],'remaining_class_unknown':counts['class_unknown'],
           'original_opposition_unknown_est':original_field['or_unknown'],
           'remaining_opposition_unknown':counts['opponent_or_unknown'],
           'original_weight_unknown_est':original_field['weight_unknown'],
           'remaining_weight_unknown':counts['field_weight_unknown'],
           'verified_historical_races':source_audit,'HPR_daily_merge':daily,
           'historical_last_date':max(str(e.get('date')) for e in events),
           'notes':['Source HPR version unchanged; all new ratings reside in separate SQLite.',
                    'Exact historical race ID + date + course verified; names alone never identify horses.',
                    'A generic race title without explicit class is kept UNKNOWN.',
                    'Past field OR and weight come from complete historical field where verified.',
                    'Old HPR v0 opponent bonus may remain in raw quality and cannot be fully reversed.',
                    'Not a validated prediction improvement; paper selections required.']}
    (out/'stage132-hpr-evidence-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    return audit


def identity_details(event):
    key=str(event.get('horse_key') or '')
    parts=key.split('|')
    if len(parts)==4 and parts[0]=='P':
        return {'horse':event.get('horse') or parts[1],'sire':parts[2],'dam':parts[3]}
    if len(parts)==2 and parts[0]=='ID':
        return {'horse':event.get('horse'),'horse_id':parts[1]}
    return None


def hpr_history(db,target):
    db=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
    records=defaultdict(list)
    for (blob,) in db.execute('SELECT payload FROM hpr_ratings WHERE model_version=? AND race_date<? ORDER BY race_date',(V,target)):
        e=json.loads(blob);records[e['horse_key']].append(e)
    db.close()
    return records


def build_paper_state(history_db,reference,hpr_hist,target):
    import stage108_model_g2 as G
    import stage109_model_g3 as G3
    from stage131_class_weight_hpr import build_context_state
    G,CState=build_context_state()
    import stage131_class_weight_hpr as stage131_class_weight_hpr
    stage131_class_weight_hpr.ContextState=CState
    con=sqlite3.connect('file:'+str(history_db.resolve())+'?mode=ro',uri=True)
    # Populate verified complete historical field averages for class/weight features.
    for row in con.execute('''SELECT race_id,AVG(CASE WHEN official_rating>0 AND official_rating<190 THEN official_rating END),
         COUNT(CASE WHEN official_rating>0 AND official_rating<190 THEN 1 END),
         AVG(CASE WHEN weight_lbs>80 AND weight_lbs<220 THEN weight_lbs END),
         COUNT(CASE WHEN weight_lbs>80 AND weight_lbs<220 THEN 1 END)
         FROM historical_runs GROUP BY race_id'''):
        rid,avg_or,n_or,avg_wt,n_wt=row
        CState.field_meta[str(rid)]={'avg_or':avg_or,'avg_weight':avg_wt,'rated_n':n_or,'weight_n':n_wt}
    con.close()
    ref,_=G.load_reference(reference,first='2022-01-01',last='2025-12-31')
    original=G.FormState;start=G.TRAIN_START
    try:
        G.FormState=CState;G.TRAIN_START=G3.SEED_START
        _,state,audit,_=G.collect_historical(history_db,first='2022-01-01',last='2025-12-31',reference=ref)
    finally:
        G.FormState=original;G.TRAIN_START=start
    from collections import defaultdict
    field2026=defaultdict(list)
    recent=[];rejected=collections.Counter()
    for horse,entries in hpr_hist.items():
        for x in entries:
            if not '2026-01-01'<=str(x.get('date'))<target:continue
            if not identity_details(x):rejected['IDENTITY_UNUSABLE']+=1;continue
            if S.num(x.get('position')) is None and str(x.get('status') or '').lower() not in ('pu','f','ur','fell','unseated','pulled up','rr','refused','bd','ro'):
                rejected['UNVERIFIED_FINISH']+=1;continue
            recent.append(x);field2026[str(x.get('race_key'))].append(x)
    for key,rows in field2026.items():
        official=[S.num(x.get('official_rating')) for x in rows]
        weights=[S.num(x.get('weight_lbs')) for x in rows]
        known_or=[x for x in official if x is not None and 0<x<190]
        known_weight=[x for x in weights if x is not None and 80<x<220]
        CState.field_meta[key]={'avg_or':S.avg(known_or),'avg_weight':S.avg(known_weight),
                                'rated_n':len(known_or),'weight_n':len(known_weight)}
    for e in sorted(recent,key=lambda x:(str(x.get('date')),str(x.get('race_key')),str(x.get('horse_key')))):
        fields=identity_details(e)
        row={**e,**fields,'race_id':str(e['race_key']),'race_name':e.get('race_type') or '',
             'finish_pos':e.get('position'),'outcome':e.get('status') or 'ran',
             'rpr':None,'topspeed':None}
        state.add(row,dt.date.fromisoformat(str(e['date'])),int(S.num(e.get('field_size')) or 0))
    return state,{'archive':audit,'hpr_recent_starts':len(recent),
                  'hpr_latest_date':max((str(x['date']) for x in recent),default=None),
                  'hpr_rejections':dict(rejected)}


def hpr_score(horse,records,card,kind):
    entries=[e for e in records.get(horse,[]) if str(e.get('date'))<str(card['date'])]
    def same_code(e):
        s=str(e.get('race_type') or '').lower()
        if kind=='hurdle':return 'hurdle' in s
        if kind=='chase':return 'chase' in s
        if kind=='nhflat':return 'flat' in s or 'bumper' in s
        return ('hurdle' not in s and 'chase' not in s)
    prior=[x for x in entries if same_code(x)]
    if not prior:return None,0
    last=prior[-3:]
    weights=list(range(1,len(last)+1))
    mean=sum(x['hpr']*w for x,w in zip(last,weights))/sum(weights)
    # Small, provisional present-day adjustments, never odds or SP.
    pastclass=S.avg([S.strength(x)[1] for x in last]);todayclass=S.strength(card)[1]
    classadj=S.clamp((pastclass-todayclass)*.2,-3.,3.) if pastclass is not None and todayclass is not None else 0.
    lastwt=S.num(last[-1].get('weight_lbs'));wt=S.num(card.get('weight_lbs') or card.get('lbs'))
    weightadj=S.clamp((lastwt-wt)*.12,-2.,2.) if lastwt is not None and wt is not None else 0.
    lastor=S.num(last[-1].get('official_rating'));todayor=S.num(card.get('official_rating') or card.get('ofr'))
    oradj=S.clamp((lastor-todayor)*.12,-2.,2.) if lastor is not None and todayor is not None else 0.
    return round(S.clamp(mean+classadj+weightadj+oradj,0.,100.),3),len(prior)


def paper_2026(history_db,reference,oldmodel,newmodel,hprdb,cardsfile,out,selection_date=None,min_coverage=.55):
    import joblib
    import pandas as pd
    import numpy as np
    import stage108_model_g2 as G
    import stage111_model_g4 as G4
    import stage131_class_weight_hpr as S131
    from rhp_runner_eligibility import active_runner
    out.mkdir(parents=True,exist_ok=True)
    now=dt.datetime.now(dt.timezone.utc)
    source=json.loads(cardsfile.read_text())
    cards=source.get('runners',[])
    # Check what dates actually exist: NEVER retroactively score finished races.
    fields=defaultdict(list)
    rejected=[]
    for c in cards:
        if not active_runner(c) or not c.get('horse'):continue
        try:off=dt.datetime.fromisoformat(str(c.get('race_time')).replace('Z','+00:00'))
        except Exception:continue
        if off.tzinfo is None:off=off.replace(tzinfo=dt.timezone.utc)
        off=off.astimezone(dt.timezone.utc)
        if off<=now+dt.timedelta(minutes=10):continue
        date=off.date().isoformat()
        if selection_date and date!=selection_date:continue
        key=f'{date}|{G.course_key(c.get("course"))}|{off.strftime("%H:%M")}'
        fields[key].append(c)
    available=sorted({key[:10] for key in fields})
    target=selection_date or (available[0] if available else None)
    report={'selection_date':target,'cutoff_utc':now.isoformat(),
            'racecard_collected_at':source.get('collected_at'),'available_future_dates':available,
            'note':'Scores only races more than 10 minutes in future; no retrospective scores.'}
    if not target or not any(k[:10]==target for k in fields):
        report.update({'status':'NO_UNRUN_RACECARDS','eligible_races':0})
        (out/'stage132-2026-paper-audit.json').write_text(json.dumps(report,indent=2)+'\n')
        return report
    if target<now.date().isoformat():raise RuntimeError('Target date already in past')
    records=hpr_history(hprdb,target)
    state,form_audit=build_paper_state(history_db,reference,records,target)
    orig=joblib.load(oldmodel);new=joblib.load(newmodel)
    if orig.get('version')!=G4.VERSION or new.get('version')!=S131.VERSION:
        raise RuntimeError('Model version mismatch: refuse paper scoring')
    if orig.get('trained_through')!='2025-12-31' or new.get('trained_through')!='2025-12-31':
        raise RuntimeError('Model trained through wrong cut-off')
    complete=[];allrows=[];eligible={}
    for key,people in sorted(fields.items()):
        if key[:10]!=target:continue
        n=len(people)
        if n<4:rejected.append({'race':key,'reason':'FIELD_LT_4'});continue
        country=G.paper_region(people[0]);
        if country not in ('GB','IRELAND'):rejected.append({'race':key,'reason':'REGION_UNVERIFIED'});continue
        entries=[]
        for c in people:
            inp={**c,'region_scope':country}
            x,prior=state.features(inp,dt.date.fromisoformat(target),n)
            kind,aw,hcap=G.category(inp)
            entries.append({**x,'date':target,'race_id':key,'course':c.get('course'),'off_utc':c.get('race_time'),
                'race_name':c.get('race_name'),'horse':c.get('horse'),'identity':G.identity(inp),
                'prior_runs':prior,'race_class_group':'Handicap' if hcap==1 else 'Non-handicap' if hcap==0 else 'Unknown',
                'discipline':kind,'surface_class':'AW' if aw is True else 'Turf' if aw is False else 'Unknown',
                '_card':inp})
        match=sum(bool(x['identity']) and x['prior_runs']>0 for x in entries)/n
        if match<min_coverage:
            rejected.append({'race':key,'reason':'FORM_COVERAGE_'+str(round(match,3))});continue
        eligible[key]=n
        tmp=pd.DataFrame(entries)
        for src,dst in [('weight_lbs','today_weight_relative'),('official_rating','today_or_relative')]:
            val=pd.to_numeric(tmp[src],errors='coerce');tmp[dst]=val-val.mean()
        old_features=list(G.FEATURES)
        for model_key,model,feat,display in [
            ('original_spec',orig,list(G.FEATURES),'G4 Specialist original'),
            ('original_blend',orig,list(G.FEATURES),'G4 Blended original'),
            ('class_spec',new,list(new['features']),'G4 Class+Weight Specialist'),
            ('class_blend',new,list(new['features']),'G4 Class+Weight Blended')]:
            try:
                G.FEATURES=feat
                mode='BLENDED' if model_key.endswith('blend') else 'PURE'
                estimator=G4.BankEstimator(model['bank'],mode)
                p=estimator.predict_proba(G.Xframe(tmp))[:,1]
                z=tmp.drop(columns=['_card']).copy()
                z['raw_probability']=p
                z=G.ranks(G.softmax_races(z,1))
                for rank,row in enumerate(z.itertuples(),1):
                    allrows.append({'date':target,'time':str(row.off_utc)[11:16],'meeting':row.course,
                        'race_id':key,'race_name':row.race_name,'discipline':row.discipline,
                        'race_type':row.race_class_group,'horse':row.horse,'model':display,
                        'rank':rank,'win_probability_pct':round(float(row.win_probability)*100,2),
                        'prior_runs':int(row.prior_runs),'form_coverage_pct':round(match*100,1),
                        'odds_used':'NO'})
            finally:G.FEATURES=old_features
        hprs=[]
        for v in entries:
            score,hist=hpr_score(v['identity'],records,v['_card'],v['discipline'])
            hprs.append((v,score,hist))
        cover=sum(s is not None for _,s,_ in hprs)/n
        if cover>=.85:
            hprs.sort(key=lambda x:(-(x[1] if x[1] is not None else -1),str(x[0]['horse'])))
            for rank,(v,score,obs) in enumerate(hprs,1):
                allrows.append({'date':target,'time':str(v['off_utc'])[11:16],'meeting':v['course'],
                    'race_id':key,'race_name':v['race_name'],'discipline':v['discipline'],
                    'race_type':v['race_class_group'],'horse':v['horse'],'model':'HPR v3.1 (rating rank)',
                    'rank':rank,'win_probability_pct':'','hpr_31_preoff':score if score is not None else '',
                    'prior_runs':obs,'form_coverage_pct':round(cover*100,1),'odds_used':'NO'})
        else:rejected.append({'race':key,'reason':'HPR_COVERAGE_BELOW_85_'+str(round(cover,3))})
    if allrows:
        import csv
        header=['date','time','meeting','race_id','race_name','discipline','race_type','horse','model',
                'rank','win_probability_pct','hpr_31_preoff','prior_runs','form_coverage_pct','odds_used']
        with (out/'stage132-all-2026-model-ranks.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=header,extrasaction='ignore');w.writeheader();w.writerows(allrows)
        with (out/'stage132-2026-rank1-comparison.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=header,extrasaction='ignore');w.writeheader();w.writerows(r for r in allrows if r['rank']==1)
        cross=defaultdict(dict)
        for row in allrows:
            if row['rank']==1:cross[row['race_id']][row['model']]=row['horse']
        report['rank1_by_model']={k:sum(k in vals for vals in cross.values()) for k in {x['model'] for x in allrows}}
        report['specialist_changes']=sum(v.get('G4 Specialist original')!=v.get('G4 Class+Weight Specialist') for v in cross.values())
        report['blended_changes']=sum(v.get('G4 Blended original')!=v.get('G4 Class+Weight Blended') for v in cross.values())
    report.update({'status':'PAPER_ONLY','eligible_races':len(eligible),'rejected':rejected,
        'scored_rows':len(allrows),'form_history':form_audit,
        'checks':'Same verified pre-race state for original G4 and new challenger; no target race outcomes or prices read.',
        'hpr_scores':'Heuristic HPR rating order, NOT calibrated win probability.'})
    (out/'stage132-2026-paper-audit.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    return report


def self_test():
    assert S.strength({'race_name':'Listed Juvenile Hurdle'})[1]==6.
    assert S.strength({'race_name':'Maiden Hurdle'})[1] is None
    assert identity_details({'horse_key':'P|TESTHORSE|TESTSIRE|TESTDAM','horse':'Test Horse'})=={'horse':'Test Horse','sire':'TESTSIRE','dam':'TESTDAM'}
    assert hpr_score('P|H|S|D',{'P|H|S|D':[{'date':'2024-01-01','hpr':80,'race_type':'Hurdle','race_class':'Class 1','weight_lbs':160,'official_rating':115}]},
        {'date':'2025-01-01','race_type':'Hurdle','race_class':'Class 4','weight_lbs':153,'official_rating':110},'hurdle')[0]>80
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        root=pathlib.Path(d);hdb=root/'h.sqlite';xdb=root/'old.sqlite';reference=root/'ref.csv'
        c=sqlite3.connect(hdb)
        c.executescript('CREATE TABLE historical_races(id TEXT,race_date TEXT,course TEXT,race_class TEXT,race_type TEXT);'
            'CREATE TABLE historical_runs(race_id TEXT,official_rating REAL,weight_lbs REAL,outcome TEXT);')
        c.execute('INSERT INTO historical_races VALUES (?,?,?,?,?)',('R1','2024-03-01','Ascot',None,'Hurdle'))
        c.executemany('INSERT INTO historical_runs VALUES (?,?,?,?)',[('R1',112,158,'ran'),('R1',111,155,'ran'),('R1',114,153,'ran')]);c.commit();c.close()
        # Provide a tiny mock reference via function fixture (not source production name).
        c=sqlite3.connect(xdb)
        c.execute('CREATE TABLE hpr_ratings(horse_key TEXT,race_key TEXT,race_date TEXT,model_version TEXT,revision INTEGER,payload TEXT)')
        for i in range(3):
            e={'horse_key':f'P|H{i}|S|D','horse':f'H{i}','race_key':'HIST|R1','date':'2024-03-01',
                'course':'Ascot','race_class':None,'race_type':'Hurdle','raw_performance':60,'pillar_composite':60,'hpr':60,
                'weight_lbs':153+i,'official_rating':112+i,'confidence':'PARTIAL'}
            c.execute('INSERT INTO hpr_ratings VALUES (?,?,?,?,?,?)',(e['horse_key'],e['race_key'],e['date'],B,1,json.dumps(e)))
        c.commit();c.close()
        events,_=load_original(xdb)
        assert len(events)==3
        fields=defaultdict(list)
        for e in events:fields[(e['date'],e['race_key'])].append(e)
        rec,op,w,ev=class_and_field(events[0],fields,{'R1':{'date':'2024-03-01','course':'Ascot','race_class':None,'race_name':'Novices Hurdle (Class 2)',
            'field_or':112.,'field_weight':155.,'rated_count':3,'weight_count':3}})
        assert S.strength(rec)[1]==7 and op==112 and w==155
        rec2,op2,w2,ev2=class_and_field(events[0],fields,{'R1':{'date':'2025-01-01','course':'Ascot','race_class':'Class 1','race_name':'',
            'field_or':112.,'field_weight':155.,'rated_count':3,'weight_count':3}})
        assert S.strength(rec2)[1] is None and 'ARCHIVE_ID_DATE_COURSE_CONFLICT' in ev2['warnings']
        # Full recovery smoke test: mock only the exact reference loader,
        # exercising every SQLite reading, versioning, and audit write step.
        import types
        original_module=sys.modules.get('stage108_model_g2')
        sys.modules['stage108_model_g2']=types.SimpleNamespace(load_reference=lambda *a,**k:(
            {'R1':('2024-03-01','Ascot','Hurdle','Novices Hurdle (Class 2)','GB')},{}))
        try:
            audit=rebuild(xdb,hdb,reference,root/'new')
        finally:
            if original_module is None:sys.modules.pop('stage108_model_g2',None)
            else:sys.modules['stage108_model_g2']=original_module
        assert audit['total_events']==3 and audit['class_recovered']==3
        assert audit['remaining_class_unknown']==0
        z=sqlite3.connect(root/'new'/'stage132-hpr-v3_1-research.sqlite')
        assert z.execute('SELECT COUNT(*) FROM hpr_ratings').fetchone()[0]==3
        assert z.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        z.close()
    print('PASS Stage132: identity-safe class recovery, stale archive refusal, weight and OR evidence, chronology, HPR score')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test')
    r=sub.add_parser('rebuild-hpr')
    r.add_argument('--old-hpr',type=pathlib.Path,required=True);r.add_argument('--history-db',type=pathlib.Path,required=True)
    r.add_argument('--reference',type=pathlib.Path,required=True);r.add_argument('--daily-dir',type=pathlib.Path)
    r.add_argument('--out-dir',type=pathlib.Path,required=True)
    q=sub.add_parser('paper-2026')
    for name in ('history-db','reference','old-g4','new-g4','hpr-db','cards','out-dir'):
        q.add_argument('--'+name,type=pathlib.Path,required=True)
    q.add_argument('--selection-date',default='')
    a=p.parse_args()
    if a.command=='self-test':self_test()
    elif a.command=='rebuild-hpr':print(json.dumps(rebuild(a.old_hpr,a.history_db,a.reference,a.out_dir,a.daily_dir),indent=2))
    else:print(json.dumps(paper_2026(a.history_db,a.reference,a.old_g4,a.new_g4,a.hpr_db,a.cards,a.out_dir,a.selection_date or None),indent=2,default=str))

if __name__=='__main__':main()
