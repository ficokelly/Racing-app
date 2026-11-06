#!/usr/bin/env python3
"""Rebuild detailed UK/IE historical form from the exact Stage2 Kaggle dataset.
Research-only; source CSV fields preserved losslessly. Never modifies production data.
"""
import argparse,csv,datetime as dt,hashlib,json,os,pathlib,sqlite3,sys,collections
FIELDS=['date','course','race_id','off','race_name','type','class','pattern','rating_band','age_band','sex_rest','dist','going','ran','num','pos','draw','ovr_btn','btn','horse','age','sex','wgt','hg','time','sp','jockey','trainer','prize','or','rpr','ts','sire','dam','damsire','owner','comment']
DETAIL=['btn','ovr_btn','or','rpr','ts','wgt','trainer','jockey','class','type','going','dist','comment','sp']
SOURCE='deltaromeo/horse-racing-results-ukireland-2015-2025';VERSION=129

def csv_paths(root):
    for p in sorted(pathlib.Path(root).rglob('*.csv')):
        if p.name.lower().startswith(('readme','metadata')):continue
        yield p

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',default='reports/stage65e');ap.add_argument('--dataset-dir',default='');ap.add_argument('--no-download',action='store_true');ap.add_argument('--max-rows',type=int,default=0)
    a=ap.parse_args();out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    if a.dataset_dir:root=pathlib.Path(a.dataset_dir)
    else:
        if a.no_download:sys.exit('No dataset supplied')
        import kagglehub
        root=pathlib.Path(kagglehub.dataset_download(SOURCE+"/versions/"+str(VERSION)))
    files=list(csv_paths(root));print('CSV candidates:',len(files),flush=True)
    if not files:sys.exit('No CSV files in dataset')
    db=out/'detailed_history.sqlite';cx=sqlite3.connect(db);cx.execute('PRAGMA journal_mode=DELETE');cx.execute('PRAGMA synchronous=NORMAL')
    cx.executescript('''CREATE TABLE IF NOT EXISTS historical_runs(
      source_file TEXT NOT NULL, source_line INTEGER NOT NULL, race_date TEXT,course TEXT,race_id TEXT,horse TEXT,
      finish TEXT, trainer TEXT,jockey TEXT,weight TEXT,official_rating TEXT,rpr TEXT,ts TEXT,
      beaten TEXT,overall_beaten TEXT,race_class TEXT,race_type TEXT,going TEXT,distance TEXT,sp TEXT,
      raw_json TEXT NOT NULL,PRIMARY KEY(source_file,source_line));
      CREATE INDEX IF NOT EXISTS ix_horse ON historical_runs(horse,race_date);
      CREATE INDEX IF NOT EXISTS ix_race ON historical_runs(race_date,course,race_id);
      CREATE TABLE IF NOT EXISTS import_files(path TEXT PRIMARY KEY,sha256 TEXT,rows INTEGER,missing_fields TEXT);
    ''')
    count=0;file_reports=[];coverage=collections.Counter();missing=collections.Counter();unknown=[]
    for file in files:
        rel=str(file.relative_to(root));sha=hashlib.sha256();local_count=0;local_missing=collections.Counter()
        with file.open('rb') as fh:
            for chunk in iter(lambda:fh.read(1024*1024),b''):sha.update(chunk)
        with file.open('r',encoding='utf-8-sig',errors='replace',newline='') as fh:
            reader=csv.DictReader(fh);headers=reader.fieldnames or []
            if not {'date','course','horse','pos'}.issubset(headers):
                unknown.append({'file':rel,'headers':headers[:30]});continue
            for line,row in enumerate(reader,2):
                if not row or not row.get('horse'):continue
                # Retain all original fields, even ones the model doesn't yet use.
                raw=json.dumps(row,ensure_ascii=False,separators=(',',':'))
                vals=(rel,line,row.get('date'),row.get('course'),row.get('race_id'),row.get('horse'),row.get('pos'),row.get('trainer'),row.get('jockey'),row.get('wgt'),row.get('or'),row.get('rpr'),row.get('ts'),row.get('btn'),row.get('ovr_btn'),row.get('class'),row.get('type'),row.get('going'),row.get('dist'),row.get('sp'),raw)
                cx.execute('INSERT OR REPLACE INTO historical_runs VALUES ('+','.join('?'*len(vals))+')',vals)
                for f in DETAIL:
                    if not str(row.get(f) or '').strip():local_missing[f]+=1;missing[f]+=1
                year=str(row.get('date') or '')[:4];coverage[year]+=1
                count+=1;local_count+=1
                if count%100000==0:cx.commit();print('Imported',count,'runner rows',flush=True)
                if a.max_rows and count>=a.max_rows:break
        cx.execute('INSERT OR REPLACE INTO import_files VALUES (?,?,?,?)',(rel,sha.hexdigest(),local_count,json.dumps(local_missing)))
        cx.commit();file_reports.append({'file':rel,'sha256':sha.hexdigest(),'rows':local_count,'missing':dict(local_missing)})
        if a.max_rows and count>=a.max_rows:break
    cx.commit();valid=cx.execute('PRAGMA quick_check').fetchone()[0];distinct=cx.execute("SELECT count(DISTINCT race_date || '|' || course || '|' || race_id) FROM historical_runs").fetchone()[0]
    report={'source':SOURCE,'version':VERSION,'dataset_directory':str(root),'status':'RESEARCH_ONLY_NO_MODEL_D_CHANGES','runner_rows':count,'distinct_race_keys':distinct,'year_counts':dict(sorted(coverage.items())),'missing_fields':dict(missing),'field_coverage_pct':{f:round(100*(count-missing[f])/count,2) if count else 0 for f in DETAIL},'files':file_reports,'unrecognized_csv':unknown,'database_quick_check':valid,'notes':['Original raw CSV values preserved for each row','No automatic matching to Stage65C; requires identity audit','Null or blank values are not filled or inferred','Original Kaggle data licence and permitted uses must be checked before publication']}
    (out/'stage65e_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    with (out/'stage65e_year_coverage.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['year','runner_rows']);w.writerows(sorted(coverage.items()))
    cx.close();print(json.dumps({k:v for k,v in report.items() if k not in ('files','year_counts')},indent=2),flush=True)
    if not count or valid!='ok':sys.exit('Import incomplete/corrupt')
    if not a.max_rows and (count<5000000 or coverage.get('1988',0)==0 or coverage.get('2025',0)==0):sys.exit('Unexpectedly incomplete historical source; do not use for live model')
if __name__=='__main__':main()
