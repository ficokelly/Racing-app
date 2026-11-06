#!/usr/bin/env python3
"""Stage65H: read-only recovered-history audit and frozen Model D reference snapshot.

No model training, mutation, merged databases, or production predictions.
This archive is NOT compatible with the production Model D database schema.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3

EXPECTED_BLOBS = {
    'scripts/stage30_gap_aware_abcd.py': 'bf070c268152fe6d8612c59ff4f5c95020e869e0',
    'scripts/stage38_model_dz_live.py': '0bffdb2de69e24300735a76e87f1d8cf5a903bed',
    'reports/stage30/stage30-summary.json': '4b19782055faf79d095f47a833eb8006abe5692e',
}
EXPECTED_METRICS = ['Ability', 'Trainer', 'Jockey', 'Course', 'Draw', 'Distance', 'Ground', 'Jumping']
REQUIRED_LIVE_RACES = {'id','race_date','course','race_type','surface','distance_f','going','race_class'}
REQUIRED_LIVE_RUNS = {'race_id','horse_name_norm','horse_name','horse_country','horse_source_id','finish_pos','outcome','trainer','jockey','official_rating','age','sire','dam','sp_decimal','draw','race_comment'}
RESEARCH_FIELDS = ['race_id','horse','finish','trainer','jockey','weight','official_rating','rpr','ts','beaten','overall_beaten','race_class','race_type','going','distance','sp']


def git_sha(b: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()


def pct(a, b):
    return round(a*100/b, 3) if b else None


def write_csv(path, rows, columns):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def config_snapshot(repo):
    refs=[]
    for relative, expected in EXPECTED_BLOBS.items():
        path=repo/relative
        if path.is_file():
            raw=path.read_bytes()
            actual=git_sha(raw)
            refs.append({'path':relative,'git_blob_sha':actual,'expected_git_blob_sha':expected,
                         'sha256':hashlib.sha256(raw).hexdigest(),'unchanged':actual==expected})
        else:
            refs.append({'path':relative,'expected_git_blob_sha':expected,'unchanged':False,'error':'missing'})
    confpath=repo/'reports/stage30/stage30-summary.json'
    conf={}
    try:
        d=json.loads(confpath.read_text(encoding='utf-8'))
        m=d['model_results']['D']
        conf={'version':m.get('chosen_combination'),'selection_margin':m.get('selection_margin'),
              'weights_pct':m.get('weights_pct'), 'test_year':d.get('test_year'), 'research_status':d.get('status')}
    except (ValueError,KeyError,OSError,TypeError) as e:
        conf={'error':str(e)}
    safe=(all(r.get('unchanged') for r in refs)
          and conf.get('version')=='D-064-gap14'
          and conf.get('selection_margin')==0.14
          and list((conf.get('weights_pct') or {}).keys())==EXPECTED_METRICS)
    return {'source_files':refs,'frozen_configuration':conf,'pinned_baseline_unchanged':safe}


def f_present(field):
    return f'SUM(CASE WHEN "{field}" IS NOT NULL AND trim("{field}")<>\'\' THEN 1 ELSE 0 END) AS "{field}_present"'


def db_audit(db, out, asof):
    cx=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
    cx.execute('PRAGMA query_only=ON')
    check=cx.execute('PRAGMA quick_check').fetchone()[0]
    if check!='ok':raise RuntimeError('Read-only archive failed integrity check: '+str(check))
    tables={r[0] for r in cx.execute('SELECT name FROM sqlite_master WHERE type="table"')}
    if 'historical_runs' not in tables:raise RuntimeError('Expected Stage65E historical_runs table is missing')
    columns={r[1] for r in cx.execute('PRAGMA table_info(historical_runs)')}
    present_fields=[f for f in RESEARCH_FIELDS if f in columns]
    base_cols={'race_date','course','horse','race_id'}
    if not base_cols<=columns:raise RuntimeError('Stage65E base schema incomplete')
    # Stage65E and production stage38 use different tables AND different runner columns.
    live_compatible=('historical_races' in tables and REQUIRED_LIVE_RACES<=
                     {r[1] for r in cx.execute('PRAGMA table_info(historical_races)')}
                     and REQUIRED_LIVE_RUNS<=columns)
    total=cx.execute('SELECT count(*) FROM historical_runs').fetchone()[0]
    date_min,date_max=cx.execute("SELECT min(race_date),max(race_date) FROM historical_runs WHERE race_date GLOB '????-??-??'").fetchone()
    # One GROUP BY scan; do not classify missing class as an erroneous value without source conventions.
    fields_sql=','.join(f_present(x) for x in present_fields)
    detail_sql=("SELECT substr(race_date,1,4) AS year,substr(race_date,1,7) AS month,count(*) AS runner_rows,"
                +fields_sql+" FROM historical_runs GROUP BY year,month ORDER BY year,month")
    month_rows=[]; year_sums=collections.defaultdict(collections.Counter)
    for row in cx.execute(detail_sql):
        year,month,n,*vals=row
        rec={'year':year,'month':month,'runner_rows':n}
        for field,value in zip(present_fields,vals):rec[field+'_present']=value
        month_rows.append(rec)
        year_sums[year]['runner_rows']+=n
        for field,value in zip(present_fields,vals):year_sums[year][field+'_present']+=value
    write_csv(out/'year_month_coverage.csv',month_rows,
              ['year','month','runner_rows']+[f+'_present' for f in present_fields])
    year_rows=[]
    for year in sorted(year_sums):
        d=year_sums[year]
        row={'year':year,'runner_rows':d['runner_rows'],'months_with_rows':sum(1 for x in month_rows if x['year']==year)}
        for f in present_fields:row[f+'_coverage_pct']=pct(d[f+'_present'],d['runner_rows'])
        year_rows.append(row)
    # Track/day inventory supports a later independent calendar reconciliation.
    # It is *not* proof that every race took place in the recovered archive.
    course_days=[]; year_course=collections.defaultdict(lambda: {'races':0,'runner_rows':0,'course_days':0,'dates':set(),'courses':set()})
    for date,course,races,runner_rows in cx.execute('''
        SELECT race_date,course,count(DISTINCT race_id),count(*) FROM historical_runs
        WHERE race_date>='2015-01-01' AND race_date<=?
        GROUP BY race_date,course ORDER BY race_date,course''',(asof,)):
        course_days.append({'date':date,'course':course,'distinct_race_ids':races,'runner_rows':runner_rows})
        yyyy=date[:4];v=year_course[yyyy];v['races']+=races;v['runner_rows']+=runner_rows
        v['course_days']+=1;v['dates'].add(date);v['courses'].add(course)
    write_csv(out/'date_course_race_inventory_2015_present.csv',course_days,
              ['date','course','distinct_race_ids','runner_rows'])
    for row in year_rows:
        v=year_course.get(row['year'])
        row['distinct_races_by_course_day']=v['races'] if v else None
        row['course_days']=v['course_days'] if v else None
        row['unique_dates_with_racing']=len(v['dates']) if v else None
        row['courses_in_source']=len(v['courses']) if v else None
    write_csv(out/'year_feature_coverage.csv',year_rows,
              ['year','runner_rows','months_with_rows','distinct_races_by_course_day','course_days',
               'unique_dates_with_racing','courses_in_source']+[f+'_coverage_pct' for f in present_fields])
    malformed=cx.execute("""SELECT count(*) FROM historical_runs
       WHERE race_date IS NULL OR length(race_date)<>10 OR race_date NOT GLOB '????-??-??'
       OR race_date<'1988-01-01' OR race_date>?""",(asof,)).fetchone()[0]
    bad_headers=cx.execute("""SELECT count(*) FROM historical_runs
       WHERE lower(trim(coalesce(race_date,''))) IN ('date','race_date')
          OR lower(trim(coalesce(horse,'')))='horse'""").fetchone()[0]
    duplicate_groups=cx.execute('''SELECT count(*) FROM (
       SELECT race_date,course,race_id,horse FROM historical_runs
       GROUP BY race_date,course,race_id,horse HAVING count(*)>1)''').fetchone()[0]
    examples=[dict(zip(('race_date','course','race_id','horse','rows'),x)) for x in cx.execute('''
       SELECT race_date,course,race_id,horse,count(*) FROM historical_runs
       GROUP BY race_date,course,race_id,horse HAVING count(*)>1
       ORDER BY count(*) DESC LIMIT 25''')]
    null_keys=cx.execute("""SELECT count(*) FROM historical_runs WHERE
      race_date IS NULL OR trim(race_date)='' OR course IS NULL OR trim(course)=''
      OR race_id IS NULL OR trim(race_id)='' OR horse IS NULL OR trim(horse)=''""").fetchone()[0]
    source_files=[]
    if 'import_files' in tables:
        source_files=[{'path':path,'sha256':sha,'rows':rows} for path,sha,rows in
          cx.execute('SELECT path,sha256,rows FROM import_files ORDER BY path')]
    anomalies=[]
    for y in year_rows:
        yr=y['year']
        if not yr.isdigit():anomalies.append({'year':yr,'issue':'Malformed year label','value':y['runner_rows']});continue
        yi=int(yr)
        if 2015<=yi<=2025 and y['months_with_rows']!=12:
            anomalies.append({'year':yr,'issue':'Missing entire calendar month(s)','value':y['months_with_rows']})
        if 2015<=yi<=2025 and y.get('rpr_coverage_pct') is not None and y['rpr_coverage_pct']<95:
            anomalies.append({'year':yr,'issue':'RPR populated under 95%','value':y['rpr_coverage_pct']})
        if 2015<=yi<=2026 and y.get('race_class_coverage_pct') is not None and y['race_class_coverage_pct']<75:
            anomalies.append({'year':yr,'issue':'Race class populated under 75% (may be unclassified by source)','value':y['race_class_coverage_pct']})
        if yr=='2026' and y.get('rpr_coverage_pct') is not None and y['rpr_coverage_pct']<95:
            anomalies.append({'year':yr,'issue':'Recent-year RPR populated under 95%','value':y['rpr_coverage_pct']})
    complete_years={x['year'] for x in year_rows if x['year'].isdigit()}
    missing_years=[str(y) for y in range(1988,2027) if str(y) not in complete_years]
    cx.close()
    return {'source':'Stage65E detailed history artifact', 'integrity':'ok', 'total_runner_rows':total,
        'earliest_date':date_min,'latest_date':date_max,'year_rows':year_rows,
        'missing_entire_years':missing_years,'malformed_or_out_of_range_dates':malformed,
        'header_like_rows':bad_headers,'null_key_rows':null_keys,'duplicate_race_horse_groups':duplicate_groups,
        'duplicate_examples':examples,'source_files':source_files,'schema':{
          'tables':sorted(tables),'historical_runs_columns':sorted(columns),
          'production_stage38_compatible':live_compatible,
          'missing_production_runner_fields':sorted(REQUIRED_LIVE_RUNS-columns),
          'missing_production_race_table': 'historical_races' not in tables},
        'issues':anomalies,
        'limitations':['A month with rows does not establish complete race coverage','Source years/fields are not independently reconciled against a racing calendar','Race-class blanks may represent races without an assigned class','Duplicates require exact race/horse identity checks before deduplication']}


def settled(s):
    if s=='Won':return True
    if s in ('Lost','Placed'):return False
    return None


def recent_reference(repo,out,days):
    records=[]; sources=[]; counts=collections.Counter()
    for day in days:
        path=repo/f'data/model-performance/{day}.json'
        if not path.exists():
            sources.append({'date':day,'status':'FILE_MISSING'});continue
        raw=path.read_bytes()
        sources.append({'date':day,'status':'AVAILABLE','sha256':hashlib.sha256(raw).hexdigest(),
                        'git_blob_sha':git_sha(raw)})
        try: data=json.loads(raw)
        except ValueError as e:
            sources[-1]['error']=str(e);continue
        if data.get('day')!=day:sources[-1]['warning']='Date mismatch'
        races=(data.get('models') or {}).get('D') or []
        sources[-1]['D_race_records']=len(races)
        for race in races:
            kind=race.get('race_type') or 'unknown'
            ranks={}
            for horse in race.get('runners') or []:
                rank=horse.get('rank')
                if rank in (1,2) and rank not in ranks:ranks[rank]=horse
            if not ranks:counts[(day,'unranked_D_records')]+=1
            for rank in (1,2):
                runner=ranks.get(rank)
                if runner is None:continue
                outcome=settled(runner.get('status'))
                try:
                    sp=float(runner.get('sp_decimal'))
                    if not 1.01<=sp<=5000:sp=None
                except (TypeError,ValueError):sp=None
                if outcome is not None and sp is not None:
                    gross=sp if outcome else 0.
                    records.append({'date':day,'discipline':kind,'course':race.get('course'),
                        'time':race.get('time'),'rank':rank,'horse':runner.get('horse'),
                        'win':int(outcome),'sp_decimal':sp,'units_returned':gross,
                        'pre_off_valid':race.get('pre_off_valid'),'model_version':race.get('model_version'),
                        'snapshot_at':race.get('snapshot_at')})
                else:counts[(day,'unsettled_or_unpriced_rank_'+str(rank))]+=1
    write_csv(out/'recent_model_d_frozen_ranked_reference.csv',records,
        ['date','discipline','course','time','rank','horse','win','sp_decimal',
         'units_returned','pre_off_valid','model_version','snapshot_at'])
    aggregated=collections.defaultdict(list)
    for r in records:
        for scope in [('day',r['date']),('discipline',r['discipline']),('day_discipline',r['date']+'|'+r['discipline']),('both_days','all')]:
            aggregated[scope+('rank'+str(r['rank']),)].append(r)
            aggregated[scope+('combined_top_two',)].append(r)
    breakdown=[]
    for (group,value,view),items in sorted(aggregated.items()):
        n=len(items);wins=sum(r['win'] for r in items);gross=sum(r['units_returned'] for r in items)
        breakdown.append({'group':group,'value':value,'view':view,'priced_settled_bets':n,
            'winners':wins,'strike_pct':pct(wins,n),'returns_units':round(gross,3),
            'profit_units':round(gross-n,3),'sp_roi_pct':round((gross/n-1)*100,3)})
    write_csv(out/'recent_model_d_reference_summary.csv',breakdown,
              ['group','value','view','priced_settled_bets','winners','strike_pct',
               'returns_units','profit_units','sp_roi_pct'])
    return {'sources':sources,'excluded_counts':{str(k):v for k,v in counts.items()},
            'priced_settled_ranked_bets':len(records), 'summaries':breakdown,
            'warning':'This is a dated recorded-pre-off snapshot, not a before/after training comparison. Missing unranked/unsettled data are never treated as losses.'}


def self_test():
    assert git_sha(b'test')=='30d74d258442c7c65512eafab474568dd706c430'
    assert pct(1,4)==25
    assert settled('Won') and settled('Lost') is False and settled('Non-runner') is None
    print('Stage65H self-test passed')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=pathlib.Path)
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage65h'))
    p.add_argument('--repo',type=pathlib.Path,default=pathlib.Path('.'))
    p.add_argument('--asof',default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    p.add_argument('--days',default='2026-11-05,2026-11-06')
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test();return
    if not a.db or not a.db.is_file():p.error('Stage65E historical SQLite database required')
    dt.date.fromisoformat(a.asof)
    a.out.mkdir(parents=True,exist_ok=True)
    base=config_snapshot(a.repo)
    history=db_audit(a.db,a.out,a.asof)
    reference=recent_reference(a.repo,a.out,[x.strip() for x in a.days.split(',') if x.strip()])
    blockers=[]
    if not base['pinned_baseline_unchanged']:blockers.append('Frozen Model D code or configuration has drifted')
    if not history['schema']['production_stage38_compatible']:blockers.append('Recovered Stage65E schema cannot directly feed production Stage38 Model D')
    if history['header_like_rows'] or history['malformed_or_out_of_range_dates']:blockers.append('Malformed historical records require source correction')
    if history['duplicate_race_horse_groups']:blockers.append('Potential duplicate historical records require identity resolution')
    if history['missing_entire_years']:blockers.append('Entire history year(s) absent')
    if history['latest_date'] and history['latest_date']<a.asof:
        blockers.append('Recovered history is not current to '+a.asof+'; live archive reconciliation required')
    blockers.append('Independent calendar and race/runner reconciliation not yet completed')
    blockers.append('No controlled chronological challenger backtest versus frozen Model D has been completed')
    report={'status':'AUDIT_FINISHED_PROMOTION_BLOCKED',
            'generated_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'no_live_model_changes':True,'promotion_allowed':False,
            'pinned_model_d':base,'historical_archive':history,
            'recent_frozen_reference':reference,'promotion_blockers':blockers,
            'next_actions':['Keep frozen live model and National Hunt performance baseline intact',
              'Build source-to-production adapter in isolation only after stable race/horse IDs are validated',
              'Reconcile 2015-present against independent racecards and result calendars',
              'Run chronological out-of-sample challenger with identical starters and settlement for rank1/rank2/top2',
              'Require National Hunt subgroup non-inferiority plus overall ROI/strike audit before any opt-in promotion']}
    (a.out/'stage65h_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (a.out/'promotion_blockers.txt').write_text('\n'.join(blockers)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'total_rows':history['total_runner_rows'],
                      'latest_date':history['latest_date'],'live_compatible':history['schema']['production_stage38_compatible'],
                      'pinned_baseline_unchanged':base['pinned_baseline_unchanged'],
                      'promotion_blockers':blockers},indent=2))


if __name__=='__main__':main()
