#!/usr/bin/env python3
"""Stage76: strictly bounded, published-result historical reconciliation.

Only the eight specified 2026-10-25..30 race slots are eligible. A mismatching
winner, runner-up, third or race identity aborts before any file is written.
Supersedes one demonstrably stale Galway post-race feed and corrects its
canonical disqualified runner; preserves original details for evidence.
"""
from __future__ import annotations

import argparse
import collections
import copy
import datetime as dt
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / 'data/racing-results'
FORM_ENGINE = ROOT / 'scripts/stage32_form_rebuild.py'
SOURCE_CHECK = ROOT / 'scripts/rhp_stage74_external_gap_recovery.py'
EVIDENCE = ROOT / 'data/rhp-stage76-historical-reconciliation.json'

# Finishing orders individually corroborated against published, dated results.
# Third is omitted if not needed for a safe match, never inferred.
CONFIRMED = [
    ('2026-10-25', 'Wexford', '16:40', 'Socrates Smiled', 'Certavi Et Vici', 'Lest Ye Forget',
     'https://www.attheraces.com/results/25-October-2026'),
    ('2026-10-25', 'Galway', '13:42', 'Lanzhou', 'Dontcavein', 'Dearg Mor',
     'https://www.racingpost.com/results/183/galway/2026-10-25/931489/'),
    ('2026-10-26', 'Southwell', '20:30', 'Tarbat Ness', 'Charging Thunder', None,
     'https://www.sportinglife.com/racing/meeting/2026-10-26/southwell/122198'),
    ('2026-10-26', 'Leicester', '16:24', 'Power Of Prayer', 'Molly Valentine', 'Kotari',
     'https://www.racingpost.com/results/30/leicester/2026-10-26/929486/'),
    ('2026-10-27', 'Wolverhampton', '20:30', 'Cabeza De Llave', 'Macanudo', 'La Belle Forest',
     'https://www.racingpost.com/results/513/wolverhampton-aw/2026-10-27/929610/'),
    ('2026-10-28', 'Kempton', '20:15', 'Strong Voice', 'Split Elevens', 'Whitesnake',
     'https://www.horseracing.net/results/28-10-26'),
    ('2026-10-29', 'Southwell', '20:30', 'Chambers', 'Mr Moonshine', 'West Tyrone',
     'https://www.racingpost.com/results/394/southwell-aw/2026-10-29/929621/'),
    ('2026-10-30', 'Dundalk', '20:30', 'Not Just Any Eagle', 'Sunrush', 'Zoffman',
     'https://www.racingpost.com/results/1138/dundalk-aw/2026-10-30/932143/'),
]


def compact(value):
    normalized = unicodedata.normalize('NFKD', str(value or ''))
    return re.sub(r'[^a-z0-9]', '', ''.join(x for x in normalized if not unicodedata.combining(x)).casefold())


def horse(value):
    return compact(re.sub(r'\s*\((?:IRE|IRL|GB|FR|USA|GER|AUS|NZ|SAF|ITA|ITY|CAN)\)\s*$', '', str(value or ''), flags=re.I))


def course(value):
    return compact(re.sub(r'\s*\((?:AW|ALL WEATHER|IRE|IRL|GB)\)', '', str(value or ''), flags=re.I))


def clock(value):
    try:
        z = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if z.tzinfo is None:
            z = z.replace(tzinfo=dt.timezone.utc)
        return z.astimezone(dt.timezone.utc).strftime('%H:%M')
    except (ValueError, TypeError):
        m = re.search(r'\b\d{2}:\d{2}\b', str(value or ''))
        return m.group(0) if m else ''


def race_at(r, day, co, tm):
    return (str(r.get('date') or '')[:10] == day and course(r.get('course')) == course(co)
            and clock(r.get('off_dt') or r.get('race_time') or r.get('off')) == tm)


def position(r, pos):
    return [x for x in r.get('runners', []) if isinstance(x, dict) and str(x.get('position') or '').strip() == str(pos)]


