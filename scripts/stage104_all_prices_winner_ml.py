#!/usr/bin/env python3
"""Stage104: winner-first ML with ALL historically OBSERVED pre-morning bookmaker quotes.

Research replay: 13 Oct–11 Nov 2026, UK/IE, training begins 8 Oct.
Three independent ranking strategies per underlying model: frozen original,
Stage103 odds-blind ML, and THIS stage's all-bookmaker winner-focused ML.
No ROI/value/odds threshold is used to decide a ranked horse; fit maximizes
classification likelihood of past winners. Morning William Hill (WH) price is
joined to the locked picks solely for retrospective equal-stake ROI reporting.

Price scenarios: 09:45 Irish local time (PRIMARY) and 09:45 UTC (Stage102/103
comparable). A race is excluded if the frozen card was committed after its
cutoff, had started by then, or lacked sufficient timestamp-valid bookmaker
quotes. Current-day outcomes are not used for current-day predictions.

Limitations: sources are historical reconstructions; archive timestamp claims
are not proof of an executable bookmaker quote; E V2 may have Oct training
leakage in its archived source; original A/B feature parity is unverified.
Frozen production app, models, odds archive and HPR ledger are not modified.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,json,math,pathlib,statistics,sys, tempfile
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage101_all_models_compare as S101
import stage102_roi_ml_simulation as S102
import stage103_winner_first_ml as S103

TEST_FROM=S103.TEST_FROM
TEST_END=S103.TEST_THROUGH
TRAIN_FROM=S103.TRAIN_FROM
CUTOFFS=('MORNING_0945_IRELAND','MORNING_0945_UTC_STAGE102')
MODELS=tuple(S103.MODELS)
MAX_AGE_MIN=180
MIN_HISTORY=25
MIN_FIELD_COVERAGE=.70
# Common individual-bookmaker slots; the aggregate statistics below include EVERY
# independently observed bookmaker, including those not in this fixed list.
BRANDS=('williamhill','paddypower','skybet','bet365','coral','ladbrokes',
        'betfred','betwayuk','unibet','boylesports','betfair','888sport')
MARKET_FEATURES=(
 'market_median_log','market_min_log','market_max_log','market_q25_log',
 'market_q75_log','market_price_dispersion','market_books_count_log',
 'market_has_price','market_williamhill_present','market_wh_vs_median_log',
 'market_median_implied_prob','market_normalized_implied_prob',
 'market_probability_rank_fraction','market_vs_field_favourite_log',
 'market_quote_mean_age_180','market_quote_youngest_age_180',
 'market_has_multiple_bookmakers','market_bookmakers_coverage_fraction',
 'market_log_price_movement_mean','market_share_shorten','market_share_drift',
 'market_historical_quote_count_log',
) + tuple(f'book_{x}_log' for x in BRANDS) + tuple(f'book_{x}_present' for x in BRANDS)
FEATURES=tuple(S103.FEATURE_NAMES)+MARKET_FEATURES
STRATEGIES=('BASE','ODDS_BLIND_ML','ALL_BOOKS_ML')

OUTPUT_COLS=['date','course','off_utc','discipline','race_type','race_key','model',
 'price_scenario','strategy','rank','horse','predicted_win_pct','winner','finish_position',
 'model_trained','prior_training_races','last_train_date',
 'bookmaker_count_race','bookmaker_count_horse','runner_price_coverage',
 'selection_cutoff_utc','feature_price_min','feature_price_median','feature_price_max',
 'feature_quote_newest_utc','quote_available','morning_wh_price_decimal',
 'wh_quote_timestamp','wh_quote_age_min','pnl_1u_at_morning_wh','price_status',
 'model_version','limitations']
SUMMARY_COLS=['model','price_scenario','strategy','rank_view','discipline','race_type',
              'selection_count','wins','strike_rate_pct','priced_bets','priced_wins',
              'morning_wh_roi_pct','morning_wh_profit_units','missing_wh_prices',
              'first_selection_date','last_selection_date']
PAIRED_COLS=['model','price_scenario','grouping','discipline','race_type','rank_view',
             'races','bets_per_strategy','base_winners','base_strike_rate_pct','base_roi_pct',
             'blind_ml_winners','blind_ml_strike_rate_pct','blind_ml_roi_pct',
             'all_books_ml_winners','all_books_ml_strike_rate_pct','all_books_ml_roi_pct',
             'all_books_vs_blind_wins','all_books_vs_blind_strike_pp','all_books_vs_blind_roi_pp']


def num(x):return S101.as_float(x)
def nrm(x):return S101.norm(x)
def write_csv(path,rows,fields):return S101.write_csv(path,rows,fields)
def rkey(entry):return (entry['date'],S102.course(entry['course']),entry['time'])


class FullBookArchive(S102.PriceArchive):
 """Uses existing Stage102 parser and git-as-of provenance checks, WITHOUT
 its William-Hill-only collection filter. No HTTP/API fetches are made.
 """
 def __init__(self,root,allowed_races=None):
  super().__init__(root)
  self.allowed_races=allowed_races

 def add_snapshot(self,obj,origin,committed=None):
  snapshots=obj if isinstance(obj,list) else [obj]
  for snap in snapshots:
   if not isinstance(snap,dict):continue
   for row in snap.get('prices',[]):
    quote=S102.quote_object(row,origin)
    if quote is None:
     self.audit['malformed_or_stale']+=1;continue
    k=quote['key']
    if not TRAIN_FROM<=k[0]<=TEST_END:continue
    if self.allowed_races is not None and k[:3] not in self.allowed_races:continue
    # Only quotes within the union of the TWO morning as-of windows can be used.
    # This avoids retaining huge afternoon/evening historical snapshots in RAM.
    local=S103.morning_cutoff(k[0],CUTOFFS[0])
    utc=S103.morning_cutoff(k[0],CUTOFFS[1])
    earliest=min(local,utc)-dt.timedelta(minutes=MAX_AGE_MIN)
    latest=max(local,utc)
    if not earliest<=quote['collected']<=latest:
     self.audit['outside_both_morning_cutoffs']+=1;continue
    quote['committed']=committed
    self.by_key[k].append(quote)
    self.audit['all_book_quotes_loaded']+=1
    self.audit['book_'+quote['book']]+=1


def observed_field(archive,entry,cutoff):
 """For each horse, return the last eligible quote PER BOOK (as-of only)."""
 off=S102.off_at(entry)
 if not off or off<=cutoff:return None,'RACE_NOT_AFTER_DECISION'
 field=[];histories=[];books=set();total=0
 for c in entry['cards']:
  k=(entry['date'],S102.course(entry['course']),off.strftime('%H:%M'),nrm(c['horse']))
  last={};history=collections.defaultdict(list)
  for q in archive.by_key.get(k,()):
   age=(cutoff-q['collected']).total_seconds()
   if not 0<=age<=MAX_AGE_MIN*60:continue
   if q['updated'] is not None and q['updated']>cutoff:continue
   if q['committed'] is not None and q['committed']>cutoff:continue
   history[q['book']].append(q)
   prev=last.get(q['book'])
   if prev is None or (q['collected'],q['updated'] or q['collected']) > (prev['collected'],prev['updated'] or prev['collected']):
    last[q['book']]=q
  field.append(last);histories.append(dict(history));books.update(last);total+=len(last)
 if sum(bool(x) for x in field)<math.ceil(MIN_FIELD_COVERAGE*len(field)):
  return None,'INCOMPLETE_ALL_BOOK_FIELD'
 return {'field':field,'histories':histories,'books':sorted(books),'quotes':total,
         'fraction':sum(bool(x) for x in field)/len(field)},'VALID'


def market_matrix(field,cutoff):
 """Every eligible quoted bookmaker contributes to the consensus features.
 Extra named bookmaker slots supplement these summaries. NO SP/outcomes.
 """
 allby=field['field'];n=len(allby)
 mid=[float(np.median([v['price'] for v in books.values()])) if books else None for books in allby]
 impl=[1/v if v is not None else 0. for v in mid]
 full=sum(impl)
 normimp=[v/full if full else 0. for v in impl]
 market_order=sorted(range(n),key=lambda i:(-normimp[i],i))
 ranks={i:k for k,i in enumerate(market_order)}
 favourite=min((v for v in mid if v is not None),default=1.)
 values=[]
 for i,quotes in enumerate(allby):
  trajectory=field['histories'][i]
  movements=[]
  for past in trajectory.values():
   if len(past)<2:continue
   past=sorted(past,key=lambda q:(q['collected'],q['updated'] or q['collected']))
   movements.append(math.log(past[-1]['price']/past[0]['price']))
  all_snapshots=sum(len(series) for series in trajectory.values())
  vals=sorted(q['price'] for q in quotes.values())
  if vals:
   med=float(np.median(vals));mn=float(vals[0]);mx=float(vals[-1])
   q25,q75=[float(x) for x in np.percentile(vals,[25,75])]
   age=[(cutoff-q['collected']).total_seconds()/60 for q in quotes.values()]
   std=float(np.std(np.log(vals))) if len(vals)>1 else 0.
  else:
   med=mn=mx=q25=q75=0.;age=[];std=0.
  wh=quotes.get('williamhill')
  v=[
   math.log(med) if med else 0.,math.log(mn) if mn else 0.,
   math.log(mx) if mx else 0.,math.log(q25) if q25 else 0.,
   math.log(q75) if q75 else 0.,std,
   math.log1p(len(vals)),float(bool(vals)),float(wh is not None),
   math.log(wh['price']/med) if med and wh else 0.,
   1/med if med else 0.,normimp[i],ranks[i]/max(1,n-1),
   math.log(med/favourite) if med and favourite else 0.,
   statistics.mean(age)/180 if age else 0.,min(age)/180 if age else 0.,
   float(len(vals)>=2),len(vals)/max(1,len(field['books'])),
   statistics.mean(movements) if movements else 0.,
   sum(x<-.001 for x in movements)/len(movements) if movements else 0.,
   sum(x>.001 for x in movements)/len(movements) if movements else 0.,
   math.log1p(all_snapshots)
  ]
  v += [math.log(quotes[b]['price']) if b in quotes else 0. for b in BRANDS]
  v += [float(b in quotes) for b in BRANDS]
  values.append(v)
 result=np.asarray(values,dtype=float)
 if result.shape!=(n,len(MARKET_FEATURES)) or not np.isfinite(result).all():
  raise RuntimeError('Invalid bookmaker market feature vectors')
 return result


def populate_records(dated,archive,commits):
 """Strict as-of record build. Historical target outcomes remain SEALED;
 only odds records collected before the chosen morning cutoff enter X.
 """
 out=collections.defaultdict(list);audit=collections.Counter()
 race_cache={}
 for (day,model),records in sorted(dated.items()):
  for rec in records:
   e=rec['entry'];rk=e['key']
   for scenario in CUTOFFS:
    ck=(rk,scenario)
    if ck not in race_cache:
     cutoff=S103.morning_cutoff(day,scenario)
     commit=commits.get(day)
     if commit is None:race_cache[ck]=(None,'CARD_GIT_PROVENANCE_MISSING',cutoff)
     elif commit>cutoff:race_cache[ck]=(None,'CARD_SNAPSHOT_AFTER_CUTOFF',cutoff)
     else:
      obs,status=observed_field(archive,e,cutoff)
      race_cache[ck]=(obs,status,cutoff)
    obs,status,cutoff=race_cache[ck]
    if obs is None:
     audit[scenario+'__'+status]+=1;continue
    market=market_matrix(obs,cutoff)
    features=np.concatenate([rec['x'],market],axis=1)
    assert features.shape[1]==len(FEATURES)
    out[(day,model,scenario)].append({**rec,'x_market':features,
      'observations':obs,'cutoff':cutoff,'scenario':scenario})
    audit[scenario+'__eligible']+=1
 # Record market coverage by RACE rather than model and keep observable dates.
 audit['unique_eligible_race_scenarios']=sum(v[0] is not None for v in race_cache.values())
 audit['unique_rejected_race_scenarios']=sum(v[0] is None for v in race_cache.values())
 return out,dict(audit),race_cache


def fit_past(history):
 if len(history)<MIN_HISTORY:return None,'INSUFFICIENT_PRIOR_MARKET_RACES'
 x=np.concatenate([r['x_market'] for r in history],axis=0)
 y=np.asarray([int(z) for r in history for z in r['sealed_y']],dtype=int)
 if len(set(y.tolist()))!=2:return None,'TRAINING_HAS_ONE_CLASS'
 model=make_pipeline(StandardScaler(),LogisticRegression(C=.15,max_iter=350,solver='lbfgs'))
 model.fit(x,y)
 return model,'TRAINED_PRIOR_DATES_ONLY'


def predict_market(dated,blind_picks):
 """No race is trained using its own outcome or another race on its date."""
 reference={}
 for p in blind_picks:
  if p['strategy']=='BASE':tag='BASE'
  elif p['strategy']=='ML':tag='ODDS_BLIND_ML'
  else:continue
  rec=p['record']
  reference[(rec['date'],rec['model'],rec['entry']['key'],tag,p['rank'])]=p

 selection=[];train=[];accuracy=[]
 for model in MODELS:
  for scenario in CUTOFFS:
   history=[]
   days=sorted({day for day,m,sc in dated if m==model and sc==scenario})
   for day in days:
    assert all(x['date']<day for x in history),'Same-day training leakage'
    estimator,state=fit_past(history)
    rows=dated[(day,model,scenario)]
    pending=[]
    for rec in rows:
     pp=S103.probability_prediction(estimator,{'x':rec['x_market']}) if estimator is not None else None
     # Independent market-only favourite baseline, using the consensus of
     # bookmaker prices observed strictly before the SAME selection cutoff.
     odds=[float(np.median([q['price'] for q in books.values()])) if books else float('inf')
           for books in rec['observations']['field']]
     market_implied=[1/x if math.isfinite(x) else 0. for x in odds]
     denom=sum(market_implied)
     for rank,i in enumerate(sorted(range(len(odds)),key=lambda i:(odds[i],i))[:2],1):
      if not math.isfinite(odds[i]):continue
      pending.append((rec,'MARKET_FAVOURITE',rank,i,market_implied[i]/denom if denom else 0.,False))
     for tag in ('BASE','ODDS_BLIND_ML'):
      for rank in (1,2):
       src=reference.get((day,model,rec['entry']['key'],tag,rank))
       if src is None:continue
       if tag=='ODDS_BLIND_ML' and not src['trained']:continue
       pending.append((rec,tag,rank,src['i'],float(src['probability']),bool(src['trained'])))
     if pp is not None:
      for rank,i in enumerate(sorted(range(len(pp)),key=lambda i:(-float(pp[i]),i))[:2],1):
       pending.append((rec,'ALL_BOOKS_ML',rank,i,float(pp[i]),True))
      pending_true=(rec,pp)
      # Accuracy is read only after every prediction for the date is complete.
      accuracy.append(pending_true)
    # All selections for this date are now sealed. Settle ONLY here.
    for rec,tag,rank,i,p,trained in pending:
     if day<TEST_FROM:continue
     selection.append(settle(rec,tag,rank,i,p,trained,len(history),
                             max((r['date'] for r in history),default='')))
    train.append({'date':day,'model':model,'scenario':scenario,'market_races':len(rows),
      'prior_market_training_races':len(history),'ml_trained':bool(estimator),
      'status':state,'last_prior_date':max((r['date'] for r in history),default='')})
    history.extend(rows)
    if day>=TEST_FROM:
     print('Stage104',day,model,scenario,'races',len(rows),'trained',bool(estimator),flush=True)
 accur=[]
 for rec,pp in accuracy:
  # No score is computed from outcomes; this accuracy report uses labels post-prediction.
  yi=next(i for i,y in enumerate(rec['sealed_y']) if y)
  accur.append({'date':rec['date'],'model':rec['model'],'scenario':rec['scenario'],
   'discipline':rec['entry']['discipline'],'race_type':rec['entry']['race_type'],
   'winner_probability_pct':round(100*float(pp[yi]),4),
   'winner_log_loss':round(-math.log(max(1e-9,float(pp[yi]))),5),
   'whole_field_brier':round(float(sum((float(x)-float(i==yi))**2 for i,x in enumerate(pp))),5)})
 return selection,train,accur


def settle(rec,strategy,rank,i,p,trained,prior,last):
 e=rec['entry'];c=e['cards'][i];scenario=rec['scenario'];cutoff=rec['cutoff']
 horse=c['horse'];all_quotes=rec['observations']['field'][i]
 wh=all_quotes.get('williamhill')
 # Note: each quote used as a *feature* was eligible at the same morning cutoff.
 price=wh['price'] if wh else None
 won=int(rec['sealed_y'][i])
 vals=[z['price'] for z in all_quotes.values()]
 return {'date':e['date'],'course':e['course'],'off_utc':e['time'],
  'discipline':e['discipline'],'race_type':e['race_type'],
  'race_key':'|'.join(map(str,e['key'])),'model':rec['model'],
  'price_scenario':scenario,'strategy':strategy,'rank':rank,'horse':horse,
  'predicted_win_pct':round(100*p,4),'winner':won,
  'finish_position':rec['sealed_positions'][i],
  'model_trained':bool(trained),'prior_training_races':prior,'last_train_date':last,
  'bookmaker_count_race':len(rec['observations']['books']),
  'bookmaker_count_horse':len(all_quotes),
  'runner_price_coverage':round(rec['observations']['fraction'],4),
  'selection_cutoff_utc':cutoff.isoformat(),
  'feature_price_min':min(vals) if vals else '',
  'feature_price_median':float(np.median(vals)) if vals else '',
  'feature_price_max':max(vals) if vals else '',
  'feature_quote_newest_utc':max((q['collected'] for q in all_quotes.values()),default=None).isoformat() if vals else '',
  'quote_available':bool(wh),'morning_wh_price_decimal':price or '',
  'wh_quote_timestamp':wh['collected'].isoformat() if wh else '',
  'wh_quote_age_min':round((cutoff-wh['collected']).total_seconds()/60,2) if wh else '',
  'pnl_1u_at_morning_wh':round(price-1 if won else -1.,6) if wh else '',
  'price_status':'VALID_WILLIAM_HILL' if wh else 'NO_WILLIAM_HILL_MORNING_QUOTE',
  'model_version':'Stage104 all-bookmarket winner ML / Stage103 source baselines',
  'limitations':'HISTORICAL_RESEARCH_ONLY;MORNING_QUOTE_NOT_GUARANTEED_EXECUTABLE'}


def stats(records):
 n=len(records);wins=sum(int(r['winner']) for r in records)
 priced=[r for r in records if r['quote_available']]
 pnl=sum(float(r['pnl_1u_at_morning_wh']) for r in priced)
 return {'selection_count':n,'wins':wins,
  'strike_rate_pct':round(wins/n*100,3) if n else None,
  'priced_bets':len(priced),'priced_wins':sum(int(r['winner']) for r in priced),
  'morning_wh_roi_pct':round(100*pnl/len(priced),3) if priced else None,
  'morning_wh_profit_units':round(pnl,3),
  'missing_wh_prices':n-len(priced),
  'first_selection_date':min((r['date'] for r in records),default=''),
  'last_selection_date':max((r['date'] for r in records),default='')}


def grouped_summary(rows,fields):
 by=collections.defaultdict(list)
 for r in rows:
  if r['date']<TEST_FROM or r['strategy']=='ODDS_BLIND_ML' and not r['model_trained']:continue
  for kind in ('Rank '+str(r['rank']),'Both ranks'):
   row={**r,'rank_view':kind}
   by[tuple(row.get(k,'') for k in fields)].append(row)
 return [{**dict(zip(fields,k)),**stats(a)} for k,a in sorted(by.items())]


def comparisons(rows):
 """Require BASE, blind ML and all-book ML picks of the same rank/race AND
 valid William Hill price for EACH chosen horse. No selective cohort mixing.
 """
 by=collections.defaultdict(dict)
 for r in rows:
  if r['date']<TEST_FROM:continue
  by[(r['model'],r['price_scenario'],r['race_key'])][(r['strategy'],r['rank'])]=r
 pools=collections.defaultdict(lambda:{tag:[] for tag in STRATEGIES})
 races=collections.defaultdict(set)
 for (model,scenario,raceid),group in by.items():
  for view,ranks in [('Rank 1',(1,)),('Rank 2',(2,)),('Both ranks',(1,2))]:
   parts={tag:[group.get((tag,k)) for k in ranks] for tag in STRATEGIES}
   if any(any(r is None for r in arr) for arr in parts.values()):continue
   if any(not r['quote_available'] for arr in parts.values() for r in arr):continue
   sample=parts['BASE'][0]
   grouping=(('Overall','All','All'),('Discipline',sample['discipline'],sample['discipline']),
             ('Race type',sample['discipline'],sample['race_type']))
   if sample['discipline']!='Jumps' or 'HANDICAP' not in sample['race_type'].upper():
    grouping+= (('NH handicap excluded', 'All allowed', 'All allowed'),)
   for kind,disc,typ in grouping:
    k=(model,scenario,kind,disc,typ,view)
    for tag in STRATEGIES:pools[k][tag].extend(parts[tag])
    races[k].add(raceid)
 res=[]
 for k,pool in sorted(pools.items()):
  model,sc,kind,disc,typ,view=k
  x={tag:stats(pool[tag]) for tag in STRATEGIES}
  b,z,m=(x[t] for t in STRATEGIES)
  assert b['selection_count']==z['selection_count']==m['selection_count']
  res.append({'model':model,'price_scenario':sc,'grouping':kind,'discipline':disc,
   'race_type':typ,'rank_view':view,'races':len(races[k]),
   'bets_per_strategy':b['selection_count'],
   'base_winners':b['wins'],'base_strike_rate_pct':b['strike_rate_pct'],'base_roi_pct':b['morning_wh_roi_pct'],
   'blind_ml_winners':z['wins'],'blind_ml_strike_rate_pct':z['strike_rate_pct'],'blind_ml_roi_pct':z['morning_wh_roi_pct'],
   'all_books_ml_winners':m['wins'],'all_books_ml_strike_rate_pct':m['strike_rate_pct'],'all_books_ml_roi_pct':m['morning_wh_roi_pct'],
   'all_books_vs_blind_wins':m['wins']-z['wins'],
   'all_books_vs_blind_strike_pp':round(m['strike_rate_pct']-z['strike_rate_pct'],3),
   'all_books_vs_blind_roi_pp':round(m['morning_wh_roi_pct']-z['morning_wh_roi_pct'],3)})
 return res


def favourite_comparison(rows):
 """On the SAME race and valid morning WH prices, compare all-books ML
 against simply selecting the lowest-odds horse by bookmaker consensus."""
 by=collections.defaultdict(dict)
 for r in rows:
  if r['date']<TEST_FROM:continue
  by[(r['model'],r['price_scenario'],r['race_key'])][(r['strategy'],r['rank'])]=r
 pool=collections.defaultdict(lambda:{'ml':[],'fav':[],'races':set()})
 for (model,sc,raceid),group in by.items():
  for label,ranks in (('Rank 1',(1,)),('Rank 2',(2,)),('Both ranks',(1,2))):
   ml=[group.get(('ALL_BOOKS_ML',rank)) for rank in ranks]
   fav=[group.get(('MARKET_FAVOURITE',rank)) for rank in ranks]
   if any(x is None or not x['quote_available'] for x in ml+fav):continue
   first=ml[0]
   grps=(('Overall','All','All'),('Discipline',first['discipline'],first['discipline']),
         ('Race type',first['discipline'],first['race_type']))
   if first['discipline']!='Jumps' or 'HANDICAP' not in first['race_type'].upper():
    grps+=(('NH handicap excluded','All allowed','All allowed'),)
   for kind,disc,typ in grps:
    k=(model,sc,kind,disc,typ,label)
    pool[k]['ml'].extend(ml);pool[k]['fav'].extend(fav);pool[k]['races'].add(raceid)
 report=[]
 for (model,sc,kind,disc,typ,label),a in sorted(pool.items()):
  m=stats(a['ml']);f=stats(a['fav'])
  report.append({'model':model,'price_scenario':sc,'grouping':kind,
   'discipline':disc,'race_type':typ,'rank_view':label,'matched_races':len(a['races']),
   'bets_per_strategy':m['selection_count'],
   'all_books_ml_winners':m['wins'],'all_books_ml_strike_rate_pct':m['strike_rate_pct'],
   'all_books_ml_roi_pct':m['morning_wh_roi_pct'],
   'market_favourite_winners':f['wins'],'market_favourite_strike_rate_pct':f['strike_rate_pct'],
   'market_favourite_roi_pct':f['morning_wh_roi_pct'],
   'ml_extra_winners':m['wins']-f['wins'],
   'ml_strike_change_pp':round(m['strike_rate_pct']-f['strike_rate_pct'],3),
   'ml_roi_change_pp':round(m['morning_wh_roi_pct']-f['morning_wh_roi_pct'],3)})
 return report


def report(rows,train,accuracy,record_issues,sourcecounts,quoteaudit,validity,out):
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 rows=sorted(rows,key=lambda r:(r['date'],r['off_utc'],r['model'],r['price_scenario'],r['strategy'],r['rank']))
 write_csv(out/'stage104-all-ranked-selections.csv',rows,OUTPUT_COLS)
 datasets=[
  ('stage104-overall.csv',['model','price_scenario','strategy','rank_view']),
  ('stage104-by-discipline.csv',['model','price_scenario','strategy','rank_view','discipline']),
  ('stage104-by-race-type.csv',['model','price_scenario','strategy','rank_view','discipline','race_type']),
  ('stage104-by-day.csv',['date','model','price_scenario','strategy','rank_view','discipline'])]
 for fname,fields in datasets:
  data=grouped_summary(rows,fields)
  write_csv(out/fname,data,fields+[x for x in SUMMARY_COLS if x not in fields])
 paired=comparisons(rows)
 write_csv(out/'stage104-three-way-strict-matched.csv',paired,PAIRED_COLS)
 fav=favourite_comparison(rows)
 write_csv(out/'stage104-vs-market-favourite.csv',fav,
           ['model','price_scenario','grouping','discipline','race_type','rank_view',
            'matched_races','bets_per_strategy','all_books_ml_winners',
            'all_books_ml_strike_rate_pct','all_books_ml_roi_pct',
            'market_favourite_winners','market_favourite_strike_rate_pct',
            'market_favourite_roi_pct','ml_extra_winners','ml_strike_change_pp','ml_roi_change_pp'])
 write_csv(out/'stage104-train-coverage.csv',train,
           ['date','model','scenario','market_races','prior_market_training_races','ml_trained','status','last_prior_date'])
 write_csv(out/'stage104-probability-accuracy.csv',accuracy,
           ['date','model','scenario','discipline','race_type','winner_probability_pct','winner_log_loss','whole_field_brier'])
 pcounts=collections.Counter()
 for r in rows:pcounts[(r['price_scenario'],r['strategy'],r['price_status'])]+=1
 write_csv(out/'stage104-price-coverage.csv',
           [{'scenario':sc,'strategy':st,'status':status,'selections':count} for (sc,st,status),count in sorted(pcounts.items())],
           ['scenario','strategy','status','selections'])
 write_csv(out/'stage104-exclusions.csv',record_issues,
           ['date','course','time','model','reason'])
 audit={'stage':'stage104-v1-winner-first-all-bookmaker-morning',
  'test':'2026-10-13..2026-11-11','warmup':'2026-10-08..2026-10-12',
  'objective':'MAXIMIZE_HORSE_WIN_PROBABILITY_NOT_ROI',
  'model_features':list(FEATURES),'includes_all_available_bookmaker_quotes':True,
  'individual_bookmaker_slots':list(BRANDS),'aggregate_includes_all_other_bookmakers':True,
  'market_minimum_runner_coverage':MIN_FIELD_COVERAGE,'quote_max_age_minutes':MAX_AGE_MIN,
  'price_cutoffs':list(CUTOFFS),'settlement':'timestamped morning William Hill recorded quote',
  'no_sp_input':True,'no_roi_or_value_gate':True,'no_future_quotes_as_model_input':True,
  'no_same_day_outcomes_in_training':True,'frozen_production_unchanged':True,
  'models':list(MODELS),'source_stage100_run_id':43089770857,
  'source_stage92_run_id':42833900420,
  'market_ml_test_picks':sum(1 for x in rows if x['strategy']=='ALL_BOOKS_ML'),
  'valid_3way_paired_rank1_races_irish':sum(x['races'] for x in paired if x['grouping']=='Overall' and x['rank_view']=='Rank 1' and x['price_scenario']==CUTOFFS[0]),
  'valid_3way_paired_rank1_races_utc':sum(x['races'] for x in paired if x['grouping']=='Overall' and x['rank_view']=='Rank 1' and x['price_scenario']==CUTOFFS[1]),
  'valid_market_favourite_rank1_races_irish':sum(x['matched_races'] for x in fav if x['grouping']=='Overall' and x['rank_view']=='Rank 1' and x['price_scenario']==CUTOFFS[0]),
  'price_audit':dict(quoteaudit),'market_coverage':dict(validity),'historical_model_coverage':sourcecounts,
  'excluded_model_races':len(record_issues),
  'limitations':['Already inspected Stage102/103 period; retrospective exploratory research, not independent test',
    'A/B original feature parity not certified; C and E research replay; Stage92 E V2 possible prior overlap',
    'Data archive collected_at is source-reported; not a guarantee quote executable at the bookmaker',
    'Historical cards before 09:45 UTC; Irish-local earlier cutoff requires card commit <= earlier time',
    'Quote freshness capped at 180 minutes; more recent than cutoff never used',
    'Differences in full-market coverage reduce comparable race sample',
    'Morning WH ROI is estimated from quotes, not guaranteed obtainable live returns']}
 (out/'stage104-audit.json').write_text(json.dumps(audit,indent=2,default=str)+'\n',encoding='utf-8')
 lines=['# Stage104 — All-bookmaker WINNER-FIRST ML: 13 October – 11 November 2026','',
  '**Goal:** pick most winners, not maximize ROI. Prices observed by selection time are available to ML; final SP and winners never are.',
  '**Primary:** 09:45 Dublin local; **Stage103 comparator:** 09:45 UTC. Morning William Hill used exclusively for payout.',
  f'**Ranked ML selections:** {audit["market_ml_test_picks"]}; **same-3-way-priced morning Rank 1:** Dublin {audit["valid_3way_paired_rank1_races_irish"]}; UTC {audit["valid_3way_paired_rank1_races_utc"]}.','',
  '## Matched Rank 1: Original vs odds-blind Stage103 vs all-book Stage104','',
  '| Model | Irish local races | Original wins / ROI | Odds blind wins / ROI | All books wins / ROI |',
  '|---|---:|---|---|---|']
 m={x['model']:x for x in paired if x['grouping']=='Overall' and x['rank_view']=='Rank 1' and x['price_scenario']==CUTOFFS[0]}
 for k in MODELS:
  x=m.get(k,{})
  lines.append(f'| {k} | {x.get("races",0)} | {x.get("base_winners","NA")} / {x.get("base_roi_pct","NA")}% | {x.get("blind_ml_winners","NA")} / {x.get("blind_ml_roi_pct","NA")}% | {x.get("all_books_ml_winners","NA")} / {x.get("all_books_ml_roi_pct","NA")}% |')
 lines+=['','## How to read','',
  '- stage104-three-way-strict-matched.csv: 3 strategies only where ALL have valid morning WH odds, same races; by discipline and race type.',
  '- stage104-overall.csv: all ranked eligible market-scored races, strike rate over all selections; ROI only where WH quote exists.',
  '- stage104-vs-market-favourite.csv: same-race winner-first ML vs the consensus shortest-priced horse (pure odds baseline).',
  '- stage104-by-discipline.csv and stage104-by-race-type.csv: Rank 1, Rank 2, both.',
  '- stage104-all-ranked-selections.csv: individual chosen horse, odds timestamp, bookmaker coverage, winner, position, payout.',
  '- stage104-train-coverage.csv: training history and training status on each date; no same-day outcome leakage.',
  '- stage104-price-coverage.csv: missing WH prices. Missing returns NEVER fabricated.',
  '- stage104-audit.json: as-of checks, price freshness, feature list and strict baseline limitations.','',
  '## Cautions','',
  '- Repeated tests on the same dates can overfit model choice and subgroup selection.',
  '- Earlier Stage103 and this study share source score reconstruction weaknesses.',
  '- Small profitable subgroup ROI should not be accepted as predictive without later untouched races.',
  '']
 (out/'README.md').write_text('\n'.join(lines),encoding='utf-8')
 print('\n'.join(lines[:21]),flush=True)
 return audit


def run(args):
 S101.audit_inputs(args.db,args.mapping)
 entries,issues,sourcecov=S101.collect_entries(TRAIN_FROM,TEST_END)
 if len(entries)<100:raise RuntimeError('Insufficient verified historic pre-off cards/results')
 abc=S101.card_models_original(args.db,args.mapping,entries,issues)
 de=S101.date_rows_de(args.db,args.ratings,entries,issues)
 original,excluded,counts=S103.make_records(entries,abc,de)
 issues.extend(excluded)
 print('Stage104 generated original full-field cards; now loading historical all-book quotes.',flush=True)
 # Stage103 baseline trained on identical initial full race/card history.
 blind_picks,blind_train,blind_acc=S103.predict_all(original)
 allowed={(e['date'],S102.course(e['course']),e['time']) for e in entries}
 archive=FullBookArchive(ROOT,allowed).load()
 commits=S103.card_commit_timestamps({e['date'] for e in entries})
 market,coverage,racecache=populate_records(original,archive,commits)
 print('Stage104 market data',coverage,flush=True)
 if not market:raise RuntimeError('No strictly observed morning all-bookmaker fields; cannot report comparison')
 selections,training,accuracy=predict_market(market,blind_picks)
 audit=report(selections,training,accuracy,issues,counts,archive.audit,coverage,args.output)
 if audit['market_ml_test_picks']<50:raise RuntimeError('Too few fitted market ML picks to justify results')
 if audit['valid_3way_paired_rank1_races_utc']<=0 and audit['valid_3way_paired_rank1_races_irish']<=0:
  raise RuntimeError('No fully matched 3-way morning-price comparison; refuse false-success')
 print('Stage104 PASS: independently matched models, ROI only at WH morning price.',flush=True)


def self_test():
 """Run integrated synthetic test without network access or production writes."""
 date='2026-10-18';utc=S103.morning_cutoff(date,CUTOFFS[1]);local=S103.morning_cutoff(date,CUTOFFS[0])
 assert utc.strftime('%H:%M')=='09:45' and local.strftime('%H:%M')=='08:45'
 entries={'date':date,'time':'13:30','course':'Lingfield','cards':[
  {'horse':f'Runner {i}','race_time':date+'T13:30:00Z'} for i in range(6)]}
 ar=FullBookArchive(ROOT)
 for i in range(6):
  for bk in ('williamhill','paddypower','skybet','bet365'):
   quote={'collected_at':date+'T08:15:00+00:00','bookmaker_updated_at':date+'T08:14:00+00:00',
    'race_time':date+'T13:30:00Z','course':'Lingfield','horse':f'Runner {i}',
    'bookmaker':bk,'decimal_odds':2+i*.7,'stale':False}
   ar.add_snapshot({'prices':[quote]},'synthetic')
 # Add one post-cutoff extreme quote; must NOT affect earlier market features.
 bad={'collected_at':date+'T11:15:00+00:00','bookmaker_updated_at':date+'T11:14:00+00:00',
    'race_time':date+'T13:30:00Z','course':'Lingfield','horse':'Runner 0',
    'bookmaker':'paddypower','decimal_odds':888.,'stale':False}
 ar.add_snapshot({'prices':[bad]},'synthetic-post-off-cutoff')
 f,status=observed_field(ar,entries,local)
 assert status=='VALID' and len(f['books'])==4 and f['fraction']==1.
 assert f['field'][0]['paddypower']['price']==2.
 feat=market_matrix(f,local)
 assert feat.shape==(6,len(MARKET_FEATURES)) and np.isfinite(feat).all()
 # No withheld winner field is present in any market feature constructor.
 base=np.stack([S103.winner_features({'sp':999,'position':1},.2,i,6,'AW','Handicap',None) for i in range(6)])
 x=np.concatenate([base,feat],axis=1)
 assert x.shape==(6,len(FEATURES))
 assert all(t not in ''.join(FEATURES) for t in ('winner','finish','pnl','roi','_sp_'))
 future_ar=FullBookArchive(ROOT)
 future_ar.add_snapshot({'prices':[{**bad,'collected_at':date+'T07:00:00Z','bookmaker_updated_at':date+'T10:00:00Z'}]},'future-updated')
 assert observed_field(future_ar,entries,local)[0] is None
 # Synthetic chronological three-way trial; tests baseline/ML joins, settle and reports.
 dater=collections.defaultdict(list)
 for n in range(36):
  d=(dt.date(2026,10,8)+dt.timedelta(days=n)).isoformat()
  for k in range(2):
   pp=np.array([.30,.24,.18,.13,.09,.06]);win=(n+k)%6
   e={**entries,'date':d,'discipline':'AW','race_type':'Handicap','key':(d,'LINGFIELD',f'13:{30+k:02d}')}
   e['cards']=[{'horse':f'Runner {i}'} for i in range(6)]
   r={'date':d,'model':'D_C','entry':e,'x':base,'x_market':x,
      'base_p':pp,'base_order':[0,1,2,3,4,5],
      'sealed_y':[int(i==win) for i in range(6)],
      'sealed_positions':[1 if i==win else 3 for i in range(6)],
      'observations':f,'cutoff':local,'scenario':CUTOFFS[0]}
   dater[(d,'D_C',CUTOFFS[0])].append(r)
 blind=[]
 for d in [x[0] for x in dater]:
  for r in dater[(d,'D_C',CUTOFFS[0])]:
   for rank,i in enumerate((0,1),1):
    blind.append({'record':r,'strategy':'BASE','rank':rank,'i':i,'probability':float(r['base_p'][i]),'trained':True})
    blind.append({'record':r,'strategy':'ML','rank':rank,'i':i,'probability':float(r['base_p'][i]),'trained':True})
 pred,training,acc=predict_market(dater,blind)
 assert any(r['strategy']=='ALL_BOOKS_ML' for r in pred)
 assert all(r['last_train_date']<r['date'] for r in pred if r['last_train_date'])
 assert all(float(r['feature_price_max'])<50 for r in pred)
 with tempfile.TemporaryDirectory() as tmp:
  p=pathlib.Path(tmp)/'picks.csv';write_csv(p,pred,OUTPUT_COLS)
  assert p.stat().st_size>100
  groups=comparisons(pred)
  assert groups and groups[0]['races']>0
 print('Stage104 PASS: as-of 09:45 Irish DST, all-books feature capture, delayed updates excluded, full walk-forward, same-race ROI checks',flush=True)


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 sub=parser.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test')
 comp=sub.add_parser('compare')
 comp.add_argument('--db',required=True);comp.add_argument('--mapping',required=True)
 comp.add_argument('--ratings',required=True);comp.add_argument('--output',default='reports/stage104/output')
 args=parser.parse_args()
 if args.cmd=='self-test':self_test()
 else:run(args)

if __name__=='__main__':main()
