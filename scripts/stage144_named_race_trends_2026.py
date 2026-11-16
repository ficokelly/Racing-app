#!/usr/bin/env python3
"""Stage144: chronological recurring-race profile research, never a live betting model.

Three archived, immutable form baselines (Frozen D, Stage143 specialist,
and Stage141 HPR + Official Rating + Weight) are re-ranked using (a) previous editions of
an exact candidate race series, or (b) the latest 15 previous-day races at the
same course, code, race-family, trip, and going. Historical *winning and losing*
runners supply denominators. 2026 SP is settlement only, NEVER a selection
input; historical SP is inspected only to discover market hypotheses.

Confidence labels for Stage100's candidate event IDs are retained, not upgraded
to independent source verification. UK/IE false-country venue collisions are
flagged and excluded where source evidence is strong. No frozen files changed.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,io,json,math,pathlib,re,sqlite3,sys,unicodedata,zipfile
from dataclasses import dataclass,field

VERSION='stage144-recurrent-profile-three-baselines-v2'
MODELS=('FROZEN_D_EXACT','D_FORM_OR_GRADE__SR','D137_HPR_ABILITY_OR_WEIGHT')
STAGE143_MODELS=MODELS[:2]
STAGE141_MODEL=MODELS[2]
STAGE141_SCORE_COLUMN=STAGE141_MODEL+'_score'
VARIANTS=('BASE','EXACT_PROFILE','COMPARABLE15_PROFILE','HIERARCHICAL_PROFILE')
ALPHAS=(0.25,0.50)
CONFIDENT=('CURATED_CANDIDATE','REFERENCE_SERIES_CANDIDATE','STRONG_AUTOMATED')
EXACT_MIN=8
EXACT_KEEP=30
FALLBACK_KEEP=15
FALLBACK_MIN=7
AMBIGUOUS_COURSES={'ASCOT','PERTH','NEWCASTLE','WARWICK','HAMILTON','YORK','CHESTER','KEMPTON','CARLISLE','SOUTHWELL'}
PILLARS=('Ability','Trainer','Jockey','Course','Draw','Distance','Ground','Jumping')
POS_COMMENT=re.compile(r'\b(stayed on|ran on|finished strongly|kept on|jumped well|travelled strongly|quickened|led close home)\b',re.I)
NEG_COMMENT=re.compile(r'\b(fell|unseated|pulled up|bad mistake|jumped poorly|weakened quickly|tailed off)\b',re.I)
FAMILY_RE=re.compile(r'\b(group\s*[123]|grade\s*[123]|\bgr[123]\b|listed)\b',re.I)
FEATURES_BY_KIND={
 'CHASE':('age_7_9','age_8_10','or_top3','or_ge160','lto_win','lto_top3','won_last5','days_31_60','days_61_120','course_seen','course_win','trip_win','going_win','win_grade1','runs3','last_positive','last_negative','last_placed_close'),
 'HURDLE':('age_5_7','age_7_9','or_top3','or_ge140','lto_win','lto_top3','won_last5','days_31_60','days_61_120','course_seen','course_win','trip_win','going_win','win_grade1','runs3','last_positive','last_negative','last_placed_close'),
 'NH_FLAT':('age_4_6','age_5_7','lto_win','lto_top3','won_last5','days_31_60','course_seen','course_win','trip_win','runs3','first_recorded','last_positive'),
 'FLAT_AW':('age_2_3','age_3_5','or_top3','lto_win','lto_top3','won_last5','days_0_30','days_31_60','course_seen','course_win','trip_win','going_win','runs3','draw_low','draw_high','first_recorded','last_positive','last_placed_close'),
 'FLAT_TURF':('age_2_3','age_3_5','or_top3','lto_win','lto_top3','won_last5','days_0_30','days_31_60','course_seen','course_win','trip_win','going_win','runs3','draw_low','draw_high','first_recorded','last_positive','last_placed_close'),
 'FLAT_UNKNOWN':('age_2_3','age_3_5','or_top3','lto_win','lto_top3','won_last5','days_0_30','days_31_60','course_seen','course_win','trip_win','going_win','runs3','first_recorded','last_positive')}
ODDS_FEATURES=('sp_le3','sp_le6','sp_le10','sp_le15')

def num(x):
 try:
  v=float(x)
  return v if math.isfinite(v) else None
 except (ValueError,TypeError):return None

def normal(s):
 x=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().upper()
 return re.sub('[^A-Z0-9]','',x)

def course(s):
 return normal(re.sub(r'\((?:AW|GB|IRE|UK|IRELAND)\)', '',str(s or ''),flags=re.I))

def horsekey(r):
 name=normal(re.sub(r'\s*\((?:IRE|GB|FR|AUS|NZ|USA|GER|ITY)\)\s*$', '',str(r[9] or r[8] or ''),flags=re.I))
 sire,dam=normal(r[18]),normal(r[19]);ident=str(r[11] or '').strip()
 if not name:return ''
 if sire and dam:return 'P|'+name+'|'+sire+'|'+dam
 return 'I|'+normal(ident)+'|'+name if ident else 'N|'+name+'|'+normal(r[10])

def discipline(r,title=''):
 typ=(str(r[3] or '')+' '+str(title or '')).lower();surf=str(r[4] or '').lower()
 if 'nh flat' in typ or 'bumper' in typ or 'national hunt flat' in typ:return 'NH_FLAT'
 if 'chase' in typ or 'steeple' in typ:return 'CHASE'
 if 'hurdle' in typ:return 'HURDLE'
 if re.search(r'\b(aw|a/w|all.weather|polytrack|tapeta|fibresand|standard)\b',surf):return 'FLAT_AW'
 if re.search(r'\b(turf|grass)\b',surf):return 'FLAT_TURF'
 return 'FLAT_UNKNOWN'

def group_name(title='',race_type='',race_class='',pattern=''):
 x=' '.join(str(z or '') for z in (title,race_type,race_class,pattern)).lower()
 if 'handicap' in x or re.search(r'\bhcap\b',x):return 'HANDICAP'
 if 'maiden' in x:return 'MAIDEN'
 if 'novice' in x or 'beginners' in x:return 'NOVICE'
 if FAMILY_RE.search(x):return 'PATTERN'
 return 'OTHER'

def going(raw):
 s=' '.join(str(raw or '').lower().replace('-',' ').split())
 if not s:return ''
 if 'standard' in s or 'tapeta' in s or 'polytrack' in s:return 'STANDARD'
 if 'heavy' in s:return 'HEAVY'
 if 'good to soft' in s or 'good to yielding' in s:return 'GOOD_SOFT'
 if 'yielding to soft' in s:return 'YIELDING_SOFT'
 if 'soft' in s:return 'SOFT'
 if 'good to firm' in s:return 'GOOD_FIRM'
 if 'yielding' in s:return 'YIELDING'
 if 'firm' in s:return 'FIRM'
 if 'good' in s:return 'GOOD'
 return ''

def fallback_key(r,title='',pattern=''):
 d=num(r[5]);g=going(r[6]);p=course(r[2]);kind=discipline(r,title)
 if d is None or d<4 or d>40 or not g or not p:return None
 # Half-furlong exact bin; never silently use a materially different trip.
 return (p,kind,group_name(title,r[3],r[7],pattern),round(d*2)/2,g)

def foreign_evidence(starters,track_name):
 # Source venues are ambiguous (e.g. Ascot WA vs Ascot UK); do not use a
 # whole race of AUS/NZ-bred horses at an ambiguous venue as UK/IE evidence.
 if course(track_name) not in AMBIGUOUS_COURSES:return False
 known=[]
 for z in starters:
  raw=str(z[10] or '').strip().upper()
  if not raw:
   m=re.search(r'\((AUS|NZ)\)\s*$',str(z[9] or ''),re.I)
   raw=m.group(1).upper() if m else ''
  if raw:known.append(raw)
 if len(known)<5:return False
 foreign=sum(x in ('AUS','NZ','NEW ZEALAND','AUSTRALIA') for x in known)
 return foreign/len(known)>=0.70

@dataclass(slots=True)
class History:
 runs:int=0
 wins:int=0
 last_date:str=''
 last_pos:float|None=None
 last_margin:float|None=None
 last_comment:str=''
 last_five:collections.deque=field(default_factory=lambda:collections.deque(maxlen=5))
 courses:set=field(default_factory=set)
 course_wins:set=field(default_factory=set)
 trip_wins:set=field(default_factory=set)
 going_wins:set=field(default_factory=set)
 g1_wins:int=0

def profile(r,st,day,cur,trip,g,fieldor,drawfraction):
 age=num(r[17]);rating=num(r[16]);age=int(age) if age is not None and age>=0 else None
 lastdays=(dt.date.fromisoformat(day)-dt.date.fromisoformat(st.last_date)).days if st.last_date else None
 wins5=sum(st.last_five)
 # Source 'beaten' may be previous-horse margin rather than distance to the winner.
 # Only combine it with finishing in top 3; never label it as total lengths lost.
 prior_close=(st.last_pos is not None and st.last_pos in (2,3) and st.last_margin is not None and st.last_margin<=5)
 return {
  'age_2_3':None if age is None else age in (2,3),'age_3_5':None if age is None else 3<=age<=5,
  'age_4_6':None if age is None else 4<=age<=6,'age_5_7':None if age is None else 5<=age<=7,
  'age_7_9':None if age is None else 7<=age<=9,'age_8_10':None if age is None else 8<=age<=10,
  'or_ge140':None if rating is None else rating>=140,'or_ge160':None if rating is None else rating>=160,
  'or_top3':None if rating is None or len(fieldor)<3 else rating>=sorted(fieldor,reverse=True)[min(2,len(fieldor)-1)],
  'lto_win':None if not st.runs else st.last_pos==1,
  'lto_top3':None if not st.runs else st.last_pos is not None and 1<=st.last_pos<=3,
  'won_last5':None if not st.runs else wins5>0,
  'days_0_30':None if lastdays is None else 0<=lastdays<=30,
  'days_31_60':None if lastdays is None else 31<=lastdays<=60,
  'days_61_120':None if lastdays is None else 61<=lastdays<=120,
  'course_seen':bool(st.runs and cur in st.courses),
  'course_win':bool(st.runs and cur in st.course_wins),
  'trip_win':bool(st.runs and trip in st.trip_wins),
  'going_win':bool(st.runs and g and g in st.going_wins),
  'win_grade1':bool(st.runs and st.g1_wins>0),
  'runs3':st.runs>=3,
  'first_recorded':st.runs==0,
  'last_positive':None if not st.runs or not st.last_comment else bool(POS_COMMENT.search(st.last_comment)),
  'last_negative':None if not st.runs or not st.last_comment else bool(NEG_COMMENT.search(st.last_comment)),
  'last_placed_close':None if not st.runs or st.last_pos is None or st.last_margin is None else bool(prior_close),
  'draw_low':None if drawfraction is None else drawfraction<=1/3,
  'draw_high':None if drawfraction is None else drawfraction>=2/3,
 }

def source_map(path):
 result={};summary=collections.Counter();series=collections.defaultdict(list)
 with open(path,encoding='utf-8-sig',newline='') as f:
  for r in csv.DictReader(f):
   rid=r.get('stage2_race_id')
   if not rid:continue
   if rid in result:raise RuntimeError('Duplicate exact source race ID in Stage100')
   z={k:r.get(k,'') for k in ('stage2_race_id','event_id','match_status','edition_year','date','course','race_type','distance','race_name','region_scope','confidence')}
   result[rid]=z;summary[r.get('match_status','')]+=1
   if z['match_status'] in CONFIDENT:series[z['event_id']].append(z)
 return result,series,summary

def usable_event(event_id,series):
 entries=series.get(event_id,[])
 if not entries:return False
 years=[e['edition_year'] for e in entries]
 if len(set(years))!=len(years):return False
 # A confidently named recurring race may move by ~1f, but same venue and
 # dominant code are required. Names that change sponsor are preserved.
 if len(set(course(e['course']) for e in entries))!=1:return False
 kinds=collections.Counter(('CHASE' if 'chase' in (e['race_type'] or '').lower() else
   'HURDLE' if 'hurdle' in (e['race_type'] or '').lower() else
   'FLAT' if 'flat' in (e['race_type'] or '').lower() else 'OTHER') for e in entries)
 if len(kinds)>1:return False
 return True

def read_archive(archive_dir, stage141_dir):
 """Reconcile three immutable 2026 runner fields before any trend is scored.

 Stage141 scores are the published, pre-race predictions from an archived
 historical walk-forward. We do NOT re-train using 2026 outcomes and we do not
 use 2026 SP to construct the base scores.
 """
 archive_dir=pathlib.Path(archive_dir);stage141_dir=pathlib.Path(stage141_dir)
 source=archive_dir/'stage143-all-runners-2026.csv.gz'
 if not source.is_file():raise FileNotFoundError(source)
 races=collections.defaultdict(lambda:collections.defaultdict(dict))
 for row in csv.DictReader(gzip.open(source,'rt',encoding='utf8')):
  if row['model'] not in STAGE143_MODELS:continue
  rid=row['race_id'];horse=normal(row['horse'])
  if not horse:raise RuntimeError('Stage143 empty horse identity in '+rid)
  if horse in races[rid][row['model']]:raise RuntimeError('Duplicate horse in same source race: '+rid+' '+horse)
  score=num(row['score'])
  if score is None:raise RuntimeError('Stage143 nonfinite score '+rid+' '+horse)
  races[rid][row['model']][horse]={
   'name':row['horse'],'score':score,'rank':int(row['rank']),'won':int(row['winner']),
   'discipline':row['discipline'],'race_type':row['race_type']}
 result={};ref_file=archive_dir/'stage143-2026-rank1-rank2.csv.gz'
 for row in csv.DictReader(gzip.open(ref_file,'rt',encoding='utf8')):
  if row['model'] in STAGE143_MODELS:
   key=(row['race_id'],row['model'])
   if key in result:raise AssertionError('Duplicate Stage143 archived rank '+str(key))
   result[key]=row
 sums=collections.Counter();n=collections.Counter()
 for rid,groups in races.items():
  if set(groups)!=set(STAGE143_MODELS):raise AssertionError('Missing Stage143 baseline within '+rid)
  if set(groups[STAGE143_MODELS[0]])!=set(groups[STAGE143_MODELS[1]]):
   raise AssertionError('Stage143 baseline runner fields differ in '+rid)
  for model,group in groups.items():
   if not group:raise AssertionError('Empty Stage143 cohort '+rid)
   ranked=sorted(group.values(),key=lambda h:h['rank'])
   if ranked[0]['rank']!=1:raise AssertionError('Missing Stage143 rank 1')
   expected=result.get((rid,model))
   if not expected or normal(expected['rank1_horse'])!=normal(ranked[0]['name']):
    raise AssertionError('Archived Stage143 rank 1 / fields disagree: '+rid+model)
   sums[model]+=ranked[0]['won'];n[model]+=1
 if n.get('FROZEN_D_EXACT')!=9794 or sums['FROZEN_D_EXACT']!=2010:
  raise AssertionError('Frozen D base cohort/settlement not reproduced: '+str((n,sums)))
 if n.get(STAGE143_MODELS[1])!=9794 or sums[STAGE143_MODELS[1]]!=2374:
  raise AssertionError('Stage143 specialist control not reproduced: '+str((n,sums)))

 # The archived Stage141 per-runner file contains contemporaneous *pre-race*
 # predicted scores, not SP quotes; its winner flag is a settlement label only.
 hfile=stage141_dir/'stage141-2026-runner-scores.csv.gz'
 rfile=stage141_dir/'stage141-2026-rank1-rank2.csv.gz'
 if not hfile.is_file() or not rfile.is_file():
  raise FileNotFoundError('Missing original Stage141 immutable runner or rank archive')
 stage141_records=collections.defaultdict(list)
 with gzip.open(hfile,'rt',encoding='utf8',newline='') as f:
  reader=csv.DictReader(f)
  required={'date','race_id','horse','winning',STAGE141_SCORE_COLUMN}
  if not required.issubset(reader.fieldnames or []):
   raise RuntimeError('Stage141 runner score schema changed: '+str(reader.fieldnames))
  for row in reader:
   score=num(row[STAGE141_SCORE_COLUMN])
   if score is None:raise AssertionError('Missing pre-race Stage141 score '+row['race_id'])
   stage141_records[row['race_id']].append((normal(row['horse']),row['horse'],score,int(row['winning'])))
 archived141={}
 with gzip.open(rfile,'rt',encoding='utf8',newline='') as f:
  for row in csv.DictReader(f):
   if row['model']!=STAGE141_MODEL:continue
   if row['race_id'] in archived141:raise AssertionError('Duplicate Stage141 rank race '+row['race_id'])
   archived141[row['race_id']]=row
 if set(races)!=set(stage141_records) or set(races)!=set(archived141):
  raise AssertionError('Stage141 and Stage143 race cohorts are not identical: '+str((len(races),len(stage141_records),len(archived141))))
 n141=0;w141=0
 for rid,source_rows in stage141_records.items():
  former=races[rid][STAGE143_MODELS[0]]
  ids=[x[0] for x in source_rows]
  if len(set(ids))!=len(ids) or set(ids)!=set(former):
   raise AssertionError('Stage141/143 runner identities do not reconcile in '+rid)
  # Preserve input order to replicate Stage141's stable tie ordering.
  ranked=sorted(enumerate(source_rows),key=lambda x:(-x[1][2],x[0]))
  expected=archived141[rid]
  if len(ranked)<2 or normal(expected['rank1_horse'])!=ranked[0][1][0] or normal(expected['rank2_horse'])!=ranked[1][1][0]:
   raise AssertionError('Stage141 archived rank 1/2 did not reproduce: '+rid)
  group=races[rid][STAGE141_MODEL]
  for pos,(_, (key,name,score,won)) in enumerate(ranked,start=1):
   if str(former[key]['won'])!=str(won):
    raise AssertionError('Stage141/143 winner labels do not reconcile in '+rid)
   group[key]={'name':name,'score':score,'rank':pos,'won':won,
      'discipline':former[key]['discipline'],'race_type':former[key]['race_type']}
  n141+=1;w141+=group[ranked[0][1][0]]['won']
 if n141!=9794 or w141!=2597:
  raise AssertionError('Stage141 OR+Weight control was not reproduced: '+str((n141,w141)))
 sums[STAGE141_MODEL]=w141;n[STAGE141_MODEL]=n141
 return races,{'baseline_wins':dict(sums),'baseline_races':dict(n),
  'original_stage141_reproduced':True,'all_three_baselines_identical_2026_runner_cohort':True,
  'stage141_archived_scores_are_pre_race_only':True}

def target_audit(refmap,series,archive_races):
 audit=collections.Counter();rows=[]
 for rid in archive_races:
  entry=refmap.get(rid)
  if not entry:audit['reference_missing']+=1;continue
  audit[entry['match_status']]+=1
  event=entry['event_id'];past=[r for r in series.get(event,[]) if r['date']<entry['date']]
  ok=usable_event(event,series)
  if ok and len(past)>=8:audit['exact_8plus_candidates']+=1
  if ok and len(past)>=15:audit['exact_15plus_candidates']+=1
  if ok and len(past)>=20:audit['exact_20plus_candidates']+=1
  rows.append({'race_id':rid,'date':entry['date'],'course':entry['course'],'name':entry['race_name'],
   'event_id':event,'status':entry['match_status'],'series_structurally_consistent':int(ok),
   'prior_editions':len(past),'exact_eligible':int(ok and len(past)>=EXACT_MIN)})
 return audit,rows

def norm_scores(items):
 # Percentage rank removes differences in scales between the frozen arms.
 order=sorted(range(len(items)),key=lambda i:(-items[i]['base']['score'],items[i]['base']['rank']))
 n=len(order);v=[0.0]*n
 for pos,i in enumerate(order):v[i]=(n-pos-1)/max(1,n-1)
 return v

def trend_from_history(editions,kind,include_odds=False):
 if not editions:return [],[]
 allowed=ODDS_FEATURES if include_odds else FEATURES_BY_KIND.get(kind,FEATURES_BY_KIND['FLAT_UNKNOWN'])
 n_editions=len(editions);allr=[r for edition in editions for r in edition['runners']]
 if not allr:return [],[]
 midpoint=max(1,n_editions//2)
 selected=[]
 for key in allowed:
  a=[r for r in allr if r['features'].get(key) is not None]
  w=[r for r in a if r['won']]
  if len(a)<max(16,n_editions*4//3) or len(w)<max(5,n_editions//2):continue
  win_yes=sum(bool(r['features'][key]) for r in w)
  all_yes=sum(bool(r['features'][key]) for r in a)
  # Winner share by itself is NOT a predictive trend. Compare to the share
  # of all participants meeting the same criterion, with smoothing.
  win_share=(win_yes+1)/(len(w)+2)
  runner_share=(all_yes+1)/(len(a)+2)
  lift=math.log(max(.001,win_share)/max(.001,runner_share))
  # Require a reproducible direction in both chronological halves. This is
  # a conservative stability penalty rather than a claim of significance.
  signs=[]
  for half in (editions[:midpoint],editions[midpoint:]):
   h=[r for e in half for r in e['runners'] if r['features'].get(key) is not None]
   hw=[r for r in h if r['won']]
   if len(hw)<3 or len(h)<10:continue
   val=math.log(((sum(bool(r['features'][key]) for r in hw)+1)/(len(hw)+2))/
                ((sum(bool(r['features'][key]) for r in h)+1)/(len(h)+2)))
   signs.append(val)
  stable=bool(len(signs)==2 and all(q*lift>0.0 for q in signs))
  shrink=(n_editions/(n_editions+20))**0.5
  effect=max(-0.65,min(0.65,lift))*shrink*(1 if stable else .25)
  if abs(effect)<.055:continue
  selected.append({'feature':key,'winners_matching':win_yes,'winners_with_data':len(w),
   'runners_matching':all_yes,'runners_with_data':len(a),
   'winner_share':round(win_share,4),'runner_share':round(runner_share,4),
   'log_lift':round(lift,5),'effect':round(effect,5),'halves_confirm_direction':stable,
   'history_editions':n_editions})
 selected.sort(key=lambda z:-abs(z['effect']))
 return selected[:4],selected

def trend_score(profile_obj, trends):
 return sum(t['effect']*int(profile_obj['features'].get(t['feature']) is True)
            for t in trends if profile_obj['features'].get(t['feature']) is not None)

def conditional_odds(r):
 v=num(r[20]);return {
  'sp_le3':None if v is None else v<=3.0,
  'sp_le6':None if v is None else v<=6.0,
  'sp_le10':None if v is None else v<=10.0,
  'sp_le15':None if v is None else v<=15.0}

def is_starter(r):
 # Mirrors the original Stage30/137 start criterion: pulled-up, fell and
 # unseated runners have no numeric position but DID start the race.
 outcome=str(r[13] or '').strip().lower().replace('-','').replace('_','').replace(' ','')
 if any(token in outcome for token in ('nonrunner','scratched','withdrawn','didnotstart')) or outcome in ('nr','n/r'):
  return False
 return r[12] is not None or (num(r[20]) or 0)>1 or bool(outcome and outcome!='nr')

def produce_edition(race,horse_states,refmap):
 first=race[0];rid=first[0];day=first[1]
 meta=refmap.get(rid,{});title=(meta.get('race_name') or first[25] or '')
 kind=discipline(first,title)
 track=course(first[2]);trip=round((num(first[5]) or 0)*2)/2;g=going(first[6])
 starters=[r for r in race if is_starter(r)]
 if len(starters)<3 or sum(int(num(r[12])==1) for r in starters)!=1:return None
 if foreign_evidence(starters,first[2]):return {'foreign':True,'race_id':rid,'date':day}
 fbor=[num(r[16]) for r in starters if num(r[16]) is not None]
 vals=[]
 for r in starters:
  st=horse_states.get(horsekey(r)) or History()
  draw=num(r[21]);df=(draw-1)/(len(starters)-1) if draw is not None and len(starters)>1 else None
  p=profile(r,st,day,track,trip,g,fbor,df)
  p.update(conditional_odds(r)) # past-run historical SP; never passed to live profile scoring
  vals.append({'horse':r[9],'key':normal(r[9]),'won':int(num(r[12])==1),
   'sp':num(r[20]),'features':p})
 return {'race_id':rid,'date':day,'kind':kind,'course':track,'go':g,'trip':trip,
  'event':meta.get('event_id'),'fallback':fallback_key(first,title,first[26]),
  'family':group_name(title,first[3],first[7],first[26]),'runners':vals}

def apply_updates(race,history,refmap):
 first=race[0];rid=first[0];day=first[1];title=(refmap.get(rid) or {}).get('race_name') or first[25] or ''
 cur=course(first[2]);trip=round((num(first[5]) or 0)*2)/2;g=going(first[6]);pattern=str(first[26] or '')
 grade1=bool(re.search(r'\b(?:grade|group)\s*1\b',title+' '+pattern,re.I))
 for r in race:
  if not is_starter(r):continue
  key=horsekey(r)
  if not key:continue
  st=history.get(key)
  if st is None:st=History();history[key]=st
  pos=num(r[12]);win=(pos==1)
  st.runs+=1;st.wins+=int(win);st.last_date=day;st.last_pos=pos
  st.last_margin=num(r[23]);st.last_comment=str(r[22] or '')[:400];st.last_five.append(int(win))
  st.courses.add(cur)
  if win:
   st.course_wins.add(cur);st.trip_wins.add(trip)
   if g:st.going_wins.add(g)
   if grade1:st.g1_wins+=1

def write_csv(path,rows,fields=None,compress=False):
 path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 fields=fields or list(dict.fromkeys(k for row in rows for k in row))
 opener=gzip.open if compress else open
 with opener(path,'wt',encoding='utf8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def measure(rows):
 results={}
 for mode in sorted(set(r['model'] for r in rows)):
  items=[r for r in rows if r['model']==mode]
  n=len(items);priced=[r for r in items if num(r.get('selected_sp')) is not None and num(r['selected_sp'])>=1.01]
  wins=sum(int(r['rank1_won']) for r in items)
  result={'races':n,'wins':wins,'strike_pct':round(100*wins/n,3) if n else None,
   'priced':len(priced),'sp_coverage_pct':round(100*len(priced)/n,2) if n else 0,
   'sp_roi_pct':round(100*sum((num(r['selected_sp']) if int(r['rank1_won']) else 0)-1 for r in priced)/len(priced),3) if priced else None,
   'rank1_or2_wins':sum(int(r['rank1_won'])+int(r['rank2_won']) for r in items),
   'exact_used':sum(r['trend_source']=='EXACT' for r in items),
   'fallback_used':sum(r['trend_source']=='COMPARABLE15' for r in items)}
  results[mode]=result
 return results

def score_target(edition,baseline,exact_history,fallback_history,refok,output,used,trend_rows,odds_rows):
 day=edition['date'];rid=edition['race_id'];kind=edition['kind']
 r_by_key={r['key']:r for r in edition['runners']}
 all_current=set(r_by_key)
 if not baseline or not all_current:return
 # Only matches with genuine prior history. Never include same-day editions.
 valid_exact=(refok and len(exact_history)>=EXACT_MIN)
 valid_fb=(edition['fallback'] is not None and len(fallback_history)>=FALLBACK_MIN)
 t_exact,_=trend_from_history(exact_history,kind) if valid_exact else ([],[])
 t_fb,_=trend_from_history(fallback_history,kind) if valid_fb else ([],[])
 source_trends={'EXACT':t_exact,'COMPARABLE15':t_fb}
 for src,trends in source_trends.items():
  editions=exact_history if src=='EXACT' else fallback_history
  for t in trends:
   trend_rows.append({'date':day,'race_id':rid,'event':edition['event'] or '',
     'source':src,'historical_races':len(editions),**t})
 if valid_exact:
  market,_=trend_from_history(exact_history,kind,include_odds=True)
  for t in market:
   if t['effect']>0 and t['halves_confirm_direction'] and len(exact_history)>=12:
    odds_rows.append({'date':day,'race_id':rid,'event':edition['event'],
     'source':'EXACT','market_pattern':t['feature'],'historical_editions':len(exact_history),
     'runners_matching':t['runners_matching'],'winners_matching':t['winners_matching'],
     'effect':t['effect'],'allowed_current_quote_gate':0,
     'reason':'No timestamped pre-off 2026 bookmaker quote provided; current race SP forbidden'})
 for base_model,group in baseline.items():
  if set(group)!=all_current:
   used['runner_join_mismatch']+=1;return
  items=[{'key':k,'current':r_by_key[k],'base':group[k]} for k in group]
  basepct=norm_scores(items)
  for version in VARIANTS:
   alphas=[0.0] if version=='BASE' else (ALPHAS+(1.0,) if version=='HIERARCHICAL_PROFILE' else ALPHAS)
   for alpha in alphas:
    source='NONE';trends=[]
    if version=='EXACT_PROFILE' and valid_exact:source='EXACT';trends=t_exact
    elif version=='COMPARABLE15_PROFILE' and valid_fb:source='COMPARABLE15';trends=t_fb
    elif version=='HIERARCHICAL_PROFILE':
     if valid_exact:source='EXACT';trends=t_exact
     elif valid_fb:source='COMPARABLE15';trends=t_fb
    if not trends:source='NONE'
    assert not any(t['feature'] in ODDS_FEATURES for t in trends), 'Current-race SP leaked into selection scoring'
    values=[]
    for idx,item in enumerate(items):
     t=trend_score(item['current'],trends)
     values.append((1-alpha)*basepct[idx]+alpha*(0.5+0.5*math.tanh(t*2)) if source!='NONE' else basepct[idx])
    ix=sorted(range(len(items)),key=lambda i:(-values[i],items[i]['base']['rank']))
    first=items[ix[0]];second=items[ix[1]];w1=first['current']['won'];w2=second['current']['won']
    model=base_model+'__'+version+('__'+str(int(alpha*100)) if alpha else '')
    output.append({'date':day,'race_id':rid,'course':edition['course'],
      'discipline':kind,'race_family':edition['family'],'event_id':edition['event'] or '',
      'model':model,'base_model':base_model,'variant':version,'alpha':alpha,
      'trend_source':source,'trend_conditions':'; '.join(t['feature'] for t in trends),
      'historical_sample':len(exact_history) if source=='EXACT' else len(fallback_history) if source=='COMPARABLE15' else 0,
      'rank1_horse':first['current']['horse'],'rank2_horse':second['current']['horse'],
      'rank1_won':w1,'rank2_won':w2,'selected_sp':first['current']['sp'],
      'grade_confidence':'STAGE100_CANDIDATE_NOT_INDEPENDENTLY_VERIFIED',
      'current_sp_used_as_input':0})
    used['variant_predictions']+=1

def evaluate(db,reference,archive_dir,stage141_dir,out,to_date='2026-11-10'):
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 refmap,series,counts=source_map(reference)
 target,base_audit=read_archive(archive_dir,stage141_dir)
 target_ids=set(target);candidate,arecs=target_audit(refmap,series,target)
 write_csv(out/'stage144-source-matches.csv',arecs)
 valid_ids={eid for eid in {refmap[rid]['event_id'] for rid in target_ids if rid in refmap}
            if usable_event(eid,series)}
 db=pathlib.Path(db)
 conn=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
 conn.execute('PRAGMA temp_store=MEMORY')
 co=set(r[1] for r in conn.execute('PRAGMA table_info(stage65p_race_details)'))
 if not {'race_name','pattern'}.issubset(co):raise RuntimeError('Stage65P explicit race-name and pattern fields unavailable')
 target_fallbacks=set()
 for r in conn.execute('''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,d.race_name,d.pattern
   FROM historical_races r LEFT JOIN stage65p_race_details d ON d.race_id=r.id
   WHERE r.race_date BETWEEN '2026-01-01' AND ?''',(to_date,)):
  if r[0] in target_ids:
   m=refmap.get(r[0],{})
   # fallback_key uses historical_races 8-field prefix and full historical title.
   key=fallback_key(r,m.get('race_name') or r[8] or '',r[9] or '')
   if key is not None:target_fallbacks.add(key)
 history={};exact=collections.defaultdict(lambda:collections.deque(maxlen=EXACT_KEEP));fb=collections.defaultdict(lambda:collections.deque(maxlen=FALLBACK_KEEP))
 output=[];trends=[];odds=[];audit=collections.Counter();pending=[];race=[];prev_key=None;prev_day=None
 sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
   u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
   u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment,u.beaten,u.weight_lbs,
   d.race_name,d.pattern
  FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
  LEFT JOIN stage65p_race_details d ON d.race_id=r.id
  WHERE r.race_date<=? ORDER BY r.race_date,r.id'''
 def finish(group):
  if not group:return
  d=produce_edition(group,history,refmap)
  if d is None:audit['invalid_results']+=1;return
  if d.get('foreign'):
   audit['foreign_collision_races']+=1
   if d['race_id'] in target_ids:audit['foreign_collision_targets']+=1
   # Never contaminate UK/Irish trends or form histories with known foreign racing.
   return
  rid=d['race_id'];day=d['date'];eid=d['event']
  if rid in target_ids:
   audit['target_races_scanned']+=1
   allowed=bool(eid in valid_ids and refmap.get(rid,{}).get('match_status') in CONFIDENT)
   eh=list(exact.get(eid,[])) if allowed else []
   fh=list(fb.get(d['fallback'],[])) if d['fallback'] else []
   if allowed and len(eh)>=EXACT_MIN:audit['eligible_exact']+=1
   if len(fh)>=FALLBACK_MIN:audit['eligible_fallback']+=1
   score_target(d,target[rid],eh,fh,allowed,output,audit,trends,odds)
  # Retain only potential target series, not every one of 505,493 event series.
  if eid in valid_ids and refmap.get(rid,{}).get('match_status') in CONFIDENT:exact[eid].append(d)
  if d['fallback'] in target_fallbacks:fb[d['fallback']].append(d)
  pending.append(group)
 def flush():
  nonlocal race,pending
  if race:finish(race);race=[]
  for x in pending:apply_updates(x,history,refmap)
  pending=[]
 for row in conn.execute(sql,(to_date,)):
  k=(row[1],row[0])
  if prev_day is not None and row[1]!=prev_day:flush()
  elif prev_key is not None and k!=prev_key and race:finish(race);race=[]
  race.append(row);prev_key=k;prev_day=row[1]
  audit['runner_rows']+=1
  if audit['runner_rows']%1000000==0:
   print('STAGE144_HISTORY',audit['runner_rows'],'target',audit['target_races_scanned'],'horses',len(history),flush=True)
 flush();conn.close()
 # All three base models and every profile variant must have identical evaluable race cohorts. An unmatched
 # field stays out of ALL variants; never use partial labels or make up losses.
 output.sort(key=lambda r:(r['date'],r['race_id'],r['model']))
 write_csv(out/'stage144-2026-rank1-rank2.csv.gz',output,compress=True)
 write_csv(out/'stage144-discovered-form-trends.csv',trends)
 write_csv(out/'stage144-historical-sp-hypotheses.csv',odds)
 overall=measure(output)
 bygroup=[]
 for grouping in ('discipline','race_family','trend_source'):
  vals=sorted(set(r[grouping] for r in output))
  for val in vals:
   for model,z in measure([r for r in output if r[grouping]==val]).items():
    bygroup.append({'breakdown':grouping,'group':val,'model':model,**z})
 write_csv(out/'stage144-2026-discipline-racetype.csv',bygroup)
 count_by_model=collections.Counter(r['model'] for r in output)
 if not count_by_model or len(set(count_by_model.values()))!=1:raise RuntimeError('Different race counts per variants: '+str(count_by_model))
 if audit['runner_join_mismatch']:raise RuntimeError('Stage141/Stage143/Stage2 runner identities did not match. See source audit, no trusted results.')
 # Only a small number of foreign collisions and incomplete race records may
 # be excluded, but the model needs genuine substantial 2026 coverage.
 if min(count_by_model.values())<8000:raise RuntimeError('Insufficient verified three-model/Stage2 2026 cohort coverage: '+str(count_by_model))
 s={'version':VERSION,'source_cutoff':to_date,'research_only':True,'original_models_modified':False,
  'odds_used_for_2026_predictions':False,'historical_sp_used_for_prior_race_hypotheses_and_settlement_only':True,
  'three_immutable_model_controls':base_audit,'reference_status_counts':dict(counts),'2026_recurrence_audit':dict(candidate),
  'run_audit':dict(audit),'target_fallback_keys':len(target_fallbacks),'models':list(sorted(count_by_model)),'base_models':list(MODELS),
  'matched_2026_races_per_model':min(count_by_model.values()),
  'overall':overall,'exact_min_prior_editions':EXACT_MIN,'fallback_min_prior_races':FALLBACK_MIN,
  'fallback_must_match':['course','discipline/surface','race_family','half_furlong_trip','going'],
  'known_limits':['Stage100 recurring matches are candidates, not independently verified editions',
   'SP of the 2026 race is never an available pre-race price',
   'No timestamped bookmaker prices: no 2026 odds trend gating performed',
   'Source beaten margin may be to previous horse; proximity flag only applies to a 2nd or 3rd',
   'Horse first recorded UK/Irish start does not guarantee career debut',
   'Foreign-course collisions are identified with a conservative breeding-country majority heuristic',
   'Trend hypothesis mining from a small number of editions is vulnerable to overfitting',
   'Results share a verified identical Stage141 and Stage143 2026 baseline cohort; next prospective period required']}
 (out/'stage144-summary.json').write_text(json.dumps(s,indent=2)+'\n')
 print('STAGE144_SUCCESS',json.dumps({'races':s['matched_2026_races_per_model'],'eligible_exact':audit['eligible_exact'],'eligible_fallback':audit['eligible_fallback'],'foreign':audit['foreign_collision_targets'],'overall':overall}),flush=True)
 return s

def audit_only(reference,archive_dir,stage141_dir,out):
 out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
 refmap,series,counts=source_map(reference);target,proof=read_archive(archive_dir,stage141_dir)
 audit,rows=target_audit(refmap,series,target)
 write_csv(out/'stage144-source-matches.csv',rows)
 queen=[r for r in rows if r['event_id']=='CHELTENHAM_CHAMPION_CHASE']
 named={k:sum(len(set(z['edition_year'] for z in v))>=t for v in series.values()) for k,t in (('10plus',10),('15plus',15),('20plus',20))}
 s={'reference_rows':len(refmap),'reference_status_counts':dict(counts),'series_count':len(series),
    'long_series':named,'target_2026':len(target),'2026_audit':dict(audit),
    'queen_mother_2026':queen,'queen_mother_source_editions':[{'date':r['date'],'name':r['race_name'],'status':r['match_status']} for r in series['CHELTENHAM_CHAMPION_CHASE']],
    'controls':proof,'critical_limit':'All source matches are candidate labels, not independently validated'}
 (out/'stage144-recurrence-feasibility.json').write_text(json.dumps(s,indent=2)+'\n')
 print('STAGE144_FEASIBILITY',json.dumps({k:s[k] for k in ('reference_rows','series_count','long_series','target_2026','2026_audit')}),flush=True)
 return s

def self_test():
 # Compare winner prevalence against population prevalence, not winner share alone.
 def mk_ed(yeswinner,yeslosers):
  return {'runners':[{'won':i==0,'features':{'course_win':yeswinner if i==0 else yeslosers}}
      for i in range(9)]}
 all_alike=[mk_ed(True,True) for _ in range(18)]
 trends,_=trend_from_history(all_alike,'CHASE')
 assert not any(x['feature']=='course_win' for x in trends),'100% winners and 100% runners is not a trend'
 meaningful=[mk_ed(True,False) for _ in range(18)]
 tr,_=trend_from_history(meaningful,'CHASE')
 assert any(z['feature']=='course_win' and z['effect']>0 and z['halves_confirm_direction'] for z in tr)
 # Same calendar-day outcome cannot update histories for another race that day.
 st=History();fake=[None]*27;fake[9]='Synthetic (IRE)';fake[8]='synthetic';fake[10]='IRE';fake[11]='123';fake[12]=1
 fake[1]='2025-01-01';fake[2]='Cheltenham';fake[3]='Chase';fake[4]='Turf';fake[5]=16;fake[6]='Good';fake[17]=7;fake[16]=160;fake[21]=2;fake[20]=6.0
 p=profile(fake,st,'2025-01-01','CHELTENHAM',16,'GOOD',[160,140,130],.25)
 assert p['first_recorded'] and p['lto_win'] is None and p['course_win'] is False
 assert fallback_key(fake,'Handicap Chase')==('CHELTENHAM','CHASE','HANDICAP',16.0,'GOOD')
 assert fallback_key(fake,'Grade 1 Chase')!=fallback_key(fake,'Handicap Chase')
 assert foreign_evidence([tuple(list(fake[:10])+['AUS']+fake[11:])]*7,'Ascot')
 fake[12]=None;fake[13]='PU';assert is_starter(fake)
 fake[13]='Non Runner';assert not is_starter(fake);fake[12]=1;fake[13]=''
 assert not foreign_evidence([tuple(list(fake[:10])+['IRE']+fake[11:])]*7,'Ascot')
 assert not usable_event('x',{})
 assert conditional_odds(fake)['sp_le6'] is True
 # Document that winning SP of the evaluated race cannot be referenced by profile.
 assert 'sp_le6' not in FEATURES_BY_KIND['CHASE']
 # A complete miniature historical trend selection: SP of the current race
 # changes but no rankings, trend strength or profile scores may change.
 prior=[mk_ed(True,False) for _ in range(12)]
 curr={'race_id':'synthetic','date':'2026-01-02','kind':'CHASE','course':'C','family':'PATTERN',
       'event':'E','fallback':('C','CHASE','PATTERN',16.,'GOOD'),
       'runners':[{'horse':'A','key':'A','won':1,'sp':5.0,'features':{'course_win':True}},
                  {'horse':'B','key':'B','won':0,'sp':7.0,'features':{'course_win':False}}]}
 bases={'FROZEN_D_EXACT':{'A':{'name':'A','score':.8,'rank':1,'won':1},
         'B':{'name':'B','score':.4,'rank':2,'won':0}}}
 def simulate():
  out=[];score_target(curr,bases,prior,prior,True,out,collections.Counter(),[],[])
  return [(r['model'],r['rank1_horse'],r['rank1_won']) for r in out]
 before=simulate();curr['runners'][0]['sp']=1.5;curr['runners'][1]['sp']=200
 after=simulate();assert before==after and len(before)==8
 print('STAGE144_SELFTEST_PASS',VERSION)

def main():
 p=argparse.ArgumentParser();cmd=p.add_subparsers(dest='command',required=True)
 cmd.add_parser('self-test')
 for mode in ('audit','backtest'):
  q=cmd.add_parser(mode);q.add_argument('--reference',required=True);q.add_argument('--stage143',required=True)
  q.add_argument('--stage141',required=True,help='Unmodified successful Stage141 run results')
  q.add_argument('--out',required=True)
  if mode=='backtest':q.add_argument('--db',required=True);q.add_argument('--to-date',default='2026-11-10')
 a=p.parse_args()
 if a.command=='self-test':self_test()
 elif a.command=='audit':audit_only(a.reference,a.stage143,a.stage141,a.out)
 else:evaluate(a.db,a.reference,a.stage143,a.stage141,a.out,a.to_date)
if __name__=='__main__':main()
