#!/usr/bin/env python3
"""D(c)-only Stage81 category integration, immutable 10 Nov baseline and one-off guard.

All writes to SQLite are exclusively to Stage65M's disposable D(c) candidate
DB, never the original Stage65E/Stage81 source or the frozen D model. Class/OR/
RPR/SP are never imputed. Only verified, exact race IDs are eligible.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, hashlib, json, pathlib, re, sqlite3, subprocess, sys
from zoneinfo import ZoneInfo

ROOT=pathlib.Path(__file__).resolve().parent.parent
SCRIPTS=ROOT/'scripts'
if str(SCRIPTS) not in sys.path:sys.path.insert(0,str(SCRIPTS))
UTC=dt.timezone.utc
IE=ZoneInfo('Europe/Dublin')
VERSION='2026-11-10-stage83-stage81-race-types-v1'
ONCE_KEY='2026-11-10|stage81-integration-once-v1'
ORIGINAL_COMMIT='e1943880f7c60e43b2946cc2ee57c71f894c9467'
SOURCE='kaggle-deltaromeo-ukie'
PINNED_SOURCE_SHA='b028ec32d2bae1d34331cda150d545b7441c9e45aa4ddb27b5ce450562288cbe'
PINNED_SOURCE_ROWS=75944
ACCEPTED_STATUSES={'EXPLICIT_SOURCE','EXPLICIT_ABBREVIATION'}
DISCIPLINE={'chase':'Chase','hurdle':'Hurdle','nh_flat':'National Hunt Flat','flat':'Flat'}
CATEGORIES={'handicap':'Handicap','maiden':'Maiden','novice':'Novice','beginner':'Beginner',
 'seller':'Seller','claimer':'Claimer','conditions':'Conditions','bumper':'Bumper',
 'listed':'Listed','hunter_chase':'Hunter Chase','classified':'Classified','auction':'Auction'}


def normhead(s):return re.sub(r'[^a-z0-9]+','_',str(s or '').casefold()).strip('_')
def source_id(s):return re.sub(r'[^a-zA-Z0-9_-]','',str(s or ''))[:90]
def canon(s):return re.sub(r'[^a-z0-9]+','',str(s or '').casefold())
def read_json(path,default=None):
 try:return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
 except (OSError,ValueError):return default


def classify_text(title,source_pattern,category,discipline,flags):
 """Return verifiable textual signals for the *existing* C category parser.

 This does not assert an official class, or infer handicap when absent. The
 original race title stays first; explicit Stage81 tags are appended separately.
 """
 labels=[]
 for flag in ('handicap','maiden','novice','beginner','listed','bumper','hunter_chase',
              'seller','claimer','conditions','classified','auction'):
  if flag in flags and flag in CATEGORIES:labels.append(CATEGORIES[flag])
 if category in CATEGORIES and CATEGORIES[category] not in labels:labels.append(CATEGORIES[category])
 if category=='graded_or_group':
  # Retain recorded pattern only. No invented Group/Grade numbers.
  m=re.search(r'\b(?:grade|group)\s*(?:[123]|I{1,3})\b',str(title)+' '+str(source_pattern),re.I)
  if m:labels.append(m.group(0))
 if discipline in DISCIPLINE:labels.append(DISCIPLINE[discipline])
 if not labels:return None
 return 'Stage81 explicit: '+', '.join(labels)


def historical_evidence(stage81):
    """Read pinned, compact Stage81 race metadata; source race names are not copied."""
    if stage81.suffix == '.gz':
        with open(stage81,'rb') as f:
            digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest != PINNED_SOURCE_SHA:
            raise RuntimeError('Stage81 compact evidence SHA256 mismatch; refuse scoring')
        n=0
        with gzip.open(stage81,'rt',encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f)
            expected={'race_date','course','source_race_id','region','race_title_sha256',
                'source_pattern','final_category','final_discipline','flags_json','classification_status'}
            if set(reader.fieldnames or []) != expected:
                raise RuntimeError('Stage81 compact evidence schema mismatch')
            for d in reader:
                n += 1
                yield (d['race_date'], d['course'], d['source_race_id'], d['region'],
                       d['race_title_sha256'], d['source_pattern'], d['final_category'],
                       d['final_discipline'], d['flags_json'], d['classification_status'])
        if n != PINNED_SOURCE_ROWS:
            raise RuntimeError('Stage81 compact source race count mismatch')
    else:
        read=sqlite3.connect('file:'+stage81.resolve().as_posix()+'?mode=ro',uri=True)
        try:
            if read.execute('pragma quick_check').fetchone()[0]!='ok':
                raise RuntimeError('Stage81 DB corrupt')
            qry="""SELECT race_date,course,source_race_id,region,race_name,source_pattern,
                final_category,final_discipline,flags_json,classification_status
                FROM category_reconciliation WHERE race_date >= '2021-01-01' AND region IN ('GB','IE')"""
            for day,course,rid,region,title,pattern,cat,disc,flags,status in read.execute(qry):
                yield (day,course,rid,region,hashlib.sha256(canon(title).encode()).hexdigest() if title else '',
                       pattern,cat,disc,flags,status)
        finally:read.close()


def integrate(candidate,stage81,out):
 candidate=pathlib.Path(candidate);stage81=pathlib.Path(stage81);out=pathlib.Path(out)
 out.mkdir(parents=True,exist_ok=True)
 if not candidate.is_file() or not stage81.is_file():raise RuntimeError('Both local candidate and Stage81 evidence DB are required')
 c=sqlite3.connect(candidate)
 if c.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('D(c) research candidate corrupt')
 assert {z[1] for z in c.execute('pragma table_info(historical_races)')}>={'id','race_date','course','race_type'}
 assert c.execute("select count(*) from sqlite_master where type='table' and name='stage65p_race_details'").fetchone()[0]==1
 # Keep existing sources untouched; only this D(c) disposable candidate gains a new table.
 c.executescript('''DROP TABLE IF EXISTS stage83_dc_verified_types;
 CREATE TABLE stage83_dc_verified_types (
 race_id TEXT PRIMARY KEY, model_race_type TEXT NOT NULL, region TEXT NOT NULL,
 category TEXT, discipline TEXT, evidence_status TEXT NOT NULL, source_race_id TEXT NOT NULL,
 source_title_hash TEXT, original_candidate_title TEXT);
 ''')
 candidates={z[0]:(z[1],z[2],z[3]) for z in c.execute('''SELECT r.id,r.race_date,r.course,d.race_name FROM historical_races r
 LEFT JOIN stage65p_race_details d ON d.race_id=r.id WHERE r.race_date >= '2021-01-01' AND r.id LIKE ?''',(SOURCE+':%',))}
 stats=collections.Counter();sample=[];batch=[];yrs=collections.Counter()
 for day,course,rid,region,title_hash,pattern,category,discipline,rawflags,status in historical_evidence(stage81):
  stats['stage81_ukie_recent_races']+=1
  if status not in ACCEPTED_STATUSES:
   stats['skipped_unverified_or_unclassified']+=1;continue
  if not day or not course or not rid:
   stats['skipped_missing_identity']+=1;continue
  key=':'.join((SOURCE,str(day),normhead(course),source_id(rid)))
  original=candidates.get(key)
  if original is None:
   stats['skipped_unmatched_exact_race_id']+=1;continue
  dbday,dbcourse,dbtitle=original
  if dbday != day or normhead(dbcourse)!=normhead(course):
   stats['skipped_identity_collision']+=1;continue
  if dbtitle and title_hash and hashlib.sha256(canon(dbtitle).encode()).hexdigest()!=title_hash:
   stats['skipped_conflicting_source_titles']+=1
   if len(sample)<30:sample.append({'day':day,'course':course,'race_id':rid,'title_hash_conflict':True})
   continue
  try:flags=json.loads(rawflags or '[]')
  except ValueError:flags=[]
  if not isinstance(flags,list):flags=[]
  label=classify_text(dbtitle,pattern,category,discipline,flags)
  if not label:
   stats['skipped_no_reliable_category_or_discipline']+=1;continue
  # No modification to official race_type, numerical class, SP, OR or RPR.
  batch.append((key,label,region,category,discipline,status,rid,title_hash,dbtitle))
  stats['verified_records_applied']+=1
  yrs[str(day)[:4]]+=1
  if len(batch)>=10000:
   c.executemany('INSERT INTO stage83_dc_verified_types VALUES (?,?,?,?,?,?,?,?,?)',batch)
   c.commit();batch=[]
 if batch:c.executemany('INSERT INTO stage83_dc_verified_types VALUES (?,?,?,?,?,?,?,?,?)',batch)
 c.commit()
 if stats['verified_records_applied']<10000 or yrs['2024']<1500 or yrs['2025']<1500:
  raise RuntimeError('Insufficient exact Stage81 integration coverage; refuse D(c) publication: '+str(dict(stats))+'; '+str(dict(yrs)))
 audit={'version':VERSION,'source':'Stage81 42728478158 UK/IE research overlay',
  'mode':'D(c)_TEMP_RESEARCH_DATABASE_ONLY','counts':dict(stats),'matched_by_year':dict(sorted(yrs.items())),
  'candidate_db':str(candidate),'conflict_sample':sample,'frozen_d_unchanged':True,
  'source_stage81_db_unchanged':True,'race_type_in_original_candidate_unchanged':True,
  'notes':['C features use documented Stage81 race-title conditions and corrected discipline',
           '2024 classifier is rebuilt from Stage81-enriched categories (separate cache)',
           'Model D factors, parameters and source datasets are unchanged',
           'A source race identity/title conflict is skipped rather than guessed',
           'Not a recovery of RPR, SP or numerical class']}
 (out/'integration_audit.json').write_text(json.dumps(audit,indent=2)+'\n')
 c.close();return audit


def corrected_rows(db,through):
 """Drop-in C.rows replacement scoped to this D(c) Python process only."""
 cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 sql='''SELECT r.race_date,r.id,r.course,COALESCE(z.model_race_type,d.race_name,r.race_type),
 r.surface,r.race_class,u.horse_name,u.horse_country,u.trainer,u.jockey,
 u.official_rating,u.weight_lbs,u.finish_pos,u.beaten,u.rpr
 FROM historical_races r JOIN historical_runs u ON r.id=u.race_id
 LEFT JOIN stage65p_race_details d ON d.race_id=r.id
 LEFT JOIN stage83_dc_verified_types z ON z.race_id=r.id
 WHERE r.race_date >= '2021-01-01' AND r.race_date < ? ORDER BY r.race_date,r.id'''
 current=None;group=[]
 try:
  for raw in cx.execute(sql,(through,)):
   if current is not None and raw[1]!=current:
    yield group;group=[]
   current=raw[1]
   day,rid,course,typ,surf,klass,horse,country,tr,jo,orr,weight,finish,bn,rpr=raw
   group.append({'date':day,'race_id':rid,'course':course,'race_type':typ,
    'surface':surf,'race_class':klass,'horse':horse,'country':country,
    'trainer':tr,'jockey':jo,'or':orr,'weight':weight,
    'finish':finish,'beaten':bn,'rpr':rpr})
  if group:yield group
 finally:cx.close()


def patched_classifier(original_module,C,db):
 """New versioned classifier cache; never use old D(c) pretrained C cache."""
 import joblib,numpy as np
 from sklearn.linear_model import LogisticRegression
 from sklearn.preprocessing import StandardScaler
 saved=ROOT/'reports/stage68/base/c_classifier_2024_stage81_v1.joblib'
 if saved.is_file():
  meta=joblib.load(saved)
  if meta.get('format')!=VERSION or meta.get('features')!=list(C.FEATURES):
   raise RuntimeError('Stage81 classifier cache provenance mismatch')
  scaler,model=meta['scaler'],meta['model']
  if getattr(scaler,'n_features_in_',None)!=len(C.FEATURES) or getattr(model,'n_features_in_',None)!=len(C.FEATURES):
   raise RuntimeError('Stage81 classifier cache dimensionality mismatch')
  return scaler,model
 X,y,years,counts,_=C.build_training(db)
 tr=(years==2024);va=(years==2025)
 if tr.sum()<100 or va.sum()<100 or y[tr].sum()<10:raise RuntimeError('Insufficient time-separated Stage81 training sample')
 for yr in (2024,2025):
  total=sum(v for (year,cat),v in counts.items() if year==yr)
  known=sum(v for (year,cat),v in counts.items() if year==yr and cat!='unknown')
  if total<100 or known/total<.60:raise RuntimeError(f'Stage81 C category coverage too low {yr}: {known}/{total}')
 scaler=StandardScaler().fit(X[tr]);model=LogisticRegression(C=.1,max_iter=500).fit(scaler.transform(X[tr]),y[tr])
 saved.parent.mkdir(parents=True,exist_ok=True)
 joblib.dump({'format':VERSION,'features':list(C.FEATURES),
   'training_year':2024,'validation_year':2025,'training_rows':int(tr.sum()),
   'validation_rows':int(va.sum()),'scaler':scaler,'model':model},saved)
 print('Stage83: trained separate C model with Stage81 historical categories',int(tr.sum()),'rows',flush=True)
 return scaler,model


def key(r):return (str(r.get('date') or ''),canon(r.get('course')),str(r.get('time') or ''))
def rank_pair(r):
 arr=sorted((z for z in r.get('runners',[]) if isinstance(z.get('rank'),int)),key=lambda x:x['rank'])
 return [(x.get('horse') or '') for x in arr[:2]]+['']*max(0,2-len(arr))


def comparison(newfile,report_dir,day,baseline_commit=ORIGINAL_COMMIT):
 """Historical immutable first publication, not whatever previous refresh retained."""
 oldraw=subprocess.run(['git','show',baseline_commit+':data/strategy-dc-probabilities.json'],
                      cwd=ROOT,text=True,capture_output=True,check=True).stdout
 original=json.loads(oldraw);new=read_json(newfile,{})
 first={key(r):r for r in original.get('races',[]) if r.get('date')==day}
 latest={key(r):r for r in new.get('races',[]) if r.get('date')==day}
 rows=[];tot=collections.Counter()
 for k,a in sorted(first.items(),key=lambda x:(x[0][0],x[0][2],x[0][1])):
  b=latest.get(k);old1,old2=rank_pair(a);new1,new2=rank_pair(b or {})
  changed1=bool(b and old1!=new1);changed2=bool(b and old2!=new2)
  status=('MISSING_LATEST' if not b else 'RANK_1_AND_2_CHANGED' if changed1 and changed2 else
     'RANK_1_CHANGED' if changed1 else 'RANK_2_CHANGED' if changed2 else 'UNCHANGED')
  tot[status]+=1
  rows.append({'date':day,'time':a.get('time'),'course':a.get('course'),'race_name':a.get('race_name'),
   'first_rank1':old1,'first_rank2':old2,'reassessed_rank1':new1,'reassessed_rank2':new2,
   'first_field_size':a.get('field_size'),'reassessed_field_size':b.get('field_size') if b else None,
   'status':status,'first_published_at':a.get('published_at'),'reassessed_at':b.get('published_at') if b else None,
   'reassessed_with_stage81':bool(b and b.get('model_version')==VERSION and b.get('published_at')!=a.get('published_at'))})
 for k,b in latest.items():
  if k not in first:
   n1,n2=rank_pair(b)
   rows.append({'date':day,'time':b.get('time'),'course':b.get('course'),'race_name':b.get('race_name'),
    'first_rank1':'','first_rank2':'','reassessed_rank1':n1,'reassessed_rank2':n2,
    'status':'NEW_RACE','reassessed_at':b.get('published_at')})
   tot['NEW_RACE']+=1
 report_dir=pathlib.Path(report_dir);report_dir.mkdir(parents=True,exist_ok=True)
 with (report_dir/f'{day}_original_vs_integrated.csv').open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else []);w.writeheader();w.writerows(rows)
 result={'date':day,'original_commit':baseline_commit,'first_races':len(first),'latest_races':len(latest),
   'changed_rank1':sum(r['status'] in ('RANK_1_CHANGED','RANK_1_AND_2_CHANGED') for r in rows),
   'changed_rank2':sum(r['status'] in ('RANK_2_CHANGED','RANK_1_AND_2_CHANGED') for r in rows),
   'changed_either':sum('CHANGED' in r['status'] for r in rows),
   'new_races':tot['NEW_RACE'],'unmatched_original':tot['MISSING_LATEST'],
   'fresh_stage81_reassessed_races':sum(bool(r.get('reassessed_with_stage81')) for r in rows),
   'status_counts':dict(tot),'frozen_model_d_modified':False}
 (report_dir/f'{day}_comparison_summary.json').write_text(json.dumps(result,indent=2)+'\n')
 return result


def one_time_guard():
 now=dt.datetime.now(IE)
 target=dt.date(2026,11,10)
 schedule=read_json(ROOT/'data/strategy-dc-schedule.json',{}) or {}
 runs=schedule.get('runs',{})
 cards=read_json(ROOT/'data/racecards.json',{}) or {}
 valid=False
 for r in cards.get('runners',[]):
  if str(r.get('date') or r.get('race_time') or '')[:10]!=target.isoformat():continue
  if not r.get('course') or not r.get('horse'):continue
  try:
   off=dt.datetime.fromisoformat(str(r['race_time']).replace('Z','+00:00'))
   if off.tzinfo is None:off=off.replace(tzinfo=UTC)
   if off.astimezone(UTC)>now.astimezone(UTC)+dt.timedelta(minutes=2):valid=True;break
  except (TypeError,KeyError,ValueError):continue
 active=now.date()==target and valid and ONCE_KEY not in runs
 output={'run':str(active).lower(),'day':target.isoformat() if active else '',
         'slot':'stage81-integration-once-v1' if active else ''}
 if 'GITHUB_OUTPUT' in __import__('os').environ:
  with open(__import__('os').environ['GITHUB_OUTPUT'],'a') as f:
   for k,v in output.items():f.write(f'{k}={v}\n')
 print('One-time Stage81 D(c) guard:',output,'now',now.isoformat(),flush=True)
 return output


def self_test():
 assert classify_text('Nov Hdle','', 'novice','hurdle',['novice'])=='Stage81 explicit: Novice, Hurdle'
 assert classify_text('Hcap Maiden Hurdle','', 'handicap','hurdle',['handicap','maiden'])=='Stage81 explicit: Handicap, Maiden, Hurdle'
 assert classify_text('Unknown Cup','', 'unclassified','unknown',[]) is None
 assert source_id('abc.234')=='abc234'
 assert normhead('Lingfield (AW)')=='lingfield_aw'
 import tempfile
 with tempfile.TemporaryDirectory() as root:
  root=pathlib.Path(root);db=root/'candidate.sqlite';overlay=root/'stage81.sqlite'
  c=sqlite3.connect(db)
  c.executescript('''CREATE TABLE historical_races(id TEXT,race_date TEXT,course TEXT,race_type TEXT,surface TEXT,race_class TEXT);
    CREATE TABLE stage65p_race_details(race_id TEXT,race_name TEXT);
    CREATE TABLE historical_runs(race_id TEXT,horse_name TEXT,horse_country TEXT,trainer TEXT,jockey TEXT,official_rating REAL,weight_lbs REAL,finish_pos INT,beaten TEXT,rpr REAL);
  ''')
  for idx,day in enumerate(('2024-05-01','2025-05-01','2026-02-01')):
   rid='kaggle-deltaromeo-ukie:'+day+':ascot:'+str(idx)
   c.execute('INSERT INTO historical_races VALUES (?,?,?,?,?,?)',(rid,day,'Ascot','Flat','Turf',None))
   c.execute('INSERT INTO stage65p_race_details VALUES (?,?)',(rid,'Nov Hdle'))
   c.execute('INSERT INTO historical_runs VALUES (?,?,?,?,?,?,?,?,?,?)',(rid,'Horse','IRE','Trainer','Jockey',None,None,1,None,None))
  c.commit();c.close()
  c=sqlite3.connect(overlay)
  c.execute('''CREATE TABLE category_reconciliation (race_date TEXT,course TEXT,source_race_id TEXT,region TEXT,race_name TEXT,source_pattern TEXT,final_category TEXT,final_discipline TEXT,flags_json TEXT,classification_status TEXT)''')
  for idx,day in enumerate(('2024-05-01','2025-05-01','2026-02-01')):
   c.execute('INSERT INTO category_reconciliation VALUES (?,?,?,?,?,?,?,?,?,?)',(day,'Ascot',str(idx),'GB','Nov Hdle','','novice','hurdle','["novice"]','EXPLICIT_ABBREVIATION'))
  c.commit();c.close()
  try:integrate(db,overlay,root)
  except RuntimeError as e:
   assert 'Insufficient exact Stage81 integration coverage' in str(e)
  c=sqlite3.connect(db)
  assert c.execute('SELECT count(*) FROM stage83_dc_verified_types').fetchone()[0]==3
  assert c.execute("SELECT race_type FROM historical_races LIMIT 1").fetchone()[0]=='Flat'
  assert 'Novice, Hurdle' in c.execute('SELECT model_race_type FROM stage83_dc_verified_types LIMIT 1').fetchone()[0]
  c.close()
 print('Stage83 PASS: exact ID, non-mutating overlay, classification and one-time logic',flush=True)


def run(a):
 from sklearn.linear_model import LogisticRegression
 from sklearn.preprocessing import StandardScaler
 import stage65p_race_type_c_only as C
 import rhp_stage68_dc as D
 audit=integrate(a.db,a.stage81,a.out)
 C.rows=corrected_rows  # only the current Python process uses the amended history
 D.c_classifier=lambda db:patched_classifier(D,C,db)
 import stage116_verified_form_live as _stage116_form
 _stage116_form_stats=_stage116_form.patch_dc_runtime(D,C,a.db,a.day)
 orig_write=D.write
 def write_with_version(path,obj):
  if pathlib.Path(path)==D.FEED:
   obj["verified_form_recovery"]=_stage116_form_stats
   for r in obj.get('races',[]):
    if r.get('date')==a.day and r.get('published_at')==obj.get('generated_at'):
     r['model_version']=VERSION;r['historical_categories']='Stage81 UK/IE exact-race verified'
   obj['model_version']=VERSION
   obj['stage81_integration']={'applied':audit['counts'].get('verified_records_applied'),
    'source_run':42728478158,'original_first_commit':ORIGINAL_COMMIT}
  orig_write(path,obj)
 D.write=write_with_version
 args=sys.argv
 try:
  sys.argv=['rhp_stage68_dc.py','--db',str(a.db),'--day',a.day,'--slot',a.slot]
  D.main()
 finally:sys.argv=args
 if a.day == '2026-11-10':
  result=comparison(D.FEED,a.out,a.day)
  print('Stage83 immediate D(c) report:',json.dumps(result,indent=2),flush=True)
  if result['first_races'] and result['latest_races']<result['first_races']//2:
   raise RuntimeError('Most baseline races unexpectedly missing; review publishing')
 else:
  (a.out/'other_day_publication.json').write_text(json.dumps({
    'day':a.day,'model_version':VERSION,'note':'No Nov 10 first-snapshot comparison for this date',
    'category_records_applied':audit['counts']['verified_records_applied']},indent=2)+'\n')


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--self-test',action='store_true');p.add_argument('--once-guard',action='store_true')
 p.add_argument('--db',type=pathlib.Path);p.add_argument('--stage81',type=pathlib.Path)
 p.add_argument('--day');p.add_argument('--slot',default='scheduled')
 p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage83'))
 a=p.parse_args()
 if a.self_test:self_test();return
 if a.once_guard:one_time_guard();return
 if not all((a.db,a.stage81,a.day)):p.error('--db --stage81 --day required')
 dt.date.fromisoformat(a.day)
 run(a)

if __name__=='__main__':main()
