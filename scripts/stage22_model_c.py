#!/usr/bin/env python3
"""Racing Hot Pots Model C v2: unsampled long-history challenger, immutable live decisions.

Train: read every historic race in date order, form computed strictly before its own
race/date, candidate SGD models, 2023 discovery, 2024 confirmation, untouched 2025
holdout, optional forward promotion guard. No starting price or result is a feature.
Live: reprocess full earlier history, use selected model on complete fields and
exactly matched bookmaker prices; pre-off external source observations are logged.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, math, os, re, sqlite3, statistics, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.linear_model import SGDClassifier
from scipy.optimize import minimize_scalar
import stage21_publish_c as PUB
import stage16_history_model as A
import stage18_historical_ab as B
import stage19_live_b as LB
import stage21_live_c as OLD

UTC=dt.timezone.utc; TZ=ZoneInfo('Europe/Dublin')
VERSION='model-c-v2-stage24-identity-corrected-2026-11'
WINDOWS=(3,5,10,20,100)
BASE=B.B_FEATURES+['recent_trainer_synergy','course_specialism','relative_form_strength','trainer_jockey_course_form']
EXTRA=[f'history_age_{w}y' for w in WINDOWS]+[f'history_rating_{w}y' for w in WINDOWS]+[f'history_weight_{w}y' for w in WINDOWS]+[f'history_sample_{w}y' for w in WINDOWS]
FEATURES=BASE+EXTRA
SCALE={
 'age':8,'rating_relative':35,'weight_relative':25,'field_size':12,'distance_f':20,
 'horse_starts':4,'days_since_last':180,'trend_positive_count':4,'trend_negative_count':4,
 'trainer_jockey_course_form':.1,'recent_trainer_synergy':.08,'course_specialism':.05,
 'relative_form_strength':10,
}
MASKS={'full':list(range(len(FEATURES))),
       'form': [i for i,x in enumerate(FEATURES) if not x.startswith('trend_') and not x.startswith('history_') and x!='named_match'],
       'history':list(range(len(FEATURES)))}

def norm(s):return A.norm(str(s or ''))
def date_of(s):
 try:return dt.date.fromisoformat(str(s)[:10])
 except (ValueError,TypeError):return dt.date(1900,1,1)
def parse(s):return OLD.parse_time(s)
def atomic_json(path,obj):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False,sort_keys=True,default=str)+'\n');os.replace(tmp,p)
def read(path,default=None):
 try:return json.loads(Path(path).read_text())
 except (OSError,ValueError):return {} if default is None else default

class LongContext(B.PastContext):
 """All previous years retained; long-term historical signal not capped at 5 years.
 Lists store only validated winner-profile facts for named and like-for-like races.
 """
 def __init__(self,ids,needed):
  super().__init__(ids,needed)
  self.named=collections.defaultdict(list)
  self.similar=collections.defaultdict(list)
 def get(self,rows):
  r=rows[0];event=self.ids.get(r[0]);date=r[1]
  if event:
   earlier=[x for x in self.named.get(event,[]) if x['date']<date]
   if len(earlier)>=3 and len({x['date'][:4] for x in earlier})>=3:
    return 'EXACT_NAMED_EVENT_ALL_YEARS',earlier
  k=B.condition_key(r[2],r[3],r[17],r[5],r[18],r[4])
  if k is None:return 'NO_SUPPORTED_MATCH',[]
  matches=[]
  for d in (-1.,-.5,0.,.5,1.):
   matches.extend(x for x in self.similar.get((*k[:-1],k[-1]+d),()) if x['date']<date)
  matches.sort(key=lambda z:z['date'])
  return ('SAME_COURSE_CONDITIONS_ALL_YEARS' if len(matches)>=12 else 'LIMITED_HISTORICAL_CONTEXT'),matches

def required_keys(db,day):
 """Get historical course/condition keys without selecting any sample of races."""
 cx=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
 mn,mx,count=cx.execute('SELECT min(race_date),max(race_date),count(*) FROM historical_races').fetchone()
 # PastContext.add only needs all known keys, not target list.
 keys=set()
 for course,typ,surf,going,cls,dist in cx.execute('SELECT DISTINCT course,race_type,surface,going,race_class,distance_f FROM historical_races WHERE race_date < ?',(day,)):
  k=B.condition_key(course,typ,surf,going,cls,dist)
  if k:keys.add(k)
 cx.close()
 return keys,{'first_date':mn,'last_date':mx,'total_races_in_db':count}

def append_features(features,rows,past,current_date):
 """Extra multi-horizon descriptors, measured only from earlier historical races."""
 dates=[date_of(x['date']) for x in past]
 now=date_of(current_date)
 by_window={}
 for w in WINDOWS:
  ss=[r for r,d in zip(past,dates) if (now-d).days>0 and (w>=100 or (now-d).days<=int(365.25*w))]
  if not ss: by_window[w]=(None,None,None,0);continue
  def median(k):
   vals=[float(z[k]) for z in ss if z.get(k) is not None]
   return statistics.median(vals) if vals else None
  by_window[w]=(median('age'),median('rating_rel'),median('weight_rel'),len(ss))
 out=[]
 for f in features:
  d=dict(f)
  d['recent_trainer_synergy']=d['horse_last5_win_rate']*d['trainer_win_rate']
  d['course_specialism']=d['trainer_course_win_rate']*d['horse_course_win_rate']
  d['relative_form_strength']=d['rating_relative']*d['horse_last5_place_rate']
  d['trainer_jockey_course_form']=d['trainer_course_win_rate']*d['jockey_course_win_rate']
  for w,(age,rat,wt,n) in by_window.items():
   d[f'history_age_{w}y']=max(0.,1-abs(d['age']-age)/5) if age is not None and d['age'] else 0.
   d[f'history_rating_{w}y']=max(0.,1-abs(d['rating_relative']-rat)/35) if rat is not None else 0.
   d[f'history_weight_{w}y']=max(0.,1-abs(d['weight_relative']-wt)/25) if wt is not None else 0.
   d[f'history_sample_{w}y']=math.log1p(n)
  out.append(d)
 return out

def matrix(features):
 x=np.array([[f.get(k,0) for k in FEATURES] for f in features],dtype=np.float32)
 x=np.nan_to_num(x,nan=0,posinf=0,neginf=0)
 scales=np.array([SCALE.get(k,10 if 'sample_' in k else 1) for k in FEATURES],dtype=np.float32)
 return np.arcsinh(np.clip(x/scales,-1e4,1e4)).astype(np.float32)

def models():
 # Independent candidates discover how much historical context is useful.
 spec=[]
 for family,reg in [('full',1e-4),('full',1e-3),('form',3e-4),('history',3e-3)]:
  spec.append((family,reg,SGDClassifier(loss='log_loss',penalty='l2',alpha=reg,random_state=23,average=True,learning_rate='optimal',fit_intercept=True)))
 return spec

def race_softmax(logits,temp=1.):
 z=np.asarray(logits,dtype=float)/max(.2,float(temp));z-=z.max();a=np.exp(np.clip(z,-60,0));return a/a.sum()

def logits(model,x):
 idx=model['indices'];coef=np.array(model['coefficients'],dtype=float)
 return np.asarray(x[:,idx],dtype=float)@coef+float(model.get('intercept',0))

def probs(model,x):return race_softmax(logits(model,x),model.get('temperature',1.))

def serialize_model(clf,family):
 return {'family':family,'indices':MASKS[family], 'coefficients':clf.coef_[0].tolist(),'intercept':float(clf.intercept_[0]),'temperature':1.0}

def candidate_loss(races,model,t):
 logs=[]
 for xs,y,sp,meta in races:
  p=race_softmax(logits(model,xs),t);logs.append(-math.log(max(1e-10,float(p[y]))))
 return statistics.mean(logs) if logs else 1e10

def summary(rows):
 n=len(rows);wins=sum(bool(r['win']) for r in rows)
 priced=[r for r in rows if r.get('sp') is not None and 1.<r['sp']<5000]
 profit=sum((r['sp']-1 if r['win'] else -1) for r in priced)
 return {'races':n,'wins':wins,'strike_rate_pct':round(100*wins/n,2) if n else None,'priced_bets':len(priced),
   'profit_eur':round(5*profit,2),'sp_roi_pct':round(100*profit/len(priced),2) if priced else None,
   'hypothetical_stake_eur':5,'source':'historical settled SP only, not bookmaker execution'}

def selection_gate(p,params):
 order=np.argsort(p)[::-1];lead=float(p[order[0]]);nextp=float(p[order[1]])
 return bool(lead-nextp >=params['lead_pp']/100 and lead>=params['min_probability'] and lead>=params['ratio']*nextp)

def selection_summary(races,model,gate):
 rows=[]
 for xs,y,sp,meta in races:
  p=probs(model,xs);i=int(np.argmax(p))
  if not selection_gate(p,gate):continue
  rows.append({'win':int(i==y),'sp':sp[i]})
 return summary(rows)

def forward_examples(data,subset=None):
 """Only genuinely settled full fields and their frozen BEFORE-OFF features."""
 history=read(data/'strategy-c-history.json',{'races':[]})
 races=[r for r in history.get('races',[]) if r.get('status') in ('Won','Lost') and isinstance(r.get('decision',{}).get('features_snapshot'),list)]
 if not races:return []
 index=PUB.result_lookup(data,{r['date'] for r in races})
 out=[]
 for r in races:
  if subset and not subset(r):continue
  original=r['decision']['features_snapshot'];candidates=r.get('runners',[])
  if len(original)!=len(candidates) or len(candidates)<2:continue
  winners=[]
  for i,runner in enumerate(candidates):
   hit=PUB.choose_result({**r,'horse':runner.get('horse'),'horse_id':runner.get('horse_id')},index)
   if hit and hit['status']=='Won':winners.append(i)
  if len(winners)!=1:continue
  x=np.asarray(original,dtype=np.float32)
  if x.shape!=(len(candidates),len(FEATURES)) or not np.isfinite(x).all():continue
  out.append((x,winners[0],r))
 return out

def forward_challenger(data,prior):
 """Promote only after paired unseen races corroborate the challenger."""
 path=data/'strategy-model-c-v2-challenger.json'
 challenger=read(path)
 if challenger.get('version')!=VERSION or challenger.get('state')!='HISTORICALLY_TRAINED_V2':return prior
 items=forward_examples(data)
 pairs=[]
 for x,winner,r in items:
  shadow=r.get('decision',{}).get('shadow')
  if not shadow or shadow.get('challenger_id')!=challenger.get('trained_at'):continue
  ch=np.array([t.get('win_probability',0) for t in r.get('runners',[])],dtype=float)
  sh=np.array(shadow.get('probabilities',[]),dtype=float)
  if len(ch)!=len(x) or len(sh)!=len(x) or min(ch)<=0 or min(sh)<=0:continue
  pairs.append((r['date'],-math.log(ch[winner]),-math.log(sh[winner]),int(np.argmax(ch)==winner),int(np.argmax(sh)==winner)))
 dates={x[0] for x in pairs}
 if len(pairs)<100 or len(dates)<10:
  print('V2 forward challenger observations:',len(pairs),'on',len(dates),'dates; retaining incumbent')
  return prior
 oldlog=statistics.mean(x[1] for x in pairs);newlog=statistics.mean(x[2] for x in pairs)
 oldwin=statistics.mean(x[3] for x in pairs);newwin=statistics.mean(x[4] for x in pairs)
 comparison={'paired_races':len(pairs),'distinct_dates':len(dates),'incumbent_logloss':round(oldlog,5),
   'challenger_logloss':round(newlog,5),'incumbent_win_rate':round(oldwin,5),'challenger_win_rate':round(newwin,5)}
 atomic_json(data/'strategy-c-v2-forward-comparison.json',comparison)
 if newlog<oldlog-.01 and newwin>=oldwin-.01:
  atomic_json(data/'strategy-model-c-v2-previous.json',prior)
  challenger['promoted_after_forward_comparison']=comparison
  atomic_json(data/'strategy-model-c-historical.json',challenger)
  # Do not retroactively alter the independent first-version 2025 test report.
  path.unlink(missing_ok=True)
  print('AUTOMATICALLY PROMOTED V2 challenger after paired forward outcomes',comparison)
  return challenger
 if len(pairs)>=250:
  path.unlink(missing_ok=True)
  print('Rejected V2 challenger after 250 forward comparisons',comparison)
 else:print('Retained V2 champion; insufficient challenger improvement',comparison)
 return prior

def train(db,mapping,root,force=False):
 root=Path(root);data=root/'data';target=data/'strategy-model-c-historical.json';prior=read(target)
 now=dt.datetime.now(UTC)
 if prior.get('version')==VERSION:
  champion=forward_challenger(data,prior)
  if (data/'strategy-model-c-v2-challenger.json').exists():return champion
  if champion.get('trained_at')!=prior.get('trained_at'):return champion
 if prior.get('version')==VERSION and not force:
  trained=parse(prior.get('trained_at'))
  if trained and (now-trained.astimezone(UTC)).total_seconds()<6*86400:
   print('V2 historical champion fresh; continuing forward assessment only');return prior
 names=B.mapping_ids(mapping);eventids,regions=names
 needed,coverage=required_keys(db,'2026-01-01')
 state=A.State();ctx=LongContext(eventids,needed)
 active=models();buffer=[];previous=None;counts=collections.Counter()
 batchx=[];batchy=[];batchw=[];validate={2023:[],2024:[],2025:[]}
 started=time.monotonic()
 def flush():
  if not batchx:return
  X=np.vstack(batchx);Y=np.concatenate(batchy);W=np.concatenate(batchw)
  for family,alpha,clf in active:clf.partial_fit(X[:,MASKS[family]],Y,classes=np.array([0,1]),sample_weight=W)
  batchx.clear();batchy.clear();batchw.clear()
 for k,group in enumerate(B.iter_db(db,until='2026-01-01')):
  rid,date=group[0][:2]
  if previous and previous!=date:
   for row in buffer:state.update(row[:17])
   for g in earlier:ctx.add(g)
   buffer=[];earlier=[]
  if previous is None:earlier=[]
  previous=date;counts['races_scanned']+=1
  if counts['races_scanned']%30000==0:print('V2 historical',dict(counts),'through',date,'seconds',round(time.monotonic()-started),flush=True)
  if regions.get(rid) in B.GOOD_REGION:
   match,past=ctx.get(group)
   base=B.features(state,group,past,match)
   if base:
    live,features=base;extra=append_features(features,live,past,date);x=matrix(extra)
    y=int(next(i for i,r in enumerate(live) if r[14]==1));sp=[A.safe_num(r[16]) for r in live]
    yr=int(date[:4]);discipline=B.surf(live[0][2],live[0][3],live[0][17],live[0][5])
    if yr<=2022:
     batchx.append(x);batchy.append(np.array([int(i==y) for i in range(len(x))],dtype=np.int8));batchw.append(np.full(len(x),1/len(x),dtype=np.float32));counts['training_races']+=1
     if sum(len(v) for v in batchy)>=12000:flush()
    elif yr in validate:
     validate[yr].append((x,y,sp,{'date':date,'race_id':rid,'discipline':discipline,'horse_names':[v[6] for v in live]}));counts[f'test_{yr}_races']+=1
   else:counts['fields_without_clear_winner']+=1
  else:counts['out_of_scope_or_missing_region']+=1
  buffer.extend(group);earlier.append(group)
 flush()
 if counts['training_races']<500 or len(validate[2023])<100 or len(validate[2024])<100 or len(validate[2025])<100:
  raise RuntimeError(f'Not enough verified historical examples for independent splits: {dict(counts)}')
 choices=[]
 for family,alpha,clf in active:
  m=serialize_model(clf,family)
  result=minimize_scalar(lambda v:candidate_loss(validate[2023],m,v),bounds=(.6,3.5),method='bounded',options={'xatol':.03})
  m['temperature']=round(float(result.x),4)
  loss24=candidate_loss(validate[2024],m,m['temperature'])
  choices.append((loss24,m,{'family':family,'alpha':alpha,'discovery_2023_logloss':round(float(result.fun),5),'confirmation_2024_logloss':round(loss24,5)}))
 choices.sort(key=lambda z:z[0]);chosen=choices[0][1]
 # Policy discovery on 2023 only. 2024 and 2025 are observation-only.
 policies=[{'lead_pp':gap,'min_probability':low,'ratio':ratio}
   for gap in (4,7,10,14) for low in (.18,.25,.33,.42) for ratio in (1.15,1.35)]
 policies_scored=[]
 for gate in policies:
  a=selection_summary(validate[2023],chosen,gate)
  if a['priced_bets']<30:continue
  # Selection objective combines likelihood of winners and historical SP returns.
  rate=a['wins']/max(1,a['races']);roi=a.get('sp_roi_pct')
  score=rate+.0008*max(-80,min(80,roi if roi is not None else -80))-15/max(1,a['races'])
  policies_scored.append((score,gate,a))
 if not policies_scored:gate={'lead_pp':10,'min_probability':.33,'ratio':1.35};discovery=summary([])
 else:_,gate,discovery=max(policies_scored,key=lambda q:q[0])
 holdout=validate[2025];allrows=[]
 for x,y,sp,meta in holdout:
  p=probs(chosen,x);i=int(np.argmax(p));allrows.append({'win':int(y==i),'sp':sp[i],'date':meta['date'],'field_size':len(x),'discipline':meta['discipline']})
 holdout_all=summary(allrows);holdout_selected=selection_summary(holdout,chosen,gate)
 bydisc={t:summary([r for r in allrows if r['discipline']==t]) for t in ('flat','aw','jumps')}
 new={'version':VERSION,'state':'HISTORICALLY_TRAINED_V2','trained_at':now.isoformat(),
  'history_integrity_version':'stage24-2026-11-history-identity-v1',
  'source_first_date':coverage['first_date'],'source_last_date':coverage['last_date'],
  'source_races_all_periods':coverage['total_races_in_db'],'features':FEATURES,'model':chosen,
  'selected_gate':gate,'selected_candidate':choices[0][2],
  'candidate_search':[z[2] for z in choices],
  'split_counts':{'train':counts['training_races'],'calibration_2023':len(validate[2023]),'confirmation_2024':len(validate[2024]),'test_2025':len(validate[2025])},
  'all_history_scanned':counts['races_scanned'],'history_context_policy':'all previous named-event editions, else same course/type/surface/class/within 1f from first historical date; multi-horizon 3/5/10/20/all features',
  'honest_test':{'top':holdout_all,'conditional':holdout_selected,'by_race_type':bydisc},
  'discovery_gate_metrics':discovery,
  'confirmation_gate_metrics':selection_summary(validate[2024],chosen,gate),
  'warning':'A win-probability ranker, not a proven positive-ROI betting system; external evidence is forward-only.'}
 if prior.get('version')==VERSION and prior.get('trained_at'):
  # Fresh verified 2026 form is added ONLY to the new challenger, never to the
  # frozen 2025 retrospective test. These are immutable pre-off observations.
  fresher=forward_examples(data)
  if len(fresher)>=100 and len({r['date'] for _,_,r in fresher})>=10:
   refine=SGDClassifier(loss='log_loss',alpha=new['selected_candidate']['alpha'],random_state=23,average=True)
   # Initialise an SGD challenger from the earlier historical fit and then adapt
   # on genuinely verified forward rows. Stored holdout results are not replayed.
   subset=chosen['indices']
   yy=[];xx=[];weights=[]
   for x,winner,r in fresher:
    xx.append(x[:,subset]);yy.append(np.array([int(i==winner) for i in range(len(x))],dtype=np.int8))
    weights.append(np.full(len(x),1/len(x),dtype=np.float32))
   refine.partial_fit(np.vstack(xx),np.concatenate(yy),classes=np.array([0,1]),sample_weight=np.concatenate(weights))
   # Conservative blend to avoid a small recent sample overwhelming decades of data.
   new['model']['coefficients']=(.9*np.array(chosen['coefficients'])+.1*refine.coef_[0]).tolist()
   new['model']['intercept']=float(.9*chosen['intercept']+.1*refine.intercept_[0])
   new['forward_training_records']=len(fresher)
  else:new['forward_training_records']=0
  new['honest_test']='Historical 2025 holdout is valid for original pre-2026 fit; not claimed as an independent test of this later challenger.'
  atomic_json(data/'strategy-model-c-v2-challenger.json',new)
  print('New V2 challenger staged for independently scored future outcomes, not promoted',new['forward_training_records'])
  return prior
 atomic_json(target,new)
 report={'version':2,'model':VERSION,'independent_test_year':2025,'top':holdout_all,'conditional':holdout_selected,'by_race_type':bydisc,
         'calibration_2023':discovery,'confirmation_2024':new['confirmation_gate_metrics'],
         'split_counts':new['split_counts'],'caveat':'2025 left untouched during candidate and gate tuning; test performance does not guarantee future returns.'}
 atomic_json(data/'strategy-model-c-2025-report.json',report)
 print('NEW V2 HISTORICAL TEST',json.dumps({'top':holdout_all,'selected':holdout_selected,'counts':new['split_counts']}),flush=True)
 return new

def course_for_price(course):
 # Equivalent bookmaker naming, while retaining exact date/off time/horse checks.
 key=norm(course)
 for suffix in ('allweather','aw'):
  if key.endswith(suffix):key=key[:-len(suffix)]
 for old,new in (('chelmsfordcity','chelmsford'),('kemptonpark','kempton'),('newcastleupon tyne','newcastle')):
  key=key.replace(old.replace(' ',''),new)
 return key

def price_index(odds,day):
 result=collections.defaultdict(list)
 for q in odds.get('prices',[]):
  off=parse(q.get('race_time'));o=A.safe_num(q.get('decimal_odds'))
  if not off or off.astimezone(TZ).date().isoformat()!=day or not o or o<=1:continue
  book=norm(q.get('bookmaker'))
  key=(course_for_price(q.get('course')),off.astimezone(TZ).strftime('%H:%M'),norm(q.get('horse')))
  result[key].append({'bookmaker':q.get('bookmaker'),'book_norm':book,'decimal':o,'captured_at':q.get('collected_at')})
 return result

def price_for(qs):
 if not qs:return None,[]
 latest={}
 for q in qs:
  k=q['book_norm']
  if not k or (q.get('captured_at') or '')>=(latest.get(k,{}).get('captured_at') or ''):latest[k]=q
 pp=latest.get('paddypower')
 return pp,list(latest.values())


def migrate_legacy(data):
 """Preserve the old experimental C's audit but hide its picks/results from v2."""
 marker=data/'strategy-c-v1-archived.json'
 if marker.exists():return
 old_history=read(data/'strategy-c-history.json',{'races':[]})
 old_results=read(data/'strategy-c-results.json',{'selections':[]})
 v1r=[r for r in old_history.get('races',[]) if r.get('model_version')!=VERSION]
 v1s=[r for r in old_results.get('selections',[]) if r.get('model_version')!=VERSION]
 atomic_json(marker,{'archived_at':dt.datetime.now(UTC).isoformat(),'reason':'replaced ineffective old Model C',
  'historical_races':v1r,'historical_selections':v1s})
 atomic_json(data/'strategy-c-history.json',{'version':2,'races':[r for r in old_history.get('races',[]) if r.get('model_version')==VERSION]})
 atomic_json(data/'strategy-c-results.json',{'version':2,'model':'C','selections':[r for r in old_results.get('selections',[]) if r.get('model_version')==VERSION]})
 print('Old Model C audit archived separately; previous UI selections removed',len(v1s))

