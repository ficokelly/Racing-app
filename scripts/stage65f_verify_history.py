#!/usr/bin/env python3
"""Small-report, research-only verification of the Stage65E historical database."""
import argparse,collections,csv,datetime as dt,json,pathlib,sqlite3

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',default='reports/stage65f');a=p.parse_args()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 db=pathlib.Path(a.db)
 if not db.is_file():raise SystemExit('Historical database missing')
 c=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
 if c.execute('pragma quick_check').fetchone()[0]!='ok':raise SystemExit('Historical database corrupt')
 total=c.execute('select count(*) from historical_runs').fetchone()[0]
 if total<5000000:raise SystemExit('Unexpectedly few historical records')
 cols=[x[1] for x in c.execute('pragma table_info(historical_runs)')]
 fields=['race_date','course','race_id','horse','finish','trainer','jockey','weight','official_rating','rpr','ts','beaten','overall_beaten','race_class','race_type','going','distance','sp']
 completeness={}
 for col in fields:
  if col not in cols:continue
  present=c.execute(f'SELECT count(*) FROM historical_runs WHERE "{col}" IS NOT NULL AND trim("{col}")<>""').fetchone()[0]
  completeness[col]={'present':present,'missing':total-present,'coverage_pct':round(100*present/total,3)}
 # Group anomalies and date problems, without exporting millions of records.
 malformed=[]
 for r in c.execute('''SELECT source_file,source_line,race_date,course,horse,substr(raw_json,1,500)
  FROM historical_runs WHERE race_date IS NULL OR length(race_date)<8
  OR substr(race_date,1,4)<'1988' OR substr(race_date,1,4)>'2026'
  LIMIT 150'''):
  malformed.append(dict(zip(['file','line','date','course','horse','raw_excerpt'],r)))
 # Duplicate race/horse is an indicator, not proof: same names can be distinct horses.
 dupe=c.execute('''SELECT count(*) FROM (SELECT race_date,course,race_id,horse,count(*) n
 FROM historical_runs GROUP BY race_date,course,race_id,horse HAVING n>1)''').fetchone()[0]
 dupes=[dict(zip(['date','course','race_id','horse','count'],r)) for r in c.execute('''SELECT race_date,course,race_id,horse,count(*) n
 FROM historical_runs GROUP BY race_date,course,race_id,horse HAVING n>1 ORDER BY n DESC LIMIT 40''')]
 year_counts=dict(c.execute("SELECT substr(race_date,1,4),count(*) FROM historical_runs GROUP BY 1 ORDER BY 1"))
 file_counts=[dict(zip(['file','rows','sha256'],r)) for r in c.execute('SELECT path,rows,sha256 FROM import_files ORDER BY path')]
 # Value-level classification audit; report class patterns, do not invent class values.
 classes=[{'class':v,'rows':n} for v,n in c.execute('''SELECT coalesce(nullif(trim(race_class),''),'[missing]'),count(*)
 FROM historical_runs GROUP BY 1 ORDER BY 2 DESC LIMIT 30''')]
 race_types=[{'type':v,'rows':n} for v,n in c.execute('''SELECT coalesce(nullif(trim(race_type),''),'[missing]'),count(*)
 FROM historical_runs GROUP BY 1 ORDER BY 2 DESC LIMIT 30''')]
 report={'status':'RESEARCH_ONLY_NO_LIVE_CHANGES','db_quick_check':'ok','total_runner_rows':total,
 'expected_stage2_rows':5106111,'difference_vs_stage2':total-5106111,
 'schema':cols,'field_completeness':completeness,'year_counts':year_counts,
 'malformed_date_sample':malformed,'duplicate_race_horse_groups':dupe,'duplicate_sample':dupes,
 'source_files':file_counts,'class_values':classes,'race_type_values':race_types,
 'warnings':['No horse identity is inferred from name alone','Duplicate groups are diagnostic and require validation','Historical database was regenerated from source, not compared row-by-row with original Stage2','Live Model D remains untouched','No missing values were imputed']}
 (out/'stage65f_history_validation.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
 with (out/'stage65f_field_completeness.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['field','present','missing','coverage_pct']);w.writerows([[k,*v.values()] for k,v in completeness.items()])
 with (out/'stage65f_year_counts.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['year','runner_rows']);w.writerows(year_counts.items())
 print(json.dumps({'status':report['status'],'total_runner_rows':total,'difference_vs_stage2':report['difference_vs_stage2'],'malformed_date_sample_count':len(malformed),'duplicate_groups':dupe,'field_completeness':completeness},indent=2))
 c.close()
if __name__=='__main__':main()
