#!/usr/bin/env python3
"""Stage127: G5 quarterly walk-forward learner, UK/IE 2023-2025.

Training: 2023. Locked OOS prediction: 2024. Retrain using completed data
strictly before each calendar 2025 quarter (Mar/Jun/Sep/Dec checkpoints).
Retain original frozen G3 and same-cadence G3 as external/internal controls.
Model features are odds-blind, and ALL graph features are calculated by the
existing Stage112 strictly-prior-race-date engine. Prices are settled AFTER.

RESEARCH ONLY. Does NOT overwrite G3 or update the live app.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import joblib
from sklearn.metrics import log_loss

import numpy as np
import pandas as pd
import stage108_model_g2 as G
import stage109_model_g3 as G3
import stage112_g5_relational_form as G5

VERSION = 'G5_QUARTERLY_LEARNER_2026_11_14_V1'
CHECKPOINTS = (
    ('2024_HOLDOUT', '2024-01-01', '2024-12-31', '2023-12-31'),
    ('2025_Q1', '2025-01-01', '2025-03-31', '2024-12-31'),
    ('2025_Q2', '2025-04-01', '2025-06-30', '2025-03-31'),
    ('2025_Q3', '2025-07-01', '2025-09-30', '2025-06-30'),
    ('2025_Q4', '2025-10-01', '2025-12-31', '2025-09-30'),
)
VARIANTS = {
    'G3_QUARTERLY_CONTROL': (G5.BASE, 'g3'),
    'G5_NETWORK_QUARTERLY': (G5.BASE + G5.NETWORK, 'g3'),
    'G5_STRENGTH_QUARTERLY': (G5.BASE + G5.STRENGTH, 'g3'),
    'G5_COMBINED_QUARTERLY': (G5.BASE + G5.NETWORK + G5.STRENGTH, 'g3'),
    'G5_ADAPTIVE_QUARTERLY': (G5.BASE + G5.NETWORK + G5.STRENGTH, 'adaptive'),
}
TARGETED_SIGNALS = G5.NETWORK + G5.STRENGTH
RANK1_COLUMNS = ['model', 'period', 'date', 'race_id', 'course', 'race_name', 'horse',
                 'identity', 'discipline', 'surface_class', 'race_class_group',
                 'is_maiden', 'is_novice', 'is_nursery', 'is_graded', 'rank',
                 'win_probability', 'prior_runs', 'win', 'sp_decimal']


def period_of(date):
    date = str(date)
    for name, first, last, _ in CHECKPOINTS:
        if first <= date <= last:
            return name
    return None


def update_weights(existing, predictions, policy):
    """Learn ONLY from winners and mistakes in the JUST FINISHED period.

    Default: exactly the historical G3 missed winner policy (winner rank >2).
    Adaptive: every losing Rank 1 gives the true winner extra weight, including
    cases where it was previously rank 2; wrong leader gets modest weight too.
    No response-dependent hyperparameter changes and no odds-driven adjustment.
    """
    if policy == 'g3':
        new, count = G3.hard_example_weights(predictions)
    elif policy == 'adaptive':
        new = {}
        count = Counter()
        for rid, race in predictions.groupby('race_id', sort=False):
            winner = race.loc[race.win == 1]
            first = race.loc[race['rank'] == 1]
            if len(winner) != 1 or len(first) != 1:
                raise RuntimeError('Invalid completed race for adaptive learning: ' + str(rid))
            w = winner.iloc[0]
            leader = first.iloc[0]
            if int(w['rank']) > 1:
                new[(str(rid), str(w.identity))] = 1.60
                new[(str(rid), str(leader.identity))] = 1.20
                count['missed_rank1_winners'] += 1
            if int(w['rank']) > 2:
                count['missed_rank2_winners'] += 1
    else:
        raise ValueError('Unknown learning policy')
    duplicate = set(new).intersection(existing)
    if duplicate:
        raise AssertionError(f'Duplicate learning example across periods: {list(duplicate)[:3]}')
    existing.update(new)
    return dict(count)


def subtype(row):
    kind = str(row.get('discipline') or 'unknown')
    if kind == 'nhflat':
        return 'NH Flat / Bumper'
    for key, label in [('is_nursery', 'Nursery'), ('is_maiden', 'Maiden'),
                       ('is_novice', 'Novice'), ('is_graded', 'Graded/Listed')]:
        if G.num(row.get(key)) == 1:
            return f'{label} {kind}'
    if str(row.get('race_class_group')) == 'Handicap':
        return 'Handicap ' + kind
    return 'Other ' + kind


def discipline_group(row):
    d = str(row.get('discipline') or 'unknown')
    surface = str(row.get('surface_class') or '').lower()
    if d == 'flat':
        if 'all-weather' in surface or surface in ('aw', 'all_weather'):
            return 'All-Weather Flat'
        if 'turf' in surface:
            return 'Flat Turf'
        return 'Flat / surface unknown'
    return {'hurdle': 'Hurdles', 'chase': 'Chases',
            'nhflat': 'NH Flat / Bumper'}.get(d, 'Other / unknown')


def summary(rows, keys):
    out = []
    for group, item in rows.groupby(keys, dropna=False):
        v = dict(zip(keys, group if isinstance(group, tuple) else (group,)))
        # Track missing SP separately; NEVER treat missing SP as a loser.
        valid = item.loc[item.sp_decimal.notna() & (item.sp_decimal > 1)]
        n = len(valid)
        w = int(valid.win.sum())
        returns = float(valid.loc[valid.win == 1, 'sp_decimal'].sum())
        out.append({**v, 'selections': len(item), 'bets_with_sp': n,
                    'missing_sp': len(item) - n, 'wins': w,
                    'strike_pct': round(100*w/n, 3) if n else np.nan,
                    'returns_units': round(returns, 3),
                    'profit_units': round(returns-n, 3),
                    'roi_sp_pct': round(100*(returns/n-1), 3) if n else np.nan})
    return pd.DataFrame.from_records(out)


def error_audit(full, limit=None):
    """Data-driven diagnostic of misses; doesn't change any predictions."""
    rows = []
    for rid, part in full.groupby('race_id', sort=False):
        leader = part.loc[part['rank'] == 1]
        winner = part.loc[part.win == 1]
        if len(leader) != 1 or len(winner) != 1:
            continue
        leader = leader.iloc[0]
        winner = winner.iloc[0]
        if int(winner['rank']) == 1:
            continue
        x = {'model': str(leader.model), 'period': str(leader.period),
             'date': str(leader.date), 'course': str(leader.course),
             'race_id': str(rid), 'race_name': str(leader.race_name),
             'wrong_rank1': str(leader.horse), 'actual_winner': str(winner.horse),
             'winner_rank': int(winner['rank']),
             'wrong_rank1_win_pct': round(float(100*leader.win_probability), 3),
             'winner_win_pct': round(float(100*winner.win_probability), 3),
             'probability_gap_pp': round(100*float(leader.win_probability - winner.win_probability), 3),
             'winner_sp_decimal': winner.sp_decimal}
        for key in TARGETED_SIGNALS:
            x[key + '_rank1'] = leader.get(key)
            x[key + '_winner'] = winner.get(key)
        rows.append(x)
    frame = pd.DataFrame(rows)
    if limit:
        return frame.head(limit)
    return frame



