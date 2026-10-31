#!/usr/bin/env python3
"""Stage 2: validate and stage UK/Irish runner-level racing history.

Does NOT modify Racing Hot Pots' current database or app. Supports a CSV,
folder of CSVs, or ZIP containing CSV files. Python 3.11+, stdlib only.
"""
import argparse, csv, datetime as dt, io, json, os, re, sqlite3, sys, unicodedata, zipfile
from collections import Counter
from pathlib import Path

SOURCE = 'kaggle-deltaromeo-ukie'
ALIASES = {
 'race_date': ['race_date','racedate','date','race_day','event_date','meeting_date','race_datetime'],
 'course': ['course','racecourse','venue','track','race_track','location','course_name'],
 'horse': ['horse_name','horse','name','runner','runner_name','horse_name_full'],
 'finish': ['finish_pos','finish_position','finishing_position','position','pos','placing','place','finish','result','finpos'],
 'race_id': ['race_id','raceid','race_key','event_id','eventid','race_code','race_ref'],
 'race_no': ['race_number','race_no','race_num','raceno','number','race'],
 'off_time': ['off_time','race_time','time','scheduled_time','post_time','off'],
 'horse_id': ['horse_id','horseid','runner_id','runnerid','horse_code','horse_ref'],
 'country': ['horse_country','horse_origin','birth_country','horse_nationality','origin'],
 'foaled': ['horse_dob','dob','foaled','birth_year','year_of_birth','foaling_year'],
 'race_type': ['race_type','racetype','discipline','type','code'],
 'surface': ['surface','surface_type','track_type'],
 'distance': ['distance_f','distance_furlongs','race_distance_f','distance','race_distance','distance_m','distance_yards'],
 'going': ['going','ground','track_condition','condition'],
 'race_class': ['class','race_class','grade','race_grade'],
 'trainer': ['trainer','trainer_name'], 'jockey': ['jockey','jockey_name','rider'],
 'rating': ['official_rating','or','rating','handicap_rating'],
 'weight': ['weight_lbs','weight','carried_weight','weight_carried'],
 'sp': ['sp_decimal','starting_price','sp','win_odds','odds'],
 'draw': ['draw','stall','barrier'],
 'age': ['horse_age','age'], 'sex': ['horse_sex','sex','gender'],
 'sire': ['sire','sire_name','father'], 'dam': ['dam','dam_name','mother']
}


def normhead(s):return re.sub(r'[^a-z0-9]+','_',str(s).casefold()).strip('_')
def normname(s):
 s=unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode().upper()
 return re.sub(r'[^A-Z0-9]','',re.sub(r'\s*\((?:IRE|GB|FR|USA|USA|GER|AUS|NZ)\)\s*$','',s))

def mapping(header):
 h={normhead(x):x for x in header}
 return {key:next((h[a] for a in options if a in h),None) for key,options in ALIASES.items()}

def dateparse(s):
 s=str(s or '').strip()
 if not s:return None
 s=s.split('T')[0].split(' ')[0]
 for fmt in ['%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%Y/%m/%d','%d.%m.%Y']:
  try:return dt.datetime.strptime(s,fmt).date().isoformat()
  except ValueError:pass
 return None

def number(s):
 try:return float(str(s).strip().replace(',',''))
 except (ValueError,TypeError):return None

def intpos(s):
 v=str(s or '').strip().upper().replace('ST','')
 if v in ('','NR','NONRUNNER','NON-RUNNER','F','PU','UR','BD','RO','DSQ','DQ','SU','RR','REF','P','VOID','DNF'):return (None,v or 'UNKNOWN')
 m=re.match(r'^(\d+)(?:ST|ND|RD|TH)?$',str(s or '').strip(),re.I)
 return (int(m.group(1)),'FINISHED') if m else (None,v[:30] or 'UNKNOWN')

