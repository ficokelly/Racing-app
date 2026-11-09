#!/usr/bin/env python3
"""Stage73: verify Racing API access and reconcile archived person identities.

Only the existing The Racing API credentials are used. Failed access or
contradictory results are left unresolved. Never edits model picks/rankings.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
ARCHIVE = DATA / 'racing-results'
REPORT = DATA / 'rhp-stage73-api-and-identity-audit.json'
STAGE32 = ROOT / 'scripts/stage32_form_rebuild.py'
UTC = dt.timezone.utc


def clean(value):
    s = unicodedata.normalize('NFKD', str(value or ''))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in s if not unicodedata.combining(c)).casefold())


def horse(value):
    return clean(re.sub(r'\s*\((?:GB|IRE|IRL|FR|USA|GER|CAN|AUS|NZ|SAF|ITA|ITY)\)\s*$', '', str(value or ''), flags=re.I))


def course(value):
    s = clean(re.sub(r'\s*\((?:AW|ALL WEATHER|IRE|IRL|GB)\)', '', str(value or ''), flags=re.I))
    return {'chelmsfordcity': 'chelmsford'}.get(s, s)


def clock(value):
    try:
        d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if not d.tzinfo:
            d = d.replace(tzinfo=UTC)
        return d.astimezone(UTC).strftime('%H:%M')
    except (TypeError, ValueError):
        x = re.search(r'\b\d\d:\d\d\b', str(value or ''))
        return x.group() if x else ''


def identity(race):
    return (str(race.get('date') or '')[:10], course(race.get('course')),
            clock(race.get('off_dt') or race.get('race_time') or race.get('off')))


def position(v):
    s = str(v or '').strip()
    return int(s) if s.isdecimal() and int(s) > 0 else None


def winner(race):
    wins = [horse(z.get('horse')) for z in race.get('runners', [])
            if isinstance(z, dict) and position(z.get('position')) == 1]
    return wins[0] if len(wins) == 1 else None


def safe_get(url, auth):
    request = urllib.request.Request(url, headers={
        'Authorization': auth, 'Accept': 'application/json',
        'User-Agent': 'RacingHotPots-Stage73-Validation/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=22) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Never log response bodies, secrets or encoded authorization.
        return error.code, None
    except (OSError, TimeoutError, ValueError) as error:
        return type(error).__name__, None


def probe_access(auth, asof):
    base = 'https://api.theracingapi.com/v1/results'
    q = urllib.parse.urlencode({'limit': 1, 'skip': 0})
    historical_q = urllib.parse.urlencode({'start_date': asof, 'end_date': asof, 'limit': 1, 'skip': 0})
    urls = {'today_free': base + '/today/free?' + q,
            'today_basic': base + '/today?' + q,
            'standard_history': base + '?' + historical_q}
    out = {}
    for name, url in urls.items():
        code, payload = safe_get(url, auth)
        out[name] = {'http_status': code,
                     'response_shape_valid': isinstance(payload, dict) and isinstance(payload.get('results'), list)}
        time.sleep(0.3)
    return out


def get_day(auth, day, maximum_pages=5):
    total = 0
    output = []
    for page in range(maximum_pages):
        q = urllib.parse.urlencode({'start_date': day, 'end_date': day,
              'region': ['gb', 'ire'], 'limit': 100, 'skip': page * 100}, doseq=True)
        status, payload = safe_get('https://api.theracingapi.com/v1/results?' + q, auth)
        if status != 200:
            return None, 'HTTP_' + str(status)
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            return None, 'INVALID_RESULTS_STRUCTURE'
        rows = payload['results']
        output += [z for z in rows if isinstance(z, dict) and
                   str(z.get('region') or '').upper() in ('GB', 'IRE', 'IE')]
        total = payload.get('total', len(rows))
        if not isinstance(total, int):
            return None, 'INVALID_PAGINATION'
        if (page+1) * 100 >= total:
            return output, None
        time.sleep(0.28)
    return None, 'PAGINATION_EXCEEDED'


def matching_result(old, records):
    """Require source race ID where present, unique winner and >=2 same horses."""
    rid = str(old.get('race_id') or '')
    if rid:
        matches = [r for r in records if str(r.get('race_id') or '') == rid]
    else:
        matches = [r for r in records if identity(r) == identity(old)]
    if len(matches) != 1 or not winner(old) or not winner(matches[0]):
        return None, 'NO_UNIQUE_MATCH'
    other = matches[0]
    if identity(old) != identity(other):
        return None, 'RACE_ID_SLOT_CONFLICT'
    if winner(old) != winner(other):
        return None, 'WINNER_CONFLICT'
    actual = {horse(z.get('horse')): z for z in other.get('runners', []) if isinstance(z, dict)}
    common = 0
    for runner in old.get('runners', []):
        newer = actual.get(horse(runner.get('horse')))
        if newer is None:
            continue
        common += 1
        a, b = position(runner.get('position')), position(newer.get('position'))
        if a is not None and b is not None and a != b:
            # Includes stewards-amended orders (e.g. Newbury): do not overwrite.
            return None, 'FINISHING_ORDER_CONFLICT'
    if common < 2:
        return None, 'INSUFFICIENT_HORSE_OVERLAP'
    return other, None


def reconcile_day(path, source_rows):
    """Apply only confirmed final status and missing explicit person fields."""
    doc = json.loads(path.read_text(encoding='utf-8'))
    stats = collections.Counter()
    errors = []
    changed = False
    for existing in doc.get('results', []):
        if not isinstance(existing, dict):
            continue
        incoming, error = matching_result(existing, source_rows)
        if error:
            if existing.get('status') != 'final':
                stats['unresolved_historical_races'] += 1
                if len(errors) < 15:
                    errors.append({'course': existing.get('course'), 'time': identity(existing)[2], 'reason': error})
            continue
        original_status = existing.get('status')
        if original_status in (None, ''):
            existing['status'] = 'final'
            existing['result_classification_source'] = 'stage73_verified_against_racing_api_historical_endpoint'
            stats['provenance_validated'] += 1
            changed = True
        people = {horse(z.get('horse')): z for z in incoming.get('runners', []) if isinstance(z, dict)}
        for r in existing.get('runners', []):
            other = people.get(horse(r.get('horse')))
            if other is None:
                continue
            for field in ('trainer', 'trainer_id', 'jockey', 'jockey_id'):
                new = str(other.get(field) or '').strip()
                old = str(r.get(field) or '').strip()
                if new and not old:
                    r[field] = new
                    changed = True
                    stats['enriched_' + field] += 1
                elif new and old and clean(new) != clean(old):
                    stats['conflicting_' + field + '_retained_original'] += 1
    if changed:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return dict(stats), errors, changed


def patch_stage32():
    """Fix overaggressive withholding of explicit result-identified jockeys.

    Trainer/jockey changes between declared racecards and actual results can
    legitimately occur. Use the race result's name + provider ID *only* where
    the source also carries a true race ID. Otherwise keep the earlier safe
    withholding behaviour; never infer missing people.
    """
    source = STAGE32.read_text(encoding='utf-8')
    old_trainer = '''                if ref and original_trainer and ref.get('trainer') and compact(original_trainer)!=compact(ref['trainer']):
                    audit['result_card_trainer_disagreement'] += 1
                    # Trust provider's explicit identity, but withhold this record if
                    # conflicting metadata could attribute it to the wrong trainer.
                    trainer=''
'''
    new_trainer = '''                if ref and original_trainer and ref.get('trainer') and compact(original_trainer)!=compact(ref['trainer']):
                    audit['result_card_trainer_disagreement'] += 1
                    # Stage73: a provider race ID + trainer ID is stronger evidence
                    # of the actual result than an earlier declared racecard.
                    # Otherwise preserve the original conservative withholding.
                    if race.get('race_id') and runner.get('trainer_id'):
                        audit['result_identity_trainer_preferred_over_declared_card'] += 1
                    else:
                        trainer=''
                        audit['result_identity_trainer_conflict_withheld'] += 1
'''
    old_jockey = '''                if ref and original_jockey and ref.get('jockey') and compact(original_jockey)!=compact(ref['jockey']):
                    audit['result_card_jockey_disagreement'] += 1
                    jockey=''
'''
    new_jockey = '''                if ref and original_jockey and ref.get('jockey') and compact(original_jockey)!=compact(ref['jockey']):
                    audit['result_card_jockey_disagreement'] += 1
                    # Stage73: permit verified result-rider changes when BOTH
                    # race ID and actual result jockey ID are provided.
                    if race.get('race_id') and runner.get('jockey_id'):
                        audit['result_identity_jockey_preferred_over_declared_card'] += 1
                    else:
                        jockey=''
                        audit['result_identity_jockey_conflict_withheld'] += 1
'''
    pairs = [('trainer', old_trainer, new_trainer), ('jockey', old_jockey, new_jockey)]
    count = 0
    for label, old, new in pairs:
        if new in source:
            continue
        if source.count(old) != 1:
            raise RuntimeError('Unexpected Stage32 source: refusing to patch ' + label)
        source = source.replace(old, new, 1)
        count += 1
    if count:
        STAGE32.write_text(source, encoding='utf-8')
    return count


def self_test():
    first = {'race_id': 'rac_a', 'date': '2026-11-09', 'course': 'Dundalk (AW) (IRE)',
             'off_dt': '2026-11-09T13:05:00Z', 'runners': [
                {'horse': 'Horse A (IRE)', 'position': 1},
                {'horse': 'Horse B (GB)', 'position': 2}]}
    new = {**first, 'runners': [{'horse': 'Horse A', 'position': '1'},
                                {'horse': 'Horse B', 'position': '2'}]}
    assert matching_result(first, [new])[1] is None
    invalid = {**new, 'runners': [{'horse': 'Horse A', 'position': 2}, {'horse': 'Horse B', 'position': 1}]}
    assert matching_result(first, [invalid])[1] == 'WINNER_CONFLICT'
    assert matching_result(first, [new, new])[1] == 'NO_UNIQUE_MATCH'
    wrong_slot = {**new, 'off_dt': '2026-11-09T13:35:00Z'}
    assert matching_result(first, [wrong_slot])[1] == 'RACE_ID_SLOT_CONFLICT'
    print('Stage73 source-matching self-test passed')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--probe-only', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test()
        return
    latest = sorted(ARCHIVE.glob('????-??-??.json'))[-1:]
    asof = min(latest[0].stem if latest else dt.date.today().isoformat(),
               dt.datetime.now(ZoneInfo('Europe/Dublin')).date().isoformat())
    old_form = json.loads((DATA/'strike-rates.json').read_text(encoding='utf-8'))
    previous = old_form.get('periods', {}).get('14', {}).get('identity_gaps', {})
    report = {'generated_at': dt.datetime.now(UTC).isoformat(), 'as_of': asof,
              'credentials_status': 'unknown', 'probes': {},
              'historical_dates_attempted': 0, 'historical_dates_read': 0,
              'historical_race_records_verified': 0, 'changes': {}, 'unresolved_examples': [],
              'person_identity_gap_before': previous,
              'frozen_model_protection': 'Script touches only archived race results and Stage32 form logic; never model selections/racecards.',
              'notes': []}
    user = os.environ.get('RACING_API_USERNAME', '').strip()
    pwd = os.environ.get('RACING_API_PASSWORD', '').strip()
    if user and pwd:
        auth = 'Basic ' + base64.b64encode((user+':'+pwd).encode()).decode()
        report['credentials_status'] = 'secret_pair_present'
        report['probes'] = probe_access(auth, asof)
        daily = report['probes'].get('today_free', {}).get('http_status')
        standard = report['probes'].get('standard_history', {}).get('http_status')
        if daily == 200 and standard in (401, 403):
            report['notes'].append('Daily API access works; historical endpoint is denied, likely plan/endpoint access restriction rather than invalid credentials.')
        elif standard in (401, 403) and daily in (401, 403):
            report['notes'].append('Both daily and historical API access denied; validate secret values or subscription. No credentials logged.')
        if standard == 200 and not args.probe_only:
            base = dt.date.fromisoformat(asof)
            for n in range(20, -1, -1):
                day = (base - dt.timedelta(days=n)).isoformat()
                target = ARCHIVE / (day+'.json')
                if not target.exists():
                    continue
                report['historical_dates_attempted'] += 1
                rows, error = get_day(auth, day)
                if error:
                    report['notes'].append('Stopped historical enrichment after '+day+': '+error)
                    break
                report['historical_dates_read'] += 1
                report['historical_race_records_verified'] += len(rows)
                counts, errs, changed = reconcile_day(target, rows)
                for key, value in counts.items():
                    report['changes'][key] = report['changes'].get(key, 0)+value
                for e in errs:
                    if len(report['unresolved_examples']) >= 35:
                        break
                    report['unresolved_examples'].append({'date':day, **e})
                time.sleep(0.28)
        elif not args.probe_only:
            report['notes'].append('Historical changes skipped: source access not confirmed.')
    else:
        report['credentials_status'] = 'secret_pair_missing'
        report['notes'].append('Both RACING_API_USERNAME and RACING_API_PASSWORD secrets are required for access diagnostics.')
    if not args.probe_only:
        report['stage32_patch_count'] = patch_stage32()
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('credentials_status','probes','historical_dates_attempted','historical_dates_read','historical_race_records_verified','changes','stage32_patch_count','notes') if key in report},indent=2))

if __name__ == '__main__':
    main()
