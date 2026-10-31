#!/usr/bin/env python3
"""Audit imported Stage 2 data by year. Read-only. Python 3.11+ stdlib."""
import argparse, csv, json, sqlite3
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--database',required=True)
p.add_argument('--output',default='historical_annual_coverage.csv')
p.add_argument('--summary',default='historical_coverage_summary.json')
p.add_argument('--expect-first-year',type=int,default=1988)
a=p.parse_args()
c=sqlite3.connect(f'file:{Path(a.database).resolve()}?mode=ro',uri=True)
q='''SELECT substr(r.race_date,1,4) AS yr, COUNT(*) runners, COUNT(DISTINCT h.race_id) races,
 SUM(h.finish_pos=1) wins, SUM(h.sp_decimal IS NOT NULL) priced,
 SUM(h.finish_pos=1 AND h.sp_decimal IS NOT NULL) priced_winners,
 SUM(h.official_rating IS NOT NULL) rating,
 SUM(h.rpr IS NOT NULL) rpr,
 SUM(h.topspeed IS NOT NULL) topspeed,
 SUM(h.age IS NOT NULL) age,
 SUM(h.draw IS NOT NULL) draw,
 SUM(h.sire IS NOT NULL AND TRIM(h.sire)!='') sire,
 SUM(h.dam IS NOT NULL AND TRIM(h.dam)!='') dam,
 SUM(h.trainer IS NOT NULL AND TRIM(h.trainer)!='') trainer,
 SUM(r.going IS NOT NULL AND TRIM(r.going)!='') going,
 SUM(r.distance_f IS NOT NULL) distance
 FROM historical_runs h JOIN historical_races r ON r.id=h.race_id
 GROUP BY yr ORDER BY yr'''
rows=c.execute(q).fetchall();cols=['year','runners','races','wins','priced','priced_winners','rating','rpr','topspeed','age','draw','sire','dam','trainer','going','distance']
with open(a.output,'w',newline='',encoding='utf-8') as f:
 w=csv.writer(f);w.writerow(cols+['sp_coverage_pct','rpr_coverage_pct','sire_coverage_pct','draw_coverage_pct'])
 for row in rows:
  y,n,*_=row;d=dict(zip(cols,row));w.writerow(list(row)+[round(100*d[k]/n,2) if n else 0 for k in ['priced','rpr','sire','draw']])
missing=[y for y in range(a.expect_first_year,int(rows[-1][0])+1) if str(y) not in {r[0] for r in rows}] if rows else []
result={'first_year_actual':int(rows[0][0]) if rows else None,'last_year_actual':int(rows[-1][0]) if rows else None,'expected_first_year':a.expect_first_year,'missing_years':missing,'total_runners':sum(r[1] for r in rows),'total_races':sum(r[2] for r in rows),'full_1988_coverage_confirmed':bool(rows) and int(rows[0][0])<=a.expect_first_year and not missing,'year_audit_csv':a.output}
Path(a.summary).write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