def feature_reliance(model, scored, columns, model_name, period):
    """Descriptive GROUP permutation sensitivity after the period is locked.

    Not causal evidence, not a feature-selection rule for the same period.
    Fixed-seed sample limits runtime without biasing model training.
    """
    if 'g5_direct_delta' not in columns:
        return []
    unique = np.asarray(sorted(scored.race_id.unique()))
    rng = np.random.default_rng(127)
    selected = rng.choice(unique, size=min(200, len(unique)), replace=False)
    data = scored.loc[scored.race_id.isin(selected)]
    x = data.reindex(columns=columns).apply(pd.to_numeric, errors='coerce').astype(float)
    target = data.win.astype(int).to_numpy()
    pred = model.predict_proba(x)[:,1]
    baseline = float(log_loss(target, pred, labels=[0,1]))
    groups = {'Direct meetings': G5.NETWORK[:2],
              'Shared opponents': G5.NETWORK[2:],
              'Subsequent race strength': G5.STRENGTH}
    out=[]
    for label, feats in groups.items():
        if not set(feats).issubset(columns):
            continue
        if x[feats].isna().all().all():
            out.append({'model':model_name,'period':period,'signal_group':label,
                        'sample_races':len(selected),'baseline_logloss':baseline,
                        'permuted_logloss':None,'loss_increase':None,
                        'note':'Features entirely missing'})
            continue
        shuffled = x.copy()
        # Shuffle one group together, preserving within-row correlation.
        order = rng.permutation(len(shuffled))
        shuffled.loc[:,feats] = x[feats].to_numpy()[order]
        changed = float(log_loss(target, model.predict_proba(shuffled)[:,1], labels=[0,1]))
        out.append({'model':model_name,'period':period,'signal_group':label,
                    'sample_races':len(selected),'baseline_logloss':round(baseline,6),
                    'permuted_logloss':round(changed,6),
                    'loss_increase':round(changed-baseline,6),
                    'note':'Positive means predictive reliance; not proof of causal benefit'})
    return out


