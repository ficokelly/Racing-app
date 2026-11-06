#!/usr/bin/env python3
"""Stage65L research-only: verified Hot Pots missing-race backfill and frozen Model D replay.
Never changes original DB or live app. Refuses incomplete or ambiguous source data.
"""
import argparse,collections,csv,datetime as dt,json,pathlib,shutil,sqlite3,subprocess,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage65k_two_day_replay as replay
from stage38_model_dz_live import name,clock,time_utc,nonrunner
UTC=dt.timezone.utc

def git(*args):return subprocess.run(['git',*args],capture_output=True,text=True,check=True).stdout

def write_csv(path,rows,fields):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def normtime(v):
    d=time_utc(v)
    return d.astimezone(UTC).strftime('%H:%M') if d else ''

def get_card_at(shas,cache,ts):
    eligible=[(t,s) for t,s in shas if t<ts]
    if not eligible:return None,None
    t,sha=eligible[-1]
    if sha not in cache:
        try:cache[sha]=json.loads(git('show',sha+':data/racecards.json'))
        except Exception:cache[sha]={}
    return cache[sha],sha

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--out',default='reports/stage65l');a=ap.parse_args()
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    baseline=pathlib.Path(a.baseline);candidate=out/'backfilled_research_only.sqlite'
    shutil.copyfile(baseline,candidate)
    cx=sqlite3.connect(candidate)
    cx.execute('PRAGMA foreign_keys=ON')
    results=json.loads(pathlib.Path('data/race-results.json').read_text())['races']
    shas=replay.snapshots();cache={};audit=[];added=[];dates=collections.Counter()
    # Only results before Nov 5 can inform either Nov 5 or Nov 6 predictions.
    for r in sorted(results,key=lambda z:(str(z.get('date')),str(z.get('course')),str(z.get('off_dt')))):
        day=str(r.get('date') or '')[:10]
        if not ('2026-10-08'<=day<='2026-11-04'):continue
        course=str(r.get('course') or '');off=time_utc(r.get('off_dt') or r.get('race_time'))
        row={'date':day,'course':course,'off':str(r.get('off_dt') or ''),'runners':len(r.get('runners') or []),'status':'','source_commit':''}
        if str(r.get('status','')).lower()!='final' or not off or not course:
            row['status']='UNVERIFIED_FINAL_OR_OFF';audit.append(row);continue
        rs=[z for z in r.get('runners',[]) if z.get('horse') and str(z.get('status','')).lower() not in ('nr','nonrunner','non-runner','withdrawn','scratched')]
        if len(rs)<2 or len({name(z['horse']) for z in rs})!=len(rs) or sum(z.get('position')==1 for z in rs)!=1:
            row['status']='INVALID_FINISHERS';audit.append(row);continue
        card,sha=get_card_at(shas,cache,int(off.timestamp()))
        row['source_commit']=sha or ''
        if not card:
            row['status']='NO_PREOFF_CARD';audit.append(row);continue
        runners=[z for z in card.get('runners',[]) if str(z.get('date') or z.get('race_time') or '')[:10]==day and name(z.get('course'))==name(course) and normtime(z.get('race_time'))==off.strftime('%H:%M') and not nonrunner(z)]
        if len(runners)!=len(rs) or {name(z['horse']) for z in runners}!={name(z['horse']) for z in rs}:
            row['status']='NO_EXACT_PREOFF_RUNNER_MATCH';audit.append(row);continue
        # Require complete metadata needed for eight-factor scoring; no post-race guessing.
        if any(not z.get('trainer') or not z.get('jockey') or not z.get('horse') or not (z.get('distance_f') or z.get('distance')) for z in runners):
            row['status']='MISSING_MODEL_D_METADATA';audit.append(row);continue
        # Existing race detection uses full field and course/date, never source-specific race IDs.
        possible=cx.execute('SELECT id FROM historical_races WHERE race_date=? AND lower(course)=lower(?)',(day,course)).fetchall()
        present=False
        for (rid,) in possible:
            names={name(z[0]) for z in cx.execute('SELECT horse_name FROM historical_runs WHERE race_id=?',(rid,))}
            if names=={name(z['horse']) for z in rs}:present=True;break
        if present:
            row['status']='ALREADY_IN_BASELINE';audit.append(row);continue
        # Prevent duplicate records from repeated result snapshots.
        rid='STAGE65L|'+day+'|'+name(course)+'|'+off.strftime('%H%M')
        if cx.execute('SELECT 1 FROM historical_races WHERE id=?',(rid,)).fetchone():
            row['status']='DUPLICATE_RESULT_SNAPSHOT';audit.append(row);continue
        sample=runners[0]
        try:
            cx.execute('INSERT INTO historical_races (id,race_date,course,race_type,surface,distance_f,going,race_class,source) VALUES (?,?,?,?,?,?,?,?,?)',
                (rid,day,course,sample.get('race_type') or sample.get('type') or sample.get('race_name'),sample.get('surface'),float(sample.get('distance_f') or 0),sample.get('going'),sample.get('race_class'),'racing-hot-pots-verified-results-preoff-card'))
            finish={name(z['horse']):z for z in rs}
            for n,z in enumerate(runners):
                f=finish[name(z['horse'])]
                pos=f.get('position');pos=int(pos) if isinstance(pos,(int,float)) and pos>0 else None
                if pos is None:raise ValueError('Missing finish position')
                sp=replay.OLD.number(f.get('sp_decimal'))
                if sp is None:
                    from stage38_model_dz_live import fractional
                    sp=fractional(f.get('sp'))
                cx.execute('''INSERT INTO historical_runs (race_id,runner_key,horse_source_id,horse_name,horse_name_norm,horse_country,finish_pos,outcome,trainer,jockey,official_rating,sp_decimal,draw,age,sire,dam)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(rid,str(n),str(z.get('horse_id') or ''),z['horse'],name(z['horse']),z.get('horse_country') or '',pos,'won' if pos==1 else 'lost',z.get('trainer'),z.get('jockey'),replay.OLD.number(z.get('official_rating') or z.get('or') or z.get('ofr')),sp,int(z['draw']) if str(z.get('draw') or '').isdigit() else None,int(z['age']) if str(z.get('age') or '').isdigit() else None,z.get('sire'),z.get('dam')))
            row['status']='INSERTED';added.append(rid);dates[day]+=1
        except Exception as exc:
            cx.execute('DELETE FROM historical_runs WHERE race_id=?',(rid,));cx.execute('DELETE FROM historical_races WHERE id=?',(rid,))
            row['status']='REJECTED_'+type(exc).__name__
        audit.append(row)
    cx.commit();cx.close()
    write_csv(out/'backfill_audit.csv',audit,['date','course','off','runners','status','source_commit'])
    # Reuse the same verified-preoff scoring/settlement procedure as Stage65K.
    oldargv=sys.argv
    try:
        sys.argv=[oldargv[0],'--baseline',str(baseline),'--corrected',str(candidate),'--out',str(out/'replay')]
        replay.main()
    finally:sys.argv=oldargv
    status=dict(collections.Counter(x['status'] for x in audit))
    report={'research_only':True,'original_database_untouched':True,'inserted_races':len(added),'inserted_by_day':dict(dates),'audit_statuses':status,'critical_caveat':'Pre-off card metadata and exact full fields required; missing/incomplete races are excluded. Racecards commit timestamps are provenance checks, not independently verified provider collection timestamps. Original historical snapshot is reconstructed, not confirmed live DB.'}
    (out/'backfill_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    # Database is research-only, not uploaded; small CSV/JSON reports suffice.
    candidate.unlink(missing_ok=True)

if __name__=='__main__':main()
