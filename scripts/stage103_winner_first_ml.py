#!/usr/bin/env python3
"""Stage103 | WINNER-FIRST, ODDS-BLIND ML, 13 Oct–11 Nov 2026 (research-only).

Training uses prior-date winning outcomes and *before-09:45 UTC* racecard features
plus each frozen/replayed model's horse score. No current quote, odds history,
starting price, value filter or ROI enters the feature construction, estimator,
ranking or policy. Odds are loaded AFTER ALL selections are sealed and joined
strictly to assess the hypothetical morning William Hill win-bet ROI.

Two separately labelled payout cutoffs: 09:45 UTC (Stage102 comparable) and
09:45 Europe/Dublin (Irish bettor), the latter included only if the actual
Git racecard commit predates that earlier local cutoff as well.

Source models/replays have limitations: A/B parity not certified; E V2 is
retrospective and has possible October training overlap. No production writes.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, re, statistics, subprocess, sys
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage101_all_models_compare as S101
import stage102_roi_ml_simulation as S102

VERSION='stage103-v1-winner-first-odds-blind'
TRAIN_FROM='2026-10-08';TEST_FROM='2026-10-13';TEST_THROUGH='2026-11-11'
MODELS=tuple(S102.BASE_MODELS)
CUTOFFS=('MORNING_0945_UTC_STAGE102','MORNING_0945_IRELAND')
UTC=dt.timezone.utc
DUBLIN=ZoneInfo('Europe/Dublin')
MIN_HISTORY_RACES=25
MAX_QUOTE_AGE_MIN=180
FEATURE_NAMES=[
 'model_logit','model_probability','model_rank_fraction','log_field_size',
 'age_10','age_missing','rating_100','rating_missing','rating_rel_max_100',
 'weight_150','weight_missing','draw_field_ratio','draw_missing',
 'distance_f_16','distance_missing','days_since_run_180','days_since_run_missing',
 'is_nh','is_aw','is_handicap','is_hurdle','is_chase','is_bumper',
 'is_maiden','is_novice','going_soft','going_heavy','going_firm','going_standard'
]
SELECTION_COLS=[
 'date','course','off_utc','discipline','race_type','race_key','model','strategy','rank',
 'horse','predicted_win_pct','base_win_score_share_pct','winner','finish_position',
 'model_trained','prior_training_races','last_train_date','price_scenario',
 'quote_available','morning_odds_decimal','quote_collected_at','quote_age_min','bookmaker',
 'pnl_at_morning_odds_1u','card_snapshot_valid_at_cutoff','price_status','source_limitations'
]
SUMMARY_COLS=[
 'model','price_scenario','strategy','rank_view','discipline','race_type',
 'eligible_races','selections','wins','strike_rate_pct','priced_bets','priced_wins',
 'morning_roi_pct','morning_profit_units','unpriced_selections','first_selection_date',
 'last_selection_date','trained_picks','untrained_picks'
]


def fnum(x):return S101.as_float(x)
def keynorm(x):return S101.norm(x)
def normcourse(x):return S102.course(x)
def read_float(row,keys):
 for k in keys:
  q=fnum(row.get(k))
  if q is not None:return q
 return None


def winner_features(card,base_p,model_order,field_size,discipline,race_type,ratings_max):
 """Strict allow-list. No quote/SP/ROI/finish fields are even accepted."""
 p=min(.9999,max(.0001,float(base_p)))
 age=read_float(card,('age',))
 rating=read_float(card,('official_rating','or','ofr'))
 weight=read_float(card,('weight_lbs',))
 draw=read_float(card,('draw','stall','stall_number'))
 distance=read_float(card,('distance_f',))
 days=read_float(card,('days_since_run','days_off','days_since_last_run'))
 going=str(card.get('going') or '').upper()
 typ=str(race_type or '').upper()
 return np.array([
  math.log(p/(1-p)),p,
  model_order/max(1,field_size-1),math.log(max(2,field_size)),
  (age or 0)/10,float(age is None),(rating or 0)/100,float(rating is None),
  (rating-ratings_max)/100 if rating is not None and ratings_max is not None else 0.,
  (weight or 0)/150,float(weight is None),
  draw/max(2,field_size) if draw is not None else 0.,float(draw is None),
  (distance or 0)/16,float(distance is None),
  min(max(days or 0,0),365)/180,float(days is None),
  float(discipline=='Jumps'),float(discipline=='AW'),
  float('HANDICAP' in typ),float('HURDLE' in typ),float('CHASE' in typ),
  float('BUMPER' in typ or 'NH FLAT' in typ),float('MAIDEN' in typ),float('NOVICE' in typ),
  float('SOFT' in going),float('HEAVY' in going),float('FIRM' in going),
  float('STANDARD' in going)
 ],dtype=float)


def stage92_vectors():
 """Research diagnostic only: already-generated Stage92 estimates."""
 files=list((ROOT/'reports/stage102/reference-e92').rglob('stage92-race-probabilities.csv'))
 if not files:return {}
 d=collections.defaultdict(list)
 with files[0].open(encoding='utf-8-sig',newline='') as h:
  for r in csv.DictReader(h):
   day=str(r.get('date') or '')[:10];time=S101.clock(r.get('time'))
   if TRAIN_FROM<=day<=TEST_THROUGH and time:
    d[(day,keynorm(r.get('course')),time)].append(r)
 return d


def make_records(entries,abc,de):
 """Build pure pre-race feature arrays; settlement labels stored separately."""
 out=collections.defaultdict(list);excluded=[];e92=stage92_vectors();base_count=collections.Counter()
 for e in entries:
  cards=e['cards'];size=len(cards);racekey=e['key']
  specs={**abc.get(racekey,{}),**de.get(racekey,{})}
  if 'D_ORIGINAL' in specs and 'D_ORIGINAL_REPLAY' not in specs:
   specs['D_ORIGINAL_REPLAY']=specs['D_ORIGINAL']
  from_e92=e92.get(racekey,[])
  if len(from_e92)==size and {keynorm(c.get('horse')) for c in cards}=={keynorm(r.get('horse')) for r in from_e92}:
   byhorse={keynorm(r['horse']):r for r in from_e92}
   try:
    ps=[float(byhorse[keynorm(c['horse'])]['win_probability_pct'])/100. for c in cards]
    if all(0<v<1 for v in ps) and .995<=sum(ps)<=1.005:
     specs['E_V2_STAGE92']={'probabilities':ps}
   except (KeyError,ValueError,TypeError):pass
  ratings=[read_float(c,('official_rating','or','ofr')) for c in cards]
  ratings_max=max((r for r in ratings if r is not None),default=None)
  prepared=[]
  for model in MODELS:
   spec=specs.get(model)
   if not spec:
    excluded.append({'date':e['date'],'course':e['course'],'time':e['time'],'model':model,'reason':'NO_FULL_FIELD_BASE_MODEL'});continue
   p=S102.base_probs(spec,size)
   if p is None or len(p)!=size:
    excluded.append({'date':e['date'],'course':e['course'],'time':e['time'],'model':model,'reason':'INVALID_BASE_MODEL_PROBS'});continue
   base_order=sorted(range(size),key=lambda i:(-float(p[i]),i))
   ranks={i:rank for rank,i in enumerate(base_order)}
   xx=np.stack([winner_features(c,p[i],ranks[i],size,e['discipline'],e['race_type'],ratings_max)
                 for i,c in enumerate(cards)])
   if not np.isfinite(xx).all() or xx.shape!=(size,len(FEATURE_NAMES)):
    raise RuntimeError('Invalid or nonfinite odds-blind feature array')
   prepared.append((model,p,base_order,xx))
  # Full field model vectors/features are now built WITHOUT inspecting target outcomes.
  # Outcome labels are sealed for training on FOLLOWING calendar dates only.
  result=S102.result_map(e)
  outcome=[result.get(keynorm(card.get('horse'))) for card in cards]
  if any(z is None for z in outcome):
   raise RuntimeError('Unexpected mismatched pre-off runner/results field')
  yy=[int(S102.intish(r.get('position'))==1) for r in outcome]
  if sum(yy)!=1:
   excluded.append({'date':e['date'],'course':e['course'],'time':e['time'],'model':'ALL','reason':'NO_SINGLE_RESULT_WINNER'});continue
  for model,p,base_order,xx in prepared:
   out[(e['date'],model)].append({
    'date':e['date'],'model':model,'entry':e,'x':xx,'base_p':p,
    'base_order':base_order,'sealed_y':yy,'sealed_positions':[r.get('position') for r in outcome]
   })
   base_count[model]+=1
 return out,excluded,dict(base_count)


def train_past(history):
 if len(history)<MIN_HISTORY_RACES:return None,'INSUFFICIENT_PRIOR_RACES'
 assert all(r['date']<history[-1]['date'] or r['date']==history[-1]['date'] for r in history)
 x=np.concatenate([r['x'] for r in history],axis=0)
 y=np.asarray([v for r in history for v in r['sealed_y']],dtype=int)
 if set(y.tolist())!={0,1}:return None,'ONE_CLASS_PRIOR_TRAINING'
 # Same family and C regularisation as Stage102, but NO odds/value/ROI tuning.
 model=make_pipeline(StandardScaler(),LogisticRegression(C=.15,solver='lbfgs',max_iter=350))
 model.fit(x,y)
 return model,'TRAINED_PRIOR_DATES_ONLY'


def probability_prediction(model,record):
 x=record['x'];p=np.asarray(model.predict_proba(x)[:,1],dtype=float)
 if not np.isfinite(p).all() or sum(p)<=0:raise RuntimeError('Invalid ML outputs')
 p=np.clip(p,1e-8,1.);p=p/p.sum()
 if abs(sum(p)-1)>1e-7:raise RuntimeError('Probabilities not normalized')
 return p


def predict_all(dated):
 """Produce every Rank 1/2 selection before loading a SINGLE market quote."""
 picks=[];training_audit=[];prob_accuracy=[]
 for model in MODELS:
  history=[]
  days=sorted({date for date,m in dated if m==model})
  for day in days:
   assert all(r['date']<day for r in history),'Outcome leakage: same-day result in training!'
   estimator,status=train_past(history)
   today=dated[(day,model)]
   pred=[]
   scored_for_later_accuracy=[]
   for rec in today:
    pp=probability_prediction(estimator,rec) if estimator is not None else None
    # Rank choices are only a function of model/card features.
    for tag,order,vec in [
     ('BASE',rec['base_order'],rec['base_p']),
     ('ML',sorted(range(len(pp)),key=lambda i:(-float(pp[i]),i)) if pp is not None else [],pp)
    ]:
     for rank,i in enumerate(order[:2],1):
      pred.append({'record':rec,'strategy':tag,'rank':rank,'i':i,
                   'probability':float(vec[i]),'trained':estimator is not None,
                   'prior_training_races':len(history),
                   'last_train_date':max((r['date'] for r in history),default='')})
    if pp is not None:scored_for_later_accuracy.append((rec,pp))
   # Every race on this UTC date has been scored. Outcomes are read only now.
   for rec,pp in scored_for_later_accuracy:
    j=next(i for i,v in enumerate(rec['sealed_y']) if v)
    prob_accuracy.append({'date':day,'model':model,'discipline':rec['entry']['discipline'],
      'race_type':rec['entry']['race_type'],'runners':len(pp),'base_winner_prob':float(rec['base_p'][j]),
      'ml_winner_prob':float(pp[j]),'base_log_loss':-math.log(max(1e-9,float(rec['base_p'][j]))),
      'ml_log_loss':-math.log(max(1e-9,float(pp[j]))),
      'base_brier':float(sum((float(v)-int(i==j))**2 for i,v in enumerate(rec['base_p']))),
      'ml_brier':float(sum((float(v)-int(i==j))**2 for i,v in enumerate(pp)))})
   if day>=TEST_FROM:picks.extend(pred)
   training_audit.append({'date':day,'model':model,'races':len(today),'prior_races':len(history),
      'fit_status':status,'ml_scored_races':len(today) if estimator is not None else 0,
      'last_prior_date':max((r['date'] for r in history),default='')})
   # Critical chronology: current day's winners are added only AFTER all its predictions.
   history.extend(today)
   if day>=TEST_FROM:
    print('Stage103',model,day,'races',len(today),'ML',status,flush=True)
 return picks,training_audit,prob_accuracy


def card_commit_timestamps(dates):
 """Actual Git commit used for Stage101 card_dates snapshot at <=09:45 UTC.

 If it postdates Irish 09:45 then local morning payout is not considered
 a live-like comparison, even if the quote existed earlier.
 """
 found={}
 for day in sorted(dates):
  cmd=['git','log','-1','--format=%ct','--until',day+' 09:45:00 +0000','--','data/racecards.json']
  try:
   s=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=True,text=True,timeout=15).stdout.strip()
   found[day]=dt.datetime.fromtimestamp(int(s),tz=UTC) if s else None
  except (OSError,ValueError,subprocess.SubprocessError):found[day]=None
 return found


def morning_cutoff(day,scenario):
 if scenario=='MORNING_0945_UTC_STAGE102':return dt.datetime.fromisoformat(day+'T09:45:00+00:00')
 if scenario=='MORNING_0945_IRELAND':return dt.datetime.combine(dt.date.fromisoformat(day),dt.time(9,45),tzinfo=DUBLIN).astimezone(UTC)
 raise ValueError('Unknown scenario '+scenario)


def actual_morning_quote(archive,entry,horse,cutoff):
 off=S102.off_at(entry)
 if off is None or cutoff>=off:return None,'RACE_STARTED_BY_CUTOFF'
 key=(entry['date'],normcourse(entry['course']),off.strftime('%H:%M'),keynorm(horse))
 allq=archive.by_key.get(key,())
 valid=[q for q in allq if q['book']=='williamhill' and q['collected']<=cutoff
        and 0<=(cutoff-q['collected']).total_seconds()<=MAX_QUOTE_AGE_MIN*60
        and (q['updated'] is None or q['updated']<=cutoff)
        and (q['committed'] is None or q['committed']<=cutoff)]
 if not valid:return None,'NO_ELIGIBLE_WILLIAM_HILL_MORNING_PRICE'
 return max(valid,key=lambda q:(q['collected'],q['updated'] or q['collected'])),'PRICED'


def settle_after_selections(picks,archive,commits):
 """Called AFTER predict_all. Quotes never passed to estimator or selector."""
 out=[];audit=collections.Counter()
 for p in picks:
  rec=p['record'];e=rec['entry'];i=p['i'];horse=e['cards'][i]['horse']
  status='';off=S102.off_at(e)
  for scenario in CUTOFFS:
   cutoff=morning_cutoff(e['date'],scenario)
   commit=commits.get(e['date'])
   cardvalid=(commit is not None and commit<=cutoff and off is not None and cutoff<off)
   quote=None
   if not cardvalid:
    if commit is None:status='CARD_GIT_PROVENANCE_MISSING'
    elif commit>cutoff:status='CARD_SNAPSHOT_AFTER_MORNING_CUTOFF'
    else:status='RACE_STARTED_BY_CUTOFF'
   else:
    quote,status=actual_morning_quote(archive,e,horse,cutoff)
   won=int(rec['sealed_y'][i]);price=quote['price'] if quote else None
   row={
    'date':e['date'],'course':e['course'],'off_utc':e['time'],'discipline':e['discipline'],
    'race_type':e['race_type'],'race_key':'|'.join(map(str,e['key'])),'model':rec['model'],
    'strategy':p['strategy'],'rank':p['rank'],'horse':horse,
    'predicted_win_pct':round(100*p['probability'],4),
    'base_win_score_share_pct':round(100*float(rec['base_p'][i]),4),
    'winner':won,'finish_position':rec['sealed_positions'][i],
    'model_trained':p['trained'],'prior_training_races':p['prior_training_races'],
    'last_train_date':p['last_train_date'],'price_scenario':scenario,
    'quote_available':bool(quote),'morning_odds_decimal':price if price else '',
    'quote_collected_at':quote['collected'].isoformat() if quote else '',
    'quote_age_min':round((cutoff-quote['collected']).total_seconds()/60,2) if quote else '',
    'bookmaker':'William Hill' if quote else '',
    'pnl_at_morning_odds_1u':round(price-1 if won else -1.,5) if quote else '',
    'card_snapshot_valid_at_cutoff':cardvalid,'price_status':status,
    'source_limitations':('E_V2_LOOKBACK_NOT_INDEPENDENT_HOLDOUT' if rec['model']=='E_V2_STAGE92'
      else 'A_B_FEATURE_PARITY_NOT_CERTIFIED' if rec['model'] in (S101.REPLAY_A,S101.REPLAY_B)
      else 'STAGE101_RESEARCH_REPLAY')}
   out.append(row);audit[(scenario,status)]+=1
 return out,audit


def calc(rows):
 n=len(rows);wins=sum(int(x['winner']) for x in rows)
 priced=[x for x in rows if x['quote_available']]
 pw=sum(int(x['winner']) for x in priced)
 profit=sum(float(x['pnl_at_morning_odds_1u']) for x in priced)
 return {'eligible_races':len({x['race_key'] for x in rows}),
  'selections':n,'wins':wins,'strike_rate_pct':round(100*wins/n,3) if n else None,
  'priced_bets':len(priced),'priced_wins':pw,
  'morning_roi_pct':round(100*profit/len(priced),3) if priced else None,
  'morning_profit_units':round(profit,3),
  'unpriced_selections':n-len(priced),
  'first_selection_date':min((x['date'] for x in rows),default=''),
  'last_selection_date':max((x['date'] for x in rows),default=''),
  'trained_picks':sum(bool(x['model_trained']) for x in rows),
  'untrained_picks':sum(not bool(x['model_trained']) for x in rows)}


def aggregate(rows,fields):
 groups=collections.defaultdict(list)
 for z in rows:groups[tuple(z.get(k,'') for k in fields)].append(z)
 return [{**dict(zip(fields,k)),**calc(group)} for k,group in sorted(groups.items())]


def view_rows(rows,disc_types=False):
 """One equal-stake bet on each of R1 and R2 for combined."""
 ranked=[]
 for x in rows:
  if not x['model_trained']:continue # apples-to-apples fitted ML window only
  base={**x,'rank_view':'Rank '+str(x['rank'])}
  ranked.append(base)
  ranked.append({**base,'rank_view':'Both ranks'})
 return ranked


def paired_rows(rows):
 """Same races AND both compared selections have eligible morning odds.

 Also produces odds-independent SR comparison on all jointly scored races.
 """
 by=collections.defaultdict(dict)
 for r in rows:
  if not r['model_trained']:continue
  key=(r['model'],r['price_scenario'],r['race_key'])
  by[key][(r['strategy'],r['rank'])]=r
 sets=collections.defaultdict(lambda:{'base':[],'ml':[],'races':set()})
 for (model,scenario,key),group in by.items():
  for view,ranks in [('Rank 1',(1,)),('Rank 2',(2,)),('Both ranks',(1,2))]:
   a=[group.get(('BASE',rank)) for rank in ranks];b=[group.get(('ML',rank)) for rank in ranks]
   if any(z is None for z in a+b):continue
   if not all(z['quote_available'] for z in a+b):continue
   disc=a[0]['discipline'];typ=a[0]['race_type']
   for grouping,subtype in [('Overall','All'),('Discipline',disc),('Race type',typ)]:
    k=(model,scenario,grouping,disc if grouping!='Overall' else 'All',subtype,view)
    sets[k]['base'].extend(a);sets[k]['ml'].extend(b);sets[k]['races'].add(key)
 results=[]
 for k,pair in sorted(sets.items()):
  model,scenario,grouping,disc,typ,view=k
  a=calc(pair['base']);b=calc(pair['ml'])
  results.append({'model':model,'price_scenario':scenario,'grouping':grouping,
   'discipline':disc,'race_type':typ,'rank_view':view,'matched_races':len(pair['races']),
   'baseline_bets':a['selections'],'baseline_wins':a['wins'],
   'baseline_strike_rate_pct':a['strike_rate_pct'],'baseline_morning_roi_pct':a['morning_roi_pct'],
   'ml_bets':b['selections'],'ml_wins':b['wins'],'ml_strike_rate_pct':b['strike_rate_pct'],
   'ml_morning_roi_pct':b['morning_roi_pct'],
   'strike_change_pp':round(b['strike_rate_pct']-a['strike_rate_pct'],3),
   'roi_change_pp':round(b['morning_roi_pct']-a['morning_roi_pct'],3)})
 return results


def write_csv(path,rows,columns):return S101.write_csv(path,rows,columns)


def publish(rows,train,prob,issues,coverage,pricing_audit,quote_audit,commits,out):
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 rows=sorted(rows,key=lambda x:(x['date'],x['off_utc'],x['model'],x['price_scenario'],x['strategy'],x['rank']))
 write_csv(out/'stage103-all-ranked-selections.csv',rows,SELECTION_COLS)
 fitted=view_rows(rows)
 overall=aggregate(fitted,['model','price_scenario','strategy','rank_view'])
 bydisc=aggregate(fitted,['model','price_scenario','strategy','rank_view','discipline'])
 bytype=aggregate(fitted,['model','price_scenario','strategy','rank_view','discipline','race_type'])
 byday=aggregate(fitted,['date','model','price_scenario','strategy','rank_view','discipline'])
 def to_cols(r,fields):return [*fields,*[x for x in SUMMARY_COLS if x not in fields]]
 for name,values,grp in [
  ('stage103-overall.csv',overall,['model','price_scenario','strategy','rank_view']),
  ('stage103-by-discipline.csv',bydisc,['model','price_scenario','strategy','rank_view','discipline']),
  ('stage103-by-race-type.csv',bytype,['model','price_scenario','strategy','rank_view','discipline','race_type']),
  ('stage103-by-day.csv',byday,['date','model','price_scenario','strategy','rank_view','discipline'])]:
  write_csv(out/name,values,to_cols(values,grp))
 pairs=paired_rows(rows)
 write_csv(out/'stage103-strict-paired-morning-prices.csv',pairs,
  ['model','price_scenario','grouping','discipline','race_type','rank_view','matched_races',
   'baseline_bets','baseline_wins','baseline_strike_rate_pct','baseline_morning_roi_pct',
   'ml_bets','ml_wins','ml_strike_rate_pct','ml_morning_roi_pct','strike_change_pp','roi_change_pp'])
 write_csv(out/'stage103-training-by-day.csv',train,
   ['date','model','races','prior_races','fit_status','ml_scored_races','last_prior_date'])
 write_csv(out/'stage103-probability-accuracy.csv',prob,
   ['date','model','discipline','race_type','runners','base_winner_prob','ml_winner_prob',
   'base_log_loss','ml_log_loss','base_brier','ml_brier'])
 result_accuracy=[]
 groups=collections.defaultdict(list)
 for p in prob:
  if p['date']>=TEST_FROM:groups[p['model']].append(p)
 for model,records in sorted(groups.items()):
  result_accuracy.append({'model':model,'races':len(records),
     'base_mean_log_loss':round(statistics.mean(x['base_log_loss'] for x in records),5),
     'ml_mean_log_loss':round(statistics.mean(x['ml_log_loss'] for x in records),5),
     'base_mean_brier':round(statistics.mean(x['base_brier'] for x in records),5),
     'ml_mean_brier':round(statistics.mean(x['ml_brier'] for x in records),5)})
 write_csv(out/'stage103-accuracy-summary.csv',result_accuracy,
  ['model','races','base_mean_log_loss','ml_mean_log_loss','base_mean_brier','ml_mean_brier'])
 bands=[]
 for model in MODELS:
  for cut in CUTOFFS:
   r=[x for x in rows if x['model']==model and x['price_scenario']==cut
       and x['strategy']=='ML' and x['rank']==1 and x['model_trained']]
   for low in (20,25,30,34,40,42,45,50):
    bands.append({'model':model,'price_scenario':cut,'min_predicted_probability_pct':low,
     **calc([x for x in r if x['predicted_win_pct']>=low])})
 write_csv(out/'stage103-win-probability-bands.csv',bands,
  ['model','price_scenario','min_predicted_probability_pct',*SUMMARY_COLS[6:]])
 write_csv(out/'stage103-exclusions.csv',issues,['date','course','time','model','reason'])
 status=[{'price_scenario':k[0],'price_status':k[1],'selection_count':v}
           for k,v in sorted(quote_audit.items())]
 write_csv(out/'stage103-price-coverage-audit.csv',status,
    ['price_scenario','price_status','selection_count'])
 fitted_test=sum(x['ml_scored_races'] for x in train if x['date']>=TEST_FROM)
 after=[x for x in rows if x['strategy']=='ML' and x['model_trained']]
 first_date=min((x['date'] for x in after),default=None)
 audit={
  'stage':VERSION,'window':{'train_from':TRAIN_FROM,'test':TEST_FROM+'..'+TEST_THROUGH},
  'test_objective':'WINNING PROBABILITY ONLY / ranked top 1 & 2 by model probability',
  'model_feature_schema':FEATURE_NAMES,'market_prices_in_model_features':False,
  'price_archive_loaded_only_after_all_picks_sealed':True,
  'betting_policy':'UNCONDITIONAL ML RANK 1 and RANK 2; no ROI/value/odds gate',
  'payout_cutoffs':CUTOFFS,'bookmaker':'William Hill only',
  'source_stage100_run':43089770857,'source_stage92_run':42833900420,
  'base_models':MODELS,'source_reconstruction_limitations':['Original A/B not certified exact original feature parity',
   'Original C retrospective replay','D/E research replay; current production models unmodified',
   'E V2 archived estimates may include training overlap with October 2026'],
  'first_ml_date':first_date,'ml_scored_test_model_races':fitted_test,
  'matched_primary_rank1_races':sum(x['matched_races'] for x in pairs if x['price_scenario']==CUTOFFS[0] and x['grouping']=='Overall' and x['rank_view']=='Rank 1'),
  'matched_local_rank1_races':sum(x['matched_races'] for x in pairs if x['price_scenario']==CUTOFFS[1] and x['grouping']=='Overall' and x['rank_view']=='Rank 1'),
  'historical_model_coverage':coverage,'price_archive_audit':dict(pricing_audit),
  'morning_price_coverage':status,'card_snapshot_commits':{day:(v.isoformat() if v else None) for day,v in commits.items()},
  'excluded_race_model_records':len(issues),
  'safety':['Card snapshots verified against historical Git, no future race results as features',
    'Each ML refit uses results exclusively from strictly earlier calendar days',
    'Model training and selection do not load odds/SP or optimise ROI',
    'Morning William Hill quotes are used exclusively after selections for ROI settlement',
    'Unpriced selections still count for strike rate and do not receive invented SP/price',
    'Irish local-price test excludes racecards not provably committed by local 09:45',
    'This is retrospective exploratory testing; same period has been inspected before',
    'Morning quotes and pre-race card were recorded but not guaranteed executable at book',
    'No live model, web app, files under data/, or historical DB were modified']
 }
 (out/'stage103-audit.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
 summary=['# Stage 103 — Winner-first ML (odds blind), 13 Oct–11 Nov 2026','',
  'All decisions rank by model-estimated winning probability. No market price or SP supplied to ML; no ROI or value gate.',
  '',f'**ML scored race/model cohorts:** {fitted_test}; **first fitted ML test day:** {first_date or "NONE"}.',
  f'**Strict paired morning ROI races:** 09:45 UTC {audit["matched_primary_rank1_races"]}; Irish 09:45 {audit["matched_local_rank1_races"]}.',
  '', '## Baseline vs ML Rank 1 at exactly matched 09:45 UTC bookmaker prices','','| Model | Matched races | Baseline wins | Baseline ROI | ML wins | ML ROI |',
  '|---|---:|---:|---:|---:|---:|']
 selected={x['model']:x for x in pairs if x['price_scenario']==CUTOFFS[0] and x['grouping']=='Overall' and x['rank_view']=='Rank 1'}
 for model in MODELS:
  z=selected.get(model,{})
  summary.append(f"| {model} | {z.get('matched_races',0)} | {z.get('baseline_wins','NA')} | {z.get('baseline_morning_roi_pct','NA')}% | {z.get('ml_wins','NA')} | {z.get('ml_morning_roi_pct','NA')}% |")
 summary.extend(['','## Read the outputs','',
  '- stage103-overall.csv: Rank 1, Rank 2 and both; win rate and priced-only ROI for each morning cutoff.',
  '- stage103-strict-paired-morning-prices.csv: fair original-versus-ML odds-matched comparisons, including by type and discipline.',
  '- stage103-all-ranked-selections.csv: every horse, final position, forecast %, odds timestamp, morning price or reason absent.',
  '- stage103-accuracy-summary.csv: mean full-field winner log-loss and Brier scores; complements winner strike rate.',
  '- stage103-price-coverage-audit.csv: missing prices and card provenance exclusions.',
  '', '## Limitations','',
  '- 09:45 UTC is 10:45 Dublin local before clocks move back on 25 October 2026.',
  '- 09:45 Irish local has stricter prior-card-provenance checks and may have a smaller sample.',
  '- Quoted prices are settlement evidence, not guaranteed obtainable stakes.',
  '- Without verified pre-off model parity, original A/B and E V2 historical results are diagnostic only.',
  '- Rank 1 win probability is model-estimated; it may be uncalibrated. Independent future validation is required.',
  '- All comparisons on the Oct–Nov study window are exploratory after reviewing Stage 102.',
  '- Historical model scores may already encode whatever pricing the original engine used, but NO NEW quote/SP is provided to Stage103 ML.',
  ''])
 (out/'README.md').write_text('\n'.join(summary),encoding='utf-8')
 print('\n'.join(summary[:22]),flush=True)
 return audit


def run(args):
 S101.audit_inputs(args.db,args.mapping)
 entries,issues,sourcecov=S101.collect_entries(TRAIN_FROM,TEST_THROUGH)
 if len(entries)<100:raise RuntimeError('Insufficient verified before-09:45 racecards/results; no fabricated model input')
 print('Stage103 before-09:45 UTC verified races:',len(entries),flush=True)
 abc=S101.card_models_original(args.db,args.mapping,entries,issues)
 de=S101.date_rows_de(args.db,args.ratings,entries,issues)
 group,rejects,source_counts=make_records(entries,abc,de)
 issues.extend(rejects)
 print('Stage103 odds-blind feature building complete; odds archive NOT LOADED.',flush=True)
 picks,training,accuracy=predict_all(group)
 if not any(x['strategy']=='ML' for x in picks):raise RuntimeError('No trained ML predictions; insufficient warm-up')
 print('Stage103 sealed',len(picks),'horse picks before any bookmaker odds were loaded.',flush=True)
 # Only now load the price file. No model is refit or reranked afterward.
 archive=S102.PriceArchive(ROOT).load()
 commits=card_commit_timestamps({p['record']['date'] for p in picks})
 selected,quoted=settle_after_selections(picks,archive,commits)
 audit=publish(selected,training,accuracy,issues,source_counts,archive.audit,quoted,commits,args.output)
 if audit['ml_scored_test_model_races']<=0 or audit['matched_primary_rank1_races']<=0:
  raise RuntimeError('Refuse successful status without trained ML and common-priced morning R1 races')
 print('Stage103 completed:',audit['matched_primary_rank1_races'],'matched morning UTC rank1 races.',flush=True)


def self_test():
 # Price, SP, winner, finish position MUST NOT affect the feature constructor.
 c={'age':7,'official_rating':129,'weight_lbs':160,'going':'Soft',
    'distance_f':16,'days_since_run':30,'draw':3}
 a=winner_features(c,.31,1,10,'Jumps','Handicap Hurdle',140.)
 b=winner_features({**c,'decimal_odds':1.5,'sp':500,'winner':True,'position':1,
                    'result':'WON','horse_price':4.5},.31,1,10,'Jumps','Handicap Hurdle',140.)
 assert a.shape==(len(FEATURE_NAMES),) and np.array_equal(a,b)
 assert np.isfinite(a).all()
 assert not any('odds' in f or 'price' in f or 'roi' in f or f=='sp' for f in FEATURE_NAMES)
 assert morning_cutoff('2026-10-14',CUTOFFS[1]).strftime('%H:%M')=='08:45'
 assert morning_cutoff('2026-11-11',CUTOFFS[1]).strftime('%H:%M')=='09:45'
 assert morning_cutoff('2026-10-14',CUTOFFS[0]).strftime('%H:%M')=='09:45'
 raw={'collected_at':'2026-11-01T09:30:00+00:00',
   'bookmaker_updated_at':'2026-11-01T09:29:00+00:00',
   'race_time':'2026-11-01T13:30:00+00:00','course':'Lingfield',
   'horse':'Fictional Runner (IRE)','bookmaker':'williamhill','decimal_odds':4.,'stale':False}
 archive=S102.PriceArchive(ROOT);archive.add_snapshot({'prices':[raw]},'synthetic')
 e={'date':'2026-11-01','time':'13:30','course':'Lingfield','cards':[{'horse':'Fictional Runner','race_time':'2026-11-01T13:30:00+00:00'}]}
 q,status=actual_morning_quote(archive,e,'Fictional Runner',morning_cutoff('2026-11-01',CUTOFFS[1]))
 assert q is not None and q['price']==4 and status=='PRICED'
 late={**raw,'collected_at':'2026-11-01T10:30:00+00:00'}
 late_archive=S102.PriceArchive(ROOT);late_archive.add_snapshot({'prices':[late]},'synthetic-late')
 assert actual_morning_quote(late_archive,e,'Fictional Runner',morning_cutoff('2026-11-01',CUTOFFS[1]))[0] is None
 from tempfile import TemporaryDirectory
 # End-to-end synthetic pure prediction + safe settlement + output artifact.
 allby=collections.defaultdict(list)
 for dnum in range(32):
  day=(dt.date(2026,10,8)+dt.timedelta(days=dnum)).isoformat()
  for race in range(2):
   e1={'date':day,'time':'13:30','course':'Lingfield','discipline':'AW','race_type':'Handicap',
    'key':(day,'LINGFIELD',f'13:{race:02d}'),
    'cards':[{'horse':f'Runner {i}','race_time':day+'T13:30:00+00:00'} for i in range(5)]}
   pp=np.array([.4,.3,.15,.10,.05]);xx=np.stack([winner_features({},pp[i],i,5,'AW','Handicap',None) for i in range(5)])
   allby[(day,'D_C')].append({'date':day,'model':'D_C','entry':e1,'x':xx,'base_p':pp,
    'base_order':[0,1,2,3,4],'sealed_y':[int(i==(race+dnum)%5) for i in range(5)],
    'sealed_positions':[1 if i==(race+dnum)%5 else 3 for i in range(5)]})
 pred,tra,acc=predict_all(allby)
 assert pred and any(x['strategy']=='ML' for x in pred)
 assert all(x['last_train_date']<x['record']['date'] for x in pred if x['last_train_date'])
 with TemporaryDirectory() as tmp:
  day=(dt.date(2026,10,8)+dt.timedelta(days=28)).isoformat()
  small=[x for x in pred if x['record']['date']==day]
  commit={day:dt.datetime.fromisoformat(day+'T08:00:00+00:00')}
  records,quoteaudit=settle_after_selections(small,archive,commit)
  assert records and all(r['morning_odds_decimal']=='' for r in records)
  assert calc(records)['selections']==len(records)
  outfile=pathlib.Path(tmp)/'test.csv';write_csv(outfile,records,SELECTION_COLS)
  assert outfile.is_file() and outfile.stat().st_size>100
 print('Stage103 PASS: odds/outcome feature isolation, Dublin DST, future quote rejection, chronological walk-forward, full settlement',flush=True)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 sub=parser.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test')
 sim=sub.add_parser('compare')
 sim.add_argument('--db',required=True);sim.add_argument('--mapping',required=True)
 sim.add_argument('--ratings',required=True);sim.add_argument('--output',default='reports/stage103/output')
 args=parser.parse_args()
 if args.cmd=='self-test':self_test()
 else:run(args)

if __name__=='__main__':main()