def load_research_inputs(args):
    ref, ref_audit = G.load_reference(args.reference, first='2022-01-01', last='2025-12-31')
    previous_start = G.TRAIN_START
    try:
        G.TRAIN_START = '2023-01-01'
        base, _, base_audit, race_count = G.collect_historical(
            args.db, first='2022-01-01', last='2025-12-31', reference=ref)
    finally:
        G.TRAIN_START = previous_start
    base = G3.tidy_data(base)
    graph, graph_audit, graph_stats = G5.graph_features(args.db, ref)
    data = base.merge(graph, on=['race_id', 'identity'],
                      how='left', validate='one_to_one', indicator=True)
    if (data['_merge'] != 'both').any():
        mismatched = int((data['_merge'] != 'both').sum())
        raise AssertionError(f'G5 exact-ID form graph did not match {mismatched} training rows')
    data.drop(columns='_merge', inplace=True)
    if data.duplicated(['race_id', 'identity']).any():
        raise AssertionError('Duplicate runner in modelling data')
    baseline = pd.read_csv(args.g3_predictions, compression='infer', low_memory=False,
                           usecols=['date','race_id','identity','horse','rank',
                                    'win','sp_decimal','period','model','discipline',
                                    'surface_class','race_class_group','course','race_name',
                                    'is_maiden','is_novice','is_nursery','is_graded',
                                    'win_probability','prior_runs'],
                           dtype={'race_id':str, 'identity':str})
    select = (((baseline.period == '2024_PREQUENTIAL') & (baseline.model == 'SEED_2023')) |
              ((baseline.period != '2024_PREQUENTIAL') & (baseline.model == 'ROLLING_MISSED_WINNER')))
    baseline = baseline.loc[select & (baseline['rank'] == 1)].copy()
    baseline['model'] = 'G3_FROZEN_ARCHIVED'
    baseline['period'] = baseline['date'].map(period_of)
    baseline = baseline.loc[baseline.period.notna()]
    keep = ['date','race_id','identity','horse','win','sp_decimal']
    # Verify all archived baseline race outcomes agree with recovered G5 training.
    right = data.loc[data.win == 1, ['race_id','identity']].rename(columns={'identity':'verified_winner_identity'})
    check = baseline.merge(right, on='race_id', how='left', validate='many_to_one')
    if check.verified_winner_identity.isna().any():
        raise AssertionError('Missing verified winner in frozen baseline race IDs')
    if (check['win'] != (check.identity == check.verified_winner_identity).astype(int)).any():
        raise AssertionError('Frozen G3 outcomes conflict with G5 source, stop for data audit')
    return data, baseline, dict(ref_audit=ref_audit, base_audit=base_audit,
                                source_races=race_count, graph_audit=graph_audit,
                                graph_stats=graph_stats)


