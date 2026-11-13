#!/usr/bin/env python3
"""Stage112: model form-history coverage audit and safe paper-only bridge.

Purpose: restore 2026 verified historical runs between a frozen 2025 G3/G4
form-state snapshot and the latest date of an immutable Stage2 SQLite archive.
After that cutoff, original G3/G4 paper scorers can replay HPR revisions; no
actual future results, synthetic horse runs, SP, prices or odds are used.

This script does not mutate models, HPR, the master SQLite or production files.
It writes research-only CSV/JSON, and an optional G4-compatible FORM STATE
snapshot for paper-mode evaluation. A GitHub workflow can run it for dates
strictly after the archived history and before off-time.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, os, pathlib, re, sqlite3, sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
UTC=dt.timezone.utc
NONRUN={'nr','nonrunner','non-runner','withdrawn','scratched','did not start','dns'}
VALID_OUTCOME={'ran','f','fell','pu','pulled up','ur','unseated','bd','brought down','rr','ro','refused','ran out','disqualified','dsq'}


def day(s):
    try:return dt.date.fromisoformat(str(s)[:10])
    except (TypeError,ValueError):return None


def timestamp(s):
    try:
        x=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return x.replace(tzinfo=UTC) if x.tzinfo is None else x.astimezone(UTC)
    except (TypeError,ValueError,AttributeError):return None


def has_public_form(row):
    """Sign of a known previous run, NOT proof that archive has one."""
    value=str(row.get('form') or '').strip()
    if re.search(r'[0-9]',value):return True
    last=str(row.get('last_run') or '').strip()
    return bool(re.fullmatch(r'[0-9]+',last))


def is_starter(row):
    return str(row.get('number') or '').upper()!='NR' and str(row.get('race_status') or '').lower() not in NONRUN


def race_key(row):
    t=timestamp(row.get('race_time'))
    return (str(row.get('date') or row.get('race_time') or '')[:10],
            re.sub(r'[^A-Z0-9]','',str(row.get('course') or '').upper().replace('(AW)','')),
            t.strftime('%H:%M') if t else str(row.get('race_time') or '')[-5:])


def valid_run(row):
    """Only add verified pre-race starters with an observed result."""
    status=str(row.get('outcome') or '').strip().lower()
    if status in NONRUN:return False
    pos=row.get('finish_pos')
    try: p=int(pos);return p>=1
    except (ValueError,TypeError):return status in VALID_OUTCOME


def archive_date_bounds(path, frozen_end, target):
    uri='file:'+str(pathlib.Path(path).resolve())+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        row=db.execute('''SELECT MIN(race_date),MAX(race_date),COUNT(*) FROM historical_races
                   WHERE race_date>? AND race_date<?''',(frozen_end,target)).fetchone()
    return row


def add_archive(state, archive, first, last, G):
    stats=collections.Counter();unresolved=[];seen=set()
    if first>last:return dict(stats),unresolved
    uri='file:'+str(pathlib.Path(archive).resolve())+'?mode=ro'
    con=sqlite3.connect(uri,uri=True)
    con.execute('PRAGMA query_only=ON')
    try:
        # G.from_db is the existing, audited historical-record adapter. This
        # invocation is strictly date bounded; it cannot fetch the target day.
        for group in G.from_db(con,first,last):
            stats['races_seen']+=1
            actual=[r for r in group if valid_run(r)]
            n=len(actual)
            if n<2:
                stats['races_without_two_verified_starters']+=1
                continue
            for r in actual:
                rid=str(r.get('race_id') or '')
                unique=(str(r.get('date') or ''),rid,str(r.get('runner_key') or ''),str(r.get('horse') or ''))
                if unique in seen:
                    stats['duplicate_source_rows_ignored']+=1;continue
                seen.add(unique)
                key=G.identity(r)
                if not key:
                    stats['no_safe_identity']+=1
                    if len(unresolved)<60:unresolved.append({'day':r.get('date'),'race_id':rid,'horse':r.get('horse'),'issue':'NO_SAFE_IDENTITY'})
                    continue
                state.add(r,G.day(r['date']),n)
                stats['prior_2026_starters_added']+=1
                if key.startswith('P|'):stats['pedigree_identity_added']+=1
                else:stats['id_only_identity_added']+=1
    finally:con.close()
    return dict(stats),unresolved


def load_cards(path,target):
    obj=json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    raw=obj.get('runners',[]) if isinstance(obj,dict) else obj
    if not isinstance(raw,list):raise RuntimeError('Racecard schema must have runners[]')
    selected=[]
    for row in raw:
        if not isinstance(row,dict) or not is_starter(row):continue
        d=day(row.get('date') or row.get('race_time'))
        if d and d.isoformat()==target:selected.append(row)
    if not selected:raise RuntimeError('No runners found for target day '+target)
    return selected,obj.get('collected_at') if isinstance(obj,dict) else None


def report_state(rows,state,G,source):
    records=[]
    for r in rows:
        k=G.identity(r)
        hist=list(state.horses.get(k,[])) if k else []
        d=day(r.get('date') or r.get('race_time'))
        previous=sum(1 for z in hist if z.get('date') and z['date']<d)
        records.append({'date':d.isoformat(),'course':r.get('course'),'off_utc':race_key(r)[-1],
            'race_key':'|'.join(race_key(r)),'horse':r.get('horse'),
            'horse_id':r.get('horse_id'),'pedigree_present':int(bool(r.get('sire') and r.get('dam'))),
            'public_form':str(r.get('form') or ''),'has_previous_form_on_card':int(has_public_form(r)),
            'identity_key':k,'matched_identity_type':('PEDIGREE' if k.startswith('P|') else 'ID' if k.startswith('ID|') else 'NONE'),
            'prior_runs':previous,'prior_form_matched':int(previous>0),
            'reported_prior_but_unmatched':int(has_public_form(r) and previous==0),
            'classification':('KNOWN_RUN_MATCHED' if previous>0 else
                'EXPECTED_FORM_MISSING' if has_public_form(r) else 'POSSIBLE_DEBUT'),
            'source':source})
    return records


def aggregate(records,threshold=.95):
    bucket=collections.defaultdict(list)
    for r in records:bucket[r['race_key']].append(r)
    res=[]
    for key,r in sorted(bucket.items()):
        total=len(r);p=sum(x['prior_form_matched'] for x in r)
        expected=sum(x['has_previous_form_on_card'] for x in r)
        matched_expected=sum(x['prior_form_matched']*x['has_previous_form_on_card'] for x in r)
        shortfall=expected-matched_expected
        res.append({'race_key':key,'date':r[0]['date'],'course':r[0]['course'],
            'off_utc':r[0]['off_utc'],'runners':total,
            'reported_prior_form_runners':expected,'confirmed_prior_runners':p,
            'reported_prior_form_confirmed':matched_expected,
            'reported_prior_form_unmatched':shortfall,
            'all_runner_coverage_pct':round(100*p/total,2),
            'form_reconciliation_pct':round(100*matched_expected/expected,2) if expected else None,
            'below_55pct_all_runner_coverage':int(p/total<.55),
            'form_reconciliation_below_95pct':int(expected>0 and matched_expected/expected<threshold),
            'suspected_missing_form':int(shortfall>0)})
    return res


def writecsv(path,rows):
    if not rows:return
    with open(path,'w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def hpr_coverage(records, hpr_path, target):
    """Read independent HPR prior ratings; the ledger is not a full-runs archive."""
    if not hpr_path or not pathlib.Path(hpr_path).exists():
        for row in records:row['prior_hpr_rating_races']=None
        return {'status':'NO_LEDGER'}
    conn=sqlite3.connect('file:'+str(pathlib.Path(hpr_path).resolve())+'?mode=ro',uri=True)
    try:
        unique={}
        for row in records:
            key=row.get('identity_key','')
            if key not in unique:
                sql='SELECT COUNT(DISTINCT race_key) FROM hpr_ratings WHERE horse_key=? AND race_date<?'
                unique[key]=int(conn.execute(sql,(key,target)).fetchone()[0]) if key.startswith('P|') else 0
            row['prior_hpr_rating_races']=unique[key]
        return {'status':'LEDGER_READ_ONLY',
                'runners_with_hpr':sum(r['prior_hpr_rating_races']>0 for r in records),
                'runners_without_hpr':sum(r['prior_hpr_rating_races']==0 for r in records),
                'declared_prior_form_but_no_hpr':sum(r['has_previous_form_on_card'] and not r['prior_hpr_rating_races'] for r in records),
                'hpr_is_not_complete_history':True}
    finally:conn.close()


def read_json(path,default=None):
    if path and pathlib.Path(path).is_file():
        try:return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
        except (ValueError,OSError):return default
    return default


def cross_model(repo,live_date,hpr_db):
    """Summarise independently verifiable signals, do NOT confuse coverage with ranking existence."""
    root=pathlib.Path(repo)
    assessment=read_json(root/'data/model-assessment-coverage.json',{}) or {}
    today=[r for r in assessment.get('races',[]) if r.get('date')==live_date]
    bymodel=collections.defaultdict(collections.Counter)
    for r in today:
        for model,status in (r.get('models') or {}).items():bymodel[model][status]+=1
    integrity=read_json(root/'data/history-integrity-audit.json',{}) or {}
    counts=((integrity.get('declared_field_coverage') or {}).get('counts') or {})
    hpr_stats={}
    if hpr_db and pathlib.Path(hpr_db).exists():
        with sqlite3.connect('file:'+str(pathlib.Path(hpr_db).resolve())+'?mode=ro',uri=True) as c:
            try:
                hpr_stats={'ledger_rows':c.execute('SELECT COUNT(*) FROM hpr_ratings').fetchone()[0],
                           'latest_day':c.execute('SELECT MAX(race_date) FROM hpr_ratings').fetchone()[0],
                           'coverage_is_model_specific':True}
            except sqlite3.Error:hpr_stats={'error':'HPR schema not readable'}
    return {'model_statuses_from_live_app':{k:dict(v) for k,v in bymodel.items()},
            'master_data':{'coverage_verified_through':integrity.get('coverage_verified_through'),
                 'declared_field_coverage':counts,'coverage_status':(integrity.get('declared_field_coverage') or {}).get('status'),
                 'missing_days':integrity.get('missing_days_count'),
                 'complete_career_records_verified':False},
            'hpr':hpr_stats,
            'D':'Independent frozen D scoring; paper assessment status is NOT runner-form coverage.',
            'D(c)':'Separate Stage83/Stage95 form + type path. Not corrected by G3 state bridge. Run dedicated per-runner feature/source audit.',
            'E/HPR':'Uses HPR-specific prior rating evidence. Missing HPR is not equivalent to never raced.',
            'G2/G3/G4':'Share Stage108 form state and pre-race previous-run gate; new paper bridge addresses missing 2026 master-history segment.'}


def command(args):
    import copy, joblib
    import stage108_model_g2 as G
    target=day(args.date)
    if not target:raise ValueError('Invalid target date')
    snapshot=joblib.load(args.state)
    frozen=str(snapshot.get('asof_after_date') or '')
    if not frozen or frozen>=target.isoformat():raise RuntimeError('Frozen state date must be earlier than target date')
    if not hasattr(snapshot['state'],'horses'):raise RuntimeError('Not an original Stage109/G4 FormState')
    row_min,archive_end,archive_n=archive_date_bounds(args.db,frozen,target.isoformat())
    if not archive_end:raise RuntimeError('No verified archive after frozen state: cannot patch with synthetic form')
    if archive_end>=target.isoformat():raise RuntimeError('Archive contains target/future racing: abort')
    cards,collected_at=load_cards(args.cards,target.isoformat())
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    # Original paper path: 2025 state -> HPR beginning 2026.
    original=copy.deepcopy(snapshot['state'])
    before_hpr=G.load_recent_hpr(original,args.hpr,frozen,target.isoformat()) if args.hpr else {'hpr_db':'not_provided','added':0}
    oldrows=report_state(cards,original,G,'G3_CURRENT_STATE_HPR_ONLY')
    # Repaired: 2025 state -> verified 2026 Stage2 history -> HPR AFTER
    # the archive's final date. The disjoint ranges prevent double counting.
    repaired=copy.deepcopy(snapshot['state'])
    archive_stats,unresolved=add_archive(repaired,args.db,(day(frozen)+dt.timedelta(days=1)).isoformat(),archive_end,G)
    patched={**snapshot,'asof_after_date':archive_end,'state':repaired}
    patched_path=out/'stage112-g4-compatible-archived-form-state.joblib'
    joblib.dump(patched,patched_path,compress=3)
    after_hpr=G.load_recent_hpr(repaired,args.hpr,archive_end,target.isoformat()) if args.hpr else {'hpr_db':'not_provided','added':0}
    newrows=report_state(cards,repaired,G,'MASTER_ARCHIVE_2026_PLUS_POST_ARCHIVE_HPR')
    hpr_crosscheck=hpr_coverage(newrows,args.hpr,target.isoformat())
    oldrace=aggregate(oldrows);newrace=aggregate(newrows)
    prior_index={(z['race_key'],z['horse']):z for z in oldrows}
    for row in newrows:
        old=prior_index.get((row['race_key'],row['horse']))
        row['original_prior_runs']=old['prior_runs'] if old else None
        row['newly_matched_prior_runs']=max(0,row['prior_runs']-(old['prior_runs'] if old else 0))
    oldmap={r['race_key']:r for r in oldrace}
    for row in newrace:
        old=oldmap.get(row['race_key'])
        row['previous_reported_prior_form_unmatched']=old['reported_prior_form_unmatched'] if old else None
        row['previous_all_runner_coverage_pct']=old['all_runner_coverage_pct'] if old else None
        row['recovered_form_runners']=((old['reported_prior_form_unmatched']-row['reported_prior_form_unmatched']) if old else None)
    writecsv(out/'stage112-runner-form-reconciliation.csv',newrows)
    writecsv(out/'stage112-race-form-reconciliation.csv',newrace)
    writecsv(out/'stage112-original-paper-form-coverage.csv',oldrace)
    flags={'source_snapshot':collected_at,'target_day':target.isoformat(),
      'original_state_through':frozen,'master_archive_through':archive_end,
      'historical_db_races_in_bridge':archive_n,'archive_ingestion':archive_stats,
      'unmatched_archive_identity_sample':unresolved,'hpr_before':before_hpr,
      'hpr_after':after_hpr,'race_count':len(newrace),
      'races_under_55pct_before':sum(r['below_55pct_all_runner_coverage'] for r in oldrace),
      'races_under_55pct_after':sum(r['below_55pct_all_runner_coverage'] for r in newrace),
      'races_with_unmatched_declared_form_before':sum(r['suspected_missing_form'] for r in oldrace),
      'races_with_unmatched_declared_form_after':sum(r['suspected_missing_form'] for r in newrace),
      'runners_with_declared_form_unmatched_before':sum(r['reported_prior_but_unmatched'] for r in oldrows),
      'runners_with_declared_form_unmatched_after':sum(r['reported_prior_but_unmatched'] for r in newrows),
      'cross_model_evidence':cross_model(args.repo,target.isoformat(),args.hpr),
      'hpr_per_runner_crosscheck':hpr_crosscheck,
      'safety':{'research_only':True,'retrained_models':False,
       'original_models_modified':False,'historical_source_modified':False,
       'odds_used_in_score':False,'future_results_used':False,
       'provisional_predictions_preserved':True,
       'known_debutants_not_counted_as_missing_without_card_form':True,
       'not_a_full_career_history_guarantee':True}}
    (out/'stage112-form-reconciliation-audit.json').write_text(json.dumps(flags,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({k:flags[k] for k in ('original_state_through','master_archive_through','race_count','races_under_55pct_before','races_under_55pct_after','races_with_unmatched_declared_form_before','races_with_unmatched_declared_form_after','runners_with_declared_form_unmatched_before','runners_with_declared_form_unmatched_after')},indent=2))
    if flags['races_with_unmatched_declared_form_after']:
       print('WARNING: unresolved prior form remains. Refuse to claim complete coverage.',file=sys.stderr)


def self_test():
    assert has_public_form({'form':'0'})
    assert has_public_form({'form':'--','last_run':'25'})
    assert not has_public_form({'form':'','last_run':''})
    assert not valid_run({'outcome':'NR','finish_pos':None})
    assert valid_run({'outcome':'ran','finish_pos':7})
    sample=[{'race_key':'x','date':'2026-11-13','course':'Dundalk','off_utc':'17:30','prior_form_matched':i<6,'has_previous_form_on_card':i<10} for i in range(11)]
    x=aggregate(sample)[0]
    assert x['all_runner_coverage_pct']==54.55
    assert x['form_reconciliation_pct']==60.0
    assert x['reported_prior_form_unmatched']==4
    print('Stage112 self-test passed: no fake debut records, exact 6/11 coverage and four unmatched with form')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('self-test')
    q=sub.add_parser('audit')
    for k in ['state','db','cards','date','out','repo']:
        q.add_argument('--'+k,required=True)
    q.add_argument('--hpr')
    args=p.parse_args()
    if args.command=='self-test':self_test()
    else:command(args)

if __name__=='__main__':main()
