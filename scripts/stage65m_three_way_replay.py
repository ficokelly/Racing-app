#!/usr/bin/env python3
"""Research-only three-way Nov 5/6 comparison. No production writes.
A: recorded pre-off Model D, B: frozen D with verified backfill,
C: frozen D with a strictly historical, learned race-category adjustment.
"""
import argparse,collections,csv,datetime as dt,json,math,pathlib,shutil,sqlite3,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage65k_two_day_replay as K
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast,live_row,prediction_rows,name,clock,time_utc,nonrunner

def cat(s):
 s=str(s or '').lower()
 if 'handicap' in s:return 'handicap'
 if 'maiden' in s:return 'maiden'
 if 'novice' in s:return 'novice'
 if 'bumper' in s or 'national hunt flat' in s:return 'bumper'
 if 'seller' in s or 'selling' in s:return 'seller'
 if 'claimer' in s or 'claiming' in s:return 'claimer'
 if 'conditions' in s:return 'conditions'
 if 'listed' in s:return 'listed'
 if 'group ' in s or 'grade ' in s:return 'group_or_graded'
 return 'other'

def learned(cx,day):
 """Train only on dated past races. Hierarchical shrinkage to avoid tiny-sample effects."""
 counts=collections.defaultdict(lambda:[0,0]);base=collections.defaultdict(lambda:[0,0])
 sql='''SELECT r.race_type,u.trainer,u.horse_name_norm,u.finish_pos
 FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
 WHERE r.race_date < ? AND u.finish_pos IS NOT NULL'''
 for typ,tr,horse,pos in cx.execute(sql,(day,)):
  category=cat(typ);tr=name(tr);horse=name(horse)
  win=int(pos==1)
  base[category][0]+=win;base[category][1]+=1
  if tr:counts[('trainer',tr,category)][0]+=win;counts[('trainer',tr,category)][1]+=1
  if horse:counts[('horse',horse,category)][0]+=win;counts[('horse',horse,category)][1]+=1
 return counts,base

def adjustment(r,counts,base):
 category=cat(r.get('race_name') or r.get('race_type') or r.get('type'))
 bw,bn=base[category];p=(bw+1)/(bn+8) if bn else .125
 score=0.
 for kind,key,weight in [('horse',name(r.get('horse')),0.65),('trainer',name(r.get('trainer')),0.35)]:
  w,n=counts.get((kind,key,category),(0,0))
  if n>=5:
   pp=(w+24*p)/(n+24)
   score+=weight*max(-1,min(1,math.log(max(.001,pp)/max(.001,p))))
 return score,category

def load_cards(perf):
 history=K.snapshots();cache={};out=[]
 for key,r in sorted(perf.items()):
  day,course,tm=key;off=time_utc(r.get('race_time'))
  e={'date':day,'course':r.get('course'),'time':tm,'status':'','race_type':'','race_name':'','actual_rank1':'','actual_rank2':'','updated_rank1':'','updated_rank2':'','race_detail_rank1':'','race_detail_rank2':''}
  original=sorted([x for x in r.get('runners',[]) if isinstance(x.get('rank'),int)],key=lambda x:x['rank'])
  if len(original)<2 or not off or not r.get('pre_off_valid'):
   e['status']='NO_VERIFIED_PREOFF_BASELINE';out.append(e);continue
  e['actual_rank1'],e['actual_rank2']=original[0]['horse'],original[1]['horse']
  valid=[(t,s) for t,s in history if t<int(off.timestamp())]
  if not valid:e['status']='NO_PREOFF_SNAPSHOT';out.append(e);continue
  sha=valid[-1][1]
  if sha not in cache:cache[sha]=K.card_at(sha)
  runners=[z for z in cache[sha].get('runners',[]) if K.key_of(z)==key and not nonrunner(z)]
  if len(runners)<2 or len({name(z['horse']) for z in runners})!=len(runners):e['status']='INCOMPLETE_PREOFF_CARD';out.append(e);continue
  expected={name(z['horse']) for z in r.get('runners',[]) if z.get('horse') and str(z.get('status','')).lower() not in ('non-runner','nr')}
  if {name(z['horse']) for z in runners}!=expected:e['status']='FIELD_MISMATCH';out.append(e);continue
  e['race_name']=runners[0].get('race_name') or '';e['race_type']=runners[0].get('race_type') or runners[0].get('type') or ''
  e['race_category']=cat(e['race_name'] or e['race_type']);e['runners']=runners;e['status']='READY';out.append(e)
 return out