def run(args):
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    data, frozen, source_checks = load_research_inputs(args)
    if set(data.date.unique()) - {x for x in data.date.unique() if period_of(x) or str(x).startswith('2023')}:
        raise AssertionError('Unexpected dates in training data')

    pick_frames = [frozen]
    fit_history = []
    mistakes = []
    feature_diagnostics = []
    saved_model_names = []
    for name, (columns, policy) in VARIANTS.items():
        error_ledger = {}
        for period, first, last, through in CHECKPOINTS:
            train = data.loc[(data.date >= '2023-01-01') & (data.date <= through)]
            holdout = data.loc[(data.date >= first) & (data.date <= last)]
            if train.empty or holdout.empty:
                raise RuntimeError('Missing training or evaluation data: ' + period)
            if train.date.max() >= holdout.date.min():
                raise AssertionError('Hindsight training leak')
            model = G5.fit_model(data, through, columns, error_ledger)
            scored = G5.predict(model, holdout, columns, name, period, through)
            if scored.empty or scored.race_id.nunique() != holdout.race_id.nunique():
                raise AssertionError('Scored race count mismatch: ' + period)
            if scored.loc[scored['rank'] == 1, 'race_id'].duplicated().any():
                raise AssertionError('More than one rank1 per race')
            picks = scored.loc[scored['rank'] == 1].copy()
            pick_frames.append(picks)
            if name in ('G5_COMBINED_QUARTERLY', 'G5_ADAPTIVE_QUARTERLY'):
                feature_diagnostics.extend(feature_reliance(model, scored, columns, name, period))
            audit = error_audit(scored) if name in ('G5_COMBINED_QUARTERLY',
                                                  'G5_ADAPTIVE_QUARTERLY') else pd.DataFrame()
            if not audit.empty:
                mistakes.append(audit)
            previously_weighted = len(error_ledger)
            update = update_weights(error_ledger, scored, policy)
            new_weighted = len(error_ledger) - previously_weighted
            fit_history.append({'model': name, 'period': period,
                                'trained_through': through, 'test_start': first,
                                'test_end': last, 'train_races': int(train.race_id.nunique()),
                                'test_races': int(holdout.race_id.nunique()),
                                'prior_learning_weighted_cases': previously_weighted,
                                'new_learning_cases': new_weighted,
                                'mistakes': update})
            n1 = len(picks); wins1 = int(picks.win.sum())
            print(name, period, f'Rank1 {wins1}/{n1}, trained through {through}', flush=True)
        # 2025 outcomes are now allowed: produce RESEARCH-ONLY model bundles.
        # Do not score 2026 until the form-bridge/data-coverage audit passes.
        if name.startswith('G5_'):
            final_model = G5.fit_model(data, '2025-12-31', columns, error_ledger)
            bundle = {'version':VERSION, 'model':name, 'trained_through':'2025-12-31',
                      'features':columns, 'estimator':final_model,
                      'learning_cases':len(error_ledger), 'odds_input':'NONE',
                      'deployable':False, 'requires_2026_form_repair':True}
            path = output/('stage127-'+name.lower()+'-2025-final.joblib')
            joblib.dump(bundle, path, compress=3)
            saved_model_names.append(path.name)
    picks = pd.concat(pick_frames, ignore_index=True, sort=False)
    picks['period'] = picks.date.map(period_of)
    if picks.period.isna().any():
        raise AssertionError('Predictions outside intended evaluation dates')
    picks['discipline_group'] = picks.apply(discipline_group, axis=1)
    picks['race_subtype'] = picks.apply(subtype, axis=1)
    keys = ['period', 'race_id']
    by_model = {m: p for m, p in picks.groupby('model')}
    baseline_race_ids = set(map(tuple, by_model['G3_FROZEN_ARCHIVED'][keys].to_numpy()))
    for model, part in by_model.items():
        set_id = set(map(tuple, part[keys].to_numpy()))
        if set_id != baseline_race_ids:
            raise AssertionError(f'Unmatched races G3 vs {model}: '
                                 f'missing={len(baseline_race_ids-set_id)} extra={len(set_id-baseline_race_ids)}')
    # The 2024 same-cadence G3 baseline is expected to reproduce the frozen
    # 2023-trained seed exactly. This is an unusually useful integrity check.
    c = by_model['G3_QUARTERLY_CONTROL']
    f = by_model['G3_FROZEN_ARCHIVED']
    c24 = c.loc[c.period == '2024_HOLDOUT', ['race_id','identity']]
    f24 = f.loc[f.period == '2024_HOLDOUT', ['race_id','identity']]
    cross = c24.merge(f24, on='race_id', how='inner', suffixes=('_rebuilt','_frozen'), validate='one_to_one')
    seed_mismatch = int((cross.identity_rebuilt != cross.identity_frozen).sum())
    if seed_mismatch:
        raise RuntimeError(f'2024 G3 seed not reproducible ({seed_mismatch} differing Rank 1 picks). '
                           'Do not report a G5 improvement until history/input parity is diagnosed.')
    # Observed SP can be missing; compare on exact identical settled races only.
    sp_ok = picks.groupby(['period','race_id']).sp_decimal.apply(lambda x: (x.notna() & (x > 1)).all())
    shared_sp = set(sp_ok[sp_ok].index.to_list())
    matched = picks.loc[[tuple(x) in shared_sp for x in picks[keys].to_numpy()]].copy()
    if matched.empty:
        raise AssertionError('No paired SP observations')
    def tables(source, suffix):
        sets = {
            'period': ['model','period'],
            'year': ['model','year'],
            'discipline': ['model','discipline_group'],
            'race-type': ['model','race_subtype'],
            'period-discipline': ['model','period','discipline_group'],
            'period-type': ['model','period','race_subtype'],
            'discipline-handicap': ['model','discipline_group','race_class_group'],
            'year-discipline': ['model','year','discipline_group']
        }
        source = source.copy()
        source['year'] = source.date.str[:4]
        for label, grouping in sets.items():
            summary(source, grouping).to_csv(output/f'g5-quarterly-{suffix}-{label}.csv', index=False)
    tables(picks, 'full')
    tables(matched, 'matched')
    picks.reindex(columns=list(dict.fromkeys(RANK1_COLUMNS + ['discipline_group', 'race_subtype'] + TARGETED_SIGNALS)))\
         .to_csv(output/'g5-quarterly-rank1-selections.csv.gz', index=False, compression='gzip')
    if mistakes:
        pd.concat(mistakes, ignore_index=True).to_csv(output/'g5-quarterly-missed-winners.csv.gz',
                                                     index=False, compression='gzip')
    pd.DataFrame(fit_history).to_json(output/'g5-quarterly-learning-checkpoints.json',
                                       orient='records', indent=2)
    pd.DataFrame(feature_diagnostics).to_csv(output/'g5-quarterly-feature-reliance.csv', index=False)
    full = summary(picks, ['model','period'])
    by_year = summary(picks.assign(year=picks.date.str[:4]), ['model','year'])
    audit = {'version': VERSION, 'mode': '2023 train, 2024 unseen, 2025 Q1-Q4 chronological',
             'no_live_changes': True, 'g3_frozen': True, 'odds_input': 'NONE',
             'sp_diagnostics_only': True, 'rank': 1,
             'same_day_form_not_used': True,
             'historical_pre_race_features_only': True,
             'matched_sp_races': len(shared_sp), 'research_model_bundles': saved_model_names,
             '2024_frozen_g3_reproduction_mismatches': seed_mismatch,
             'source': source_checks,
             'network_feature_coverage_pct': {
                 key: round(100*float(data[key].notna().mean()), 2) for key in TARGETED_SIGNALS},
             'original_g3_calendar': '2024 full year, 2025 Jan-Apr, May-Aug, Sep-Dec; archived G3 not quarterly',
             'quarters': [{ 'period': p, 'test_start': f, 'test_end': l, 'trained_through':t }
                          for p, f, l, t in CHECKPOINTS]}
    (output/'g5-quarterly-integrity.json').write_text(json.dumps(audit, indent=2, default=str))
    report = ['# G5 quarterly, odds-blind, Rank 1 only', '',
              'Train only on 2023, score full 2024, then update after each completed 2025 quarter.',
              'G3 archived comparator has a different 2025 four-month update schedule.',
              'G3 quarterly control is retrained on the SAME quarterly schedule as G5.',
              'No odds are in training or ranking. SP is retrospective ONLY.\n',
              '## Yearly results (all eligible Rank 1 with SP)',
              '| Model | Year | Wins/Bets | Strike % | ROI at SP % |',
              '|---|---|---:|---:|---:|']
    for x in by_year.itertuples(index=False):
        report.append(f'| {x.model} | {x.year} | {x.wins}/{x.bets_with_sp} | '
                      f'{x.strike_pct:.2f} | {x.roi_sp_pct:.2f} |')
    report += ['', '## 2024 holdout and 2025 quarterly checkpoints',
               '| Model | Period | Wins/Bets | Strike % | ROI at SP % |',
               '|---|---|---:|---:|---:|']
    for x in full.itertuples(index=False):
        report.append(f'| {x.model} | {x.period} | {x.wins}/{x.bets_with_sp} | '
                      f'{x.strike_pct:.2f} | {x.roi_sp_pct:.2f} |')
    report += ['', 'Additional CSVs: race types, discipline, handicaps, yearly, quarters and paired settlements.',
               'Read g5-quarterly-learning-checkpoints.json for chronological learning audits.',
               'Feature-reliance permutations are diagnostic only and cannot prove causal benefit.',
               'Four 2025-trained G5 research bundles are saved but NOT deployed.',
               'No evidence of new profitability should be asserted until actual backtest finishes.']
    (output/'README.md').write_text('\n'.join(report)+'\n')
    print('\n'.join(report), flush=True)


