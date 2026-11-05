#!/usr/bin/env python3
"""Stage61: research-only, date-safe recurring-event and condition trend reranking of ALL runners.
Frozen D scores are obtained from the existing Stage49/Stage38 engine, unchanged.
2025 development; 2026 temporal evaluation (not a virgin holdout).
"""
import argparse,csv,json,math,re,sqlite3,collections,datetime,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import stage49_model_d_top_two as S49
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast,prediction_rows

def number(v):
 try:
  f=float(v);return f if math.isfinite(f) else None
 except (TypeError,ValueError):return None

def classify(t, surface, klass, distance):
 s=' '.join(str(x or '') for x in (t,surface,klass)).lower()
 if re.search(r'\b(chase|steeple)\b',s):discipline='chase'
 elif re.search(r'\b(hurdle|hurdles)\b',s):discipline='hurdle'
 elif re.search(r'\b(nh flat|bumper)\b',s):discipline='nh_flat'
 elif 'all weather' in s or re.search(r'\b(aw|polytrack|tapeta|fibresand|synthetic)\b',s):discipline='aw'
 else:discipline='flat'
 if re.search(r'\b(novice|novices|nov)\b',s):rtype='novice'
 elif re.search(r'\b(maiden)\b',s):rtype='maiden'
 elif re.search(r'\b(handicap|hcap|hcp)\b',s):rtype='handicap'
 elif re.search(r'\b(selling|seller)\b',s):rtype='seller'
 elif re.search(r'\b(claiming|claimer)\b',s):rtype='claimer'
 elif re.search(r'\b(group|grade|graded|listed)\b',s):rtype='stakes'
 else:rtype='unknown'
 m=re.search(r'\bclass\s*([1-7])\b',s)
 cl='class'+m.group(1) if m else 'class_unknown'
 d=number(distance);db='dist_unknown' if d is None else 'dist'+str(round(d/2)*2)
 return (discipline,rtype,cl,db)

def load_mapping(path):
 mapping={};conflicts=set();allowed={'ANCHORED_CONTINUITY_CANDIDATE','ANCHORED_WITH_GAPS','TITLE_CONTINUITY_CANDIDATE'}
 with open(path,encoding='utf-8-sig') as f:
  for row in csv.DictReader(f):
   if row.get('status') not in allowed:continue
   rid=row.get('stage2_race_id','');event=row.get('recurring_event_id','')
   if not rid or not event:continue
   if rid in mapping and mapping[rid]!=event:conflicts.add(rid)
   else:mapping[rid]=event
 for rid in conflicts:mapping.pop(rid,None)
 return mapping,len(conflicts)

def bucket(v,metric):
 if v is None:return None
 if metric=='age':return int(v)//2*2
 if metric=='or':return int(v)//10*10
 if metric=='weight':return int(v)//7*7
 if metric=='draw':return int(v)//4*4
 return None

def trend_score(history, candidates, min_editions=5):
 # historical outcomes only; pooled winner vs starter distribution, with smoothing/shrinkage
 if len(history)<min_editions:return [0.]*len(candidates),0
 scores=[0.]*len(candidates);used=0
 for metric in ('age','or','weight','draw'):
  w=[bucket(h['winner'][metric],metric) for h in history if h['winner'][metric] is not None]
  a=[bucket(z[metric],metric) for h in history for z in h['runners'] if z[metric] is not None]
  if len(w)<5 or len(a)<30:continue
  wc=collections.Counter(w);ac=collections.Counter(a);nb=max(2,len(ac))
  for i,c in enumerate(candidates):
   b=bucket(c[metric],metric)
   if b is None:continue
   win=(wc[b]+1)/(len(w)+nb)
   start=(ac[b]+1)/(len(a)+nb)
   shrink=len(w)/(len(w)+30)
   scores[i]+=max(-1,min(1,math.log(win/start)*shrink))
  used+=1
 return [s/max(1,used) for s in scores],used