def odds(s):
 s=str(s or '').strip().upper()
 if s in ('EVS','EVENS','EVEN','1/1'):return 2.0
 if '/' in s:
  try:
   a,b=s.split('/',1);return 1+float(a)/float(b)
  except (ValueError,ZeroDivisionError):return None
 v=number(s);return v if v and v>=1.01 else None

def distance_f(s,column):
 if s is None:return None
 s=str(s).strip().lower()
 if not s:return None
 if 'm' in column.lower() and column.lower().endswith('_m'):
  v=number(s);return round(v/201.168,2) if v else None
 if 'yard' in column.lower():
  v=number(s);return round(v/220,2) if v else None
 m=re.search(r'(\d+(?:\.\d+)?)\s*m(?:iles?)?',s)
 f=re.search(r'(\d+(?:\.\d+)?)\s*f',s)
 if m or f:return round((float(m.group(1))*8 if m else 0)+(float(f.group(1)) if f else 0),2)
 v=number(s)
 if v is None:return None
 if v>500:return round(v/201.168,2)
 return v if v<50 else None

def source_iter(p):
 p=Path(p)
 if p.is_dir():
  for q in sorted(p.rglob('*')):
   if q.is_file() and q.suffix.lower()=='.csv':
    yield str(q),lambda q=q:open(q,'r',encoding='utf-8-sig',errors='replace',newline='')
   elif q.is_file() and q.suffix.lower()=='.zip':
    yield from source_iter(q)
 elif p.suffix.lower()=='.zip':
  with zipfile.ZipFile(p) as z:
   for info in z.infolist():
    if info.filename.lower().endswith('.csv') and not info.is_dir():
     yield info.filename,lambda z=z,n=info.filename: io.TextIOWrapper(z.open(n),encoding='utf-8-sig',errors='replace',newline='')
 elif p.is_file():yield p.name,lambda:open(p,'r',encoding='utf-8-sig',errors='replace',newline='')
 else:raise FileNotFoundError(str(p))

def create_db(c):
 c.executescript('''
 CREATE TABLE IF NOT EXISTS historical_races(
   id TEXT PRIMARY KEY, race_date TEXT NOT NULL, course TEXT NOT NULL, race_type TEXT,
   surface TEXT, distance_f REAL, going TEXT, race_class TEXT, source TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS historical_runs(
   race_id TEXT NOT NULL, runner_key TEXT NOT NULL, horse_source_id TEXT,
   horse_name TEXT NOT NULL, horse_name_norm TEXT NOT NULL, horse_country TEXT,
   foaled TEXT, finish_pos INTEGER, outcome TEXT, trainer TEXT, jockey TEXT,
   official_rating REAL, weight_lbs REAL, sp_decimal REAL, draw INTEGER,
   age INTEGER, sex TEXT, sire TEXT, dam TEXT,
   PRIMARY KEY (race_id,runner_key), FOREIGN KEY(race_id) REFERENCES historical_races(id));
 CREATE TABLE IF NOT EXISTS data_provenance(source TEXT PRIMARY KEY,source_url TEXT,license_claim TEXT,download_time TEXT, caveat TEXT);
 CREATE INDEX IF NOT EXISTS hist_name_idx ON historical_runs(horse_name_norm,horse_country);
 CREATE INDEX IF NOT EXISTS hist_id_idx ON historical_runs(horse_source_id);
 CREATE INDEX IF NOT EXISTS hist_date_idx ON historical_races(race_date);
 ''')

def value(row,m,k):return str(row.get(m.get(k), '') or '').strip() if m.get(k) else ''

