#!/usr/bin/env python3
"""Stage167: reconstruct the Stage166B/C validated archive privately per daily specialist job.
No history or source CSVs are published. No training cutoffs, weights or model logic changed.
"""
import pathlib, subprocess, sys, tempfile, importlib.util
from contextlib import contextmanager

DAILY_SOURCE_ROOTS = {
    'reports/stage141-today/source/stage2/historical_ukie_staging.sqlite',
    'reports/stage147-143-146/source/stage2/historical_ukie_staging.sqlite',
    'reports/stage150-d142/source/stage2/historical_ukie_staging.sqlite',
    'reports/stage151-d142/source/stage2/historical_ukie_staging.sqlite',
    'reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite',
    'reports/stage128/source/stage2/historical_ukie_staging.sqlite',
}

def daily_source(history):
    p=pathlib.Path(history).resolve()
    try: return p.relative_to(pathlib.Path.cwd().resolve()).as_posix() in DAILY_SOURCE_ROOTS
    except ValueError: return False

@contextmanager
def refreshed_inputs(history, reference, date):
    if not daily_source(history):
        yield history, reference
        return
    if date <= '2026-11-20':
        raise RuntimeError('Stage167: candidate history overlaps prediction date')
    if importlib.util.find_spec('kagglehub') is None:
        subprocess.run([sys.executable,'-m','pip','install','--disable-pip-version-check','kagglehub>=0.3,<1'],check=True)
    import kagglehub
    src=pathlib.Path(kagglehub.dataset_download('deltaromeo/horse-racing-results-ukireland-2015-2025/versions/132'))
    with tempfile.TemporaryDirectory(prefix='stage167-history-') as td:
        db,ref=build(pathlib.Path(history).resolve(),pathlib.Path(reference).resolve(),src,pathlib.Path(td))
        # STAGE171_RECENT_BRIDGE: G5 has a separate HPR ledger, so do not advance its cutoff on partial dates.
        if 'reports/stage128/source/stage2/' not in pathlib.Path(history).resolve().as_posix():
            # STAGE182C_VERIFIED_SOURCE
            from scripts.stage182c_verified_source import apply as apply_verified_source
            apply_verified_source(db,ref,src,date)
            # STAGE182H_VERIFIED_HISTORY
            from scripts.stage182h_verified_history import apply as apply_final_history
            apply_final_history(db,ref,src,date)
            # STAGE180_ARCHIVE_OVERLAY
            from scripts.stage180_archive_overlay import apply as apply_archive_overlay
            apply_archive_overlay(db,ref,date)
            from stage194_recent_bridge import append_bridge
            append_bridge(db,ref,date)
            from scripts.stage196_verified_backfill import append as backfill_verified
            backfill_verified(db,ref,src,date)
            # STAGE201_VERIFIED_RECENT_HISTORY
            from scripts.stage201_recent_history import append as append_stage201
            append_stage201(db,ref,date)
        yield db,ref

