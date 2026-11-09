#!/usr/bin/env python3
"""Stage 78: independently compare Stage65E SQLite to exact original Kaggle CSV.

Read-only. No model rankings, live data, odds, training, or selection changes.
The Kaggle file SHA256 is compared to the hashes captured at Stage65E import.
All source runner rows, raw_json values and 18 typed field mappings are checked.
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3

# (Database column, exact original Kaggle CSV column)
MAPPING = [
    ('race_date', 'date'), ('course', 'course'), ('race_id', 'race_id'),
    ('horse', 'horse'), ('finish', 'pos'), ('trainer', 'trainer'),
    ('jockey', 'jockey'), ('weight', 'wgt'), ('official_rating', 'or'),
    ('rpr', 'rpr'), ('ts', 'ts'), ('beaten', 'btn'),
    ('overall_beaten', 'ovr_btn'), ('race_class', 'class'),
    ('race_type', 'type'), ('going', 'going'), ('distance', 'dist'), ('sp', 'sp'),
]

def present(v):
    return v is not None and str(v).strip() != ''

def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def audit(db, src, out):
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect('file:' + db.resolve().as_posix() + '?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    check = con.execute('PRAGMA quick_check').fetchone()[0]
    if check != 'ok':
        raise RuntimeError('SQLite quick_check failed: ' + str(check))
    cols = {x[1] for x in con.execute('PRAGMA table_info(historical_runs)')}
    if not {'raw_json', 'source_file', 'source_line'} | {x for x, _ in MAPPING} <= cols:
        raise RuntimeError('Stage65E columns absent: ' + str(sorted(cols)))
    catalog = collections.defaultdict(collections.Counter)
    mapped = collections.defaultdict(collections.Counter)
    mismatches = collections.Counter()
    mismatches_by_file = []
    samples = []
    source_rows = 0
    db_rows_matched = 0
    file_reports = []

    def sample(issue, path, line, field='', source_value='', db_value=''):
        if len(samples) < 150:
            samples.append({'issue': issue, 'source_file': path, 'source_line': line,
                            'field': field, 'source_value': str(source_value)[:250],
                            'database_value': str(db_value)[:250]})

    files = con.execute('SELECT path,sha256,rows FROM import_files ORDER BY path').fetchall()
    if not files:
        raise RuntimeError('Stage65E import_files manifest is empty')
    projection = ','.join('"' + c + '"' for c, _ in MAPPING)
    for rel, expected_sha, imported_rows in files:
        fpath = src / rel
        if not fpath.exists():
            file_reports.append({'source_file': rel, 'expected_sha256': expected_sha,
                                 'actual_sha256': '', 'sha256_match': False,
                                 'source_rows': 0, 'database_manifest_rows': imported_rows,
                                 'matched_rows': 0, 'missing_database_rows': 0,
                                 'orphan_database_rows': 0, 'note': 'SOURCE_FILE_NOT_FOUND'})
            mismatches['missing_source_files'] += 1
            continue
        sha = digest(fpath)
        sha_ok = sha == expected_sha
        if not sha_ok:
            mismatches['source_file_hash_mismatch'] += 1
        db_iter = iter(con.execute(
            f'SELECT source_line,raw_json,{projection} FROM historical_runs WHERE source_file=? ORDER BY source_line',
            (rel,)))
        dbrow = next(db_iter, None)
        file_found = file_matched = file_orphan = file_missing = 0
        with fpath.open('r', encoding='utf-8-sig', errors='replace', newline='') as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            if not set(src_field for _, src_field in MAPPING).issubset(set(headers)):
                mismatches['missing_source_csv_columns'] += 1
                sample('missing_source_csv_columns', rel, 1, source_value=','.join(headers))
            for line, row in enumerate(reader, 2):
                if not row or not row.get('horse'):
                    continue
                source_rows += 1
                file_found += 1
                while dbrow is not None and dbrow[0] < line:
                    mismatches['orphan_db_runner_rows'] += 1
                    file_orphan += 1
                    sample('orphan_db_runner', rel, dbrow[0])
                    dbrow = next(db_iter, None)
                if dbrow is None or dbrow[0] > line:
                    mismatches['source_runner_missing_from_database'] += 1
                    file_missing += 1
                    sample('source_runner_missing_from_database', rel, line)
                    continue
                file_matched += 1
                db_rows_matched += 1
                try:
                    stored_raw = json.loads(dbrow[1])
                except (TypeError, ValueError):
                    stored_raw = {}
                    mismatches['invalid_raw_json'] += 1
                    sample('invalid_raw_json', rel, line)
                for name, original in row.items():
                    if not isinstance(name, str):
                        continue
                    cat = catalog[name]
                    cat['source_rows'] += 1
                    if present(original):
                        cat['source_present'] += 1
                    if name in stored_raw:
                        cat['raw_json_key_present'] += 1
                        if present(stored_raw[name]):
                            cat['raw_json_value_present'] += 1
                    if name not in stored_raw or stored_raw[name] != original:
                        cat['raw_json_mismatch'] += 1
                        mismatches['raw_json_field_mismatches'] += 1
                        sample('raw_json_mismatch', rel, line, name, original, stored_raw.get(name))
                for index, (dest, from_csv) in enumerate(MAPPING, 2):
                    original = row.get(from_csv)
                    database = dbrow[index]
                    m = mapped[dest]
                    m['source_rows'] += 1
                    if present(original):
                        m['source_present'] += 1
                    if present(database):
                        m['database_present'] += 1
                    if not present(original) and not present(database):
                        m['both_blank'] += 1
                    elif str(original or '') != str(database or ''):
                        if present(original) and not present(database):
                            m['source_nonblank_database_blank'] += 1
                            mismatches['source_to_db_lost_value'] += 1
                            sample('source_to_db_lost_value', rel, line, dest, original, database)
                        elif not present(original) and present(database):
                            m['source_blank_database_nonblank'] += 1
                            mismatches['source_to_db_added_value'] += 1
                            sample('source_to_db_added_value', rel, line, dest, original, database)
                        else:
                            m['source_to_db_value_changed'] += 1
                            mismatches['source_to_db_value_changed'] += 1
                            sample('source_to_db_value_changed', rel, line, dest, original, database)
                    else:
                        m['matching_nonblank'] += 1
                dbrow = next(db_iter, None)
                if source_rows % 250000 == 0:
                    print(f'Compared {source_rows:,} source runner rows', flush=True)
        while dbrow is not None:
            mismatches['orphan_db_runner_rows'] += 1
            file_orphan += 1
            sample('orphan_db_runner', rel, dbrow[0])
            dbrow = next(db_iter, None)
        if file_found != imported_rows:
            mismatches['source_manifest_count_disagreements'] += 1
        report = {'source_file': rel, 'expected_sha256': expected_sha, 'actual_sha256': sha,
                  'sha256_match': sha_ok, 'source_rows': file_found,
                  'database_manifest_rows': imported_rows, 'matched_rows': file_matched,
                  'missing_database_rows': file_missing, 'orphan_database_rows': file_orphan,
                  'note': ''}
        file_reports.append(report)
        print(f"SOURCE {rel}: hash_match={sha_ok} source_rows={file_found} db_matched={file_matched} "
              f"db_missing={file_missing} db_orphan={file_orphan}", flush=True)

    total_db = con.execute('SELECT COUNT(*) FROM historical_runs').fetchone()[0]
    con.close()
    csv_field_rows = [dict(field=k,**v) for k,v in sorted(catalog.items())]
    field_rows = []
    for name, src_name in MAPPING:
        row = dict(field=name,source_field=src_name)
        row.update(mapped[name])
        field_rows.append(row)
    write_csv(out/'mapped_field_comparison.csv', field_rows,
              ['field','source_field','source_rows','source_present','database_present','matching_nonblank',
               'both_blank','source_nonblank_database_blank', 'source_blank_database_nonblank', 'source_to_db_value_changed'])
    write_csv(out/'all_source_field_retention.csv', csv_field_rows,
              ['field','source_rows','source_present','raw_json_key_present','raw_json_value_present','raw_json_mismatch'])
    write_csv(out/'source_file_integrity.csv', file_reports,
              ['source_file','expected_sha256','actual_sha256','sha256_match','source_rows',
               'database_manifest_rows','matched_rows','missing_database_rows','orphan_database_rows','note'])
    write_csv(out/'discrepancy_samples.csv', samples,
              ['issue','source_file','source_line','field','source_value','database_value'])
    source_hashes_ok = all(x['sha256_match'] for x in file_reports)
    source_to_database_exact = (
        source_rows == total_db == db_rows_matched and source_hashes_ok and not any(mismatches.values()))
    summary = {
        'stage':78, 'generated_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'status':'PASS' if source_to_database_exact else 'REVIEW_REQUIRED',
        'mode':'READ_ONLY_NO_LIVE_MODEL_CHANGES',
        'source':'Kaggle deltaromeo/horse-racing-results-ukireland-2015-2025 version 129',
        'source_files_checked':len(file_reports), 'source_sha256_all_match':source_hashes_ok,
        'runner_rows_original_source':source_rows,'runner_rows_database':total_db,
        'runner_rows_exactly_matched':db_rows_matched,
        'source_to_db_discrepancies':dict(mismatches),
        'mapped_database_columns_checked':len(MAPPING),
        'original_csv_fields_checked':len(catalog),
        'source_fields_retained_in_raw_json':[k for k in sorted(catalog)],
        'source_fields_without_dedicated_database_column':sorted(set(catalog)-{s for _,s in MAPPING}),
        'per_field_source_to_db_lost_values':{x['field']:x.get('source_nonblank_database_blank',0) for x in field_rows},
        'per_field_missing_in_original_source':{x['field']:source_rows-x.get('source_present',0) for x in field_rows},
        'per_field_retained_raw_json_missing_values':{x['field']:source_rows-x.get('raw_json_value_present',0) for x in csv_field_rows},
        'safety':'Audit reads historical DB and downloaded source only. No files in data/, model code, or selections are changed.',
        'limitations':[
            'Matching source CSV proves source-to-Stage65E preservation but not external fixture completeness or source accuracy',
            'Unpopulated source values cannot be recovered without a second licensed, independently verified source',
            'The legacy Stage2 database is separate; this audit does not assume its rows were all equivalent',
            'Live API response retention is not covered unless original responses were independently archived'
        ]
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in (
        'source_fields_retained_in_raw_json','per_field_missing_in_original_source',
        'per_field_retained_raw_json_missing_values','limitations')}, indent=2),flush=True)
    return summary

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',required=True,type=pathlib.Path)
    p.add_argument('--source-dir',required=True,type=pathlib.Path)
    p.add_argument('--out',default='reports/stage78',type=pathlib.Path)
    a=p.parse_args()
    audit(a.db,a.source_dir,a.out)

if __name__=='__main__':
    main()
