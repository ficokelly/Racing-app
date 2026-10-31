#!/usr/bin/env python3
"""Stage 5: chronological race-trend + pre-race form ranking research (stdlib only).
All historical features are built from PRIOR calendar days. SP is evaluation only.
"""
import argparse, collections, csv, datetime as dt, json, math, os, sqlite3
from pathlib import Path

D=collections.defaultdict

def norm(x): return str(x or '').strip().upper()
def num(x):
 try: return float(x) if x is not None else None
 except (ValueError,TypeError): return None
def ratio(w,n,prior=.1,strength=25): return (w+prior*strength)/(n+strength)
def metric(w,n,base,prior=35): return math.log(max(0.01,(w+base*prior)/(n+prior))/max(.01,base))
def bucket(x):
 v=num(x)
 return 'unknown' if v is None else f'{round(v*2)/2:g}f'
def age_band(x):
 v=num(x);return str(int(v)) if v is not None and 2<=v<=20 else 'unknown'
def rating_band(x):
 v=num(x);return str(int(v//10)*10) if v is not None and 0<=v<200 else 'unknown'
def distance_band(x):
 v=num(x);return 'unknown' if v is None else str(int(v//2)*2)
def valid_date(x):
 try:return dt.date.fromisoformat(str(x)[:10])
 except (TypeError,ValueError):return None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--out',default='stage5_output');ap.add_argument('--start',type=int,default=2023);ap.add_argument('--end',type=int,default=2025);ap.add_argument('--sample',type=int,default=300);args=ap.parse_args()
 out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
 con=sqlite3.connect('file:'+os.path.abspath(args.db)+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
 sql='''SELECT r.id race_id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.race_class,r.going,
 u.horse_name_norm,u.horse_source_id,u.horse_country,u.foaled,u.finish_pos,u.trainer,u.jockey,
 u.age,u.official_rating,u.draw,u.sp_decimal
 FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
 ORDER BY r.race_date,r.id'''
 # Maintain strictly prior-day historical counts; accumulate the current day's results separately.
 hist=D(lambda:[0,0,0]); horse_recent=D(lambda:collections.deque(maxlen=5)); trainer_recent=D(collections.deque)
 trends=D(lambda:[0,0]); cohorts=D(lambda:[0,0]); overall=[0,0]
 pending=[];lastday=None; races=0; evaluated=0; invalid=collections.Counter();results=D(lambda:[0,0,0.,0.,0]); byyear=D(lambda:D(lambda:[0,0,0.,0.,0])); samples=[]
 variants={
  'historical_trends':(1,0,0,0,0,0),
  'horse_form':(0,1,0,0,0,0),
  'balanced':(1,1.3,.6,.5,.7,.35),
  'form_focused':(.5,2,.8,.5,1,.5),
  'course_distance_focused':(1,1,.4,.4,1.4,.8),
  'trainer_focused':(.8,.7,1.5,.7,.8,.5),
 }
 def apply_pending():
  for row,day in pending:
   h,t,j,c,dist,kind,co,age,rb,win,placed=row
   for key in [('horse',h),('trainer',t),('jockey',j),('horse_course',h,c),('horse_distance',h,dist),('horse_course_distance',h,c,dist),('trainer_course',t,c)]:
    if any(not x for x in key[1:]):continue
    a=hist[key];a[0]+=1;a[1]+=win;a[2]+=placed
   if h:horse_recent[h].append((win,placed))
   if t:trainer_recent[t].append((day,win,placed))
   cohorts[co][0]+=1;cohorts[co][1]+=win
   for dim,val in [('age',age),('rating',rb)]:
    if val!='unknown':a=trends[(co,dim,val)];a[0]+=1;a[1]+=win
   overall[0]+=1;overall[1]+=win
  pending.clear()
 def getscore(x,day):
  h,t,j,c,dist,kind,co,age,rb=x
  base=max(.025, min(.5,ratio(*[overall[1],overall[0]],prior=.1,strength=100)))
  # Local cohort baseline for age/rating enrichment, shrink heavily for sparse cohorts.
  cr,cw=cohorts[co];cb=ratio(cw,cr,base,150)
  ts=[]
  for dim,val in [('age',age),('rating',rb)]:
   if val=='unknown':continue
   n,w=trends[(co,dim,val)];ts.append(metric(w,n,cb,50))
  trend=sum(ts)/len(ts) if ts else 0
  recent=horse_recent.get(h,());hw=sum(v[0] for v in recent);hp=sum(v[1] for v in recent)
  horse=metric(hw,len(recent),base,6)+.35*metric(hp,len(recent),min(.65,base*3),6) if recent else 0
  tr=trainer_recent.get(t)
  if tr:
   cutoff=day-dt.timedelta(days=30)
   while tr and tr[0][0]<cutoff:tr.popleft()
  tn=len(tr) if tr else 0;tw=sum(v[1] for v in tr) if tr else 0
  trainer=metric(tw,tn,base,30) if tn else 0
  jn,jw,_=hist[('jockey',j)] if j else (0,0,0)
  jockey=metric(jw,jn,base,75) if jn else 0
  course_score=0;parts=[]
  for key in [('horse_course',h,c),('horse_distance',h,dist),('horse_course_distance',h,c,dist)]:
   if not all(key[1:]):continue
   n,w,_=hist[key];parts.append(metric(w,n,base,15))
  if parts:course_score=sum(parts)/len(parts)
  n,w,_=hist[('trainer_course',t,c)] if t and c else (0,0,0)
  tc=metric(w,n,base,50) if n else 0
  return (trend,horse,trainer,jockey,course_score,tc),len(recent)
 def evaluate(rows,day):
  nonlocal races,evaluated
  races+=1
  starters=[x for x in rows if num(x['sp_decimal']) is not None and num(x['sp_decimal'])>=1.01]
  winners=[x for x in starters if x['finish_pos']==1]
  if len(winners)!=1 or len(starters)<2:
   invalid['invalid_winner_or_sp']+=1;return
  year=day.year
  if not args.start<=year<=args.end:return
  evaluated+=1
  candidates=[]
  for x in starters:
   h=norm(x['horse_name_norm']);t=norm(x['trainer']);j=norm(x['jockey']);c=norm(x['course']);dist=distance_band(x['distance_f'])
   co=(c,norm(x['surface'] or x['race_type']),bucket(x['distance_f']),norm(x['race_class']))
   features,nprior=getscore((h,t,j,c,dist,norm(x['race_type']),co,age_band(x['age']),rating_band(x['official_rating'])),day)
   candidates.append((x,features,nprior))
  # Each race is a single decision. Ties deterministic but no claims of predictive accuracy from ties.
  methods=dict(variants)
  for method,weights in methods.items():
   selected=max(candidates,key=lambda v:(sum(a*b for a,b in zip(v[1],weights)),-float(v[0]['sp_decimal'])))
   x=selected[0];win=int(x['finish_pos']==1);odds=float(x['sp_decimal'])
   for target in (results[method],byyear[year][method]):
    target[0]+=1;target[1]+=win;target[2]+=odds*win-1;target[3]+=int(selected[2]>0);target[4]+=int(odds<=3)
   if len(samples)<args.sample and method=='balanced':
    samples.append([str(day),x['course'],x['race_id'],x['horse_name_norm'],round(sum(a*b for a,b in zip(selected[1],weights)),4),win,odds,selected[2],len(starters)])
  fav=min(candidates,key=lambda v:float(v[0]['sp_decimal']))[0]
  for target in (results['sp_favourite_reference'],byyear[year]['sp_favourite_reference']):
   win=int(fav['finish_pos']==1);target[0]+=1;target[1]+=win;target[2]+=float(fav['sp_decimal'])*win-1
 def process_race(rows,day):
  evaluate(rows,day)
  for x in rows:
   sp=num(x['sp_decimal']);win=int(x['finish_pos']==1);placed=int(x['finish_pos'] is not None and 1<=x['finish_pos']<=3)
   if sp is None or sp<1.01:continue
   h=norm(x['horse_name_norm']);t=norm(x['trainer']);j=norm(x['jockey']);c=norm(x['course']);dist=distance_band(x['distance_f']);co=(c,norm(x['surface'] or x['race_type']),bucket(x['distance_f']),norm(x['race_class']))
   pending.append(((h,t,j,c,dist,norm(x['race_type']),co,age_band(x['age']),rating_band(x['official_rating']),win,placed),day))
 current_id=None;rows=[];day=None;count=0
 for x in con.execute(sql):
  rid=x['race_id'];d=valid_date(x['race_date'])
  if d is None:invalid['bad_date_rows']+=1;continue
  if current_id is not None and rid!=current_id:
   process_race(rows,day);rows=[]
  if lastday is not None and d!=lastday:
   apply_pending()
  lastday=d;day=d;current_id=rid;rows.append(x);count+=1
 if rows:process_race(rows,day)
 apply_pending()
 def stats(v):
  n,w,p,withform,short=v
  return {'races':n,'wins':w,'top_pick_strike_rate_pct':round(w*100/n,3) if n else None,'sp_profit_units':round(p,3),'sp_roi_pct':round(100*p/n,3) if n else None,'picks_with_prior_horse_form':withform,'picks_sp_3_or_shorter':short}
 report={'status':'complete','source_years':'historical data available in database; evaluation is limited to specified years','evaluation_years':[args.start,args.end], 'rows_scanned':count,'races_scanned':races,'races_evaluated':evaluated,'methods':{k:stats(v) for k,v in results.items()},'yearly':{str(y):{k:stats(v) for k,v in a.items()} for y,a in sorted(byyear.items())},'excluded':dict(invalid),'limitations':['This is a fixed-weight exploratory benchmark, NOT optimised or calibrated probabilities.','All form and trend statistics exclude the current calendar day.','Horse identity uses normalised name, which can conflate horses with identical names.','SP is post-race and used ONLY for retrospective comparison and settlement, not as a scoring feature.','SP favourite comparison is retrospective, not a deployable pre-race odds benchmark.','Trainer 30-day form is available; horse last-five, horse/course, horse/distance, trainer/course, jockey and age/rating cohort trends are included.','Course/distance refers to distance buckets, not proof of suitability to exact distance.','Named-race trends cannot be computed without race names or identifiers.','No independent tuning, out-of-sample validation, going/weight/draw features, or current live racecard predictions yet.','Prior-year backfill may include international races; UK/IE filtering requires validated country labels.','RPR/Topspeed deliberately excluded: may be post-race information.','No dead-heat settlement, non-runner refund, or each-way accounting.']}
 (out/'stage5_summary.json').write_text(json.dumps(report,indent=2))
 with (out/'stage5_by_year.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['year','method','races','wins','strike_rate_pct','sp_profit_units','sp_roi_pct'])
  for y,a in sorted(byyear.items()):
   for method,v in sorted(a.items()):
    z=stats(v);w.writerow([y,method,z['races'],z['wins'],z['top_pick_strike_rate_pct'],z['sp_profit_units'],z['sp_roi_pct']])
 with (out/'stage5_sample_picks.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['date','course','race_id','horse','score','won','sp_decimal','prior_horse_runs_last5','field_size']);w.writerows(samples)
 print(json.dumps({'status':'complete','rows_scanned':count,'races_evaluated':evaluated,'methods':report['methods']},indent=2))
if __name__=='__main__':main()