def process(src, destination, min_date, max_date, mode, current_db=None, max_rows=None):
 summary={'source':SOURCE,'mode':mode,'files':[],'rows_seen':0,'eligible':0,'inserted':0,'duplicates':0,'skipped':Counter(),'date_min':None,'date_max':None}
 conn=None
 if mode=='import':
  conn=sqlite3.connect(destination);create_db(conn)
  conn.execute('INSERT OR IGNORE INTO data_provenance VALUES (?,?,?,?,?)', (SOURCE,'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025','CDLA-Sharing-1.0 per third-party repository; VERIFY ON KAGGLE','', 'Archive is independently sourced; identity matches not confirmed'))
 for filename,opener in source_iter(src):
  fi={'file':filename,'columns':[],'mapped':{},'recognized':False,'first_valid_row':None}
  summary['files'].append(fi)
  with opener() as f:
   sample=f.read(8192);f.seek(0) if hasattr(f,'seek') and f.seekable() else None
   try:dialect=csv.Sniffer().sniff(sample,delimiters=',;\t|')
   except csv.Error:dialect=csv.excel
   reader=csv.DictReader(f,dialect=dialect)
   if not reader.fieldnames:continue
   m=mapping(reader.fieldnames)
   fi['columns']=reader.fieldnames;fi['mapped']={k:v for k,v in m.items() if v}
   if not all(m.get(x) for x in ('race_date','course','horse','finish')):
    fi['reason']='Missing required date/course/horse/finish fields';continue
   if not any(m.get(x) for x in ('race_id','race_no','off_time')):
    fi['reason']='Missing reliable race identity (race_id, race_no or time)';continue
   fi['recognized']=True
   for row in reader:
    summary['rows_seen']+=1
    if max_rows and summary['rows_seen']>max_rows:break
    date=dateparse(value(row,m,'race_date'))
    if not date:summary['skipped']['bad_date']+=1;continue
    if date < min_date or date > max_date:summary['skipped']['outside_date_range']+=1;continue
    horse=value(row,m,'horse');course=value(row,m,'course')
    if not horse or not course:summary['skipped']['blank_identity']+=1;continue
    rid=value(row,m,'race_id') or (value(row,m,'race_no') and 'R'+value(row,m,'race_no')) or ('T'+value(row,m,'off_time'))
    if not rid:summary['skipped']['race_identity_missing']+=1;continue
    horseid=value(row,m,'horse_id')
    runnerkey='id:'+horseid if horseid else 'name:'+normname(horse)
    raceid=SOURCE+':'+date+':'+normhead(course)+':'+re.sub('[^a-zA-Z0-9_-]','',rid)[:90]
    pos,outcome=intpos(value(row,m,'finish'))
    if fi['first_valid_row'] is None:fi['first_valid_row']={'date':date,'course':course,'horse':horse,'finish':value(row,m,'finish'),'race_identifier':rid}
    summary['eligible']+=1
    if summary['date_min'] is None or date<summary['date_min']:summary['date_min']=date
    if summary['date_max'] is None or date>summary['date_max']:summary['date_max']=date
    if mode!='import':continue
    conn.execute('INSERT OR IGNORE INTO historical_races VALUES (?,?,?,?,?,?,?,?,?)',(
      raceid,date,course,value(row,m,'race_type') or None,value(row,m,'surface') or None,
      distance_f(value(row,m,'distance'),m['distance'] or '') if m.get('distance') else None,
      value(row,m,'going') or None,value(row,m,'race_class') or None,SOURCE))
    country=value(row,m,'country').upper() or None
    weight=number(value(row,m,'weight'))
    rowrun=(raceid,runnerkey,horseid or None,horse,normname(horse),country,
      value(row,m,'foaled') or None,pos,outcome,value(row,m,'trainer') or None,
      value(row,m,'jockey') or None,number(value(row,m,'rating')),weight,odds(value(row,m,'sp')),
      int(number(value(row,m,'draw'))) if number(value(row,m,'draw')) is not None else None,
      int(number(value(row,m,'age'))) if number(value(row,m,'age')) is not None else None,
      value(row,m,'sex') or None,value(row,m,'sire') or None,value(row,m,'dam') or None)
    before=conn.total_changes
    conn.execute('INSERT OR IGNORE INTO historical_runs VALUES ('+','.join('?'*len(rowrun))+')',rowrun)
    if conn.total_changes>before:summary['inserted']+=1
    else:summary['duplicates']+=1
    if summary['rows_seen']%10000==0:conn.commit()
   if max_rows and summary['rows_seen']>max_rows:break
 if conn:
  conn.commit()
  summary['races']=conn.execute('SELECT count(*) FROM historical_races').fetchone()[0]
  summary['runs']=conn.execute('SELECT count(*) FROM historical_runs').fetchone()[0]
  summary['distinct_usable_horse_ids']=conn.execute('SELECT count(DISTINCT horse_source_id) FROM historical_runs').fetchone()[0]
  summary['integrity']=conn.execute('PRAGMA integrity_check').fetchone()[0]
  if current_db:link_preview(conn,current_db,str(Path(destination).with_suffix(''))+'_identity_review.csv',summary)
  conn.close()
 summary['skipped']=dict(summary['skipped'])
 return summary

