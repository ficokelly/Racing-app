#!/usr/bin/env python3
"""Build a small rolling UK/IE results archive for Racing Hot Pots.
Requires PUNTERSEDGE_API_KEY in GitHub Actions secrets. Uses no third-party packages.
Runs once daily. No scraped pages, no guessed statistics.
"""
import datetime as dt
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
FILE = ROOT / 'data' / 'form-results.json'
UTC = dt.timezone.utc
now = dt.datetime.now(UTC)
key = os.environ.get('PUNTERSEDGE_API_KEY')
if not key:
    raise SystemExit('PUNTERSEDGE_API_KEY secret is missing; archive unchanged.')


def parse_date(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        return None


def field_names(value):
    """Log keys only, never response values or credentials."""
    if not isinstance(value, dict):
        return f'type={type(value).__name__}'
    return ', '.join(sorted(str(k) for k in value.keys())[:60])


def diagnose_payload(payload, races):
    print('DIAGNOSTIC: payload type:', type(payload).__name__)
    print('DIAGNOSTIC: top-level keys:', field_names(payload))
    print('DIAGNOSTIC: race count:', len(races))
    if not races:
        return
    first = races[0]
    print('DIAGNOSTIC: first race type:', type(first).__name__)
    print('DIAGNOSTIC: first race keys:', field_names(first))
    if not isinstance(first, dict):
        return
    for k in ('runners', 'horses', 'entries', 'results', 'participants', 'race_result'):
        value = first.get(k)
        if isinstance(value, list):
            print(f'DIAGNOSTIC: first race {k} count:', len(value))
            if value:
                print(f'DIAGNOSTIC: first {k} item keys:', field_names(value[0]))
        elif isinstance(value, dict):
            print(f'DIAGNOSTIC: first race {k} keys:', field_names(value))
    counts = {'status_not_final_or_country': 0, 'missing_race_id_or_time': 0,
              'missing_runners_list': 0, 'runners_found': 0,
              'runner_status_or_name': 0, 'missing_trainer_or_jockey': 0,
              'missing_or_invalid_position': 0, 'accepted': 0}
    for race in races:
        if not isinstance(race, dict):
            counts['status_not_final_or_country'] += 1
            continue
        if race.get('status') != 'final' or race.get('country') not in ('GB', 'IE'):
            counts['status_not_final_or_country'] += 1
            continue
        if not race.get('race_id') or not parse_date(race.get('start_time')):
            counts['missing_race_id_or_time'] += 1
            continue
        runners = race.get('runners')
        if not isinstance(runners, list):
            counts['missing_runners_list'] += 1
            continue
        counts['runners_found'] += len(runners)
        for runner in runners:
            if not isinstance(runner, dict) or runner.get('status') != 'ran' or not runner.get('name'):
                counts['runner_status_or_name'] += 1
            elif not runner.get('trainer') or not runner.get('jockey'):
                counts['missing_trainer_or_jockey'] += 1
            else:
                try:
                    if runner.get('position') is None:
                        raise ValueError('missing')
                    int(runner['position'])
                except (ValueError, TypeError):
                    counts['missing_or_invalid_position'] += 1
                else:
                    counts['accepted'] += 1
    for name, count in counts.items():
        print(f'DIAGNOSTIC: {name}: {count}')


def fetch_results():
    query = urlencode({'country': 'GB,IE', 'category': 'horse', 'hours_back': 48})
    req = Request('https://api.puntersedge.online/v1/racing/results?' + query,
                  headers={'X-API-Key': key, 'Accept': 'application/json', 'User-Agent': 'RacingHotPots/1.0'})
    try:
        with urlopen(req, timeout=50) as resp:
            payload = json.load(resp)
    except HTTPError as e:
        detail = e.read(1500).decode('utf-8', errors='replace')
        raise SystemExit(f'PuntersEdge HTTP {e.code}: {detail}; existing archive unchanged.') from e
    if isinstance(payload, list):
        diagnose_payload(payload, payload)
        return payload
    if isinstance(payload, dict):
        for field in ('results', 'races', 'data'):
            if isinstance(payload.get(field), list):
                diagnose_payload(payload, payload[field])
                return payload[field]
    print('DIAGNOSTIC: unexpected payload top-level keys:', field_names(payload))
    raise RuntimeError('Unexpected results API schema; existing archive unchanged.')


existing = {}
prior_days = set()
if FILE.exists():
    raw = json.loads(FILE.read_text())
    prior_days = set(raw.get('fetched_days', []))
    for runner in raw.get('runners', []):
        if isinstance(runner, dict) and runner.get('race_id') and runner.get('horse'):
            existing[(runner['race_id'], runner['horse'].casefold())] = runner

received = fetch_results()
for race in received:
    if not isinstance(race, dict) or race.get('status') != 'final' or race.get('country') not in ('GB', 'IE'):
        continue
    started = race.get('start_time')
    race_id = race.get('race_id')
    if not race_id or not parse_date(started):
        continue
    runners = race.get('runners')
    if not isinstance(runners, list):
        continue
    for runner in runners:
        if not isinstance(runner, dict) or runner.get('status') != 'ran' or not runner.get('name'):
            continue
        if not runner.get('trainer') or not runner.get('jockey'):
            continue
        position = runner.get('position')
        if position is None:
            continue
        try:
            position = int(position)
        except (ValueError, TypeError):
            continue
        item = {'race_id': race_id, 'race_time': started, 'horse': runner['name'],
                'trainer': runner['trainer'], 'jockey': runner['jockey'], 'position': position}
        existing[(race_id, runner['name'].casefold())] = item

cutoff = now - dt.timedelta(days=18)
entries = sorted((item for item in existing.values()
                  if (parsed := parse_date(item.get('race_time'))) and parsed >= cutoff),
                 key=lambda x: (x['race_time'], x['race_id'], x['horse']))
today = now.date()
prior_days.add(today.isoformat())
fetched = sorted(d for d in prior_days if d >= (today - dt.timedelta(days=18)).isoformat())
complete = False
content = {'schema': 'racing-hot-pots-form-v1', 'updated_at': now.isoformat(),
           'coverage_start': entries[0]['race_time'] if entries else None,
           'complete': complete, 'fetched_days': fetched, 'runners': entries,
           'note': 'Incomplete until race and runner coverage audited; do not report strike rates as verified.'}
FILE.parent.mkdir(parents=True, exist_ok=True)
FILE.write_text(json.dumps(content, ensure_ascii=False, separators=(',', ':')) + '\n')
print(f'Fetched {len(received)} races; archived {len(entries)} verified-position runners; full coverage not yet established')
