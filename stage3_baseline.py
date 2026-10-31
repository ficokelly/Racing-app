#!/usr/bin/env python3
"""Stage 3 chronological trainer/course baseline using Stage 2 historical schema."""
import argparse,collections,csv,json,os,sqlite3,re
from pathlib import Path

def odds_decimal(x):
    if x is None:return None
    s=str(x).strip().lower()
    if s in ('evens','evs','even'):return 2.0
    try:
        if '/' in s:
            a,b=s.split('/',1);return 1+float(a)/float(b)
        v=float(s);return v if v>=1 else None
    except (ValueError,ZeroDivisionError):return None

def summary(v):
    n,w,p=v
    return {'bets':n,'wins':w,'profit_units':round(p,3),'strike_rate_pct':round(w*100/n,3) if n else None,'roi_pct':round(p*100/n,3) if n else None}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--out',default='stage3_output');a=ap.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect('file:'+os.path.abspath(a.db)+'?mode=ro',uri=True)
    tables={r[0]:[c[1] for c in con.execute('PRAGMA table_info("'+r[0]+'")')] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    (out/'schema.json').write_text(json.dumps(tables,indent=2))
    required_r={'id','race_date','course'};required_u={'race_id','trainer','sp_decimal','finish_pos'}
    if not required_r.issubset(tables.get('historical_races',[])) or not required_u.issubset(tables.get('historical_runs',[])):
        (out/'status.json').write_text(json.dumps({'status':'schema_mapping_needed','message':'Required Stage 2 columns missing'},indent=2));return
    query='''SELECT r.race_date,r.course,u.trainer,u.sp_decimal,u.finish_pos
             FROM historical_runs u JOIN historical_races r ON u.race_id=r.id
             WHERE r.race_date IS NOT NULL ORDER BY r.race_date,r.id'''
    hist=collections.defaultdict(lambda:[0,0]);overall=[0,0,0.0];years=collections.defaultdict(lambda:[0,0,0.0]);bad=collections.Counter()
    current_date=None;pending=[];seen=0
    def commit_day():
        for key,win in pending:
            hist[key][0]+=1;hist[key][1]+=win
    for date,course,trainer,sp,finish in con.execute(query):
        seen+=1;d=str(date)[:10]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',d):bad['bad_date']+=1;continue
        if current_date is not None and d!=current_date:commit_day();pending=[]
        current_date=d
        if not trainer or not course:bad['missing_trainer_or_course']+=1;continue
        price=odds_decimal(sp)
        if price is None:bad['unparsed_sp']+=1;continue
        win=int(str(finish).strip().lower() in ('1','1st','1.0'))
        key=(str(trainer).strip().casefold(),str(course).strip().casefold())
        runs,wins=hist[key];year=int(d[:4])
        if 2023<=year<=2025 and runs>=30 and wins/runs>=.20:
            for m in (overall,years[year]):m[0]+=1;m[1]+=win;m[2]+=price*win-1
        pending.append((key,win))
    report={'status':'complete','source_tables':['historical_races','historical_runs'],'rows_scanned':seen,'assumptions':['One unit stake per runner at stored decimal SP; no commission or price slippage.','Same-day outcomes excluded from prior trainer/course history.','Trainer names are not disambiguated; exploratory only.','Only 2023-2025 evaluated; earlier data used as training history.','Dead heats and missing SP may need further treatment.'],'baseline':{'trainer_course_30runs_20pct':summary(overall)},'by_year':{str(y):summary(v) for y,v in sorted(years.items())},'excluded':dict(bad)}
    (out/'baseline_report.json').write_text(json.dumps(report,indent=2))
    with (out/'baseline_by_year.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['year','bets','wins','profit_units','strike_rate_pct','roi_pct'])
        for year,v in sorted(years.items()):
            s=summary(v);w.writerow([year,s['bets'],s['wins'],s['profit_units'],s['strike_rate_pct'],s['roi_pct']])
    (out/'status.json').write_text(json.dumps({'status':'complete','rows_scanned':seen},indent=2))
    print('Stage 3 complete:',json.dumps(summary(overall)),'rows scanned:',seen,flush=True)
if __name__=='__main__':main()