def link_preview(c,current_db,path,summary):
 # Candidates only; names never auto-merged. Same name can refer to different horses.
 cc=sqlite3.connect(current_db)
 try:rows=cc.execute('SELECT horse_id,horse_name,birth_country FROM performances WHERE horse_id IS NOT NULL GROUP BY horse_id,horse_name,birth_country').fetchall()
 except sqlite3.DatabaseError:return
 candidates=[]
 for horseid,name,country in rows:
  n=normname(name)
  hits=c.execute('''SELECT horse_source_id,horse_name,horse_country,min(race_date),max(race_date),count(*)
    FROM historical_runs h JOIN historical_races r ON r.id=h.race_id
    WHERE horse_name_norm=? GROUP BY horse_source_id,horse_name,horse_country ORDER BY count(*) DESC LIMIT 10''',(n,)).fetchall()
  for h in hits:
   if h[2] and country and h[2].upper()!=str(country).upper():continue
   candidates.append((horseid,name,country,*h,'REVIEW REQUIRED; NAME ALONE IS NOT PROOF'))
 with open(path,'w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['current_horse_id','current_horse_name','current_origin','archive_horse_id','archive_horse_name','archive_origin','first_run','last_run','archive_runs','status']);w.writerows(candidates)
 summary['identity_review_file']=path
 summary['unconfirmed_identity_candidates']=len(candidates)
 cc.close()

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--input',required=True,help='CSV, ZIP, or folder containing CSVs')
 p.add_argument('--mode',choices=['inspect','import'],default='inspect')
 p.add_argument('--database',default='historical_ukie_staging.sqlite')
 p.add_argument('--current-db',help='October baseline database, optional; only used for review suggestions')
 p.add_argument('--min-date',default='2010-01-01')
 p.add_argument('--max-date',default='2026-10-07',help='Stage 1 starts 2026-10-08; exclude overlaps by default')
 p.add_argument('--report',default='historical_stage2_report.json')
 p.add_argument('--max-rows',type=int,default=0,help='Optional diagnostic cap')
 args=p.parse_args()
 if args.mode=='import' and Path(args.database).exists():
  raise SystemExit('Refusing to overwrite existing staging database; choose a new output file')
 report=process(args.input,args.database,args.min_date,args.max_date,args.mode,args.current_db,args.max_rows or None)
 Path(args.report).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
 print(json.dumps({k:v for k,v in report.items() if k!='files'},indent=2))
 for f in report['files']:
  print('FILE:',f['file'],'RECOGNIZED:',f['recognized'],'FIELDS:',f.get('mapped'),'SAMPLE:',f.get('first_valid_row'),'REASON:',f.get('reason',''))
 if not any(f['recognized'] for f in report['files']):raise SystemExit('No compatible runner-level CSV found. Inspect columns in report; do not force an import.')
 if report['eligible']==0:raise SystemExit('No eligible historical rows in requested dates. No changes made.')
 print('Stage 2 completed. The live app and Stage 1 database were NOT modified.')
if __name__=='__main__':main()
