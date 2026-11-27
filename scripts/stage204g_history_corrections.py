"""Evidence-scoped corrections to disposable shared history; original archive retained."""
import csv, json, pathlib, sqlite3, re
from scripts.stage201_recent_history import state
from scripts.stage86_hpr_engine import norm

REFUSALS = [
 ('kaggle-deltaromeo-ukie:2024-04-27:sandown:864322','2024-04-27','Sandown','Maasai Mara',20,17,12,'https://www.irishracing.com/raceresults/Sat-27th-Apr-2024/Sandown/1350'),
 ('kaggle-deltaromeo-ukie:2025-11-08:kelso:905430','2025-11-08','Kelso','Benson',14,11,10,'https://www.irishracing.com/raceresults/Sat-8th-Nov-25/Kelso/1242'),
]
CANCELLED='kaggle-deltaromeo-ukie:2026-09-30:warwick:940920'
CANCELLED_SOURCE='https://prod.thejockeyclub.co.uk/warwick/media/press-releases/2026/09/racing-cancelled/'
CANCELLED_NAMES={norm(x) for x in ['Delpotro','Four Decades','Gaelic Rambler','Move With The Beat','My Friend Sean','Palamon',"Shabalko D'herm",'Start Again','Ukantango']}

def apply(db,ref,src,target):
    report=[]; remove_reference=False
    with sqlite3.connect(db) as c:
        c.execute('pragma foreign_keys=on')
        assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        for rid,day,venue,name,size,finishers,cannock_pos,url in REFUSALS:
            if day>=target: continue
            race=c.execute('select race_date,course from historical_races where id=?',(rid,)).fetchone()
            assert race and race[0]==day and norm(race[1])==norm(venue),('Unexpected race',rid,race)
            rows=c.execute('select runner_key,horse_name,finish_pos,outcome from historical_runs where race_id=? order by runner_key',(rid,)).fetchall()
            assert len(rows)==size and len({norm(x[1]) for x in rows})==size,('Field changed',rid)
            hits=[x for x in rows if norm(x[1])==norm(name)];assert len(hits)==1
            key,horse,pos,outcome=hits[0];assert pos is None and (str(outcome or '').upper() in ('','UNKNOWN','RR','REF','R','REFUSED') or state({'position':pos,'status':outcome})[0]=='REF'),('Unexpected refusal outcome',rid,outcome)
            assert [x[2] for x in rows if norm(x[1])==norm('Cannock Park')]==[cannock_pos]
            other=[state({'position':x[2],'status':x[3]}) for x in rows if x[0]!=key]
            assert all(st not in ('UNKNOWN','NR') for st,_ in other),('Other unresolved field',rid,other)
            assert sorted(p for st,p in other if p is not None)==list(range(1,finishers+1)),('Finishing order mismatch',rid)
            c.execute('update historical_runs set outcome=? where race_id=? and runner_key=?',('REF',rid,key))
            report.append({'race_id':rid,'horse':horse,'previous_outcome':outcome,'corrected_outcome':'REF','source':url})
        if '2026-09-30'<target:
            race=c.execute('select race_date,course from historical_races where id=?',(CANCELLED,)).fetchone()
            rows=c.execute('select horse_name,finish_pos,outcome from historical_runs where race_id=?',(CANCELLED,)).fetchall()
            if race:
                assert race[0]=='2026-09-30' and norm(race[1])==norm('Warwick')
                assert len(rows)==9 and {norm(x[0]) for x in rows}==CANCELLED_NAMES,('Cancelled field changed',rows)
                assert all(p is None and state({'position':p,'status':st})[0]=='UNKNOWN' for h,p,st in rows),('Recorded result exists: refuse deletion',rows)
                # Exclude cancelled declarations in the derived database only. Do not edit the immutable source archive.
                c.execute('delete from historical_runs where race_id=?',(CANCELLED,))
                c.execute('delete from historical_races where id=?',(CANCELLED,))
            else: assert not rows
            remove_reference=True
            report.append({'race_id':CANCELLED,'status':'CANCELLED_EXCLUDED_FROM_DERIVED_HISTORY','source':CANCELLED_SOURCE,'declared_runners':9})
        assert c.execute('pragma quick_check').fetchone()[0]=='ok'
        c.commit()
    if remove_reference:
        p=pathlib.Path(ref);tmp=p.with_suffix(p.suffix+'.204g.tmp')
        with p.open(encoding='utf-8-sig',newline='') as fi,tmp.open('w',encoding='utf-8',newline='') as fo:
            reader=csv.DictReader(fi);assert 'stage2_race_id' in reader.fieldnames
            writer=csv.DictWriter(fo,fieldnames=reader.fieldnames);writer.writeheader()
            for row in reader:
                if row['stage2_race_id']!=CANCELLED:writer.writerow(row)
        tmp.replace(p)
    print('STAGE204G_VERIFIED_HISTORY_CORRECTIONS',json.dumps(report,sort_keys=True),flush=True)
    return report
