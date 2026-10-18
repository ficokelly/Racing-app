#!/usr/bin/env python3
"""Detect newly qualifying Power Favs and send one OneSignal push per horse/race."""
import json
import os
import re
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA = Path('data')
LATEST = DATA / 'latest-odds.json'
HISTORY = DATA / 'odds-history.json'
STATE = DATA / 'power-fav-alert-state.json'
APP_ID = '1bbabde1-23d9-4e6a-9c97-eacf144d332b'
TARGET = 'https://ficokelly.github.io/Racing-app/?tab=power'
# Optional OneSignal Web Push subscription ID (not the OneSignal user ID).
SUBSCRIPTION_ID = os.environ.get('ONESIGNAL_SUBSCRIPTION_ID', '').strip()


def load(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def number(value):
    try:
        n = float(value)
        return n if n > 1 else None
    except (ValueError, TypeError):
        return None


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()


def bookmaker(value):
    v = norm(value).replace('_', ' ').replace('-', ' ')
    if v in ('paddypower', 'paddy power'):
        return 'Paddy Power'
    return v


def strategy(p):
    typ = norm(p.get('race_type'))
    surf = norm(p.get('surface'))
    jumps = bool(re.search(r'hurdle|chase|nh[ -]?flat|national hunt|bumper|jumps|steeple', typ))
    flat = 'flat' in typ and not jumps
    if flat and surf in ('turf', 'grass'):
        return 'Strategy A'
    if jumps or (flat and surf in ('aw', 'all-weather', 'all weather', 'synthetic', 'polytrack', 'tapeta', 'fibresand')):
        return 'Strategy B'
    return 'Unverified'


def price_text(decimal):
    from fractions import Fraction
    frac = Fraction(decimal - 1).limit_denominator(100)
    return f'{frac.numerator}/{frac.denominator}'


def main():
    latest = load(LATEST, {})
    snapshots = load(HISTORY, [])
    current = latest.get('prices', [])
    if not current:
        print('No current prices; skipping notifications.')
        return

    # Earliest available *prior* observed quote per horse/bookmaker/race.
    earliest = {}
    current_at = latest.get('collected_at', '')
    for snap in snapshots:
        if snap.get('collected_at', '') >= current_at:
            continue
        for p in snap.get('prices', []):
            if p.get('stale') is True:
                continue
            odds = number(p.get('decimal_odds'))
            if odds is None:
                continue
            key = (str(p.get('race_id')), bookmaker(p.get('bookmaker')), norm(p.get('horse')))
            earliest.setdefault(key, odds)

    races = defaultdict(dict)
    for p in current:
        if p.get('stale') is True or p.get('country') not in ('GB', 'IE'):
            continue
        odds = number(p.get('decimal_odds'))
        if odds is None or not p.get('race_id') or not p.get('horse') or not p.get('bookmaker'):
            continue
        key = (str(p['race_id']), bookmaker(p['bookmaker']))
        races[key][norm(p['horse'])] = (p, odds)

    qualifiers = {}
    now = datetime.now(timezone.utc)
    for (race_id, book), runners in races.items():
        sorted_runners = sorted(runners.values(), key=lambda x: x[1])
        if len(sorted_runners) < 2:
            continue
        (p, fav), (_, second) = sorted_runners[:2]
        if not (fav < 3 and second >= 4 and fav < second):
            continue
        previous = earliest.get((race_id, book, norm(p['horse'])))
        opening = number(p.get('opening_odds'))
        baseline = previous if previous is not None else opening
        if baseline is None or baseline <= fav:
            continue
        off = p.get('race_time')
        if off:
            try:
                dt = datetime.fromisoformat(off.replace('Z', '+00:00'))
                if dt.tzinfo and dt <= now:
                    continue
            except (ValueError, TypeError):
                pass
        unique = json.dumps([race_id, norm(p['horse'])], separators=(',', ':'))
        candidate = (p, book, fav, second, baseline, strategy(p))
        # One alert per horse/race, prioritise Paddy Power, then shortest odds.
        if unique not in qualifiers or (book == 'Paddy Power', -fav) > (qualifiers[unique][1] == 'Paddy Power', -qualifiers[unique][2]):
            qualifiers[unique] = candidate

    state = load(STATE, None)
    if state is None:
        # First run is a baseline; no retroactive flood of existing qualifiers.
        STATE.write_text(json.dumps({'sent': {k: current_at for k in qualifiers}}, indent=2) + '\n', encoding='utf-8')
        print(f'Initialized notification baseline ({len(qualifiers)} existing qualifiers). No push sent.')
        return

    sent = state.get('sent', {})
    pending = [(k, q) for k, q in qualifiers.items() if k not in sent]
    if not pending:
        print(f'No new Power Favs. {len(qualifiers)} currently qualify.')
        return
    api_key = os.environ.get('ONESIGNAL_API_KEY')
    if not api_key:
        raise RuntimeError('Missing GitHub Actions secret ONESIGNAL_API_KEY')
    for unique, (p, book, fav, second, baseline, label) in pending:
        course = p.get('course') or 'Unknown meeting'
        off = p.get('race_time') or ''
        try:
            off_label = datetime.fromisoformat(off.replace('Z', '+00:00')).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Dublin')).strftime('%H:%M')
        except (ValueError, TypeError):
            off_label = off
        body = f"{p['horse']} · {course} {off_label} · {book} {price_text(fav)} (was {price_text(baseline)}) · 2nd fav {price_text(second)} · {label}"
        payload = {
            'app_id': APP_ID,
            **({'include_subscription_ids': [SUBSCRIPTION_ID]} if SUBSCRIPTION_ID else {'included_segments': ['Subscribed Users']}),
            'headings': {'en': '🔥 New Power Fav detected'},
            'contents': {'en': body},
            'web_url': TARGET,
            'data': {'tab': 'power', 'race_id': str(p['race_id']), 'horse': p['horse']},
        }
        request = urllib.request.Request('https://api.onesignal.com/notifications', data=json.dumps(payload).encode(), headers={
            'Authorization': f'Key {api_key}', 'Content-Type': 'application/json', 'Accept': 'application/json',
        }, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
            if result.get('errors') or not result.get('id'):
                print(f'WARNING: OneSignal did not accept notification for {p["horse"]}: {result}')
                continue
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            print(f'WARNING: OneSignal HTTP {exc.code} for {p["horse"]}: {detail[:2000]}')
            continue
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f'WARNING: OneSignal notification failed for {p["horse"]}: {exc}')
            continue
        sent[unique] = current_at
        state['sent'] = sent
        STATE.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
        print(f"Sent alert: {p['horse']} at {course} via {book} ({label})")


if __name__ == '__main__':
    main()
