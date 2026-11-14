#!/usr/bin/env python3
"""Stage128: prospective odds-blind G5 paper predictions for dated UK/IE cards.

Models are the exact Stage127 2025-trained bundles. Baseline horse features come
from the frozen G3 state, extended with verified prior-only 2026 results. G5
network features are rebuilt from the verified full-field 2022+ history, then
extended with HPR events strictly after the archive end. Same-day results
and bookmaker prices are never read. Research only: no production updates.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import datetime as dt
import json
from pathlib import Path
import sqlite3

import joblib
import numpy as np
import pandas as pd

import stage108_model_g2 as G
import stage109_model_g3 as G3
import stage112_g5_relational_form as G5
import stage112_form_coverage_reconcile as Cover

UTC = dt.timezone.utc
VERSIONS = ('G5_NETWORK_QUARTERLY', 'G5_STRENGTH_QUARTERLY',
            'G5_COMBINED_QUARTERLY', 'G5_ADAPTIVE_QUARTERLY')
PERMITTED_REGION = {'GB', 'IRELAND'}
PICK_COLUMNS = ['model','date','course','off_utc','race_id','race_type','horse',
                'rank','win_probability','prior_runs','race_field_size',
                'declared_form_unmatched','form_quality_status',
                'g5_direct_rivals','g5_shared_rivals','g5_shared_external_horses',
                'g5_nextout_count','g5_nextout_source_races']


def as_day(text):
    d = G.day(text)
    if not d: raise ValueError('Invalid date: '+str(text))
    return d


def valid_card(r, target, cutoff):
    day = G.day(r.get('date') or r.get('race_time'))
    when = G.stamp(r.get('race_time'))
    return bool(day == target and when and when > cutoff + dt.timedelta(minutes=10)
                and str(r.get('race_status') or '').lower() not in
                ('result','finished','cancelled','abandoned','nr','non-runner','nonrunner','scratched','withdrawn')
                and str(r.get('number') or '').upper() != 'NR')


def race_key(row):
    when = G.stamp(row.get('race_time'))
    return '|'.join((str(G.day(row.get('date') or row.get('race_time'))),
                     G.course_key(row.get('course')),
                     when.strftime('%H:%M') if when else ''))


def group_cards(path, target, cutoff):
    obj = json.loads(Path(path).read_text())
    groups = defaultdict(list)
    for runner in G.flat_cards(obj):
        if valid_card(runner,target,cutoff):
            groups[race_key(runner)].append(runner)
    return dict(groups), (obj.get('collected_at') if isinstance(obj,dict) else None)


def read_archive_for_network(network, db, refs, target):
    """Exact reference filters for 2022-25; UK/IE course checks for 2026.

    A previous calendar day's results become features only after that whole day
    has finished. Unmatched international races never enter the network.
    """
    conn = sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
    conn.execute('PRAGMA query_only=ON')
    until=(target-dt.timedelta(days=1)).isoformat()
    current=None;pending=[];seen=Counter();last=None
    try:
        for entries in G.from_db(conn,'2022-01-01',until):
            d=as_day(entries[0]['date'])
            if current is not None and d != current:
                network.end_of_day(pending);pending=[]
            current=d
            rid=str(entries[0]['race_id'])
            reference = refs.get(rid)
            if reference:
                rd,venue,rtype,title,region=reference
                if rd != str(d) or G.course_key(venue)!=G.course_key(entries[0].get('course')):
                    raise RuntimeError('Race ID mismatch with dated verified reference: '+rid)
                if region not in PERMITTED_REGION:continue
            elif d.year>=2026:
                region=G.paper_region(entries[0])
                if region not in PERMITTED_REGION:continue
                venue=entries[0]['course'];rtype=entries[0].get('race_type');title=''
            else:
                seen['unmatched_prehistory_races']+=1;continue
            runners=[]
            for row in entries:
                x=dict(row);x.update(course=venue, race_name=title,
                                     race_type=rtype or row.get('race_type'), region_scope=region)
                runners.append(x)
            # The historical Stage112 builder accepts all known full fields;
            # 2026 bridge rows must have a verified winner and >=4 starters.
            if d.year>=2026:
                valid=[r for r in runners if Cover.valid_run(r)]
                if len(valid)<4 or sum(G.num(r.get('finish_pos'))==1 for r in valid)!=1:
                    seen['2026_incomplete_races_excluded']+=1;continue
                runners=valid
            pending.append(runners)
            seen['races_'+str(d.year)]+=1
            last=str(d)
        if pending:network.end_of_day(pending)
    finally:conn.close()
    return dict(seen),last


def archive_end_date(db,target,frozen='2025-12-31'):
    conn=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
    try:
        last=conn.execute('SELECT MAX(race_date) FROM historical_races WHERE race_date>? AND race_date<?',
                          (frozen,str(target))).fetchone()[0]
    finally:conn.close()
    return str(last or frozen)


def extend_network_hpr(network,db,after,target):
    """Use losslessly restored HPR rows after archive cutoff only.

    HPR revisions represent evidence of completed events, not predictions or
    future outcomes. Deduplicate the exact horse/event identity before reading.
    """
    path=Path(db)
    if not path.exists():return {'status':'NO_HPR_LEDGER','races':0,'rows':0}
    con=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
    con.execute('PRAGMA query_only=ON')
    groups=defaultdict(list);seen=set();stats=Counter()
    try:
        q='''SELECT h.horse_key,h.race_key,h.race_date,h.payload FROM hpr_ratings h
          JOIN (SELECT horse_key,race_key,MAX(revision) latest
                FROM hpr_ratings WHERE race_date>? AND race_date<?
                GROUP BY horse_key,race_key) mx
            ON mx.horse_key=h.horse_key AND mx.race_key=h.race_key AND mx.latest=h.revision
          WHERE h.race_date>? AND h.race_date<?
          ORDER BY h.race_date,h.race_key'''
        for key,rid,day,raw in con.execute(q,(after,str(target),after,str(target))):
            if (key,rid) in seen:continue
            seen.add((key,rid))
            parts=str(key).split('|')
            if len(parts)!=4 or parts[0]!='P':
                stats['unsupported_identity']+=1;continue
            x=json.loads(raw)
            venue=x.get('course') or ''
            r={'horse':parts[1],'sire':parts[2],'dam':parts[3],
               'date':day,'race_id':str(rid),'course':venue,
               'race_type':x.get('race_type'),'race_name':x.get('race_name'),
               'surface':x.get('surface'),'going':x.get('going'),
               'distance_f':x.get('distance_f'),'weight_lbs':x.get('weight_lbs'),
               'position':x.get('position'),'beaten':x.get('beaten'),
               'outcome':x.get('status') or 'ran'}
            if G.identity(r)!=key:
                stats['identity_mismatch']+=1;continue
            if G.paper_region(r) not in PERMITTED_REGION:
                stats['other_region_excluded']+=1;continue
            if not Cover.valid_run({'outcome':r['outcome'],'finish_pos':r['position']}):
                stats['unverified_run_excluded']+=1;continue
            groups[(day,str(rid))].append(r)
        days=defaultdict(list)
        for (day,rid),runners in sorted(groups.items()):
            if len(runners)<4 or sum(G.num(x.get('position'))==1 for x in runners)!=1:
                stats['unverified_race_excluded']+=1;continue
            days[day].append(runners)
        for day,races in sorted(days.items()):
            if day>=str(target):raise AssertionError('Future result in HPR network')
            network.end_of_day(races)
            stats['hpr_races_added']+=len(races)
            stats['hpr_runners_added']+=sum(len(r) for r in races)
    finally:con.close()
    return dict(stats)


def add_base_2026(state,db,hpr,archive_end,target):
    frozen='2025-12-31'
    stats={}
    if archive_end>frozen:
        stats['archive'],issues=Cover.add_archive(state,db,'2026-01-01',archive_end,G)
        stats['unresolved_archive_identities']=len(issues)
    else:stats['archive']={'no_verified_2026_archive':True}
    stats['hpr']=G.load_recent_hpr(state,hpr,archive_end,str(target)) if hpr else {'status':'NOT_PROVIDED'}
    return stats


def confidence_report(base,state,network,groups,target):
    outcomes={};counts=Counter()
    for k,entries in groups.items():
        missing=0;linked=0;some_form=0
        for r in entries:
            key=G.identity(r)
            historic=state.horses.get(key,[]) if key else []
            had_past=any(q['date']<target for q in historic)
            if Cover.has_public_form(r) and not had_past:missing+=1
            if had_past:linked+=1
            if Cover.has_public_form(r):some_form+=1
        status='POSSIBLE_MISSING_FORM' if missing else 'CARD_FORM_MATCHED'
        outcomes[k]={'missing':missing,'baseline_prior_runners':linked,'declared_prior_runners':some_form,'status':status}
        counts[status]+=1
    return outcomes,dict(counts)


def run(args):
    cutoff=G.stamp(args.cutoff)
    target=as_day(args.date)
    if not cutoff:raise ValueError('Invalid as-of clock')
    # Paper scores are prospective; no backdated selection is permitted.
    if target < cutoff.date():raise RuntimeError('Refusing retrospective selection of completed racing date')
    if cutoff > dt.datetime.now(UTC) + dt.timedelta(minutes=3):
        raise RuntimeError('Decision timestamp must not be in the future')
    groups,card_ts=group_cards(args.cards,target,cutoff)
    if not groups:raise RuntimeError('No declared races left for target date')
    saved=joblib.load(args.state)
    if saved.get('asof_after_date')!='2025-12-31':
        raise RuntimeError('Wrong G3 frozen historical state timestamp')
    frozen=joblib.load(args.g3)
    if frozen.get('version')!=G3.VERSION or frozen.get('trained_through')!='2025-12-31':
        raise RuntimeError('G3 baseline is not frozen 2025 model')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    last=archive_end_date(args.db,target)
    state=saved['state']
    base_coverage=add_base_2026(state,args.db,args.hpr,last,target)
    network=G5.FormNetwork()
    ref,_=G.load_reference(args.reference,first='2022-01-01',last='2025-12-31')
    graph_audit,graph_last=read_archive_for_network(network,args.db,ref,target)
    network_hpr=extend_network_hpr(network,args.hpr,last,target) if args.hpr else {'status':'NO_LEDGER'}
    base,excluded=G.score_card_rows(state,frozen['estimator'],1.,
                                    json.loads(Path(args.cards).read_text()),cutoff)
    if base.empty:raise RuntimeError('G3 base form could not rank any eligible race')
    base=base.loc[base['date']==str(target)].copy()
    if base.empty:raise RuntimeError('No base predictions for requested date')
    conf,conf_counts=confidence_report(base,state,network,groups,target)
    graph_features={}
    for k,card in groups.items():
        f=network.features(card,target)
        for runner in card:
            h=G.identity(runner)
            if h in f:graph_features[(k,h)]=f[h]
    rows=[]
    for old in base.to_dict('records'):
        k=str(old['race_id']);h=str(old['identity'])
        feat=graph_features.get((k,h),G5.ZERO)
        meta=conf.get(k,{})
        rows.append({**old,**feat,'race_field_size':len(groups.get(k,[])),
                     'declared_form_unmatched':meta.get('missing',-1),
                     'form_quality_status':meta.get('status','UNKNOWN')})
    data=pd.DataFrame(rows)
    if len(data)<40:raise RuntimeError('Too few horses scored; refuse misleading partial card output')
    all_results=[];top_results=[];bundles={}
    for name in VERSIONS:
        path=Path(args.models)/('stage127-'+name.lower()+'-2025-final.joblib')
        if not path.is_file():raise FileNotFoundError('Missing trained G5 bundle: '+str(path))
        model=joblib.load(path)
        cols=model['features']
        if model.get('version')!='G5_QUARTERLY_LEARNER_2026_11_14_V1' or model.get('model')!=name:
            raise RuntimeError('Wrong G5 research bundle: '+name)
        if model.get('trained_through')!='2025-12-31' or model.get('odds_input')!='NONE':
            raise RuntimeError('Model invalid or not strictly odds blind')
        absent=set(cols)-set(data)
        if absent:raise RuntimeError(name+' missing expected features '+str(absent))
        x=data.reindex(columns=cols).apply(pd.to_numeric,errors='coerce').astype(float)
        p=data.copy()
        p['raw_probability']=model['estimator'].predict_proba(x)[:,1]
        p=G.softmax_races(p,1.)
        p=G.ranks(p)
        p['model']=name
        ranks=p.loc[p['rank']==1]
        if ranks['race_id'].duplicated().any():raise RuntimeError('Duplicate rank1 '+name)
        all_results.append(p);top_results.append(ranks)
        bundles[name]={'ranked_races':p.race_id.nunique(),
                       'rank1':len(ranks),'median_top1_probability':float(ranks.win_probability.median())}
    full=pd.concat(all_results,ignore_index=True,sort=False)
    selected=pd.concat(top_results,ignore_index=True,sort=False)
    selected=selected.sort_values(['off_utc','course','model'])
    full.to_csv(out/'stage128-g5-all-horses.csv',index=False)
    selected.to_csv(out/'stage128-g5-rank1.csv',index=False)
    report={
        'date':str(target),'asof_utc':cutoff.isoformat(),'card_collected_at':card_ts,
        'model_family':'G5_QUARTERLY','selection':'Rank1 only','odds_input':'NONE',
        'train_through':'2025-12-31','production_changes':False,
        'baseline_state_2026_update':base_coverage,
        'graph_archive_last_day':graph_last,'graph_archive':graph_audit,
        'graph_hpr':network_hpr,'graph_counters':dict(network.counters),
        'form_coverage_race_status':conf_counts,
        'form_coverage_races_with_missing':{k:v for k,v in conf.items() if v['missing']},
        'eligible_card_races':len(groups),'base_scored_races':base.race_id.nunique(),
        'base_exclusions':excluded,'models':bundles,
        'methodology':'Dated as-of verified prior form, shared rivals, next-out strength; no odds or hindsight.'}
    (out/'stage128-g5-paper-audit.json').write_text(json.dumps(report,indent=2,default=str))
    lines=['# G5 odds-blind Rank 1 research selections',
           '',f'Date: {target} | Decision clock: {cutoff.isoformat()}',
           f'Racecards: {len(groups)} races | eligible scored: {base.race_id.nunique()}',
           f'Unreconciled declared past form in {conf_counts.get("POSSIBLE_MISSING_FORM",0)} races.',
           '', '| Time | Course | Model | Rank1 Horse | Win % | Data confidence |',
           '|---|---|---|---|---:|---|']
    for p in selected.itertuples(index=False):
        lines.append(f'| {p.off_utc} | {p.course} | {p.model.replace("_QUARTERLY","")} | {p.horse} | {p.win_probability*100:.1f}% | {p.form_quality_status} |')
    lines+=['','**RESEARCH ONLY.** These are pre-race probability rankings, not betting advice.',
            'Some archived 2026 results or prior form may still be missing; check the JSON audit.']
    (out/'README.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines),flush=True)


def self_test():
    target=as_day('2026-11-14');early=G.stamp('2026-11-14T01:00:00Z')
    def row(time,horse,status='declared'):
        return dict(date=str(target),horse=horse,sire='A',dam='B',course='Cheltenham',
                    race_time=time,number='1',race_status=status)
    assert valid_card(row('2026-11-14T12:35:00Z','A'),target,early)
    assert not valid_card(row('2026-11-13T12:35:00Z','A'),target,early)
    assert not valid_card(row('2026-11-14T12:35:00Z','A','finished'),target,early)
    assert race_key(row('2026-11-14T12:35:00Z','A')) == '2026-11-14|CHELTENHAM|12:35'
    assert not any(k.lower().find('odd')>=0 or k.lower().find('price')>=0 for k in PICK_COLUMNS)
    print('Stage128 temporal/race-key/card validation passed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['self-test','paper'])
    parser.add_argument('--date');parser.add_argument('--cutoff')
    parser.add_argument('--state');parser.add_argument('--g3')
    parser.add_argument('--models');parser.add_argument('--db')
    parser.add_argument('--reference');parser.add_argument('--hpr')
    parser.add_argument('--cards',default='data/racecards.json')
    parser.add_argument('--out',default='reports/stage128/output')
    args=parser.parse_args()
    return self_test() if args.mode=='self-test' else run(args)

if __name__=='__main__':main()
