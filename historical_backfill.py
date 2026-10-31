#!/usr/bin/env python3
"""Racing Hot Pots historical race-results backfill, offline, Python 3.11+ stdlib.

Run once against a repository, then import licensed historical flat CSV exports.
DOES NOT modify existing app files or access the internet.

Usage:
 python historical_backfill.py ingest-repo --repo . --db out/horse_history.sqlite
 python historical_backfill.py inspect-csv --input '/path/to/historical.csv'
 python historical_backfill.py ingest-csv --input history.csv --db out/horse_history.sqlite
 python historical_backfill.py report --db out/horse_history.sqlite --date 2026-10-31
"""
import argparse, csv, datetime as dt, io, json, re, sqlite3, sys, unicodedata, zipfile
from collections import Counter
from pathlib import Path

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS races (
 race_key TEXT PRIMARY KEY, source TEXT NOT NULL, source_race_id TEXT, race_date TEXT NOT NULL,
 course TEXT NOT NULL, off_time TEXT, race_type TEXT, surface TEXT, going TEXT,
 distance_f REAL, race_class TEXT, region TEXT, field_size INTEGER
);
CREATE TABLE IF NOT EXISTS performances (
 race_key TEXT NOT NULL, horse_key TEXT NOT NULL, horse_id TEXT, horse_name TEXT NOT NULL,
 birth_country TEXT, trainer_id TEXT, trainer TEXT, jockey_id TEXT, jockey TEXT,
 finish_pos INTEGER, outcome TEXT, draw INTEGER, weight_lbs REAL, official_rating REAL,
 sp_decimal REAL, source TEXT NOT NULL, PRIMARY KEY (race_key, horse_key),
 FOREIGN KEY (race_key) REFERENCES races(race_key)
);
CREATE INDEX IF NOT EXISTS idx_perf_horse_id ON performances(horse_id);
CREATE INDEX IF NOT EXISTS idx_perf_name ON performances(horse_name);
CREATE INDEX IF NOT EXISTS idx_perf_racekey ON performances(race_key);
CREATE INDEX IF NOT EXISTS idx_race_date ON races(race_date);
CREATE TABLE IF NOT EXISTS provenance(source TEXT PRIMARY KEY, licensing_note TEXT, imported_at TEXT, records INTEGER);
'''
ALIASES = {
 'date': ['date','race_date','racedate','meeting_date','event_date'],
 'horse': ['horse','horse_name','horsename','name','runner','runner_name'],
 'course': ['course','racecourse','track','venue','course_name','track_name'],
 'position': ['position','pos','finish','finishing_position','finishingposition','place','placing'],
 'race_id': ['race_id','raceid','event_id','race_key','race_number'],
 'off': ['off','off_time','race_time','time'],
 'type': ['race_type','type','discipline'],
 'surface': ['surface','track_surface'],
 'going': ['going','ground'],
 'dist_f': ['dist_f','distance_f','distance_furlongs','furlongs','distance'],
 'class': ['class','race_class','grade'],
 'region': ['region','country','nation'],
 'horse_id': ['horse_id','horseid','runner_id'],
 'trainer': ['trainer','trainer_name'], 'trainer_id':['trainer_id'],
 'jockey': ['jockey','jockey_name','rider'], 'jockey_id':['jockey_id'],
 'weight_lbs': ['weight_lbs','lbs','weight'], 'or': ['or','ofr','official_rating','rating'],
 'sp': ['sp','starting_price','sp_fractional','sp_decimal','odds'],
 'draw': ['draw','stall','barrier'], 'status':['status','runner_status'],
}

def norm(s):
 s = unicodedata.normalize('NFKD', str(s or ''))
 s = ''.join(ch for ch in s if not unicodedata.combining(ch))
 return re.sub(r'[^A-Z0-9]', '', s.upper())

def name_country(raw):
 s = str(raw or '').strip()
 m = re.search(r'\s*\(([A-Za-z]{2,3})\)\s*$', s)
 country = m.group(1).upper() if m else None
 return (s[:m.start()].strip() if m else s), country

def value_int(v):
 try: return int(str(v).strip())
 except (ValueError,TypeError):return None

def value_float(v):
 try: return float(str(v).replace(',',''))
 except (ValueError,TypeError):return None

def distance_f(v):
 s=str(v or '').lower().strip()
 if not s: return None
 if s.endswith('f'): return value_float(s[:-1])
 m=re.fullmatch(r'(\d+)m(?:\s*(\d+(?:\.\d+)?)f)?',s)
 if m:return int(m.group(1))*8+float(m.group(2) or 0)
 return value_float(s)

def sp_dec(v):
 s=str(v or '').strip().lower()
 if not s or s in ('-', 'n/a', 'nan') :return None
 if s in ('evens','even','evs'):return 2.0
 if '/' in s:
  try:
   a,b=s.split('/',1);return 1+float(a)/float(b)
  except (ValueError,ZeroDivisionError):return None
 f=value_float(s)
 return f if f and f>=1 else None

def get_date(v):
 s=str(v or '').strip()
 if re.fullmatch(r'\d{4}-\d{2}-\d{2}',s):return s
 for fmt in ('%d/%m/%Y','%d-%m-%Y','%Y/%m/%d','%d %b %Y'):
  try:return dt.datetime.strptime(s,fmt).date().isoformat()
  except ValueError:pass
 return s[:10] if re.match(r'^\d{4}-\d{2}-\d{2}[T ]',s) else None

def make_db(path):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(p)
 c.executescript(SCHEMA)
 return c

def race_key(r,source):
 rid=r.get('race_id')
 date=get_date(r.get('date'));course=norm(r.get('course'))
 if rid: return f'{source}:{date}:{course}:id:{norm(rid)}'
 off=norm(r.get('off_dt') or r.get('off') or r.get('off_time'))
 return f'{source}:{date}:{course}:off:{off}'

def insert_race(c,r,source):
 k=race_key(r,source)
 d=get_date(r.get('date'))
 course=(r.get('course') or '').strip()
 if not (d and course): return None
 c.execute('''INSERT INTO races(race_key,source,source_race_id,race_date,course,off_time,race_type,surface,going,distance_f,race_class,region,field_size)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(race_key) DO UPDATE SET
 race_type=COALESCE(excluded.race_type,races.race_type), surface=COALESCE(excluded.surface,races.surface),
 going=COALESCE(excluded.going,races.going), distance_f=COALESCE(excluded.distance_f,races.distance_f),
 race_class=COALESCE(excluded.race_class,races.race_class), field_size=COALESCE(excluded.field_size,races.field_size)''',
 (k,source,r.get('race_id'),d,course,str(r.get('off_dt') or r.get('off') or r.get('off_time') or ''),r.get('type'),r.get('surface'),r.get('going'),distance_f(r.get('dist_f') or r.get('distance_f')),r.get('class'),r.get('region'),value_int(r.get('field_size') or len(r.get('runners',[])))))
 return k

def insert_runner(c,k,r,source):
 raw=r.get('horse') or r.get('name')
 name,country=name_country(raw)
 if not name:return False
 hid=str(r.get('horse_id') or '').strip() or None
 # When no trusted stable horse ID exists, preserve country to lower collision risk;
 # never attach a historical name-only record to a stable horse ID automatically.
 hkey=f'id:{hid}' if hid else f'name:{norm(name)}:{country or "UNKNOWN"}'
 pos=value_int(r.get('position'))
 status=str(r.get('status') or '').lower().strip()
 if status in ('nr','non-runner','non_runner','withdrawn') or str(r.get('position')).upper() in ('NR','WD'):
  outcome='NR';pos=None
 elif pos and pos>0:outcome='RAN'
 elif status in ('ran','finished'):outcome='RAN'
 else:outcome='OTHER'
 c.execute('''INSERT INTO performances(race_key,horse_key,horse_id,horse_name,birth_country,trainer_id,trainer,jockey_id,jockey,finish_pos,outcome,draw,weight_lbs,official_rating,sp_decimal,source)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(race_key,horse_key) DO UPDATE SET
 finish_pos=excluded.finish_pos, outcome=excluded.outcome,
 sp_decimal=COALESCE(excluded.sp_decimal,performances.sp_decimal),
 official_rating=COALESCE(excluded.official_rating,performances.official_rating)''',
 (k,hkey,hid,name,country,r.get('trainer_id'),r.get('trainer'),r.get('jockey_id'),r.get('jockey'),pos,outcome,value_int(r.get('draw')),value_float(r.get('weight_lbs')),value_float(r.get('or') or r.get('ofr')),sp_dec(r.get('sp')),source))
 return True

def ingest_repo(c,repo):
 files=sorted((Path(repo)/'data'/'racing-results').glob('*.json'))
 if not files:raise RuntimeError('No data/racing-results/*.json files found')
 total=0
 for file in files:
  doc=json.loads(file.read_text(encoding='utf-8'))
  for r in doc.get('results',[]):
   k=insert_race(c,r,'the-racing-api')
   if not k:continue
   for x in r.get('runners',[]): total+=bool(insert_runner(c,k,x,'the-racing-api'))
  c.commit()
 c.execute('INSERT OR REPLACE INTO provenance VALUES (?,?,?,?)',('the-racing-api','Existing Racing Hot Pots daily results; preserve source API terms.',dt.datetime.now(dt.timezone.utc).isoformat(),total))
 c.commit()
 return {'files':len(files),'runner_rows_processed':total,'source':'the-racing-api'}

def resolve(headers):
 lookup={norm(h):h for h in headers or []}
 return {field: next((lookup[norm(a)] for a in names if norm(a) in lookup),None) for field,names in ALIASES.items()}

def iter_csv_rows(path,member=None):
 p=Path(path)
 if p.suffix.lower()=='.zip':
  with zipfile.ZipFile(p) as z:
   names=[x for x in z.namelist() if x.lower().endswith('.csv') and not x.startswith('__MACOSX/')]
   if member:
    if member not in names:raise ValueError(f'CSV member not found. Candidates: {names[:20]}')
    names=[member]
   if len(names)!=1:raise ValueError(f'ZIP contains {len(names)} CSVs. Choose one with --member. First 15: {names[:15]}')
   with z.open(names[0]) as raw:
    with io.TextIOWrapper(raw,encoding='utf-8-sig',errors='replace',newline='') as txt:
     yield from csv.DictReader(txt)
 else:
  with p.open('r',encoding='utf-8-sig',errors='replace',newline='') as f:
   yield from csv.DictReader(f)

def inspect_csv(path,member=None):
 it=iter_csv_rows(path,member)
 try: first=next(it)
 except StopIteration:raise ValueError('CSV has no rows')
 mapping=resolve(first.keys())
 print(json.dumps({'columns':list(first.keys()),'detected':mapping,'first_row_sample': {k:first.get(v) for k,v in mapping.items() if v and k not in ('horse', 'trainer', 'jockey')}},indent=2))
 if not all(mapping.get(x) for x in ('date','horse','course','position')):
  print('Missing required mapping: date, horse, course, position. Adjust aliases before import.',file=sys.stderr)
 return mapping

def ingest_csv(c,path,member=None,license_note='Unverified - review original dataset terms before reuse',max_rows=0):
 it=iter_csv_rows(path,member)
 try:first=next(it)
 except StopIteration:raise RuntimeError('Historical CSV is empty')
 mapping=resolve(first.keys())
 missing=[x for x in ('date','horse','course','position') if not mapping.get(x)]
 if missing:raise ValueError(f'Missing required fields {missing}. Detected headers: {list(first)}')
 import itertools
 count=0; skipped=0
 for x in itertools.chain([first],it):
  def get(key):return x.get(mapping[key]) if mapping.get(key) else None
  date=get_date(get('date'))
  if not date or not get('course') or not get('horse'):
   skipped+=1;continue
  r={'date':date,'course':str(get('course')),'race_id':get('race_id'),'off':get('off'), 'type':get('type'), 'surface':get('surface'),'going':get('going'),'dist_f':get('dist_f'),'class':get('class'),'region':get('region')}
  # If original lacks stable race ID, unique date/course/off cannot separate races
  # when off is missing. Reject rather than collapsing an entire meeting.
  if not r['race_id'] and not r['off']:
   skipped+=1;continue
  k=insert_race(c,r,'historical-csv')
  if not k:skipped+=1;continue
  runner={'horse':get('horse'),'horse_id':get('horse_id'),'position':get('position'),'trainer':get('trainer'),'trainer_id':get('trainer_id'),'jockey':get('jockey'),'jockey_id':get('jockey_id'),'weight_lbs':get('weight_lbs'),'or':get('or'),'sp':get('sp'),'draw':get('draw'),'status':get('status')}
  if not insert_runner(c,k,runner,'historical-csv'):skipped+=1;continue
  count+=1
  if count%10000==0:
   c.commit();print('processed',count,file=sys.stderr)
  if max_rows and count>=max_rows:break
 c.execute('INSERT OR REPLACE INTO provenance VALUES (?,?,?,?)',('historical-csv',license_note,dt.datetime.now(dt.timezone.utc).isoformat(),count));c.commit()
 return {'processed':count,'skipped':skipped,'warning':'Historical IDs/names are not automatically merged with Racing API horse IDs; review before linking.'}

def report(c,date):
 total=c.execute('SELECT COUNT(*) FROM performances').fetchone()[0]
 rs=c.execute('SELECT COUNT(*),MIN(race_date),MAX(race_date) FROM races').fetchone()
 h=c.execute('SELECT COUNT(DISTINCT horse_id) FROM performances WHERE horse_id IS NOT NULL').fetchone()[0]
 sources=c.execute('SELECT source,COUNT(*) FROM performances GROUP BY source').fetchall()
 print(json.dumps({'races':rs[0],'runners':total,'from':rs[1],'to':rs[2],'horses_with_stable_id':h,'source_rows':dict(sources)},indent=2))
 if date:
  # strict as-of: no race on or after date can contribute historical features
  rows=c.execute('''SELECT p.horse_name,p.horse_id,p.horse_key,COUNT(*) AS starts,
  SUM(CASE WHEN p.finish_pos=1 THEN 1 ELSE 0 END) AS wins
  FROM performances p JOIN races r USING(race_key)
  WHERE r.race_date < ? AND p.outcome='RAN' GROUP BY p.horse_key
  ORDER BY starts DESC LIMIT 10''',(date,)).fetchall()
  print(json.dumps({'as_of_exclusive':date,'top_ten_by_starts':[{'horse':x[0],'horse_id':x[1],'horse_key':x[2],'prior_starts':x[3],'prior_wins':x[4]} for x in rows]},indent=2))

def horse_history(c, horse_id, asof=None, limit=30):
    if not horse_id:
        raise ValueError('Stable horse ID is required to avoid mistaken name matches')
    sql = '''SELECT r.race_date,r.course,r.distance_f,r.surface,r.going,r.race_type,
     p.horse_name,p.finish_pos,p.outcome,p.official_rating,p.sp_decimal
     FROM performances p JOIN races r USING(race_key)
     WHERE p.horse_id=?'''
    params=[horse_id]
    if asof:
        sql+=' AND r.race_date < ?';params.append(asof)
    sql+=' ORDER BY r.race_date DESC LIMIT ?';params.append(limit)
    rows=c.execute(sql,params).fetchall()
    return {'horse_id':horse_id,'as_of_exclusive':asof,'returned_runs':len(rows),
    'history':[dict(zip(['date','course','furlongs','surface','going','race_type','horse','finish','outcome','official_rating','sp_decimal'],row)) for row in rows]}

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 sp=ap.add_subparsers(dest='cmd',required=True)
 s=sp.add_parser('ingest-repo');s.add_argument('--repo',default='.');s.add_argument('--db',default='out/horse_history.sqlite')
 s=sp.add_parser('inspect-csv');s.add_argument('--input',required=True);s.add_argument('--member')
 s=sp.add_parser('ingest-csv');s.add_argument('--input',required=True);s.add_argument('--member');s.add_argument('--db',default='out/horse_history.sqlite');s.add_argument('--license-note',default='Source terms must be checked');s.add_argument('--max-rows',type=int,default=0)
 s=sp.add_parser('report');s.add_argument('--db',default='out/horse_history.sqlite');s.add_argument('--date')
 s=sp.add_parser('horse');s.add_argument('--db',default='out/horse_history.sqlite');s.add_argument('--horse-id',required=True);s.add_argument('--as-of');s.add_argument('--limit',type=int,default=30)
 a=ap.parse_args()
 if a.cmd=='inspect-csv':inspect_csv(a.input,a.member);return
 c=make_db(a.db)
 try:
  if a.cmd=='ingest-repo':print(json.dumps(ingest_repo(c,a.repo),indent=2))
  elif a.cmd=='ingest-csv':print(json.dumps(ingest_csv(c,a.input,a.member,a.license_note,a.max_rows),indent=2))
  elif a.cmd=='report':report(c,a.date)
  elif a.cmd=='horse':print(json.dumps(horse_history(c,a.horse_id,a.as_of,a.limit),indent=2))
 finally:c.close()

if __name__=='__main__':main()
