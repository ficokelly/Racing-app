#!/usr/bin/env python3
"""Research-only same-clock B/C predictions; never writes data/ or modifies app."""
import argparse,collections,csv,datetime as dt,json,pathlib,sys
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage65p_race_type_c_only as C
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast,live_row,prediction_rows,name,clock,nonrunner,time_utc

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',required=True);p.add_argument('--day');a=p.parse_args()
 day=a.day or dt.datetime.now(ZoneInfo('Europe/Dublin')).date().isoformat()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 cards=json.loads(pathlib.Path('data/racecards.json').read_text());d=json.loads(pathlib.Path('data/strategy-d-probabilities.json').read_text())
 fields=collections.defaultdict(list)
 for r in cards.get('runners',[]):
  if str(r.get('date') or r.get('race_time') or '')[:10]!=day or not r.get('horse') or not r.get('course') or not r.get('race_time') or nonrunner(r):continue
  fields[(name(r['course']),clock(r['race_time']))].append(r)
 fields={k:v for k,v in fields.items() if len(v)>=2 and len({name(z['horse']) for z in v})==len(v)}
 if not fields:raise RuntimeError('No valid current-day fields; do not create empty comparison')
 X,y,years,counts,_=C.build_training(a.db);tr=years==2024;va=years==2025
 for yr in (2024,2025):
  total=sum(v for (year,cat),v in counts.items() if year==yr);classified=sum(v for (year,cat),v in counts.items() if year==yr and cat!='unknown')
  if not total or classified/total<.60:raise RuntimeError(f'Insufficient race category coverage {yr}: {classified}/{total}')
 scaler=StandardScaler().fit(X[tr]);model=LogisticRegression(C=.1,max_iter=500).fit(scaler.transform(X[tr]),y[tr])
 audit=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())
 cfg=audit['model_results']['D'];weights=[float(cfg['weights_pct'][k])/100 for k in OLD.METRICS]
 past=LivePast({});history_info=past.scan_until(a.db,day)
 unique={}
 for h,country in past.last:
  if h not in unique:unique[h]=country
  elif unique[h]!=country:unique[h]=None
 entries=[]
 for (_place,t),rs in sorted(fields.items(),key=lambda x:(x[0][1],x[0][0])):
  entries.append({'date':day,'course':rs[0]['course'],'time':t,'runners':rs})
 feats=C.live_features(a.db,day,entries)
 dmap={(name(r.get('course')),r.get('time')):r for r in d.get('races',[]) if r.get('date')==day}
 rows=[];now=dt.datetime.now(dt.timezone.utc)
 for e in entries:
  rs=e['runners'];off=time_utc(rs[0]['race_time'])
  if not off or off<=now+dt.timedelta(minutes=2):continue
  key=(name(e['course']),e['time']);mat,infos=feats[(day,*key)]
  scored=past.score([live_row(z,day,'STAGE66|'+day+'|'+e['course']+'|'+e['time'],unique) for z in rs],live=True)
  if scored is None:continue
  ds,order,_=prediction_rows(scored,weights);learned=model.predict_proba(scaler.transform(mat))[:,1]
  dz=(ds-ds.mean())/(ds.std()+1e-8);lz=(learned-learned.mean())/(learned.std()+1e-8)
  rank_b=sorted(range(len(rs)),key=lambda i:(-ds[i],i));rank_c=sorted(range(len(rs)),key=lambda i:(-(.75*dz[i]+.25*lz[i]),i))
  dr=dmap.get(key);dranks=sorted(dr.get('runners',[]),key=lambda r:r.get('rank',999)) if dr else []
  typ=rs[0].get('race_name') or rs[0].get('race_type') or ''
  discipline=infos[0]['discipline'];category=C.category(typ)
  nh_handicap=category=='handicap' and discipline in ('hurdle','chase')
  rows.append({'date':day,'time':e['time'],'course':e['course'],'race_name':typ,'race_type':category,'discipline':discipline,'nh_handicap':nh_handicap,'off_utc':off.isoformat(),'a_rank1':dranks[0]['horse'] if len(dranks)>0 else '', 'a_rank2':dranks[1]['horse'] if len(dranks)>1 else '', 'a_snapshot_generated_at':d.get('generated_at') if dr else '', 'b_rank1':rs[rank_b[0]]['horse'],'b_rank2':rs[rank_b[1]]['horse'],'c_rank1':rs[rank_c[0]]['horse'],'c_rank2':rs[rank_c[1]]['horse'],'runner_count':len(rs)})
 if not rows:raise RuntimeError('No future races to score')
 stamp=now.strftime('%Y%m%dT%H%M%SZ');path=out/f'comparison_{stamp}.csv'
 with path.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 (out/f'comparison_{stamp}.json').write_text(json.dumps({'generated_at':now.isoformat(),'day':day,'source_d_generated_at':d.get('generated_at'),'history_info':history_info,'research_only':True,'app_unchanged':True,'a_missing_count':sum(not x['a_rank1'] for x in rows),'races':rows},indent=2)+'\n')
 print(f'Wrote {len(rows)} races; A missing in {sum(not x["a_rank1"] for x in rows)} races; file {path}',flush=True)
if __name__=='__main__':main()
