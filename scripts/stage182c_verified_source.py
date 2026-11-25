"""Stage182c: allowlisted, checksum-verified source-v132 historical additions.
Raw licensed source data stays private; only source IDs, hashes and metadata repairs are stored.
"""
import collections as C, csv, hashlib, json, pathlib as P, sqlite3, tempfile
import import_historical_ukie as I
from scripts import stage180_archive_overlay as O

LEDGER=P.Path('data/stage182c-approved-history.json')
def signature(race,runners):
 return hashlib.sha256(json.dumps([list(race),[list(x) for x in runners]],ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def fetch(c,rid):
 race=c.execute('select * from historical_races where id=?',(rid,)).fetchone()
 rows=c.execute('select * from historical_runs where race_id=? order by runner_key',(rid,)).fetchall()
 if race is None or not rows: raise RuntimeError('Approved race missing: '+rid)
 return race,rows

def apply(db,ref,source,target,ledger=LEDGER):
 doc=json.loads(P.Path(ledger).read_text());assert doc['source_version']==132 and doc['approved_run']==45518317420 and len(doc['races'])==115
 entries={e['id']:e for e in doc['races'] if e['date']<target};assert len(entries)==sum(e['date']<target for e in doc['races'])
 if not entries:return {'new_races':0,'new_runners':0,'already_present':0}
 with tempfile.TemporaryDirectory(prefix='stage182c-') as tmp:
  delta=P.Path(tmp)/'delta.sqlite';first=min(e['date'] for e in entries.values());last=max(e['date'] for e in entries.values());I.process(source,delta,first,last,'import')
  with sqlite3.connect(delta) as incoming:
   for rid,e in entries.items():
    for field,value in e['repairs'].items():
     if field=='race_name':continue
     assert field in ('race_type','surface','distance_f')
     incoming.execute(f"update historical_races set {field}=? where id=? and ({field} is null or {field}='')",(value,rid));assert incoming.execute('select changes()').fetchone()[0]==1,(rid,field)
    race,rows=fetch(incoming,rid)
    assert signature(race,rows)==e['sha256'] and len(rows)==e['runners'],('Approved source changed',rid)
   incoming.commit()
  with P.Path(ref).open(encoding='utf-8-sig',newline='') as f:
   r=csv.DictReader(f);fields=r.fieldnames;existing_ref={x['stage2_race_id']:x for x in r if x.get('stage2_race_id') in entries}
  new=[];count=0;present=0
  with sqlite3.connect(db) as c:
   c.execute('pragma foreign_keys=ON');c.execute('attach database ? as incoming',(str(delta),));c.execute('begin immediate')
   slots=C.defaultdict(set)
   for rid,day,horse,hid in c.execute('select r.id,r.race_date,h.horse_name,h.horse_source_id from historical_races r join historical_runs h on r.id=h.race_id where r.race_date between ? and ?',(first,last)):
    slots[(day,'name:'+O.name(horse))].add(rid)
    if hid:slots[(day,'id:'+str(hid))].add(rid)
   for rid,e in sorted(entries.items()):
    if c.execute('select 1 from historical_races where id=?',(rid,)).fetchone():
     assert signature(*fetch(c,rid))==e['sha256'] and rid in existing_ref,('Existing approved race differs',rid);present+=1;continue
    assert rid not in existing_ref,('Orphan reference',rid)
    day=e['date'];rr=[(h,hid) for h,hid,o in c.execute('select horse_name,horse_source_id,outcome from incoming.historical_runs where race_id=?',(rid,)) if O.M.norm(o) not in O.M.NR]
    conflicts=set().union(*(slots[(day,'name:'+O.name(h))] for h,hid in rr),*(slots[(day,'id:'+str(hid))] for h,hid in rr if hid))
    assert not conflicts,('Overlap: refuse duplicate',rid,sorted(conflicts))
    c.execute('insert into historical_races select * from incoming.historical_races where id=?',(rid,));assert c.execute('select changes()').fetchone()[0]==1
    c.execute('insert into historical_runs select * from incoming.historical_runs where race_id=?',(rid,));assert c.execute('select changes()').fetchone()[0]==e['runners'];count+=e['runners']
    assert signature(*fetch(c,rid))==e['sha256'];new.append(e['reference'])
    for h,hid in rr:
     slots[(day,'name:'+O.name(h))].add(rid)
     if hid:slots[(day,'id:'+str(hid))].add(rid)
   assert c.execute('pragma quick_check').fetchone()[0]=='ok';c.commit()
  if new:
   with P.Path(ref).open('a',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=fields,extrasaction='ignore').writerows(new)
 result={'new_races':len(new),'new_runners':count,'already_present':present};print('STAGE182C_LIVE_ALLOWLIST',json.dumps(result),flush=True);return result
