#!/usr/bin/env python3
"""Prospective, race-type routed ROI Specialist Hot Pots display feed.

Only the exact pre-off predictions from frozen specialist models are used.
Never infer a selection from SP, a bookmaker price, a race result or another model.
Preserve a race's first published picks when the race is off; revised declared
fields need a fresh underlying specialist prediction before publication.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, re
from zoneinfo import ZoneInfo
UTC=dt.timezone.utc
DUBLIN=ZoneInfo('Europe/Dublin')

ROUTES={
 'novice hurdle':'G5_NETWORK_QUARTERLY',
 'maiden hurdle':'D142_LONG_CORE_FORM_OR_WEIGHT',
 'handicap hurdle':'STAGE143_TYPE_EVIDENCE',
 'novice handicap hurdle':'STAGE146_A_RETRAINED_TRENDS',
 'beginners chase':'D142_LONG_CORE_FORM_HPR',
 'novice handicap chase':'D137_HPR_ABILITY_OR_WEIGHT',
 'handicap chase':'D137_HPR_ABILITY_GRADE_OR_WEIGHT',
 'nh flat open':'D137_HPR_ABILITY_OR_WEIGHT',
 'aw maiden':'D142_LONG_CORE_FORM_OR_WEIGHT',
 'aw nursery':'STAGE143_TYPE_EVIDENCE',
 'auction restricted':'STAGE143_TYPE_EVIDENCE',
 'aw handicap':'D_FORM_WEIGHT_GRADE__ROI',
}
# Stage156: alternative specialist arms are a research comparison, not promises of profitability.
# Route only unsupported race types; preserve previously deployed Stage153 decisions.
ROUTE_MAP_FILE=pathlib.Path(__file__).resolve().parent.parent/'data'/'roi-racetype-route-map.json'
def load_route_map():
 try:
  payload=json.loads(ROUTE_MAP_FILE.read_text())
  routes=payload.get('routes',{})
  assert len(routes)>=50, 'Incomplete route catalogue'
  assert all(v.get('model') and v.get('historical_races',0)>0 for v in routes.values())
  return routes
 except (OSError,ValueError,AssertionError) as e:
  raise RuntimeError('Stage156 research type route map missing or incomplete: '+str(e)) from e

# Load the type map during import, including specialist names required for source ingestion.
STAGE156_ROUTES=load_route_map()
for code,settings in STAGE156_ROUTES.items():
 if code not in ROUTES:ROUTES[code]=settings['model']

FILES=(
 'stage141-today-all-runners.csv',
 'stage151-d142-core-form-weight-all-runners.csv',
 'stage152-strongest-roi-specialists-all-runners.csv',
 'stage146-today-all-runners.csv',
 'stage128-g5-all-horses.csv',
)

def normalize(s): return ''.join(c for c in str(s or '').casefold() if c.isalnum())
def utcnow(): return dt.datetime.now(UTC)
def iso(t): return t.astimezone(UTC).isoformat()
def parse_time(s):
 try:
  t=dt.datetime.fromisoformat(str(s or '').replace('Z','+00:00'))
  if t.tzinfo is None:return None
  return t.astimezone(UTC)
 except (ValueError,TypeError):return None

def num(v):
 try:
  x=float(v)
  return x if math.isfinite(x) else None
 except (TypeError,ValueError):return None

def type_for(runners):
 """Typed course and race name, no bookmaker odds or results involved."""
 f=runners[0];name=str(f.get('race_name') or '').casefold();typ=str(f.get('type') or f.get('race_type') or '').casefold()
 surface=str(f.get('surface') or '').casefold()
 course=normalize(f.get('course'))
 jumps=('hurdle' in name or 'hurdle' in typ or 'chase' in name or 'chase' in typ
        or 'steeple' in name or 'bumper' in name or 'nh flat' in name or 'national hunt flat' in name
        or 'nhf' in typ)
 if 'hurdle' in name or 'hurdle' in typ:
  if 'handicap' in name:
   return 'novice handicap hurdle' if re.search(r'\bnovice|\bnovices',name) else 'handicap hurdle'
  return 'maiden hurdle' if 'maiden' in name else 'novice hurdle' if 'novice' in name else None
 if 'chase' in name or 'chase' in typ or 'steeple' in name:
  if 'handicap' in name:
   return 'novice handicap chase' if 'novice' in name else 'handicap chase'
  return 'beginners chase' if 'beginner' in name else None
 if 'bumper' in name or 'nh flat' in name or 'national hunt flat' in name or 'nhf' in typ:
  return 'nh flat open'
 if jumps:return None
 aw=course in {'dundalk','chelmsford','chelmsfordcity','wolverhampton'} or ('aw' in surface or 'polytrack' in surface or 'tapeta' in surface or 'synthetic' in surface)
 if not aw and course in {'kempton','kemptonpark','lingfield','southwell','newcastle'}:
  aw=not any(x in surface for x in ('turf','grass'))
 if not aw:return None # Never apply AW ROI evidence to turf unvalidated.
 if 'nursery' in name:return 'aw nursery'
 if 'handicap' in name:return 'aw handicap'
 if 'auction' in name or 'restricted' in name:return 'auction restricted'
 if 'maiden' in name:return 'aw maiden'
 return None

def model_score(row):
 for field in ('model_win_probability','win_probability','raw_probability',
               'model_score_not_calibrated_probability','raw_score_not_calibrated_win_pct',
               'raw_model_score_not_calibrated_win_pct','raw_score'):
  v=num(row.get(field))
  if v is not None:return v
 return None

def off_of(row):
 t=parse_time(row.get('race_time'))
 if t:return t
 day=str(row.get('date') or ''); clock=str(row.get('time_utc') or row.get('off_utc') or row.get('time') or '')
 try:return dt.datetime.fromisoformat(day+'T'+clock[:5]+':00+00:00')
 except ValueError:return None

def read_sources(dirs,target):
 grouped=collections.defaultdict(list); stats=collections.Counter(); route_by_race={}
 for dirname in dirs:
  root=pathlib.Path(dirname)
  for filename in FILES:
   for path in root.rglob(filename):
    source_asof=None
    if filename=='stage128-g5-all-horses.csv':
     try:
      audit=json.loads((path.parent/'stage128-g5-paper-audit.json').read_text())
      source_asof=parse_time(audit.get('asof_utc'))
     except (OSError,ValueError):pass
     if source_asof is None:
      stats['missing_g5_preoff_audit']+=1
      continue
    with path.open(encoding='utf-8-sig',newline='') as f:
     for row in csv.DictReader(f):
      if source_asof is not None:row['prediction_asof']=iso(source_asof)
      if row.get('date')!=target:continue
      model=row.get('model','')
      if model not in ROUTES.values():continue
      when=off_of(row)
      if when is None:stats['invalid_time']+=1;continue
      meeting=row.get('meeting') or row.get('course')
      stamp=when.strftime('%Y-%m-%d %H:%M')
      course_key=normalize(meeting)
      key=(stamp,course_key,model)
      grouped[key].append(row);stats['rows']+=1
      # Stage147 provides one canonical historical 50-type route for every race.
      # It is pre-off metadata, not a result/odds input.
      route=str(row.get('route') or '').strip()
      if route and model.startswith(('STAGE143_','STAGE146_')):
       race_key=(stamp,course_key)
       if race_key in route_by_race and route_by_race[race_key]!=route:
        stats['route_conflicts']+=1
       else:route_by_race[race_key]=route
 return grouped,stats,route_by_race

def declared(cards,target):
 grouped=collections.defaultdict(list)
 for r in cards.get('runners',[]):
  if str(r.get('date'))!=target or not r.get('horse') or not r.get('race_time'):continue
  off=parse_time(r['race_time'])
  if off is None:continue
  k=(off.strftime('%Y-%m-%d %H:%M'),normalize(r.get('course')))
  grouped[k].append(r)
 return grouped

def nonrunner(r):
 return any(str(r.get(k) or '').casefold().replace('-','').replace('_','') in ('true','1','yes','nr','nonrunner','withdrawn','scratched','dns')
   for k in ('is_non_runner','is_withdrawn','withdrawn','runner_status','status'))

def prose(horse,card,pred,rank,leader,other,group,source):
 parts=[]
 form=str(card.get('form') or '').replace('-','').strip()
 if form and form not in ('None','-'):
  digits=[v for v in form if v in '1234567890']
  if digits:
   last=digits[-1]
   fact=('won last time' if last=='1' else 'finished second on the latest recorded run' if last=='2'
         else 'finished third last time' if last=='3' else 'was outside the first three last time')
   parts.append(f'{horse} {fact}.')
 rating=num(card.get('ofr') or card.get('official_rating'))
 if rating is not None:parts.append(f'Officially rated {rating:g}.')
 hpr=num(pred.get('hpr'))
 if hpr is not None:parts.append(f'The historical performance rating is {hpr:g}.')
 days=num(card.get('days_since_last_run') or card.get('days_since_run'))
 if days is not None and 0<=days<=700:
  parts.append(f'Last raced {int(days)} days ago.')
 if group=='aw handicap' or 'handicap' in group:
  lbs=num(card.get('lbs') or card.get('weight_lbs'))
  if lbs is not None:parts.append(f'Carries {int(lbs)}lb in this handicap.')
 if group=='novice hurdle' and num(pred.get('win_probability')) is not None:
  parts.append('The G5 network ranks its recent form and opposition favourably for this novice hurdle.')
 elif source=='STAGE146_A_RETRAINED_TRENDS':
  trends=str(pred.get('trend_source') or '')
  parts.append('Race-type trends add to the form-based comparison'+(f' ({trends})' if trends else '')+'.')
 else:
  parts.append('The selected specialist places its historical form and race-type evidence ahead of the opposition.')
 if rank==1 and other:parts.append(f'{other} is the nearest challenger in these ratings.')
 elif rank==2:parts.append(f'{leader} is ranked just ahead.')
 elif rank>2:parts.append(f'{leader} sets the standard on the specialist ranking.')
 if not form and rating is None and hpr is None:
  parts=[f'{horse} is ranked {rank} in this {group} by the {source.replace("_"," ")} specialist.',
         'Verified individual form reasons are not available in this data snapshot, so no specific form claim is made.']
 return ' '.join(parts)

def build(args):
 now=utcnow();target=args.date or now.astimezone(DUBLIN).date().isoformat()
 local=now.astimezone(DUBLIN).date()
 if target not in {local.isoformat(),(local+dt.timedelta(days=1)).isoformat()}:
  raise RuntimeError('Only current or tomorrow Dublin date may receive new predictions')
 cards=json.loads(args.cards.read_text())
 grouped=declared(cards,target)
 if not grouped:raise RuntimeError('No declared fields available for target date')
 sources,read_stats,route_by_race=read_sources(args.sources,target)
 prior={}
 if args.output.exists():
  try:
   old=json.loads(args.output.read_text())
   for r in old.get('races',[]):
    if r.get('date')==target:prior[(str(r.get('date'))+' '+str(r.get('time')),normalize(r.get('course')))]=r
  except (OSError,ValueError):pass
 races=[];counts=collections.Counter()
 for (stamp,course_key),entries in sorted(grouped.items()):
  off=dt.datetime.strptime(stamp,'%Y-%m-%d %H:%M').replace(tzinfo=UTC)
  course=entries[0].get('course');clock=stamp[11:]
  old=prior.get((stamp.split()[0]+' '+clock,course_key))
  # Persist the original pre-off prediction once the race has begun.
  if old and off<=now:
   races.append(old);counts['preserved_post_off']+=1;continue
  active=[v for v in entries if not nonrunner(v)]
  subtype=type_for(entries)
  model=ROUTES.get(subtype)
  extended_route=route_by_race.get((stamp,course_key))
  # Junior NH hurdles are age-restricted juvenile-type events. Stage147's broad
  # OTHER_OPEN tag would otherwise mask the narrower, limited evidence; proxy is flagged.
  title=str(entries[0].get('race_name') or '').casefold()
  junior_proxy=('junior' in title and 'hurdle' in title)
  if junior_proxy:extended_route='HURDLE_JUVENILE'
  # Preserve existing mapped race types, but recover every other type via
  # Stage147's exact quarter-tested race classification, never from SP/odds.
  newly_routed=model is None and extended_route in STAGE156_ROUTES
  if newly_routed:
   subtype=extended_route
   model=STAGE156_ROUTES[extended_route]['model']
  evidence=STAGE156_ROUTES.get(extended_route,{})
  research_status=(str(evidence.get('evidence_grade') or 'provisional_small_sample')
                   if newly_routed else 'existing_previously_tested_route')
  race={'date':target,'course':course,'time':clock,'race_time':iso(off),
     'race_name':entries[0].get('race_name'),'race_type':subtype or 'unmapped',
     'field_size':len(active),'source_model':model,'model':'ROI Specialists',
     'research_status':research_status,'junior_hurdle_proxy':junior_proxy,
     'historical_races':evidence.get('historical_races'),
     'historical_roi_pct':evidence.get('historical_sp_roi_pct'),
     'historical_strike_pct':evidence.get('win_strike_pct'),
     'runners':[],'selection':None,'selection_status':'ASSESSMENT_PENDING',
     'published_at':iso(now),'nap_nb':None,'commentary':None}
  if off<=now:
   race['reason']='No combined specialist selection was published before the off; no post-race pick can be inserted'
   races.append(race);counts['not_published_preoff']+=1;continue
  if len(active)<2 or not model:
   race['reason']='No historically validated specialist mapping for this type' if not model else 'Insufficient active runners'
   races.append(race);counts['unmapped_or_small']+=1;continue
  rows=sources.get((stamp,course_key,model),[])
  ranked=[x for x in rows if normalize(x.get('horse'))]
  byhorse={normalize(x['horse']):x for x in ranked}
  eligible={normalize(x['horse']) for x in active}
  if len(byhorse)!=len(eligible) or set(byhorse)!=eligible:
   race['reason']='Specialist has not produced a complete assessment of this declared field (or a non-runner changed)'
   races.append(race);counts['unscored_or_changed']+=1;continue
  # A model may produce scores that are NOT calibrated probabilities; rank exactly by its frozen decision.
  ranked=sorted(ranked,key=lambda v:int(v.get('rank') or 9999))
  if [int(v.get('rank') or 0) for v in ranked]!=list(range(1,len(ranked)+1)):
   race['reason']='Specialist ranking is incomplete or has duplicate positions'
   races.append(race);counts['invalid_rank']+=1;continue
  fresh_times=[parse_time(x.get('prediction_asof') or x.get('decision_utc') or x.get('scored_at') or x.get('cards_asof')) for x in ranked]
  fresh_times=[t for t in fresh_times if t]
  if not fresh_times or max(fresh_times)>=off:
   race['reason']='Prediction timestamp is absent or not verifiably pre-off'
   races.append(race);counts['not_preoff']+=1;continue
  first,second=ranked[:2];s1=model_score(first);s2=model_score(second)
  gap=(s1-s2)/max(abs(s1),abs(s2),0.01) if s1 is not None and s2 is not None else 0
  # Only relative within-race evidence is compared: never claim cross-model calibrated odds.
  race['decision']={'gap':round(gap,5),'metric':'within-specialist relative score separation',
                    'calibrated_win_probability':False}
  race['evidence_asof']=iso(max(fresh_times))
  leader=first['horse'];second_name=second['horse']
  cards_byhorse={normalize(x['horse']):x for x in active}
  for pred in ranked:
   k=normalize(pred['horse']);c=cards_byhorse[k];rank=int(pred['rank'])
   race['runners'].append({'horse':c['horse'],'rank':rank,'research_score':model_score(pred),
      'official_rating':num(c.get('ofr')),'age':c.get('age'),
      'weight':c.get('lbs'), 'draw':c.get('draw'),
      'source_model':model,'trainer':c.get('trainer'),'jockey':c.get('jockey'),
      'commentary':prose(c['horse'],c,pred,rank,leader,second_name,subtype,model)})
  race['selection']={'horse':race['runners'][0]['horse'],'kind':'RECORDED_PRE_OFF',
                     'published_at':race['evidence_asof'],'pre_off_valid':True}
  race['selection_status']='RESEARCH_ONLY'
  race['confidence_evidence']={'relative_gap':round(gap,4),'field_size':len(active)}
  lead_sent=race['runners'][0]['commentary']
  second_sent=race['runners'][1]['commentary']
  race['commentary']=(f'{leader} tops this {subtype} assessment, with {second_name} rated next best. '
    +lead_sent+' As for the danger, '+second_sent+' These are relative research ratings, not betting probabilities.')
  races.append(race);counts['ranked']+=1
 # Provisional sample rankings can be shown but must never receive confident NAP/NB labels.
 for r in races:
  if r.get('selection') and r.get('research_status')=='provisional_small_sample':
   r['commentary']=(f"Limited historical evidence: only {r.get('historical_races')} completed races in this specific category. "
     'This is a provisional research ranking, not a validated race-type advantage. '+r['commentary'])
 # Per specialist, NAP/NB only where meaningful gaps exist; an empty day need not force either.
 bymodel=collections.defaultdict(list)
 for r in races:
  if r['selection'] and r['race_time'] and parse_time(r['race_time'])>now and r.get('research_status')!='provisional_small_sample':
   gap=r.get('confidence_evidence',{}).get('relative_gap',0)
   if r['field_size']>=5 and gap>=.13:bymodel[r['source_model']].append((gap,r))
 for name,ranked in bymodel.items():
  ranked.sort(key=lambda t:-t[0])
  if ranked:ranked[0][1]['nap_nb']='NAP'
  if len(ranked)>1 and ranked[1][0]>=.10:ranked[1][1]['nap_nb']='NB'
 for r in races:
  if r.get('nap_nb'):
   r['commentary']=f"{r['nap_nb']} for {r['source_model'].replace('_',' ')}. "+r['commentary']
 existing=[]
 if args.output.exists():
  try:existing=[r for r in json.loads(args.output.read_text()).get('races',[]) if r.get('date')!=target]
  except (ValueError,OSError):pass
 payload={'version':1,'model':'ROI Specialists','day':target,'generated_at':iso(now),
          'schedule':'20:00 previous day; 08:00, 10:00, 12:00, 14:00 and 16:00 Dublin',
          'races':sorted(existing+races,key=lambda r:(r.get('date',''),r.get('race_time',''))),
          'coverage':dict(counts),'source_rows':dict(read_stats),'source_models':sorted(set(ROUTES.values())),
          'odds_used_to_select':False,
          'warning':'Research only; historical ROI is not a promise. No specialist = no fabricated selection.'}
 args.output.parent.mkdir(parents=True,exist_ok=True)
 args.output.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
 print('STAGE153_ASSEMBLED',json.dumps({'day':target,'races':len(races),'counts':dict(counts)}))
 if not counts['ranked'] and not counts['preserved_post_off']:
  raise RuntimeError('No verified complete-field specialist selections; withheld all races')

def settle(args):
 if not args.output.exists():raise RuntimeError('No combined feed yet')
 feed=json.loads(args.output.read_text())
 results=json.loads(args.results.read_text());final=results.get('races',results.get('results',[]))
 mapping={}
 for result in final:
  if result.get('status')!='final':continue
  t=parse_time(result.get('off_dt'))
  if t is None:continue
  mapping[(t.strftime('%Y-%m-%d %H:%M'),normalize(result.get('course')))]=result
 settled=[]
 for r in feed.get('races',[]):
  if not r.get('selection'):continue
  t=parse_time(r.get('race_time'))
  if not t:continue
  result=mapping.get((t.strftime('%Y-%m-%d %H:%M'),normalize(r.get('course'))))
  if not result:continue
  horse=normalize(r['selection']['horse']);runner=next((x for x in result.get('runners',[]) if normalize(x.get('horse'))==horse),None)
  if not runner:continue
  status='Non-runner' if str(runner.get('status')).lower() in ('non-runner','nonrunner','nr') else 'Won' if runner.get('position')==1 else 'Unplaced'
  settled.append({'date':r['date'],'course':r['course'],'time':r['time'],'race_time':r['race_time'],
                  'horse':r['selection']['horse'],'race_type':r.get('race_type'),
                  'status':status,'position':runner.get('position'),'sp':runner.get('sp'),
                  'result_source':'Verified final race result',
                  'source_model':r.get('source_model'),'nap_nb':r.get('nap_nb')})
 dest=args.settled;dest.parent.mkdir(parents=True,exist_ok=True)
 # Keep the permanent results ledger when the rolling live results feed expires.
 older=[]
 if dest.exists():
  try:older=json.loads(dest.read_text()).get('selections',[])
  except (OSError,ValueError):pass
 keyed=lambda r:(r.get('date'),r.get('time'),normalize(r.get('course')),normalize(r.get('horse')))
 archive={keyed(r):r for r in older}
 archive.update({keyed(r):r for r in settled})
 settled=sorted(archive.values(),key=lambda r:(r.get('date',''),r.get('time','')),reverse=True)
 dest.write_text(json.dumps({'model':'ROI Specialists','updated_at':iso(utcnow()),'selections':settled},indent=2)+'\n')
 print('STAGE153_SETTLED',len(settled))

def selftest():
 x=lambda name,typ='',surface='',course='Ffos Las':{'race_name':name,'type':typ,'surface':surface,'course':course}
 cases=[('Maiden Hurdle','maiden hurdle'),('Novices Hurdle','novice hurdle'),('Novices Handicap Hurdle','novice handicap hurdle'),
  ('Beginner Chase','beginners chase'),('Novices Handicap Chase','novice handicap chase'),('Handicap Chase','handicap chase'),
  ('Handicap','aw handicap'),('Nursery','aw nursery'),('Restricted Auction','auction restricted'),('Maiden','aw maiden')]
 for title,expected in cases:
  assert type_for([x(title,course='Dundalk' if expected.startswith('aw') or expected=='auction restricted' else 'Hexham')])==expected,(title,expected)
 assert type_for([x('Flat Handicap',course='Ascot')]) is None
 assert len(STAGE156_ROUTES)==50
 assert STAGE156_ROUTES['AW_NOVICE']['model']=='STAGE143_TYPE_INTERACTIONS'
 assert STAGE156_ROUTES['AW_LISTED_PATTERN']['evidence_grade']=='provisional_small_sample'
 assert STAGE156_ROUTES['HURDLE_JUVENILE']['model']=='D_FORM_WEIGHT_GRADE__ROI'
 print('STAGE153_SELF_TEST_OK', 'stage156_routes',len(STAGE156_ROUTES))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=('self-test','build','settle'))
 p.add_argument('--date',default='');p.add_argument('--cards',type=pathlib.Path,default=pathlib.Path('data/racecards.json'))
 p.add_argument('--sources',type=pathlib.Path,nargs='*',default=[])
 p.add_argument('--output',type=pathlib.Path,default=pathlib.Path('data/strategy-roi-combined.json'))
 p.add_argument('--results',type=pathlib.Path,default=pathlib.Path('data/race-results.json'))
 p.add_argument('--settled',type=pathlib.Path,default=pathlib.Path('data/strategy-roi-combined-results.json'))
 a=p.parse_args()
 if a.mode=='self-test':selftest()
 elif a.mode=='build':build(a)
 else:settle(a)
