#!/usr/bin/env python3
"""Stage92: research-only Model E v2 win-probability combinations.

Pre-off features only; no bookmaker odds, SP, ROI, or prices enter the model.
Each declared runner has a probability; all race probabilities total 100%.
HPR absence changes only evidence coverage, not the scoring baseline or eligibility.
Model combination and temperature chosen on earlier dates, evaluated on later dates.
Outputs are research reports; never writes frozen D, D(c), live app or HPR store.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,json,math,pathlib,re,sqlite3,statistics,sys
from zoneinfo import ZoneInfo

PILLARS=('ability','trainer','jockey','course','draw','distance','ground','jumping')
D_PRIOR=dict(zip(PILLARS,(13.93,8.96,5.60,17.77,12.25,11.17,25.03,5.28)))
PRESETS={
 'balanced': dict(zip(PILLARS,(30,12,9,12,9,12,10,6))),
 'ability_heavy':dict(zip(PILLARS,(45,9,7,10,7,10,8,4))),
 'conditions_heavy':dict(zip(PILLARS,(25,10,7,15,10,15,13,5))),
 'people_heavy':dict(zip(PILLARS,(26,21,15,9,7,10,8,4))),
 'course_distance':dict(zip(PILLARS,(30,9,7,18,6,18,9,3))),
 'ground_jump':dict(zip(PILLARS,(28,8,6,9,5,10,17,17))),
 'frozen_d_prior':D_PRIOR,
}
STOP={'nr','nonrunner','non-runner','scratched','withdrawn','dns','did not start'}
OUT_FIELDS=['date','course','time','discipline','race_type','race_name','horse','rank',
 'win_probability_pct','has_hpr','hpr_prior_count','evidence_count','model','probability_sum_pct',
 'is_holdout','winner_verified']

def norm(x):
 import unicodedata
 s=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().upper()
 s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$','',s)
 return re.sub('[^A-Z0-9]','',s)
def num(x):
 try:
  v=float(x);return v if math.isfinite(v) else None
 except (TypeError,ValueError):return None
def clamp(v,lo=0.,hi=100.):return max(lo,min(hi,v))
def rdate(x):return str(x or '')[:10]
def course(x):return norm(re.sub(r'\s*\((?:AW|IRE|GB)\)','',str(x or ''),flags=re.I))
def going_band(x):
 x=str(x or '').lower()
 if 'heavy' in x:return 'heavy'
 if 'soft' in x or 'yield' in x:return 'soft'
 if 'firm' in x:return 'firm'
 if 'standard' in x or 'tapeta' in x or 'polytrack' in x:return 'standard'
 if 'good' in x:return 'good'
 return ''
def tripband(x):
 z=num(x)
 return round(z/2)*2 if z is not None and z>0 else None

def discipline(x):
 t=str(x.get('type') or x.get('race_type') or '').lower()
 if any(s in t for s in ('hurdle','chase','steeple','nh flat','national hunt flat','bumper','point-to-point')):return 'Jumps'
 s=str(x.get('surface') or '').lower();c=str(x.get('course') or '').lower()
 if 'aw' in s or 'standard' in s or 'polytrack' in s or 'tapeta' in s or '(aw)' in c:return 'AW'
 if s=='turf':return 'Flat'
 return 'Unknown'

def race_type(x):
 s=str(x.get('race_name') or '').lower();t=str(x.get('type') or '').lower()
 jump=('hurdle' in t or 'chase' in t or 'steeple' in t or 'nh flat' in t)
 if 'nh flat' in t or 'national hunt flat' in s or 'bumper' in s or 'point-to-point flat' in s:return 'NH Flat/Bumper'
 if 'hunter' in s and ('chase' in s or 'steeple' in s):return 'Hunter Chase'
 if 'listed' in s:return 'Listed'
 if re.search(r'\b(?:grade|group)\s*(?:[123]|i{1,3})\b',s):return 'Graded/Group'
 if 'handicap' in s or 'nursery' in s:
  if 'novice' in s:return 'Novice Handicap Chase' if 'chase' in t else 'Novice Handicap Hurdle' if 'hurdle' in t else 'Novice Handicap'
  if 'chase' in t:return 'Handicap Chase'
  if 'hurdle' in t:return 'Handicap Hurdle'
  return 'Handicap'
 if 'maiden' in s:return 'Maiden Hurdle' if 'hurdle' in t else 'Maiden'
 if 'beginner' in s:return 'Beginners Chase' if 'chase' in t else 'Beginners'
 if 'novice' in s:return 'Novice Chase' if 'chase' in t else 'Novice Hurdle' if 'hurdle' in t else 'Novice'
 if 'claim' in s:return 'Claiming'
 if 'sell' in s:return 'Selling'
 return 'Other/Unknown' if s else 'Unknown Race Type'

def form_signals(s):
 """Unrated first-timers have no form signal (not a zero score)."""
 s=str(s or '').upper();marks=[]
 for ch in s:
  if ch in '1234567890':marks.append(10 if ch=='0' else int(ch))
  elif ch in 'PUFRB':marks.append(12)
 if not marks:return None,None
 vals=[clamp(100-9*(p-1),10,100) for p in marks[-4:]]
 ww=[.54,.28,.12,.06][:len(vals)]
 recent=sum(x*w for x,w in zip(reversed(vals),ww))/sum(ww)
 trend=50 if len(vals)<2 else clamp(50+1.2*(vals[-1]-vals[0]))
 return recent,trend

def weighted_available(parts):
 """Missing observations are omitted, never scored 0 or given a penalty."""
 a=[(float(score),weight) for score,weight in parts if score is not None]
 return sum(score*weight for score,weight in a)/sum(weight for _,weight in a) if a else 50.

def probability(scores,temperature):
 assert temperature>0
 center=max(scores);ex=[math.exp((s-center)/temperature) for s in scores]
 total=sum(ex);res=[v/total for v in ex]
 assert abs(sum(res)-1.)<1e-9
 return res

def weights(name,disc):
 w=dict(PRESETS[name]);w['jumping']=0 if disc in ('Flat','AW') else w['jumping']
 w['draw']=0 if disc=='Jumps' else w['draw']
 if disc=='AW':w['ground']*=.25
 if disc=='Jumps':w['ground']*=1.4
 z=sum(w.values());return {k:v/z for k,v in w.items()}

def race_scores(race,variant):
 w=weights(variant,race['discipline'])
 # NH Flat/bumper is National Hunt racing, but horses do not jump obstacles.
 if race['race_type']=='NH Flat/Bumper':
  w['jumping']=0.;total=sum(w.values());w={k:v/total for k,v in w.items()}
 return [sum(x['pillars'][k]*w[k] for k in PILLARS) for x in race['runners']]

def posterior_person(starts,wins):
 # A shrinkage estimate toward a neutral 10% win rate, not a zero for missing data.
 if not starts:return 50.
 post=(wins+1.5)/(starts+15.)
 return clamp(50+175*(post-.1)*starts/(starts+20),20,80)

def historical_population(db,start):
 """Full historical runner observations, not selected-horse-only training samples."""
 con=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 rows=[];by_race=collections.defaultdict(list)
 sql='''SELECT r.id,r.race_date,r.course,r.surface,r.distance_f,
          u.trainer,u.jockey,u.finish_pos,u.draw
        FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
        WHERE r.race_date>=? AND r.race_date<? ORDER BY r.id'''
 for record in con.execute(sql,(start,'2026-10-08')):
  rid,date,track,surface,dist,trainer,jockey,pos,draw=record
  if not date or not track:continue
  by_race[str(rid)].append((date,course(track),str(surface or ''),tripband(dist),norm(trainer),norm(jockey),num(pos),num(draw)))
 con.close()
 draw_hist=collections.Counter()
 for rid,people in by_race.items():
  n=len(people)
  known=sorted((float(r[7]),i) for i,r in enumerate(people) if r[7] and r[7]>0)
  bins={i:min(2,int(j*3/len(known))) for j,(_,i) in enumerate(known)} if len(known)>=5 else {}
  for i,(date,track,surf,trip,tr,jk,pos,draw) in enumerate(people):
   if pos is None:continue
   rows.append((date,tr,jk,pos==1))
   if i in bins:
    key=(track,'AW' if any(x in surf.lower() for x in ('aw','standard','tapeta','polytrack')) else 'Flat',trip,bins[i]);draw_hist[key+(int(pos==1),)]+=1
 return rows,draw_hist

def aggregate_people(hist,dates):
 # Historical source ends 7 Oct, later observations are read-only verified results.
 bytrainer=collections.defaultdict(list);byjockey=collections.defaultdict(list)
 for date,tr,jk,won in hist:
  if tr:bytrainer[tr].append((date,won))
  if jk:byjockey[jk].append((date,won))
 for v in (bytrainer,byjockey):
  for vals in v.values():vals.sort()
 return bytrainer,byjockey

def period_stats(index,name,before):
 if not name:return (0,0)
 earliest=(dt.date.fromisoformat(before)-dt.timedelta(days=365)).isoformat()
 # Indexed in date order. We include ONLY days strictly before the race date.
 values=index.get(norm(name),())
 recent=[v for date,v in values if earliest<=date<before]
 return len(recent),sum(recent)

def previous_ratings(conn,horse_key,before):
 rows=conn.execute('''SELECT r.payload FROM hpr_ratings r JOIN (
  SELECT horse_key,race_key,model_version,MAX(revision) mx FROM hpr_ratings
  WHERE horse_key=? AND race_date<? GROUP BY horse_key,race_key,model_version) z
  ON r.horse_key=z.horse_key AND r.race_key=z.race_key AND r.model_version=z.model_version AND r.revision=z.mx
  ORDER BY r.race_date,r.race_key''',(horse_key,before)).fetchall()
 return [json.loads(z[0]) for z in rows]

def contextual(prior,pred,target,expected):
 if not prior or target in ('',None):return 50.
 matching=[num(z.get('raw_performance')) for z in prior if pred(z)==target]
 matching=[z for z in matching if z is not None]
 allv=[num(z.get('raw_performance')) for z in prior[-10:]];allv=[z for z in allv if z is not None]
 if not matching or not allv:return 50.
 deviation=(sum(matching[-10:])/len(matching[-10:])-sum(allv)/len(allv))
 return clamp(50+deviation*len(matching)/(len(matching)+3),20,80)

def condition_pillars(card,prior,field,trainer_index,jockey_index,draw_stats):
 or_vals=[num(z.get('ofr') or z.get('or') or z.get('official_rating')) for z in field]
 or_vals=[o for o in or_vals if o is not None and o>0]
 own_or=num(card.get('ofr') or card.get('or') or card.get('official_rating'))
 or_score=clamp(50+1.5*(own_or-statistics.median(or_vals)),15,85) if own_or and or_vals else None
 form,trend=form_signals(card.get('form'))
 hpr_vals=[num(z.get('hpr')) for z in prior[-3:]]
 hpr_vals=[v for v in hpr_vals if v is not None]
 raw_vals=[num(z.get('raw_performance')) for z in prior[-3:]]
 raw_vals=[v for v in raw_vals if v is not None]
 hpr=None
 if hpr_vals:
  ww=[.55,.30,.15][:len(hpr_vals)]
  hpr=sum(v*w for v,w in zip(reversed(hpr_vals),ww))/sum(ww)
 raw=sum(raw_vals)/len(raw_vals) if raw_vals else None
 ability=weighted_available(((hpr,.35),(raw,.15),(or_score,.25),(form,.20),(trend,.05)))
 cutoff=rdate(card.get('date') or card.get('race_time'))
 ns,ws=period_stats(trainer_index,card.get('trainer'),cutoff)
 nj,wj=period_stats(jockey_index,card.get('jockey'),cutoff)
 track=course(card.get('course'))
 trip=tripband(card.get('distance_f'))
 ground=going_band(card.get('going'))
 ty=str(card.get('type') or '').lower()
 kind='chase' if 'chase' in ty else 'hurdle' if 'hurdle' in ty else 'nhflat' if 'flat' in ty and 'nh' in ty else 'flat'
 cp=contextual(prior,lambda z:course(z.get('course')),track,50)
 dp=contextual(prior,lambda z:tripband(z.get('distance_f')),trip,50)
 gp=contextual(prior,lambda z:going_band(z.get('going')),ground,50)
 jump=50.
 if kind in ('hurdle','chase','nhflat'):
  relevant=[z for z in prior if ('chase' if 'chase' in str(z.get('race_type') or '').lower() else 'hurdle' if 'hurdle' in str(z.get('race_type') or '').lower() else 'nhflat')==kind]
  if relevant:
   completed=sum(num(z.get('position')) is not None for z in relevant)
   jump=clamp(50+55*((completed+3)/(len(relevant)+4)-.75)*len(relevant)/(len(relevant)+3),20,80)
 draw=50.
 if kind=='flat' and trip is not None:
  active=sorted((num(z.get('draw')),norm(z.get('horse'))) for z in field if num(z.get('draw')) and num(z.get('draw'))>0)
  if len(active)>=5:
   for idx,(_,name) in enumerate(active):
    if name!=norm(card.get('horse')):continue
    band=min(2,int(idx*3/len(active)))
    k=(track,'AW' if discipline(card)=='AW' else 'Flat',trip,band)
    starts=draw_stats[k+(0,)]+draw_stats[k+(1,)]
    if starts>=80:
     winrate=draw_stats[k+(1,)]/starts
     # An evidence-based draw contribution, heavily shrunk toward neutral.
     draw=clamp(50+clamp((winrate-.12)*130,-12,12)*starts/(starts+150),35,65)
    break
 return {'pillars':dict(ability=ability,trainer=posterior_person(ns,ws),jockey=posterior_person(nj,wj),
                     course=cp,draw=draw,distance=dp,ground=gp,jumping=jump),
         'has_hpr':bool(hpr_vals),'hpr_prior_count':len(prior),
         'evidence_count':sum(x is not None for x in (hpr,raw,or_score,form,trend))+int(bool(ns))+int(bool(nj))+int(bool(prior))}

def recent_population(results,dated):
 bydate=collections.defaultdict(list);cards={}
 for rows in dated.values():
  for z in rows:cards.setdefault((rdate(z.get('date') or z.get('race_time')),course(z.get('course')),str(z.get('race_time') or '')[11:16],norm(z.get('horse'))),z)
 import stage86_hpr_engine as hpr
 for r in results.values():
  d=rdate(r.get('date') or r.get('off_dt'));t=hpr.off(r.get('off_dt') or r.get('off'))
  for z in r['runners']:
   if num(z.get('position')) is None:continue
   c=cards.get((d,course(r.get('course')),t,norm(z.get('horse'))),{})
   tr=norm(c.get('trainer') or z.get('trainer'));jk=norm(c.get('jockey') or z.get('jockey'))
   bydate[d].append((d,tr,jk,num(z.get('position'))==1))
 return bydate

def fetch_data(root,source_db,hpr_db,asof,days):
 import stage86_hpr_engine as hpr
 start=asof-dt.timedelta(days=days);end=asof-dt.timedelta(days=1)
 earliest=max(start-dt.timedelta(days=90),dt.date(2026,10,8))
 results,bad=hpr.load_results(root,earliest,end)
 snapshots=hpr.card_dates(root,earliest,end,git_preoff=True)
 population,draw_hist=historical_population(source_db,(start-dt.timedelta(days=365)).isoformat())
 recent=recent_population(results,snapshots)
 for d in sorted(recent):population.extend(recent[d])
 tr_index,jk_index=aggregate_people(population,None)
 grouped=collections.defaultdict(list)
 for day,arr in snapshots.items():
  for card in arr:grouped[hpr.race_key(card)].append(card)
 hr=sqlite3.connect('file:'+str(pathlib.Path(hpr_db).resolve())+'?mode=ro',uri=True)
 data=[];reject=collections.Counter();cache={}
 for race_key,r in sorted(results.items()):
  d=hpr.day(r.get('date') or r.get('off_dt'))
  if d is None or d<start or d>end:continue
  rows=[z for z in grouped.get(race_key,[]) if str(z.get('race_status') or '').lower() not in STOP]
  if len(rows)<2:
   reject['NO_PREOFF_FIELD']+=1;continue
  names=[norm(z.get('horse')) for z in rows]
  if len(names)!=len(set(names)):
   reject['DUPLICATE_PREOFF_RUNNER']+=1;continue
  settled={norm(z.get('horse')):z for z in r['runners']}
  if len(settled)<2 or not set(settled)<=set(names):
   reject['UNVERIFIED_RESULT_FIELD']+=1;continue
  # Evaluate probabilities over independently verified actual starters; a
  # morning card may include a subsequently scratched horse. As in Stage86,
  # never treat non-starters as losers or reduce starters' probabilities.
  # The surviving starters' features still come solely from the pre-off card.
  rows=[z for z in rows if norm(z.get('horse')) in settled]
  winner=[norm(z.get('horse')) for z in r['runners'] if num(z.get('position'))==1]
  if len(winner)!=1:
   reject['UNVERIFIED_WINNER']+=1;continue
  disp=discipline(rows[0]);typ=race_type(rows[0]);
  if disp=='Unknown':reject['UNKNOWN_DISCIPLINE']+=1;continue
  rc={'date':str(d),'course':r.get('course'),'time':hpr.off(r.get('off_dt') or r.get('off')),
      'discipline':disp,'race_type':typ,'race_name':rows[0].get('race_name') or '',
      'winner':winner[0],'runners':[],'snapshot_field':len(rows),'verified_starters':len(settled)}
  for row in rows:
   key=hpr.identity(row)
   if key:
    ck=(key,str(d))
    if ck not in cache:cache[ck]=previous_ratings(hr,key,str(d))
    prior=cache[ck]
   else:prior=[]
   item=condition_pillars(row,prior,rows,tr_index,jk_index,draw_hist)
   item.update(horse=row.get('horse') or '',horse_key=key,won=norm(row.get('horse'))==winner[0])
   rc['runners'].append(item)
  data.append(rc)
 hr.close()
 return data,dict(reject),len(population),bad

def group_key(r,by):return r['discipline'] if by=='discipline' else r['discipline']+' / '+r['race_type']
def outcomes(races,variant,temp):
 if not races:return None
 nll=[];brier=[];top=0;correct=[]
 for r in races:
  probs=probability(race_scores(r,variant),temp)
  j=next(i for i,z in enumerate(r['runners']) if z['won'])
  nll.append(-math.log(max(1e-15,probs[j])))
  brier.append(sum((p-(i==j))**2 for i,p in enumerate(probs)))
  top+=int(j==max(range(len(probs)),key=lambda k:probs[k]))
  correct.extend((p,int(i==j)) for i,p in enumerate(probs))
 bins=collections.defaultdict(list)
 for p,y in correct:bins[min(9,int(p*10))].append((p,y))
 ece=sum(len(b)*abs(sum(p for p,_ in b)/len(b)-sum(y for _,y in b)/len(b)) for b in bins.values())/len(correct)
 return {'races':len(races),'nll':round(statistics.mean(nll),5),'brier':round(statistics.mean(brier),5),
         'top1_hit_rate_pct':round(100*top/len(races),2),'ece':round(ece,5),
         'winner_probability_mean_pct':round(100*statistics.mean(math.exp(-v) for v in nll),2)}

def train_variant(races):
 if not races:return None
 pool=[]
 for variant in PRESETS:
  for temp in (6,8,10,13,16,20,25,32,42):
   metric=outcomes(races,variant,temp)
   pool.append((metric['nll'],variant,temp,metric))
 nll,var,t,metric=min(pool,key=lambda z:(z[0],z[1],z[2]))
 return {'variant':var,'temperature':t,'training_metrics':metric}

def run(races,output,days,asof):
 output.mkdir(parents=True,exist_ok=True)
 if not races:raise RuntimeError('No verified races available for probability evaluation')
 daylist=sorted(set(r['date'] for r in races));holdout_dates=set(daylist[-min(7,max(1,len(daylist)//3)):])
 train=[r for r in races if r['date'] not in holdout_dates];test=[r for r in races if r['date'] in holdout_dates]
 if len(train)<30 or len(test)<5:raise RuntimeError('Insufficient chronological training/holdout races')
 allgroups=sorted(set(group_key(r,'race_type') for r in races))
 disciplines=sorted(set(r['discipline'] for r in races))
 chosen={}
 for disc in disciplines:
  dtrain=[r for r in train if r['discipline']==disc]
  chosen[disc]=train_variant(dtrain) if len(dtrain)>=25 else train_variant(train)
 for group in allgroups:
  sub=[r for r in train if group_key(r,'race_type')==group]
  d=group.split(' / ',1)[0]
  chosen[group]=train_variant(sub) if len(sub)>=50 else chosen[d]
 fields=['segment','training_races','holdout_races','strategy','temperature',
         'train_nll','holdout_nll','uniform_holdout_nll','holdout_brier','uniform_holdout_brier',
         'top1_hit_rate_pct','holdout_ece','out_of_sample_status']
 rows=[]
 for group in disciplines+allgroups:
  filt=(lambda r:r['discipline']==group) if group in disciplines else (lambda r:group_key(r,'race_type')==group)
  tr=[r for r in train if filt(r)];te=[r for r in test if filt(r)]
  choice=chosen[group]
  m=outcomes(te,choice['variant'],choice['temperature']) if te else None
  u=statistics.mean(math.log(len(r['runners'])) for r in te) if te else None
  bu=statistics.mean((len(r['runners'])-1)/len(r['runners']) for r in te) if te else None
  rows.append({'segment':group,'training_races':len(tr),'holdout_races':len(te),
         'strategy':choice['variant'],'temperature':choice['temperature'],
         'train_nll':outcomes(tr,choice['variant'],choice['temperature'])['nll'] if tr else '',
         'holdout_nll':m['nll'] if m else '','uniform_holdout_nll':round(u,5) if u is not None else '',
         'holdout_brier':m['brier'] if m else '','uniform_holdout_brier':round(bu,5) if bu is not None else '',
         'top1_hit_rate_pct':m['top1_hit_rate_pct'] if m else '', 'holdout_ece':m['ece'] if m else '',
         'out_of_sample_status':'TOO_SMALL_INTERPRET_CAUTIOUSLY' if len(te)<20 else 'RESEARCH_ONLY'})
 write_csv(output/'stage92-segment-performance.csv',rows,fields)
 # Every variant is evaluated on the same holdout race cohort. Temperatures
 # are selected ONLY on training dates (or inherited from the parent group).
 variants=[]
 for group in disciplines+allgroups:
  filt=(lambda r:r['discipline']==group) if group in disciplines else (lambda r:group_key(r,'race_type')==group)
  gtrain=[r for r in train if filt(r)];gtest=[r for r in test if filt(r)]
  parent=(group if group in disciplines else group.split(' / ',1)[0])
  fallback=[r for r in train if r['discipline']==parent]
  fit=gtrain if len(gtrain)>=50 else fallback if len(fallback)>=25 else train
  for variant in PRESETS:
   options=[(outcomes(fit,variant,temp)['nll'],temp) for temp in (6,8,10,13,16,20,25,32,42)]
   train_loss,temp=min(options)
   train_metric=outcomes(gtrain,variant,temp) if gtrain else None
   hold_metric=outcomes(gtest,variant,temp) if gtest else None
   variants.append({'segment':group,'variant':variant,'temperature':temp,
      'training_races':len(gtrain),'holdout_races':len(gtest),
      'training_nll':train_metric['nll'] if train_metric else '',
      'holdout_nll':hold_metric['nll'] if hold_metric else '',
      'holdout_brier':hold_metric['brier'] if hold_metric else '',
      'holdout_top1_hit_rate_pct':hold_metric['top1_hit_rate_pct'] if hold_metric else '',
      'status':'SMALL_HOLDOUT' if len(gtest)<20 else 'RESEARCH_ONLY'})
 write_csv(output/'stage92-all-variant-results.csv',variants,
   ['segment','variant','temperature','training_races','holdout_races','training_nll',
    'holdout_nll','holdout_brier','holdout_top1_hit_rate_pct','status'])
 per=[]
 for race in races:
  choice=chosen[group_key(race,'race_type')]
  scores=race_scores(race,choice['variant']);probs=probability(scores,choice['temperature'])
  pred=sorted(zip(scores,probs,race['runners']),key=lambda z:(-z[1],norm(z[2]['horse'])))
  for k,(score,p,rr) in enumerate(pred,1):
   per.append({'date':race['date'],'course':race['course'],'time':race['time'],
         'discipline':race['discipline'],'race_type':race['race_type'],'race_name':race['race_name'],
         'horse':rr['horse'],'rank':k,'win_probability_pct':round(p*100,4),
         'has_hpr':int(rr['has_hpr']),
         'hpr_prior_count':rr['hpr_prior_count'],'evidence_count':rr['evidence_count'],
         'model':choice['variant'],'probability_sum_pct':100.,
         'is_holdout':int(race['date'] in holdout_dates),'winner_verified':int(rr['won'])})
 write_csv(output/'stage92-race-probabilities.csv',per,OUT_FIELDS)
 report={'stage':92,'model':'Model E v2 / RESEARCH ONLY / win probability',
    'start':(asof-dt.timedelta(days=days)).isoformat(),'end':(asof-dt.timedelta(days=1)).isoformat(),
    'training_days':sorted(set(r['date'] for r in train)), 'holdout_days':sorted(holdout_dates),
    'training_races':len(train),'holdout_races':len(test),
    'total_verified_races':len(races),'runners':len(per),
    'without_hpr':sum(not z['has_hpr'] for r in races for z in r['runners']),
    'with_hpr':sum(z['has_hpr'] for r in races for z in r['runners']),
    'segments':rows,'all_variant_comparison_rows':len(variants),'tested_variants':sorted(PRESETS),
    'notes':['Each race sums to 100% for all independently verified actual starters matched to the pre-off racecard.',
       'No starter is excluded or penalised because it lacks HPR; available Ability inputs are renormalised.',
       'Scores are experimental; probabilities require more out-of-sample calibration before live use.',
       'Weight choice and temperature use earlier dates only; held-out dates never optimise models.',
       'Sparse groups inherit their discipline strategy and are NOT separately optimised.',
       'Some pre-off trainer/jockey records and draw history are incomplete; missing signals contribute neutral 50.',
       'No bookmaker odds, SP, ROI or market prices enter the probability model.',
       'Frozen D and D(c), persistent HPR and live app remain untouched.']}
 (output/'stage92-summary.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('segments','training_days','holdout_days')},indent=2))
 for row in rows:print(f"{row['segment'][:42]:42s} train={row['training_races']:3d} test={row['holdout_races']:3d} variant={row['strategy']:<18s} logloss={row['holdout_nll']} uniform={row['uniform_holdout_nll']}")
 return report

def write_csv(path,rows,cols):
 p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rows)

def self_test():
 assert form_signals('')==(None,None)
 assert weighted_available([(None,100),(82,1)])==82
 assert weighted_available([(None,10)])==50
 assert discipline({'type':'NH Flat','surface':'AW'})=='Jumps'
 assert discipline({'type':'Flat','surface':'AW'})=='AW'
 assert race_type({'type':'Hurdle','race_name':'Special Novices Handicap Hurdle'})=='Novice Handicap Hurdle'
 for variant in PRESETS:
  for disc in ('Flat','AW','Jumps'):
   assert abs(sum(weights(variant,disc).values())-1)<1e-9
 assert abs(sum(probability([30,80,60,50],15))-1)<1e-12
 assert race_scores({'discipline':'Jumps','race_type':'NH Flat/Bumper','runners':[{'pillars':dict(zip(PILLARS,[50]*8))}]},'balanced')==[50.0]
 # No HPR must not count as 0, and a high-form debutant can beat low-form rated horse.
 a=weighted_available([(None,.35),(None,.15),(80,.25),(90,.20),(70,.05)])
 b=weighted_available([(25,.35),(35,.15),(40,.25),(20,.20),(20,.05)])
 assert a>b,(a,b)
 for n in (2,5,16):assert all(0<x<1 for x in probability([50.]*n,12))
 # Holdout selection: choose strategy on train only; all runners represented.
 from tempfile import TemporaryDirectory
 with TemporaryDirectory() as d:
  races=[]
  for z in range(40):
   day=(dt.date(2026,10,1)+dt.timedelta(days=z)).isoformat()
   runners=[]
   for i in range(5):
    runners.append({'horse':str(i),'won':i==(z%5),'has_hpr':i%2==0,
      'hpr_prior_count':int(i%2==0),'evidence_count':3,'pillars':dict(zip(PILLARS,[40+10*i]*8))})
   races.append({'date':day,'course':'Test','time':'12:00','discipline':'AW',
           'race_type':'Handicap','race_name':'Test Handicap','runners':runners})
  report=run(races,pathlib.Path(d),40,dt.date(2026,11,10))
  assert report['without_hpr']==80 and report['runners']==200
  assert report['holdout_races']>0 and report['training_races']>0
  assert len(list(csv.DictReader(open(pathlib.Path(d)/'stage92-race-probabilities.csv',encoding='utf-8-sig'))))==200
 print('PASS: probability totals, missing-HPR neutral, race type, held-out scoring, all runners')

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--self-test',action='store_true')
 p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
 p.add_argument('--db',type=pathlib.Path)
 p.add_argument('--ratings',type=pathlib.Path)
 p.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage92'))
 p.add_argument('--as-of',default='');p.add_argument('--days',type=int,default=30)
 a=p.parse_args()
 if a.self_test:self_test();return
 if not a.db or not a.db.is_file() or not a.ratings or not a.ratings.is_file():p.error('--db and --ratings must exist')
 import stage86_hpr_engine as hpr
 asof=hpr.day(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
 if not asof or not 14<=a.days<=90:p.error('Provide --days between 14 and 90')
 races,rejected,population,source_errors=fetch_data(a.root,a.db,a.ratings,asof,a.days)
 report=run(races,a.output,a.days,asof)
 report['rejected_races']=rejected;report['historical_population_observations']=population
 report['source_invalid_results']=source_errors
 (a.output/'stage92-summary.json').write_text(json.dumps(report,indent=2)+'\n')
 print('Audit',json.dumps({'rejected_races':rejected,'model_races':len(races),'historical_population_observations':population},indent=2))

if __name__=='__main__':main()
