#!/usr/bin/env python3
"""Stage140: 2026 chronological research replay of Stage139 D137+HPR Ability.

Frozen Stage137 / Stage139 base code is imported, never changed on disk.
SAME features, HPR-v3.1 formula, 0.35 Ability lift, ML parameters and fallbacks.
2023-25 historical training; 2026 Q1, Q2, Q3 and available Q4 evaluated,
retraining only after each completed quarter. HPR scores are available only
from previous CALENDAR days. Stage137's original 2022 start is unchanged;
HPR independently uses all verified archive years (1988 onward if available).
Archive availability is not a claim that every historical horse start exists.
No production writes, no simulated 2026 claims beyond source coverage.

Technical exception required only for forward scoring: the original research
Past.score deliberately short-circuits for years outside 2023-2025. This
module temporarily enables 2026 in its identical source body in memory,
asserting that the *only* substitution is the allowed scoring year. It does
not alter Stage30 file, D weights or 2023-2025 predictions.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,inspect,json,math,pathlib,sqlite3,sys,textwrap
from collections import defaultdict
import numpy as np
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H

VERSION='stage140-2026-d137-hpr-ability-exact-v1'
MODEL_A='STAGE137_ORIGINAL_REPLAY'
MODEL_B='D137_HPR_ABILITY'
SOURCE_SCORE_GUARD='if day.year not in (2023,2024,2025):return None'
SPLITS=(('2026Q1','2026-01-01','2026-03-31'),('2026Q2','2026-04-01','2026-06-30'),
        ('2026Q3','2026-07-01','2026-09-30'),('2026Q4','2026-10-01','2026-12-31'))
FIELDS=('period','date','race_id','course','country','race_name','broad','discipline','subtype','official_tier',
        'race_type','model','horse','second_horse','won','rank2_won','winner','sp','field_size',
        'chosen_p','second_p','trained_through','hpr_top','hpr_second','hpr_covered_runners','hpr_coverage_pct',
        'selected_hpr_prior_runs','bank')


def audit_limit(db, max_date=None):
    db=pathlib.Path(db).resolve()
    cx=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
    try:
        assert cx.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        earliest,latest=cx.execute('SELECT MIN(race_date), MAX(race_date) FROM historical_races').fetchone()
        boundary=(dt.date.today()-dt.timedelta(days=1)).isoformat()
        last=min(latest,boundary,str(max_date) if max_date else '9999-12-31')
        if not '2026-03-01'<=last<='2026-12-31':
            raise RuntimeError('2026 archive incomplete or beyond 2026: '+str((earliest,latest,last)))
        months=dict(cx.execute('''SELECT substr(race_date,1,7),COUNT(*) FROM historical_races
                                  WHERE race_date>='2026-01-01' AND race_date<=?
                                  GROUP BY substr(race_date,1,7)''',(last,)))
        summary={'archive_earliest':earliest,'archive_last_date':latest,'evaluation_last_date':last,
                 'cutoff_is_partial_quarter':last<'2026-12-31',
                 '2026_archive_races_per_month':months}
        return last,summary
    finally:cx.close()


def extend_stage137_only_for_2026(db,reference,last):
    """Return original Stage137 data plus eligible 2026 fields, no original-file edit."""
    import stage30_gap_aware_abcd as D
    import stage108_model_g2 as G
    original_score=D.Past.score
    original_ref=S.read_reference;original_read=S.read_races
    raw=textwrap.dedent(inspect.getsource(original_score))
    assert raw.count(SOURCE_SCORE_GUARD)==1,'Unexpected original D scoring source: refuse to patch'
    replaced=raw.replace(SOURCE_SCORE_GUARD,'if day.year not in (2023,2024,2025,2026):return None')
    assert replaced.replace('if day.year not in (2023,2024,2025,2026):return None',SOURCE_SCORE_GUARD)==raw
    ns=dict(D.__dict__)
    exec(compile(replaced,'<stage140-year-only-original-D-score>', 'exec'),ns)
    new_score=ns['score']
    def ref_loader(path):return G.load_reference(path,first=S.HISTORY_START,last=last)
    def stream(dbpath,first=S.HISTORY_START,last_date=last):
        return original_read(dbpath,first=first,last=last_date)
    try:
        D.Past.score=new_score
        S.read_reference=ref_loader; S.read_races=stream
        return S.construct(db,reference)
    finally:
        D.Past.score=original_score
        S.read_reference=original_ref;S.read_races=original_read


def store_rows(path,rows,compressed=False):
    if not rows:raise RuntimeError('No rows to save: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    op=gzip.open if compressed else open
    with op(path,'wt',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def metric(picks):
    n=len(picks);wins=sum(x['won'] for x in picks)
    priced=[x for x in picks if x['sp'] is not None and 1<float(x['sp'])<1001]
    returned=sum(float(x['sp']) for x in priced if x['won'])
    capped=sum(min(float(x['sp']),21.) for x in priced if x['won'])
    return {'races':n,'rank1_winners':wins,'strike_pct':round(100*wins/n,3) if n else None,
            'rank2_winners':sum(x['rank2_won'] for x in picks),
            'rank1_or_rank2':wins+sum(x['rank2_won'] for x in picks),
            'priced':len(priced),'price_coverage_pct':round(100*len(priced)/n,2) if n else None,
            'sp_roi_pct':round(100*(returned/len(priced)-1),3) if priced else None,
            'capped_sp_roi_pct':round(100*(capped/len(priced)-1),3) if priced else None,
            'roi_coverage_caution':bool(n and len(priced)/n<.9)}


def summarise(rows):
    groups=defaultdict(list)
    for p in rows:
        for dim,key in [('ALL','ALL'),('YEAR',p['date'][:4]),('QUARTER',p['period']),
                        ('MONTH',p['date'][:7]),('BROAD',p['broad']),
                        ('DISCIPLINE',p['discipline']),('RACE_TYPE',p['subtype']),
                        ('COUNTRY',p['country']),('OFFICIAL_TIER',p['official_tier'])]:
            groups[(p['model'],dim,key)].append(p)
    return [{'model':model,'breakdown':dim,'group':name,**metric(rs)}
            for (model,dim,name),rs in sorted(groups.items())]


def verified_2025_regression(train,races,archived):
    """Reproduce source Stage137 Q4-2025, to prove 2026 extension did not drift."""
    train5=[r for r in races if '2023-01-01'<=r['date']<'2025-10-01']
    hold5=[r for r in races if '2025-10-01'<=r['date']<='2025-12-31']
    base=H.l137_ml_banks(train5)
    with gzip.open(archived,'rt',newline='',encoding='utf8') as f:
        expected={str(x['race_id']):x for x in csv.DictReader(f)
                  if x['period']=='2025Q4' and x['model']=='D_RACETYPE_NLP'}
    mismatch=[];checked=0
    for r in hold5:
        q,_=S.predict_bank(base,r);picked=r['horse'][int(np.argmax(q))]
        e=expected.get(str(r['race_id']))
        if not e or str(e['horse'])!=str(picked):
            if len(mismatch)<20:mismatch.append({'race':r['race_id'],'expected':e.get('horse') if e else None,'got':picked})
        checked+=1
    if checked<3000 or len(expected)!=checked or mismatch:
        raise RuntimeError('2025Q4 Stage137 reproduction drift: '+str({'checked':checked,'archived':len(expected),'mismatch':mismatch[:10]}))
    return {'2025_q4_baseline_exact_match':True,'2025_q4_checked_races':checked}


def evaluate(races,last,out,archived):
    fit_races=[r for r in races if r['date']>='2023-01-01']
    in2026=[r for r in fit_races if '2026-01-01'<=r['date']<=last]
    if len(in2026)<500:raise RuntimeError('Fewer than 500 eligible verified 2026 races')
    regression=verified_2025_regression(None,races,archived)
    predictions=[];horses=[];folds=[]
    for period,start,planned_end in SPLITS:
        if last<start:break
        end=min(planned_end,last)
        training=[r for r in fit_races if '2023-01-01'<=r['date']<start]
        testing=[r for r in in2026 if start<=r['date']<=end]
        if not testing:
            folds.append({'period':period,'completed':False,'reason':'No verified eligible 2026 races','start':start,'end':end})
            continue
        if len(testing)<100:raise RuntimeError('Too few eligible verified races '+period+': '+str(len(testing)))
        base=H.l137_ml_banks(training)
        ability=H.l137_ml_banks(training,ability=True)
        through=(dt.date.fromisoformat(start)-dt.timedelta(days=1)).isoformat()
        for r in testing:
            b,bank_b=S.predict_bank(base,r)
            b2,bank_h=S.predict_bank(ability,H.hpr_ability_137(r))
            if not np.all(np.isfinite(b2)):raise RuntimeError('Nonfinite prediction for '+str(r['race_id']))
            for model,p,bank in [(MODEL_A,b,bank_b),(MODEL_B,b2,bank_h)]:
                order=np.argsort(-p,kind='stable');i=int(order[0]);j=int(order[1]);sp=float(r['sp'][i]) if np.isfinite(r['sp'][i]) else None
                predictions.append({'period':period,'date':r['date'],'race_id':r['race_id'],
                  'course':r['course'],'country':r['country'],'race_name':r['race_name'],
                  'broad':r['broad'],'discipline':r['kind'],'subtype':r['subtype'],
                  'official_tier':r['official_tier'],'race_type':r['type'],
                  'model':model,'horse':r['horse'][i],'second_horse':r['horse'][j],
                  'won':int(i==r['winner_idx']),'rank2_won':int(j==r['winner_idx']),
                  'winner':r['horse'][r['winner_idx']],'sp':sp,'field_size':len(r['horse']),
                  'chosen_p':round(float(p[i]),6),'second_p':round(float(p[j]),6),
                  'trained_through':through,
                  'hpr_top':round(float(r['hpr_preoff'][i]),3) if np.isfinite(r['hpr_preoff'][i]) else '',
                  'hpr_second':round(float(r['hpr_preoff'][j]),3) if np.isfinite(r['hpr_preoff'][j]) else '',
                  'hpr_covered_runners':int(r['hpr_known'].sum()),
                  'hpr_coverage_pct':round(100*float(r['hpr_known'].mean()),2),
                  'selected_hpr_prior_runs':int(r['hpr_prior_same_code'][i]),'bank':bank})
            for idx,h in enumerate(r['horse']):
                horses.append({'period':period,'date':r['date'],'race_id':r['race_id'],'horse':h,
                               'broad':r['broad'],'race_type':r['subtype'],'won':int(idx==r['winner_idx']),
                               'hpr_preoff':round(float(r['hpr_preoff'][idx]),3) if np.isfinite(r['hpr_preoff'][idx]) else '',
                               'hpr_relative':round(float(r['hpr_z'][idx]),6),'hpr_prior_runs':int(r['hpr_prior_same_code'][idx]),
                               'base_score':round(float(b[idx]),6),'ability_hpr_score':round(float(b2[idx]),6),
                               'base_rank':int(np.sum(b>b[idx]))+1,'hpr_rank':int(np.sum(b2>b2[idx]))+1})
        folds.append({'period':period,'start':start,'end':end,
                      'completed':end==planned_end,'training_through':through,
                      'train_races':len(training),'verified_test_races':len(testing),
                      'original_stage137_score_bank_count':len(base),'ability_hpr_bank_count':len(ability)})
        print('STAGE140_QUARTER',json.dumps(folds[-1]),flush=True)
    if not predictions:raise RuntimeError('No 2026 selections produced')
    write={'predictions':'stage140-2026-rank1-rank2.csv.gz',
           'runners':'stage140-2026-full-runners.csv.gz',
           'metrics':'stage140-2026-period-racetype.csv'}
    summary=summarise(predictions)
    store_rows(out/write['predictions'],predictions,True)
    store_rows(out/write['runners'],horses,True)
    store_rows(out/write['metrics'],summary)
    overall={m:next(x for x in summary if x['model']==m and x['breakdown']=='ALL') for m in (MODEL_A,MODEL_B)}
    assert overall[MODEL_A]['races']==overall[MODEL_B]['races']
    return overall,folds,regression,write


def research(db,reference,archive,out,to_date=None):
    import stage30_gap_aware_abcd as D
    last,audit=audit_limit(db,to_date)
    source=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    if source['chosen_combination']!='D-064-gap14':raise RuntimeError('Original D identity changed')
    exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
    if not np.allclose([source['weights_pct'][p] for p in S.PILLARS],exact*100,atol=.011):
        raise RuntimeError('Original D pillar weights changed')
    S.FROZEN[:]=exact;S.CANDIDATES=S.candidate_weights()
    race_rows,sa,refaudit=extend_stage137_only_for_2026(db,reference,last)
    target={str(k) for r in race_rows for k in r['identity'] if k}
    print('STAGE140 historical race 2023-2026',len(race_rows),'target identities',len(target),flush=True)
    out.mkdir(parents=True,exist_ok=True)
    prior_end=H.END
    try:
        H.END=last
        hpr_db,hpr_audit=H.hpr_history(db,reference,out,target)
    finally:H.END=prior_end
    cov=collections.Counter();H.attach_hpr(race_rows,hpr_db,cov)
    overall,folds,regression,files=evaluate(race_rows,last,out,archive)
    s={'version':VERSION,'status':'RESEARCH_ONLY_2026_HISTORICAL_REPLAY','model_names':[MODEL_A,MODEL_B],
       'frozen_D_unchanged':True,'Stage137_unchanged':True,'Stage139_HPR_unchanged':True,
       'live_app_modified':False,'model_paper_only':True,
       'hpr_treatment':'EXACT Stage139 Ability +0.35 within-field-normalized prior HPR; no ninth pillar',
       'classification_and_model_source':'Stage137 and Stage139 unchanged imported source',
       'hpr_history_earliest':hpr_audit['history_start'],
       'source_date_audit':audit,'model_source_audit':sa,'reference_audit':refaudit,
       'hpr_source_audit':hpr_audit,'hpr_coverage':dict(cov),'baseline_guard':regression,
       'training_years':'2023-2025; in 2026 each completed prior quarter enters training',
       'overall':overall,'folds':folds,'results_files':files,
       'limitations':['Archived results stop at the source-reported last date, possibly before current date.',
         '2026 testing was conceived in November 2026 after seeing some 2026 outcomes; retrospective chronological validation is not a pristine prospective test.',
         'Prices missing in the 2026 archive are NOT externally fabricated or treated as losing bets.',
         '2026 SP ROI is for the priced subset only; flag if coverage below 90%.',
         'Some historic race comments, grades or pre-race form may be missing.',
         'Stage137 recent-form lookback starts in 2022; only HPR searches the earliest verified archive history.',
         'Race results are used for outcome settlement only after those races; racecards reflect actual starters, not pre-scratch declarations.',
         'Model probabilities are not race-normalised or calibrated winning probabilities.']}
    (out/'stage140-2026-summary.json').write_text(json.dumps(s,indent=2,default=str)+'\n')
    print('STAGE140_2026_OVERALL',json.dumps(overall),flush=True)
    return s


def self_test():
    assert SOURCE_SCORE_GUARD=='if day.year not in (2023,2024,2025):return None'
    assert H.ABILITY_LIFT==.35
    assert 'horse_beaten3' in S.FEATURES
    assert len(S.PILLARS)==8
    assert len(SPLITS)==4
    assert SPLITS[0][1]=='2026-01-01'
    assert H.hpr_ability_137.__module__=='stage139_hpr_137_138_walkforward'
    assert metric([{'won':1,'rank2_won':0,'sp':5},{'won':0,'rank2_won':1,'sp':None}])['price_coverage_pct']==50
    print('STAGE140 SELFTEST PASS: 2026-only, exact HPR Ability, quarter chronology, strict SP coverage')


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test')
    q=sub.add_parser('walkforward');q.add_argument('--db',required=True,type=pathlib.Path)
    q.add_argument('--reference',required=True,type=pathlib.Path)
    q.add_argument('--stage137',required=True,type=pathlib.Path)
    q.add_argument('--out',required=True,type=pathlib.Path)
    q.add_argument('--to-date',default=None,help='Optional maximum <= archive/yesterday')
    args=p.parse_args()
    if args.command=='self-test':self_test()
    else:research(args.db,args.reference,args.stage137,args.out,args.to_date)
if __name__=='__main__':main()
