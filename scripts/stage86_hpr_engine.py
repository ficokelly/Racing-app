#!/usr/bin/env python3
"""Stage86 Hot Pots Ratings: research-only historical HPR and append-only daily ledger.

HPR v0 is an experimental, odds-blind performance rating, not an official speed
figure, Racing Post Rating, proven predictor or calibrated win probability.

Safety:
* Never reads odds/SP when computing HPR or a pre-race HPR ranking.
* A target race's outcome is inserted only after all of its pre-race features
  are calculated; all selection inputs come from PREVIOUS CALENDAR DAYS.
* Never joins historical horses on their name alone; pedigree or exact ID
  required. Final results without those keys can be linked only through an
  exact pre-off racecard's horse ID and unique full field.
* Historical ratings are revisioned, not overwritten. Same source+version
  reruns are idempotent; changed source evidence creates an audit revision.
* Only writes to --store and --output; frozen D and D(c) are never changed.

HPR v0 = 70% observed run quality + 30% D-weighted condition-adjusted run
quality. Conditional adjustments are based on the horse's own prior performances
under matching conditions, shrunk toward neutral for small samples. The original
D weights are reused as a STARTING PRIOR, not as a claimed optimal formula.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, hashlib, json, math, pathlib, re, sqlite3, statistics, subprocess, sys, tempfile, unicodedata
from zoneinfo import ZoneInfo

VERSION='HPR-v0-2026-11-10-research'
WEIGHTS={'Ability':13.93,'Trainer':8.96,'Jockey':5.60,'Course':17.77,'Draw':12.25,
         'Distance':11.17,'Ground':25.03,'Jumping':5.28}
STOP={'nr','non-runner','nonrunner','withdrawn','scratched','dns','did not start'}
UNFINISHED={'pu','pulled up','f','fell','ur','unseated','unseated rider','bd','brought down','rr','refused','ro','ran out'}
# Strict GB/IE scope; venue variants are canonicalised with course(). Explicit
# source regions GB/UK/IE/IRE also qualify, but foreign regions do not.
UKIE_COURSES=set('''AINTREE ASCOT AYR BALLINROBE BANGORONDEE BATH BELLEWSTOWN BEVERLEY BRIGHTON CARLISLE CARTMEL CATTERICK CATTERICKBRIDGE CHELMSFORD CHELMSFORDCITY CHELTENHAM CHEPSTOW CHESTER CLONMEL CORK CURRAGH THECURRAGH DONCASTER DOWNROYAL DOWNPATRICK DUNDALK EPSOM EPSOMDOWNS EXETER FAIRYHOUSE FAKENHAM FFOSLAS FONTWELL GALWAY GOODWOOD GOWRANPARK HAMILTON HAMILTONPARK HAYDOCK HAYDOCKPARK HEREFORD HEXHAM HUNTINGDON KELSO KEMPTON KEMPTONPARK KILBEGGAN KILLARNEY LEICESTER LEOPARDSTOWN LIMERICK LINGFIELD LINGFIELDPARK LISTOWEL LUDLOW MARKETRASEN MUSSELBURGH NAAS NAVAN NEWBURY NEWCASTLE NEWMARKET NEWMARKETJULY NEWTONABBOT NOTTINGHAM PERTH PLUMPTON PONTEFRACT PUNCHESTOWN REDCAR RIPON ROSCOMMON SALISBURY SANDOWN SANDOWNPARK SEDGEFIELD SLIGO SOUTHWELL STRATFORD STRATFORDONAVON TAUNTON THIRSK THURLES TIPPERARY TRAMORE UTTOXETER WARWICK WETHERBY WEXFORD WINCANTON WINDSOR WOLVERHAMPTON WORCESTER YARMOUTH GREATYARMOUTH YORK'''.split())
UTC=dt.timezone.utc

def norm(x):
 s=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().upper()
 s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$','',s)
 return re.sub('[^A-Z0-9]','',s)

def course(x):return norm(re.sub(r'\s*\((?:AW|IRE|GB)\)','',str(x or ''),flags=re.I))
def number(x):
 try:
  z=float(x)
  return z if math.isfinite(z) else None
 except (TypeError,ValueError):return None

def day(x):
 try:return dt.date.fromisoformat(str(x or '')[:10])
 except (ValueError,TypeError):return None

def off(x):
 s=str(x or '')
 if 'T' in s:
  try:
   parsed=dt.datetime.fromisoformat(s.replace('Z','+00:00'))
   return parsed.astimezone(UTC).strftime('%H:%M') if parsed.tzinfo else parsed.strftime('%H:%M')
  except ValueError:return ''
 return s[:5] if re.match(r'^\d{2}:\d{2}',s) else ''

def race_key(r):
 d=day(r.get('date') or r.get('off_dt') or r.get('race_time'))
 return f"{d or ''}|{course(r.get('course'))}|{off(r.get('off_dt') or r.get('race_time') or r.get('off'))}"

def read_json(path,default=None):
 try:return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
 except (OSError,ValueError):return default

def save_json(path,data):
 p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(data,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')

def flat_cards(obj):
 if isinstance(obj,list):
  for v in obj:yield from flat_cards(v)
 elif isinstance(obj,dict):
  if obj.get('horse') and obj.get('course') and (obj.get('date') or obj.get('race_time')):
   yield obj
  elif obj.get('course') and obj.get('runners') and (obj.get('date') or obj.get('race_time')):
   for r in obj['runners']:
    if isinstance(r,dict):yield {**{k:v for k,v in obj.items() if k!='runners'},**r}
  else:
   for v in obj.values():
    if isinstance(v,(dict,list)):yield from flat_cards(v)

def card_dates(root,start,end,git_preoff=False):
 """Per-day pre-off cards. Historical dates use a strictly earlier Git commit.

 A 09:45 UTC snapshot can be too early for some declarations; a mismatch
 marks that race ineligible, not reconstructed from its finishing result.
 """
 found={};root=pathlib.Path(root)
 if git_preoff:
  cur=start
  while cur<=end:
   # Use a morning snapshot; refuse to claim coverage for stale cards.
   cmd=['git','log','-1','--format=%H','--until',cur.isoformat()+' 09:45:00 +0000','--','data/racecards.json']
   try:sha=subprocess.run(cmd,cwd=root,capture_output=True,text=True,check=True).stdout.strip()
   except (OSError,subprocess.CalledProcessError):sha=''
   if sha:
    try:
     txt=subprocess.run(['git','show',sha+':data/racecards.json'],cwd=root,capture_output=True,text=True,check=True).stdout
     v=json.loads(txt)
     found[str(cur)]=[x for x in flat_cards(v) if day(x.get('date') or x.get('race_time'))==cur]
    except (subprocess.CalledProcessError,ValueError):pass
   cur+=dt.timedelta(days=1)
 else:
  for f in [root/'data/racecards.json',root/'data/racecards-archive.json']:
   data=read_json(f,{})
   for row in flat_cards(data):
    d=day(row.get('date') or row.get('race_time'))
    if d and start<=d<=end:found.setdefault(str(d),[]).append(row)
 return found

def parse_beaten(x):
 if isinstance(x,(int,float)) and math.isfinite(x):return max(0.0,float(x))
 s=str(x or '').lower().strip()
 if s in ('nse','nose'):return .06
 if s in ('sh','short head'):return .10
 if s in ('hd','head'):return .20
 if s in ('nk','neck'):return .30
 if s in ('dist','distance'):return 40.
 try:return float(re.sub(r'\s*(?:lengths?|lens?|l)$','',s))
 except ValueError:pass
 m=re.match(r'^(\d+)\s+(\d+)/(\d+)\s*(?:l|lengths?)?$',s)
 if m and int(m[3]):return int(m[1])+int(m[2])/int(m[3])
 return None

def win_status(r):
 pos=number(r.get('position') or r.get('finish_pos'))
 return pos is not None and int(pos)==1

def load_results(root,earliest,latest):
 root=pathlib.Path(root);candidates=[]
 for p in sorted((root/'data/racing-results').glob('*.json')):
  v=read_json(p,{})
  for r in (v.get('results') or v.get('races') or []):
   if isinstance(r,dict):candidates.append((r,p.name))
 v=read_json(root/'data/race-results.json',{})
 for r in v.get('races',[]):
  if isinstance(r,dict):candidates.append((r,'race-results.json'))
 by={};bad=collections.Counter()
 for r,source in candidates:
  d=day(r.get('date') or r.get('off_dt'))
  if d is None or d<earliest or d>latest:continue
  if str(r.get('status','')).lower()!='final':continue
  region=norm(r.get('region') or r.get('country'))
  if region and region not in ('UK','GB','GBR','IRE','IE','IRL','NORTHERNIRELAND'):
   bad['FOREIGN_REGION_EXCLUDED']+=1;continue
  if course(r.get('course')) not in UKIE_COURSES and region not in ('UK','GB','GBR','IRE','IE','IRL','NORTHERNIRELAND'):
   bad['UNVERIFIED_UK_IE_COURSE']+=1;continue
  key=race_key(r)
  if not key.endswith('|') and key.split('|')[1] and key.split('|')[2]:
   starters=[v for v in r.get('runners',[]) if isinstance(v,dict) and v.get('horse') and str(v.get('status','')).lower() not in STOP]
   if len(starters)<2 or sum(win_status(z) for z in starters)!=1:bad['incomplete_or_unverified_winner']+=1;continue
   # Prefer a full-field result with richer metadata over a sparse duplicate.
   quality=(len(starters),sum(bool(z.get('horse_id')) for z in starters),sum(bool(z.get('sire')) for z in starters),bool(r.get('race_name')))
   if key not in by or quality>by[key][1]:by[key]=({**r,'runners':starters,'_result_source':source},quality)
  else:bad['no_precise_off_time']+=1
 return {k:x[0] for k,x in by.items()},dict(bad)

def by_race_cards(cards):
 by=collections.defaultdict(list)
 for c in cards:by[race_key(c)].append(c)
 return by

def card_match(result,cardmap):
 rs=result['runners'];cards=[z for z in cardmap.get(race_key(result),[]) if str(z.get('race_status','')).lower() not in STOP]
 if len(cards)<2:return None,'NO_PREOFF_CARD'
 # Require unique exact field and unique horse names. Withdrawals can make the
 # pre-off field larger, so we require identity of the known actual starters.
 names=[norm(x['horse']) for x in rs];cardnames=[norm(x.get('horse')) for x in cards]
 if len(names)!=len(set(names)) or len(cardnames)!=len(set(cardnames)):return None,'DUPLICATE_HORSE_NAME'
 if not set(names)<=set(cardnames):return None,'PREOFF_FIELD_DISAGREES'
 lookup={norm(x['horse']):x for x in cards}
 return lookup,'PREOFF_FIELD_VERIFIED'

def identity(row):
 a=norm(row.get('horse'));s=norm(row.get('sire'));d=norm(row.get('dam'))
 if not a:return ''
 if s and d:return 'P|'+a+'|'+s+'|'+d
 h=str(row.get('horse_id') or row.get('horse_source_id') or '').strip()
 return 'ID|'+h if h else ''

def historical_matches(rootdb,profiles,until):
 """Fetch only past races for selected pedigrees. Unverified name-only rows skipped."""
 if rootdb is None:return [],{'name_only_skipped':0,'db_available':False}
 conn=sqlite3.connect('file:'+str(pathlib.Path(rootdb).resolve())+'?mode=ro',uri=True)
 conn.row_factory=sqlite3.Row
 names=sorted(set(norm(p['horse']) for p in profiles.values()))
 q=collections.defaultdict(list)
 for p in profiles.values():q[norm(p['horse'])].append((identity(p),norm(p.get('sire')),norm(p.get('dam')),str(p.get('horse_id') or '')))
 out=[];skipped=0;fieldcache={}
 for z in range(0,len(names),250):
  chunk=names[z:z+250]
  if not chunk:continue
  sql=('SELECT r.id AS race_id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,'
       'u.horse_name,u.horse_source_id,u.horse_name_norm,u.sire,u.dam,u.trainer,u.jockey,u.official_rating,'
       'u.weight_lbs,u.finish_pos,u.outcome,u.beaten,u.draw,u.sp_decimal '
       'FROM historical_runs u JOIN historical_races r ON r.id=u.race_id WHERE u.horse_name_norm IN ('+
       ','.join('?' for _ in chunk)+') AND r.race_date < ? ORDER BY r.race_date,r.id')
  for t in conn.execute(sql,[*chunk,str(until)]):
   r=dict(t);name=norm(r.get('horse_name'))
   options=[]
   for key,sire,dam,hid in q[name]:
    if not key:continue
    if hid and str(r.get('horse_source_id') or '')==hid:options.append(key)
    elif sire and dam and norm(r.get('sire'))==sire and norm(r.get('dam'))==dam:options.append(key)
   if len(set(options))!=1:skipped+=1;continue
   pk=next(iter(options));rid=r['race_id']
   if rid not in fieldcache:
    z=conn.execute('SELECT COUNT(*), AVG(CASE WHEN official_rating>0 THEN official_rating ELSE NULL END), COUNT(CASE WHEN official_rating>0 THEN 1 END) FROM historical_runs WHERE race_id=?',(rid,)).fetchone()
    fieldcache[rid]=(int(z[0]),z[1],int(z[2]))
   n,fieldor,validor=fieldcache[rid]
   item={'horse_key':pk,'horse':r['horse_name'],'race_key':'HIST|'+str(rid),'date':r['race_date'],
         'course':r['course'],'race_type':r['race_type'],'surface':r['surface'],'distance_f':r['distance_f'],
         'going':r['going'],'race_class':r['race_class'],'trainer':r['trainer'],'jockey':r['jockey'],
         'weight_lbs':r['weight_lbs'],'official_rating':r['official_rating'],'draw':r['draw'],
         'position':r['finish_pos'],'status':r['outcome'],'beaten':r['beaten'],'field_size':n,
         'field_or':fieldor if validor>=max(2,math.ceil(n*.5)) else None,
         'source':'archive_pedigree_or_id','source_race_id':rid}
   out.append(item)
 conn.close()
 return out,{'historical_past_runs':len(out),'name_only_skipped':skipped,'db_available':True}

def profiles_from_cards(cards):
 profiles={};names=collections.defaultdict(set)
 for c in cards:
  k=identity(c)
  if k:
   profiles[k]=c;names[norm(c.get('horse'))].add(k)
 return profiles,names

def result_events(results,cardmaps,profiles,names,earliest,latest):
 events=[];noid=collections.Counter();perrace={};datecards={}
 for k,r in results.items():
  d=day(r.get('date') or r.get('off_dt'))
  if d is None or not earliest<=d<=latest:continue
  daymap=cardmaps.get(str(d),{})
  cm,stat=card_match(r,daymap)
  perrace[k]=stat
  n=len(r['runners']);ors=[number(z.get('or') or z.get('official_rating')) for z in r['runners']]
  ov=[o for o in ors if o and o>0]
  fieldor=sum(ov)/len(ov) if len(ov)>=max(2,math.ceil(n*.5)) else None
  for z in r['runners']:
   if str(z.get('status','')).lower() in STOP:continue
   name=norm(z['horse']);snapshot=cm.get(name) if cm else None
   row={**(snapshot or {}),**{kk:vv for kk,vv in z.items() if vv is not None and vv!=''}}
   h=identity(row)
   if not h:
    # An outcome's name alone does not authorise a lifetime identity join.
    noid['NO_PEDIGREE_OR_ID']+=1;continue
   if snapshot is None and not (norm(z.get('sire')) and norm(z.get('dam'))):
    noid['NO_VERIFIED_FIELD_OR_PEDIGREE']+=1;continue
   # Link a sparse final-result name to its exact field's declaration identity.
   h=identity(snapshot) if snapshot and identity(snapshot) else h
   if h not in profiles:profiles[h]=row;names[name].add(h)
   ev={'horse_key':h,'horse':row['horse'],'race_key':k,'date':str(d),'course':r['course'],
       'race_type':r.get('type') or row.get('race_name') or row.get('type'),
       'surface':r.get('surface') or row.get('surface'),
       'distance_f':r.get('dist_f') or r.get('distance_f') or row.get('distance_f'),
       'going':r.get('going') or row.get('going'), 'race_class':r.get('class') or row.get('race_class'),
       'trainer':row.get('trainer'),'jockey':row.get('jockey'),
       'weight_lbs':row.get('weight_lbs') or row.get('lbs'),
       'official_rating':row.get('or') or row.get('ofr') or row.get('official_rating'),
       'draw':row.get('draw'), 'position':z.get('position'),
       'status':z.get('status'),'beaten':z.get('beaten') or row.get('beaten'),
       'field_size':n,'field_or':fieldor,
       'source':r.get('_result_source'), 'source_race_id':r.get('race_id') or '',
       'sp_for_settlement':z.get('sp_decimal') or z.get('sp'),
       'race_preoff_field_verified':stat=='PREOFF_FIELD_VERIFIED'}
   events.append(ev)
 return events,{'result_eligibility':dict(perrace),'identity_rejections':dict(noid)}

def pcode(e):
 s=(str(e.get('race_type') or '')+' '+str(e.get('surface') or '')).lower()
 if 'chase' in s or 'steeple' in s:return 'chase'
 if 'hurdle' in s:return 'hurdle'
 if 'bumper' in s or 'national hunt flat' in s:return 'nhflat'
 if 'tapeta' in s or 'polytrack' in s or 'all weather' in s or 'all-weather' in s or 'standard' in s:return 'aw'
 return 'flat'

def going_band(x):
 x=str(x or '').lower()
 if 'heavy' in x:return 'heavy'
 if 'soft' in x or 'yield' in x:return 'soft'
 if 'firm' in x:return 'firm'
 if 'standard' in x or 'tapeta' in x or 'polytrack' in x:return 'standard'
 if 'good' in x:return 'good'
 return ''

def tripband(e):
 z=number(e.get('distance_f'))
 return round(z/2)*2 if z and z>0 else None

def effective_weights(e):
 d=pcode(e);w=dict(WEIGHTS)
 if d in ('chase','hurdle','nhflat'):w.pop('Draw')
 else:w.pop('Jumping')
 # Draw is not an applicable historical signal without a usable stall.
 if number(e.get('draw')) is None or number(e.get('draw'))<=0:w.pop('Draw',None)
 total=sum(w.values());return {k:round(100*v/total,4) for k,v in w.items()}

def clamp(v,a=0.,b=100.):return min(b,max(a,v))

def run_quality(e):
 n=int(number(e.get('field_size')) or 0);pos=number(e.get('position'))
 if n<2:return None,'INVALID_FIELD'
 if pos is None:
  if str(e.get('status') or '').lower() not in UNFINISHED:return None,'UNKNOWN_POSITION'
  return 5.,'KNOWN_NONFINISHER'
 if pos<1 or pos>n:return None,'INVALID_POSITION'
 percentile=100*(n-pos)/(n-1)
 legs=[(percentile,.7)]
 beaten=parse_beaten(e.get('beaten'))
 if int(pos)==1:beaten=0.
 if beaten is not None:legs.append((100*math.exp(-beaten/7.),.3))
 quality=sum(v*w for v,w in legs)/sum(w for _,w in legs)
 # Use class/rating of contemporaneous opposition, not race results or SP.
 fieldor=number(e.get('field_or'))
 if fieldor is not None:quality+=clamp((fieldor-95)/12.,-7.,7.)
 clas=re.search(r'\bclass\s*([1-6])\b',str(e.get('race_class') or ''),re.I)
 if clas:quality+=(4-int(clas.group(1)))*1.1
 return round(clamp(quality),4),('FINISHED_WITH_MARGIN' if beaten is not None else 'FINISHED_NO_MARGIN')

def source_digest(e):
 # No prices or SP in the fingerprint. A result correction triggers an HPR
 # revision and the horse's subsequent ratings are recalculated in date order.
 return hashlib.sha256(json.dumps({k:v for k,v in e.items() if k!='sp_for_settlement'},
                          sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


def cached_event(con,e):
 z=con.execute('''SELECT payload FROM hpr_ratings WHERE horse_key=? AND race_key=? AND model_version=?
    ORDER BY revision DESC LIMIT 1''',(e['horse_key'],e['race_key'],VERSION)).fetchone()
 return json.loads(z[0]) if z else None


def score_event(e,prior):
 q,quality_status=run_quality(e)
 if q is None:return None,quality_status
 previous=[x for x in prior if x['date']<e['date']]
 overall=sum(x['hpr'] for x in previous[-5:])/len(previous[-5:]) if previous else None
 indicators={'Trainer':('trainer',lambda z:norm(z.get('trainer'))),
             'Jockey':('jockey',lambda z:norm(z.get('jockey'))),
             'Course':('course',lambda z:course(z.get('course'))),
             'Distance':('distance',tripband),
             'Ground':('going',lambda z:going_band(z.get('going'))),
             'Jumping':('discipline',pcode),
             'Draw':('draw',lambda z: int((number(z.get('draw')) or 0)*3/max(1,int(number(z.get('field_size')) or 1))))}
 pillars={'Ability':q};status={};w=effective_weights(e)
 for k in w:
  if k=='Ability':continue
  _,fn=indicators[k];key=fn(e)
  if key in ('',None) or (k=='Draw' and not number(e.get('draw'))):pillars[k]=q;status[k]='NO_APPLICABLE_PRIOR';continue
  matched=[x for x in previous if fn(x)==key]
  if not matched or overall is None:pillars[k]=q;status[k]='NEUTRAL_NO_PRIOR';continue
  mean=sum(x['hpr'] for x in matched[-10:])/len(matched[-10:])
  # Bounded/shrunk prior conditional outcome, NOT arbitrary penalties for new types.
  adjustment=clamp((mean-overall)*(len(matched)/(len(matched)+4)), -10.,10.)
  pillars[k]=round(clamp(q+adjustment),4);status[k]=f'PRIOR_SUPPORT_{len(matched)}'
 contextual=sum(pillars[k]*w[k]/100 for k in w)
 hpr=round(.7*q+.3*contextual,2)
 confidence='HIGH' if quality_status=='FINISHED_WITH_MARGIN' and len(previous)>=2 and len(w)>=5 else 'PARTIAL'
 result={k:e.get(k) for k in ('horse_key','horse','race_key','date','course','race_type','surface','distance_f','going','race_class','trainer','jockey','weight_lbs','official_rating','draw','position','status','beaten','field_size','source','source_race_id','race_preoff_field_verified')}
 result.update(source_digest=source_digest(e),hpr=hpr,raw_performance=q,pillar_composite=round(contextual,3),
               pillar_scores={k:round(v,3) for k,v in pillars.items()},effective_weights=w,
               evidence=status,quality_status=quality_status,confidence=confidence,
               prior_hpr_count=len(previous),model_version=VERSION)
 return result,'SCORED'

def store_connect(path):
 p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 con=sqlite3.connect(p)
 con.execute('PRAGMA busy_timeout=5000')
 con.executescript('''CREATE TABLE IF NOT EXISTS hpr_ratings(
  horse_key TEXT NOT NULL,race_key TEXT NOT NULL,race_date TEXT NOT NULL,
  model_version TEXT NOT NULL,revision INTEGER NOT NULL,source_sha TEXT NOT NULL,
  hpr REAL NOT NULL,confidence TEXT NOT NULL,payload TEXT NOT NULL,
  inserted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY(horse_key,race_key,model_version,revision));
  CREATE INDEX IF NOT EXISTS hpr_horse_date ON hpr_ratings(horse_key,race_date);
  CREATE INDEX IF NOT EXISTS hpr_race_date ON hpr_ratings(race_date);
  CREATE TABLE IF NOT EXISTS hpr_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
 con.execute('INSERT OR IGNORE INTO hpr_metadata VALUES (?,?)',('version',VERSION))
 return con

def save_ratings(con,rows):
 counts=collections.Counter()
 for row in rows:
  # Only ratings derived from real finished starts; no odds or SP persisted as features.
  payload=json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False)
  sig=hashlib.sha256(payload.encode()).hexdigest()
  key=(row['horse_key'],row['race_key'],row['model_version'])
  prev=con.execute('SELECT revision,source_sha FROM hpr_ratings WHERE horse_key=? AND race_key=? AND model_version=? ORDER BY revision DESC LIMIT 1',key).fetchone()
  if prev and prev[1]==sig:counts['unchanged']+=1;continue
  revision=prev[0]+1 if prev else 1
  con.execute('INSERT INTO hpr_ratings(horse_key,race_key,race_date,model_version,revision,source_sha,hpr,confidence,payload) VALUES (?,?,?,?,?,?,?,?,?)',
              (*key[:2],row['date'],key[2],revision,sig,row['hpr'],row['confidence'],payload))
  counts['revised' if prev else 'inserted']+=1
 con.commit();return dict(counts)

def previous_ratings(con,horse_key,before):
 x=con.execute('''SELECT r.payload FROM hpr_ratings r JOIN (
  SELECT horse_key,race_key,model_version,MAX(revision) mx FROM hpr_ratings
  WHERE horse_key=? AND race_date<? GROUP BY horse_key,race_key,model_version) latest
  ON latest.horse_key=r.horse_key AND latest.race_key=r.race_key AND latest.model_version=r.model_version AND latest.mx=r.revision
  ORDER BY r.race_date''',(horse_key,before)).fetchall()
 return [json.loads(r[0]) for r in x]

def preoff_hpr(prior,card):
 if not prior:return None
 recent=prior[-3:];weights=[.55,.30,.15][:len(recent)]
 value=sum(x['hpr']*weights[i] for i,x in enumerate(reversed(recent)))/sum(weights)
 # Slight observable-condition adjustment using prior HPRs, not prices.
 mean=sum(x['hpr'] for x in prior[-5:])/len(prior[-5:])
 for fn in (lambda x:going_band(x.get('going')), tripband,lambda x:pcode(x)):
  target=fn(card)
  if not target:continue
  same=[x['hpr'] for x in prior if fn(x)==target]
  if same:value+=clamp((sum(same[-10:])/len(same[-10:])-mean)*len(same)/(len(same)+4),-4,4)/3
 return round(clamp(value),3)

def rank_backtest(results,cards,con,start,end):
 grouped=collections.defaultdict(list)
 for x in cards:grouped[race_key(x)].append(x)
 items=[];reasons=collections.Counter()
 for k,r in sorted(results.items()):
  d=day(r.get('date') or r.get('off_dt'))
  if d is None or d<start or d>end:continue
  cards_here=[x for x in grouped.get(k,[]) if str(x.get('race_status','')).lower() not in STOP]
  if not cards_here:reasons['NO_PREOFF_CARD']+=1;continue
  winners=[z for z in r['runners'] if win_status(z)]
  if len(winners)!=1:reasons['NO_VERIFIED_WINNER']+=1;continue
  settlement={norm(x.get('horse')):x for x in r['runners']}
  full=[x for x in cards_here if norm(x.get('horse')) in settlement]
  if len(full)<2 or {norm(x.get('horse')) for x in full}!={norm(x.get('horse')) for x in r['runners']}:
   reasons['FIELD_UNVERIFIED_OR_CHANGED']+=1;continue
  predicted=[];applicable=0;scored=0
  for x in full:
   hid=identity(x)
   prior=previous_ratings(con,hid,str(d)) if hid else []
   had_prior=bool(str(x.get('form') or '').strip(' -')) or number(x.get('last_run')) is not None
   if had_prior:applicable+=1;scored+=int(bool(prior))
   rank=preoff_hpr(prior,x)
   if rank is not None:predicted.append((rank,x))
  if len(predicted)<2 or len(predicted)!=len(full) or (applicable and scored/applicable < .90):
   # This is an HPR-only research challenger. The eventual full E model may
   # assess verified debutants from other pillars, but never mis-rank them
   # merely for lacking a historical HPR.
   reasons['INSUFFICIENT_FULL_FIELD_HPR']+=1;continue
  predicted.sort(key=lambda x:(-x[0],norm(x[1].get('horse'))))
  for num_rank,(score,x) in enumerate(predicted[:2],1):
   z=settlement[norm(x['horse'])]
   # Settlement only; never an input to pre-off rankings.
   sp=z.get('sp_decimal') or z.get('sp')
   decimal=None
   if sp is not None:
    try:
     y=str(sp).strip()
     if '/' in y:
      a,b=y.split('/',1);decimal=1+float(a)/float(b)
     else:decimal=float(y)
    except (ValueError,ZeroDivisionError):pass
   items.append({'date':str(d),'course':r.get('course'),'time':off(r.get('off_dt') or r.get('off')), 'rank':num_rank,
     'horse':x.get('horse'),'hpr_preoff':score,'position':z.get('position'),'win':int(win_status(z)),
     'sp_decimal':decimal,'return_units':decimal if win_status(z) and decimal is not None else (0 if decimal is not None else None),
     'source':'preoff_github_card_and_prior_hpr','applicable_form_covered':f'{scored}/{applicable}'})
 summary=[]
 for n in (1,2):
  a=[r for r in items if r['rank']==n];b=[r for r in a if r['return_units'] is not None]
  gross=sum(r['return_units'] for r in b)
  summary.append({'rank':n,'selections':len(a),'wins':sum(r['win'] for r in a),
       'strike_rate_pct':round(100*sum(r['win'] for r in a)/len(a),2) if a else None,
       'priced_selections':len(b),'sp_roi_pct':round(100*(gross/len(b)-1),2) if b else None})
 return items,summary,dict(reasons)

def write_csv(p,rows,cols=None):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 cols=cols or (list(rows[0]) if rows else [])
 with p.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rows)

def self_test():
 assert abs(parse_beaten('2 1/2L')-2.5)<1e-10
 assert abs(parse_beaten('nk')-.30)<1e-10
 assert off('2026-10-20T20:30:00+01:00')=='19:30'
 assert course('Dundalk (AW) (IRE)')==course('Dundalk (AW)')
 e={'date':'2026-11-07','horse':'Test','horse_key':'P|TEST|SIRE|DAM','race_key':'2026-11-07|ASCOT|14:00',
     'position':3,'field_size':8,'beaten':'2 1/2L','trainer':'Joe','jockey':'Ali','course':'Ascot',
     'race_type':'Handicap Hurdle','going':'Good','distance_f':16,'source':'test','status':'ran','sire':'Sire','dam':'Dam'}
 q,st=run_quality(e);assert q is not None and 0<=q<=100
 r,ok=score_event(e,[]);assert ok=='SCORED' and 0<=r['hpr']<=100 and 'Draw' not in r['effective_weights']
 with tempfile.TemporaryDirectory() as td:
  db=pathlib.Path(td)/'db.sqlite';con=store_connect(db)
  x=save_ratings(con,[r]);assert x=={'inserted':1},x
  assert save_ratings(con,[r])=={'unchanged':1}
  assert not previous_ratings(con,e['horse_key'],e['date'])
  assert len(previous_ratings(con,e['horse_key'],'2026-11-08'))==1
  rev=dict(r,hpr=min(100,r['hpr']+1))
  assert save_ratings(con,[rev])=={'revised':1}
  assert len(previous_ratings(con,e['horse_key'],'2026-11-08'))==1
  con.close()
 assert identity({'horse':'Test','sire':'Sire','dam':'Dam'})=='P|TEST|SIRE|DAM'
 assert identity({'horse':'Test','sire':'','dam':'','horse_id':''})==''
 print('PASS: odds-blind HPR, applicable Draw, historical cutoff, stored revisions, idempotent ingestion')

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--mode',choices=['backtest','daily','self-test'],default='backtest')
 ap.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
 ap.add_argument('--db',type=pathlib.Path,help='Historical Stage84 original SQLite')
 ap.add_argument('--store',type=pathlib.Path,default=pathlib.Path('data/hpr/hpr-ratings.sqlite'))
 ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage86'))
 ap.add_argument('--as-of',type=str,help='As-of Irish date; defaults to today')
 ap.add_argument('--days',type=int,default=30)
 ap.add_argument('--date',type=str,help='Daily mode: completed race date; default yesterday')
 ap.add_argument('--git-preoff',action='store_true',help='Use real morning Git commit racecards in backtest')
 a=ap.parse_args()
 if a.mode=='self-test':self_test();return
 if not a.db or not a.db.exists():ap.error('--db historical archive is required')
 frozen=read_json(a.root/'reports/stage30/stage30-summary.json',{})
 expected=((frozen.get('model_results') or {}).get('D') or {}).get('weights_pct')
 if expected:
  for label,w in WEIGHTS.items():
   if label not in expected or abs(float(expected[label])-w)>.03:
    raise RuntimeError('Frozen Model D pillar weights differ from the documented HPR starting point: '+label)
 else:
  raise RuntimeError('Original Stage30 frozen D weight evidence is unavailable; refuse research run')
 asof=day(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
 if asof is None or not 1<=a.days<=90:ap.error('Invalid as-of date or days (1..90)')
 start=asof-dt.timedelta(days=a.days);end=asof-dt.timedelta(days=1)
 target=day(a.date) if a.date else end
 if a.mode=='daily':
  if target is None or target>=asof:ap.error('Daily mode only rates completed previous dates')
  start=target;end=target
 out=a.output;out.mkdir(parents=True,exist_ok=True)
 # Recent finals back to 2026-10-08 form a continuous historical bridge.
 # Older races are sourced exclusively from the dated SQLite source archive.
 earliest=max(start-dt.timedelta(days=90),dt.date(2026,10,8))
 all_results,bad=load_results(a.root,earliest,end)
 lookup_start=earliest if a.mode=='backtest' else start
 lookup_end=end
 dated=card_dates(a.root,lookup_start,lookup_end,git_preoff=a.git_preoff and a.mode=='backtest')
 if a.mode=='daily':
  # For daily settlement we may use earlier captured card snapshots if today's
  # committed racecards have already rolled; if absent, only full-result IDs
  # and pedigree can qualify, not a name-only guess.
  for d in list(dated):pass
  if not dated and a.git_preoff:
   dated=card_dates(a.root,start,end,git_preoff=True)
 cardmaps={d:by_race_cards(rs) for d,rs in dated.items()}
 cards=[x for rs in dated.values() for x in rs]
 profiles,names=profiles_from_cards(cards)
 # Also extract identities directly supplied in rich archived results, not a
 # name-only guess. These profile the horses for old source-history searches.
 for r in all_results.values():
  for z in r['runners']:
   if z.get('horse') and ((z.get('sire') and z.get('dam')) or z.get('horse_id')):
    k=identity(z)
    if k:profiles.setdefault(k,z);names[norm(z['horse'])].add(k)
 old,ha=historical_matches(a.db,profiles,start if a.mode=='backtest' else target)
 current,ra=result_events(all_results,cardmaps,profiles,names,earliest,end)
 all_events=old+current
 # Historical duplicates: preserve original archive where same horse/date/race
 # appears in both. Only one run per exact race identity and date.
 seen=set();dedup=[]
 for e in sorted(all_events,key=lambda z:(z['date'],str(z['race_key']).startswith('HIST|'),z['race_key'])):
  key=(e['horse_key'],e['date'],course(e['course']))
  if key in seen:continue
  seen.add(key);dedup.append(e)
 if a.mode=='backtest':
  # Preserve the research test in a separate temporary SQLite; main repository
  # persistent database is read-only for this mode.
  store=out/'hpr-backtest-research.sqlite'
 else:store=a.store
 con=store_connect(store)
 per=collections.defaultdict(list);rated=[];fail=collections.Counter()
 reused=0;dirty=set()
 for e in sorted(dedup,key=lambda x:(x['date'],x['race_key'],x['horse_key'])):
  # Ratings already assessed from identical evidence are reused intact.
  # Source changes or newly recovered old runs require recalculating this
  # horse's later assessments, with auditable revisions rather than erasure.
  key=e['horse_key']
  should_mark_dirty=False
  if a.mode=='daily' and key not in dirty:
   prev=cached_event(con,e)
   if prev and prev.get('source_digest')==source_digest(e):
    per[key].append(prev);reused+=1;continue
   should_mark_dirty=True
  scored,status=score_event(e,per[key])
  if scored is None:
   fail[status]+=1
   # A truly unusable historical row doesn't invalidate later cached
   # ratings: it never contributed an HPR in the first place.
   continue
  if should_mark_dirty:dirty.add(key)
  per[key].append(scored)
  rated.append(scored)
 stat=save_ratings(con,rated)
 con.close()
 # Preoff HPR ranking reads all stored prior HPRs but never same-day outcomes.
 if a.mode=='backtest':
  con=store_connect(store)
  items,metrics,reasons=rank_backtest(all_results,cards,con,start,end)
  con.close()
  write_csv(out/'hpr-race-by-race.csv',items,['date','course','time','rank','horse','hpr_preoff','position','win','sp_decimal','return_units','source','applicable_form_covered'])
  write_csv(out/'hpr-metrics.csv',metrics)
 else:
  items=[];metrics=[];reasons={}
  finalday=[e for e in rated if e['date']==str(target)]
  # Immutable audit daily event snapshot, though the sqlite store is the
  # canonical revisioned database. Update snapshot only by full revisioned
  # state; append-only revisions remain available in SQLite.
  dailyfile=a.root/'data/hpr/daily'/(str(target)+'.json')
  prior=read_json(dailyfile,{}) or {}
  archived={x['horse_key']+'|'+x['race_key']+'|'+x['model_version']:x for x in prior.get('ratings',[])}
  for e in finalday:archived[e['horse_key']+'|'+e['race_key']+'|'+e['model_version']]=e
  save_json(dailyfile,{'date':str(target),'model_version':VERSION,
      'ratings':sorted(archived.values(),key=lambda z:(z['race_key'],z['horse_key'])),
      'status':'PARTIAL_UNLESS_ALL_FINAL_RESULTS_VERIFIED',
      'safety':'This snapshot is secondary evidence. Full revision history in SQLite.'})
  write_csv(out/'hpr-rated-today.csv',finalday)
 report={'model':'E_HPR_RESEARCH_ONLY','version':VERSION,'mode':a.mode,'as_of':str(asof),
         'window':{'start':str(start),'end':str(end),'days':a.days},
         'source_results':len(all_results),'cards_snapshotted_days':len(dated),'horse_profiles':len(profiles),
         'history':ha,'outcomes':ra,'invalid_results':bad,'rated_events':len(rated),
         'store_write':stat,'historical_ratings_reused':reused,'ratings_rejected':dict(fail),'backtest_rejections':reasons,
         'backtest_metrics':metrics,'historical_data_90pct_verified':False,
         'warning':'Early research HPR and limited scored-race test, not an independent validation of superior strike rate or ROI. Original frozen D and D(c) remain unchanged.'}
 save_json(out/'hpr-audit.json',report)
 print(json.dumps({k:report[k] for k in ('version','mode','window','source_results','cards_snapshotted_days','horse_profiles','rated_events','store_write','ratings_rejected','backtest_rejections','backtest_metrics')},indent=2))

if __name__=='__main__':main()
