#!/usr/bin/env python3
"""Stage152: missing historically strongest ROI arms, same-day PRE-OFF PAPER ONLY.

Reproduce three *named* historical model recipes using their original, frozen
functions and strictly prior quarterly training data:
  1. D137_HPR_ABILITY_GRADE_WEIGHT (2026 Stage141, AW handicap comparator)
  2. D137_HPR_ABILITY_GRADE_OR_WEIGHT (2026 Stage141, chase handicap)
  3. D142_LONG_CORE_FORM_HPR (2026 Stage142, beginners' chase)
  4. D_FORM_WEIGHT_GRADE__ROI (2026 Stage143D, AW handicap ROI specialist)

The fourth arm optimises its eight pillar shares against previous completed
race SP only, NEVER today's bookmaker prices. Racecards on target day are
inserted with dummy RESULT labels in an isolated scratch database and are
NEVER used in training, historical signal updates or current win-percent claims.
Existing D, D(c), G, Stage141/143/146/151 and app files are unchanged.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import pathlib
from zoneinfo import ZoneInfo
import numpy as np
import stage159_explainability as E
import stage30_gap_aware_abcd as D
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H
import stage141_grade_weight_2026 as J
import stage141_today_or_weight_paper as L
import stage142_full_history_core_2026 as T
import stage143_frozen_d_specialist_weights_2026 as P

DUBLIN = ZoneInfo('Europe/Dublin')
MODELS = (J.GRADE_WEIGHT, J.ALL, T.BOTH, P.W_G+'__ROI')


def today():
    return dt.datetime.now(DUBLIN).date().isoformat()


def quarter_end(day):
    d=dt.date.fromisoformat(day)
    start=dt.date(d.year, 1+3*((d.month-1)//3),1)
    return (start-dt.timedelta(days=1)).isoformat()


def original_frozen_weights():
    record=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    if record['chosen_combination']!='D-064-gap14':
        raise RuntimeError('Stage30 frozen model identity changed')
    w=np.asarray(D.candidates('D')[64],dtype=float)
    w/=w.sum()
    if not np.allclose([record['weights_pct'][p] for p in S.PILLARS],w*100,atol=.011):
        raise RuntimeError('Historical eight-pillar weight reproduction mismatch')
    S.FROZEN[:]=w
    S.CANDIDATES=S.candidate_weights()
    return w


def main(args):
    date=args.date or today()
    now_dublin=dt.date.fromisoformat(today())
    if date not in (now_dublin.isoformat(),(now_dublin+dt.timedelta(days=1)).isoformat()):
        raise RuntimeError('Only today or tomorrow Dublin pre-off predictions are allowed')
    args.out.mkdir(parents=True,exist_ok=True)
    cards,cards_audit=L.today_cards(args.cards,date)
    if not cards:raise RuntimeError('No eligible GB/IE declared fields')
    if all(x['off']<=L.utcnow()+dt.timedelta(minutes=2) for x in cards):
        raise RuntimeError('No races remain pre-off')
    scratch,reference,historical_latest=L.scratch_archive(args.db,args.reference,cards,date,args.out)
    if not historical_latest or historical_latest>=date:
        raise RuntimeError('Historical database contains target day outcomes')
    if (dt.date.fromisoformat(date)-dt.date.fromisoformat(historical_latest)).days>21:
        raise RuntimeError('Historical form snapshot >21 days behind; fail closed')
    frozen=original_frozen_weights()
    cutoff=quarter_end(date)
    if date.startswith('2026-') and cutoff!='2026-09-30':
        raise RuntimeError('Wrong Stage141/142/143 2026Q4 training boundary')
    print('STAGE152_HISTORY_CUTOFF',historical_latest,'TRAIN_CUTOFF',cutoff,flush=True)

    # Match Stage141 exact verified grade recovery; baseline's original D
    # pillars are unchanged. Both variants share identical horse order.
    overlay,grade_audit=J.official_grade_overlay(scratch,reference,args.out,date)
    if len(overlay)<100:raise RuntimeError('No verifiable historical grade overlay')
    base,base_audit,_=J.construct_2026(scratch,reference,date)
    graded,grade_model_audit,_=J.construct_2026(scratch,reference,date,overlay)
    bmap={str(r['race_id']):r for r in base}
    gmap={str(r['race_id']):r for r in graded}
    if set(bmap)!=set(gmap) or not bmap:raise RuntimeError('Grade feature cohort drift')
    for rid,r in bmap.items():
        g=gmap[rid]
        if r['identity']!=g['identity'] or r['horse']!=g['horse'] or not np.allclose(r['x8'],g['x8'],equal_nan=True):
            raise RuntimeError('Different runners or D pillars after grade overlay: '+rid)

    # Original 1988+ Stage142 warmup: only affects D142 long-core+form arm.
    state,form,history_audit=T.warmup(scratch,reference,date)
    long,long_audit,_=T.reconstruct_full_core(scratch,reference,date,state,form)
    T.align(base,long)
    longmap={str(r['race_id']):r for r in long}
    del state,form,long

    # The original HPR as-of feature cache attaches to both constructs.
    identities={h for r in base for h in r['identity'] if h}
    old_end=H.END
    try:
        H.END=date
        hpr,hpr_audit=H.hpr_history(scratch,reference,args.out,identities)
    finally:
        H.END=old_end
    hpr_coverage=collections.Counter()
    H.attach_hpr(base,hpr,hpr_coverage)
    hpr_g=collections.Counter()
    H.attach_hpr(graded,hpr,hpr_g)
    if dict(hpr_coverage)!=dict(hpr_g):
        raise RuntimeError('Grade overlay changed HPR coverage')

    weights_audit=J.weight_context(scratch,reference,base,args.out,date)
    for rid,g in gmap.items():
        r=bmap[rid]
        for key in ('weight_features','or_features','weight_today_lbs','official_rating_today'):
            g[key]=r[key]

    # Historical Stage143D first-observed debut/Trainer/Sire signals as-of date.
    # In the original Stage143D scorer, the broad race subtype and official
    # grade from the graded view override only those descriptive keys.
    debut_audit=P.first_recorded_history(scratch,reference,base,date,args.out)
    for rid,r in bmap.items():
        g=gmap[rid]
        for key in ('subtype','official_tier','race_name'):
            r[key]=g[key]
        r['scores']={P.W_G:P.additive(r,g,r.get('debut_features'),weight=True,official=False,grade=True)}
        if r['scores'][P.W_G].shape!=r['x8'].shape:
            raise RuntimeError('Stage143D pillar shape mismatch: '+rid)

    train=[r for r in base if '2023-01-01'<=r['date']<=cutoff]
    if len(train)<100 or any(r['date']>=date for r in train):
        raise RuntimeError('Training includes current result or too few races')
    print('STAGE152_TRAIN_RACES',len(train),flush=True)

    # Stage141 EXACT arms: grade+weight or grade+OR+weight, plus HPR ability.
    train_grade_weight=[J.copy_race(gmap[str(r['race_id'])],weights=True) for r in train]
    train_grade_all=[J.copy_race(gmap[str(r['race_id'])],weights=True,official=True) for r in train]
    fit_gw=H.l137_ml_banks(train_grade_weight,ability=True)
    fit_all=H.l137_ml_banks(train_grade_all,ability=True)
    del train_grade_weight,train_grade_all

    # Stage142 EXACT Core+Form (WITHOUT extra OR/weight signals).
    train_both=[T.variant(r,longmap[str(r['race_id'])],core=True,form=True,weights=False) for r in train]
    fit_both=H.l137_ml_banks(train_both,ability=True)
    del train_both

    # Stage143D exactly optimises Stage143's 8 pillars using past capped SP.
    grid=P.candidate_grid(frozen)
    bank143=P.optimise_by_race_type(train,P.W_G,grid,frozen)['ROI']

    bycard={c['id']:c for c in cards}
    future=sorted((r for r in base if r['date']==date),key=lambda r:bycard[str(r['race_id'])]['off'])
    rank1=[];all_runner_rows=[];skipped=collections.Counter()
    for r in future:
        rid=str(r['race_id']);c=bycard[rid]
        if c['off']<=L.utcnow()+dt.timedelta(minutes=2):
            skipped['race_off_or_imminent']+=1;continue
        g=gmap[rid]
        variants=[
            (J.GRADE_WEIGHT,J.copy_race(g,weights=True),fit_gw),
            (J.ALL,J.copy_race(g,weights=True,official=True),fit_all),
            (T.BOTH,T.variant(r,longmap[rid],core=True,form=True,weights=False),fit_both)
        ]
        for name,inputs,bank in variants:
            stage159_inputs=H.hpr_ability_137(inputs)
            scores,branch=S.predict_bank(bank,stage159_inputs)
            stage159_drivers=E.safe_forest(stage159_inputs['xf'],scores,bank[branch],S.FEATURES)
            if len(scores)!=len(r['horse']) or not np.all(np.isfinite(scores)):
                raise RuntimeError('Failed complete field for '+rid+' '+name)
            top=int(np.argmax(scores))
            for rank,idx in enumerate(np.argsort(-scores,kind='stable'),1):
                all_runner_rows.append({'date':date,'time_utc':c['off'].strftime('%H:%M'),
                    'meeting':r['course'],'race_id':rid,'race_name':r['race_name'],
                    'race_type':r['subtype'],'discipline':r['kind'],'model':name,
                    'rank':rank,'horse':r['horse'][idx],
                    'model_score_not_calibrated_probability':round(float(scores[idx]),6),
                    'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),
                    'driver_json':json.dumps(stage159_drivers[int(idx)],separators=(',',':')),
                    'odds_used_for_current_prediction':False})
            rank1.append({'date':date,'time_utc':c['off'].strftime('%H:%M'),'meeting':r['course'],
                'race_id':rid,'race_name':r['race_name'],'race_type':r['subtype'],'discipline':r['kind'],
                'model':name,'rank':1,'horse':r['horse'][top],'field_size':len(r['horse']),
                'model_score_not_calibrated_probability':round(float(scores[top]),6),
                'selection_branch':str(branch),'trained_through':cutoff,
                'prediction_asof':L.utcnow().isoformat(),'odds_used_for_current_prediction':False})
        name=P.W_G+'__ROI'
        weights,level,group,details=P.choose_bank(bank143,r,frozen)
        scores=r['scores'][P.W_G]@weights
        stage159_pillars=E.safe_pillars(r['scores'][P.W_G],weights)
        if len(scores)!=len(r['horse']) or not np.all(np.isfinite(scores)):
            raise RuntimeError('Failed Stage143D field '+rid)
        top=int(np.argmax(scores))
        for rank,idx in enumerate(np.argsort(-scores,kind='stable'),1):
            all_runner_rows.append({'date':date,'time_utc':c['off'].strftime('%H:%M'),
                'meeting':r['course'],'race_id':rid,'race_name':r['race_name'],
                'race_type':r['subtype'],'discipline':r['kind'],'model':name,
                'rank':rank,'horse':r['horse'][idx],
                'model_score_not_calibrated_probability':round(float(scores[idx]),6),
                'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),
                'driver_json':json.dumps(stage159_pillars[int(idx)],separators=(',',':')),
                'odds_used_for_current_prediction':False})
        rank1.append({'date':date,'time_utc':c['off'].strftime('%H:%M'),'meeting':r['course'],
            'race_id':rid,'race_name':r['race_name'],'race_type':r['subtype'],'discipline':r['kind'],
            'model':name,'rank':1,'horse':r['horse'][top],'field_size':len(r['horse']),
            'model_score_not_calibrated_probability':round(float(scores[top]),6),
            'selection_branch':str(level)+':'+str(group),'trained_through':cutoff,
            'prediction_asof':L.utcnow().isoformat(),'odds_used_for_current_prediction':False})

    if not rank1:raise RuntimeError('No unstarted races could be scored')
    grouped=collections.Counter((r['model'],r['race_id']) for r in rank1)
    if any(n!=1 for n in grouped.values()):raise RuntimeError('Duplicate model/race picks')
    L.csvwrite(args.out/'stage152-strongest-roi-specialists-rank1.csv',rank1)
    L.csvwrite(args.out/'stage152-strongest-roi-specialists-all-runners.csv',all_runner_rows)
    audit={'date':date,'status':'PREOFF_PAPER_ONLY', 'models':list(MODELS),
           'matched_model_count':len(MODELS),'rank1_rows':len(rank1),
           'unique_races':len({r['race_id'] for r in rank1}),
           'training_through':cutoff,'training_races':len(train),
           'historical_database_through':historical_latest,'cards':cards_audit,
           'grade_overlay_rows':len(overlay),'original_d_unchanged':True,
           'base_audit':base_audit,'graded_audit':grade_model_audit,'long_audit':long_audit,
           'pre2022_warmup_races':history_audit['counts'].get('verified_prior_races',0),
           'hpr_coverage':dict(hpr_coverage),'hpr_audit':hpr_audit.get('status','historic'),
           'weight_audit':weights_audit,'debut_audit':debut_audit,
           'skipped':dict(skipped),'stages_141_142_143d_unchanged':True,
           'current_bookmaker_odds_used':False,
           'prior_historical_sp_used_for_stage143d_roi_weights':True,
           'calibrated_probabilities':False,
           'warning':'Research only; zero guarantee of future ROI. Historic form coverage ends at the recorded archive cutoff.'}
    (args.out/'stage152-strongest-roi-specialists-audit.json').write_text(json.dumps(audit,indent=2,default=str)+'\n')
    print('STAGE152_RANK1',json.dumps(rank1),flush=True)
    print('STAGE152_AUDIT',json.dumps({'date':date,'races':audit['unique_races'], 'model_rows':len(rank1),'historical_cutoff':historical_latest}),flush=True)


def self_test():
    assert MODELS==(J.GRADE_WEIGHT,J.ALL,T.BOTH,P.W_G+'__ROI')
    assert quarter_end('2026-11-18')=='2026-09-30'
    assert quarter_end('2027-01-06')=='2026-12-31'
    assert P.W_G=='D_FORM_WEIGHT_GRADE'
    print('STAGE152_SELF_TEST_OK')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('command',choices=['self-test','today'])
    ap.add_argument('--db',type=pathlib.Path)
    ap.add_argument('--reference',type=pathlib.Path)
    ap.add_argument('--cards',type=pathlib.Path)
    ap.add_argument('--date',default='')
    ap.add_argument('--out',type=pathlib.Path)
    args=ap.parse_args()
    if args.command=='self-test':self_test()
    else:
        if any(v is None for v in (args.db,args.reference,args.cards,args.out)):
            ap.error('today requires --db --reference --cards --out')
        main(args)

# STAGE159_MODEL_INPUT_DRIVERS_V1
