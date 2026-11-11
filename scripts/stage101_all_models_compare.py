#!/usr/bin/env python3
"""Stage101 — READ-ONLY chronological UK/IE model comparison, 5–11 Nov 2026.

This is a historical reconstruction of missed A/B/C predictions, NOT a live pick archive.
A/B historical coefficients are original but the feature projection is a RESEARCH
approximation until original engine parity is verified; their rows are explicitly
flagged. C is the original Stage22 long-context features/model without external tips.
D/D(c)/E75 use the very same Stage95 scorer and frozen research parameters.
D's separate archived pre-off ranking is shown independently, not overwritten.
SP/finishing results are used ONLY after generating predictions for a dated race.
Nothing writes to data/, production models, HPR shards, or repo history.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,json,math,pathlib,re,sqlite3,sys,textwrap
from fractions import Fraction

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
START='2026-11-05'; END='2026-11-11'
DATE_FMT='%Y-%m-%d'
ARCHIVED='D_ARCHIVED_PREOFF'
REPLAY_A='ORIGINAL_A_RECONSTRUCTED'
REPLAY_B='ORIGINAL_B_RECONSTRUCTED'
REPLAY_C='ORIGINAL_C_V2_RECONSTRUCTED'
MODELS=(REPLAY_A,REPLAY_B,REPLAY_C,ARCHIVED,'D_ORIGINAL_REPLAY','D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR','E_V2_STAGE92','D_C_STAGE99_NH','E_BALANCED_STAGE99_NH')
RESEARCH_A_B='RECONSTRUCTED_AB_FEATURE_PARITY_UNVERIFIED'
RESEARCH_C='MODEL_C_ORIGINAL_FEATURE_ENGINE_BACKDATED_REPLAY'
RESEARCH_DE='STAGE95_FROZEN_FORMULA_REPLAY'
RESEARCH_E92='STAGE92_ARCHIVED_RESEARCH_REPLAY'
RESEARCH_STAGE99='STAGE99_RECONSTRUCTED_NH_PROFILE_NOT_PROSPECTIVE'
PREOFF_ARCHIVE='RECORDED_PREOFF_RANKING'
FIELDS=['date','course','off_utc','discipline','race_type','race_name','race_key','model','model_version','basis','rank',
        'horse','win_probability_pct','score','finish_position','finish_status','won','sp','sp_decimal','pnl_1u',
        'settlement_source','snapshot_source','evidence_status','qualified','sample_type']
THRESHOLDS=(20,25,30,34,40,42,45,50)


def read_json(p,default=None):
 try:return json.loads(pathlib.Path(p).read_text(encoding='utf-8'))
 except (OSError,ValueError):return default if default is not None else {}


def norm(x):
 import unicodedata
 v=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().upper()
 v=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$','',v)
 return re.sub(r'[^A-Z0-9]','',v)


def clock(v):
 v=str(v or '')
 m=re.search(r'(\d{2}):(\d{2})',v)
 return f'{m.group(1)}:{m.group(2)}' if m else None


def as_float(v):
 try:
  x=float(v)
  return x if math.isfinite(x) else None
 except (ValueError,TypeError):return None


def sp_decimal(v):
 if v is None:return None
 if isinstance(v,(int,float)):return float(v) if 1<float(v)<10000 else None
 s=str(v).strip().upper().replace(' ','')
 if s in ('EVS','EVENS','EVEN','E'):return 2.0
 if '/' in s:
  try:
   f=Fraction(s)
   x=1+float(f)
   return x if 1<x<10000 else None
  except (ValueError,ZeroDivisionError):return None
 x=as_float(s)
 return x if x and 1<x<10000 else None


def write_csv(path,rows,fields):
 p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8-sig',newline='') as fh:
  w=csv.DictWriter(fh,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def install_engines():
 """Recover *unchanged* pinned A/B/C modules from pre-existing Stage22 install code.

 The only script evaluated is the repository's own pinned Stage22 C_INSTALL
 heredoc, already executed by the existing GitHub workflow. This command
 does not train/publish models or alter tracked production data.
 """
 source=ROOT/'.github/workflows/stage21-model-c-live.yml'
 raw=source.read_text(encoding='utf-8')
 match=re.search(r"python - <<'C_INSTALL'\s*\n(.*?)\n\s*C_INSTALL\s*\n",raw,re.S)
 if not match:raise RuntimeError('Original Stage22 engine installer was changed; refusing estimated replacement')
 script=textwrap.dedent(match.group(1))
 if 'for name, pieces in assets.items():' not in script or "if name.startswith('data/strategy-model-c-'):continue" not in script:
  raise RuntimeError('Original safe Stage22 installer marker changed')
 if 'base64' not in script or 'gzip.decompress' not in script:raise RuntimeError('Unexpected Stage22 engine encoding')
 # Execute the existing trusted repository installer, not downloaded web code.
 exec(compile(script,str(source)+':C_INSTALL','exec'),{'__name__':'__main__'})
 for p in ('stage16_history_model.py','stage18_historical_ab.py','stage19_live_b.py','stage21_live_c.py','stage21_publish_c.py'):
  if not (ROOT/'scripts'/p).is_file():raise RuntimeError('Pinned engine missing: '+p)
 print('Pinned original historical scoring engines available. No models or data changed.',flush=True)


def audit_inputs(db,mapping):
 path=pathlib.Path(db);mp=pathlib.Path(mapping)
 if not path.is_file() or not mp.is_file():raise RuntimeError('Stage100 paired artifacts missing')
 if path.stat().st_size<2_000_000_000 or mp.stat().st_size<50_000_000:
  raise RuntimeError('Stage100 source incomplete; refusing lightweight substitute')
 cx=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
 try:
  if cx.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('Historical database integrity failed')
  tables={x[0] for x in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if not {'historical_races','historical_runs','stage83_dc_verified_types','stage65p_race_details'}<=tables:
   raise RuntimeError('Shared A/B/C + D(c) tables missing')
  races=cx.execute('select count(*) from historical_races').fetchone()[0]
  if races<400000:raise RuntimeError('Historical sample incomplete')
  mn,mx=cx.execute('select min(race_date),max(race_date) from historical_races').fetchone()
  if mn>'1988-12-31' or mx<'2026-10-07':raise RuntimeError('History coverage incomplete')
  print('Verified single Stage100 source:',races,'races',mn,'to',mx,flush=True)
 finally:cx.close()


def stable_p(scores):
 import numpy as np
 z=np.asarray(scores,dtype=float)
 if len(z)<2 or not np.isfinite(z).all():return None
 z=z-z.max();z=np.exp(np.clip(z,-100,10));v=z/z.sum()
 return v if abs(float(v.sum())-1)<1e-6 else None


def historical_model_p(model, features):
 """Original stored A/B coefficients + saved training scaler; PROXY feature parity.

 Unlike original C's matrix method, this uses a shared B-live feature projection.
 This uncertainty MUST be shown in reports; do not promote A/B replay to exact.
 """
 import numpy as np
 obj=model['model']; names=model['features'];co=np.asarray(obj['coefficients'],dtype=float)
 mu=np.asarray(obj['means'],dtype=float);sd=np.asarray(obj['stds'],dtype=float)
 if not (len(names)==len(co)==len(mu)==len(sd)):raise RuntimeError('A/B trained model dimensionality mismatch')
 xx=[];missing=collections.Counter()
 for f in features:
  row=[]
  for name in names:
   value=f.get(name)
   if value is None:
    missing[name]+=1
    row.append(0.)
    continue
   value=as_float(value)
   if value is None:
    missing[name]+=1
    row.append(0.)
   else:
    row.append(value)
  xx.append(row)
 if missing:return None,dict(missing)  # fail closed: never guess a missing model feature
 z=(np.asarray(xx,dtype=float)-mu)/np.maximum(sd,1e-8)
 # The 2025-trained model stores per-runner logistic coefficients. Convert
 # their relative log-odds to whole-race probabilities without using SP.
 logits=z@co+float(obj.get('intercept',0.))
 return stable_p(logits/max(.2,float(obj.get('temperature',1.) or 1.))),dict(missing)


def card_models_original(db,mapping,entries,issues):
 import numpy as np
 import stage16_history_model as A
 import stage18_historical_ab as B
 import stage19_live_b as LB
 import stage22_model_c as C
 da=read_json(ROOT/'data/strategy-historical-model.json')
 dbmodel=read_json(ROOT/'data/strategy-model-b-historical.json')
 dc=read_json(ROOT/'data/strategy-model-c-historical.json')
 if not da.get('model') or not dbmodel.get('model') or dc.get('version')!=C.VERSION:
  raise RuntimeError('Original A B C trained models missing or incompatible')
 names=set();tr=set();jk=set();needed=set();byday=collections.defaultdict(list)
 for item in entries:
  day=item['date'];cards=item['cards'];byday[day].append(item)
  for r in cards:
   names.add(A.history_key(r.get('horse'),r.get('sire'),r.get('dam')))
   if r.get('trainer'):tr.add(C.norm(r['trainer']))
   if r.get('jockey'):jk.add(C.norm(r['jockey']))
  head=cards[0]
  key=B.condition_key(head['course'],LB.norm_type(head),head.get('surface'),head.get('going'),head.get('race_class'),A.safe_num(head.get('distance_f')))
  if key:
   for dv in (-1.,-.5,0.,.5,1.):needed.add((*key[:-1],key[-1]+dv))
 eventids,regions=B.mapping_ids(mapping)
 reg=ROOT/'.github/workflows/stage4_reference_race_registry.csv'
 for day,dayitems in byday.items():
  fields={(C.norm(e['course']),e['time']):e['cards'] for e in dayitems}
  eventids.update(LB.named_lookup(mapping,reg,fields,day))
 ctx=C.LongContext(eventids,needed)
 state=B.ScopedState(names,tr,jk)
 days=sorted(byday);idx=0;buffer=[];older=[];previous=None;out=collections.defaultdict(dict);historical=0

 def score_day(day):
  for item in byday.get(day,[]):
   cards=item['cards']; course=C.norm(item['course']);t=item['time'];rid=f'LIVE|{course}|{t}'
   rows=[LB.live_row(r,day,rid) for r in cards]
   match,past=ctx.get(rows)
   feature_list=LB.live_features(state,rows,past,match)
   if not feature_list or len(feature_list)!=len(cards):
    issues.append({'date':day,'course':item['course'],'time':t,'model':'ORIGINAL_ABC','reason':'NO_ORIGINAL_FORM_FEATURES'})
    continue
   augmented=[]
   for i,f in enumerate(feature_list):
    f=dict(f);r=cards[i]
    for name,keys in [('rating',('official_rating','or','ofr')),('weight',('weight_lbs','weight'))]:
     if name not in f:
      values=[as_float(r.get(k)) for k in keys]
      f[name]=next((v for v in values if v is not None),0.)
    if 'course_race_run_count' not in f:f['course_race_run_count']=len(past)
    augmented.append(f)
   for label,model in ((REPLAY_A,da),(REPLAY_B,dbmodel)):
    p,missing=historical_model_p(model,augmented)
    if p is None:
     issues.append({'date':day,'course':item['course'],'time':t,'model':label,'reason':'MISSING_OR_INVALID_AB_FEATURES:'+str(missing)[:250]})
     continue
    out[item['key']][label]={'scores':p,'probabilities':p,'version':model.get('version',''),'basis':RESEARCH_A_B,
      'qualified':False,'missing_features':missing}
   x=C.matrix(C.append_features(feature_list,rows,past,day))
   p=C.probs(dc['model'],x)
   if len(p)!=len(cards) or not np.isfinite(p).all():
    issues.append({'date':day,'course':item['course'],'time':t,'model':REPLAY_C,'reason':'INVALID_C_PROBABILITY'})
    continue
   out[item['key']][REPLAY_C]={'scores':p,'probabilities':p,'version':dc['version'],'basis':RESEARCH_C,
      'qualified':C.selection_gate(p,dc.get('selected_gate',{'lead_pp':7,'min_probability':.42,'ratio':1.15})),
      'match_basis':match,'history_examples':len(past)}

 until=(dt.date.fromisoformat(days[-1])+dt.timedelta(days=1)).isoformat() if days else END
 for group in B.iter_db(db,until=until):
  d=group[0][1];historical+=1
  if previous is not None and d!=previous:
   for r in buffer:state.update(r[:17])
   for g in older:ctx.add(g)
   buffer=[];older=[]
  if d!=previous:
   while idx<len(days) and days[idx]<=d:
    score_day(days[idx]);idx+=1
   previous=d
  buffer.extend(group);older.append(group)
  if historical%50000==0:print('Stage101 original ABC historical scanned',historical,'through',d,flush=True)
 if buffer:
  for r in buffer:state.update(r[:17])
  for g in older:ctx.add(g)
 while idx<len(days):score_day(days[idx]);idx+=1
 print('Stage101 A/B/C research replay complete:',historical,'historic races,',len(out),'target races',flush=True)
 return out


def date_rows_de(db,ratings,entries,issues):
 """Identical Stage95 D, D(c), E75 date scorer and feature pipeline."""
 import numpy as np
 import stage95_five_model_paired_replay as S95
 import stage94_dc_probability_replay as S94
 import stage92_model_e_winprob as E92
 import stage86_hpr_engine as HPR
 import stage38_model_dz_live as D
 import stage65p_race_type_c_only as Cform
 import stage30_gap_aware_abcd as OLD
 import rhp_stage83_dc_integrated as S83

 Cform.rows=S83.corrected_rows
 clf=S83.patched_classifier(None,Cform,db)
 audit=read_json(ROOT/'reports/stage30/stage30-summary.json')
 frozen=audit.get('model_results',{}).get('D',{})
 if frozen.get('chosen_combination')!='D-064-gap14' or abs(float(frozen.get('selection_margin',0))-.14)>1e-8:
  raise RuntimeError('D frozen weights missing/changed: refuse substitute')
 weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
 days=sorted({e['date'] for e in entries});start=days[0];end=days[-1]
 past=D.LivePast({});past.scan_until(db,start)
 form=Cform.Form()
 for group in Cform.rows(db,start):
  for z in group:
   z['field_size']=len(group);form.update(z)
 begin=dt.date.fromisoformat(start);finish=dt.date.fromisoformat(end)
 population,draw_hist=E92.historical_population(db,(begin-dt.timedelta(days=365)).isoformat())
 old_results=HPR.load_results(ROOT,max(begin-dt.timedelta(days=90),dt.date(2026,10,8)),finish)[0]
 old_cards=HPR.card_dates(ROOT,max(begin-dt.timedelta(days=90),dt.date(2026,10,8)),finish,git_preoff=True)
 recent=E92.recent_population(old_results,old_cards)
 for date in sorted(recent):population.extend(recent[date])
 people=E92.aggregate_people(population,None)
 updates=S94.load_incremental_history(db,start,end)
 rated=sqlite3.connect('file:'+str(pathlib.Path(ratings).resolve())+'?mode=ro',uri=True)
 byday=collections.defaultdict(list)
 for item in entries:byday[item['date']].append(item)
 out=collections.defaultdict(dict);cache={}
 try:
  for day in days:
   candidates=[(e['result'],{'date':day,'course':e['course'],'time':e['time'],'runners':e['cards']},e['cards']) for e in byday[day]]
   try:rows=S95.score_date(candidates,day,past,form,clf,weights,people,draw_hist,rated,cache)
   except Exception as ex:
    raise RuntimeError(f'Frozen D/E scorer failed on {day}, NOT replacing with proxy: {ex}') from ex
   for row in rows:
    key=next((e['key'] for e in byday[day] if norm(e['course'])==norm(row['entry']['course']) and e['time']==row['entry']['time']),None)
    if not key:continue
    for label,values in row['scores_by_model'].items():
     p=S94.tempscore(values,1.) # score-derived; NOT a validated win probability
     if p is None or len(p)!=len(row['cards']):continue
     out[key][label]={'scores':values,'probabilities':None,'score_share':p,'version':S95.VERSION,
        'basis':RESEARCH_DE,'qualified':False}
   S94.add_completed_date(past,form,updates.get(day,[]))
   print('Stage101 frozen D/E reconstructed',day,len(rows),'matched races',flush=True)
 finally:rated.close()
 return out


def frozen_e_v2_reference():
 """Read already generated Stage92 original E probabilities, never refit on target days."""
 folder=ROOT/'reports/stage101/reference-e92'
 files=list(folder.rglob('stage92-race-probabilities.csv'))
 if not files:return {}
 out=collections.defaultdict(list)
 with files[0].open(encoding='utf-8-sig',newline='') as f:
  for r in csv.DictReader(f):
   day=str(r.get('date') or '')[:10];time=clock(r.get('time'))
   if not (START<=day<=END and time):continue
   key=(day,norm(r.get('course')),time)
   out[key].append(r)
 return out


def frozen_stage99_reference():
 """Supplementary Nov11 Stage99 ranks. These are NOT part of strict paired cohort.

 Stage99 source selected its optimal profiles after retrospective analysis;
 it should never be described as an untouched live out-of-sample benchmark.
 """
 files=list((ROOT/'reports/stage101/reference-stage99').rglob('stage99-11nov-all-rank1-rank2.csv'))
 out=collections.defaultdict(lambda:collections.defaultdict(dict))
 if not files:return out
 with files[0].open(encoding='utf-8-sig',newline='') as f:
  for r in csv.DictReader(f):
   label={'D_C':'D_C_STAGE99_NH','E_75_25_NO_HPR':'E_BALANCED_STAGE99_NH'}.get(str(r.get('model')))
   if not label:continue
   date=str(r.get('date') or '')[:10];off=clock(r.get('off_utc'))
   if date!='2026-11-11' or not off:continue
   try:rank=int(r.get('rank'))
   except (ValueError,TypeError):continue
   if rank not in (1,2):continue
   key=(date,norm(r.get('course')),off)
   out[key][label][rank]=r
 return out


def archived_d_rankings(day,key):
 p=ROOT/'data/model-performance'/f'{day}.json'
 d=read_json(p)
 for race in d.get('models',{}).get('D',[]):
  if not race.get('pre_off_valid'):continue
  if (day,norm(race.get('course')),clock(race.get('time') or race.get('race_time')))!=key:continue
  ranks={int(z['rank']):norm(z['horse']) for z in race.get('runners',[]) if isinstance(z.get('rank'),int)}
  return ranks
 return {}


def collect_entries(start,end):
 import stage86_hpr_engine as HPR
 import stage94_dc_probability_replay as S94
 begin=dt.date.fromisoformat(start);stop=dt.date.fromisoformat(end)
 results,bad=HPR.load_results(ROOT,begin,stop)
 cards=HPR.card_dates(ROOT,begin,stop,git_preoff=True)
 entries=[];issues=[]
 for key,result in sorted(results.items()):
  d=str(result.get('date') or result.get('off_dt'))[:10];snapshot=HPR.by_race_cards(cards.get(d,[]))
  matched,status=HPR.card_match(result,snapshot)
  if status!='PREOFF_FIELD_VERIFIED':
   issues.append({'date':d,'course':result.get('course'),'time':key[-5:],'model':'ALL','reason':status});continue
  preoff,status=S94.verify_preoff_field(matched,result)
  if status!='OK':
   issues.append({'date':d,'course':result.get('course'),'time':key[-5:],'model':'ALL','reason':status});continue
  off=clock(result.get('off_dt')) or clock(preoff[0].get('race_time'))
  if not off:issues.append({'date':d,'course':result.get('course'),'time':'','model':'ALL','reason':'MISSING_OFF_TIME'});continue
  # Only final, complete and concordant fields. Never construct a post-race field.
  e={'date':d,'course':result['course'],'time':off,'key':(d,norm(result['course']),off),
    'cards':preoff,'result':result,'discipline':S94.race_disc(preoff[0]),'race_type':S94.race_cat(preoff[0]),
    'race_name':preoff[0].get('race_name') or '','snapshot':'git_before_09:45_utc'}
  entries.append(e)
 print('Stage101 verified pre-off/finished matched races',len(entries),'result warnings',bad,flush=True)
 return entries,issues,{'verified_result_races':len(results),'verified_snapshot_races':len(entries),'result_rejections':bad}


def format_rows(entries,abc,de,issues):
 e92=frozen_e_v2_reference();s99=frozen_stage99_reference();rows=[];held=collections.Counter()
 for e in entries:
  rk=e['key']; result=e['result'];cards=e['cards']
  actual={norm(r.get('horse')):r for r in result.get('runners',[])}
  candidate={**abc.get(rk,{}),**de.get(rk,{})}
  archived=archived_d_rankings(e['date'],rk)
  if 1 in archived and 2 in archived:
   name_to_index={norm(c['horse']):i for i,c in enumerate(cards)}
   if archived[1] in name_to_index and archived[2] in name_to_index:
    order=[name_to_index[archived[1]],name_to_index[archived[2]]]
    candidate[ARCHIVED]={'forced_order':order,'version':'original-frozen-D-archived-snapshot',
      'basis':PREOFF_ARCHIVE,'qualified':False,'probabilities':None,'scores':None}
  if rk in e92:
   ref=e92[rk]
   expected={norm(c['horse']) for c in cards}
   if len(ref)==len(cards) and {norm(x.get('horse')) for x in ref}==expected:
    m={norm(z['horse']):z for z in ref}
    try:
     p=[float(m[norm(c['horse'])]['win_probability_pct'])/100 for c in cards]
     if all(0<=x<=1 for x in p):
      candidate['E_V2_STAGE92']={'scores':p,'probabilities':p,'basis':RESEARCH_E92,
        'version':'stage92-frozen-research','qualified':False}
    except (KeyError,ValueError,TypeError):pass
  for label,ranking in s99.get(rk,{}).items():
   if set(ranking)!={1,2}:continue
   try:preoff_n=int(ranking[1].get('field_size') or 0)
   except (TypeError,ValueError):continue
   if preoff_n!=len(cards):continue
   indices={norm(z['horse']):i for i,z in enumerate(cards)}
   h1=norm(ranking[1].get('horse'));h2=norm(ranking[2].get('horse'))
   if not h1 or h1==h2 or h1 not in indices or h2 not in indices:continue
   candidate[label]={'forced_order':[indices[h1],indices[h2]],'probabilities':None,
     'scores':None,'basis':RESEARCH_STAGE99,'version':ranking[1].get('research_setting') or 'stage99',
     'qualified':False}
  for model in MODELS:
   spec=candidate.get(model)
   if not spec:
    held[(model,'NO_MATCHED_PREOFF_REPLAY')]+=1;continue
   if 'forced_order' in spec:order=spec['forced_order'];scores=[]
   else:
    values=spec['scores'];order=sorted(range(len(cards)),key=lambda i:(-float(values[i]),i))[:2]
    scores=values
   for rank,i in enumerate(order,1):
    horse=cards[i].get('horse');actual_r=actual.get(norm(horse))
    if not actual_r:
     issues.append({'date':e['date'],'course':e['course'],'time':e['time'],'model':model,'reason':'NO_MATCHED_RESULT_RUNNER'});continue
    position=actual_r.get('position');status=actual_r.get('status') or ''
    win=int(str(position).strip()=='1')
    sp=actual_r.get('sp') or actual_r.get('sp_decimal');decimal=sp_decimal(sp)
    prob=spec.get('probabilities');pval=round(float(prob[i])*100,4) if prob is not None else ''
    if spec.get('basis')==RESEARCH_DE:pval='' # do not fake calibrated win likelihood
    s=float(scores[i]) if len(scores) else None
    row=dict(date=e['date'],course=e['course'],off_utc=e['time'],discipline=e['discipline'],race_type=e['race_type'],
      race_name=e['race_name'],race_key='|'.join(rk),model=model,model_version=spec.get('version',''),basis=spec['basis'],rank=rank,
      horse=horse,win_probability_pct=pval,score=round(s,6) if s is not None else '',finish_position=position if position is not None else '',
      finish_status=status,won=win,sp=sp or '',sp_decimal=decimal if decimal else '',
      pnl_1u=round((decimal-1) if win else -1,5) if decimal else '',settlement_source=result.get('_result_source',''),
      snapshot_source=e['snapshot'],evidence_status='VERIFIED_PREOFF_FIELD_AND_RESULT',qualified=spec.get('qualified',False) if rank==1 else False,
      sample_type='RECONSTRUCTED' if model!=ARCHIVED else 'ARCHIVED_LIVE_RANKING')
    rows.append(row)
 return rows,held


def metrics(rows):
 n=len(rows);wins=sum(int(r['won']) for r in rows)
 priced=[r for r in rows if r['pnl_1u']!='']
 profit=sum(float(r['pnl_1u']) for r in priced)
 return dict(selections=n,winners=wins,strike_rate_pct=round(100*wins/n,2) if n else '',
    priced_bets=len(priced),sp_missing=n-len(priced),profit_units=round(profit,4),
    profit_eur_5_stake=round(5*profit,2),sp_roi_pct=round(100*profit/len(priced),2) if priced else '')


def aggregate(rows,groups,filename,out):
 arr=[]
 by=collections.defaultdict(list)
 for r in rows:
  for label,filter_rank in (('Rank 1',1),('Rank 2',2),('Combined','both')):
   if filter_rank=='both' or r['rank']==filter_rank:
    by[(r['model'],label,*[r[g] for g in groups])].append(r)
 for key,items in sorted(by.items()):arr.append({'model':key[0],'rank':key[1],**dict(zip(groups,key[2:])),**metrics(items)})
 cols=['model','rank',*groups,*list(metrics([]))]
 write_csv(pathlib.Path(out)/filename,arr,cols)
 return arr


def summarize(entries,rows,issues,held,coverage,out):
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 write_csv(out/'stage101-per-selection.csv',rows,FIELDS)
 write_csv(out/'stage101-exclusions.csv',issues,['date','course','time','model','reason'])
 overall=aggregate(rows,[],'stage101-overall.csv',out)
 aggregate(rows,['date'],'stage101-by-day.csv',out)
 aggregate(rows,['discipline'],'stage101-by-discipline.csv',out)
 aggregate(rows,['discipline','race_type'],'stage101-by-race-type.csv',out)
 aggregate(rows,['course'],'stage101-by-course.csv',out)
 probs=[]
 for model in MODELS:
  for rank in (1,2):
   available=[r for r in rows if r['model']==model and r['rank']==rank and r['win_probability_pct']!='']
   for threshold in THRESHOLDS:
    selection=[r for r in available if float(r['win_probability_pct'])>=threshold]
    probs.append({'model':model,'rank':rank,'minimum_win_pct':threshold,**metrics(selection)})
 write_csv(out/'stage101-probability-thresholds.csv',probs,['model','rank','minimum_win_pct',*list(metrics([]))])
 # A head-to-head result is ONLY valid on races ranked by all four replay groups.
 required={REPLAY_A,REPLAY_B,REPLAY_C,'D_ORIGINAL_REPLAY','D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR'}
 by=collections.defaultdict(dict)
 for r in rows:by[r['race_key']].setdefault(r['model'],[]).append(r)
 paired=[key for key,d in by.items() if required<=set(d) and all(len(d[m])==2 for m in required)]
 paired_rows=[r for r in rows if r['race_key'] in paired and r['model'] in required]
 matched=aggregate(paired_rows,[],'stage101-same-races-only.csv',out)
 coverage_rows=[]
 for e in entries:
  m=by.get('|'.join(e['key']),{})
  coverage_rows.append({'date':e['date'],'course':e['course'],'time':e['time'],'race_type':e['race_type'],'discipline':e['discipline'],
    'race_key':'|'.join(e['key']),'models_with_two_ranks':'; '.join(sorted(k for k,v in m.items() if len(v)==2)),
    'all_seven_replayed_models_present':int('|'.join(e['key']) in paired)})
 write_csv(out/'stage101-coverage.csv',coverage_rows,['date','course','time','discipline','race_type','race_key','models_with_two_ranks','all_seven_replayed_models_present'])
 top2=[]
 for model in MODELS:
  rr=collections.defaultdict(list)
  for r in rows:
   if r['model']==model:rr[r['race_key']].append(r)
  complete=[rs for rs in rr.values() if len(rs)==2]
  top2.append({'model':model,'races_with_two_ranks':len(complete),
   'races_won_by_either':sum(any(x['won'] for x in rs) for rs in complete),
   'either_rank_hit_pct':round(100*sum(any(x['won'] for x in rs) for rs in complete)/len(complete),2) if complete else ''})
 write_csv(out/'stage101-top2-race-coverage.csv',top2,['model','races_with_two_ranks','races_won_by_either','either_rank_hit_pct'])
 record={
  'stage':101,'status':'RESEARCH_COMPARISON_COMPLETED','period':{'start':START,'end':END},
  'historical_source':'Same successful Stage100 V2 run 43089770857 for Stage2 and Stage4',
  'source_provenance':coverage,'verified_races':len(entries),'common_races_all_7_replay_models':len(paired),
  'full_cohort_models':sorted(required),'original_abc_status':'A/B weight replay uses proxy feature projection; not yet independently feature-parity certified; original C uses model C v2 engine',
  'model_versions_untouched':True,'no_repository_data_modified':True,
  'd_family_mapping':{'D family A':'D_ARCHIVED_PREOFF','D family B':'D_ORIGINAL_REPLAY','D family C':'D_C'},
  'recovered_model_rows_by_model':dict(collections.Counter(r['model'] for r in rows)),
  'overall':overall,'paired_only':matched,'top2':top2,
  'missing_model_counts':[{ 'model':m,'missing_races':sum(1 for e in entries if len(by.get('|'.join(e['key']),{}).get(m,[]))!=2)} for m in MODELS],
  'exclusion_count':len(issues),'research_notes':[
    'A/B original fitted coefficients retained but exact original historical feature transformation was not proven equivalent in this runner. Their win% and ROI are only proxy research, not exact model performance.',
    'Model C v2 uses its existing long-context features and trained coefficients, with external tips excluded; this is a backdated replay, not original published live selection.',
    'D_ORIGINAL_REPLAY, D_C and E75 variants use the unchanged Stage95 scorer; E75 are research prototypes, not identical to independently published E v2.',
    'E_V2_STAGE92 only appears where Stage92 had complete archived probabilities; no invented later days or ranks.',
    'Stage99 optimized NH D(c) and E Balanced ranks appear ONLY as a supplementary 11-Nov research simulation, never in the strict seven-model paired cohort.',
    'HPR V2 and comment-enhanced variants do not have a full recent 5-11 Nov pre-race evidence archive; their historical Oct holdouts must not be misrepresented as Nov live performance.',
    'D_ARCHIVED_PREOFF only appears where a dated, valid archived rank-1/rank-2 exists, and is separate from recovered D.',
    'Card snapshots come from git commits no later than 09:45 UTC on each race day; exact final starters required.',
    'Starting prices and outcomes are used only for post-scoring settlement, never model features.',
    'SP ROI uses priced selections only. Strike rate counts every matched settled starter. Non-runners and unmatched fields excluded.',
    'No data, research scripts, trained weights, UI or production workflows are committed or changed.',
    'Zero common matched races is an honest result, not a reason to invent winners or prices.'
  ]}
 (out/'stage101-summary.json').write_text(json.dumps(record,indent=2,default=str)+'\n')
 overall_rank1=[x for x in overall if x['rank']=='Rank 1']
 lines=['# Stage101 — All-model comparison | 5–11 November 2026','',
   f"**Verified pre-off full-field races:** {len(entries)} · **Seven-model common cohort:** {len(paired)}",
   '', '**Important:** This is a reconstructed research replay; it does **not** assert missed picks were published live. Original A/B exact feature parity is **not yet certified**. D archived rankings are tracked separately.',
   '', '| Model | Rank 1 wins / bets | Strike | SP ROI |', '|---|---:|---:|---:|']
 for x in overall_rank1:
  lines.append(f"| {x['model']} | {x['winners']}/{x['selections']} | {x['strike_rate_pct']}% | {x['sp_roi_pct']}% |")
 lines+=['','## Downloads within this artifact','',
   'Read `stage101-same-races-only.csv` for the strict seven-model comparison, `stage101-per-selection.csv` for every ranked horse and finishing position, `stage101-overall.csv` for all Rank 1/Rank 2/combined summaries, and the dated, discipline, race-type and course CSV files for breakouts.',
   '',f"**Coverage exclusions:** {len(issues)} · Missing/unsupported models are recorded explicitly in `stage101-coverage.csv`.",
   '', 'Do not pool Stage95 30-day and Stage101 7-day figures; the same races would be counted twice.',
   '']
 (out/'README.md').write_text('\n'.join(lines))
 print('\n'.join(lines[:15]),flush=True)


def self_test():
 assert sp_decimal('9/4')==3.25 and sp_decimal('EVS')==2.
 assert sp_decimal(None) is None and sp_decimal('') is None
 assert norm('Sea Bay (IRE)')==norm('Sea Bay')
 assert clock('2026-11-08T12:05:00+00:00')=='12:05'
 assert metrics([{'won':1,'pnl_1u':2},{'won':0,'pnl_1u':-1}])['sp_roi_pct']==50.0
 assert stable_p([2,1,0])[0]>stable_p([2,1,0])[1]
 import tempfile
 with tempfile.TemporaryDirectory() as temp:
  write_csv(pathlib.Path(temp)/'ok.csv',[{'model':'A','rank':1}],['model','rank'])
  assert 'model,rank' in (pathlib.Path(temp)/'ok.csv').read_text(encoding='utf-8-sig')
 print('Stage101 tests: fractional SP, name/time normalization, ROI and writer passed',flush=True)


def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test');sub.add_parser('install-engines')
 audit=sub.add_parser('audit-inputs');audit.add_argument('--db',required=True);audit.add_argument('--mapping',required=True)
 run=sub.add_parser('compare');run.add_argument('--db',required=True);run.add_argument('--mapping',required=True)
 run.add_argument('--ratings',required=True);run.add_argument('--start',default=START);run.add_argument('--end',default=END)
 run.add_argument('--output',default='reports/stage101/output')
 a=p.parse_args()
 if a.cmd=='self-test':self_test();return
 if a.cmd=='install-engines':install_engines();return
 if a.cmd=='audit-inputs':audit_inputs(a.db,a.mapping);return
 if a.start!=START or a.end!=END:raise RuntimeError('Stage101 fixed test period is Nov 5-11; no unvalidated date changes')
 audit_inputs(a.db,a.mapping)
 entries,issues,coverage=collect_entries(a.start,a.end)
 if len(entries)<5:raise RuntimeError('Too few exact preoff/result races to make comparison; stop rather than fabricate')
 abc=card_models_original(a.db,a.mapping,entries,issues)
 de=date_rows_de(a.db,a.ratings,entries,issues)
 rows,held=format_rows(entries,abc,de,issues)
 if not rows:raise RuntimeError('No verified ranked selection rows; cannot make numerical claims')
 summarize(entries,rows,issues,held,coverage,a.output)

if __name__=='__main__':main()
