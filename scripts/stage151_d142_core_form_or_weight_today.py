#!/usr/bin/env python3
"""Stage151 — exact D142_LONG_CORE_FORM_OR_WEIGHT, same-day, Rank-1 paper selections.

Reproduces the historical Stage142 long CORE + FORM + OR + WEIGHT arm,
with original HPR and quarterly training. The 1988-2021 history warms both
original D pillar statistics and form. Verified OR/weight inputs are added. Today's unresulted runners enter only a disposable
SQLite copy, and cannot enter the training set or same-day form history.
No changes to original D, D(c), Stage141, Stage143/146 or the live app.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
from zoneinfo import ZoneInfo

import numpy as np
import stage159_explainability as E
import stage30_gap_aware_abcd as D
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H
import stage141_grade_weight_2026 as J
import stage141_today_or_weight_paper as L
import stage142_full_history_core_2026 as T

MODEL = 'D142_LONG_CORE_FORM_OR_WEIGHT'
DUBLIN = ZoneInfo('Europe/Dublin')


def today():
    return dt.datetime.now(DUBLIN).date().isoformat()


def previous_quarter_end(day):
    date = dt.date.fromisoformat(day)
    start = dt.date(date.year, ((date.month - 1) // 3) * 3 + 1, 1)
    return (start - dt.timedelta(days=1)).isoformat()


def check_original_d():
    original = json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    if original['chosen_combination'] != 'D-064-gap14':
        raise RuntimeError('Unexpected frozen D benchmark: refuse scoring')
    exact = np.asarray(D.candidates('D')[64], dtype=float)
    exact /= exact.sum()
    if not np.allclose([original['weights_pct'][p] for p in S.PILLARS], exact*100, atol=.011):
        raise RuntimeError('Frozen D weight identity mismatch')
    S.FROZEN[:] = exact
    S.CANDIDATES = S.candidate_weights()


def score_today(args):
    date = args.date or today()
    now_dublin=dt.date.fromisoformat(today())
    if date not in (now_dublin.isoformat(),(now_dublin+dt.timedelta(days=1)).isoformat()):
        raise RuntimeError('Only today or tomorrow Dublin pre-off predictions are allowed')
    args.out.mkdir(parents=True, exist_ok=True)
    cards, cards_audit = L.today_cards(args.cards, date)
    if not cards:
        raise RuntimeError('No verifiable UK/IE racecards available for today')
    if all(c['off'] <= L.utcnow()+dt.timedelta(minutes=2) for c in cards):
        raise RuntimeError('No eligible unstarted races')

    scratch, reference, source_latest = L.scratch_archive(
        args.db, args.reference, cards, date, args.out)
    # This is the same fixed source used by Stage141. Warn if older, but
    # never claim a more current history than exists.
    age = (dt.date.fromisoformat(date)-dt.date.fromisoformat(source_latest)).days
    if age > 21:
        raise RuntimeError('Historic source is over 21 days behind; refresh archive before scoring')
    check_original_d()

    # Construct the same preoff Stage142 baseline; no current outcome leaks.
    base, baseline_audit, _ = J.construct_2026(scratch, reference, date)
    if not base:
        raise RuntimeError('No baseline race features constructed')
    print('STAGE151_BASELINE', len(base), flush=True)

    # Same 1988–2021 State/Form warmup and 2022+ forward reconstruction as
    # Stage142's historical experiment. No historical model is retrained here.
    state, form, history_audit = T.warmup(scratch, reference, date)
    extended, extension_audit, _ = T.reconstruct_full_core(
        scratch, reference, date, state, form)
    cohort_audit = T.align(base, extended)
    by_extended = {str(r['race_id']): r for r in extended}
    del state, form, extended

    identities = {k for r in base for k in r['identity'] if k}
    original_end = H.END
    try:
        H.END = date
        hpr, hpr_audit = H.hpr_history(scratch, reference, args.out, identities)
    finally:
        H.END = original_end
    hpr_coverage = collections.Counter()
    H.attach_hpr(base, hpr, hpr_coverage)

    # Stage142 BOTH_WEIGHT uses verified historical OR/weight features from
    # the unmodified baseline race, NOT from a post-race outcome.
    weight_audit = J.weight_context(scratch, reference, base, args.out, date)

    cutoff = previous_quarter_end(date)
    if date.startswith('2026-') and cutoff != '2026-09-30':
        raise RuntimeError('Unexpected 2026 Stage142 quarterly training cutoff')
    training = [
        T.variant(r, by_extended[str(r['race_id'])], core=True, form=True, weights=True)
        for r in base if '2023-01-01' <= r['date'] <= cutoff
    ]
    if len(training) < 100 or any(r['date'] >= date for r in training):
        raise RuntimeError('Insufficient or time-leaked model training')
    banks = H.l137_ml_banks(training, ability=True)
    print('STAGE151_TRAINED', len(training), 'through', cutoff, flush=True)

    card_by_id = {c['id']: c for c in cards}
    selected, all_runners = [], []
    skips = collections.Counter()
    future = [r for r in base if r['date'] == date]
    for r in sorted(future, key=lambda z: card_by_id[str(z['race_id'])]['off']
                    if str(z['race_id']) in card_by_id else dt.datetime.max.replace(tzinfo=dt.timezone.utc)):
        rid = str(r['race_id'])
        card = card_by_id.get(rid)
        if card is None:
            skips['unmatched_race'] += 1
            continue
        if card['off'] <= L.utcnow() + dt.timedelta(minutes=2):
            skips['off_or_imminent'] += 1
            continue
        # EXACT BOTH_WEIGHT arm from scripts/stage142_full_history_core_2026.py
        model_race = T.variant(r, by_extended[rid], core=True, form=True, weights=True)
        stage159_inputs=H.hpr_ability_137(model_race)
        scores, bank = S.predict_bank(banks, stage159_inputs)
        stage159_drivers=E.safe_forest(stage159_inputs['xf'],scores,banks[bank],S.FEATURES)
        if len(scores) != len(r['horse']) or not np.all(np.isfinite(scores)):
            raise RuntimeError('Invalid or incomplete full-field scores for '+rid)
        order = np.argsort(-scores, kind='stable')
        for rank, idx in enumerate(order, 1):
            idx = int(idx)
            rec = {
                'date': date,
                'time_utc': card['off'].strftime('%H:%M'),
                'meeting': r['course'],
                'race_id': rid,
                'race_name': r['race_name'],
                'race_type': r['subtype'],
                'discipline': r['kind'],
                'horse': r['horse'][idx],
                'rank': rank,
                'model': MODEL,
                'raw_score_not_calibrated_win_pct': round(float(scores[idx]), 6),
                'driver_json': json.dumps(stage159_drivers[idx],separators=(',',':')),
                'field_size': len(order),
                'trained_through': cutoff,
                'prediction_asof': L.utcnow().isoformat(),
                'result_known': False,
                'odds_used': False,
                'bank': bank,
            }
            all_runners.append(rec)
            if rank == 1:
                selected.append(rec)

    if not selected or len({x['race_id'] for x in selected}) != len(selected):
        raise RuntimeError('Missing/duplicate Rank 1 predictions; no output published')
    L.csvwrite(args.out/'stage151-d142-core-form-weight-rank1.csv', selected)
    L.csvwrite(args.out/'stage151-d142-core-form-weight-all-runners.csv', all_runners)
    audit = {
        'date': date, 'model': MODEL, 'model_version': T.VERSION,
        'status': 'PREOFF_UNRESULTED_PAPER_ONLY',
        'ranked_races': len(selected), 'training_races': len(training),
        'trained_through': cutoff, 'historical_source_through': source_latest,
        'historical_source_age_days': age, 'cards': cards_audit,
        '1988_2021_verified_warmup_races': history_audit['counts'].get('verified_prior_races', 0),
        'cohort_alignment': cohort_audit,
        'historical_form_model': MODEL,
        'used_extended_core': True, 'used_extended_form': True,
        'used_verified_OR_and_weight': True, 'weight_audit': weight_audit,
        'extension_audit': extension_audit,
        'baseline_audit': baseline_audit,
        'hpr_status': hpr_audit.get('status', 'HPR historical preoff'),
        'hpr_coverage': dict(hpr_coverage),
        'skips': dict(skips),
        'original_d_unchanged': True,
        'no_2026_target_day_result_in_training': True,
        'no_real_betting_or_live_app_changes': True,
        'calibrated_win_probability_available': False,
        'warning': 'Research-only model scores are not calibrated win percentages; check updated non-runners and stale form.'
    }
    (args.out/'stage151-d142-core-form-weight-audit.json').write_text(json.dumps(audit, indent=2, default=str)+'\n')
    print('STAGE151_RANK1', json.dumps(selected), flush=True)
    print('STAGE151_AUDIT', json.dumps(audit, default=str), flush=True)


def self_test():
    assert MODEL == T.BOTH_WEIGHT == 'D142_LONG_CORE_FORM_OR_WEIGHT'
    assert 'horse_form_quality3' in T.FORM_FEATURES
    assert previous_quarter_end('2026-11-18') == '2026-09-30'
    assert previous_quarter_end('2027-01-15') == '2026-12-31'
    assert len(S.PILLARS) == 8
    print('STAGE151_SELF_TEST_OK')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['today', 'self-test'])
    p.add_argument('--db', type=pathlib.Path)
    p.add_argument('--reference', type=pathlib.Path)
    p.add_argument('--cards', type=pathlib.Path)
    p.add_argument('--date', default='')
    p.add_argument('--out', type=pathlib.Path)
    a = p.parse_args()
    if a.command == 'self-test':
        self_test()
    else:
        if not all((a.db, a.reference, a.cards, a.out)):
            p.error('The today command needs --db, --reference, --cards and --out')
        score_today(a)

# STAGE159_MODEL_INPUT_DRIVERS_V1