def live(db,mapping,root,day=None):
 root=Path(root);data=root/'data';day=day or dt.datetime.now(TZ).date().isoformat();now=dt.datetime.now(UTC)
 model=read(data/'strategy-model-c-historical.json')
 output={'version':2,'model':'C','model_version':model.get('version'),'day':day,'generated_at':now.isoformat(),
  'state':'UNAVAILABLE','races':[],'selections':0,'warning':'Model C v2 is unproven on ROI; sparse form and extreme model/market disagreement block official picks. Original discoveries remain archived.'}
 if model.get('version')!=VERSION or model.get('state')!='HISTORICALLY_TRAINED_V2':
  output['reason']='NO_VALIDATED_V2_MODEL';atomic_json(data/'strategy-c-probabilities.json',output);return output
 cards=read(data/'racecards.json');odds=read(data/'latest-odds.json')
 fields=collections.defaultdict(list)
 for row in cards.get('runners',[]):
  if row.get('date')==day and row.get('course') and row.get('race_time') and not LB.nonrunner(row):
   fields[(norm(row['course']),LB.clock(row['race_time']))].append(row)
 fields={k:v for k,v in fields.items() if len(v)>=2 and len({norm(x['horse']) for x in v})==len(v)}
 if not fields:
  output.update(state='NO_RACECARDS_FOR_DAY',reason='COMPLETE_FIELDS_MISSING');atomic_json(data/'strategy-c-probabilities.json',output);return output
 names={A.history_key(r.get('horse'),r.get('sire'),r.get('dam')) for rs in fields.values() for r in rs}
 trs={norm(r.get('trainer')) for rs in fields.values() for r in rs if r.get('trainer')}
 jks={norm(r.get('jockey')) for rs in fields.values() for r in rs if r.get('jockey')}
 keys=set()
 for rs in fields.values():
  r=rs[0];k=B.condition_key(r['course'],LB.norm_type(r),r.get('surface'),r.get('going'),r.get('race_class'),A.safe_num(r.get('distance_f')))
  if k:
   for d in (-1.,-.5,0.,.5,1.):keys.add((*k[:-1],k[-1]+d))
 ids,_=B.mapping_ids(mapping)
 ids.update(LB.named_lookup(mapping,root/'.github/workflows/stage4_reference_race_registry.csv',fields,day))
 ctx=LongContext(ids,keys);state=B.ScopedState(names,trs,jks)
 scanned=0;last='';previous=None;buffer=[];earlier=[]
 for group in B.iter_db(db,until=day):
  d=group[0][1]
  if previous and d!=previous:
   for r in buffer:state.update(r[:17])
   for g in earlier:ctx.add(g)
   buffer=[];earlier=[]
  previous=d;last=d;scanned+=1;buffer.extend(group);earlier.append(group)
 for r in buffer:state.update(r[:17])
 for g in earlier:ctx.add(g)
 output['historical_races_scanned']=scanned;output['historical_data_through']=last
 OLD.SOURCES=('https://www.racingtv.com/tips','https://www.attheraces.com/tips','https://www.sportinglife.com/racing/tips-centre')
 signals,sources=OLD.research_sources([r for rows in fields.values() for r in rows],now,True)
 staged=read(data/'strategy-model-c-v2-challenger.json')
 has_shadow=(staged.get('version')==VERSION and staged.get('state')=='HISTORICALLY_TRAINED_V2')
 # Source tips may be displayed and timestamped, but cannot alter probability
 # before a valid forward comparison establishes calibrated incremental value.
 atomic_json(data/'strategy-c-external-evidence.json',{'observed_at':now.isoformat(),'sources':sources,'signals':signals,'policy':'observed, displayed, no unvalidated numeric boost'})
 prices=price_index(odds,day)
 for (course,t),rs in sorted(fields.items()):
  off=parse(rs[0].get('race_time'))
  if not off or off<=now+dt.timedelta(minutes=3):continue
  rid=f'LIVE|{course}|{t}';rows=[LB.live_row(r,day,rid) for r in rs]
  match,past=ctx.get(rows);features=LB.live_features(state,rows,past,match)
  if not features:continue
  x=matrix(append_features(features,rows,past,day));p=probs(model['model'],x)
  if not np.isfinite(p).all() or abs(float(p.sum())-1)>1e-4:raise ValueError('Invalid full-field probabilities')
  # Explicit, pre-off tips can contribute a small transparent FORWARD-ONLY adjustment.
  # No retrospective probability improvement is claimed without paired validation.
  raw_p=p.copy();external_for=[];boost=np.zeros(len(rs),dtype=float)
  for source in signals:
   seen=parse(source.get('observed_at'))
   if not seen or seen>=off or seen>now or source.get('signal')!='explicit_public_tip_phrase':continue
   for ix,runner in enumerate(rs):
    if norm(runner.get('horse'))==norm(source.get('horse')):
     boost[ix]=min(.12,boost[ix]+.06);external_for.append(source)
  if np.any(boost):p=race_softmax(np.log(np.maximum(1e-10,p))+boost,1.)
  rank=np.argsort(p)[::-1];i=int(rank[0]);j=int(rank[1]);selected=selection_gate(p,model['selected_gate'])
  shadow={'challenger_id':staged.get('trained_at'),'probabilities':probs(staged['model'],x).round(6).tolist()} if has_shadow else None
  picks=[]
  for idx,r in enumerate(rs):
   q=prices.get((course_for_price(r.get('course')),t,norm(r.get('horse'))),[]);pp,allbooks=price_for(q)
   actual=pp['decimal'] if pp else None
   picks.append({'horse':r.get('horse'),'horse_id':r.get('horse_id'),'win_probability':round(float(p[idx]),6),
    'historical_probability':round(float(raw_p[idx]),6),'positive_count':int(features[idx]['_positive']),
    'negative_count':int(features[idx]['_negative']),'form_runs':int(round(math.expm1(features[idx]['horse_starts']))),
    'paddypower_decimal':actual,'bookmaker_prices':allbooks,
    'expected_roi_if_price_current_pct':round(100*(float(p[idx])*actual-1),2) if actual else None,
    'external_source_count':sum(norm(z.get('horse'))==norm(r.get('horse')) for z in signals)})
  # Live risk review is a CONSERVATIVE safety screen, not an optimized or validated ROI strategy.
  # In particular, very sparse runner form or extreme disagreement with market
  # prices must not become a promoted pick just because a probability model is confident.
  discovery_selected=bool(selected)
  risk_reasons=[]
  leader=picks[i]
  if leader['form_runs']<3:risk_reasons.append('FEWER_THAN_3_RECORDED_PRIOR_RUNS')
  pp_odds=leader.get('paddypower_decimal')
  if not pp_odds or pp_odds<=1:risk_reasons.append('NO_MATCHED_PADDY_POWER_PRICE')
  elif float(p[i])/(1/pp_odds)>1.65:risk_reasons.append('UNVALIDATED_MODEL_MARKET_DISAGREEMENT')
  if discovery_selected and risk_reasons:
   selected=False
  sources_matched=[s for s in signals if norm(s.get('horse'))==norm(rs[i]['horse']) and (ts:=parse(s.get('observed_at'))) and ts<off]
  a=rs[i]['horse'];b=rs[j]['horse']
  caution=(' Earlier C discovery gate nominated this horse, but the live safety review withheld selection: '+', '.join(risk_reasons)+'.') if discovery_selected and not selected else ''
  message=(f'Model C v2 ranks {a} first at {100*p[i]:.1f}% versus {b} at {100*p[j]:.1f}%. '
   f'Long-horizon historical comparison: {len(past)} earlier comparable races ({match.lower().replace("_"," ")}). '
   f'Pre-race form: {int(round(math.expm1(features[i]["horse_starts"]))) } matched previous runs; this is not a guaranteed complete career record. '
   f'External evidence: {len(sources_matched)} matching explicit tips; small pre-off adjustment is experimental and unvalidated. '
   +('Experimental selection passed the sparse-form and market-sanity checks. No verified positive ROI is claimed.' if selected else 'No official selection after independent model/policy review.')+caution)
  output['races'].append({'model':'C','model_version':VERSION,'date':day,'course':rs[0]['course'],'time':t,
   'race_time':rs[0]['race_time'],'race_id':rs[0].get('race_id'),'race_name':rs[0].get('race_name'),
   'race_type':B.surf(rs[0]['course'],LB.norm_type(rs[0]),rs[0].get('surface'),rs[0].get('going')),
   'field_size':len(rs),'match_basis':match,'historical_examples':len(past),'runners':picks,
   'ranking':[{'horse':rs[k]['horse'],'probability':round(float(p[k]),6)} for k in rank],
   'selection':picks[i] if selected else None,'selection_status':'SELECTED' if selected else ('RISK_FILTERED' if discovery_selected else 'NO_SELECTION'),
   'provisional_discovery_leader':{'horse':leader['horse'],'win_probability':leader['win_probability'],'form_runs':leader['form_runs'],'paddypower_decimal':leader['paddypower_decimal']} if discovery_selected and not selected else None,
   'decision':{'leader':a,'second':b,'margin_pp':round(100*float(p[i]-p[j]),2),
    'threshold_frozen':True,'selected_gate':model['selected_gate'],'external_sources':sources_matched,
    'safety_review':{'discovery_selected':discovery_selected,'pass':not risk_reasons,'reasons':risk_reasons,'min_form_runs':3,'max_model_to_market_ratio':1.65,'note':'Not empirically calibrated or proven profitable'},
    'features_snapshot':x.round(6).tolist(),'shadow':shadow,'historical_model_probabilities':raw_p.round(6).tolist()},
   'verdict':message,'published_at':now.isoformat(),'complete_field':True})
 output['state']='READY' if output['races'] else 'NO_UPCOMING_COMPLETE_FIELDS'
 output['selections']=sum(bool(q.get('selection')) for q in output['races'])
 output['research_policy']=model['history_context_policy']
 migrate_legacy(data)
 atomic_json(data/'strategy-c-probabilities.json',output)
 print('V2 live day',day,'races',len(output['races']),'selections',output['selections'],'PP coverage',sum(z.get('paddypower_decimal') is not None for r in output['races'] for z in r['runners']),flush=True)
 return output

