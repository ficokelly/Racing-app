#!/usr/bin/env python3
"""Read-only historical quality audit. Does not modify Model D."""
import argparse,csv,json,pathlib,sqlite3
p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',default='reports/stage65g');a=p.parse_args()
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
c=sqlite3.connect('file:'+str(pathlib.Path(a.db).resolve())+'?mode=ro',uri=True)
assert c.execute('pragma quick_check').fetchone()[0]=='ok'
cols={r[1] for r in c.execute('pragma table_info(historical_runs)')}
fields=[f for f in ('race_date','course','race_id','horse','finish','trainer','jockey','weight','official_rating','rpr','ts','beaten','overall_beaten','race_class','race_type','going','distance','sp') if f in cols]
years=[{'year':y,'runner_rows':n} for y,n in c.execute("select substr(race_date,1,4),count(*) from historical_runs group by 1 order by 1")]
with (out/'year_counts.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=['year','runner_rows']);w.writeheader();w.writerows(years)
with (out/'year_field_coverage.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['year','runner_rows','field','present','missing','coverage_pct'])
 for y in years:
  for field in fields:
   present=c.execute(f'''select count(*) from historical_runs where substr(race_date,1,4)=? and "{field}" is not null and trim("{field}")<>'' ''',(y['year'],)).fetchone()[0]
   n=y['runner_rows'];w.writerow([y['year'],n,field,present,n-present,round(100*present/n,3) if n else 0])
duplicates=c.execute('select count(*) from (select race_date,course,race_id,horse from historical_runs group by 1,2,3,4 having count(*)>1)').fetchone()[0]
headers=c.execute("select count(*) from historical_runs where lower(trim(horse))='horse' or lower(trim(race_date)) in ('date','race_date')").fetchone()[0]
periods={label:sum(y['runner_rows'] for y in years if y['year'].isdigit() and lo<=int(y['year'])<=hi) for label,lo,hi in [('1988-2004',1988,2004),('2005-2014',2005,2014),('2015-2025',2015,2025),('2026',2026,2026)]}
report={'status':'RESEARCH_ONLY','integrity':'ok','total_rows':sum(y['runner_rows'] for y in years),'periods':periods,'duplicate_groups':duplicates,'header_rows':headers,'limitations':['Internal counts do not prove complete external race coverage','No duplicates deleted','No Model D training or ROI performed']}
(out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2));c.close()
