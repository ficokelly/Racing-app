#!/usr/bin/env python3
"""Stage64: combine Stage62 full historical top-two with pre-off Nov4/Nov5 replays.
Research only; no changes to production or frozen Model D.
"""
import argparse,csv,json,subprocess,sys,datetime as dt
from pathlib import Path
from collections import defaultdict

def load(p):
 with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def norm(s):return ' '.join(str(s or '').lower().split())
def stat(items):
 valid=[(p,sp) for p,sp in items if p not in ('','NR',None) and sp not in ('',None)]
 wins=sum(str(p)=='1' for p,sp in valid);returns=sum(float(sp) for p,sp in valid if str(p)=='1')
 return {'bets':len(valid),'wins':wins,'strike_rate_pct':round(100*wins/len(valid),3) if valid else None,'returns_units':round(returns,3),'profit_units':round(returns-len(valid),3),'roi_pct':round(100*(returns/len(valid)-1),3) if valid else None}
def main(a):
 a.out.mkdir(parents=True,exist_ok=True)
 old=load(a.baseline);new=load(a.patched)
 ix={(r['date'],r['race_id']):r for r in old};iy={(r['date'],r['race_id']):r for r in new}
 common=sorted(set(ix)&set(iy));rows=[]
 for k in common:
  b,n=ix[k],iy[k]
  row={'date':k[0],'course':b['course'],'time':'','race_id':k[1],'source':'historical_stage49',
       'original_rank1':b['rank1_horse'],'original_rank2':b['rank2_horse'],
       'metadata_rank1':n['rank1_horse'],'metadata_rank2':n['rank2_horse']}
  for label,r in [('original',b),('metadata',n)]:
   for rank in (1,2):row[f'{label}_rank{rank}_pos']=r[f'rank{rank}_finish'];row[f'{label}_rank{rank}_sp']=r[f'rank{rank}_sp']
  rows.append(row)
 days={}
 for day in ('2026-11-04','2026-11-05'):
  p=a.out/f'nov{day[-2:]}'/'stage63_race_by_race.csv'
  if not p.exists():raise SystemExit(f'Missing verified pre-off replay for {day}: {p}')
  rr=load(p);days[day]={'races':len(rr)}
  for x in rr:
   row={'date':day,'course':x['course'],'time':x['time'],'race_id':'','source':'archived_preoff_live_replay'}
   for label in ('original','metadata'):
    for rank in (1,2):
     row[f'{label}_rank{rank}']=x[f'{label}_rank{rank}'];row[f'{label}_rank{rank}_pos']=x[f'{label}_rank{rank}_pos'];row[f'{label}_rank{rank}_sp']=x[f'{label}_rank{rank}_sp']
   rows.append(row)
 # Avoid double counting if historic backtest unexpectedly contains Nov4/Nov5.
 hist=[r for r in rows if r['source']=='historical_stage49' and r['date']<'2026-11-04']
 recent=[r for r in rows if r['source']=='archived_preoff_live_replay']
 rows=hist+recent
 for r in rows:
  r['rank1_changed']=int(norm(r['original_rank1'])!=norm(r['metadata_rank1']))
  r['rank2_changed']=int(norm(r['original_rank2'])!=norm(r['metadata_rank2']))
  r['top2_set_changed']=int({norm(r['original_rank1']),norm(r['original_rank2'])}!={norm(r['metadata_rank1']),norm(r['metadata_rank2'])})
 fields=list(rows[0]);out=a.out/'stage64_all_races.csv'
 with out.open('w',encoding='utf-8',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 with (a.out/'stage64_changed_races.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(r for r in rows if r['rank1_changed'] or r['rank2_changed'])
 def metrics(part):
  result={}
  for label in ('original','metadata'):
   result[label]={f'rank{rank}':stat([(r[f'{label}_rank{rank}_pos'],r[f'{label}_rank{rank}_sp']) for r in part]) for rank in (1,2)}
   both=[(r[f'{label}_rank{rank}_pos'],r[f'{label}_rank{rank}_sp']) for r in part for rank in (1,2)]
   result[label]['both_rank1_and_rank2']=stat(both)
  return {'races':len(part),'rank1_changed':sum(r['rank1_changed'] for r in part),'rank2_changed':sum(r['rank2_changed'] for r in part),'top2_set_changed':sum(r['top2_set_changed'] for r in part),'performance':result}
 summary={'status':'RESEARCH_ONLY','historical_to':'2026-11-03','coverage_note':'Historical Stage49 replay through Nov3 plus separate archived pre-off live replays Nov4 and Nov5; these use different replay paths and must be reported separately, not treated as one uniform backtest.',
 'historical_2025_to_nov3':metrics(hist),'nov4':metrics([r for r in recent if r['date']=='2026-11-04']),'nov5':metrics([r for r in recent if r['date']=='2026-11-05']),
 'combined_descriptive_only':metrics(rows),'limits':['Live Nov4/Nov5 uses original Stage38 live scorer and archived racecards; historical period uses Stage49 historical scorer.','Pre-off Git snapshot required for each date; if missing, workflow fails rather than inventing results.','Results must be available and uniquely matched; missing SP excluded.','Historical training source may stop Oct7, so live replay is not necessarily equivalent to actual Nov5 model history.','No new trends, retraining or weight changes.']}
 (a.out/'stage64_summary.json').write_text(json.dumps(summary,indent=2))
 print(json.dumps({k:{'races':v['races'],'rank1_changed':v['rank1_changed'],'rank2_changed':v['rank2_changed']} for k,v in summary.items() if isinstance(v,dict) and 'races' in v},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--patched',type=Path,required=True);p.add_argument('--out',type=Path,default=Path('reports/stage64'));main(p.parse_args())