def self_test():
 assert len(FEATURES)==len(set(FEATURES))
 a=np.array([.1,.8,.2]);b=race_softmax(a)
 assert abs(sum(b)-1)<1e-9
 assert not selection_gate(b,{'lead_pp':100,'min_probability':1,'ratio':5})
 previous=LongContext({},set());example=[('a','2000-01-01','c','flat',8.,'good','h','','','','',3,40,120,1,'',3,'turf','class5')]
 assert previous.get(example)[0]=='LIMITED_HISTORICAL_CONTEXT'
 z=matrix(append_features([{'age':4,'rating_relative':2,'weight_relative':0,'horse_last5_win_rate':.25,'trainer_win_rate':.1,'trainer_course_win_rate':.1,'horse_course_win_rate':.2,'horse_last5_place_rate':.4,'jockey_course_win_rate':.1}],example,[], '2000-01-01'))
 assert z.shape==(1,len(FEATURES)) and np.isfinite(z).all()
 assert course_for_price('Chelmsford (AW)')==course_for_price('Chelmsford City')
 assert course_for_price('Kempton Park')==course_for_price('Kempton (AW)')
 print('Model C v2 unit checks passed')

def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=('train','live','self-test'));p.add_argument('--repo',default='.');p.add_argument('--db');p.add_argument('--mapping');p.add_argument('--day');p.add_argument('--force',action='store_true');args=p.parse_args()
 if args.command=='self-test':return self_test()
 if not args.db or not args.mapping:raise SystemExit('Historical SQLite database and mapping required')
 if args.command=='train':train(args.db,args.mapping,args.repo,args.force)
 else:live(args.db,args.mapping,args.repo,args.day)

if __name__=='__main__':main()
