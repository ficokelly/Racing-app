#!/usr/bin/env python3
"""Stage94: read-only, date-locked backcast of original D(c) scoring and win-prob calibration.

The live D and D(c) feeds are never read as predictions for past dates and never
written. Research estimates reconstruct historical races from pre-off Git card
snapshots, and all model inputs have strict previous-day time cutoffs. Real
pre-off historical selections do not exist before 9 Nov 2026; label BACKCAST.

Caution: this replays the frozen Stage83 scoring recipe on historically available
data, which may differ from precisely what a scheduler would have known then.
Results and SP enter settlement ONLY, and missing SP stays missing.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, re, sqlite3, statistics, sys
from zoneinfo import ZoneInfo
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage30_gap_aware_abcd as OLD
import stage65p_race_type_c_only as C
import stage38_model_dz_live as D
import rhp_stage83_dc_integrated as S83
import stage86_hpr_engine as R86

UTC=dt.timezone.utc
THRESHOLDS=(20,25,30,32,34,35,36,38,40,42,45,50,55,60)
TEMPS=(.30,.40,.50,.60,.75,.90,1.10,1.30,1.60,2.,2.5,3.,3.75,4.5,6.)
STOP={'nr','nonrunner','non-runner','withdrawn','scratched','dns','did not start'}
COLUMNS=['date','time','course','discipline','race_type','race_name','horse','rank','score',
 'win_probability_pct','finish','result_status','won','sp','sp_decimal','sp_source','pnl_1u',
 'race_eligible','d_c_research_qualifier','data_split','card_commit_snapshot','model_version','research_only']

def write_csv(p,rows,columns):
 p=pathlib.Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(rows)

def race_disc(card):
 text=str(card.get('type') or card.get('race_type') or '').lower();name=str(card.get('race_name') or '').lower()
 s=str(card.get('surface') or '').lower();place=str(card.get('course') or '').lower()
 if any(z in text+' '+name for z in ('hurdle','chase','steeple','national hunt flat','nh flat','bumper','point-to-point flat')):return 'Jumps'
 if 'standard' in s or 'polytrack' in s or 'tapeta' in s or s in ('aw','all weather','all-weather') or '(aw)' in place:return 'AW'
 if s=='turf' or 'flat' in text:return 'Flat'
 return 'Unknown'

def race_cat(card):
 title=str(card.get('race_name') or '').lower();typ=str(card.get('type') or card.get('race_type') or '').lower()
 if 'point-to-point flat' in title or 'bumper' in title or 'national hunt flat' in title or 'nh flat' in typ:return 'NH Flat / Bumper'
 if 'hunter' in title and ('chase' in title or 'steeple' in title):return 'Hunter Chase'
 if re.search(r'\b(?:grade|group)\s*(?:[123]|i{1,3})\b',title):return 'Graded/Group'
 if 'listed' in title:return 'Listed'
 if 'handicap' in title or 'nursery' in title:
  if 'novice' in title:return 'Novice Handicap Chase' if 'chase' in typ else 'Novice Handicap Hurdle' if 'hurdle' in typ else 'Novice Handicap'
  if 'chase' in typ:return 'Handicap Chase'
  if 'hurdle' in typ:return 'Handicap Hurdle'
  return 'Handicap'
 if 'maiden' in title:return 'Maiden Hurdle' if 'hurdle' in typ else 'Maiden'
 if 'beginner' in title:return 'Beginners Chase' if 'chase' in typ else 'Beginners'
 if 'novice' in title:return 'Novice Chase' if 'chase' in typ else 'Novice Hurdle' if 'hurdle' in typ else 'Novice'
 if 'selling' in title or 'seller' in title:return 'Selling'
 if 'claim' in title:return 'Claiming'
 return 'Other/Unknown'

def tempscore(scores,temp):
 z=np.array(scores,dtype=float);assert z.ndim==1 and z.size>=2 and temp>0
 a=(z-z.max())/temp;a=np.exp(a);p=a/a.sum()
 assert np.isfinite(p).all() and abs(float(p.sum())-1.)<1e-9
 return p

def sp_decimal(s):
 try:
  if s is None or str(s).strip()=='':return None
  if isinstance(s,(float,int)) and 1.001<float(s)<10000:return float(s)
  t=str(s).strip().upper().replace(' ','').replace('F','').replace('J','')
  if t in ('EVS','EVENS','EVEN'):return 2.
  if re.fullmatch(r'\d+/\d+',t):
   a,b=map(int,t.split('/'));return 1+a/b if b else None
  if t.endswith('/1') and t.split('/')[0].isdigit():return 1+int(t.split('/')[0])
 except (TypeError,ValueError,ZeroDivisionError):pass
 return None

def parse_finish(r):
 s=str(r.get('position') or r.get('finish_pos') or '').strip()
 status=str(r.get('status') or '').strip()
 if s.isdigit() and int(s)>0:return str(int(s)),int(s)==1
 if status.lower() in STOP:return 'NR',False
 return (s or status.upper() or 'UNVERIFIED'),False

def price_candidates(root,start,end):
 """Independently published result files, supplemented from verified URLs where present.
 Never use a race SP for forecasting or for probability calibration.
 """
 root=pathlib.Path(root);by=collections.defaultdict(list)
 files=sorted((root/'data/racing-results').glob('*.json'))
 files.append(root/'data/race-results.json')
 for p in files:
  try:j=json.loads(p.read_text())
  except (OSError,ValueError):continue
  for race in (j.get('results') or j.get('races') or []):
   if str(race.get('status') or '').lower()!='final':continue
   day=R86.day(race.get('date') or race.get('off_dt'))
   if not day or not start<=day<=end:continue
   key=R86.race_key(race)
   for rr in race.get('runners',[]):
    if not isinstance(rr,dict) or not rr.get('horse'):continue
    sp=rr.get('sp') or rr.get('starting_price');dv=sp_decimal(sp)
    dprice=rr.get('sp_decimal')
    if dprice is not None:
     try:
      dd=float(dprice)
      if 1.001<dd<10000:dv=dd
     except (TypeError,ValueError):pass
    if dv is not None:
     by[key,R86.norm(rr['horse'])].append((sp or f'{dv-1:.6g}/1',dv,str(race.get('verified_source') or p.name)))
 return by


def external_price_candidates(days,existing):
 """Optional verified external SP recovery from public dated results tables.

 The provider must expose a dedicated Horse and SP column. If its structure
 changes, no price is inferred; a failed request is harmless.
 """
 try:
  from bs4 import BeautifulSoup
  from urllib.request import Request,urlopen
 except ImportError:
  print('External SP recovery skipped: BeautifulSoup unavailable',flush=True);return {}
 evidence=collections.defaultdict(list)
 totals=collections.Counter()
 for day in sorted(days):
  url='https://stablewhispers.co.uk/fast-results/'+day+'/'
  try:
   req=Request(url,headers={'User-Agent':'RacingHotPotsResearch/1.0 (historical SP evidence)'})
   with urlopen(req,timeout=9) as reply:
    content=reply.read(3_000_000).decode('utf-8','replace')
   soup=BeautifulSoup(content,'html.parser')
   found=0
   for table in soup.find_all('table'):
    rows=table.find_all('tr')
    header=rows[0].find_all(['th','td']) if rows else []
    cols=[x.get_text(' ',strip=True).strip().lower() for x in header]
    if not ('horse' in cols and 'sp' in cols):continue
    h_idx=cols.index('horse');p_idx=cols.index('sp');pos_idx=cols.index('pos') if 'pos' in cols else None
    for tr in rows[1:]:
     cells=tr.find_all(['td','th'])
     if len(cells)<=max(h_idx,p_idx):continue
     horse_text=cells[h_idx].get_text(' ',strip=True)
     horse_text=re.sub(r'^\s*\d+\s+','',horse_text)
     # Some sites carry country suffix after the name; preserve it for normalisation.
     horse_key=R86.norm(horse_text)
     # If noisy numbers precede names, retain a conservative exact match requirement.
     sp_text=cells[p_idx].get_text(' ',strip=True)
     m=re.search(r'(?<![A-Z0-9])(\d{1,3}\s*/\s*\d{1,3}|EVENS?|EVS)(?![A-Z0-9])',sp_text,re.I)
     if not m or not horse_key:continue
     price=m.group(1).replace(' ','');decimal=sp_decimal(price)
     if decimal is None:continue
     pos_text=cells[pos_idx].get_text(' ',strip=True) if pos_idx is not None and len(cells)>pos_idx else ''
     evidence[(day,horse_key)].append((price,decimal,url,pos_text))
     found+=1
   totals['pages_scanned']+=1;totals['prices_parsed']+=found
  except Exception as exc:
   totals['fetch_failures']+=1
   if totals['fetch_failures']<=4:print('External SP page unavailable:',day,type(exc).__name__,flush=True)
 out={}
 for k,vals in evidence.items():
  ds={round(v[1],4) for v in vals}
  # Multiple distinct SP prices for a horse on one date would be unsafe.
  if len(ds)==1 and len(vals)==1:out[k]=vals[0]
 print('External SP recovery:',dict(totals),'unique candidate rows:',len(out),flush=True)
 return out

def choose_price(index,key,horse):
 vals=index.get((key,R86.norm(horse)),[])
 if not vals:return ('',None,'MISSING_SP')
 # Warn about inconsistent independent results sources, but don't invent consensus.
 uniq={round(x[1],4) for x in vals}
 if len(uniq)>1:return ('',None,'CONFLICTING_SP')
 return vals[0]

def load_incremental_history(db,start,end):
 """Day-indexed result updates for BOTH engines. Never add today's results until tomorrow."""
 conn=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
 u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
 u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment,
 COALESCE(z.model_race_type,d.race_name,r.race_type),u.weight_lbs,u.beaten,u.rpr
 FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
 LEFT JOIN stage65p_race_details d ON d.race_id=r.id
 LEFT JOIN stage83_dc_verified_types z ON z.race_id=r.id
 WHERE r.race_date>=? AND r.race_date<=? ORDER BY r.race_date,r.id'''
 days=collections.defaultdict(list);prev=None;group=[]
 def add(gr):
  if gr:days[gr[0][1]].append(gr)
 for row in conn.execute(sql,(start,end)):
  if prev is not None and row[0]!=prev:add(group);group=[]
  group.append(row);prev=row[0]
 add(group);conn.close()
 return days

def add_completed_date(past,form,groups):
 for group in groups:
  pastrows=[z[:23] for z in group]
  if OLD.track(pastrows[0][2]) in OLD.TRACKS:past.update(pastrows)
  formrows=[]
  for z in group:
   a={'date':z[1],'race_id':z[0],'course':z[2],'race_type':z[23],
      'surface':z[4],'race_class':z[7],'horse':z[9],'country':z[10],
      'trainer':z[14],'jockey':z[15],'or':z[16],
      'weight':z[24],'finish':z[12],'beaten':z[25],'rpr':z[26],
      'field_size':len(group)}
   formrows.append(a)
  for a in formrows:form.update(a)

def score_date(entries,day,past,form,clf,weights):
 scaler,model=clf;allout=[]
 unique={}
 for h,country in past.last:
  if h not in unique:unique[h]=country
  elif unique[h]!=country:unique[h]=None
 for r,e,matched in entries:
  cards=matched
  if len(cards)<2:continue
  x=[D.live_row(z,day,'D-C-RETRO|'+day+'|'+D.name(e['course'])+'|'+e['time'],unique) for z in cards]
  original=past.score(x,live=True)
  if original is None:continue
  dsc,_,_=D.prediction_rows(original,weights)
  oscores=[];audit=[]
  ors=[C.num(z.get('official_rating') or z.get('or') or z.get('ofr')) for z in cards]
  ww=[C.num(z.get('weight_lbs')) for z in cards]
  avgo=sum(v for v in ors if v is not None)/sum(v is not None for v in ors) if any(v is not None for v in ors) else None
  avgw=sum(v for v in ww if v is not None)/sum(v is not None for v in ww) if any(v is not None for v in ww) else None
  for z in cards:
   f={'date':day,'horse':z.get('horse'),'country':z.get('horse_country'),
     'trainer':z.get('trainer'),'jockey':z.get('jockey'),
     'race_type':z.get('race_name') or z.get('race_type') or z.get('type'),
     'surface':z.get('surface'),'race_class':z.get('race_class'),
     'or':z.get('official_rating') or z.get('or') or z.get('ofr'),
     'weight':z.get('weight_lbs')}
   v,details=form.vector(f,avgo,avgw);oscores.append(v);audit.append(details)
  prob=model.predict_proba(scaler.transform(np.asarray(oscores)))[:,1]
  dz=(dsc-dsc.mean())/(dsc.std()+1e-8)
  cz=(prob-prob.mean())/(prob.std()+1e-8)
  combined=.75*dz+.25*cz
  order=sorted(range(len(cards)),key=lambda i:(-float(combined[i]),i))
  dorder=sorted(range(len(cards)),key=lambda i:(-float(dsc[i]),i))
  gate_pass=bool(order[0]==dorder[0] and float(dsc[dorder[0]]-dsc[dorder[1]])>=.14)
  allout.append({'date':day,'race':r,'entry':e,'cards':cards,'scores':combined,'gate_pass':gate_pass,
    'order':order,'audit':audit,'disc':race_disc(cards[0]),'type':race_cat(cards[0])})
 return allout

def train_temperature(rows,min_subgroup=45):
 """Only earlier 23 calendar days are used to tune probability temperatures."""
 groups=collections.defaultdict(list)
 for r in rows:
  groups['ALL'].append(r)
  groups['D:'+r['disc']].append(r)
  groups['T:'+r['disc']+'|'+r['type']].append(r)
 selected={}
 for g,rr in groups.items():
  if g.startswith('T:') and len(rr)<min_subgroup:continue
  if g.startswith('D:') and len(rr)<20:continue
  loss=[]
  for temp in TEMPS:
   z=[]
   for r in rr:
    p=tempscore(r['scores'],temp)
    z.append(-math.log(max(1e-12,float(p[r['winner_idx']]))))
   loss.append((sum(z)/len(z),temp))
  selected[g]={'temperature':min(loss)[1],'train_races':len(rr),'train_log_loss':round(min(loss)[0],5)}
 return selected

def category_temp(sel,r):
 # Minimum sample requirement for subtype-specific temperature; fallback to discipline.
 for key in ('T:'+r['disc']+'|'+r['type'],'D:'+r['disc'],'ALL'):
  if key in sel:return sel[key]['temperature'],key
 return 1.,'UNTRAINED'

def stats(rows):
 n=len(rows);wins=sum(int(z['won']) for z in rows)
 priced=[z for z in rows if z['sp_decimal'] is not None and z['finish']!='NR']
 pnl=sum(float(z['pnl_1u']) for z in priced)
 return {'selections':n,'winners':wins,'strike_rate_pct':round(100*wins/n,2) if n else None,
   'priced':len(priced),'priced_winners':sum(int(z['won']) for z in priced),
   'sp_missing':n-len(priced),'profit_1u':round(pnl,3),
   'sp_roi_pct':round(100*pnl/len(priced),2) if priced else None}

def self_test():
 p=tempscore([2.,1.,0.],.5);assert abs(sum(p)-1)<1e-10 and p[0]>p[1]>p[2]
 assert sp_decimal('9/4')==3.25 and sp_decimal('EVS')==2. and sp_decimal(None) is None
 assert race_disc({'surface':'AW','type':'Flat'})=='AW'
 assert race_disc({'surface':'Turf','type':'Hurdle'})=='Jumps'
 assert race_cat({'race_name':"A Handicap Hurdle",'type':'Hurdle'})=='Handicap Hurdle'
 assert parse_finish({'position':1})==('1',True)
 assert parse_finish({'position':'F','status':'fell'})==('F',False)
 assert stats([{'won':True,'sp_decimal':3.,'finish':'1','pnl_1u':2.},
   {'won':False,'sp_decimal':4.,'finish':'3','pnl_1u':-1.}])['sp_roi_pct']==50.
 print('PASS: probability invariants, no odds inputs to scores, SP settlement and race type')

def run(a):
 root=pathlib.Path(a.root);out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
 asof=dt.date.fromisoformat(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
 end=asof-dt.timedelta(days=1);start=end-dt.timedelta(days=a.days-1)
 lastseven=end-dt.timedelta(days=6)
 print('Stage94 research period',start,end,'holdout',lastseven,end,flush=True)
 C.rows=S83.corrected_rows
 clf=S83.patched_classifier(None,C,a.db)
 audit=json.loads((root/'reports/stage30/stage30-summary.json').read_text())
 frozen=audit.get('model_results',{}).get('D',{})
 if frozen.get('chosen_combination')!='D-064-gap14' or abs(float(frozen.get('selection_margin',0))-.14)>1e-8:
  raise RuntimeError('Frozen D baseline has changed; refusing non-equivalent replay')
 weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
 resultdb=R86.load_results(root,start,end)[0]
 cards_by_date=R86.card_dates(root,start,end,git_preoff=True)
 prices=price_candidates(root,start,end)
 external={} if a.no_external_sp else external_price_candidates(
  ((start+dt.timedelta(days=k)).isoformat() for k in range(a.days)),prices)
 incremental=load_incremental_history(a.db,start.isoformat(),end.isoformat())
 past=D.LivePast({});scan=past.scan_until(a.db,start.isoformat());print('Past scanned',scan,flush=True)
 form=C.Form()
 for group in C.rows(a.db,start.isoformat()):
  for z in group:
   z['field_size']=len(group);form.update(z)
 print('C previous history loaded',len(form.h),'profiles',flush=True)
 staged=[];reject=collections.Counter();covered=collections.Counter();rejected_rows=[]
 for day_idx in range(a.days):
  day=(start+dt.timedelta(days=day_idx)).isoformat()
  candidates=[];cards=R86.by_race_cards(cards_by_date.get(day,[]))
  for key,race in resultdb.items():
   if key[:10]!=day:continue
   matched,status=R86.card_match(race,cards)
   if status!='PREOFF_FIELD_VERIFIED':
    reject[status]+=1;rejected_rows.append({'date':day,'course':race.get('course'),'time':key.split('|')[-1],'reason':status});continue
   preoff=list(matched.values())
   actual={R86.norm(x['horse']) for x in race['runners']}
   preoff={R86.norm(x['horse']) for x in preoff}
   # NR field changes after a morning snapshot create a different model ranking.
   # Refuse hindsight removal; don't pretend it was a truly live prediction.
   if actual!=preoff:
    reject['FIELD_CHANGED_AFTER_SNAPSHOT']+=1;rejected_rows.append({'date':day,'course':race.get('course'),'time':key.split('|')[-1],'reason':'FIELD_CHANGED_AFTER_SNAPSHOT'});continue
   if len(preoff)<2 or any(not x.get('trainer') or not x.get('jockey') for x in preoff):
    reject['MISSING_PREOFF_FEATURES']+=1;rejected_rows.append({'date':day,'course':race.get('course'),'time':key.split('|')[-1],'reason':'MISSING_PREOFF_FEATURES'});continue
   source=preoff[0]
   e={'date':day,'course':source['course'],'time':key.split('|')[-1],
      'runners':preoff}
   candidates.append((race,e,preoff))
  for r in score_date(candidates,day,past,form,clf,weights):
   lookup={R86.norm(z['horse']):z for z in r['race']['runners']}
   if not all(R86.norm(z['horse']) in lookup for z in r['cards']):
    reject['RESULT_FIELD_CONFLICT']+=1;rejected_rows.append({'date':day,'course':r['entry']['course'],'time':r['entry']['time'],'reason':'RESULT_FIELD_CONFLICT'});continue
   r['winner_idx']=next((i for i,z in enumerate(r['cards']) if R86.win_status(lookup[R86.norm(z['horse'])])),None)
   if r['winner_idx'] is None:
    reject['WINNER_NOT_ON_CARD']+=1;rejected_rows.append({'date':day,'course':r['entry']['course'],'time':r['entry']['time'],'reason':'WINNER_NOT_ON_CARD'});continue
   if r['disc']=='Unknown':
    reject['UNKNOWN_DISCIPLINE']+=1;rejected_rows.append({'date':day,'course':r['entry']['course'],'time':r['entry']['time'],'reason':'UNKNOWN_DISCIPLINE'});continue
   staged.append(r);covered[day]+=1
  # Up-to-date scoring state for tomorrow, only after ALL today's picks made.
  add_completed_date(past,form,incremental.get(day,[]))
  if day_idx%3==0:print('Scored',day,'races',covered[day],flush=True)
 if len(staged)<100:raise RuntimeError(f'Only {len(staged)} matched races; retrospective coverage unexpectedly low')
 train=[r for r in staged if r['date']<lastseven.isoformat()]
 held=[r for r in staged if r['date']>=lastseven.isoformat()]
 calibrated=train_temperature(train)
 if not calibrated or not held:raise RuntimeError('Insufficient chronological probability calibration/holdout')
 allsel=[];allrunner=[];calibcounts=collections.Counter()
 for r in staged:
  temp,source=category_temp(calibrated,r);p=tempscore(r['scores'],temp)
  if abs(float(p.sum())-1)>1e-8:raise RuntimeError('Race probabilities do not sum 100%')
  split='HOLDOUT' if r['date']>=lastseven.isoformat() else 'TRAIN'
  for rank,i in enumerate(r['order'],1):
   horse=r['cards'][i]['horse'];rr={R86.norm(x['horse']):x for x in r['race']['runners']}[R86.norm(horse)]
   finish,won=parse_finish(rr)
   key=R86.race_key(r['race'])
   sp,dv,sp_src=choose_price(prices,key,horse)
   if dv is None:
    evidence=external.get((r['date'],R86.norm(horse)))
    if evidence:
     extsp,extdecimal,exturl,extpos=evidence
     extpos=re.sub(r'[^0-9A-Z]','',str(extpos).upper())
     if (not extpos or extpos==str(finish).upper() or
        (finish in ('F','PU','UR') and extpos.startswith(str(finish)))):
      sp,dv,sp_src=extsp,extdecimal,exturl
   # Verify that the SP price candidate belongs to this exact race; unavailable is not 0.
   row={'date':r['date'],'time':r['entry']['time'],'course':r['entry']['course'],
      'discipline':r['disc'],'race_type':r['type'],'race_name':r['cards'][0].get('race_name') or '',
      'horse':horse,'rank':rank,'score':round(float(r['scores'][i]),6),
      'win_probability_pct':round(100*float(p[i]),3),'finish':finish,
      'result_status':rr.get('status') or '', 'won':int(won),
      'sp':sp,'sp_decimal':dv,'sp_source':sp_src,
      'pnl_1u':(round(dv-1,5) if won else -1.) if dv is not None else None,
      'race_eligible':True,'d_c_research_qualifier':bool(r['gate_pass'] and rank==1),'data_split':split,
      'card_commit_snapshot':'git-before-09:45-UTC','model_version':'D(c) Stage83 recipe / research replay',
      'research_only':True,'temperature_source':source}
   if rank==1:allsel.append(row)
   if split=='HOLDOUT':allrunner.append(row)
  calibcounts[source]+=1
 allsel.sort(key=lambda r:(r['date'],r['time'],r['course']))
 last7=[r for r in allsel if r['data_split']=='HOLDOUT']
 if len({(r['date'],r['time'],r['course']) for r in allsel})!=len(allsel):
  raise RuntimeError('Duplicated races in Rank 1 results')
 outputs=[(out/'stage94-30d-all-rank1.csv',allsel),(out/'stage94-last7-rank1.csv',last7),
          (out/'stage94-last7-all-ranked-runners.csv',allrunner)]
 for path,rows in outputs:write_csv(path,rows,COLUMNS+['temperature_source'])
 band=[]
 for segment,rows in [('ALL_30_DAYS',allsel),('HOLDOUT_7_DAYS',last7),('TRAINING_PERIOD',[x for x in allsel if x['data_split']=='TRAIN'])]:
  for threshold in THRESHOLDS:
   ss=[z for z in rows if float(z['win_probability_pct'])>=threshold]
   band.append({'period':segment,'minimum_win_probability_pct':threshold,**stats(ss)})
 write_csv(out/'stage94-thresholds.csv',band,['period','minimum_win_probability_pct','selections','winners','strike_rate_pct','priced','priced_winners','sp_missing','profit_1u','sp_roi_pct'])
 subtype=[]
 for period,rr in [('ALL_30_DAYS',allsel),('HOLDOUT_7_DAYS',last7)]:
  for groupkey in ('discipline','race_type'):
   for k in sorted(set(z[groupkey] for z in rr)):
    group=[z for z in rr if z[groupkey]==k]
    subtype.append({'period':period,'group_by':groupkey,'group':k,**stats(group)})
 write_csv(out/'stage94-subtypes.csv',subtype,['period','group_by','group','selections','winners','strike_rate_pct','priced','priced_winners','sp_missing','profit_1u','sp_roi_pct'])
 write_csv(out/'stage94-excluded-races.csv',rejected_rows,['date','course','time','reason'])
 summary={'stage':94,'model':'D(c) ORIGINAL 75% D + 25% C, BACKCAST not historically published',
   'start':start.isoformat(),'end':end.isoformat(),'holdout_start':lastseven.isoformat(),
   'races':len(staged),'training_races':len(train),'holdout_races':len(held),
   'all30':stats(allsel),'last7':stats(last7),
   'all30_rank1_research_qualifiers':stats([x for x in allsel if x['d_c_research_qualifier']]),
   'last7_rank1_research_qualifiers':stats([x for x in last7 if x['d_c_research_qualifier']]),'calibrations':calibrated,
   'chosen_temperature_sources':dict(calibcounts),'rejections':dict(reject),'by_day':dict(covered),
   'missing_sp_examples':[{k:r[k] for k in ('date','time','course','horse','finish','win_probability_pct')} for r in allsel if r['sp_decimal'] is None][:50],
   'assumptions':['Only archived pre-off Git snapshots at or before 09:45 UTC for each date',
     'Only exact complete pre-off starter fields matched to verified final results',
     'Stage83 historical race type integration; classifier trained in 2024, tested 2025',
     'Original frozen D prior weights and 75%-25% D(c) score formula unchanged',
     'One calendar-day lag; 3-9 Nov probability holdout calibrated on preceding dates',
     'Missing prices excluded from ROI numerator and denominator, never counted as losing bets',
     'Results and SP never enter selection or probability calculations',
     'Not a record of selections actually published before 9 November 2026']}
 (out/'stage94-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':
 a=argparse.ArgumentParser(description=__doc__)
 a.add_argument('--db',default='reports/stage94/base/backfilled_research_only.sqlite')
 a.add_argument('--root',default='.')
 a.add_argument('--output',default='reports/stage94/output')
 a.add_argument('--as-of',default='2026-11-10')
 a.add_argument('--days',type=int,default=30)
 a.add_argument('--self-test',action='store_true')
 a.add_argument('--no-external-sp',action='store_true',help='Disable optional public source SP recovery')
 z=a.parse_args()
 if z.self_test:self_test()
 else:run(z)
