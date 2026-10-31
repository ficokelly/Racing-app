#!/usr/bin/env python3
"""Stage 3: schema audit and chronological trainer/course SP baseline."""
import argparse,csv,json,sqlite3,re,collections,datetime,math,os
from pathlib import Path

def ident(s):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',s):raise ValueError('Unsafe identifier')
    return '"'+s+'"'
def price(x):
    if x is None:return None
    s=str(x).strip().lower().replace(' ','')
    if s in ('','nan','none','-','nr','evs','evens','even'):return 2.0 if s in ('evs','evens','even') else None
    try:
        if '/' in s:
            a,b=s.split('/',1);return 1+float(a)/float(b)
        v=float(s);return v if v>=1 else None
    except (ValueError,ZeroDivisionError):return None

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--out',default='stage3_output');a=p.parse_args()
    out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect('file:'+os.path.abspath(a.db)+'?mode=ro',uri=True)
    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    schema={t:[r[1] for r in con.execute('PRAGMA table_info('+ident(t)+')')] for t in tables}
    (out/'schema.json').write_text(json.dumps(schema,indent=2))
    aliases={'date':['race_date','date'],'course':['course','track'],'trainer':['trainer'],'sp':['sp','starting_price'],'finish':['finish','pos','position'],'race':['race_id','race_identifier']}
    selection=None
    for t,cols in schema.items():
        c={k:next((z for z in v if z in cols),None) for k,v in aliases.items()}
        if all(c[k] for k in ('date','course','trainer','sp','finish')):selection=(t,c);break
    if not selection:
        (out/'status.json').write_text(json.dumps({'status':'schema_mapping_needed','message':'No compatible runner table found; see schema.json'},indent=2));return
    t,c=selection
    fields=','.join(ident(c[k]) for k in ('date','course','trainer','sp','finish'))
    query='SELECT '+fields+' FROM '+ident(t)+' WHERE '+ident(c['date'])+' IS NOT NULL ORDER BY '+ident(c['date'])
    # Only evaluate 2023-2025 using trailing trainer/course records through 2022 onward.
    # No same-race information is used to form a prior estimate.
    history=collections.defaultdict(lambda:[0,0]);metrics=collections.defaultdict(lambda:[0,0,0.0]);years=collections.defaultdict(lambda:[0,0,0.0]);bad=collections.Counter();lastdate=''
    for date,course,trainer,sp,finish in con.execute(query):
        d=str(date)[:10]
        if not re.fullmatch(r'\d{4}-\d\d-\d\d',d):bad['bad_date']+=1;continue
        if d!=lastdate and lastdate:
            for k,w in pending:history[k][0]+=1;history[k][1]+=w
        if d!=lastdate:pending=[];lastdate=d
        if not course or not trainer:bad['missing_trainer_or_course']+=1;continue
        odds=price(sp)
        if odds is None:bad['unparsed_sp']+=1;continue
        f=str(finish).strip().lower()
        win=int(f in ('1','1st','1.0'))
        key=(str(trainer).strip().lower(),str(course).strip().lower())
        runs,wins=history[key]
        year=int(d[:4])
        # fixed, predeclared baseline; only years 2023-2025 evaluated.
        if 2023<=year<=2025 and runs>=30 and wins/runs>=0.20:
            m=metrics['trainer_course_30runs_20pct'];m[0]+=1;m[1]+=win;m[2]+=odds*win-1
            y=years[year];y[0]+=1;y[1]+=win;y[2]+=odds*win-1
        pending.append((key,win))
    # Last day history not needed for evaluation.
    def row(v):return {'bets':v[0],'wins':v[1],'profit_units':round(v[2],3),'strike_rate_pct':round(100*v[1]/v[0],3) if v[0] else None,'roi_pct':round(100*v[2]/v[0],3) if v[0] else None}
    result={'status':'complete','table':t,'mapping':c,'assumptions':['SP is assumed fractional when in a/b format; decimal when numeric.','One unit stake per qualifying runner; no commission or price slippage.','Same-day results excluded from prior trainer/course history.','Trainer names are not disambiguated; results are exploratory only.','Only 2023-2025 are evaluated; earlier records are used for prior statistics.','Races with dead heats may need separate settlement logic.'],'baseline':{k:row(v) for k,v in metrics.items()},'by_year':{str(k):row(v) for k,v in sorted(years.items())},'excluded':dict(bad)}
    (out/'baseline_report.json').write_text(json.dumps(result,indent=2))
    with (out/'baseline_by_year.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['year','bets','wins','profit_units','strike_rate_pct','roi_pct']);[(w.writerow([y,*row(v).values()])) for y,v in sorted(years.items())]
    (out/'status.json').write_text(json.dumps({'status':'complete','table':t},indent=2))
if __name__=='__main__':main()
