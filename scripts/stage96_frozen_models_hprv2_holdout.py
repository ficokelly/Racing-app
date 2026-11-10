#!/usr/bin/env python3
"""Stage 96: strictly chronological, matched-field D/D(c)/E research replay.

Compare reconstructed pre-race D, D(c), E75-no-HPR and E75-with-old-HPR
against FIXED overlays from strictly previous-day HPR v2 (comments, beaten
lengths, conditions, breeding). All eight variants get Rank 1, Rank 2 and
race-normalised win %. Training dates 12-18 Oct 2026, holdout 19-23 Oct 2026.

The older E75 scores are reconstructed RESEARCH MODELS, not archived live picks.
Archived E v2 October probabilities were fitted using October outcomes and
MUST NOT be called independent; intentionally EXCLUDED from this holdout.

Neither odds nor SP enter scores or probability calibration. Kaggle v129 only
settles predictions after results. No production files, HPR archives, live
ratings, workflows or models are changed.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,io,json,math,pathlib,re,statistics,sys,zipfile

VERSION='stage96-fixed-hprv2-oos-v1'
START='2026-10-12'
REPLAY_SEED='2026-10-11'  # Exact 30-day Stage95 initial state date
TRAIN_END='2026-10-18'
TEST_START='2026-10-19'
END='2026-10-23'
BASE=('D_ORIGINAL','D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR')
MODELS=tuple(v for b in BASE for v in (b,b+'_HPRV2'))
TEMPERATURES=(.65,.85,1.05,1.3,1.65,2.1,2.7)
THRESHOLDS=(20,25,30,34,40,42,45,50)
HPR_ALPHA=.35  # Pre-fixed, NOT selected on holdout outcomes.
HPR_DENOM=20.0
HPR_CAP=.70
MIN_HPR_COVERAGE=.70
FIELDS=('date','course','time_utc','time_local','race_id','discipline','race_type','model','rank',
        'horse','win_probability_pct','position','won','sp','sp_decimal','price_status',
        'hpr_coverage_pct','hpr_overlay_active','data_split','source_preoff_status','research_only')


def norm(v):
 import unicodedata
 s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().upper()
 return re.sub('[^A-Z0-9]','',s)


def venue(v):
 return norm(re.sub(r'\((?:AW|IRE|GB|UK)\)','',str(v or ''),flags=re.I))


def number(v):
 try:
  if v is None or str(v).strip()=='':return None
  x=float(v)
  return x if math.isfinite(x) else None
 except (ValueError,TypeError):return None


def zscores(v):
 if len(v)<2:raise ValueError('Need 2+ starters')
 arr=[float(x) for x in v]
 if any(not math.isfinite(x) for x in arr):raise ValueError('Invalid pre-race score')
 middle=statistics.mean(arr)
 scale=statistics.pstdev(arr)
 return [(x-middle)/(scale if scale>1e-8 else 1.) for x in arr]


def overlay(base,form):
 """Fixed bounded adjustment; unknown HPR is median/neutral, not zero."""
 valid=[float(v) for v in form if v is not None]
 if len(valid)<2 or len(valid)/len(form)<MIN_HPR_COVERAGE:
  return list(base),False
 med=statistics.median(valid)
 out=[]
 for raw,h in zip(base,form):
  delta=0. if h is None else max(-HPR_CAP,min(HPR_CAP,HPR_ALPHA*(h-med)/HPR_DENOM))
  out.append(raw+delta)
 return out,True


def softmax(scores,temp):
 if temp<=0:raise ValueError('Temperature must be positive')
 m=max(scores);w=[math.exp((float(x)-m)/temp) for x in scores]
 return [x/sum(w) for x in w]


def training_temperature(cohort):
 """Calibrate ONE temperature on TRAINING D-original races only.
 All eight models share it, avoiding per-variant multiple-testing choices.
 """
 if len(cohort)<25:raise RuntimeError('Insufficient earlier D training races')
 losses=[]
 for temp in TEMPERATURES:
  loss=statistics.mean(-math.log(max(1e-12,softmax(r['vectors']['D_ORIGINAL'],temp)[r['winner_idx']])) for r in cohort)
  losses.append((loss,temp))
 return min(losses)[1], {str(t):round(n,5) for n,t in losses}


def load_hpr(zip_path):
 """Stage94 *pre*-race score joined by exact date/course/local off and whole field.
    Excludes duplicate keys and ambiguous source race IDs.
 """
 import stage95a_hpr_v2_out_of_sample as A
 groups=collections.defaultdict(list)
 with zipfile.ZipFile(zip_path) as z:
  with gzip.open(z.open('stage94-hpr-v2-research.csv.gz'),'rt',encoding='utf-8',newline='') as fh:
   for row in csv.DictReader(fh):
    d=row.get('date','')
    if not START<=d<=END:continue
    off=A.clock(row.get('off'))
    if not off:continue
    k=(d,venue(row.get('course')),off)
    groups[k].append(row)
 out={};counts=collections.Counter()
 for k,rows in groups.items():
  names=[norm(r.get('horse')) for r in rows]
  ids={r.get('source_race_id') for r in rows}
  if len(names)!=len(set(names)) or len(ids)!=1 or '' in ids:
   counts['duplicate_or_conflicting_hpr_race']+=1;continue
  out[k]=rows
 return out,dict(counts)


def match_hpr(race,hpr_by_clock):
 """If no unique full-field HPR match, skip entire race (no false pedigree join)."""
 import stage95a_hpr_v2_out_of_sample as A
 d=race['date']; c=venue(race['entry']['course']);local=A.utc_to_local_clock(d,race['entry']['time'])
 rows=hpr_by_clock.get((d,c,local))
 if not rows:return None,'NO_EXACT_HPR_RACE'
 names=[norm(x['horse']) for x in race['cards']]
 if set(names)!={norm(r['horse']) for r in rows} or len(names)!=len(rows):
  return None,'HPR_FIELD_DISAGREES'
 indexed={norm(r['horse']):r for r in rows}
 if not all(indexed[n].get('horse_identity','').startswith('PEDIGREE_SHA256_') for n in names):
  return None,'UNVERIFIED_HPR_IDENTITY'
 return [indexed[n] for n in names],None


def settle_prices(key,source_hpr_row,outcome,sp):
 import stage95a_hpr_v2_out_of_sample as A
 pos=A.position(outcome.get('position'))
 status=str(outcome.get('status') or '').lower()
 if status in {'nr','non-runner','nonrunner','withdrawn','scratched','dns'}:
  return '',None,'NON_STARTER_NOT_SETTLED'
 # Unfinished runners can be losers with a verified SP: exact source race ID
 # and runner identity are required. Kaggle records nonnumeric finish as None.
 return A.settle(key,source_hpr_row['horse'],pos==1,pos,sp)


def outcome_metrics(selections):
 n=len(selections);wins=sum(int(x['won']) for x in selections)
 priced=[x for x in selections if number(x.get('sp_decimal')) is not None]
 pnl=sum((float(x['sp_decimal'])-1 if int(x['won']) else -1) for x in priced)
 return {'selections':n,'winners':wins,'strike_rate_pct':round(wins/n*100,2) if n else None,
         'priced':len(priced),'sp_missing':n-len(priced),'profit_1u':round(pnl,3),
         'sp_roi_pct':round(100*pnl/len(priced),2) if priced else None,
         'mean_predicted_win_pct':round(statistics.mean(float(x['win_probability_pct']) for x in selections),2) if selections else None}


def write_csv(path,rows,columns):
 with pathlib.Path(path).open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(rows)


def prepare_frozen_replay(root,db,ratings,artifact_hpr):
 import sqlite3
 import numpy as np
 import stage95_five_model_paired_replay as S95
 import stage94_dc_probability_replay as S94
 import stage86_hpr_engine as HPR
 import stage92_model_e_winprob as E92
 import stage65p_race_type_c_only as C
 import stage38_model_dz_live as D
 import rhp_stage83_dc_integrated as S83
 import stage30_gap_aware_abcd as OLD

 audit=json.loads((root/'reports/stage30/stage30-summary.json').read_text())
 frozen=audit['model_results']['D']
 if frozen.get('chosen_combination')!='D-064-gap14' or abs(float(frozen.get('selection_margin',0))-.14)>1e-8:
  raise RuntimeError('Frozen D criteria changed; refuse comparison')
 weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
 C.rows=S83.corrected_rows
 clf=S83.patched_classifier(None,C,str(db))
 results=HPR.load_results(root,dt.date.fromisoformat(REPLAY_SEED),dt.date.fromisoformat(END))[0]
 cards_by_date=HPR.card_dates(root,dt.date.fromisoformat(REPLAY_SEED),dt.date.fromisoformat(END),git_preoff=True)
 changes=S94.load_incremental_history(str(db),REPLAY_SEED,END)
 past=D.LivePast({});past.scan_until(str(db),REPLAY_SEED)
 form=C.Form()
 for group in C.rows(str(db),REPLAY_SEED):
  for v in group:
   v['field_size']=len(group);form.update(v)
 population,draw_hist=E92.historical_population(str(db),(dt.date.fromisoformat(REPLAY_SEED)-dt.timedelta(days=365)).isoformat())
 earlier=HPR.load_results(root,dt.date(2026,10,8),dt.date.fromisoformat(END))[0]
 cards_earlier=HPR.card_dates(root,dt.date(2026,10,8),dt.date.fromisoformat(END),git_preoff=True)
 recent=E92.recent_population(earlier,cards_earlier)
 for d in sorted(recent):population.extend(recent[d])
 people=E92.aggregate_people(population,None)
 conn=sqlite3.connect('file:'+str(ratings.resolve())+'?mode=ro',uri=True)
 cache={};scored=[];rejections=collections.Counter()
 try:
  for daynum in range((dt.date.fromisoformat(END)-dt.date.fromisoformat(REPLAY_SEED)).days+1):
   date=(dt.date.fromisoformat(REPLAY_SEED)+dt.timedelta(days=daynum)).isoformat()
   known=HPR.by_race_cards(cards_by_date.get(date,[]))
   candidates=[]
   for key,race in results.items():
    if key[:10]!=date:continue
    matched,status=HPR.card_match(race,known)
    if status!='PREOFF_FIELD_VERIFIED':rejections[status]+=1;continue
    cards,ok=S94.verify_preoff_field(matched,race)
    if ok!='OK':rejections[ok]+=1;continue
    entry={'date':date,'course':cards[0]['course'],'time':key[-5:],'runners':cards}
    candidates.append((race,entry,cards))
   for obj in S95.score_date(candidates,date,past,form,clf,weights,people,draw_hist,conn,cache):
    if obj['winner_idx'] is None or obj['disc']=='Unknown':
     rejections['NO_WINNER_OR_DISCIPLINE']+=1;continue
    # Check each starter's result against race snapshots (no invented placings).
    field={norm(q.get('horse')):q for q in obj['race']['runners']}
    if len(field)!=len(obj['cards']) or set(field)!={norm(c['horse']) for c in obj['cards']}:
     rejections['STARTER_SET_CHANGED']+=1;continue
    vectors={b:zscores(list(obj['scores_by_model'][b])) for b in BASE}
    if date>=START:scored.append({**obj,'vectors':vectors})
   S94.add_completed_date(past,form,changes.get(date,[]))
   print('Stage96 pre-off replay',date,'eligible',len(scored),flush=True)
 finally:conn.close()
 return scored,dict(rejections)


def run(args):
 import stage95a_hpr_v2_out_of_sample as A
 out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
 root=pathlib.Path(args.root);db=pathlib.Path(args.db);ratings=pathlib.Path(args.ratings)
 if not args.stage94.is_file():raise RuntimeError('Pinned Stage94 artifact missing')
 hpr,hpr_flags=load_hpr(args.stage94)
 original,rej=prepare_frozen_replay(root,db,ratings,args.stage94)
 accepted=[];excluded=collections.Counter()
 for r in original:
  histories,reason=match_hpr(r,hpr)
  if reason:excluded[reason]+=1;continue
  r['hpr_rows']=histories
  prior=[number(h.get('pre_race_hpr_v2')) for h in histories]
  r['coverage']=sum(x is not None for x in prior)/len(prior)
  r['active']=r['coverage']>=MIN_HPR_COVERAGE
  for base in BASE:
   enhanced,enabled=overlay(r['vectors'][base],prior)
   r['vectors'][base+'_HPRV2']=enhanced
   assert enabled==r['active']
  accepted.append(r)
 train=[r for r in accepted if r['date']<=TRAIN_END]
 hold=[r for r in accepted if r['date']>=TEST_START]
 calibration=[r for r in original if r['date']<=TRAIN_END]
 if len(calibration)<25 or len(train)<15 or len(hold)<40:
  raise RuntimeError(f'Insufficient strictly prior training / paired holdout: {len(calibration)} training races, {len(train)} matched training, {len(hold)} matched test')
 temp,losses=training_temperature(calibration)
 prices,ps=A.sp_map(args.kaggle)
 selected=[];runners=[];duplicates=set();nll=collections.defaultdict(list);brier=collections.defaultdict(list)
 for race in accepted:
  d=race['date'];split='TRAIN' if d<=TRAIN_END else 'HOLDOUT'
  card=race['cards'];by_horse={norm(z['horse']):z for z in race['race']['runners']}
  histories=race['hpr_rows'];source_rid=histories[0]['source_race_id']
  local=A.utc_to_local_clock(d,race['entry']['time'])
  price_key=(d,venue(race['entry']['course']),source_rid,local)
  finish=[A.position(by_horse[norm(z['horse'])].get('position')) for z in card]
  if sum(p==1 for p in finish)!=1:raise RuntimeError('Verified winner missing for '+str(price_key))
  gold=finish.index(1)
  for variant in MODELS:
   raw=race['vectors'][variant]
   probs=softmax(raw,temp)
   assert abs(sum(probs)-1)<1e-9
   if split=='HOLDOUT':
    nll[variant].append(-math.log(max(1e-12,probs[gold])))
    brier[variant].append(sum((x-(i==gold))**2 for i,x in enumerate(probs)))
   order=sorted(range(len(card)),key=lambda i:(-probs[i],i))
   for rank,i in enumerate(order,1):
    h=histories[i];q=card[i]
    sp,dec,stat=settle_prices(price_key,h,by_horse[norm(q['horse'])],prices)
    item={
     'date':d,'course':race['entry']['course'],'time_utc':race['entry']['time'],'time_local':local,
     'race_id':source_rid,'discipline':race['disc'],'race_type':race['type'],
     'model':variant,'rank':rank,'horse':q['horse'],'win_probability_pct':round(100*probs[i],4),
     'position':finish[i] if finish[i] is not None else 'UNFINISHED','won':int(i==gold),
     'sp':sp,'sp_decimal':dec,'price_status':stat,'hpr_coverage_pct':round(race['coverage']*100,2),
     'hpr_overlay_active':int(race['active']),'data_split':split,
     'source_preoff_status':'EXACT_PREOFF_AND_HPR_KAGGLE_FIELD','research_only':True}
    if rank<=2:selected.append(item)
    if split=='HOLDOUT':runners.append(item)
  k=(d,venue(race['entry']['course']),race['entry']['time'])
  if k in duplicates:raise RuntimeError('Duplicate race in match: '+str(k))
  duplicates.add(k)
 # Same train/test, prices and ROI rules for every model.
 scorecards=[];thresholds=[];segments=[];daily=[];proper=[]
 for model in MODELS:
  for rank in (1,2,'1+2'):
   rows=[z for z in selected if z['model']==model and z['data_split']=='HOLDOUT' and (rank=='1+2' or z['rank']==rank)]
   scorecards.append(dict(model=model,rank=rank,**outcome_metrics(rows)))
   for t in THRESHOLDS:
    subset=[x for x in rows if float(x['win_probability_pct'])>=t]
    thresholds.append(dict(model=model,rank=rank,min_probability_pct=t,**outcome_metrics(subset)))
   for key in ('discipline','race_type'):
    for v in sorted({z[key] for z in rows}):
     group=[z for z in rows if z[key]==v]
     segments.append(dict(model=model,rank=rank,group_by=key,group=v,**outcome_metrics(group)))
   for day in sorted({z['date'] for z in rows}):
    group=[z for z in rows if z['date']==day]
    daily.append(dict(model=model,rank=rank,date=day,**outcome_metrics(group)))
  proper.append(dict(model=model,holdout_races=len(nll[model]),mean_log_loss=round(statistics.mean(nll[model]),5),
                     mean_multiclass_brier=round(statistics.mean(brier[model]),5)))
 cols=['model','rank',*outcome_metrics([])]
 write_csv(out/'stage96-scorecard.csv',scorecards,cols)
 write_csv(out/'stage96-thresholds.csv',thresholds,['model','rank','min_probability_pct',*outcome_metrics([])])
 write_csv(out/'stage96-race-types.csv',segments,['model','rank','group_by','group',*outcome_metrics([])])
 write_csv(out/'stage96-daily.csv',daily,['model','rank','date',*outcome_metrics([])])
 write_csv(out/'stage96-all-rank1-rank2.csv',selected,FIELDS)
 with gzip.open(out/'stage96-holdout-all-runners.csv.gz','wt',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(runners)
 write_csv(out/'stage96-probability-accuracy.csv',proper,['model','holdout_races','mean_log_loss','mean_multiclass_brier'])
 report={
  'stage':96,'version':VERSION,'status':'RESEARCH_ONLY_NO_LIVE_CHANGES',
  'dates':{'train_start':START,'train_end':TRAIN_END,'holdout_start':TEST_START,'holdout_end':END},
  'replayed_frozen_model_races':len(original),'matched_train_races':len(train),
  'matched_heldout_races':len(hold),'matched_total':len(accepted),
  'selected_models':list(MODELS),
  'probability_fit':{'temperature':temp,'train_D_races':len(calibration),'training_only_nll_by_temperature':losses,
    'same_temperature_all_models':True,'no_temperature_fit_on_heldout':True},
  'hpr':{'source_run':args.source94_run,'min_complete_field_coverage_pct':70,
         'overlay_active_holdout':sum(x['active'] for x in hold),
         'overlay_neutral_holdout':sum(not x['active'] for x in hold),
         'fixed_alpha':HPR_ALPHA,'fixed_margin_scale':HPR_DENOM,'fixed_additive_cap':HPR_CAP},
  'prices':ps,'matched_exclusions':dict(excluded),'frozen_preoff_rejections':rej,
  'stage94_link_warnings':hpr_flags,'scorecard':scorecards,'proper_scoring':proper,
  'limitations':[
    'Comparison includes unchanged frozen D and D(c) recipe, and TWO RECONSTRUCTED E75 research formulas; not exact archived live E picks.',
    'Archived E v2 published probabilities were fitted partly on October 2026 outcomes; excluded from independent heldout benchmark.',
    'E model research components may have originally been chosen after October evaluation; holdout is independent only for THIS fixed overlay / temperature fit, not a fully prospective untouched experiment.',
    'The selected 2026-10-19..23 holdout dates were previously reviewed in Stage95A/B; decisions about HPR use can be exploratory, not truly prospective.',
    'Comment intelligence is deterministic Stage91 pattern matching, not an LLM understanding comments.',
    'Training uses 2026-10-12..18 race outcomes to fit ONE global D temperature. Scores/overlays are fixed in advance.',
    'Exact complete pre-race card and source race identity match required; no name-only history joins or inferred SP.',
    'HPR v2 pre-race ratings include only earlier racing DAYS; missing HPR uses median neutral, not zero.',
    'Full 30-day recent Kaggle running comments after 2026-10-23 unavailable; cannot claim full 30-day independent comparison.',
    'Model selection and probabilities do not use results or prices of target or later races.',
    'Small samples, selection bias and wide ROI variance limit investment/betting conclusions; source licensing must be checked.',
  ],
 }
 (out/'stage96-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'train':len(train),'holdout':len(hold),'temperature':temp,'hpr_active':report['hpr']['overlay_active_holdout'],
   'rank1': [x for x in scorecards if x['rank']==1]},indent=2),flush=True)
 return report


def self_test():
 assert abs(sum(zscores([10,20,30])))<1e-10
 a=[.5,-.2,1.1];b,active=overlay(a,[None,None,None]);assert not active and a==b
 b,active=overlay(a,[70,50,20]);assert active and b[0]>a[0] and b[2]<a[2]
 b,active=overlay(a,[70,50,None]);assert not active and a==b
 p=softmax([1,2,3],1.2);assert abs(sum(p)-1)<1e-9 and p[2]>p[0]
 race={'date':'2026-10-15','vectors':{'D_ORIGINAL':[.2,.1,-.1]},'winner_idx':0}
 samples=[race]*30
 t,l=training_temperature(samples);assert t in TEMPERATURES
 try:training_temperature(samples[:3])
 except RuntimeError:pass
 else:raise AssertionError('Small calibration erroneously accepted')
 s=outcome_metrics([{'won':1,'sp_decimal':3.,'win_probability_pct':45.},
                    {'won':0,'sp_decimal':2.,'win_probability_pct':30.}]);assert s['sp_roi_pct']==50.
 assert venue('Dundalk (AW) (IRE)')=='DUNDALK'
 assert number('') is None
 print('PASS Stage96: fixed HPR overlay, missing neutral, probability mass, holdout gating, equal-stakes ROI')


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--self-test',action='store_true')
 p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
 p.add_argument('--db',type=pathlib.Path,default=pathlib.Path('reports/stage96/base/backfilled_research_only.sqlite'))
 p.add_argument('--ratings',type=pathlib.Path,default=pathlib.Path('reports/stage96/hpr-ratings.sqlite'))
 p.add_argument('--stage94',type=pathlib.Path)
 p.add_argument('--source94-run',type=int,default=42895103772)
 p.add_argument('--kaggle',type=pathlib.Path)
 p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage96/output'))
 a=p.parse_args()
 if a.self_test:self_test();return
 if not a.db.is_file() or not a.ratings.is_file():p.error('Research DB and HPR v0 ledger required')
 if a.stage94 is None or not a.stage94.is_file():p.error('Stage94 artifact ZIP required')
 if a.kaggle is None or not a.kaggle.is_dir():p.error('Kaggle v129 CSV directory required')
 run(a)

if __name__=='__main__':main()
