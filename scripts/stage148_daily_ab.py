#!/usr/bin/env python3
"""Daily independent A/B2 Rank 1/2 paper selections, with no odds or results.

A: existing validated Stage16 model probability ranking, unchanged.
B2: NEW, clearly versioned form-only ranking of the same independently
archived prior-race features; not the unavailable original B classifier.
B2 score is relative and NOT calibrated win probability.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import json
import math
import pathlib
import re
from collections import defaultdict
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
DUBLIN = ZoneInfo('Europe/Dublin')
VERSION = 'STAGE148_A_ORIGINAL_B2_FORM_2026_11'
STOP = {'nr','nonrunner','non-runner','scratched','withdrawn','did not start','dns','abandoned','cancelled'}


def timestamp(value):
    if not value:
        return None
    try:
        result = dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.astimezone(UTC) if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def normalize(value):
    return re.sub('[^a-z0-9]', '', str(value or '').lower().replace('(aw)', '').replace('(ire)', ''))


def key(row):
    t = timestamp(row.get('race_time'))
    if not t:
        raise ValueError('Missing timezone-aware race_time')
    return t.date().isoformat(), normalize(row.get('course')), t.strftime('%H:%M')


def active(row):
    status = str(row.get('race_status') or row.get('runner_status') or row.get('status') or '').lower().strip()
    return bool(row.get('horse')) and status not in STOP and not row.get('non_runner') and not row.get('is_non_runner')


def rate(value, name):
    v = float(value)
    if not math.isfinite(v) or not 0 <= v <= 1:
        raise ValueError('Invalid historical form rate: ' + name)
    return v


def b2_score(row):
    """New B2 signal, excluding A probability, current odds and outcomes."""
    starts = int(row['prior_starts'])
    if starts < 0:
        raise ValueError('Negative prior starts')
    trainer = rate(row['trainer_historical_rate'], 'trainer')
    trainer_course = rate(row['trainer_course_historical_rate'], 'trainer_course')
    horse_course = rate(row['course_prior_wins_rate'], 'horse_course')
    horse_distance = rate(row['prior_distance_wins_rate'], 'horse_distance')
    experience = min(1.0, math.log1p(starts) / math.log1p(20))
    return round(100 * (.28 * trainer + .20 * trainer_course +
                        .23 * horse_course + .29 * horse_distance) + 3 * experience, 6)


def calculate(cards, original_a, day, now):
    if original_a.get('day') != day:
        raise ValueError('Original A historical-probability feed has wrong date')
    generated = timestamp(original_a.get('generated_at'))
    if generated is None or generated > now + dt.timedelta(minutes=1):
        raise ValueError('Missing/invalid original A feed timestamp')
    if generated < dt.datetime.combine(dt.date.fromisoformat(day)-dt.timedelta(days=1),dt.time(0), UTC):
        raise ValueError('Original A source too old')
    live = defaultdict(list)
    for r in cards.get('runners', []):
        if not active(r):
            continue
        try:
            k = key(r)
        except ValueError:
            continue
        if k[0] == day:
            live[k].append(r)
    a_races = defaultdict(list)
    for r in original_a.get('races', []):
        try:
            k = key(r)
        except ValueError:
            continue
        if k[0] == day:
            a_races[k].append(r)
    audit = {'version': VERSION, 'date': day, 'a_original_model_version': original_a.get('model_version'),
             'a_source_generated_at': generated.isoformat(),
             'b_version': 'B2_REBUILT_HISTORICAL_FORM_STAGE148_V1',
             'b_is_original_frozen_b': False, 'b_score_is_calibrated_probability': False,
             'a_probability_is_from_original_model': True, 'odds_used': False,
             'historical_form_only': True, 'races_available': len(live), 'races_scored': 0,
             'races_skipped': []}
    top = []
    allrows = []
    for k, field in sorted(live.items()):
        off = timestamp(field[0].get('race_time'))
        if off is None or off <= now + dt.timedelta(minutes=5):
            audit['races_skipped'].append({'race': k, 'reason': 'OFF_OR_TOO_CLOSE'}); continue
        if len(field) < 2:
            audit['races_skipped'].append({'race': k, 'reason': 'FIELD_TOO_SMALL'}); continue
        source = a_races.get(k, [])
        if len(source) != 1:
            audit['races_skipped'].append({'race': k, 'reason': 'A_SOURCE_MISSING_OR_AMBIGUOUS'}); continue
        if generated >= off:
            audit['races_skipped'].append({'race': k, 'reason': 'A_SOURCE_PUBLISHED_AFTER_OFF'}); continue
        source = source[0]
        pool = {normalize(r['horse']): r for r in source.get('runners', []) if r.get('horse')}
        current = {normalize(r['horse']): r for r in field}
        if len(current) != len(field) or not set(current).issubset(pool):
            audit['races_skipped'].append({'race': k, 'reason': 'FIELD_HORSE_MISMATCH'}); continue
        values = []
        try:
            for horse, card in current.items():
                r = pool[horse]
                values.append({'horse': card['horse'], 'a': rate(r['probability'], 'A'),
                               'b': b2_score(r), 'prior_starts': int(r['prior_starts'])})
        except (KeyError, TypeError, ValueError, OverflowError) as ex:
            audit['races_skipped'].append({'race': k, 'reason': 'MISSING_VERIFIED_FEATURE', 'details': str(ex)}); continue
        # Validate the ORIGINAL full-field probability distribution first.
        # The active subset can legitimately sum below 1 after withdrawals.
        try:
            source_total = sum(rate(r['probability'], 'A_SOURCE') for r in pool.values())
        except (TypeError, ValueError, KeyError):
            audit['races_skipped'].append({'race': k, 'reason': 'INVALID_A_SOURCE_FIELD'}); continue
        total = sum(r['a'] for r in values)
        if total <= 0 or not math.isfinite(total) or abs(source_total - 1.0) > .02:
            audit['races_skipped'].append({'race': k, 'reason': 'A_PROBABILITIES_INCOMPLETE', 'source_sum': source_total}); continue
        for r in values:
            r['a'] /= total  # Withdrawal-adjusted A field, not odds-derived
        a_order = sorted(values, key=lambda r: (-r['a'], normalize(r['horse'])))
        b_order = sorted(values, key=lambda r: (-r['b'], -r['prior_starts'], normalize(r['horse'])))
        for model, ordered in [('A', a_order), ('B', b_order)]:
            for rank, r in enumerate(ordered, 1):
                row = {'date': day, 'model': model,
                       'model_version': original_a.get('model_version') if model == 'A' else audit['b_version'],
                       'course': field[0]['course'], 'time_utc': k[2], 'race_time': off.isoformat(),
                       'horse': r['horse'], 'rank': rank,
                       'win_probability': round(r['a'], 6) if model == 'A' else '',
                       'relative_form_score': r['b'] if model == 'B' else '',
                       'b2_provisional_research': model == 'B',
                       'odds_used': False, 'source_asof': generated.isoformat(),
                       'scored_at': now.isoformat(), 'paper_only': True}
                allrows.append(row)
                if rank <= 2:
                    top.append(row)
        audit['races_scored'] += 1
    if not top:
        raise ValueError('No pre-off A/B2 ranks. Reasons: ' + str(audit['races_skipped'][:5]))
    return top, allrows, audit


def export(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def self_test():
    now = dt.datetime(2026, 11, 17, 8, 0, tzinfo=UTC)
    def horse(name, prob, tc, c, d, n):
        return {'horse':name, 'probability':prob, 'prior_starts':n,
                'trainer_historical_rate':tc, 'trainer_course_historical_rate':tc,
                'course_prior_wins_rate':c,'prior_distance_wins_rate':d}
    cards = {'runners':[{'date':'2026-11-17', 'course':'Carlisle',
            'race_time':'2026-11-17T12:00:00Z', 'horse':name}
            for name in ['Alpha', 'Beta','Gamma']]}
    original_a={'day':'2026-11-17','generated_at':'2026-11-17T07:30:00Z',
        'model_version':'TEST_A','races':[{'date':'2026-11-17','course':'Carlisle',
        'race_time':'2026-11-17T12:00:00Z','runners':[
        horse('Alpha', .6, .05,.05,.05,1),
        horse('Beta', .25,.30,.30,.30,8),
        horse('Gamma',.15,.10,.10,.10,2)]}]}
    top, allrows, audit = calculate(cards,original_a,'2026-11-17',now)
    assert len(top)==4 and len(allrows)==6 and audit['races_scored']==1
    assert next(r for r in top if r['model']=='A' and r['rank']==1)['horse']=='Alpha'
    assert next(r for r in top if r['model']=='B' and r['rank']==1)['horse']=='Beta'
    assert all(not r['win_probability'] for r in top if r['model']=='B')
    cards['runners'][-1]['runner_status']='NR'
    top,_,audit=calculate(cards,original_a,'2026-11-17',now)
    assert all(r['horse']!='Gamma' for r in top)
    original_a['races'][0]['runners'][1].pop('prior_distance_wins_rate')
    try: calculate(cards,original_a,'2026-11-17',now)
    except ValueError:pass
    else:raise AssertionError('Missing historical B feature not caught')
    print('STAGE148_AB_SELFTEST_OK')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('command',choices=['self-test','today'])
    ap.add_argument('--date',default='')
    ap.add_argument('--cards',type=pathlib.Path)
    ap.add_argument('--a',type=pathlib.Path)
    ap.add_argument('--out',type=pathlib.Path)
    args=ap.parse_args()
    if args.command=='self-test':
        self_test();return
    today=dt.datetime.now(DUBLIN).date().isoformat()
    day=args.date or today
    if day!=today:raise ValueError('Cannot backdate live A/B selections')
    if any(getattr(args,z) is None for z in ('cards','a','out')):
        ap.error('Required: --cards --a --out')
    cards=json.loads(args.cards.read_text())
    a=json.loads(args.a.read_text())
    now=dt.datetime.now(UTC)
    snapshot=timestamp(cards.get('collected_at'))
    if snapshot is None or (now-snapshot).total_seconds()>3*3600:
        raise ValueError('Racecard snapshot missing or >3 hours old')
    top,allrows,audit=calculate(cards,a,day,now)
    args.out.mkdir(parents=True,exist_ok=True)
    export(args.out/'stage148-ab-rank1-rank2.csv',top)
    export(args.out/'stage148-ab-all-runners.csv',allrows)
    (args.out/'stage148-ab-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    print('STAGE148_AB_SCORED',audit['races_scored'],'of',audit['races_available'],'eligible races')

if __name__=='__main__':main()