def verify_order(r, expected):
    runners = r.get('runners', [])
    if not isinstance(runners, list) or len(runners) < 2:
        raise RuntimeError('Race has too few or invalid runner records')
    keys = [horse(x.get('horse')) for x in runners if isinstance(x, dict)]
    if len(keys) != len(runners) or not all(keys) or len(set(keys)) != len(keys):
        raise RuntimeError('Duplicate or nameless runners: refusing unverified result')
    for placing, name in enumerate(expected, 1):
        if name is None:
            continue
        found = position(r, placing)
        if len(found) != 1 or horse(found[0]['horse']) != horse(name):
            raise RuntimeError(f'Expected {placing}: {name}; archive does not match')
    if len(position(r, 1)) != 1:
        raise RuntimeError('Missing or ambiguous winner')


def patch_single(source, old, new, label):
    if new in source:
        return source
    n = source.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected exactly one safe patch site; found {n}')
    return source.replace(old, new, 1)


def update_engines():
    form = FORM_ENGINE.read_text(encoding='utf-8')
    form = patch_single(form,
        "            if not isinstance(race, dict):\n                continue\n            region = str(race.get('region') or '').upper()",
        "            if not isinstance(race, dict):\n                continue\n            if race.get('status') == 'superseded':\n                audit['stage76_superseded_race_rows_excluded'] += 1\n                continue\n            region = str(race.get('region') or '').upper()",
        'Stage32 disputed-final isolation')
    compile(form, str(FORM_ENGINE), 'exec')

    source = SOURCE_CHECK.read_text(encoding='utf-8')
    old = "race.get('status') not in ('final', 'abandoned')"
    new = "race.get('status') not in ('final', 'abandoned', 'superseded')"
    if old in source:
        if source.count(old) != 2:
            raise RuntimeError('Stage74 expected two provenance status checks')
        source = source.replace(old, new)
    elif source.count(new) != 2:
        raise RuntimeError('Stage74 unexpected provenance logic')
    source = patch_single(source,
        "race.get('status') == 'abandoned':",
        "race.get('status') in ('abandoned', 'superseded'):",
        'Stage74 avoid rechecking superseded race')
    compile(source, str(SOURCE_CHECK), 'exec')
    return {FORM_ENGINE: form, SOURCE_CHECK: source}


