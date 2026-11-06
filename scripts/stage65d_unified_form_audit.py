#!/usr/bin/env python3
"""Stage65D: non-destructive provenance-preserving recent form overlay and audit.

Does not update production data or frozen D. Stage65C SQLite must be supplied.
"""
import argparse,csv,datetime as dt,hashlib,json,pathlib,re,sqlite3,sys,collections

def canon(s):return re.sub(r'[^a-z0-9]','',str(s or '').casefold())
def load(path):
 try:return json.loads(pathlib.Path(path).read_text())
 except (OSError,ValueError):return None

def main():
 p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--results',default='data/race-results.json');p.add_argument('--out',default='reports/stage65d');p.add_argument('--historical-db',default='');a=p.parse_args()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 if not pathlib.Path(a.archive).exists():sys.exit('Missing Stage65C archive: cannot proceed')
 src=sqlite3.connect('file:'+str(pathlib.Path(a.archive).resolve())+'?mode=ro',uri=True)
 if src.execute('pragma quick_check').fetchone()[0]!='ok':sys.exit('Corrupt source archive')
 tables={r[0] for r in src.execute("select name from sqlite_master where type='table'")}
 required={'races','runners','placings','closing_prices','race_versions','closing_price_versions','api_day_audit'}
 if not required<=tables:sys.exit('Incomplete source schema: '+str(required-tables))
 db=out/'unified_form_overlay.sqlite'; dst=sqlite3.connect(db)
 dst.executescript('''CREATE TABLE IF NOT EXISTS source_races(source TEXT,race_id TEXT,day TEXT,venue TEXT,off_time TEXT,raw_json TEXT,PRIMARY KEY(source,race_id));
 CREATE TABLE IF NOT EXISTS source_runners(source TEXT,race_id TEXT,runner_key TEXT,horse TEXT,position INTEGER,sp TEXT,trainer TEXT,jockey TEXT,weight TEXT,margin TEXT,raw_json TEXT,PRIMARY KEY(source,race_id,runner_key));
 CREATE TABLE IF NOT EXISTS race_links(source_a TEXT,id_a TEXT,source_b TEXT,id_b TEXT,method TEXT,PRIMARY KEY(source_a,id_a,source_b,id_b));
 CREATE TABLE IF NOT EXISTS field_evidence(source TEXT,race_id TEXT,runner_key TEXT,field TEXT,value TEXT,PRIMARY KEY(source,race_id,runner_key,field));''')
 # Preserve source JSON unchanged; original Stage65C remains the authoritative full version archive.
 rows=src.execute('select race_id,gbie_day,venue,start_time,raw_json from races where country in (\'GB\',\'IE\')').fetchall()
 for rid,day,venue,t,raw in rows:dst.execute('insert or replace into source_races values (?,?,?,?,?,?)',('puntersedge',rid,day,venue,t,raw))
 rr=src.execute('select race_id,runner_key,horse,position,official_sp,trainer,jockey,weight,margin,raw_json from runners').fetchall()
 for r in rr:
  dst.execute('insert or replace into source_runners values (?,?,?,?,?,?,?,?,?,?,?)',('puntersedge',)+r)
  for field,value in zip(('horse','position','sp','trainer','jockey','weight','margin'),r[2:9]):
   if value is not None and str(value).strip():dst.execute('insert or replace into field_evidence values (?,?,?,?,?)',('puntersedge',r[0],r[1],field,str(value)))
 # Cross-reference repository results using exact local date + normalised course + 15 minute off-time.
 repo=load(a.results); repo_races=(repo.get('races',[]) if isinstance(repo,dict) else [])
 from zoneinfo import ZoneInfo
 local=ZoneInfo('Europe/Dublin')
 def minute(s):
  try:
   d=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
   if d.tzinfo is None:return None
   d=d.astimezone(local);return d.hour*60+d.minute
  except (TypeError,ValueError):return None
 candidates=collections.defaultdict(list)
 for rid,day,venue,t,raw in rows:
  m=minute(t)
  if day and m is not None:candidates[(day,canon(venue))].append((rid,m))
 matched=[];unmatched=[];ambiguous=[];runner_conflicts=[];used=set()
 for r in repo_races:
  if not isinstance(r,dict) or r.get('status')!='final':continue
  day=str(r.get('date') or '')[:10];course=canon(r.get('course'));tm=minute(r.get('off_dt') or r.get('race_time'))
  if not(day and course and tm is not None):continue
  options=[(rid,m) for rid,m in candidates.get((day,course),[]) if abs(m-tm)<=15 and rid not in used]
  if len(options)!=1:
   (ambiguous if len(options)>1 else unmatched).append({'day':day,'course':r.get('course'),'time':r.get('off_dt'),'matches':len(options)})
   continue
  rid=options[0][0];used.add(rid);matched.append(rid)
  # Exact matching to avoid confusing similarly named horses in different races.
  repo_horses={canon(h.get('horse')) for h in r.get('runners',[]) if isinstance(h,dict) and h.get('horse')}
  src_horses={canon(h[0]) for h in src.execute('select horse from runners where race_id=?',(rid,)) if h[0]}
  if repo_horses and src_horses and repo_horses!=src_horses:
   runner_conflicts.append({'race_id':rid,'repo_only':sorted(repo_horses-src_horses),'provider_only':sorted(src_horses-repo_horses)})
   continue
  key=str(r.get('id') or r.get('race_id') or f'{day}:{course}:{tm}')
  dst.execute('insert or replace into race_links values (?,?,?,?,?)',('puntersedge',rid,'repo_results',key,'day_course_time_15min_and_runner_check'))
 # Inspect historical DB schema only: never assume it exists or has identical IDs.
 history={'status':'NOT_AVAILABLE'}
 if a.historical_db and pathlib.Path(a.historical_db).is_file():
  try:
   h=sqlite3.connect('file:'+str(pathlib.Path(a.historical_db).resolve())+'?mode=ro',uri=True)
   history={'status':'AVAILABLE_SCHEMA_ONLY','tables':{name:[x[1] for x in h.execute('pragma table_info("'+name.replace('"','')+'")')] for (name,) in h.execute("select name from sqlite_master where type='table'") if name in ('historical_runs','historical_races')}};h.close()
  except sqlite3.Error as exc:history={'status':'ERROR','message':str(exc)}
 dst.commit();dst.close()
 fields=('official_sp','trainer','jockey','weight','margin')
 completeness={field:{'present':sum(bool(r[idx] is not None and str(r[idx]).strip()) for r in rr),'missing':sum(not bool(r[idx] is not None and str(r[idx]).strip()) for r in rr)} for idx,field in [(4,'official_sp'),(5,'trainer'),(6,'jockey'),(7,'weight'),(8,'margin')]}
 report={'status':'AUDIT_ONLY_NO_LIVE_CHANGES','source_archive':str(a.archive),'source_races':len(rows),'source_runners':len(rr),'source_closing_price_rows':src.execute('select count(*) from closing_prices').fetchone()[0], 'source_race_versions':src.execute('select count(*) from race_versions').fetchone()[0],'source_price_versions':src.execute('select count(*) from closing_price_versions').fetchone()[0], 'source_api_days':src.execute("select count(*) from api_day_audit where endpoint='results' and status='FETCHED'").fetchone()[0], 'completeness':completeness,'race_class_present':sum(bool(json.loads(r[4]).get('race_class')) for r in rows),'conditions_present':sum(bool(json.loads(r[4]).get('conditions')) for r in rows),'repo_results_available':bool(repo),'repo_matched':len(matched),'repo_unmatched':unmatched,'repo_ambiguous':ambiguous,'repo_runner_conflicts':runner_conflicts,'historical_database':history,'unresolved':['Missing fields cannot be recovered from PuntersEdge results alone','No direct merging with 1988-2026 archive until stable race and horse IDs are validated','No live Model D change until chronological pre-race feature reconstruction is tested','Daily source archive must be backed up outside expiring GitHub artifacts']}
 (out/'stage65d_audit.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 with (out/'stage65d_missing_fields.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['field','present','missing','coverage_pct'])
  for k,v in completeness.items():w.writerow([k,v['present'],v['missing'],round(100*v['present']/len(rr),2) if rr else 0])
 src.close();print(json.dumps({k:v for k,v in report.items() if k not in ('repo_unmatched','repo_ambiguous','repo_runner_conflicts')},indent=2))
 if not rows:sys.exit('No GB/IE races found; refusing to call this successful')
if __name__=='__main__':main()