def self_test():
    assert [r[0] for r in CHECKPOINTS] == ['2024_HOLDOUT','2025_Q1','2025_Q2','2025_Q3','2025_Q4']
    assert period_of('2023-12-31') is None
    assert period_of('2024-01-01') == '2024_HOLDOUT'
    assert period_of('2025-03-31') == '2025_Q1'
    assert period_of('2025-04-01') == '2025_Q2'
    assert period_of('2025-06-30') == '2025_Q2'
    assert period_of('2025-07-01') == '2025_Q3'
    assert period_of('2025-10-01') == '2025_Q4'
    assert period_of('2026-01-01') is None
    for i, (period, start, end, cutoff) in enumerate(CHECKPOINTS):
        assert cutoff < start <= end
        if i:
            assert CHECKPOINTS[i-1][2] == cutoff
    example = pd.DataFrame([
        {'race_id':'R1','identity':'X','rank':1,'win':0},
        {'race_id':'R1','identity':'Y','rank':2,'win':1},
        {'race_id':'R1','identity':'Z','rank':3,'win':0},
        {'race_id':'R2','identity':'A','rank':1,'win':1},
        {'race_id':'R2','identity':'B','rank':2,'win':0},
        {'race_id':'R2','identity':'C','rank':3,'win':0},
    ])
    a = {}
    x = update_weights(a, example, 'adaptive')
    assert x['missed_rank1_winners'] == 1
    assert a == {('R1','Y'):1.60,('R1','X'):1.20}
    b = {}
    update_weights(b, example, 'g3')
    assert b == {('R1','X'):1.20}, 'Default G3 downweights no rank2 winner; it only reweights wrong leader'
    for m in VARIANTS:
        assert not any('odd' in f or 'sp_' in f or 'price' in f for f in VARIANTS[m][0])
    G5.self_test()
    print('Stage127 self-test PASSED: strict quarterly cutoffs, training isolation, adaptive errors, odds-blind features')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['self-test','historical'])
    p.add_argument('--db')
    p.add_argument('--reference')
    p.add_argument('--g3-predictions')
    p.add_argument('--out', default='reports/stage127/output')
    args = p.parse_args()
    if args.mode == 'self-test':
        return self_test()
    if not args.db or not args.reference or not args.g3_predictions:
        p.error('Historical mode requires --db, --reference and --g3-predictions')
    run(args)

if __name__ == '__main__':
    main()
