#!/usr/bin/env python3
"""Stage 95 five-model research replay on one immutable UK/IE race cohort.

Models:
  D original: original frozen D Stage30 weighted eight pillar scores.
  D(c): Stage83 75% frozen D z-score + 25% 2024-trained C z-score.
  E75_NO_HPR: reconstructed *proposal* (not an archived live model):
    75% enhanced eight pillar E score, 25% race-type C, HPR absent everywhere.
  E75_WITH_HPR: same, with prior HPR blended ONLY into Ability.
  E_V2: unchanged historical Stage92 published research probabilities.

The two E75 variants are PROTOTYPES. Neither had frozen published predictions;
their precise modelling choices are versioned below rather than misattributed
as exact historical live rules. HPR is absent for some horses; missing is neutral.

Exact complete pre-off cards, one-day historical lag, no target race results/SP
as scoring inputs. The *same* races, finishing outcomes and prices settle every
variant. All training of temperature is pre-Nov-3, with Nov 3-9 held out.
This script is read-only to app, source DB, live D, live D(c), HPR archive.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,json,math,pathlib,sqlite3,statistics,sys
import numpy as np
from zoneinfo import ZoneInfo
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage94_dc_probability_replay as S94
import stage92_model_e_winprob as E92
import stage86_hpr_engine as HPR
import stage30_gap_aware_abcd as OLD
import stage65p_race_type_c_only as C
import stage38_model_dz_live as D
import rhp_stage83_dc_integrated as S83

VERSION='stage95-v1-2026-11-10'
MODELS=('D_ORIGINAL','D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR','E_V2_PUBLISHED')
CAL_MODELS=MODELS[:-1]
FIELDS=['date','time','course','discipline','race_type','race_name','model',
        'horse','win_probability_pct','finish','result_status','won','sp',
        'sp_decimal','sp_source','pnl_1u','has_hpr','prior_hpr_count',
        'data_split','score','model_version','research_only']
THRESHOLDS=(20,25,30,32,34,35,36,38,40,42,45,50,55,60)

def norm(s):return HPR.norm(s)
def rkey(row):return (str(row['date']),HPR.course(row['course']),str(row['time'])[:5])
def standardized(a):
 x=np.asarray(a,dtype=float)
 return (x-x.mean())/(x.std()+1e-8)
def transformed_d_pillars(race):
 """Normalize *preoff* D factor vectors within race; never look at results."""
 x=np.asarray(race['x'][:,:8],dtype=float)
 return np.clip(50+12*(x-x.mean(axis=0))/(x.std(axis=0)+1e-8),15,85)

def hpr_ability(prior,base):
 """E92-style recent HPR 55/30/15, bounded missing-data neutral.

 Historical raw performance is only read for the HPR-on version. It is not used
 anywhere by the HPR-off model.
 """
 recent=[E92.num(z.get('hpr')) for z in prior[-3:]]
 recent=[z for z in recent if z is not None]
 raw=[E92.num(z.get('raw_performance')) for z in prior[-3:]]
 raw=[z for z in raw if z is not None]
 if not recent and not raw:return base
 if recent:
  weights=[.55,.30,.15][:len(recent)]
  score=sum(x*w for x,w in zip(reversed(recent),weights))/sum(weights)
 else:score=None
 rawavg=sum(raw)/len(raw) if raw else None
 return E92.weighted_available(((base,.50),(score,.35),(rawavg,.15)))

def e75_preoff(cards,original,learned,people,draw_hist,hr,date,cache):
 """Paired prototype: same enhanced pillars and C for HPR off vs on.

 Eight-pillar reconstruction: 65% historical D per-factor evidence plus 35%
 E92 contemporary no-HPR assessments. HPR *only* changes the Ability pillar
 via the same E92 (.35 HPR / .15 raw / .50 non-HPR) available-data method.
 This blend has NOT been claimed to be an original published E implementation.
 """
 race_type=S94.race_cat(cards[0]);disc=S94.race_disc(cards[0])
 dparts=transformed_d_pillars(original)
 wc=E92.weights('people_heavy',disc)
 if race_type in ('NH Flat / Bumper','NH Flat/Bumper'):
  wc['jumping']=0.;tot=sum(wc.values());wc={k:v/tot for k,v in wc.items()}
 results_off=[];results_on=[];available=[];hprn=[]
 for i,c in enumerate(cards):
  extra=E92.condition_pillars(c,[],cards,people[0],people[1],draw_hist)
  scores={}
  for k in E92.PILLARS:
   idx=E92.PILLARS.index(k)
   scores[k]=float(.65*dparts[i,idx]+.35*extra['pillars'][k])
  # The no-HPR assessment genuinely never queries previous HPR ratings.
  ability_without=scores['ability']
  off=sum(scores[k]*wc[k] for k in E92.PILLARS)
  key=HPR.identity(c)
  if key:
   ck=(key,date)
   if ck not in cache:cache[ck]=E92.previous_ratings(hr,key,date)
   prior=cache[ck]
  else:prior=[]
  on_scores=dict(scores)
  on_scores['ability']=hpr_ability(prior,ability_without)
  on=sum(on_scores[k]*wc[k] for k in E92.PILLARS)
  available.append(bool(any(E92.num(z.get('hpr')) is not None for z in prior)))
  hprn.append(len(prior))
  results_off.append(off);results_on.append(on)
 c_z=standardized(learned)
 eoff=.75*standardized(results_off)+.25*c_z
 eon=.75*standardized(results_on)+.25*c_z
 return eoff,eon,available,hprn

def score_date(entries,day,past,form,clf,weights,people,draw_hist,hr,cache):
 scaler,model=clf;allout=[]
 unique={}
 for horse,country in past.last:
  if horse not in unique:unique[horse]=country
  elif unique[horse]!=country:unique[horse]=None
 for result,entry,cards in entries:
  if len(cards)<2:continue
  x=[D.live_row(z,day,'STAGE95|'+day+'|'+D.name(entry['course'])+'|'+entry['time'],unique) for z in cards]
  original=past.score(x,live=True)
  if original is None:continue
  dsc,_,_=D.prediction_rows(original,weights)
  ors=[C.num(z.get('official_rating') or z.get('or') or z.get('ofr')) for z in cards]
  ww=[C.num(z.get('weight_lbs')) for z in cards]
  avgo=sum(v for v in ors if v is not None)/sum(v is not None for v in ors) if any(v is not None for v in ors) else None
  avgw=sum(v for v in ww if v is not None)/sum(v is not None for v in ww) if any(v is not None for v in ww) else None
  oscores=[]
  for z in cards:
   feat={'date':day,'horse':z.get('horse'),'country':z.get('horse_country'),
       'trainer':z.get('trainer'),'jockey':z.get('jockey'),
       'race_type':z.get('race_name') or z.get('race_type') or z.get('type'),
       'surface':z.get('surface'),'race_class':z.get('race_class'),
       'or':z.get('official_rating') or z.get('or') or z.get('ofr'),
       'weight':z.get('weight_lbs')}
   v,_=form.vector(feat,avgo,avgw);oscores.append(v)
  cprob=model.predict_proba(scaler.transform(np.asarray(oscores)))[:,1]
  dc=.75*standardized(dsc)+.25*standardized(cprob)
  eno,eon,hprflags,hprn=e75_preoff(cards,original,cprob,people,draw_hist,hr,day,cache)
  scores={'D_ORIGINAL':np.asarray(dsc,dtype=float),'D_C':dc,
          'E_75_25_NO_HPR':eno,'E_75_25_WITH_HPR':eon}
  allout.append({'date':day,'entry':entry,'race':result,'cards':cards,
                 'scores_by_model':scores,'hpr_flags':hprflags,'prior_hpr_count':hprn,
                 'disc':S94.race_disc(cards[0]),'type':S94.race_cat(cards[0]),
                 'winner_idx':next((i for i,z in enumerate(cards) if HPR.win_status(
                    {norm(q['horse']):q for q in result['runners']}[norm(z['horse'])])),None)})
 return allout

def calibrations(train):
 out={}
 for variant in CAL_MODELS:
  rr=[dict(scores=r['scores_by_model'][variant],disc=r['disc'],type=r['type'],winner_idx=r['winner_idx']) for r in train]
  selected=S94.train_temperature(rr)
  if not selected:raise RuntimeError('No calibration for '+variant)
  out[variant]=selected
 return out

def settle_price(race,row,source_prices,external):
 key=HPR.race_key(race);horse=row['horse'];sp,dv,src=S94.choose_price(source_prices,key,horse)
 if dv is None:
  ev=external.get((row['date'],norm(horse)))
  if ev:
   s,p,url,pos=ev
   pos=str(pos or '').upper().replace(' ','')
   finish=row['finish'].upper()
   if not pos or pos==finish or (finish in ('F','PU','UR') and pos.startswith(finish)):
    sp,dv,src=s,p,url
 return sp,dv,src

def join_e_v2(reference):
 if not pathlib.Path(reference).exists():raise RuntimeError('Must supply frozen Stage92 race probabilities')
 by=collections.defaultdict(list)
 with open(reference,encoding='utf-8-sig',newline='') as f:
  for row in csv.DictReader(f):by[rkey(row)].append(row)
 return by

def ref_rank1(rows):
 r=[x for x in rows if str(x.get('rank'))=='1']
 if len(r)!=1:raise ValueError('Stage92 no unique Rank1')
 return r[0]

def read_dc_reference(path):
 if not path:return {}
 with open(path,encoding='utf-8-sig',newline='') as f:return {rkey(z):z for z in csv.DictReader(f)}

def stats(rows):
 n=len(rows);wins=sum(int(z['won']) for z in rows)
 priced=[z for z in rows if z['sp_decimal'] is not None and z['finish']!='NR']
 pnl=sum(z['pnl_1u'] for z in priced)
 return {'selections':n,'winners':wins,'strike_rate_pct':round(100*wins/n,2) if n else None,
         'priced':len(priced),'priced_winners':sum(int(z['won']) for z in priced),
         'sp_missing':n-len(priced),'profit_1u':round(pnl,4),
         'sp_roi_pct':round(100*pnl/len(priced),2) if priced else None}

def self_test():
 s=standardized([1.,2.,3.]);assert s[2]>s[1]>s[0] and abs(float(s.sum()))<1e-9
 assert abs(float(S94.tempscore([2,0],1).sum())-1)<1e-9
 assert hpr_ability([],45.)==45.
 assert hpr_ability([{'hpr':70.,'raw_performance':60.}],45.)>45.
 assert hpr_ability([{'hpr':20.,'raw_performance':30.}],45.)<45.
 assert stats([{'won':1,'sp_decimal':3.,'pnl_1u':2.,'finish':'1'},
               {'won':0,'sp_decimal':2.,'pnl_1u':-1.,'finish':'2'}])['sp_roi_pct']==50.
 r={'date':'2026-11-05','course':'Lingfield (AW)','time':'12:30'}
 assert rkey(r)==('2026-11-05','LINGFIELD','12:30')
 print('PASS Stage95 paired controls: HPR absent neutral, probability totals, parsing, ROI')

def run(a):
 root=pathlib.Path(a.root);out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
 asof=dt.date.fromisoformat(a.as_of)
 end=asof-dt.timedelta(days=1);start=end-dt.timedelta(days=a.days-1)
 lastseven=end-dt.timedelta(days=6)
 if (a.days!=30 or a.as_of!='2026-11-10') and a.dc_reference:
  raise RuntimeError('Frozen Stage94 comparison only supports original 30-day sample')
 C.rows=S83.corrected_rows
 clf=S83.patched_classifier(None,C,a.db)
 audit=json.loads((root/'reports/stage30/stage30-summary.json').read_text())
 frozen=audit.get('model_results',{}).get('D',{})
 if frozen.get('chosen_combination')!='D-064-gap14' or abs(float(frozen.get('selection_margin',0))-.14)>1e-8:
  raise RuntimeError('Frozen original Model D provenance changed')
 weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
 prices=S94.price_candidates(root,start,end)
 external={} if a.no_external_sp else S94.external_price_candidates(
  ((start+dt.timedelta(days=k)).isoformat() for k in range(a.days)),prices)
 resultdb=HPR.load_results(root,start,end)[0]
 cards_by_date=HPR.card_dates(root,start,end,git_preoff=True)
 increments=S94.load_incremental_history(a.db,start.isoformat(),end.isoformat())
 past=D.LivePast({});print('Stage95 initial D history:',past.scan_until(a.db,start.isoformat()),flush=True)
 form=C.Form()
 for grp in C.rows(a.db,start.isoformat()):
  for z in grp:
   z['field_size']=len(grp);form.update(z)
 print('Stage95 C form profiles',len(form.h),flush=True)
 # Same E92 preoff trainers/jockeys + draw history, with date filtering in period_stats.
 population,draw_hist=E92.historical_population(a.db,(start-dt.timedelta(days=365)).isoformat())
 earliest=max(start-dt.timedelta(days=90),dt.date(2026,10,8))
 old_results=HPR.load_results(root,earliest,end)[0]
 old_cards=HPR.card_dates(root,earliest,end,git_preoff=True)
 recent=E92.recent_population(old_results,old_cards)
 for date in sorted(recent):population.extend(recent[date])
 people=E92.aggregate_people(population,None)
 ref=join_e_v2(a.e_v2_reference)
 dc_ref=read_dc_reference(a.dc_reference)
 rated=sqlite3.connect('file:'+str(pathlib.Path(a.ratings).resolve())+'?mode=ro',uri=True)
 cache={};staged=[];rejected=collections.Counter();excluded=[]
 try:
  for di in range(a.days):
   day=(start+dt.timedelta(days=di)).isoformat();cards=HPR.by_race_cards(cards_by_date.get(day,[]));candidates=[]
   for key,race in resultdb.items():
    if key[:10]!=day:continue
    matched,cardstatus=HPR.card_match(race,cards)
    if cardstatus!='PREOFF_FIELD_VERIFIED':
     rejected[cardstatus]+=1;excluded.append((day,race.get('course'),key[-5:],cardstatus));continue
    preoff,preoff_status=S94.verify_preoff_field(matched,race)
    if preoff_status!='OK':
     rejected[preoff_status]+=1;excluded.append((day,race.get('course'),key[-5:],preoff_status));continue
    c=preoff[0];e={'date':day,'course':c['course'],'time':key[-5:],'runners':preoff}
    candidates.append((race,e,preoff))
   for row in score_date(candidates,day,past,form,clf,weights,people,draw_hist,rated,cache):
    if row['winner_idx'] is None or row['disc']=='Unknown':
     why='NO_WINNER' if row['winner_idx'] is None else 'UNKNOWN_DISCIPLINE'
     rejected[why]+=1;excluded.append((day,row['entry']['course'],row['entry']['time'],why));continue
    k=rkey({'date':day,'course':row['entry']['course'],'time':row['entry']['time']})
    if k not in ref:
     rejected['MISSING_STAGE92_REFERENCE']+=1
     excluded.append((day,row['entry']['course'],row['entry']['time'],'MISSING_STAGE92_REFERENCE'))
     continue
    rr=ref[k]
    # Ensure exact result/winner runner scope for published Model E v2.
    if len(rr)!=len(row['cards']) or {norm(z['horse']) for z in rr}!={norm(z['horse']) for z in row['cards']}:
     rejected['STAGE92_FIELD_CONFLICT']+=1
     excluded.append((day,row['entry']['course'],row['entry']['time'],'STAGE92_FIELD_CONFLICT'))
     continue
    row['e_v2']=rr
    staged.append(row)
   S94.add_completed_date(past,form,increments.get(day,[]))
   if di%3==0:print('Stage95',day,'paired races accumulated',len(staged),flush=True)
 finally:rated.close()
 if len(staged)<150:raise RuntimeError(f'Too few shared D/Dc/E cohort races: {len(staged)}')
 train=[z for z in staged if z['date']<lastseven.isoformat()]
 held=[z for z in staged if z['date']>=lastseven.isoformat()]
 if len(train)<60 or len(held)<60:raise RuntimeError('Insufficient 23-day train / 7-day holdout')
 cal=calibrations(train)
 output=[];wide=[];nonstarter=0
 for r in staged:
  day=r['date'];e=r['entry'];race=r['race'];cards=r['cards'];typ=r['type'];disc=r['disc']
  snap={norm(x['horse']):x for x in race['runners']}
  split='HOLDOUT' if day>=lastseven.isoformat() else 'TRAIN'
  by_model={}
  for model in MODELS:
   if model=='E_V2_PUBLISHED':
    e92=ref_rank1(r['e_v2']);horse=e92['horse'];idx=next((i for i,c in enumerate(cards) if norm(c['horse'])==norm(horse)),None)
    if idx is None:raise RuntimeError('Published E v2 ranked horse not in common starter field')
    prob=float(e92['win_probability_pct']);score='';temperature_src='stage92_original_research'
    has_hpr=bool(int(e92.get('has_hpr') or 0))
    hpr_count=int(e92.get('hpr_prior_count') or 0)
   else:
    scores=r['scores_by_model'][model]
    idx=int(np.argmax(scores));horse=cards[idx]['horse']
    if model not in cal:raise RuntimeError('Missing calibration '+model)
    temper,temperature_src=S94.category_temp(cal[model],dict(disc=disc,type=typ))
    prob=round(100*float(S94.tempscore(scores,temper)[idx]),4)
    score=round(float(scores[idx]),6)
    has_hpr=bool(r['hpr_flags'][idx]);hpr_count=r['prior_hpr_count'][idx]
   outcome=snap[norm(horse)]
   finish,won=S94.parse_finish(outcome)
   if model=='E_V2_PUBLISHED' and int(e92.get('winner_verified') or 0)!=int(won):
    raise RuntimeError('Stage92 outcome differs from verified Stage94 settlement')
   if finish=='NR':raise RuntimeError('Nonrunner incorrectly present in complete runner cohort')
   sample={'date':day,'time':e['time'],'course':e['course'],'horse':horse,'finish':finish}
   sp,dv,source=settle_price(race,sample,prices,external)
   final={'date':day,'time':e['time'],'course':e['course'],'discipline':disc,'race_type':typ,
      'race_name':cards[0].get('race_name') or '', 'model':model,
      'horse':horse,'win_probability_pct':prob,'finish':finish,'result_status':outcome.get('status') or '',
      'won':int(won),'sp':sp,'sp_decimal':dv,'sp_source':source,
      'pnl_1u':(round(dv-1,8) if won else -1.) if dv is not None else None,
      'has_hpr':int(has_hpr),'prior_hpr_count':hpr_count,
      'data_split':split,'score':score,
      'model_version':VERSION if model!='E_V2_PUBLISHED' else 'stage92-original-unchanged',
      'research_only':True,'temperature_source':temperature_src}
   output.append(final);by_model[model]=final
  pivot={'date':day,'time':e['time'],'course':e['course'],'discipline':disc,'race_type':typ,'race_name':cards[0].get('race_name') or '',
         'winner':next((z['horse'] for z in race['runners'] if HPR.win_status(z)),'UNKNOWN'),'data_split':split}
  for model,x in by_model.items():
   prefix=model.lower();pivot.update({prefix+'_horse':x['horse'],prefix+'_win_pct':x['win_probability_pct'],
       prefix+'_finish':x['finish'],prefix+'_sp':x['sp'],prefix+'_won':x['won'],
       prefix+'_pnl_1u':x['pnl_1u'],prefix+'_has_hpr':x['has_hpr']})
  wide.append(pivot)
 output.sort(key=lambda z:(z['date'],z['time'],z['course'],MODELS.index(z['model'])))
 wide.sort(key=lambda z:(z['date'],z['time'],z['course']))
 # Prior Stage94 exact baseline regression: same races, same #1, same score and probability.
 checked=0;problems=[]
 for w in wide:
  if w['data_split']!='HOLDOUT':continue
  key=rkey(w);original=dc_ref.get(key)
  if not original:problems.append(f'Missing archived D(c) {key}');continue
  if norm(w['d_c_horse'])!=norm(original['horse']):problems.append(f'D(c) Rank1 changed {key}')
  if abs(float(w['d_c_win_pct'])-float(original['win_probability_pct']))>.025:
   problems.append(f'D(c) probability drifted {key}: {w["d_c_win_pct"]} vs {original["win_probability_pct"]}')
  checked+=1
 if dc_ref and (checked!=len(held) or problems):
  raise RuntimeError('Cannot claim same frozen D(c) replay. Verified '+str(checked)+'/'+str(len(held))+'; problems '+str(problems[:12]))
 if len(held)!=153 and a.as_of=='2026-11-10' and a.days==30:
  raise RuntimeError(f'Expected 153 exactly shared holdout races from Stage94 vs Stage92; found {len(held)}. Refuse non-comparable ROI.')
 # Strong control: HPR-only toggle is neutral for any race lacking an HPR for all runners.
 no_hpr_races=0
 for r in staged:
  if not any(r['hpr_flags']):
   no_hpr_races+=1
   if np.max(np.abs(r['scores_by_model']['E_75_25_NO_HPR']-r['scores_by_model']['E_75_25_WITH_HPR']))>1e-8:
    raise RuntimeError('HPR missing altered E score: violates neutral coverage rule')
 last7=[z for z in output if z['data_split']=='HOLDOUT']
 last7wide=[z for z in wide if z['data_split']=='HOLDOUT']
 S94.write_csv(out/'stage95-last7-line-by-line.csv',last7wide,list(last7wide[0]))
 S94.write_csv(out/'stage95-all30-line-by-line.csv',wide,list(wide[0]))
 S94.write_csv(out/'stage95-last7-all-model-selections.csv',last7,FIELDS+['temperature_source'])
 S94.write_csv(out/'stage95-all30-all-model-selections.csv',output,FIELDS+['temperature_source'])
 agg=[];bands=[];sub=[];daily=[];hpr_impact=[]
 for model in MODELS:
  for period,arr in [('LAST_7_DAYS',last7),('ALL_30_DAYS',output)]:
   picks=[z for z in arr if z['model']==model]
   agg.append({'model':model,'period':period,**stats(picks)})
   if period=='LAST_7_DAYS':
    for day in sorted({z['date'] for z in picks}):
     daily.append({'model':model,'date':day,**stats([z for z in picks if z['date']==day])})
   for t in THRESHOLDS:
    band=[z for z in picks if float(z['win_probability_pct'])>=t]
    bands.append({'model':model,'period':period,'threshold_pct':t,**stats(band)})
   for field in ('discipline','race_type'):
    for k in sorted({z[field] for z in picks}):
     zz=[z for z in picks if z[field]==k]
     sub.append({'model':model,'period':period,'group_by':field,'group':k,**stats(zz)})
 S94.write_csv(out/'stage95-overall.csv',agg,['model','period',*list(stats(last7).keys())])
 S94.write_csv(out/'stage95-thresholds.csv',bands,['model','period','threshold_pct',*list(stats(last7).keys())])
 S94.write_csv(out/'stage95-race-types.csv',sub,['model','period','group_by','group',*list(stats(last7).keys())])
 S94.write_csv(out/'stage95-daily.csv',daily,['model','date',*list(stats(last7).keys())])
 byoutput={(rkey(x),x['model']):x for x in output}
 for z in wide:
  before=byoutput.get((rkey(z),'E_75_25_NO_HPR'))
  after=byoutput.get((rkey(z),'E_75_25_WITH_HPR'))
  if before is None or after is None:raise RuntimeError('HPR pair not present on common race')
  hpr_impact.append({'date':z['date'],'time':z['time'],'course':z['course'],
    'discipline':z['discipline'],'race_type':z['race_type'],'data_split':z['data_split'],
    'no_hpr_rank1':before['horse'],'no_hpr_win_pct':before['win_probability_pct'],
    'no_hpr_finish':before['finish'],'no_hpr_sp':before['sp'],'no_hpr_won':before['won'],
    'hpr_rank1':after['horse'],'hpr_win_pct':after['win_probability_pct'],
    'hpr_finish':after['finish'],'hpr_sp':after['sp'],'hpr_won':after['won'],
    'hpr_selected_has_prior_rating':after['has_hpr'],
    'rank1_changed':int(norm(before['horse'])!=norm(after['horse']))})
 S94.write_csv(out/'stage95-hpr-impact.csv',hpr_impact,list(hpr_impact[0]))
 S94.write_csv(out/'stage95-exclusions.csv',
   [dict(date=z[0],course=z[1],time=z[2],reason=z[3]) for z in excluded],['date','course','time','reason'])
 n_runners=sum(len(r['cards']) for r in staged)
 rated_runners=sum(sum(r['hpr_flags']) for r in staged)
 splits={'start':start.isoformat(),'end':end.isoformat(),'holdout_start':lastseven.isoformat(),'trained_earlier':len(train),'heldout':len(held)}
 summary={'stage':95,'version':VERSION,'period':splits,'same_races_for_every_model':True,
  'total_paired_races':len(staged),'selected_models':list(MODELS),'overall':agg,
  'hpr_effect_on_rank1':{'holdout_changed_rank1_races':sum(z['rank1_changed'] for z in hpr_impact if z['data_split']=='HOLDOUT'),
    'holdout_unchanged_rank1_races':sum(not z['rank1_changed'] for z in hpr_impact if z['data_split']=='HOLDOUT'),
    'all30_changed_rank1_races':sum(z['rank1_changed'] for z in hpr_impact)},
  'hpr_coverage':{'runners_with_prior_hpr':rated_runners,'eligible_runners':n_runners,
    'pct_runners_with_hpr':round(100*rated_runners/n_runners,2),
    'all_runners_no_hpr_races':no_hpr_races,'missing_neutral_tests_passed':True},
  'comparison_with_stage94':{'verified_holdout_races':checked,'rank1_and_probability_identical':bool(dc_ref)},
  'reject_counts':dict(rejected),'conventions':[
   '75/25 E variants are RECONSTRUCTED RESEARCH FORMULAS, not historically published frozen E versions.',
   '75% enhances eight-pillar E score using 65% date-locked D pillar evidence / 35% E v2 contemporaneous non-HPR form/people inputs, plus 25% Stage83 C race-type model.',
   'E 75/25 HPR-on changes only Ability (50% baseline, 35% prior HPR, 15% prior raw run quality), re-normalized among available inputs.',
   'When no HPR or raw run quality is available, E HPR-on equals E HPR-off for that horse.',
   'Live frozen D weights unchanged; D(c) same Stage94 historical 75%D +25%C rankings.',
   'E v2 rank/probabilities sourced from frozen successful Stage92 research output, not retrained.',
   'SP and race results are for settlement exclusively, never scoring inputs.',
   'Win % calibration fitted only on pre-3-Nov races; older 30-day portion is in-sample for calibration.',
   'HPR observations are prior-calendar-day only; no missing HPR penalty; HPR feature presence audited.',
   'All models settled on matched 153 3–9 Nov races (fixed date window) and identical SP sources.']}
 (out/'stage95-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print('Stage95 FULL PAIR',json.dumps({'sample':splits,'hpr':summary['hpr_coverage'],'overall':agg},indent=2),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--root',default='.')
 p.add_argument('--db',default='reports/stage95/base/backfilled_research_only.sqlite')
 p.add_argument('--ratings',default='reports/stage95/hpr-ratings.sqlite')
 p.add_argument('--e-v2-reference',required=False,default='reports/stage95/reference_e92/stage92-race-probabilities.csv')
 p.add_argument('--dc-reference',default='reports/stage95/reference_dc/stage94-last7-rank1.csv')
 p.add_argument('--output',default='reports/stage95/output')
 p.add_argument('--as-of',default='2026-11-10')
 p.add_argument('--days',default=30,type=int)
 p.add_argument('--no-external-sp',action='store_true')
 p.add_argument('--self-test',action='store_true')
 args=p.parse_args()
 if args.self_test:self_test()
 else:run(args)