def reconcile(root=ROOT, write=True):
    root = Path(root)
    # For local, isolated self-tests, paths live under the supplied root.
    source_root = root / 'data/racing-results'
    stages = [root / 'scripts/stage32_form_rebuild.py', root / 'scripts/rhp_stage74_external_gap_recovery.py']
    original_form, original_source = FORM_ENGINE, SOURCE_CHECK
    # Global engine paths are only resolved when running against the actual checkout.
    if root != ROOT:
        raise RuntimeError('Use reconcile only at repository root; see self_test for fixtures')
    planned = {}
    report = []
    for day in sorted({x[0] for x in CONFIRMED}):
        path = source_root / f'{day}.json'
        if not path.is_file():
            raise RuntimeError(f'Result file missing: {path}')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data.get('results'), list):
            raise RuntimeError('Result archive missing array')
        planned[path] = data
    for day, venue, off, winner, second, third, url in CONFIRMED:
        data = planned[source_root / f'{day}.json']
        candidates = [r for r in data['results'] if isinstance(r, dict) and race_at(r, day, venue, off)]
        if venue == 'Galway':
            if len(candidates) != 2:
                raise RuntimeError(f'Galway must have exactly two known competing sources; saw {len(candidates)}')
            canonical = [r for r in candidates if len(position(r, 1)) == 1
                         and horse(position(r, 1)[0].get('horse')) == horse('Lanzhou')]
            erroneous = [r for r in candidates if len(position(r, 1)) == 1
                         and horse(position(r, 1)[0].get('horse')) == horse('Prince Palace')]
            if len(canonical) != 1 or len(erroneous) != 1:
                raise RuntimeError('Galway requires unique correct Lanzhou and stale Prince Palace feed')
            race = canonical[0]
            stale = erroneous[0]
            # Check stale source has the specific wrong provisional ordering.
            verify_order(stale, ['Prince Palace', 'Lanzhou', 'Dontcavein'])
            if stale.get('status') not in ('final', 'superseded'):
                raise RuntimeError('Galway disputed record changed state; manual review required')
            stale['status'] = 'superseded'
            stale['excluded_from_form'] = True
            stale['stage76_superseded_reason'] = 'Provisional result disagrees with official post-disqualification result'
            stale['stage76_authoritative_source'] = url
            # The canonical Racing API result gives this runner 12th, but the
            # published official decision is DSQ. Preserve the original value.
            row = [r for r in race.get('runners', []) if horse(r.get('horse')) == 'princepalace']
            if len(row) != 1 or str(row[0].get('position')).upper() not in ('12', 'DSQ'):
                raise RuntimeError('Galway Prince Palace status is not the verified old value')
            if str(row[0]['position']).upper() == '12':
                row[0]['stage76_original_position'] = row[0]['position']
                row[0]['position'] = 'DSQ'
            row[0]['stage76_official_disqualification_source'] = url
        else:
            if len(candidates) != 1:
                raise RuntimeError(f'Ambiguous result for {day} {venue} {off}: {len(candidates)}')
            race = candidates[0]
        verify_order(race, [winner, second, third])
        if race.get('status') not in (None, '', 'final'):
            raise RuntimeError(f'Unexpected status for {day} {venue} {off}')
        race['status'] = 'final'
        race['result_classification_source'] = 'stage76_independently_corroborated_published_result'
        race['stage76_verification'] = {
            'date_checked': '2026-11-09', 'race_date': day,
            'source_url': url, 'winner': winner, 'runner_up': second,
            'third': third, 'claim': 'Published finish positions corroborate archived runner placings; full field not newly asserted'
        }
        report.append({'date': day, 'course': venue, 'off': off, 'winner': winner,
                       'source_url': url, 'status': 'final', 'verified_first_three': third is not None})
    if len(report) != 8:
        raise RuntimeError('Expected exactly eight source-verified race reconciliations')
    revised = update_engines()
    evidence = {'stage': '76', 'checked_at': '2026-11-09', 'scope': 'UK/IE selected historical race results',
                'source_verified_race_count': len(report), 'verified_races': report,
                'galway_superseded_provisional_records': 1,
                'galway_prince_palace_status': 'DSQ',
                'model_selection_data_changed': False,
                'warning': 'Only eight specified race slots verified; this is not a global historical completeness certificate. Original provisional record retained for audit.'}
    if write:
        for path, contents in planned.items():
            path.write_text(json.dumps(contents, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        for path, contents in revised.items():
            path.write_text(contents, encoding='utf-8')
        EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return evidence


def self_test():
    assert len(CONFIRMED) == 8
    assert course('Dundalk (AW) (IRE)') == 'dundalk'
    assert course('Wolverhampton (AW)') == 'wolverhampton'
    assert horse('Lanzhou (FR)') == 'lanzhou'
    assert clock('2026-10-25T13:42:00Z') == '13:42'
    good = {'runners': [
        {'horse': 'Lanzhou (FR)', 'position': '1'},
        {'horse': 'Dontcavein (IRE)', 'position': 2},
        {'horse': 'Dearg Mor (IRE)', 'position': 3},
        {'horse': 'Prince Palace (FR)', 'position': 'DSQ'},
    ]}
    verify_order(good, ['Lanzhou', 'Dontcavein', 'Dearg Mor'])
    try:
        verify_order(good, ['Prince Palace', 'Dontcavein', 'Dearg Mor'])
        raise AssertionError('Bad winner accepted')
    except RuntimeError:
        pass
    # Validate the current repo patch points and compile generated replacements.
    engines = update_engines()
    assert all(isinstance(x, str) for x in engines.values())
    print('Stage76 verified source match and code patch self-tests passed')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test()
        return
    print(json.dumps(reconcile(write=not args.dry_run), indent=2))


if __name__ == '__main__':
    main()
