"""Collect final GB/IE horse racing results for Racing Hot Pots.

Fetch only from PuntersEdge; preserves existing published final results if a later
response omits them. GitHub Actions publishes data/race-results.json separately.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

API_KEY = os.getenv('PUNTERSEDGE_API_KEY', '').strip()
if not API_KEY:
    sys.exit('Missing PUNTERSEDGE_API_KEY GitHub secret')
DEST = Path('data/race-results.json')
PARAMS = urllib.parse.urlencode({'hours_back': 36, 'categories': 'horse', 'country': 'GB,IE', 'limit': 200})
URL = 'https://api.puntersedge.online/v1/racing/results?' + PARAMS
request = urllib.request.Request(URL, headers={'X-API-Key': API_KEY, 'Accept': 'application/json'})
with urllib.request.urlopen(request, timeout=35) as response:
    payload = json.load(response)
    remaining = response.headers.get('X-Credits-Remaining', 'unknown')
raw = payload if isinstance(payload, list) else payload.get('results', payload.get('races', []))
if not isinstance(raw, list):
    sys.exit('Unexpected results API payload: expected races array')

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
        if d < 1:
            return None
        if abs(d - 1) < 1e-8:
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
        return str(sp)

def normalize(race):
    if not isinstance(race, dict):
        return None
    if str(race.get('country', '')).upper() not in ('GB', 'IE'):
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
        raw_pos = r.get('position')
        try:
            pos = int(raw_pos) if raw_pos is not None and str(raw_pos).strip().isdigit() and int(raw_pos) > 0 else None
        except (ValueError, TypeError):
            pos = None
        status = str(r.get('status') or '').strip()
        if not status and pos is None:
            status = 'Unplaced' if str(race.get('field_status','')).lower() == 'complete' else ''
        out_runners.append({'horse': r['name'], 'position': pos, 'status': status, 'sp': decimal_to_fractional(r.get('sp'))})
    if not any(r['position'] is not None for r in out_runners):
        return None
    return {
        'course': str(course),
        'date': off.astimezone(ZoneInfo('Europe/Dublin')).date().isoformat(),
        'off_dt': off.isoformat().replace('+00:00', 'Z'),
        'status': 'final',
        'runners': out_runners,
    }

results = [x for race in raw if (x := normalize(race))]
print(f'PuntersEdge: fetched {len(raw)} race records, {len(results)} eligible final results. Credits remaining: {remaining}.')
if not results:
    print('No final results in response. Leaving existing archive untouched.')
    sys.exit(0)

existing = []
if DEST.exists():
    try:
        old = json.loads(DEST.read_text(encoding='utf-8'))
        existing = old.get('races', []) if isinstance(old, dict) else []
    except (ValueError, OSError) as exc:
        sys.exit(f'Existing race results file is unreadable; refusing overwrite: {exc}')

# Matches existing app's course + Irish date + race time mapping.
def key(x):
    return (str(x.get('course', '')).strip().casefold(), str(x.get('date', '')), str(x.get('off_dt', '')))

merged = {key(r): r for r in existing if isinstance(r, dict) and r.get('course') and r.get('off_dt')}
for r in results:
    merged[key(r)] = r
# Keep seven days of results, not an ever-expanding archive.
cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date()
kept = [r for r in merged.values() if (dtparse(r.get('off_dt')) or datetime.min.replace(tzinfo=timezone.utc)).date() >= cutoff]
kept.sort(key=lambda r: r.get('off_dt', ''), reverse=True)
DEST.parent.mkdir(parents=True, exist_ok=True)
tmp = DEST.with_suffix('.json.tmp')
tmp.write_text(json.dumps({'races': kept, 'updated_at': datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
tmp.replace(DEST)
print(f'Published data/race-results.json containing {len(kept)} finished races. No bets or odds files modified.')
