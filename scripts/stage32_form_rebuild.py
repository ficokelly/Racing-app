#!/usr/bin/env python3
"""Stage 32: restore trainer/jockey IDs for historical results and rebuild 1-14d form.

This script NEVER guesses winners, results, trainer identities, or names. It uses
ONLY exact matching of date/course/off-time and normalized horse names between
owned UK/IE Racing API result and racecard archives. It deduplicates duplicate
race result records (provider and legacy variants). Records with ambiguous or
conflicting positions/identities are withheld from person strike-rate totals.

The cache is continuously updated by the nightly results job. --backfill adds
one frozen GitHub racecard snapshot from 2026-10-26 to repair missing identities
for October 2026; it can be omitted on routine future jobs.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CACHE_FILE = Path('data/form-identity-cache.json')
RESULTS_DIR = Path('data/racing-results')
OUTPUT_FILE = Path('data/strike-rates.json')
AUDIT_FILE = Path('reports/stage32/form-rebuild-audit.json')
SNAPSHOT_SHA = '80011e8822b6fb5a51c4aa06186a4bf5456307f5'
SNAPSHOT_URL = f'https://raw.githubusercontent.com/ficokelly/Racing-app/{SNAPSHOT_SHA}/data/racecards-archive.json'
NONRUNNERS = {'NR','N/R','NON RUNNER','NON-RUNNER','WD','WITHDRAWN','SCRATCHED','DNS','VOID','ABANDONED',''}
IRISH = {'fairyhouse','punchestown','leopardstown','naas','navan','curragh','clonmel', 'limerick','cork','thurles','killarney','galway','downroyal','downpatrick', 'ballinrobe','bellewstown','tipperary','tramore','listowel','dundalk','wexford','sligo','gowranpark','roscommon','kilbeggan','laytown'}

def compact(value: object) -> str:
    s = unicodedata.normalize('NFKD', str(value or ''))
    s = ''.join(x for x in s if not unicodedata.combining(x))
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def horse_key(name: object) -> str:
    s = re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|SAF|ITA|ITY)\)\s*$', '', str(name or ''), flags=re.I)
    return compact(s)


def course_key(name: object) -> str:
    s = str(name or '')
    # Handles Dundalk (AW) (IRE), Wolverhampton (AW), Dundalk, etc.
    s = re.sub(r'\s*\((?:AW|ALL WEATHER|IRE|GB|IRL)\)', '', s, flags=re.I)
    s = compact(s)
    return {'chelmsfordcity':'chelmsford','gowranpark':'gowranpark'}.get(s, s)


def off_key(value: object) -> str:
    s = str(value or '')
    try:
        parsed = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).strftime('%H:%M')
    except ValueError:
        match = re.search(r'\b(\d\d:\d\d)\b', s)
        return match.group(1) if match else ''


def runner_key(day: str, course: object, off: object, horse: object) -> str:
    return '|'.join((str(day or ''), course_key(course), off_key(off), horse_key(horse)))


def is_starter(row: dict) -> bool:
    pos = str(row.get('position') or '').strip().upper()
    if pos in NONRUNNERS:
        return False
    if str(row.get('status') or '').strip().lower() in {'scratched','non-runner','withdrawn','abandoned'}:
        return False
    return True


def is_win(row: dict) -> bool:
    return str(row.get('position') or '').strip().upper() in {'1', '1=', '1ST'}


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def historical_snapshot() -> dict:
    # This is OUR OWN existing, public GitHub racecard archive, NOT a new third-party
    # prices source. Prefer the raw snapshot; use a one-commit git fetch as a backup.
    try:
        request = Request(SNAPSHOT_URL, headers={'User-Agent':'RacingHotPots-OwnRepo-Archive/1.0'})
        with urlopen(request, timeout=90) as response:
            raw = response.read(10_000_001)
    except Exception as first_error:
        try:
            subprocess.run(['git','fetch','--no-tags','--depth=1','origin',SNAPSHOT_SHA],
                           check=True,stdout=subprocess.DEVNULL,timeout=110)
            raw = subprocess.check_output(['git','show',f'{SNAPSHOT_SHA}:data/racecards-archive.json'],timeout=35)
        except Exception as second_error:
            raise RuntimeError('Cannot verify the previously saved 26 October racecard archive; no historical person data imported') from second_error
    if len(raw) > 10_000_000:
        raise ValueError('Old saved racecard archive exceeds sanity limit')
    v = json.loads(raw)
    if not isinstance(v.get('runners'), list) or len(v['runners']) < 1000:
        raise ValueError('Unexpected historical racecard snapshot format')
    return v


def cache_index(raw: dict) -> dict:
    rows = raw.get('identities', {})
    return rows if isinstance(rows, dict) else {}


def combine_person_cache(cache: dict, cards: dict, source: str, audit: collections.Counter) -> None:
    rows = cards.get('runners', [])
    if not isinstance(rows, list):
        return
    for r in rows:
        if not isinstance(r, dict) or not (r.get('horse') and r.get('course') and r.get('race_time') and r.get('date')):
            continue
        day = str(r['date'])[:10]
        key = runner_key(day, r['course'], r['race_time'], r['horse'])
        if not all(key.split('|')):
            continue
        trainer = str(r.get('trainer') or '').strip()
        jockey = str(r.get('jockey') or '').strip()
        if not trainer and not jockey:
            continue
        existing = cache.get(key)
        incoming = {'trainer': trainer, 'trainer_id': str(r.get('trainer_id') or ''),
                    'jockey': jockey, 'jockey_id': str(r.get('jockey_id') or ''), 'source': source}
        if existing:
            # Same runner, same source person identities: retain verified names/IDs.
            for person in ('trainer','jockey'):
                old = existing.get(person, '')
                new = incoming.get(person, '')
                if old and new and compact(old) != compact(new):
                    # A changed stable / contradictory racecard identity cannot be
                    # silently turned into a form observation.
                    existing['identity_conflict'] = True
                    audit['racecard_identity_conflicts'] += 1
            if not existing.get('identity_conflict'):
                for k, v in incoming.items():
                    if v and (k not in existing or not existing.get(k)):
                        existing[k] = v
        else:
            cache[key] = incoming
            audit['identity_cache_new'] += 1


def get_best_rows(as_of: dt.date, cache: dict, audit: collections.Counter):
    start = as_of - dt.timedelta(days=13)
    selected = {}  # One horse in one race exactly once, even if legacy+API duplicate.
    source_dates = set()
    results_files = sorted(RESULTS_DIR.glob('????-??-??.json'))
    for path in results_files:
        try:
            day = dt.date.fromisoformat(path.stem)
        except ValueError:
            continue
        if not start <= day <= as_of:
            continue
        payload = load_json(path)
        races = payload.get('results', [])
        if not isinstance(races, list):
            continue
        if races:
            source_dates.add(day.isoformat())
        for race in races:
            if not isinstance(race, dict):
                continue
            region = str(race.get('region') or '').upper()
            if region and region not in ('GB', 'IRE', 'IE'):
                audit['excluded_non_ukie_races'] += 1
                continue
            race_date = str(race.get('date') or day.isoformat())[:10]
            if race_date != day.isoformat():
                audit['invalid_race_day'] += 1
                continue
            course, off = race.get('course'), race.get('off_dt') or race.get('race_time') or race.get('off')
            if not course or not off or not off_key(off):
                audit['missing_race_identity'] += 1
                continue
            audit['races_source_rows'] += 1
            for runner in race.get('runners') or []:
                if not isinstance(runner, dict) or not runner.get('horse') or not is_starter(runner):
                    continue
                audit['source_starters'] += 1
                key = runner_key(race_date, course, off, runner.get('horse'))
                ref = cache.get(key)
                if ref and ref.get('identity_conflict'):
                    # Provider has its own identity? Keep it. A conflict only makes
                    # the fallback identity unusable.
                    ref = None
                original_trainer = str(runner.get('trainer') or '').strip()
                original_jockey = str(runner.get('jockey') or '').strip()
                trainer = original_trainer or (ref or {}).get('trainer', '')
                jockey = original_jockey or (ref or {}).get('jockey', '')
                trainer_id = str(runner.get('trainer_id') or '') or (ref or {}).get('trainer_id','')
                jockey_id = str(runner.get('jockey_id') or '') or (ref or {}).get('jockey_id','')
                if ref and (not original_trainer or not original_jockey):
                    audit['rows_with_identity_enrichment'] += 1
                if ref and original_trainer and ref.get('trainer') and compact(original_trainer)!=compact(ref['trainer']):
                    audit['result_card_trainer_disagreement'] += 1
                    # Trust provider's explicit identity, but withhold this record if
                    # conflicting metadata could attribute it to the wrong trainer.
                    trainer=''
                if ref and original_jockey and ref.get('jockey') and compact(original_jockey)!=compact(ref['jockey']):
                    audit['result_card_jockey_disagreement'] += 1
                    jockey=''
                normalized = {'date':race_date, 'trainer':trainer, 'trainer_id':trainer_id,
                              'jockey':jockey, 'jockey_id':jockey_id, 'position':str(runner.get('position')).upper(),
                              'had_explicit_provider_identity': bool(original_trainer and original_jockey),
                              'had_provider_race_id':bool(race.get('race_id'))}
                score=(int(normalized['had_explicit_provider_identity'])*10+
                       int(normalized['had_provider_race_id'])*3+
                       int(bool(trainer))+int(bool(jockey)))
                existing=selected.get(key)
                if existing:
                    audit['duplicate_runner_rows_removed'] += 1
                    previous=existing['record']
                    if (is_win(previous) != is_win(normalized)) or (previous['position'] not in (normalized['position'], '1=', '1ST') and normalized['position'] not in (previous['position'], '1=', '1ST')):
                        audit['contradictory_results_removed'] += 1
                        existing['conflict']=True
                    if score>existing['score']:
                        existing['record']=normalized
                        existing['score']=score
                else:
                    selected[key]={'record':normalized,'score':score,'conflict':False}
    audit['unique_starters'] = len(selected)
    return [x['record'] for x in selected.values() if not x['conflict']], source_dates


def build_form(rows: list[dict], as_of: dt.date, dates: set[str], audit: collections.Counter) -> dict:
    periods = {}
    for n in range(1, 15):
        lower = (as_of-dt.timedelta(days=n-1)).isoformat()
        batch=[x for x in rows if lower <= x['date'] <= as_of.isoformat()]
        # Sum by unique normalized person name, not mutable API identifier.
        people = {'trainer':{},'jockey':{}}
        unknown={'trainer':0,'jockey':0}
        used_id={}
        for typ in ('trainer','jockey'):
            byname=collections.defaultdict(lambda:{'name':'','runs':0,'wins':0,'ids':collections.Counter()})
            for row in batch:
                name=row.get(typ) or ''
                if not name:
                    unknown[typ] += 1
                    continue
                person=byname[compact(name)]
                person['name']=name
                person['runs'] += 1
                person['wins'] += int(is_win(row))
                ident=row.get(typ+'_id') or ''
                if ident:
                    person['ids'][ident]+=1
            for nm,val in byname.items():
                # Prefer the most consistently observed provider ID for this name.
                identity=val['ids'].most_common(1)[0][0] if val['ids'] else 'name:'+nm
                runs=val['runs']
                people[typ][identity]={'name':val['name'],'runs' if typ=='trainer' else 'rides':runs,
                                       'wins':val['wins'],'strike_rate':round(100*val['wins']/runs,1)}
        date_values=sorted(d for d in dates if d >= lower)
        periods[str(n)] = {
            'days': n,
            'races': None,
            'runners': len(batch),
            'dates_available': date_values,
            'trainers': people['trainer'],
            'jockeys': people['jockey'],
            'identity_gaps':unknown,
            'coverage_complete': False if sum(unknown.values()) or len(date_values)<n else None,
            'coverage_note':'Results-based, deduplicated counts; some races/runners may be missing in upstream API.'
        }
    return {'as_of':as_of.isoformat(),'source':'The Racing API results + linked racecard identity archive',
            'max_days':14,'model_data_changed':False,'deduplicated':True,'periods':periods,
            'methodology':'Dates, courses, UTC off time and exact normalised horse identities must agree. Do not use race outcomes to match.'}


def fix_calculator_wrapper():
    # Existing nightly cron invokes calculate-strike-rates.py; this ensures the
    # same audited method runs nightly, not just once during Stage 32.
    f=Path('calculate-strike-rates.py')
    script='''#!/usr/bin/env python3
"""Nightly 1-14 day strike-rate recomputation using the audited Stage 32 merger."""
from scripts.stage32_form_rebuild import main
if __name__ == '__main__':
    main()
'''
    f.write_text(script,encoding='utf-8')


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--backfill',action='store_true',help='Load the historical 26 October card snapshot from own GitHub repository')
    ap.add_argument('--as-of',type=str,help='Override archived last complete day for controlled testing')
    ap.add_argument('--no-wrapper',action='store_true',help='Do not change the old calculate-strike-rates.py')
    args=ap.parse_args()
    files=[p for p in RESULTS_DIR.glob('????-??-??.json') if re.fullmatch(r'\d{4}-\d\d-\d\d', p.stem)]
    local_today=dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
    available=[dt.date.fromisoformat(p.stem) for p in files if dt.date.fromisoformat(p.stem)<=local_today]
    if not available:
        raise SystemExit('No dated race results on disk. No strike-rate file changed.')
    as_of=dt.date.fromisoformat(args.as_of) if args.as_of else max(available)
    if as_of > local_today:
        raise SystemExit('Cannot calculate trainer form from future results')
    audit=collections.Counter()
    cache=cache_index(load_json(CACHE_FILE))
    if args.backfill:
        combine_person_cache(cache,historical_snapshot(),'OWN_REPOSITORY_ARCHIVE_2026_10_26',audit)
    for path in (Path('data/racecards-archive.json'),Path('data/racecards.json')):
        if path.exists():
            combine_person_cache(cache,load_json(path),path.name,audit)
    # Do not retain trainer/jockey identities indefinitely in the public repo.
    cutoff=(as_of-dt.timedelta(days=20)).isoformat()
    cache={k:v for k,v in cache.items() if k[:10]>=cutoff}
    rows, dates=get_best_rows(as_of,cache,audit)
    if not rows or not any(x.get('trainer') for x in rows):
        raise SystemExit('No verified form rows: leave the published strike-rate file unchanged')
    output=build_form(rows,as_of,dates,audit)
    report={'status':'AUDITED_PARTIAL_COVERAGE','as_of':as_of.isoformat(),'date_start_14d':(as_of-dt.timedelta(days=13)).isoformat(),
            'counts':dict(audit),'fourteen_day_form_runners':output['periods']['14']['runners'],
            'fourteen_day_missing_trainer_rows':output['periods']['14']['identity_gaps']['trainer'],
            'fourteen_day_missing_jockey_rows':output['periods']['14']['identity_gaps']['jockey'],
            'gordon_elliott':next((x for x in output['periods']['14']['trainers'].values() if compact(x['name'])=='gordonelliott'),None),
            'jack_kennedy':next((x for x in output['periods']['14']['jockeys'].values() if compact(x['name'])=='jackkennedy'),None),
            'reference_screenshot_2026_11_03':{'gordon_elliott_wins':9,'gordon_elliott_runners':45,
                'note':'Racing Post displayed last 14 days at 12:01 Nov 3; our rolling window ends at latest archived results day. Do not force an exact match across nonidentical time windows.'},
            'warnings':['Racing API coverage is not assured complete; unmatched identities are withheld.',
                        'No selection or probability models were changed.',
                        'Duplicated runner records are counted once, not twice.',
                        'This fixes form result identity linking, not the original API source coverage.']}
    CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
    CACHE_FILE.write_text(json.dumps({'schema':'rhp-form-identities-v1','updated_as_of':as_of.isoformat(),'identities':cache},separators=(',',':'))+'\n',encoding='utf-8')
    OUTPUT_FILE.write_text(json.dumps(output,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    AUDIT_FILE.parent.mkdir(parents=True,exist_ok=True)
    AUDIT_FILE.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    if not args.no_wrapper:
        fix_calculator_wrapper()
    print(json.dumps({'as_of':as_of.isoformat(),'counted_runs':len(rows),'gordon_elliott':report['gordon_elliott'],
                      'jack_kennedy':report['jack_kennedy'],'identity_rows_unmatched':report['fourteen_day_missing_trainer_rows'],
                      'duplicates_removed':audit['duplicate_runner_rows_removed'],'audit_file':str(AUDIT_FILE)},indent=2))


if __name__ == '__main__':
    main()
