#!/usr/bin/env python3
"""Stage 65C: lossless, resumable GB/IE PuntersEdge result + price archive.

Uses only published PuntersEdge endpoints and PUNTERSEDGE_API_KEY. This script
NEVER edits Racing Hot Pots' production SQLite or frozen Model D scoring logic.

Source date is Australia/Sydney, not GB/IE local date. Query a day either side,
then identify the real event date from start_time in Europe/Dublin.

Result field completeness is not assumed: ``placings`` is NOT ``runners``.
The API does not provide beaten margins for British/Irish horses; these remain
explicitly missing rather than being manufactured.
"""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = 'https://api.puntersedge.online'
PROVIDER = 'puntersedge'
LOCAL = ZoneInfo('Europe/Dublin')
UTC = dt.timezone.utc


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def event_day(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        if t.tzinfo is None:
            return None
        return t.astimezone(LOCAL).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def init(c):
    c.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS races (
      provider TEXT NOT NULL, race_id TEXT NOT NULL,
      gbie_day TEXT, api_day TEXT NOT NULL, country TEXT NOT NULL,
      venue TEXT, start_time TEXT, race_name TEXT, race_class TEXT,
      conditions TEXT, distance_m REAL, going TEXT, status TEXT,
      field_status TEXT, full_field_availability TEXT,
      reported_field_size INTEGER, captured_utc TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL, raw_json TEXT NOT NULL,
      PRIMARY KEY (provider,race_id)
    );
    CREATE TABLE IF NOT EXISTS race_versions (
      provider TEXT NOT NULL, race_id TEXT NOT NULL, sha256 TEXT NOT NULL,
      first_captured_utc TEXT NOT NULL, raw_json TEXT NOT NULL,
      PRIMARY KEY (provider,race_id,sha256)
    );
    CREATE TABLE IF NOT EXISTS runners (
      provider TEXT NOT NULL, race_id TEXT NOT NULL, runner_key TEXT NOT NULL,
      horse TEXT, horse_ref TEXT, runner_ref TEXT, number TEXT,
      position INTEGER, status TEXT, official_sp TEXT, trainer TEXT,
      jockey TEXT, weight TEXT, margin TEXT, raw_json TEXT NOT NULL,
      PRIMARY KEY (provider,race_id,runner_key)
    );
    CREATE TABLE IF NOT EXISTS placings (
      provider TEXT NOT NULL, race_id TEXT NOT NULL, placing_key TEXT NOT NULL,
      horse TEXT, horse_ref TEXT, position INTEGER, official_sp TEXT,
      raw_json TEXT NOT NULL,
      PRIMARY KEY (provider,race_id,placing_key)
    );
    CREATE TABLE IF NOT EXISTS closing_prices (
      provider TEXT NOT NULL, price_key TEXT NOT NULL, api_day TEXT NOT NULL,
      race_id TEXT, runner_key TEXT, runner_ref TEXT, bookmaker_key TEXT,
      recorded_at_utc TEXT NOT NULL, raw_json TEXT NOT NULL,
      PRIMARY KEY(provider,price_key)
    );
    CREATE TABLE IF NOT EXISTS closing_price_versions (
      provider TEXT NOT NULL, price_key TEXT NOT NULL, sha256 TEXT NOT NULL,
      first_captured_utc TEXT NOT NULL, raw_json TEXT NOT NULL,
      PRIMARY KEY(provider,price_key,sha256)
    );
    CREATE TABLE IF NOT EXISTS api_day_audit (
      endpoint TEXT NOT NULL, api_day TEXT NOT NULL, country_set TEXT NOT NULL,
      fetched_utc TEXT NOT NULL, records INTEGER NOT NULL, pages INTEGER NOT NULL,
      status TEXT NOT NULL, PRIMARY KEY(endpoint,api_day,country_set)
    );
    CREATE INDEX IF NOT EXISTS race_date_ix ON races(gbie_day,country);
    CREATE INDEX IF NOT EXISTS runner_horse_ix ON runners(horse_ref);
    CREATE INDEX IF NOT EXISTS closing_race_ix ON closing_prices(race_id);
    ''')


def check_db(c):
    x = c.execute('PRAGMA quick_check').fetchone()
    if not x or x[0] != 'ok':
        raise RuntimeError('Archive SQLite quick_check failed; refusing to continue')


def json_get(path, params, key, state):
    state['requests'] += 1
    if state['requests'] > state['max_requests']:
        raise RuntimeError('Request budget exceeded. Resume from saved artifact with a higher budget if appropriate.')
    url = BASE + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        'X-API-Key': key, 'Accept': 'application/json',
        'User-Agent': 'RacingHotPots-Stage65C-Recovery/1.0'})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=70) as response:
                size = int(response.headers.get('X-Limit-Applied') or params.get('limit', 0))
                body = response.read(30000000)
                if len(body) >= 30000000:
                    raise RuntimeError('Response was larger than 30 MB; refusing truncated data')
                return json.loads(body), size
        except urllib.error.HTTPError as exc:
            # Never expose the API key or request headers in logs.
            snippet = exc.read(900).decode('utf-8', errors='replace')
            if exc.code in (401, 402, 403, 422):
                raise RuntimeError(f'API refused {path}: HTTP {exc.code}: {snippet[:350]}') from None
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise RuntimeError(f'API {path} failed: HTTP {exc.code}: {snippet[:180]}') from None
        except (OSError, TimeoutError, ValueError) as exc:
            if attempt == 4:
                raise RuntimeError(f'API request to {path} failed after retries: {type(exc).__name__}') from exc
        time.sleep(min(3 * (attempt + 1), 20))
    raise AssertionError('unreachable')


def get_results(day, key, state):
    allrows = []
    offset = 0
    for pages in range(1, 100):
        params = {'date': day, 'country': 'GB,IE', 'categories': 'horse',
                  'limit': 200, 'offset': offset}
        body, applied = json_get('/v1/racing/results', params, key, state)
        if not isinstance(body, list):
            raise RuntimeError(f'Results {day}: expected top-level ARRAY, received {type(body).__name__}')
        allrows.extend(body)
        if len(body) < applied:
            return allrows, pages
        if applied < 1:
            raise RuntimeError(f'Results {day}: invalid X-Limit-Applied')
        offset += applied
    raise RuntimeError(f'Results {day}: reached 99 pages without finishing')


def get_prices(day, key, state):
    allrows = []
    offset = 0
    for pages in range(1, 100):
        params = {'date': day, 'country': 'GB,IE', 'categories': 'horse',
                  'closing_only': 'false', 'include_flagged': 'true',
                  'include_scratched': 'true', 'limit': 5000, 'offset': offset}
        body, _ = json_get('/v1/racing/closing-lines', params, key, state)
        if not isinstance(body, dict) or not isinstance(body.get('rows'), list):
            raise RuntimeError(f'Closing prices {day}: expected object with rows array')
        chunk = body['rows']
        allrows.extend(chunk)
        declared = body.get('rows_returned')
        total = body.get('total_rows')
        if declared is None or total is None or int(declared) != len(chunk):
            raise RuntimeError(f'Closing prices {day}: inconsistent page metadata')
        offset += len(chunk)
        if offset >= int(total):
            return allrows, pages
        if not chunk:
            raise RuntimeError(f'Closing prices {day}: empty page before reported total')
    raise RuntimeError(f'Closing prices {day}: reached 99 pages without finishing')


def numeric_pos(value):
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def stable_runner_key(x):
    # Horse identity used only within one race, not guaranteed across race history.
    n = str(x.get('number') or '').strip()
    nm = str(x.get('name') or '').strip().casefold()
    return digest({'number': n, 'name': nm})[:32]


def ingest_result(c, r, api_day, now):
    if r.get('category') != 'horse' or r.get('country') not in ('GB','IE'):
        raise RuntimeError('Results endpoint returned a race outside required GB/IE horse filters')
    race_id = str(r.get('race_id') or '').strip()
    if not race_id:
        raise RuntimeError('Results endpoint returned a race without race_id')
    payload = canonical(r)
    sha = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    d = event_day(r.get('start_time'))
    av = r.get('availability') or {}
    runners = r.get('runners')
    placings = r.get('placings')
    c.execute('''INSERT OR IGNORE INTO race_versions VALUES (?,?,?,?,?)''',
              (PROVIDER, race_id, sha, now, payload))
    # Preserve a previously captured full field if the source temporarily loses it.
    earlier = c.execute('SELECT reported_field_size FROM races WHERE provider=? AND race_id=?',
                        (PROVIDER,race_id)).fetchone()
    if not isinstance(runners,list) and earlier and earlier[0] is not None:
        return d
    size = len(runners) if isinstance(runners, list) else None
    c.execute('''INSERT INTO races VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(provider,race_id) DO UPDATE SET
         gbie_day=excluded.gbie_day,api_day=excluded.api_day,
         country=excluded.country,venue=excluded.venue,
         start_time=excluded.start_time,race_name=excluded.race_name,
         race_class=excluded.race_class,conditions=excluded.conditions,
         distance_m=excluded.distance_m,going=excluded.going,
         status=excluded.status,field_status=excluded.field_status,
         full_field_availability=excluded.full_field_availability,
         reported_field_size=excluded.reported_field_size,
         captured_utc=excluded.captured_utc,
         payload_sha256=excluded.payload_sha256,raw_json=excluded.raw_json''',
         (PROVIDER, race_id, d, api_day, r['country'],
          r.get('venue_canonical') or r.get('venue'), r.get('start_time'),
          r.get('race_name'), r.get('race_class'), r.get('conditions'),
          r.get('distance_m'),r.get('going'),r.get('status'),r.get('field_status'),
          av.get('full_field'),size,now,sha,payload))
    if isinstance(runners,list):
        keys=set()
        for x in runners:
            if not isinstance(x,dict): raise RuntimeError('Nonobject runner')
            key=stable_runner_key(x)
            if key in keys: raise RuntimeError(f'Duplicate runner identity in {race_id}')
            keys.add(key)
            c.execute('''INSERT INTO runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider,race_id,runner_key) DO UPDATE SET
                horse=excluded.horse,horse_ref=excluded.horse_ref,
                runner_ref=excluded.runner_ref,number=excluded.number,
                position=excluded.position,status=excluded.status,
                official_sp=excluded.official_sp,trainer=excluded.trainer,
                jockey=excluded.jockey,weight=excluded.weight,
                margin=excluded.margin,raw_json=excluded.raw_json''',
                (PROVIDER,race_id,key,x.get('name'),x.get('horse_ref'),
                 x.get('runner_ref'),str(x.get('number') or ''),numeric_pos(x.get('position')),
                 x.get('status'),str(x.get('sp')) if x.get('sp') is not None else None,
                 x.get('trainer'),x.get('jockey'),
                 str(x.get('weight_kg') or x.get('weight') or '') or None,
                 str(x.get('margin')) if x.get('margin') is not None else None,
                 canonical(x)))
    if isinstance(placings,list):
        for x in placings:
            if not isinstance(x,dict): raise RuntimeError('Nonobject placing')
            key=stable_runner_key(x)
            c.execute('''INSERT INTO placings VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(provider,race_id,placing_key) DO UPDATE SET
                 horse=excluded.horse,horse_ref=excluded.horse_ref,
                 position=excluded.position,official_sp=excluded.official_sp,
                 raw_json=excluded.raw_json''',
                 (PROVIDER,race_id,key,x.get('name'),x.get('horse_ref'),
                  numeric_pos(x.get('position')),
                  str(x.get('sp')) if x.get('sp') is not None else None,
                  canonical(x)))
    return d


def ingest_prices(c, rows, api_day, now):
    count = 0
    for x in rows:
        if not isinstance(x,dict):
            raise RuntimeError('Nonobject closing price row')
        rid = str(x.get('race_id') or '')
        book = str(x.get('bookmaker_key') or '')
        runner = str(x.get('runner_ref') or x.get('runner_key') or '')
        if not rid or not book or not runner:
            # Preserve rows with missing identifiers under a full content hash.
            key = digest(x)
        else:
            key = digest({'race_id':rid,'book':book,'runner':runner,'api_day':api_day})
        sha = digest(x)
        c.execute('INSERT OR IGNORE INTO closing_price_versions VALUES (?,?,?,?,?)',
                  (PROVIDER,key,sha,now,canonical(x)))
        c.execute('''INSERT INTO closing_prices VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(provider,price_key) DO UPDATE SET
             recorded_at_utc=excluded.recorded_at_utc,raw_json=excluded.raw_json''',
             (PROVIDER,key,api_day,rid,x.get('runner_key'),x.get('runner_ref'),
              book,now,canonical(x)))
        count += 1
    return count


def summarize(c, since, until, errors, query_days, reqs):
    by_day = collections.defaultdict(lambda: collections.Counter())
    recent = c.execute('''SELECT gbie_day,status,field_status,full_field_availability,
          reported_field_size,raw_json FROM races WHERE country IN ('GB','IE')
          AND gbie_day BETWEEN ? AND ?''',(since,until))
    for day, status, field, full, n, raw in recent:
        s = by_day[day]
        s['race_records'] += 1
        s['final'] += status == 'final'
        s['nonfinal'] += status != 'final'
        s['full_fields'] += (field == 'complete' or full == 'available') and n is not None
        s['missing_full_fields'] += n is None
        s['runner_rows'] += n or 0
        r = json.loads(raw)
        if not r.get('start_time'): s['no_start_time'] += 1
        if not isinstance(r.get('placings'),list): s['missing_placings'] += 1
        if not isinstance(r.get('runners'),list): s['no_runners_array'] += 1
        if r.get('race_class') is None:s['missing_race_class'] += 1
        if r.get('conditions') is None:s['missing_conditions'] += 1
    keys = c.execute('''SELECT r.gbie_day,
       SUM(CASE WHEN u.official_sp IS NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.trainer IS NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.jockey IS NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.weight IS NULL THEN 1 ELSE 0 END),
       SUM(CASE WHEN u.margin IS NULL THEN 1 ELSE 0 END)
       FROM races r JOIN runners u ON r.race_id=u.race_id
       WHERE r.gbie_day BETWEEN ? AND ? GROUP BY r.gbie_day''',(since,until))
    for day,sp,tr,jo,w,ma in keys:
        by_day[day].update({'runner_missing_sp':sp,'runner_missing_trainer':tr,
          'runner_missing_jockey':jo,'runner_missing_weight':w,'runner_missing_margin':ma})
    days = []
    start = dt.date.fromisoformat(since)
    stop = dt.date.fromisoformat(until)
    for i in range((stop-start).days+1):
        day = str(start+dt.timedelta(days=i))
        totals = dict(by_day[day]); totals['date'] = day
        totals['provider_presence'] = 'PRESENT_NEEDS_RECONCILIATION' if totals.get('race_records') else 'NO_PROVIDER_RESULT_RACES'
        days.append(totals)
    result = {'status':'RECOVERY_AUDIT_NOT_FULL_VERIFICATION',
       'source':PROVIDER,'since':since,'until':until,
       'api_day_range':[str(min(query_days)),str(max(query_days))] if query_days else [],
       'api_requests':reqs,'date_count':len(days),
       'race_records':sum(x.get('race_records',0) for x in days),
       'full_field_races':sum(x.get('full_fields',0) for x in days),
       'no_full_field_races':sum(x.get('missing_full_fields',0) for x in days),
       'dates_without_provider_results':[x['date'] for x in days if not x.get('race_records')],
       'errors':errors,'days':days,
       'important_limitations':[
         'This reports provider coverage, NOT a guarantee of every GB/IE race.',
         'Australian-Eastern API day differs from local GB/IE race date. Local date derives from start_time.',
         'The provider does not supply GB/IE beaten margins; do not fill these with zero.',
         'Some GB/IE race_class/conditions/weights may be absent; originals are retained.',
         'Placed horses are not a complete field when runners is null.',
         'No production historical SQLite, app feed or frozen D scorer is changed.',
         'API retention, corrections and historical completeness must be independently checked.',
         'Race and price source payload versions are preserved when they change.']}
    return result


def norm(x):
    return re.sub(r'[^a-z0-9]', '', str(x or '').casefold())


def reconcile_repository_results(c, path, since, until):
    """Compare stored provider races against repository results independently.

    Matching is deliberately conservative. Report unpaired races and disjoint
    fields; never assume they are the same race or silently merge them.
    """
    report = {'source': str(path), 'status': 'NOT_AVAILABLE',
              'matched': 0, 'repository_only': [], 'provider_only': [],
              'runner_mismatches': []}
    path = pathlib.Path(path)
    if not path.is_file():
        return report
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data.get('races'), list):
        report['status'] = 'INVALID_REPOSITORY_FORMAT'
        return report

    def minute(value):
        try:
            d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if d.tzinfo is None:
                return None
            d = d.astimezone(LOCAL)
            return d.hour * 60 + d.minute
        except (ValueError, TypeError):
            return None

    checks = []
    for row in data['races']:
        if not isinstance(row, dict) or row.get('status') != 'final':
            continue
        day = str(row.get('date') or '')[:10]
        course = norm(row.get('course'))
        time = minute(row.get('off_dt') or row.get('race_time'))
        if since <= day <= until and course and time is not None:
            checks.append((row, day, course, time))
    if not checks:
        report['status'] = 'NO_COMPARABLE_REPOSITORY_RESULTS'
        return report

    days = sorted(set(r[1] for r in checks))
    sql = """SELECT race_id,gbie_day,venue,start_time,raw_json FROM races
             WHERE gbie_day>=? AND gbie_day<=? AND country IN ('GB','IE')"""
    candidates = []
    for rid, day, venue, scheduled, raw in c.execute(sql, (days[0], days[-1])):
        if day in days:
            candidates.append((rid, day, norm(venue), minute(scheduled), json.loads(raw)))
    used = set()
    for row, day, course, start_min in checks:
        matching = [x for x in candidates if x[0] not in used and x[1] == day
                    and x[2] == course and x[3] is not None
                    and abs(x[3] - start_min) <= 15]
        if len(matching) != 1:
            report['repository_only'].append({
                'date': day, 'course': row.get('course'),
                'off_dt': row.get('off_dt'), 'possible_matches': len(matching)})
            continue
        rec = matching[0]
        used.add(rec[0])
        report['matched'] += 1
        source_horses = {norm(x.get('name')) for x in (rec[4].get('runners') or [])
                         if isinstance(x, dict) and norm(x.get('name'))}
        repo_horses = {norm(x.get('horse')) for x in (row.get('runners') or [])
                       if isinstance(x, dict) and norm(x.get('horse'))}
        if source_horses and repo_horses and source_horses != repo_horses:
            report['runner_mismatches'].append({
                'date': day, 'course': row.get('course'),
                'provider_total': len(source_horses), 'repository_total': len(repo_horses),
                'repository_only': sorted(repo_horses - source_horses),
                'provider_only': sorted(source_horses - repo_horses)})
    report['provider_only'] = [
        {'race_id': rid, 'date': day, 'venue': payload.get('venue')}
        for rid, day, _, _, payload in candidates if rid not in used]
    report['repository_races_compared'] = len(checks)
    report['provider_races_on_comparison_dates'] = len(candidates)
    report['status'] = ('NEEDS_REVIEW'
                        if report['repository_only'] or report['runner_mismatches']
                        else 'MATCHED_REPOSITORY_SOURCE_NOT_EXHAUSTIVE')
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default='reports/stage65c/form_archive.sqlite')
    p.add_argument('--out',default='reports/stage65c')
    p.add_argument('--since',default='2026-10-08')
    p.add_argument('--until',default=None)
    p.add_argument('--refresh-days',type=int,default=4)
    p.add_argument('--include-prices',choices=['true','false'],default='true')
    p.add_argument('--max-requests',type=int,default=150)
    args=p.parse_args()
    key=os.environ.get('PUNTERSEDGE_API_KEY')
    if not key: raise SystemExit('Missing secret PUNTERSEDGE_API_KEY')
    since=dt.date.fromisoformat(args.since)
    until=dt.date.fromisoformat(args.until) if args.until else dt.datetime.now(LOCAL).date()-dt.timedelta(days=1)
    if until<since: raise SystemExit('Invalid date range')
    if args.max_requests<1:raise SystemExit('--max-requests must be positive')
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    db=pathlib.Path(args.db);db.parent.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect(db)
    init(cx);check_db(cx);cx.commit()
    state={'requests':0,'max_requests':args.max_requests}
    # Source date uses Sydney timezone. Including both edge days is mandatory.
    query_start=since-dt.timedelta(days=1)
    query_end=until+dt.timedelta(days=2)
    stamp=dt.datetime.now(UTC).isoformat()
    errors=[]
    dates=[]
    for i in range((query_end-query_start).days+1):
        day=str(query_start+dt.timedelta(days=i));dates.append(day)
        past = cx.execute('''SELECT endpoint FROM api_day_audit WHERE api_day=? AND country_set=?
            AND status='FETCHED' ''',(day,'GB,IE')).fetchall()
        prior={x[0] for x in past}
        # Refresh recent days even if imported previously; older completed pages are kept.
        changed = dt.date.fromisoformat(day) >= dt.datetime.now(LOCAL).date()-dt.timedelta(days=args.refresh_days+2)
        try:
            if 'results' not in prior or changed:
                races,pages=get_results(day,key,state)
                with cx:  # Checkpoint each completed date.
                    ids=set()
                    for r in races:
                        rid=str(r.get('race_id') or '')
                        if rid in ids:raise RuntimeError('Duplicate race_id within API day '+day)
                        ids.add(rid)
                        ingest_result(cx,r,day,stamp)
                    cx.execute('INSERT OR REPLACE INTO api_day_audit VALUES (?,?,?,?,?,?,?)',
                       ('results',day,'GB,IE',stamp,len(races),pages,'FETCHED'))
                print(f'{day} results: {len(races)} races across {pages} pages',flush=True)
            if args.include_prices=='true' and ('closing-lines' not in prior or changed):
                prices,pages=get_prices(day,key,state)
                with cx:
                    n=ingest_prices(cx,prices,day,stamp)
                    cx.execute('INSERT OR REPLACE INTO api_day_audit VALUES (?,?,?,?,?,?,?)',
                       ('closing-lines',day,'GB,IE',stamp,n,pages,'FETCHED'))
                print(f'{day} closing: {len(prices)} source rows across {pages} pages',flush=True)
        except Exception as exc:
            errors.append({'api_day':day,'error':str(exc)[:600]})
            print(f'{day} INCOMPLETE: {type(exc).__name__}: {str(exc)[:250]}',flush=True)
            # Exceeded budget / authentication / API outage: report the gap and stop spending.
            break
        time.sleep(.2)
    check_db(cx)
    summary=summarize(cx,str(since),str(until),errors,dates,state['requests'])
    recon=reconcile_repository_results(cx,pathlib.Path('data/race-results.json'),str(since),str(until))
    (out/'stage65c_reconciliation.json').write_text(json.dumps(recon,indent=2,ensure_ascii=False)+'\n')
    summary['repository_reconciliation']={k:v for k,v in recon.items() if not isinstance(v,list)}
    (out/'stage65c_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    (out/'stage65c_daily_coverage.csv').write_text('date,race_records,final,full_fields,missing_full_fields,runner_rows,runner_missing_sp,runner_missing_margin,provider_presence\n'+
        ''.join(','.join(str(row.get(k,'')) for k in ('date','race_records','final','full_fields','missing_full_fields','runner_rows','runner_missing_sp','runner_missing_margin','provider_presence'))+'\n' for row in summary['days']))
    cx.close()
    print(json.dumps({k:v for k,v in summary.items() if k not in ('days','important_limitations')},indent=2),flush=True)
    if errors: return 2
    # Require every API date in the requested query window to be fetched; empty days remain an audit warning.
    cx=sqlite3.connect(db)
    check = {d for (d,) in cx.execute('''SELECT api_day FROM api_day_audit WHERE endpoint='results' AND country_set='GB,IE' AND status='FETCHED' ''')}
    misses=sorted(set(dates)-check)
    cx.close()
    if misses:
        print('ERROR: API days not fetched: '+','.join(misses),flush=True)
        return 2
    print('Provider request dates fetched. Coverage and field completeness NOT guaranteed.',flush=True)
    return 0

if __name__=='__main__':sys.exit(main())
