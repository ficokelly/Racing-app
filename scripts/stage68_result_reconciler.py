#!/usr/bin/env python3
"""Append verified provider results and settle existing Hot Pots predictions.

Does not predict race outcomes, change model scores, or treat bookmaker prices as SP.
The existing result feed has already been authenticated and normalized by the
PuntersEdge collector. This script only processes `status: final` race records.
"""
import argparse
import datetime as dt
import json
import re
from pathlib import Path

UTC = dt.timezone.utc
NOT_FINISHED = {'pulled up': 'Pulled up', 'pu': 'Pulled up', 'p.u.': 'Pulled up',
                'fell': 'Fell', 'f': 'Fell', 'unseated rider': 'Unseated rider',
                'ur': 'Unseated rider', 'refused': 'Refused', 'r': 'Refused',
                'brought down': 'Brought down', 'bd': 'Brought down',
                'ran out': 'Ran out', 'ro': 'Ran out', 'unplaced': 'Unplaced',
                'finished unplaced': 'Unplaced', 'disqualified': 'Disqualified',
                'dq': 'Disqualified'}
NON_RUNNERS = {'nr', 'non runner', 'non-runner', 'withdrawn', 'scratched', 'dns'}


def norm(value):
    s = re.sub(r'\s*\((?:GB|IRE|FR|AW)\)\s*$', '', str(value or ''), flags=re.I)
    return re.sub(r'[^a-z0-9]', '', s.casefold())


def minute(value):
    text = str(value or '')
    try:
        p = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
        if p.tzinfo is None:
            return p.strftime('%H:%M')
        return p.astimezone(UTC).strftime('%H:%M')
    except ValueError:
        m = re.search(r'\b(\d\d:\d\d)\b', text)
        return m.group(1) if m else ''


def key(r):
    return (str(r.get('date') or '')[:10], norm(r.get('course')),
            minute(r.get('off_dt') or r.get('race_time') or r.get('off') or r.get('time')))