def build(base,ref,source,tmp):
    import csv,datetime as dt,json,os,pathlib,re,sqlite3,shutil,collections
    import import_historical_ukie as I
    import stage4_match_all_recurring_races as R
    src=source
    dest=tmp/'candidate.sqlite'; delta=tmp/'delta.sqlite'; out=tmp
    with sqlite3.connect(f'file:{base}?mode=ro',uri=True) as b:
        before=b.execute('select max(race_date), count(*) from historical_races').fetchone()
        before_runs=b.execute('select count(*) from historical_runs').fetchone()[0]
        assert b.execute('pragma quick_check').fetchone()[0]=='ok'
        assert before==('2026-11-10',505493) and before_runs==5107097, ('Unexpected baseline; refuse unvalidated refresh',before,before_runs)
    first=min('2026-10-23',(dt.date.fromisoformat(before[0])+dt.timedelta(days=1)).isoformat()); last='2026-11-20'
    assert first<=last and first>'2026-09-30',first
    print('Baseline cutoff',before[0],'reconciliation window',first,last,flush=True)
    # Source-identical race IDs: collect factual titles, detect contradictory source duplicates.
    names={}; bad=set(); runner_seen={}; counters=collections.Counter()
    for filename,opener in I.source_iter(src):
        with opener() as f:
            reader=csv.DictReader(f); m=I.mapping(reader.fieldnames or [])
            if not all(m.get(k) for k in ('race_date','course','horse','finish','race_id')): continue
            for row in reader:
                d=I.dateparse(I.value(row,m,'race_date')) if first<=str(row.get(m['race_date'],'')).strip()[:10]<=last else None
                if not d or not first<=d<=last:continue
                course=I.value(row,m,'course'); raw=I.value(row,m,'race_id'); horse=I.value(row,m,'horse')
                if not course or not raw or not horse:continue
                rid=I.SOURCE+':'+d+':'+I.normhead(course)+':'+re.sub('[^a-zA-Z0-9_-]','',raw)[:90]
                name=str(row.get('race_name') or '').strip(); region=R.region(course)
                facts=(d,course,I.value(row,m,'race_type'),name,region,raw)
                if rid in names and names[rid]!=facts:bad.add(rid)
                names[rid]=facts
                hid=I.value(row,m,'horse_id'); key=(rid,'id:'+hid if hid else 'name:'+I.normname(horse))
                signature=tuple(I.value(row,m,k) for k in ('horse','country','foaled','finish','trainer','jockey','rating','weight','sp','draw','age','sex','sire','dam','rpr','ts','comment','beaten'))
                if key in runner_seen:
                    if runner_seen[key]!=signature:bad.add(rid)
                    else:counters['identical_duplicate_source_rows']+=1
                runner_seen[key]=signature
    report=I.process(src,delta,first,last,'import')
    assert report['date_max']==last and report['inserted']>0,report
    shutil.copy2(base,dest)
    with sqlite3.connect(dest) as c:
        c.execute('attach database ? as incoming',(str(delta),))
        c.execute('create temp table approved(id text primary key)')
        # Match only exact original source race identifiers; existing result conflicts quarantine the race.
        for rid in c.execute('''select distinct i.race_id from incoming.historical_runs i join historical_runs b on i.race_id=b.race_id and i.runner_key=b.runner_key where i.finish_pos is not b.finish_pos or i.outcome is not b.outcome or i.horse_name_norm is not b.horse_name_norm'''):
            bad.add(rid[0]);counters['baseline_conflict_races']+=1
        # Detect cross-ID duplicate or conflicting race records before any append.
        # A shared runner on the same day and course is a conservative quarantine signal,
        # never a reason to merge two provider race IDs automatically.
        def slot(day,course,horse):
            return (day,R.canonical_course(course),I.normname(horse))
        baseline_slots=collections.defaultdict(set)
        for rid,day,course,horse in c.execute('select r.id,r.race_date,r.course,h.horse_name from historical_races r join historical_runs h on h.race_id=r.id where r.race_date between ? and ?',(first,last)):
            baseline_slots[slot(day,course,horse)].add(rid)
        incoming_slots=collections.defaultdict(set)
        for rid,day,course,horse in c.execute('select r.id,r.race_date,r.course,h.horse_name from incoming.historical_races r join incoming.historical_runs h on h.race_id=r.id'):
            incoming_slots[slot(day,course,horse)].add(rid)
        cross_baseline=set();cross_incoming=set()
        for k,ids in incoming_slots.items():
            for rid in ids:
                if baseline_slots.get(k,set())-{rid}:cross_baseline.add(rid)
                if len(ids)>1:cross_incoming.add(rid)
        counters['cross_id_baseline_quarantine']=len(cross_baseline)
        counters['cross_id_incoming_quarantine']=len(cross_incoming)
        bad.update(cross_baseline|cross_incoming)
        oldref={}
        with ref.open(encoding='utf-8-sig',newline='') as f:
            rr=csv.DictReader(f); fields=rr.fieldnames or []
            assert {'stage2_race_id','date','region_scope','course','race_type','race_name'}<=set(fields)
            for row in rr:
                if first<=row.get('date','')<=last:
                    rid=row['stage2_race_id']; facts=(row['date'],row['course'],row['race_type'],row['race_name'],row['region_scope'])
                    if rid in oldref and oldref[rid]!=facts:bad.add(rid)
                    oldref[rid]=facts
        append=[]
        for rid,d,course,typ in c.execute('select id,race_date,course,race_type from incoming.historical_races'):
            facts=names.get(rid)
            if rid in bad: counters['quarantined_races']+=1;continue
            if not facts or not facts[3] or facts[4] not in ('GB','IRELAND'):
                counters['missing_or_out_of_scope_reference']+=1;continue
            if rid in oldref and oldref[rid] != facts[:5]:
                counters['reference_conflicts']+=1;continue
            c.execute('insert into approved values(?)',(rid,))
            if rid not in oldref:
                append.append(dict(stage2_race_id=rid,date=d,course=course,race_type=typ or '',race_name=facts[3],region_scope=facts[4],source_race_id=facts[5]))
        # Do not overwrite historical performances or training data.
        c.execute('insert or ignore into historical_races select i.* from incoming.historical_races i join approved a on a.id=i.id');counters['new_races']=c.execute('select changes()').fetchone()[0]
        c.execute('insert or ignore into historical_runs select i.* from incoming.historical_runs i join approved a on a.id=i.race_id');counters['new_runners']=c.execute('select changes()').fetchone()[0]
        c.commit()
        latest, races=c.execute('select max(race_date),count(*) from historical_races').fetchone(); runs=c.execute('select count(*) from historical_runs').fetchone()[0]
        assert latest==last and runs==before_runs+counters['new_runners'] and races==before[1]+counters['new_races'],(latest,runs,races,counters)
        assert c.execute('pragma quick_check').fetchone()[0]=='ok'
        assert not c.execute('select count(*) from historical_runs h left join historical_races r on r.id=h.race_id where r.id is null').fetchone()[0]
    with ref.open(encoding='utf-8-sig',newline='') as f,(tmp/'candidate-reference.csv').open('w',encoding='utf-8',newline='') as destref:
        r=csv.DictReader(f); w=csv.DictWriter(destref,fieldnames=fields,extrasaction='ignore'); w.writeheader();w.writerows(r);w.writerows(append)
    # The 21–23 November archive is preserved; do not silently convert unmatched API identities.
    recent={}
    for day in ('2026-11-21','2026-11-22','2026-11-23'):
        p=pathlib.Path('data/racing-results')/(day+'.json')
        rs=[r for r in json.loads(p.read_text()).get('results',[]) if r.get('status')=='final'] if p.exists() else []
        recent[day]={'final_races':len(rs),'runner_rows':sum(len(r.get('runners',[])) for r in rs),
            'races_with_name':sum(bool(r.get('race_name') or r.get('name')) for r in rs),
            'races_with_type':sum(bool(r.get('type') or r.get('race_type')) for r in rs),
            'races_with_source_id':sum(bool(r.get('race_id') or (r.get('external_verification') or {}).get('source_race_id')) for r in rs),
            'runners_with_horse_id':sum(bool(h.get('horse_id')) for r in rs for h in r.get('runners',[])),
            'runners_with_trainer_and_jockey':sum(bool(h.get('trainer') and h.get('jockey')) for r in rs for h in r.get('runners',[]))}
    result={'status':'CANDIDATE_ONLY_NOT_DEPLOYED','baseline_cutoff':before[0],'candidate_cutoff':latest,'first_reconciled_date':first,'kaggle_version':132,'original_races':before[1],'original_runs':before_runs,'candidate_races':races,'candidate_runs':runs,'new_reference_rows':len(append),'counts':dict(counters),'later_archive_not_yet_merged':recent,'note':'Existing historical rows are not overwritten. No model changes. Raw archive not published.'}
    assert (counters['new_races'], counters['new_runners'], counters['quarantined_races']) == (839,8159,143), ('Candidate differs from verified Stage166C',dict(counters))
    result['status']='VALIDATED_V132_USED_BY_DAILY_SPECIALIST'
    print('STAGE167_HISTORY_REFRESH',json.dumps(result),flush=True)
    return dest,tmp/'candidate-reference.csv'