def main():
 p=argparse.ArgumentParser();p.add_argument('--baseline',required=True);p.add_argument('--updated',required=True);p.add_argument('--out',required=True);a=p.parse_args()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 cfg=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
 assert cfg['chosen_combination']=='D-064-gap14' and abs(float(cfg['selection_margin'])-.14)<1e-9
 weights=[float(cfg['weights_pct'][x])/100 for x in OLD.METRICS]
 perf=K.performance();entries=load_cards(perf)
 cx=sqlite3.connect('file:'+str(pathlib.Path(a.updated).resolve())+'?mode=ro',uri=True)
 for day in K.DAYS:
  dayrows=[e for e in entries if e['date']==day and e['status']=='READY']
  if not dayrows:continue
  past=LivePast({});info=past.scan_until(a.updated,day)
  unique={}
  for h,c in past.last:
   if h not in unique:unique[h]=c
   elif unique[h]!=c:unique[h]=None
  counts,base=learned(cx,day)
  for e in dayrows:
   runners=e['runners'];rows=[live_row(z,day,'STAGE65M|'+day+'|'+e['course']+'|'+e['time'],unique) for z in runners]
   race=past.score(rows,live=True)
   if race is None:e['status']='UNSCORABLE';continue
   scores,order,gap=prediction_rows(race,weights)
   e['updated_rank1']=runners[int(order[0])]['horse'];e['updated_rank2']=runners[int(order[1])]['horse']
   # Keep the eight frozen model weights. A bounded category interaction is
   # an explicitly separate experimental feature, NOT claimed to be frozen D.
   # Adjustment strength is fixed before evaluation, never fitted to Nov 5/6.
   adj=[adjustment(z,counts,base)[0] for z in runners]
   changed=[float(s)+.10*x for s,x in zip(scores,adj)]
   order2=sorted(range(len(changed)),key=lambda i:(-changed[i],i))
   e['race_detail_rank1']=runners[order2[0]]['horse'];e['race_detail_rank2']=runners[order2[1]]['horse']
   e['last_historical_result']=info['last_result_date']
   e['status']='COMPARED'
 cx.close()
 for e in entries:
  if e['status']!='COMPARED':continue
  actual=perf[(e['date'],name(e['course']),e['time'])]
  settlement={name(z['horse']):z for z in actual.get('runners',[])}
  for v in ('actual','updated','race_detail'):
   for rank in (1,2):
    horse=e[v+'_rank'+str(rank)];s=settlement.get(name(horse))
    if not s:e['status']='MISSING_SETTLEMENT';break
    won=int(str(s.get('status','')).lower()=='won' or s.get('position')==1)
    sp=s.get('sp_decimal')
    if won and not isinstance(sp,(int,float)):e['status']='WINNER_SP_MISSING';break
    e[v+'_rank'+str(rank)+'_win']=won;e[v+'_rank'+str(rank)+'_return']=float(sp) if won else 0.
  e.pop('runners',None)
 columns=['date','course','time','status','race_name','race_type','race_category','last_historical_result']
 for v in ('actual','updated','race_detail'):
  for rank in (1,2):columns += [v+'_rank'+str(rank),v+'_rank'+str(rank)+'_win',v+'_rank'+str(rank)+'_return']
 with (out/'race_by_race.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(entries)
 stats=[]
 valid=[e for e in entries if e['status']=='COMPARED']
 groups={'Combined':valid,'2026-11-05':[e for e in valid if e['date']=='2026-11-05'],'2026-11-06':[e for e in valid if e['date']=='2026-11-06']}
 for category in sorted({e.get('race_category') for e in valid}):groups['Type: '+category]=[e for e in valid if e.get('race_category')==category]
 for course in sorted({e['course'] for e in valid}):groups['Track: '+course]=[e for e in valid if e['course']==course]
 for group,rows in groups.items():
  for v in ('actual','updated','race_detail'):
   for rank in (1,2,'combined'):
    ranks=(1,2) if rank=='combined' else (rank,)
    bets=len(rows)*len(ranks);wins=sum(e[v+'_rank'+str(k)+'_win'] for e in rows for k in ranks);gross=sum(e[v+'_rank'+str(k)+'_return'] for e in rows for k in ranks)
    stats.append({'group':group,'version':v,'rank':rank,'races':len(rows),'bets':bets,'wins':wins,'strike_rate_pct':round(100*wins/bets,2) if bets else '', 'sp_roi_pct':round(100*(gross/bets-1),2) if bets else ''})
 with (out/'comparison_summary.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(stats[0]));w.writeheader();w.writerows(stats)
 report={'research_only':True,'production_unchanged':True,'baseline_is_recorded_live_preoff_rankings':True,'updated_is_frozen_D':True,'race_detail_is_experimental_adjustment':True,'race_detail_formula':'frozen score + 0.10 * shrunk horse/trainer category log win-rate lift; min 5 historical starts','no_same_day_or_future_training':True,'statuses':dict(collections.Counter(e['status'] for e in entries)),'compared':len(valid),'limitations':['No exact named-event matching: category is derived from pre-off race name/type; recurring named-event IDs require verified mapping','Only verified pre-off cards and SP settlements are included','Historical source must be audited; reconstructed baseline is not necessarily live production DB']}
 (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
