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
    except ValueError:
        return None


def fetch_results(offset):
    query = urlencode({'country': 'GB,IE', 'category': 'horse', 'hours_back': 48, 'limit': 500, 'offset': offset, 'status': 'final'})
    req = Request('https://api.puntersedge.online/v1/racing/results?' + query,
                  headers={'X-API-Key': key, 'Accept': 'application/json', 'User-Agent': 'RacingHotPots/1.0'})
    try:
        with urlopen(req, timeout=50) as resp:
            payload = json.load(resp)
    except HTTPError as e:
        raise SystemExit(f'PuntersEdge HTTP {e.code}; existing archive unchanged.') from e
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for field in ('results', 'races', 'data'):
            if isinstance(payload.get(field), list):
                return payload[field]
    raise RuntimeError('Unexpected results API schema; existing archive unchanged.')


existing = {}
prior_days = set()
if FILE.exists():
    raw = json.loads(FILE.read_text())
    prior_days = set(raw.get('fetched_days', []))
    for runner in raw.get('runners', []):
        if isinstance(runner, dict) and runner.get('race_id') and runner.get('horse'):
            existing[(runner['race_id'], runner['horse'].casefold())] = runner

received = []
for offset in range(0, 5000, 500):
    page = fetch_results(offset)
    received += page
    if len(page) < 500:
        break
else:
    raise RuntimeError('Pagination limit reached; archive unchanged.')

for race in received:
    if race.get('status') != 'final' or race.get('country') not in ('GB', 'IE'):
        continue
    started = race.get('start_time')
    race_id = race.get('race_id')
    if not race_id or not parse_date(started):
        continue
    runners = race.get('runners')
    if not isinstance(runners, list):
        continue
    for runner in runners:
        if runner.get('status') != 'ran' or not runner.get('name'):
            continue
        if not runner.get('trainer') or not runner.get('jockey'):
            continue
        # Null finish positions cannot safely be counted as wins or losses.
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

# Keep enough history for a rolling 14-day period; results before a race do not leak future results.
cutoff = now - dt.timedelta(days=18)
entries = sorted((item for item in existing.values()
                  if (parsed := parse_date(item.get('race_time'))) and parsed >= cutoff),
                 key=lambda x: (x['race_time'], x['race_id'], x['horse']))
# Avoid claiming full population coverage merely because a request succeeded.
# Once the archive has aged 14 days, results may be used only if all daily
# requests were successful and field completeness has been independently verified.
today = now.date()
prior_days.add(today.isoformat())
fetched = sorted(d for d in prior_days if d >= (today - dt.timedelta(days=18)).isoformat())
complete = False  # must remain false until coverage audit establishes full UK/IE fields
content = {'schema': 'racing-hot-pots-form-v1', 'updated_at': now.isoformat(),
           'coverage_start': entries[0]['race_time'] if entries else None,
           'complete': complete, 'fetched_days': fetched, 'runners': entries,
           'note': 'Incomplete until race and runner coverage audited; do not report strike rates as verified.'}
FILE.parent.mkdir(parents=True, exist_ok=True)
FILE.write_text(json.dumps(content, ensure_ascii=False, separators=(',', ':')) + '\n')
print(f'Fetched {len(received)} final races; archived {len(entries)} verified-position runners; full coverage not yet established')
