#!/usr/bin/env python3
"""Reusable daily Stage143A/B and Stage146A/B forward paper scoring on UK/IE fields.

Rebuilds original Stage146 (Stage143 specialist evidence + Stage144 prior-day
historical trend evidence) as-of current pre-off racecards. Uses the prior completed calendar-quarter
training boundary and a strictly historical verified Stage2 source.

The pre-off feature extractor expects a finishing-position label. This runner
uses the same ISOLATED UNRESULTED scratch DB technique as Stage141-today:
synthetic positions are used only for legacy feature-extraction shape, never
as training labels, trend evidence, model predictions or settled results.
No live model, database, site or historic source is modified.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, math
from zoneinfo import ZoneInfo
import numpy as np

DUBLIN=ZoneInfo('Europe/Dublin')
TODAY=dt.datetime.now(DUBLIN).date().isoformat()
def previous_quarter_end(day):
    d=dt.date.fromisoformat(day)
    current_start=dt.date(d.year,1+3*((d.month-1)//3),1)
    return (current_start-dt.timedelta(days=1)).isoformat()
TRAIN_THROUGH=previous_quarter_end(TODAY)
NAMES=('STAGE143_TYPE_EVIDENCE','STAGE143_TYPE_INTERACTIONS',
       'STAGE146_A_RETRAINED_TRENDS','STAGE146_B_RETRAINED_TRENDS')


def now(): return dt.datetime.now(dt.timezone.utc)


def verify_today(date, cards_audit):
    now_local=dt.datetime.now(DUBLIN).date()
    if date not in (now_local.isoformat(),(now_local+dt.timedelta(days=1)).isoformat()):
        raise RuntimeError('Live paper scorer permits today or tomorrow only (Dublin local date)')
    if cards_audit['snapshot_hours_old']>4.0:
        raise RuntimeError('Racecard data >4 hours old. Refresh racecards before scoring.')


def output_csv(path,rows):
    if not rows: raise RuntimeError('No remaining live selections to write')
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def validate_no_current_train(train, live, date):
    assert train and live
    assert all('2023-01-01'<=r['date']<=TRAIN_THROUGH for r in train)
    assert all(r['date']==date for r in live)
    assert {str(r['race_id']) for r in train}.isdisjoint({str(r['race_id']) for r in live})
    assert all(len(r['horse'])>=4 and len(set(r['horse']))==len(r['horse']) for r in live)


def main(a):
    import stage141_today_or_weight_paper as L
    import stage146_full_field_trend_fusion as F
    global TRAIN_THROUGH
    date=a.date or now().astimezone(DUBLIN).date().isoformat()
    TRAIN_THROUGH=previous_quarter_end(date)
    a.out.mkdir(parents=True,exist_ok=True)
    cards,cards_audit=L.today_cards(a.cards,date)
    verify_today(date,cards_audit)
    scratch,ref,source_latest=L.scratch_archive(a.db,a.reference,cards,date,a.out)
    if not source_latest or source_latest>=date:
        raise RuntimeError('Historical archive overlaps current racing date; no safe preoff replay: '+str(source_latest))
    archive_age=(dt.date.fromisoformat(date)-dt.date.fromisoformat(source_latest)).days
    if archive_age>14:
        raise RuntimeError('Historical racing archive too stale for daily use: '+str(source_latest)+' age '+str(archive_age)+' days')

    A,R,T,J,U,H,S=F.modules()
    import stage30_gap_aware_abcd as D
    obj=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
    if obj['chosen_combination']!='D-064-gap14' or not np.allclose(
          [obj['weights_pct'][p] for p in S.PILLARS],exact*100,atol=.011):
        raise RuntimeError('Frozen D source did not reproduce')
    S.FROZEN[:]=exact;S.CANDIDATES=S.candidate_weights()
    config=json.loads(a.config.read_text())
    assert config['type_count']==59 and len(config['full_type_catalog'])==59
    # Legacy D inputs are the same chronologically built Stage143/146 inputs.
    # The scratch archive contains ONLY newly declared fields with no real results.
    overlay,grade_audit=J.official_grade_overlay(scratch,ref,a.out,date)
    base,baseaudit,refaudit=J.construct_2026(scratch,ref,date)
    state,form,historical_audit=T.warmup(scratch,ref,date)
    long,longaudit,_=T.reconstruct_full_core(scratch,ref,date,state,form)
    T.align(base,long)
    del state,form

    identities={str(k) for r in base for k in r['identity'] if k}
    orig_end=H.END
    try:
        H.END=date
        hpr,hpr_audit=H.hpr_history(scratch,ref,a.out,identities)
    finally:H.END=orig_end
    H.attach_hpr(base,hpr,collections.Counter())
    weights_audit=J.weight_context(scratch,ref,base,a.out,date)
    metadata_audit=A.attach_preoff_observations(scratch,base,date)
    if any('preoff_meta' not in r for r in base if r['date']==date):
        raise RuntimeError('Missing current-race preoff metadata: '+str(metadata_audit))
    if metadata_audit['races_with_preoff_metadata']<len(base)*.9:
        raise RuntimeError('Incomplete historical preoff metadata: '+str(metadata_audit))

    # Independent unchanged Stage146 feature recipe. All candidate races
    # (including the *future* target, whose result labels are placeholder) are
    # routed before fitting; only prior completed races are admitted to train.
    routing={};qa=collections.Counter();longmap={str(r['race_id']):r for r in long}
    joined=[]
    for r in base:
        rid=str(r['race_id']);info,reason=A.classified(r,overlay,config)
        if reason:
            qa[reason]+=1;continue
        routing[rid]=info
        joined.append(T.variant(r,longmap[rid],core=True,form=True,weights=False))
    del base,longmap,long

    live=[r for r in joined if r['date']==date]
    train=[r for r in joined if '2023-01-01'<=r['date']<=TRAIN_THROUGH]
    validate_no_current_train(train,live,date)
    byid={rr['id']:rr for rr in cards}
    if set(str(r['race_id']) for r in live)!=set(byid):
        raise RuntimeError('Unmatched declared field: '+str(set(byid)-set(str(r['race_id']) for r in live)))
    # F.source_to_pre_race_trends traditionally stops at 2026-11-10. For
    # today's pre-off forward score, extend only the read cursor over the
    # scratch date. No today's outcome is used for a trend feature.
    previous_cutoff=F.CUTOFF
    try:
        F.CUTOFF=date
        evidence,trend_audit=F.source_to_pre_race_trends(
            scratch,ref,train+live,a.out)
    finally:F.CUTOFF=previous_cutoff
    for r in train+live:
        z=evidence[str(r['race_id'])]
        if z['sample_lag']!='PRIOR_CALENDAR_DAY_ONLY':raise RuntimeError('Trend forward-time guard failed')
        r['trend_features']=z['features']
        r['trend_provenance']=z
    del evidence

    allrows=[];toprows=[];runmeta=[]
    recipes=((F.A_MODEL,False,True),(F.B_MODEL,True,True),
             ('STAGE143_TYPE_EVIDENCE',False,False),
             ('STAGE143_TYPE_INTERACTIONS',True,False))
    for modelid,nonlinear,with_trend in recipes:
        bank,fit=F.fitted_bank(train,routing,config,nonlinear=nonlinear,with_trend=with_trend)
        predictions=F.predict(live,routing,config,bank,nonlinear=nonlinear,with_trend=with_trend)
        runmeta.append({'model':modelid,**fit})
        for race in sorted(live,key=lambda r:byid[str(r['race_id'])]['off']):
            rid=str(race['race_id']);off=byid[rid]['off']
            if off<=now()+dt.timedelta(minutes=2):continue
            p=predictions.get(rid)
            if p is None or not np.isclose(float(np.sum(p)),1.0,atol=1e-4):
                raise RuntimeError('Invalid probability for '+rid)
            if len(p)!=len(race['horse']):raise RuntimeError('Runner count drift '+rid)
            order=np.argsort(-p,kind='stable')
            info=routing[rid];trend=race['trend_provenance']
            for rank,k in enumerate(order,1):
                k=int(k)
                row={'date':date,'time_utc':off.strftime('%H:%M'),'course':race['course'],
                    'race_name':info['race_name'],'route':info['route'],
                    'model':modelid,'rank':rank,'horse':race['horse'][k],
                    'model_win_probability':round(float(p[k]),6),
                    'trend_source':trend['source'] if with_trend else 'DISABLED',
                    'prior_races_for_trend':trend['history'],
                    'trend_signals':trend['historical_features'] if with_trend else '',
                    'trend_horse_score':round(float(race['trend_features'][k][0]),5) if with_trend else '',
                    'field_size':len(race['horse']),
                    'trained_through':TRAIN_THROUGH,
                    'historical_results_through':source_latest,
                    'cards_asof':cards_audit['collected_at'],
                    'scored_at':now().isoformat(),'result_known':False,'odds_used':False,
                    'paper_only':True}
                allrows.append(row)
                if rank<=2:toprows.append(row)
        print('STAGE146_TODAY_MODEL',modelid,'fitted',fit,'remaining_races',sum(z['model']==modelid and z['rank']==1 for z in toprows),flush=True)

    if not toprows:
        raise RuntimeError('No races remain before the off; no backdated selections')
    output_csv(a.out/'stage146-today-rank1-rank2.csv',toprows)
    output_csv(a.out/'stage146-today-all-runners.csv',allrows)
    audit={'version':'stage147-daily-stage143-stage146-forward-v1','date':date,'status':'PREOFF_PAPER_ONLY',
       'models':list(NAMES),'races_remaining':len(toprows)//(2*len(NAMES)),
       'train_through':TRAIN_THROUGH,'historical_results_through':source_latest,
       'racecard_audit':cards_audit,'excluded_classification_races':dict(qa),
       'trend_audit':trend_audit,'fit':runmeta,'preoff_metadata_audit':metadata_audit,
       'hpr_audit':{'source_status':hpr_audit.get('status','historical_preoff'), 'from':hpr_audit.get('history_start')},
       'source_originals_unchanged':True,'synthetic_live_winner_labels_only_for_extraction':True,
       'never_train_on_live_labels':True,'no_postrace_live_result_in_features':True,
       'no_sp_or_odds_as_selection_features':True,
       'limits':['Research paper predictions, not a validated profitable betting strategy',
         'Racecards are a timestamped snapshot; confirm latest non-runners before betting',
         'Historical source cutoff is audited and no future races are ingested; missing subsequent days may affect form',
         '2026 retrospective tests do not establish prospective win probabilities',
         'Live target has dummy finish position in isolated scratch ONLY to satisfy legacy extractor']}
    (a.out/'stage146-today-audit.json').write_text(json.dumps(audit,indent=2,default=str)+'\n')
    print('STAGE146_TODAY_TOP2',json.dumps(toprows,default=str),flush=True)
    print('STAGE146_TODAY_AUDIT',json.dumps(audit,default=str),flush=True)


def self_test():
    assert len(NAMES)==4 and TRAIN_THROUGH<TODAY
    assert previous_quarter_end('2026-11-17')=='2026-09-30'
    assert previous_quarter_end('2027-01-04')=='2026-12-31'
    train=[{'race_id':'A','date':TRAIN_THROUGH,'horse':['a','b','c','d']}]
    test=[{'race_id':'B','date':TODAY,'horse':['e','f','g','h']}]
    validate_no_current_train(train,test,TODAY)
    try:validate_no_current_train(train,test,'1900-01-01')
    except AssertionError:pass
    else:raise AssertionError('Live date mismatch not detected')
    print('STAGE147_DAILY_143_146_SELFTEST_OK',TODAY,TRAIN_THROUGH)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=('self-test','today'))
    for name in ('db','reference','cards','config','out'):
        p.add_argument('--'+name,type=pathlib.Path)
    p.add_argument('--date',default='')
    args=p.parse_args()
    if args.command=='self-test':self_test()
    else:
        if any(getattr(args,z) is None for z in ('db','reference','cards','config','out')):
            raise RuntimeError('Missing required pre-off archive or racecards')
        main(args)
