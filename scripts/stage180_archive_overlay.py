"""Prior-form overlay for independently archived complete results, prior to Stage171 bridge.
No overwrites; archive facts and exact source identities only; no inferred SP or finish.
"""
import collections as C, csv, datetime as D, hashlib, json, pathlib as P, re, sqlite3
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
from scripts import stage171_recent_history as M
BASE=M.BASE
SOURCE='APP_ARCHIVED_RACING_API_STAGE180'

def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def name(x):return M.norm(re.sub(r'\s*\([A-Za-z]{2,3}\)\s*$','',str(x or '')))
def ident(h):return tuple(name(h.get(k)) for k in ('horse_id','sire','dam'))
def status(h):
 p=h.get('position');s=M.norm(h.get('status'));t=M.norm(p)
 if s in M.NR or t in M.NR:return ('NR',None) if not str(p or '').isdigit() else ('INVALID',None)
 if s in M.TERMINAL or t in M.TERMINAL:return (M.TERMINAL.get(s) or M.TERMINAL[t],None) if not str(p or '').isdigit() else ('INVALID',None)
 if not isinstance(p,bool) and re.fullmatch(r'[0-9]+(?:\.0+)?',str(p)) and float(p)>0:return ('FINISHED',int(float(p)))
 return ('INVALID',None)
def eligible(r,day):
 if not (r.get('status')=='final' and r.get('date')==day and day<=BASE and str(r.get('race_id','')).startswith('rac_') and r.get('result_classification_source')=='stage71_archive_only_validated' and R.region(r.get('course')) in ('GB','IRELAND')):return False
 if not all(r.get(k) for k in ('course','race_name','type','surface','dist_f')) or M.off(r) is None:return False
 d=I.number(str(r['dist_f']).rstrip('fF')); rows=r.get('runners') or []; nm=[name(h.get('horse')) for h in rows]; ids=[h.get('horse_id') for h in rows]; st=[status(h) for h in rows]; pos=sorted(p for s,p in st if p is not None)
 return bool(d and 0<d<50 and len(rows)>=2 and all(nm) and len(nm)==len(set(nm)) and all(ids) and len(ids)==len(set(ids)) and all(s!='INVALID' for s,p in st) and pos and pos==list(range(1,len(pos)+1)) and all(all(ident(h)) for h in rows if status(h)[0]!='NR'))

def apply(db,ref,target,root=P.Path('data')):
 root=P.Path(root);ledger=root/'stage180-history-overlay';counts=C.Counter(); added=[]; details=[]
 if not ledger.exists():return dict(counts),details
 for p in sorted(ledger.glob('????-??-??.json')):
  day=p.stem
  if not day<=BASE or not day<target:continue
  doc=json.loads(p.read_text());assert doc['date']==day and doc['schema']=='stage180-archive-overlay-v1'
  archive=M.load_day(root,day); byid=C.defaultdict(list)
  for r in archive:byid[str(r.get('race_id') or '')].append(r)
  with sqlite3.connect(db) as c:
   c.execute('PRAGMA foreign_keys=ON'); prior=C.defaultdict(set); race_fields=C.defaultdict(set)
   for rid,co,hn,hid in c.execute('select r.id,r.course,h.horse_name,h.horse_source_id from historical_races r join historical_runs h on h.race_id=r.id where r.race_date=?',(day,)):
    prior['name:'+name(hn)].add(rid)
    if hid:prior['id:'+str(hid)].add(rid)
    race_fields[rid].add(name(hn))
   for e in doc['races']:
    rid0=e['source_race_id'];matches=byid[rid0]; rr=matches[0] if len(matches)==1 else None
    if not rr or digest(rr)!=e['archive_sha256'] or not eligible(rr,day):counts['stale_or_invalid']+=1;details.append({'date':day,'id':rid0,'status':'STALE_OR_INVALID'});continue
    rows=[h for h in rr['runners'] if status(h)[0]!='NR']; names={name(h['horse']) for h in rows}; existing=set().union(*(prior['name:'+n] for n in names),*(prior['id:'+str(h['horse_id'])] for h in rows))
    rid='stage180:'+day+':'+hashlib.sha256(rid0.encode()).hexdigest()[:24]
    if existing:
     same=existing=={rid} and race_fields[rid]==names
     counts['already_present' if same else 'overlap_quarantined']+=1;details.append({'date':day,'id':rid0,'status':'ALREADY_PRESENT' if same else 'OVERLAP_QUARANTINED','existing_ids':sorted(existing)});continue
    # Reject matching source ID even where the source uses a different race identifier format.
    if c.execute('select 1 from historical_races where race_date=? and (id=? or id like ?) limit 1',(day,rid,'%:'+rid0)).fetchone():counts['race_id_collision']+=1;continue
    distance=I.number(str(rr['dist_f']).rstrip('fF'));region=R.region(rr['course'])
    c.execute('INSERT INTO historical_races VALUES(?,?,?,?,?,?,?,?,?)',(rid,day,rr['course'],rr['type'],rr['surface'],distance,rr.get('going'),rr.get('class'),SOURCE))
    for h in rows:
     st,pos=status(h);sp=M.sp_decimal(h.get('sp'))
     c.execute('INSERT INTO historical_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,'id:'+str(h['horse_id']),str(h['horse_id']),h['horse'],I.normname(h['horse']),None,None,pos,st,h.get('trainer'),h.get('jockey'),I.number(h.get('or')),I.weight_lbs(h.get('weight_lbs') or h.get('weight')),sp,I.number(h.get('draw')),I.number(h.get('age')),h.get('sex'),h['sire'],h['dam'],I.number(h.get('rpr')),I.number(h.get('ts')),h.get('comment') or h.get('race_comment'),h.get('overall_beaten')))
     prior['name:'+name(h['horse'])].add(rid);prior['id:'+str(h['horse_id'])].add(rid)
    race_fields[rid]=names;counts['new_races']+=1;counts['new_runners']+=len(rows);details.append({'date':day,'id':rid0,'status':'ADDED','runners':len(rows)})
    added.append(dict(stage2_race_id=rid,date=day,region_scope=region,course=rr['course'],race_type=rr['type'],race_name=rr['race_name'],source_race_id=rid0))
   c.execute('INSERT OR IGNORE INTO data_provenance VALUES(?,?,?,?,?)',(SOURCE,'Previously published Racing API app result archive','Existing app data',D.datetime.now(D.timezone.utc).isoformat(),'Stage71 validated complete fields; no imputed missing values; overlap quarantined'))
   c.commit()
 if added:
  with P.Path(ref).open(encoding='utf-8-sig',newline='') as f:fields=csv.DictReader(f).fieldnames
  assert {'stage2_race_id','date','region_scope','course','race_type','race_name'}<=set(fields)
  with P.Path(ref).open('a',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=fields,extrasaction='ignore').writerows(added)
 print('STAGE180_OLDER_OVERLAY',json.dumps(dict(counts)),flush=True)
 return dict(counts),details
