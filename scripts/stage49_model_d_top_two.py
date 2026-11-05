#!/usr/bin/env python3
"""Stage49: unchanged Model D top-two walk-forward paper replay, 2025-2026.

This is retrospective research using verified starters. Same-day outcomes are
never added to history until the next date. No live models/files are changed.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
import numpy as np
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast, prediction_rows

START = '2025-01-01'
END = '2026-11-03'  # 4 November was first live Model D day
VERSION = 'model-d-stage38-2023-24-frozen-gap-aware'
FIELDS = ['date','course','race_id','discipline','field_size','winner','winner_rank',
          'winner_sp','rank1_horse','rank2_horse','rank3_horse',
          'rank1_sp','rank2_sp','rank1_finish','rank2_finish','rank3_finish',
          'score_gap','gap_gate_qualified','winner_in_top2',
          'exact_forecast','reverse_forecast','top3_combination',
          'rank1_return','rank2_return']

def price(x):
    try:
        v=float(x)
        return v if math.isfinite(v) and 1.01 <= v <= 5000 else None
    except (TypeError,ValueError):return None

def race_record(r,weights,gate,positions):
    scores,order,gap=prediction_rows(r,weights)
    winner=r['winning_index']
    if winner is None or not (0 <= winner < len(r['horse'])):return None
    n=len(order)
    i1,i2=int(order[0]),int(order[1])
    i3=int(order[2]) if n>2 else None
    sp=[price(s) for s in r['sp']]
    rank={int(v):i+1 for i,v in enumerate(order)}
    p1,p2=positions[i1],positions[i2]
    p3=positions[i3] if i3 is not None else None
    return {'date':r['date'],'course':r['course'],'race_id':r['race_id'],
        'discipline':r['kind'],'field_size':n,
        'winner':r['horse'][winner],'winner_rank':rank[winner],
        'winner_sp':sp[winner],
        'rank1_horse':r['horse'][i1],'rank2_horse':r['horse'][i2],
        'rank3_horse':r['horse'][i3] if i3 is not None else '',
        'rank1_sp':sp[i1],'rank2_sp':sp[i2],
        'rank1_finish':p1,'rank2_finish':p2,'rank3_finish':p3,
        'score_gap':round(float(gap),6),'gap_gate_qualified':int(gap >= gate),
        'winner_in_top2':int(winner in (i1,i2)),
        'exact_forecast':int(p1==1 and p2==2),
        'reverse_forecast':int(p1==2 and p2==1),
        'top3_combination':int(i3 is not None and {p1,p2,p3}=={1,2,3}),
        'rank1_return':sp[i1] if winner==i1 and sp[i1] is not None else 0.,
        'rank2_return':sp[i2] if winner==i2 and sp[i2] is not None else 0.}

def summary(rows):
    count=len(rows)
    a=[x for x in rows if x['rank1_sp'] is not None]
    b=[x for x in rows if x['rank2_sp'] is not None]
    complete=[x for x in rows if x['rank1_sp'] is not None and x['rank2_sp'] is not None]
    w1=sum(x['winner_rank']==1 for x in rows)
    w2=sum(x['winner_rank']==2 for x in rows)
    def calc(n,gross):
        return {'stakes':n,'returned':round(gross,3),'profit':round(gross-n,3),
                'roi_pct':round((gross/n-1)*100,3) if n else None}
    ret1=sum(x['rank1_return'] for x in a)
    ret2=sum(x['rank2_return'] for x in b)
    exact=sum(x['exact_forecast'] for x in rows)
    reverse=sum(x['reverse_forecast'] for x in rows)
    return {'races':count,'rank1_winners':w1,'rank2_winners':w2,
        'top2_winners':w1+w2,
        'top2_win_coverage_pct':round(100*(w1+w2)/count,3) if count else None,
        'rank1_winner_rate_pct':round(100*w1/count,3) if count else None,
        'rank2_winner_rate_pct':round(100*w2/count,3) if count else None,
        'exact_order_forecasts':exact,'reverse_order_forecasts':reverse,
        'dual_order_forecast_hits':exact+reverse,
        'top3_combination_finishes':sum(x['top3_combination'] for x in rows),
        'rank1_priced':len(a),'rank2_priced':len(b),'both_priced':len(complete),
        'rank1_sp':calc(len(a),ret1),'rank2_sp':calc(len(b),ret2),
        'both_ranked_sp':calc(len(a)+len(b),ret1+ret2),
        'both_priced_races_only_sp':calc(2*len(complete),sum(x['rank1_return']+x['rank2_return'] for x in complete))}

def calculate(db,out):
    config=json.loads(Path('reports/stage30/stage30-summary.json').read_text())
    model=config['model_results']['D']
    if model['chosen_combination']!='D-064-gap14':
        raise RuntimeError('Refusing altered Model D combination')
    weights=[float(model['weights_pct'][k])/100 for k in OLD.METRICS]
    gate=float(model['selection_margin'])
    if len(weights)!=8 or abs(sum(weights)-1)>0.002 or gate!=0.14:
        raise RuntimeError('Frozen Model D weights/threshold do not match')
    out.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
    cx.execute('PRAGMA temp_store=MEMORY')
    sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
       u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
       u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment
       FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
       WHERE r.race_date <= ? ORDER BY r.race_date,r.id'''
    past=LivePast({})
    races=[]
    pending=[]
    current=[]
    last_date=None
    last_race=None
    scanned=0
    skipped=0
    invalid=0
    source_last_date=None
    def finish(group):
        nonlocal skipped,invalid
        if not group:return
        if OLD.track(group[0][2]) not in OLD.TRACKS:
            skipped+=1
            return
        when=str(group[0][1])
        # Pre-race prediction; historical data updated at the NEXT date boundary.
        if when >= START:
            starters=[z for z in group if OLD.started(z)]
            winners=[i for i,z in enumerate(starters) if z[12]==1]
            if len(starters)>=2 and len(winners)==1:
                r=past.score(group,live=True)
                if r is not None:
                    positions=[int(z[12]) if isinstance(z[12],int) and z[12]>0 else None for z in starters]
                    record=race_record(r,weights,gate,positions)
                    if record:races.append(record)
            else:invalid+=1
        pending.append(group)
    for row in cx.execute(sql,(END,)):
        day,identity=row[1],row[0]
        source_last_date=day
        if last_date is not None and day!=last_date:
            if current:finish(current);current=[]
            # Yesterday and earlier only. All races on one day see the same history.
            for group in pending:past.update(group)
            pending=[]
        if current and identity!=last_race:finish(current);current=[]
        current.append(row)
        last_date,last_race=day,identity
        scanned+=1
        if scanned%1000000==0:print('Processed historical rows',scanned,'target races',len(races),flush=True)
    if current:finish(current)
    cx.close()
    if not races:raise RuntimeError('No 2025-26 races scored; check source database coverage')
    all_years=defaultdict(list)
    kind_year=defaultdict(list)
    monthly=defaultdict(list)
    for r in races:
        all_years[r['date'][:4]].append(r)
        kind_year[(r['date'][:4],r['discipline'])].append(r)
        monthly[r['date'][:7]].append(r)
    if not {'2025','2026'}.issubset(all_years):
        raise RuntimeError('Missing 2025 or 2026 historical data; incomplete comparison')
    with (out/'model_d_top_two_2025_2026_per_race.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader();writer.writerows(races)
    report={'version':'stage49-frozen-model-d-top2-v1',
      'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
      'status':'RESEARCH_ONLY','model_version':VERSION,
      'weight_combination':model['chosen_combination'],
      'weights_pct':model['weights_pct'],'selection_margin':gate,
      'requested_start':START,'requested_end':END,
      'actual_historical_source_last_date':source_last_date,
      'source_rows_scanned':scanned,'non_uk_ie_races_excluded':skipped,
      'races_without_one_verified_winner_or_two_starters':invalid,
      'whole_sample':summary(races),
      'by_year':{key:summary(val) for key,val in sorted(all_years.items())},
      'by_year_and_discipline':{'|'.join(key):summary(val) for key,val in sorted(kind_year.items())},
      'by_month':{key:summary(val) for key,val in sorted(monthly.items())},
      'by_year_original_selection_gate':{key:summary([r for r in val if r['gap_gate_qualified']]) for key,val in sorted(all_years.items())},
      'limitations':[
          'All eligible UK/Irish historical races use exactly frozen Stage38/Stage30 D eight-feature score.',
          '2023-2024 supplied fixed weights and 2025-26 have previously been inspected; this is not a virgin holdout.',
          'Historical starter list reflects actual starters, not necessarily archived pre-off declarations.',
          'No same-calendar-day results leak into scoring. Current and final odds are not predictive features.',
          'SP is used for hypothetical unit stake settlement only, excluding a rank when that SP is missing.',
          'Dual forecast and tricast successes counted; dividend data absent, so their financial ROI is not fabricated.',
          '2026 results stop at the latest recorded result in the historical source, even if before requested end.',
          'No live model, weight, calibration, results archive or app file is changed.'
      ]}
    (out/'model_d_top_two_2025_2026_summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'source_end':source_last_date,'by_year':report['by_year'],
                      'by_year_and_discipline':report['by_year_and_discipline']},indent=2),flush=True)
    return report

def self_test():
    base={k:None for k in FIELDS}
    base.update(date='2025-01-01',winner_rank=2,rank1_sp=7.,rank2_sp=4.,
                rank1_return=0.,rank2_return=4.,exact_forecast=0,
                reverse_forecast=1,top3_combination=0)
    x=summary([base])
    assert x['both_ranked_sp']['stakes']==2
    assert x['top2_winners']==1 and x['both_ranked_sp']['roi_pct']==100.
    print('Stage49 top-two synthetic self test passed')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=Path)
    p.add_argument('--output',type=Path,default=Path('reports/stage49'))
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test()
    else:
        if not a.db:p.error('--db required')
        calculate(a.db,a.output)