def stats(rows,key):
 rs=[r for r in rows if r.get(key+'_sp') is not None]
 wins=sum(r[key+'_won'] for r in rows)
 gross=sum(r[key+'_sp'] for r in rs if r[key+'_won'])
 return {'races':len(rows),'priced_bets':len(rs),'winners':wins,'strike_rate_pct':round(100*wins/len(rows),2) if rows else None,'returned_units':round(gross,2),'profit_units':round(gross-len(rs),2),'sp_roi_pct':round(100*(gross/len(rs)-1),2) if rs else None}

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--mapping',required=True);p.add_argument('--out',required=True);p.add_argument('--min-editions',type=int,default=5);a=p.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 config=json.loads(Path('reports/stage30/stage30-summary.json').read_text());model=config['model_results']['D']
 assert model['chosen_combination']=='D-064-gap14' and abs(float(model['selection_margin'])-.14)<1e-9
 weights=[float(model['weights_pct'][k])/100 for k in OLD.METRICS]
 mapping,conflicts=load_mapping(a.mapping)
 cx=sqlite3.connect('file:'+str(Path(a.db).resolve())+'?mode=ro',uri=True)
 cx.execute('PRAGMA temp_store=MEMORY')
 sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
 u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
 u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment,u.weight_lbs
 FROM historical_races r JOIN historical_runs u ON u.race_id=r.id WHERE r.race_date <= '2026-11-03'
 ORDER BY r.race_date,r.id'''
 past=LivePast({});events=collections.defaultdict(list);conditions=collections.defaultdict(list)
 pending=[];day=None;group=[];lastid=None;scanned=0;target=0;skipped=collections.Counter();output=[]
 # Stage49/38 scorer expects 23 columns. Weight column is used for trend only.
 def process(g):
  nonlocal target
  if not g:return
  rid,date,course,typ,surface,dist,going,klass=g[0][:8]
  if OLD.track(course) not in OLD.TRACKS:return
  starters=[r for r in g if OLD.started(r[:23])]
  winners=[i for i,r in enumerate(starters) if r[12]==1]
  if len(starters)<2 or len(winners)!=1:
   skipped['incomplete_result']+=1;pending.append([r[:23] for r in g]);return
  key=classify(typ,surface,klass,dist)
  event=mapping.get(rid)
  # Race type is ALWAYS part of the match; never mix handicap and novice hurdle.
  eventkey=(event,key[:3]) if event else None
  condkey=(OLD.track(course),key)
  e_hist=events[eventkey] if eventkey else []
  c_hist=conditions[condkey]
  features=[{'age':number(r[17]),'or':number(r[16]),'draw':number(r[21]),'weight':number(r[23])} for r in starters]
  eh=e_hist if len(e_hist)>=a.min_editions else []
  ch=c_hist if len(c_hist)>=a.min_editions else []
  # Stronger same-event evidence when available, else truly comparable conditions.
  source='same_event' if eh else 'same_conditions' if ch else 'insufficient_history'
  hist=eh or ch
  trend,metrics=trend_score(hist,features,a.min_editions)
  if date>='2025-01-01':
   r=past.score([x[:23] for x in g],live=True)
   if r is not None:
    dscore,order,gap=prediction_rows(r,weights)
    # Align trend features with the scorer's starter ordering, as used by Stage49.
    if len(dscore)!=len(starters):
     skipped['runner_alignment']+=1
    else:
     # Normalize trend to score scale without using results, SP or future races.
     scale=max(0.01,float(np.std(dscore)))
     dtop=int(order[0]);dsecond=int(order[1]);win=winners[0]
     row={'date':date,'race_id':rid,'course':course,'discipline':r['kind'],
          'race_type':key[1],'race_class':key[2],'distance_bucket':key[3],
          'source':source,'prior_editions':len(hist),'trend_metrics':metrics,
          'field_size':len(starters),'winner':starters[win][9],
          'd_rank1':starters[dtop][9],'d_rank2':starters[dsecond][9],
          'd_rank1_won':int(dtop==win),'d_rank1_sp':number(starters[dtop][20]),
          'd_rank2_won':int(dsecond==win),'d_rank2_sp':number(starters[dsecond][20]),
          'd_top2_covered':int(win in (dtop,dsecond))}
     for alpha in (0.05,0.1,0.2):
      # Same runner order as scorer; fixed, pre-declared strength grid.
      combined=np.asarray(dscore)+alpha*scale*np.asarray(trend)
      ranked=np.argsort(-combined,kind='stable');top=int(ranked[0]);second=int(ranked[1]);k='a'+str(alpha).replace('.','')
      row[k+'_rank1']=starters[top][9];row[k+'_rank2']=starters[second][9]
      row[k+'_rank1_won']=int(top==win);row[k+'_rank1_sp']=number(starters[top][20]);row[k+'_top2_covered']=int(win in (top,second))
     output.append(row);target+=1
  # Append only after predictions; same-day histories are not made visible until next date.
  record={'date':date,'winner':features[winners[0]],'runners':features}
  pending.append([x[:23] for x in g])
  pending_trends.append((eventkey,condkey,record))
 pending_trends=[]
 def flush_date():
  for group0 in pending:past.update(group0)
  for ek,ck,rec in pending_trends:
   if ek:events[ek].append(rec)
   conditions[ck].append(rec)
  pending.clear();pending_trends.clear()
 for r in cx.execute(sql):
  d=r[1]
  if day is not None and d!=day:
   process(group);group=[];flush_date()
  if group and r[0]!=lastid:process(group);group=[]
  group.append(r);day=d;lastid=r[0];scanned+=1
  if scanned%1000000==0:print('Runner rows',scanned,'evaluated',target,flush=True)
 if group:process(group)
 cx.close()
 if not output:raise RuntimeError('No eligible evaluated races; check source and joins')
 dev=[x for x in output if x['date'][:4]=='2025'];test=[x for x in output if x['date'][:4]=='2026']
 if not dev or not test:raise RuntimeError('2025 development or 2026 temporal test is missing')
 choices=['a005','a01','a02']
 # Select alpha using ONLY 2025, by strike rate; tie break on lower strength.
 selected=max(choices,key=lambda k:(sum(x[k+'_rank1_won'] for x in dev),-choices.index(k)))
 def describe(rs):
  d={'races':len(rs),'frozen_d':stats(rs,'d_rank1'),'frozen_d_rank2':stats(rs,'d_rank2'),
     'frozen_d_top2_coverage_pct':round(100*sum(x['d_top2_covered'] for x in rs)/len(rs),2) if rs else None}
  for k in choices:
   d[k]={'rank1':stats(rs,k+'_rank1'),'top2_coverage_pct':round(100*sum(x[k+'_top2_covered'] for x in rs)/len(rs),2) if rs else None}
  return d
 report={'status':'RESEARCH_ONLY','live_model_d_modified':False,'source_rows_scanned':scanned,'mapped_race_ids':len(mapping),'mapping_conflicts':conflicts,
         'method':'All runners scored by unchanged frozen Model D, then historical age/OR/weight/draw winner-vs-starter lifts. Exact recurring event only when candidate mapping exists and discipline/race type/class agree; otherwise same course/discipline/type/class/distance bucket. Prior dates only.',
         'development_2025':describe(dev),'temporal_test_2026':describe(test),'selected_alpha_2025_only':selected,
         'selected_alpha_2026_result':stats(test,selected+'_rank1'),
         'by_2026_discipline':{k:describe([x for x in test if x['discipline']==k]) for k in sorted({x['discipline'] for x in test})},
         'by_2026_race_type':{k:describe([x for x in test if x['race_type']==k]) for k in sorted({x['race_type'] for x in test})},
         'by_2026_source':{k:describe([x for x in test if x['source']==k]) for k in sorted({x['source'] for x in test})},
         'excluded':dict(skipped),
         'limitations':['2026 is a temporal test but not an untouched holdout: earlier model development examined these years.','Race event mapping remains candidate/unverified; same-conditions matches are not proof of same annual race.','Age/OR/weight/draw are first four historical trend signals; broader 8-pillar form assessment comes from frozen D.','The source has no guaranteed race-name, race-status, going-history or market movement coverage.','Race type can be unknown when source metadata lacks novice/handicap information; unknown is never assumed to be handicap.','No future results or starting prices used as predictive features; SP used for settlement only.','Retrospective actual starters may differ from declarations.','No live application or weights modified.']}
 with (out/'stage61_per_race.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=list(output[0]));w.writeheader();w.writerows(output)
 (out/'stage61_summary.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'races':len(output),'selected_alpha':selected,'2026_d':report['temporal_test_2026']['frozen_d'],'2026_trends':report['selected_alpha_2026_result']},indent=2))
if __name__=='__main__':main()