def read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save_if_changed(path, obj):
    content = json.dumps(obj, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return True


def verified(race):
    if race.get('status') != 'final' or not race.get('course') or not key(race)[2]:
        return False
    rows = race.get('runners') or []
    if len(rows) < 2 or len({norm(x.get('horse')) for x in rows}) != len(rows):
        return False
    winners = [x for x in rows if str(x.get('position')) in ('1', '1.0')]
    return len(winners) == 1


def insert_races(root, races):
    changed = 0
    byday = {}
    for race in races:
        if not verified(race):
            continue
        day = key(race)[0]
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            continue
        byday.setdefault(day, []).append(race)
    for day, incoming in byday.items():
        path = root / 'data' / 'racing-results' / (day + '.json')
        current = read(path, {'date': day, 'results': []})
        existing = current.get('results', [])
        slot_index = {key(x):i for i,x in enumerate(existing) if isinstance(x, dict)}
        update = False
        for race in incoming:
            loc = key(race)
            if loc in slot_index:
                old = existing[slot_index[loc]]
                oldr = {norm(x.get('horse')): x for x in old.get('runners', []) if isinstance(x, dict)}
                # Preserve richer Racing API trainer/jockey identities, updating only
                # fields which that source did not provide. Never replace conflicting
                # official finishing positions without a manual source audit.
                for runner in race['runners']:
                    prior = oldr.get(norm(runner.get('horse')))
                    if not prior:
                        continue
                    for field in ('position', 'status', 'sp'):
                        if prior.get(field) in (None, '') and runner.get(field) not in (None, ''):
                            prior[field] = runner[field]
                            update = True
                continue
            existing.append({**race, 'region': race.get('region') or '',
                             'source': 'verified PuntersEdge final results'})
            slot_index[loc] = len(existing)-1
            update = True
        if update:
            current['results'] = existing
            current['date'] = day
            current['source'] = 'Racing API archive + verified PuntersEdge final results'
            current['collected_at'] = dt.datetime.now(UTC).isoformat()
            if save_if_changed(path, current):
                changed += 1
    return changed


def sp_decimal(value):
    if value is None or value == '':
        return None
    text = str(value).strip().lower().replace(' ', '')
    if text in ('evs', 'evens', 'even'):
        return 2.0
    try:
        if '/' in text:
            a, b = text.split('/', 1)
            return round(1 + float(a)/float(b), 6) if float(b) > 0 else None
        d = float(text)
        return round(d, 6) if d > 1 else None
    except (TypeError, ValueError, OverflowError):
        return None


def settlement(record, matches):
    # Require a single verified final race at the exact date, course and UTC off.
    slot = (str(record.get('date') or '')[:10], norm(record.get('course')),
            minute(record.get('race_time') or record.get('time')))
    race = matches.get(slot)
    if not race:
        return False
    chosen = [r for r in race.get('runners', []) if norm(r.get('horse')) == norm(record.get('horse'))]
    if len(chosen) != 1:
        return False
    r = chosen[0]
    rawpos = r.get('position')
    try:
        pos = int(rawpos) if str(rawpos).isdigit() and int(rawpos) > 0 else None
    except (ValueError, TypeError):
        pos = None
    status = str(r.get('status') or (rawpos if pos is None else '') or '').strip().lower()
    if pos is None and status not in NOT_FINISHED and status not in NON_RUNNERS:
        return False
    if pos:
        record['position'] = pos
        record['status'] = 'Won' if pos == 1 else 'Lost'  # win-only ROI, even if placed
        record['finishing_status'] = ('Won' if pos == 1 else 'Placed' if pos <= 3 else 'Unplaced')
    elif status in NON_RUNNERS:
        record['status'] = 'Non-runner'
        record['finishing_status'] = 'Non-runner'
    else:
        record['status'] = 'Lost'
        record['finishing_status'] = NOT_FINISHED[status]
    if r.get('sp') is not None:
        record['sp'] = str(r['sp'])
        record['odds'] = sp_decimal(r['sp'])
    record['settlement_source'] = 'verified final result and provider-reported SP (not live bookmaker quote)'
    record['settled_at'] = dt.datetime.now(UTC).isoformat()
    return True


def reconcile(root):
    source = read(root / 'data' / 'race-results.json', {'races': []})
    fresh = [r for r in source.get('races', []) if verified(r)]
    days_changed = insert_races(root, fresh)
    files = sorted((root / 'data' / 'racing-results').glob('????-??-??.json'))
    archive = {}
    for path in files:
        for r in read(path, {}).get('results', []):
            if verified(r):
                archive[key(r)] = r
    for r in fresh:
        archive[key(r)] = r  # Prefer the latest explicitly final provider response
    count = 0
    for fname in ('strategy-d-history.json', 'strategy-d-results.json'):
        path = root / 'data' / fname
        if not path.exists():
            continue
        obj = read(path, {})
        changed = False
        for r in obj.get('selections', []):
            if r.get('status') != 'Pending':
                continue
            if settlement(r, archive):
                count += 1
                changed = True
        if changed:
            save_if_changed(path, obj)
    print(f'RHP_STAGE68: {len(fresh)} verified source races, {days_changed} updated result dates, {count} settled records')
    return days_changed, count


def self_test():
    race = {'date':'2026-11-09','course':'Aintree','off_dt':'2026-11-09T15:25:00Z','status':'final',
            'runners':[{'horse':'First Horse','position':1,'sp':'11/10'},
                       {'horse':'Second Horse','position':None,'status':'PU','sp':'14/1'}]}
    assert verified(race) and sp_decimal('11/10') == 2.1
    r={'date':'2026-11-09','course':'Aintree','time':'15:25','horse':'Second Horse','status':'Pending'}
    assert settlement(r,{key(race):race}) and r['finishing_status']=='Pulled up' and r['status']=='Lost'
    assert not settlement({'date':'2026-11-09','course':'Aintree','time':'15:30','horse':'First Horse'}, {key(race):race})
    fall={**race, 'runners':[race['runners'][0], {'horse':'Second Horse','position':'F','sp':'14/1'}]}
    fell={'date':'2026-11-09','course':'Aintree','time':'15:25','horse':'Second Horse','status':'Pending'}
    assert settlement(fell,{key(fall):fall}) and fell['finishing_status']=='Fell'
    print('stage68 reconciliation self-test passed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',default='.')
    parser.add_argument('--self-test',action='store_true')
    opts=parser.parse_args()
    if opts.self_test:
        self_test()
    else:
        reconcile(Path(opts.repo))
