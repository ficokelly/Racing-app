#!/usr/bin/env python3
"""Stage105 — E+HPR Stage102 ROI-first ML applied prospectively to 12 Nov 2026.

Research only; no live app/model/bets changes. Uses exactly the saved E 75/25
with HPR model, Stage102's odds-aware LogisticRegression, and its conservative
past-only ROI risk/coverage policy. Full-field probabilities rank every horse.
Quotes used only as of the actual selection cutoff; no future odds or results.
National Hunt handicaps are excluded from the ROI-qualified shortlist but
remain visible in the full probability rankings. Output is NOT verified bets.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,json,math,pathlib,sqlite3,subprocess,sys
from zoneinfo import ZoneInfo
import numpy as np

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage101_all_models_compare as S101
import stage102_roi_ml_simulation as S102

TARGET='2026-11-12'
MODEL='E_75_25_WITH_HPR'
UTC=dt.timezone.utc
DUBLIN=ZoneInfo('Europe/Dublin')
MAX_QUOTE_AGE_MIN=180
COLS=['date','course','off_utc','discipline','race_type','race_name','race_key','rank','horse',
 'win_probability_pct','base_e_hpr_share_pct','william_hill_decimal','william_hill_fractional',
 'expected_roi_pct','meets_roi_gate','nh_handicap_excluded','roi_shortlist','selection_status',
 'price_collected_utc','quote_age_minutes','decision_cutoff_utc','snapshot_commit_utc','model','model_version',
 'trained_history_races','trained_through','probability_basis']


def to_iso(z):return z.astimezone(UTC).isoformat() if z else ''

def as_datetime(s):return S102.timestamp(s)

def morning_cutoff(day):
 return dt.datetime.combine(dt.date.fromisoformat(day),dt.time(9,45),tzinfo=DUBLIN).astimezone(UTC)

def asof_cutoff(now,day):
 if now.tzinfo is None:raise ValueError('UTC datetime with timezone required')
 return min(now.astimezone(UTC),morning_cutoff(day))

def exclude_nh(discipline,racetype):
 return discipline=='Jumps' and 'HANDICAP' in str(racetype).upper()

def is_nonrunner(card):
 import stage86_hpr_engine as HPR
 return str(card.get('race_status') or card.get('status') or '').strip().lower() in HPR.STOP


def snapshot_date_cards(day,now):
 """Same Git-verified morning racecard source as Stage101; never post-race cards."""
 import stage86_hpr_engine as HPR
 output=HPR.card_dates(ROOT,dt.date.fromisoformat(day),dt.date.fromisoformat(day),git_preoff=True)
 raw=output.get(day,[])
 cmd=['git','log','-1','--format=%H %ct','--until',day+' 09:45:00 +0000','--','data/racecards.json']
 try:
  line=subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True,text=True,timeout=20).stdout.strip()
  sha,epoch=line.split()[:2]
  commit=dt.datetime.fromtimestamp(int(epoch),tz=UTC)
 except (OSError,ValueError,subprocess.SubprocessError) as exc:
  raise RuntimeError('No provable pre-morning Git racecard snapshot') from exc
 if commit>asof_cutoff(now,day):raise RuntimeError('Racecard snapshot is later than decision cutoff')
 grouped=collections.defaultdict(list)
 for c in raw:
  if str(c.get('date') or c.get('race_time') or '')[:10]!=day:continue
  course=S101.norm(c.get('course'));clock=S101.clock(c.get('race_time'))
  if not course or not clock or not c.get('horse'):continue
  if is_nonrunner(c):continue
  key=(day,course,clock)
  grouped[key].append(c)
 issues=[];out=[]
 for key,cards in sorted(grouped.items(),key=lambda pair:(pair[0][2],pair[0][1])):
  if len(cards)<2:
   issues.append({'race_key':'|'.join(key),'reason':'FEWER_THAN_TWO_DECLARED_RUNNERS'});continue
  if len({S101.norm(c['horse']) for c in cards})!=len(cards):
   issues.append({'race_key':'|'.join(key),'reason':'DUPLICATE_RUNNER_NAMES'});continue
  result_stub={'date':day,'course':cards[0]['course'],'runners':[{'horse':c['horse'],'position':''} for c in cards]}
  out.append({'date':day,'course':cards[0]['course'],'time':key[2],'key':key,
              'cards':cards,'result':result_stub,'race_name':cards[0].get('race_name') or '',
              'snapshot':'git_before_morning','snapshot_commit':commit})
 return out,issues,commit


class TodayPriceArchive(S102.PriceArchive):
 def __init__(self,root,day):
  super().__init__(root);self.day=day
 def add_snapshot(self,obj,origin,committed=None):
  for snap in (obj if isinstance(obj,list) else [obj]):
   if not isinstance(snap,dict):continue
   for row in snap.get('prices',[]):
    q=S102.quote_object(row,origin)
    if not q:continue
    if q['key'][0]!=self.day or q['book']!='williamhill':continue
    q['committed']=committed
    self.by_key[q['key']].append(q)
    self.audit['today_wh_rows']+=1
 def load_today(self,now):
  for f in sorted((self.root/'data/odds-archive').glob(self.day+'*.json')):
   try:self.add_snapshot(json.loads(f.read_text(encoding='utf-8')),f.name)
   except (OSError,ValueError):self.audit['unreadable_today_archive']+=1
  # Recover time-bounded versions of the latest odds feed. This is critical
  # if the currently tracked file was overwritten AFTER 09:45; a later file
  # must never masquerade as the quote available at the morning cutoff.
  limit=asof_cutoff(now,self.day)
  try:
   command=['git','log','-160','--format=%H %ct','--until',
            limit.strftime('%Y-%m-%d %H:%M:%S +0000'),'--','data/latest-odds.json']
   output=subprocess.run(command,cwd=self.root,check=True,capture_output=True,text=True,timeout=45).stdout
   for line in output.splitlines():
    try:
     sha,epoch=line.split()[:2]
     committed=dt.datetime.fromtimestamp(int(epoch),tz=UTC)
     if committed>limit:continue
     raw=subprocess.run(['git','show',sha+':data/latest-odds.json'],
         cwd=self.root,check=True,capture_output=True,text=True,timeout=15).stdout
     obj=json.loads(raw)
     collected=S102.timestamp(obj.get('collected_at'))
     if not collected or collected>committed+dt.timedelta(seconds=90):continue
     self.add_snapshot(obj,'git_'+sha[:12],committed)
     self.audit['git_verified_snapshots']+=1
    except (ValueError,OSError,subprocess.SubprocessError):
     self.audit['git_snapshot_skips']+=1
  except (OSError,subprocess.SubprocessError):self.audit['git_history_unavailable']+=1
  # latest-odds.json is a tracked file: the Git commit time is the availability proof.
  source=self.root/'data/latest-odds.json'
  try:
   line=subprocess.run(['git','log','-1','--format=%ct','--','data/latest-odds.json'],
      cwd=self.root,check=True,capture_output=True,text=True,timeout=20).stdout.strip()
   committed=dt.datetime.fromtimestamp(int(line),tz=UTC)
   if committed<=now:
    self.add_snapshot(json.loads(source.read_text(encoding='utf-8')),'latest-odds.json',committed)
   else:self.audit['latest_snapshot_future_commit']+=1
  except (OSError,ValueError,subprocess.SubprocessError):self.audit['latest_snapshot_unavailable']+=1
  return self
 def get_asof(self,entry,horse,cutoff):
  off=S102.off_at(entry)
  if off is None or cutoff>=off:return None
  key=(entry['date'],S102.course(entry['course']),off.strftime('%H:%M'),S101.norm(horse))
  valid=[q for q in self.by_key.get(key,())
         if q['collected']<=cutoff
         and 0<=(cutoff-q['collected']).total_seconds()<=MAX_QUOTE_AGE_MIN*60
         and (q['updated'] is None or q['updated']<=cutoff)
         and (q['committed'] is None or q['committed']<=cutoff)]
  return max(valid,key=lambda q:(q['collected'],q['updated'] or q['collected'])) if valid else None


def build_historical_e_records(entries,de,archive):
 group=collections.defaultdict(list);counts=collections.Counter()
 for e in entries:
  spec=de.get(e['key'],{}).get(MODEL)
  if not spec:
   counts['missing_E_HPR_score']+=1;continue
  n=len(e['cards']);base=S102.base_probs(spec,n)
  if base is None:counts['bad_scores']+=1;continue
  quotes=[archive.get(e,c['horse'],'PREOFF_MORNING0945') for c in e['cards']]
  if sum(q is not None for q in quotes)<max(2,math.ceil(.70*n)):
   counts['incomplete_morning_quotes']+=1;continue
  byhorse=S102.result_map(e)
  actual=[byhorse.get(S101.norm(c['horse'])) for c in e['cards']]
  if any(r is None for r in actual):raise RuntimeError('Historical full-field mismatch')
  yy=[int(S102.intish(r.get('position'))==1) for r in actual]
  if sum(yy)!=1:counts['no_verified_winner']+=1;continue
  arr=np.asarray([S102.ml_features(c,base[i],quotes[i]['price'] if quotes[i] else None,n,
                          e['discipline'],e['race_type']) for i,c in enumerate(e['cards'])],dtype=float)
  group[e['date']].append({'features':arr,'sealed_y':yy,'quotes':quotes,'date':e['date']})
  counts['eligible_training_races']+=1
 return group,dict(counts)


def fit_historical_policy(grouped,day):
 """Historical rolling out-of-sample choices only; current race outcomes never loaded."""
 prior=[];history=[]
 for d in sorted(grouped):
  if d>=day:raise RuntimeError('Training includes target day')
  clf,_=S102.fit_challenger(history)
  if clf is not None:
   for rec in grouped[d]:
    pp=S102.probabilities(clf,rec);choices=[]
    for ev,_,i in S102.evaluated_candidate(rec,pp):
     q=rec['quotes'][i];won=int(rec['sealed_y'][i])
     choices.append({'ev':ev,'odds':q['price'],'probability':float(pp[i]),
                     'profit':q['price']-1 if won else -1.,'won':won})
    prior.append({'date':d,'choices':choices})
  history.extend(grouped[d])
 final,status=S102.fit_challenger(history)
 if final is None:raise RuntimeError(f'Unable to train ROI-first E + HPR ML: {status} / {len(history)} previous races')
 threshold,cap,basis=S102.choose_policy(prior)
 return final,{'prior_races':len(history),'first_training_date':min(grouped),
               'last_training_date':max(grouped),'roi_min_ev':threshold,
               'roi_max_odds':cap,'roi_policy_basis':basis,
               'earlier_out_of_sample_races':len(prior)}


def score_live_e_hpr(db,ratings,entries,day):
 """Stage95 exact E+HPR scoring implementation, no target-day outcomes."""
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
 audit=S101.read_json(ROOT/'reports/stage30/stage30-summary.json')
 frozen=audit.get('model_results',{}).get('D',{})
 if frozen.get('chosen_combination')!='D-064-gap14' or abs(float(frozen.get('selection_margin',0))-.14)>1e-8:
  raise RuntimeError('Original frozen D provenance mismatch; E-HPR replay refused')
 weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
 # This preparation matches Stage101's previous-day feature state. Target-day results never enter.
 fromdate=S102.WARMUP_START;prior=(dt.date.fromisoformat(day)-dt.timedelta(days=1)).isoformat()
 past=D.LivePast({});past.scan_until(db,fromdate)
 form=Cform.Form()
 for group in Cform.rows(db,fromdate):
  for z in group:
   z['field_size']=len(group);form.update(z)
 population,draw_hist=E92.historical_population(db,(dt.date.fromisoformat(fromdate)-dt.timedelta(days=365)).isoformat())
 rs=HPR.load_results(ROOT,max(dt.date.fromisoformat(fromdate)-dt.timedelta(days=90),dt.date(2026,10,8)),dt.date.fromisoformat(prior))[0]
 cards=HPR.card_dates(ROOT,dt.date.fromisoformat(fromdate),dt.date.fromisoformat(prior),git_preoff=True)
 rec=E92.recent_population(rs,cards)
 for d in sorted(rec):population.extend(rec[d])
 people=E92.aggregate_people(population,None)
 updates=S94.load_incremental_history(db,fromdate,prior)
 for d in sorted(updates):
  if d>=day:raise RuntimeError('Future date appeared in E HPR form update')
  S94.add_completed_date(past,form,updates[d])
 rated=sqlite3.connect('file:'+str(pathlib.Path(ratings).resolve())+'?mode=ro',uri=True)
 try:
  candidates=[(e['result'],{'date':day,'course':e['course'],'time':e['time'],'runners':e['cards']},e['cards']) for e in entries]
  scores=S95.score_date(candidates,day,past,form,clf,weights,people,draw_hist,rated,{})
 finally:rated.close()
 out={}
 for r in scores:
  k=(day,S101.norm(r['entry']['course']),r['entry']['time'])
  if MODEL in r['scores_by_model']:
   out[k]=r['scores_by_model'][MODEL]
 return out


def fractional(x):
 from fractions import Fraction
 if not x or x<=1:return ''
 f=Fraction(x-1).limit_denominator(100)
 return f'{f.numerator}/{f.denominator}'


def generate(entries,scoremap,model,prices,cutoff,meta,snapshot_commit):
 rows=[];exclusions=[];rank1=[];value=[]
 for e in entries:
  scores=scoremap.get(e['key'])
  if scores is None:
   exclusions.append({'race_key':'|'.join(e['key']),'reason':'E_HPR_SCORER_DID_NOT_RETURN_RACE'});continue
  n=len(e['cards']);base=S102.base_probs({'scores':scores},n)
  if base is None:
   exclusions.append({'race_key':'|'.join(e['key']),'reason':'E_HPR_BAD_SCORE_VECTOR'});continue
  quotes=[prices.get_asof(e,c['horse'],cutoff) for c in e['cards']]
  x=np.asarray([S102.ml_features(c,base[i],quotes[i]['price'] if quotes[i] else None,
                                    n,e['discipline'],e['race_type']) for i,c in enumerate(e['cards'])],dtype=float)
  pp=S102.probabilities(model,{'features':x})
  if abs(float(sum(pp))-1.)>1e-6:raise RuntimeError('Win probabilities do not total 100%')
  order=sorted(range(n),key=lambda i:(-float(pp[i]),i))
  nh_excl=exclude_nh(e['discipline'],e['race_type'])
  qualified=[]
  for rank,i in enumerate(order,1):
   c=e['cards'][i];q=quotes[i];odds=q['price'] if q else None
   ev=(float(pp[i])*odds-1.) if odds else None
   passed=(ev is not None and ev>=meta['roi_min_ev'] and 1.01<odds<=meta['roi_max_odds'])
   # The ROI-filter is a candidate gate, not a claim of realised profitability.
   status='EXCLUDED_NH_HANDICAP' if nh_excl else ('QUALIFIED_VALUE' if passed else ('NO_VERIFIED_CURRENT_PRICE' if q is None else 'BELOW_HISTORICAL_ROI_GATE'))
   row={'date':e['date'],'course':e['course'],'off_utc':e['time'],
     'discipline':e['discipline'],'race_type':e['race_type'],'race_name':e['race_name'],
     'race_key':'|'.join(e['key']),'rank':rank,'horse':c['horse'],
     'win_probability_pct':round(float(pp[i])*100,3),'base_e_hpr_share_pct':round(float(base[i])*100,3),
     'william_hill_decimal':odds if odds else '',
     'william_hill_fractional':fractional(odds) if odds else '',
     'expected_roi_pct':round(ev*100,2) if ev is not None else '',
     'meets_roi_gate':bool(passed),'nh_handicap_excluded':bool(nh_excl),
     'roi_shortlist':False,'selection_status':status,
     'price_collected_utc':to_iso(q['collected']) if q else '',
     'quote_age_minutes':round((cutoff-q['collected']).total_seconds()/60,1) if q else '',
     'decision_cutoff_utc':to_iso(cutoff),'snapshot_commit_utc':to_iso(snapshot_commit),
     'model':MODEL,'model_version':'Stage102 E+HPR ROI-first ML (Stage105 daily replay)',
     'trained_history_races':meta['prior_races'],'trained_through':meta['last_training_date'],
     'probability_basis':'ML race-normalised estimate, not empirically calibrated live probability'}
   rows.append(row)
   if passed and not nh_excl:qualified.append((ev,row))
   if rank==1 and not nh_excl:rank1.append(row)
  # Select at most one ROI-qualified horse per race, highest EV (may NOT be rank 1).
  if qualified:
   _,best=max(qualified,key=lambda pair:(pair[0],pair[1]['win_probability_pct']))
   best['roi_shortlist']=True
   value.append(best)
 return rows,exclusions,rank1,value


def write_outputs(rows,issues,rank1,value,training,cutoff,day,commit,output,pricing_audit):
 root=pathlib.Path(output);root.mkdir(parents=True,exist_ok=True)
 S101.write_csv(root/'stage105-all-runners-probabilities.csv',rows,COLS)
 S101.write_csv(root/'stage105-rank1-allowed-races.csv',rank1,COLS)
 S101.write_csv(root/'stage105-roi-shortlist.csv',value,COLS)
 S101.write_csv(root/'stage105-issues.csv',issues,['race_key','reason'])
 ordered=sorted(rows,key=lambda r:(r['off_utc'],r['course'],r['rank']))
 S101.write_csv(root/'stage105-by-race-and-rank.csv',ordered,COLS)
 top=sorted([r for r in rows if not r['nh_handicap_excluded']],key=lambda r:(-r['win_probability_pct'],r['off_utc']))
 S101.write_csv(root/'stage105-best-win-probability-all-races.csv',top,COLS)
 audit={'version':'stage105-v1-ROI-first-E-HPR-prospective','date':day,'model':MODEL,
        'selection_cutoff_utc':to_iso(cutoff),'morning_cutoff_utc':to_iso(morning_cutoff(day)),
        'snapshot_commit_utc':to_iso(commit), 'training':training,
        'race_count':len({r['race_key'] for r in rows}), 'runner_count':len(rows),
        'rank1_allowed':len(rank1),'roi_qualified_bets':len(value),
        'available_wh_runner_quotes':sum(bool(r['william_hill_decimal']) for r in rows),
        'excluded_nh_handicap_races':len({r['race_key'] for r in rows if r['nh_handicap_excluded']}),
        'pricing_audit':dict(pricing_audit),'issues':len(issues),
        'provisional':cutoff<morning_cutoff(day),
        'notes':['Research only. No app publication, bet placements or frozen model changes.',
          'A pre-09:45 run is provisional and MUST be rerun after morning selection time.',
          'Historical Stage102 E+HPR ROI policy, model scores and as-of William Hill prices.',
          'NH handicaps remain in full rankings but are excluded from allowed/value shortlists.',
          'Win probabilities are race-normalised ML estimates, not guaranteed or independently calibrated.',
          'Outcome for today was never loaded. No realised ROI is claimed for unfinished races.',
          'The 2026-10-13..2026-11-11 testing window has been studied repeatedly; live test is only one day.',
          'Missing prices are left blank, never replaced with SP or future prices.']}
 (root/'stage105-audit.json').write_text(json.dumps(audit,indent=2,default=str)+'\n',encoding='utf-8')
 lines=['# Stage105 — E + HPR ROI-first selections for '+day,'',
   '**Selection cutoff (UTC):** '+to_iso(cutoff),
   '**Status:** '+('PROVISIONAL / before morning — repeat after 09:45 Ireland' if audit['provisional'] else 'MORNING cutoff; no future prices'),
   f"**Race coverage:** {audit['race_count']} races, {audit['runner_count']} runners; {audit['available_wh_runner_quotes']} eligible WH quote matches.",
   f"**NH handicaps excluded:** {audit['excluded_nh_handicap_races']} races.",
   f"**ROI-qualified:** {len(value)} one-per-race shortlist selections. **Allowed Rank1:** {len(rank1)}.",
   '',f"**Historical ROI gate:** expected ROI >= {100*training['roi_min_ev']:.1f}% and odds <= {training['roi_max_odds']:.1f} decimal; policy: {training['roi_policy_basis']}",
   '', '## Top 20 win-probability rankings (NH handicaps excluded)','',
   '| Time (UTC) | Course | Horse | Rank | Est win % | WH decimal | Value selection |','|---|---|---|---:|---:|---:|---|']
 for r in top[:20]:
  lines.append(f"| {r['off_utc']} | {r['course']} | {r['horse']} | {r['rank']} | {r['win_probability_pct']:.1f}% | {r['william_hill_decimal'] or '—'} | {'Yes' if r['roi_shortlist'] else 'No'} |")
 lines+=['','## ROI-qualified shortlist','',
         '| Time (UTC) | Course | Horse | Win % | WH odds | Model EV |','|---|---|---|---:|---:|---:|']
 for r in sorted(value,key=lambda r:(r['off_utc'],r['course'])):
  lines.append(f"| {r['off_utc']} | {r['course']} | {r['horse']} | {r['win_probability_pct']:.1f}% | {r['william_hill_decimal']} | {r['expected_roi_pct']:+.1f}% |")
 if not value:lines.append('| — | — | No selections passed the historical ROI gate with verified current odds | — | — | — |')
 lines+=['','**Audit:** Probabilities from Stage102 logistic ML conditional on available racecard/quote features; ROI only hypothetical until settled. No race-result data for '+day+' was used.',
         '', '**Files:** `stage105-all-runners-probabilities.csv`, `stage105-rank1-allowed-races.csv`, `stage105-roi-shortlist.csv`, `stage105-by-race-and-rank.csv`, `stage105-best-win-probability-all-races.csv`, `stage105-audit.json`.']
 (root/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 print('\n'.join(lines[:18]),flush=True)
 return audit


def run(args):
 day=args.date
 if day!=TARGET:raise RuntimeError('Stage105 is locked to 2026-11-12; future dates require a fresh dated audit')
 now=as_datetime(args.now) if args.now else dt.datetime.now(UTC)
 if now is None:raise ValueError('Use an ISO 8601 --now with timezone')
 if now.date()<dt.date.fromisoformat(day):raise RuntimeError('Wrong date — run on selection day')
 if now.date()>dt.date.fromisoformat(day):raise RuntimeError('Missed dated selection time; refusing backdated live claim')
 cutoff=asof_cutoff(now,day)
 S101.audit_inputs(args.db,args.mapping)
 print('Stage105 cutoff',to_iso(cutoff),'target',day,flush=True)
 today,issues,commit=snapshot_date_cards(day,now)
 if len(today)<3:raise RuntimeError('Too few current date morning cards; check racecard collector first')
 for e in today:
  import stage94_dc_probability_replay as S94
  e['discipline']=S94.race_disc(e['cards'][0]);e['race_type']=S94.race_cat(e['cards'][0])
 start=S102.WARMUP_START;end=(dt.date.fromisoformat(day)-dt.timedelta(days=1)).isoformat()
 history,history_issues,coverage=S101.collect_entries(start,end)
 if len(history)<100:raise RuntimeError('Insufficient pre-morning historical races for ML')
 print('Stage105 historical results',len(history),'races. Scoring E+HPR.',flush=True)
 de=S101.date_rows_de(args.db,args.ratings,history,history_issues)
 source=S102.PriceArchive(ROOT).load()
 grouped,traincov=build_historical_e_records(history,de,source)
 estimator,traininfo=fit_historical_policy(grouped,day)
 traininfo['coverage']=traincov;traininfo['history_rejected']=len(history_issues)
 print('Stage105 trained ONLY on historical E+HPR',traininfo,flush=True)
 todayscores=score_live_e_hpr(args.db,args.ratings,today,day)
 todayprices=TodayPriceArchive(ROOT,day).load_today(now)
 rows,scoring_issues,rank1,value=generate(today,todayscores,estimator,todayprices,cutoff,traininfo,commit)
 issues.extend(scoring_issues)
 if not rows or not rank1:raise RuntimeError('No legitimate E+HPR probabilities generated; no fabricated selections')
 audit=write_outputs(rows,issues,rank1,value,traininfo,cutoff,day,commit,args.output,todayprices.audit)
 print('Stage105 finished research selections:',audit['race_count'],'races',audit['roi_qualified_bets'],'value-qualified',flush=True)


def self_test():
 x=dt.datetime(2026,11,12,0,55,tzinfo=UTC)
 assert morning_cutoff(TARGET)==dt.datetime(2026,11,12,9,45,tzinfo=UTC)
 assert asof_cutoff(x,TARGET)==x
 assert asof_cutoff(dt.datetime(2026,11,12,11,tzinfo=UTC),TARGET)==morning_cutoff(TARGET)
 assert exclude_nh('Jumps','Novice Handicap Chase')
 assert not exclude_nh('Jumps','Novice Hurdle')
 assert not exclude_nh('AW','Handicap')
 assert fractional(3.5)=='5/2'
 raw={'collected_at':'2026-11-12T08:40:00Z','bookmaker_updated_at':'2026-11-12T08:39:00Z',
      'race_time':'2026-11-12T12:30:00Z','course':'Hereford','horse':'Example Horse',
      'bookmaker':'williamhill','decimal_odds':4.,'stale':False}
 a=TodayPriceArchive(ROOT,TARGET);a.add_snapshot({'prices':[raw]},'test')
 e={'date':TARGET,'course':'Hereford','time':'12:30','cards':[{'race_time':'2026-11-12T12:30:00Z','horse':'Example Horse'}]}
 assert a.get_asof(e,'Example Horse',morning_cutoff(TARGET))['price']==4.
 assert a.get_asof(e,'Example Horse',dt.datetime(2026,11,12,8,30,tzinfo=UTC)) is None
 r=dict(raw,collected_at='2026-11-12T10:00:00Z',bookmaker_updated_at='2026-11-12T09:59:00Z')
 b=TodayPriceArchive(ROOT,TARGET);b.add_snapshot({'prices':[r]},'late')
 assert b.get_asof(e,'Example Horse',morning_cutoff(TARGET)) is None
 # A current-race finish or SP cannot enter the Stage102 feature function.
 c={'age':6,'official_rating':121};l=S102.ml_features(c,.33,4.,8,'Jumps','Novice Hurdle')
 assert l==S102.ml_features({**c,'position':1,'sp_decimal':30.,'winner':1},.33,4.,8,'Jumps','Novice Hurdle')
 assert len(l)==16
 print('Stage105 self-test passed: dated 09:45 lock, handicap exclusion, no future quote or result leakage',flush=True)


def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
 sub.add_parser('self-test')
 r=sub.add_parser('run');r.add_argument('--db',required=True);r.add_argument('--mapping',required=True)
 r.add_argument('--ratings',required=True);r.add_argument('--date',default=TARGET)
 r.add_argument('--output',default='reports/stage105/output');r.add_argument('--now',default=None)
 a=p.parse_args()
 if a.cmd=='self-test':self_test()
 else:run(a)

if __name__=='__main__':main()
