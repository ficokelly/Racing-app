"""Verified Stage182h additions. Source data remains private; only approved repair metadata and already-archived app facts are committed."""
import collections as C, csv, json, pathlib as P, sqlite3, tempfile
import import_historical_ukie as I
from scripts import stage182c_verified_source as V
from scripts import stage180_archive_overlay as O
LEDGER=P.Path('data/stage182h-approved-history.json')

def apply(db,ref,source,target,ledger=LEDGER):
 doc=json.loads(P.Path(ledger).read_text())
 assert doc['approved_run']==45522254220 and doc['source_version']==132 and len(doc['races'])==12
 entries={e['id']:e for e in doc['races'] if e['date']<target}
 assert len(entries)==sum(e['date']<target for e in doc['races'])
 if not entries:return dict(new_races=0,new_runners=0,already_present=0)
 with tempfile.TemporaryDirectory(prefix='stage182h-') as tmp:
  delta=P.Path(tmp)/'delta.sqlite'
  I.process(source,delta,'2026-10-10','2026-11-20','import')
  values={}
  with sqlite3.connect(delta) as incoming:
   for rid,e in entries.items():
    if e['kind']=='app_archive':
     race,rows=e['race'],e['rows']
     assert race[0]==rid and all(row[0]==rid for row in rows)
    else:
     assert e['kind']=='pinned_source'
     for field,val in e['repairs'].items():
      if field=='race_name':continue
      assert field in ('race_type','surface','distance_f')
      incoming.execute(f"update historical_races set {field}=? where id=? and ({field} is null or {field}='')",(val,rid));assert incoming.execute('select changes()').fetchone()[0]==1
     for n,st,pos in e['outcome_repairs']:
      keys=[k for k,h in incoming.execute('select runner_key,horse_name from historical_runs where race_id=?',(rid,)) if O.name(h)==n];assert len(keys)==1
      incoming.execute('update historical_runs set outcome=?,finish_pos=? where race_id=? and runner_key=?',(st,pos,rid,keys[0]));assert incoming.execute('select changes()').fetchone()[0]==1
     for n,col,val in e['pedigree_repairs']:
      assert col in ('sire','dam')
      keys=[k for k,h in incoming.execute('select runner_key,horse_name from historical_runs where race_id=?',(rid,)) if O.name(h)==n];assert len(keys)==1
      incoming.execute(f"update historical_runs set {col}=? where race_id=? and runner_key=? and ({col} is null or {col}='')",(val,rid,keys[0]));assert incoming.execute('select changes()').fetchone()[0]==1
     race,rows=V.fetch(incoming,rid)
    assert race[1]==e['date'] and len(rows)==e['runners'] and V.signature(race,rows)==e['sha256'],('Approved signature changed',rid)
    values[rid]=(race,rows)
  with P.Path(ref).open(encoding='utf-8-sig',newline='') as f:
   reader=csv.DictReader(f);fields=reader.fieldnames;existing={r['stage2_race_id']:r for r in reader if r['stage2_race_id'] in entries}
  added=[];count=present=0
  with sqlite3.connect(db) as c:
   c.execute('pragma foreign_keys=ON');c.execute('begin immediate');slots=C.defaultdict(set)
   for rid,day,h,hid in c.execute('select r.id,r.race_date,h.horse_name,h.horse_source_id from historical_races r join historical_runs h on r.id=h.race_id where r.race_date between ? and ?',(min(e['date'] for e in entries.values()),max(e['date'] for e in entries.values()))):
    slots[(day,'name:'+O.name(h))].add(rid)
    if hid:slots[(day,'id:'+str(hid))].add(rid)
   for rid,e in sorted(entries.items()):
    if c.execute('select 1 from historical_races where id=?',(rid,)).fetchone():
     assert rid in existing and V.signature(*V.fetch(c,rid))==e['sha256'],('Existing approved race differs',rid);present+=1;continue
    assert rid not in existing,('Orphan reference',rid)
    race,rows=values[rid];day=e['date'];keys=set()
    for row in rows:
     if O.M.norm(row[8]) in O.M.NR:continue
     keys.add((day,'name:'+O.name(row[3])))
     if row[2]:keys.add((day,'id:'+str(row[2])))
    conflicts=set().union(*(slots[k] for k in keys));assert not conflicts,('Historical overlap',rid,sorted(conflicts))
    c.execute('insert into historical_races values(?,?,?,?,?,?,?,?,?)',race)
    c.executemany('insert into historical_runs values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    assert V.signature(*V.fetch(c,rid))==e['sha256']
    for k in keys:slots[k].add(rid)
    added.append(e['reference']);count+=len(rows)
   assert c.execute('pragma quick_check').fetchone()[0]=='ok';c.commit()
  if added:
   with P.Path(ref).open('a',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=fields,extrasaction='ignore').writerows(added)
 result=dict(new_races=len(added),new_runners=count,already_present=present)
 print('STAGE182H_LIVE_ALLOWLIST',json.dumps(result),flush=True);return result
