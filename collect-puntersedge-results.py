"""Collect final GB/IE horse racing results for Racing Hot Pots.

Fetch only from PuntersEdge; preserves existing published final results if a later
response omits them. GitHub Actions publishes data/race-results.json separately.
"""
import copy
import json
import math
import re
import unicodedata
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DEST = Path('data/race-results.json')
DUBLIN = ZoneInfo('Europe/Dublin')


def fetch_results():
    api_key = os.getenv('PUNTERSEDGE_API_KEY', '').strip()
    if not api_key:
        raise SystemExit('Missing PUNTERSEDGE_API_KEY GitHub secret')
    params = urllib.parse.urlencode({
        'hours_back': 36, 'categories': 'horse', 'country': 'GB,IE', 'limit': 200,
    })
    request = urllib.request.Request(
        'https://api.puntersedge.online/v1/racing/results?' + params,
        headers={'X-API-Key': api_key, 'Accept': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        payload = json.load(response)
        remaining = response.headers.get('X-Credits-Remaining', 'unknown')
    races = payload if isinstance(payload, list) else payload.get('results', payload.get('races', []))
    if not isinstance(races, list):
        raise ValueError('Unexpected results API payload: expected races array')
    return races, remaining


def dtparse(s):
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

def decimal_to_fractional(sp):
    if sp is None or sp == '':
        return None
    try:
        from fractions import Fraction
        d = float(sp)
        if not math.isfinite(d) or d <= 1:
            return None
        # Decimal feed is rounded; favour familiar UK/Irish SP fractions.
        common = [(n, q) for n, q in [(1, 20), (1, 10), (1, 8), (1, 7), (1, 6), (1, 5), (2, 9), (1, 4), (2, 7), (3, 10), (1, 3), (4, 11), (2, 5), (4, 9), (1, 2), (8, 15), (4, 7), (8, 13), (4, 6), (8, 11), (4, 5), (5, 6), (10, 11), (1, 1), (11, 10), (6, 5), (5, 4), (11, 8), (6, 4), (13, 8), (7, 4), (15, 8), (2, 1), (9, 4), (5, 2), (11, 4), (3, 1), (10, 3), (7, 2), (4, 1), (9, 2), (5, 1), (11, 2), (6, 1), (13, 2), (7, 1), (15, 2), (8, 1), (9, 1), (10, 1), (11, 1), (12, 1), (14, 1), (16, 1), (18, 1), (20, 1), (22, 1), (25, 1), (28, 1), (33, 1), (40, 1), (50, 1), (66, 1), (100, 1)]]
        target = d - 1
        nearest = min(common, key=lambda x: abs(x[0]/x[1] - target))
        if abs(nearest[0]/nearest[1] - target) <= max(0.025, target * 0.003):
            return f'{nearest[0]}/{nearest[1]}'
        f = Fraction(target).limit_denominator(20)
        return f'{f.numerator}/{f.denominator}'
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_sp(*values):
    for value in values:
        text = str(value or '').strip()
        if text.lower() in {'evs', 'even', 'evens'}:
            return '1/1'
        fraction = re.fullmatch(r'(\d+)\s*/\s*(\d+)(?:\s*[FCJ]+)?', text, re.I)
        if fraction and int(fraction[1]) > 0 and int(fraction[2]) > 0:
            return f'{fraction[1]}/{fraction[2]}'
        decimal = decimal_to_fractional(text)
        if decimal:
            return decimal
    return None


def name_key(value):
    bare = re.sub(r'\s*\((?:AW|GB|IRE|FR|USA|GER)\)\s*$', '', str(value or ''), flags=re.I)
    plain = unicodedata.normalize('NFKD', bare).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', plain.lower())


def normalize(race):
    if not isinstance(race, dict):
        return None
    if str(race.get('country', '')).upper() not in ('GB', 'IE', 'IRE'):
        return None
    if str(race.get('status', '')).lower() != 'final':
        return None
    off = dtparse(race.get('start_time'))
    course = race.get('venue')
    runners = race.get('runners')
    if not off or not course or not isinstance(runners, list):
        return None
    out_runners = []
    for r in runners:
        if not isinstance(r, dict) or not r.get('name'):
            continue
        raw_pos = str(r.get('position') or '').strip().lower()
        match = re.fullmatch(r'([1-9]\d*)(?:\.0|st|nd|rd|th|=)?', raw_pos)
        pos = int(match[1]) if match else None
        status = str(r.get('status') or '').strip().lower()
        if not status and pos is None:
            status = raw_pos if raw_pos and raw_pos != '0' else ''
            if not status and str(race.get('field_status', '')).lower() == 'complete':
                status = 'Unplaced'
        withdrawn = status in {'nr', 'non-runner', 'non runner', 'nonrunner', 'scratched', 'withdrawn', 'dns', 'reserve'}
        sp = normalize_sp(r.get('sp_fractional'), r.get('sp'), r.get('sp_decimal'))
        out_runners.append({
            'horse': r['name'], 'position': None if withdrawn else pos,
            'status': status, 'sp': None if withdrawn else sp,
        })
    identities = [name_key(r['horse']) for r in out_runners]
    if not all(identities) or len(identities) != len(set(identities)):
        return None
    if not any(r['position'] == 1 for r in out_runners):
        return None
    return {
        'course': str(course),
        'date': off.astimezone(DUBLIN).date().isoformat(),
        'off_dt': off.isoformat().replace('+00:00', 'Z'),
        'status': 'final',
        'runners': out_runners,
    }

def race_key(race):
    course = name_key(race.get('course'))
    course = {
        'chelmsfordcity': 'chelmsford', 'kemptonpark': 'kempton',
        'lingfieldpark': 'lingfield', 'bangor': 'bangorondee',
    }.get(course, course)
    off = dtparse(race.get('off_dt'))
    return (course, race.get('date'), off.astimezone(DUBLIN).strftime('%H:%M') if off else '')


def merge_race(previous, current):
    if not previous:
        return current
    old = {name_key(row['horse']): row for row in previous['runners']}
    new = {name_key(row['horse']): row for row in current['runners']}
    nr = {'nr', 'non-runner', 'non runner', 'nonrunner', 'scratched', 'withdrawn', 'dns', 'reserve'}
    if any(str(old[name].get('status') or '').lower() not in nr for name in old.keys() - new.keys()):
        # A partial refresh cannot erase a previously verified starter.
        return previous
    # Keep recorded SP when the provider briefly drops it on the same result.
    rows = []
    for name, row in new.items():
        prior = old.get(name, {})
        if not row['sp'] and row['position'] == prior.get('position') and row['status'] not in nr:
            row = {**row, 'sp': prior.get('sp')}
        rows.append(row)
    rows.extend(old[name] for name in old if name not in new)
    return {**previous, **current, 'runners': rows}


def publish(results, dest=DEST):
    old = json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else {'races': []}
    existing = old['races']
    if not isinstance(existing, list):
        raise ValueError('Existing race results file has no races list')
    merged = {race_key(r): copy.deepcopy(r) for r in existing}
    for race in results:
        key = race_key(race)
        merged[key] = merge_race(merged.get(key), race)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=30)).date()
    kept = [
        r for r in merged.values()
        if (dtparse(r.get('off_dt')) or datetime.min.replace(tzinfo=timezone.utc)).date() >= cutoff
    ]

    # Preserve existing verified amendments after each provider refresh.
    from scripts.rhp_stage69_verified_results import patch as apply_verified_race_finishes
    from scripts.rhp_stage72_reconcile import amend_newbury_race
    corrected, _ = apply_verified_race_finishes(kept, strict=False)
    if corrected:
        print('Retained externally verified non-finisher statuses:', len(corrected))
    for race in kept:
        amend_newbury_race(race)
    kept.sort(key=lambda r: (r['off_dt'], r['course']), reverse=True)
    if kept == existing:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix('.json.tmp')
    temp.write_text(json.dumps({'races': kept, 'updated_at': now.isoformat()},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(dest)
    print(f'Published {len(kept)} finished races.')
    return True


def main():
    raw, remaining = fetch_results()
    results = [result for race in raw if (result := normalize(race))]
    print(f'PuntersEdge fetched {len(raw)} records, {len(results)} eligible final results. Credits remaining: {remaining}.')
    if not results or not publish(results):
        print('No new or amended final results.')


if __name__ == '__main__':
    main()
