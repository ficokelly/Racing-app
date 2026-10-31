#!/usr/bin/env python3
"""Stage 4: historical race-cohort trends and data readiness (stdlib only).
Exploratory association, NOT calibrated winning probabilities or a live form model.
"""
import argparse,csv,json,sqlite3,collections,os,math
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',default='stage4_output');p.add_argument('--min-races',type=int,default=40);a=p.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 con=sqlite3.connect('file:'+os.path.abspath(a.db)+'?mode=ro',uri=True)
 con.row_factory=sqlite3.Row
 required={'historical_races':{'id','race_date','course','race_type','surface','distance_f','going','race_class'},'historical_runs':{'race_id','horse_name_norm','finish_pos','trainer','jockey','sp_decimal','age','official_rating','rpr','topspeed','draw'}}
 schema={t:{r[1] for r in con.execute('PRAGMA table_info('+t+')')} for t in required}
 missing={t:sorted(cols-schema[t]) for t,cols in required.items() if cols-schema[t]}
 if missing:raise SystemExit('Missing schema fields: '+json.dumps(missing))
 # No race name exists in the imported schema: this is explicitly comparable-race analysis.
 # Grouping uses course + surface/type + distance bucket + class. Avoid per-race lookahead.
 race_sql='SELECT id,race_date,course,race_type,surface,distance_f,going,race_class FROM historical_races ORDER BY race_date,id'
 runs_sql='SELECT race_id,horse_name_norm,finish_pos,trainer,jockey,sp_decimal,age,official_rating,rpr,topspeed,draw FROM historical_runs ORDER BY race_id'
 races={r['id']:dict(r) for r in con.execute(race_sql)}
 by_race=collections.defaultdict(list)
 for r in con.execute(runs_sql):by_race[r['race_id']].append(dict(r))
 def bucket(x):
  if x is None:return 'unknown'
  v=float(x);return f'{round(v*2)/2:g}f'
 def cohort(r):return (str(r['course'] or 'unknown').strip().upper(),str(r['surface'] or r['race_type'] or 'unknown').strip().upper(),bucket(r['distance_f']),str(r['race_class'] or 'unknown').strip().upper())
 def band(v,typ):
  if v is None:return 'unknown'
  try:v=float(v)
  except (ValueError,TypeError):return 'unknown'
  if typ=='age':return str(int(v)) if 2<=v<=20 else 'other'
  if typ=='sp':return ('odds-on' if v<2 else 'evens-3/1' if v<=4 else 'over 3/1-8/1' if v<=9 else 'over 8/1-16/1' if v<=17 else 'over 16/1')
  if typ=='draw':return str(int(v)) if 1<=v<=30 else 'other'
  if typ=='rating':return str(int(v//10)*10)+'-'+str(int(v//10)*10+9)
  return 'unknown'
 summary=collections.Counter();groups=collections.defaultdict(lambda:collections.Counter());dimensions=collections.defaultdict(lambda:collections.Counter())
 annual=collections.defaultdict(lambda:collections.Counter())
 for rid,r in races.items():
  rows=by_race.get(rid,[]);year=str(r['race_date'])[:4];annual[year]['races']+=1;annual[year]['runners']+=len(rows)
  if not rows:summary['empty_races']+=1;continue
  winners=[x for x in rows if x['finish_pos']==1]
  if len(winners)!=1:summary['non_single_winner_races']+=1;continue
  # Exclude unplaced/withdrawn/nonfinishers? They are valid losing starters when SP exists.
  starters=[x for x in rows if x['sp_decimal'] is not None and x['sp_decimal']>=1.01]
  if not starters or winners[0] not in starters:summary['unusable_sp_races']+=1;continue
  key=cohort(r);groups[key]['races']+=1;groups[key]['runners']+=len(starters)
  summary['valid_races']+=1;summary['valid_runners']+=len(starters)
  for x in starters:
   win=int(x['finish_pos']==1)
   for dim,col,kind in [('age','age','age'),('sp_band','sp_decimal','sp'),('draw','draw','draw'),('official_rating','official_rating','rating')]:
    b=band(x[col],kind);dimensions[(key,dim,b)]['runners']+=1;dimensions[(key,dim,b)]['wins']+=win
    if win:dimensions[(key,dim,b)]['winning_sp_return']+=float(x['sp_decimal'])
 # Avoid false certainty: show all groups with enough historical races; use enrichment over cohort baseline.
 with (out/'cohort_trends.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['course','surface_or_type','distance_bucket','class','historical_races','historical_runners','dimension','value','runners_with_value','winners_with_value','runner_share_pct','winner_share_pct','enrichment_ratio','roi_sp_pct','caution'])
  for (key,dim,b),v in sorted(dimensions.items()):
   g=groups[key]
   if g['races']<a.min_races:continue
   n=v['runners'];wins=v['wins'];runner_share=n/g['runners'];winner_share=wins/g['races'];enrichment=winner_share/runner_share if runner_share else None
   roi=100*(v['winning_sp_return']-n)/n if n else None
   w.writerow([*key,g['races'],g['runners'],dim,b,n,wins,round(100*runner_share,2),round(100*winner_share,2),round(enrichment,3) if enrichment else '',round(roi,2) if roi is not None else '', 'DESCRIPTIVE ONLY; SP IS POST-RACE; NO FORM MODEL OR HOLDOUT VALIDATION'])
 with (out/'annual_readiness.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['year','races','runners']);[w.writerow([y,c['races'],c['runners']]) for y,c in sorted(annual.items())]
 report={'status':'complete','analysis':'comparable-race descriptive historical trends','years':sorted(annual),'race_count':len(races),'summary':dict(summary),'cohorts_total':len(groups),'cohorts_above_threshold':sum(g['races']>=a.min_races for g in groups.values()),'limitations':['No race names in current schema: cannot identify named recurring race editions.','SP is a post-race price; SP bands are descriptive and cannot be used as known pre-race features.','RPR and Topspeed may be post-race performance ratings; excluded from trends until temporal provenance verified.','Horse identities are not validated; recent form and course form are not computed.','No holdout validation, odds calibration or live runner ranking in this audit.','Dead heats and races without exactly one identified winner excluded.','Historical coverage does not prove all races were captured.']}
 (out/'readiness.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
