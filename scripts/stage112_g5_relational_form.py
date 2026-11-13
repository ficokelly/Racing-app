#!/usr/bin/env python3
"""Stage 112: G5 odds-blind form networks and subsequent-race strength.

Independent research challenger to G3. Rank 1 only. Original G3 files/models are
never modified. All form, including rivals' NEXT starts, must predate the
selection date. SP is used only after selections for descriptive ROI.
"""
from __future__ import annotations
import argparse
from collections import defaultdict, deque, Counter
import datetime as dt
import json
import math
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
import stage108_model_g2 as G
import stage109_model_g3 as G3

VERSION = 'G5_GRAPH_RACE_STRENGTH_V1_2026_11_13'
BASE = list(G3.FEATURES)
NETWORK = ['g5_direct_delta', 'g5_direct_rivals', 'g5_shared_delta',
           'g5_shared_rivals', 'g5_shared_external_horses']
STRENGTH = ['g5_nextout_quality', 'g5_nextout_win_rate', 'g5_nextout_place_rate',
            'g5_nextout_count', 'g5_nextout_source_races']
VARIANTS = {'G5_NETWORK': NETWORK, 'G5_RACE_STRENGTH': STRENGTH,
            'G5_COMBINED': NETWORK + STRENGTH}
ZERO = {x: np.nan for x in NETWORK + STRENGTH}


def number(x):
    return G.num(x)


def capped(x, bound=18.):
    return min(bound, max(-bound, x))


def wmean(pairs):
    pairs = [(v, w) for v, w in pairs if w > 0 and v is not None and math.isfinite(v)]
    total = sum(w for _, w in pairs)
    return sum(v*w for v, w in pairs)/total if total else float('nan')


def adjusted_result(r, trip):
    """Weight-corrected performance within a race, in approximate lengths.

    The weight adjustment is a hypothesis to learn/test, not an absolute rating.
    Different prior race distances/surfaces are discounted when compared.
    """
    pos = number(r.get('finish_pos') if 'finish_pos' in r else r.get('position'))
    if pos is None or pos <= 0:
        return None
    beaten = G.beaten_lengths(r.get('beaten'))
    if pos == 1:
        beaten = 0.
    if beaten is None:
        beaten = min(30., (pos-1)*2.)  # explicit fallback, lower confidence
    wt = number(r.get('weight_lbs'))
    lbs_per_length = 3. if (trip or 0) < 9 else 2. if (trip or 0) < 18 else 1.5
    return capped(-beaten + ((wt-130.)/lbs_per_length if wt is not None else 0.), 30.)


class FormNetwork:
    """Dated race graph, updated one entire race DAY at a time."""
    def __init__(self):
        self.horses = defaultdict(lambda: deque(maxlen=6))
        self.next_out = defaultdict(dict)  # prior race -> horse -> its next-start outcome
        self.counters = Counter()

    @staticmethod
    def similarity(prior, current, day):
        days = (day-prior['date']).days
        if days <= 0 or days > 730:
            return 0.
        value = math.exp(-days/365.)
        distance = number(current.get('distance_f'))
        if distance is not None and prior['distance'] is not None:
            value *= max(.35, math.exp(-abs(distance-prior['distance'])/5.))
        kind, aw, _ = G.category(current)
        if kind != prior['kind']:
            value *= .55
        if aw != prior['aw']:
            value *= .55
        new_going = G.going_band(current.get('going'))
        if new_going and prior['going'] and new_going != prior['going']:
            value *= .8
        return value

    def edges(self, horse, current, day):
        by_opponent = defaultdict(list)
        for prior in self.horses.get(horse, ()):
            weight = self.similarity(prior, current, day)
            if not weight:
                continue
            for other, other_score in prior['peers'].items():
                if other == horse or other_score is None:
                    continue
                by_opponent[other].append((capped(prior['score']-other_score), weight))
        return {k: (wmean(v), sum(w for _, w in v)) for k, v in by_opponent.items()}

    def features(self, starters, day):
        """Peer comparisons use only results from days STRICTLY before day."""
        ids = {G.identity(r) for r in starters if G.identity(r)}
        edges = {G.identity(r): self.edges(G.identity(r), r, day)
                 for r in starters if G.identity(r)}
        result = {}
        for runner in starters:
            h = G.identity(runner)
            if not h:
                continue
            direct, shared = [], []
            direct_rivals, shared_rivals, external = set(), set(), set()
            for rival in ids-{h}:
                he, re = edges[h], edges.get(rival, {})
                if rival in he:
                    delta, weight = he[rival]
                    direct.append((delta, weight))
                    direct_rivals.add(rival)
                # Both have encountered a third horse, not in TODAY's field.
                for third in set(he).intersection(re)-ids:
                    first, fw = he[third]
                    second, sw = re[third]
                    if math.isfinite(first) and math.isfinite(second):
                        shared.append((capped(first-second), min(fw, sw)))
                        shared_rivals.add(rival)
                        external.add(third)
            quality, wins, places = [], [], []
            source_races = set()
            for prior in self.horses.get(h, ()):
                weight = self.similarity(prior, runner, day)
                if not weight:
                    continue
                # A horse's own next start is excluded: only its PREVIOUS rivals.
                for peer in prior['peers']:
                    if peer == h:
                        continue
                    e = self.next_out.get(prior['race_id'], {}).get(peer)
                    if not e or e['date'] >= day or e['quality'] is None:
                        continue
                    quality.append((e['quality'], weight))
                    wins.append((float(e['win']), weight))
                    places.append((float(e['place']), weight))
                    source_races.add(prior['race_id'])
            result[h] = {
                'g5_direct_delta': wmean(direct),
                'g5_direct_rivals': len(direct_rivals),
                'g5_shared_delta': wmean(shared),
                'g5_shared_rivals': len(shared_rivals),
                'g5_shared_external_horses': len(external),
                'g5_nextout_quality': wmean(quality),
                'g5_nextout_win_rate': wmean(wins),
                'g5_nextout_place_rate': wmean(places),
                'g5_nextout_count': len(quality),
                'g5_nextout_source_races': len(source_races),
            }
        return result

    def end_of_day(self, races):
        """Process the entire day's completed races only after its predictions."""
        additions = []
        for race in races:
            if not race:
                continue
            date = G.day(race[0]['date'])
            key = str(race[0]['race_id'])
            starters = [x for x in race if str(x.get('outcome') or '').lower() not in
                        ('nr', 'nonrunner', 'withdrawn', 'dns', 'scratched')]
            if len(starters) < 2:
                continue
            trip = number(race[0].get('distance_f'))
            peers = {G.identity(x): adjusted_result(x, trip) for x in starters if G.identity(x)}
            for x in starters:
                ident = G.identity(x)
                if not ident or peers[ident] is None:
                    continue
                position = number(x.get('finish_pos'))
                prior = self.horses[ident][-1] if self.horses.get(ident) else None
                if prior and prior['date'] < date:
                    # Only the FIRST subsequent start confirms the old race's form.
                    self.next_out[prior['race_id']][ident] = {
                        'date': date, 'quality': G.quality(x, len(starters)),
                        'win': position == 1, 'place': position is not None and position <= 3}
                kind, aw, _ = G.category(x)
                additions.append((ident, {
                    'date': date, 'race_id': key, 'score': peers[ident],
                    'peers': peers, 'distance': trip, 'kind': kind, 'aw': aw,
                    'going': G.going_band(x.get('going'))}))
        # Do not expose one runner's same-day result to another's prediction.
        for horse, run in additions:
            self.horses[horse].append(run)
        self.counters['races'] += len(races)
        self.counters['runs'] += len(additions)


def graph_features(db, reference):
    conn = sqlite3.connect('file:' + str(Path(db).resolve()) + '?mode=ro', uri=True)
    network = FormNetwork()
    pending = []
    current = None
    rows = []
    checks = Counter()
    for race in G.from_db(conn, '2022-01-01', '2025-12-31'):
        day = G.day(race[0]['date'])
        if current is not None and day != current:
            network.end_of_day(pending)
            pending = []
        current = day
        race_id = str(race[0]['race_id'])
        ref = reference.get(race_id)
        if not ref:
            checks['unmatched_races'] += 1
            continue
        expected_date, venue, rtype, title, region = ref
        if region not in ('GB', 'IRELAND'):
            continue
        if expected_date != race[0]['date'] or G.course_key(venue) != G.course_key(race[0]['course']):
            raise ValueError('Conflicting exact race identity: ' + race_id)
        runners = []
        for record in race:
            x = dict(record)
            x.update(course=venue, race_name=title, region_scope=region,
                     race_type=rtype or x.get('race_type'))
            runners.append(x)
        starters = [x for x in runners if str(x.get('outcome') or '').lower() not in
                    ('nr', 'nonrunner', 'withdrawn', 'dns', 'scratched')]
        winners = [x for x in starters if number(x.get('finish_pos')) == 1]
        if day >= G.day('2023-01-01') and len(starters) >= 4 and len(winners) == 1:
            features = network.features(starters, day)
            for x in starters:
                ident = G.identity(x)
                if ident:
                    rows.append({'race_id': race_id, 'identity': ident,
                                 **features.get(ident, ZERO)})
            checks['eligible_races'] += 1
        pending.append(runners)
    if pending:
        network.end_of_day(pending)
    conn.close()
    result = pd.DataFrame.from_records(rows)
    if result.duplicated(['race_id', 'identity']).any():
        raise AssertionError('Duplicate runner identity within race')
    return result, dict(checks), dict(network.counters)


def fit_model(data, cutoff, features, past_error_weights):
    training = data[(data.date >= '2023-01-01') & (data.date <= cutoff)]
    if training.race_id.nunique() < 150:
        raise RuntimeError('Insufficient pre-cutoff data')
    weights = [past_error_weights.get((str(rid), str(ident)), 1.)
               for rid, ident in zip(training.race_id, training.identity)]
    estimator = HistGradientBoostingClassifier(**G3.PARAMS)
    estimator.fit(G.Xframe(training).reindex(columns=features).astype(float)
                  if features == BASE else training.reindex(columns=features).apply(pd.to_numeric, errors='coerce').astype(float),
                  training.win.astype(int), sample_weight=np.asarray(weights, dtype=float))
    return estimator


def predict(model, data, columns, model_name, period, trained_through):
    if data.empty:
        return data
    if data.date.min() <= trained_through:
        raise AssertionError('Cannot score data within training window')
    scored = data.copy()
    x = scored.reindex(columns=columns).apply(pd.to_numeric, errors='coerce').astype(float)
    scored['raw_probability'] = model.predict_proba(x)[:, 1]
    scored = G.softmax_races(scored, 1.)
    scored = G.ranks(scored)
    scored['period'] = period
    scored['model'] = model_name
    return scored


def summary_frame(picks):
    records = []
    for grouping in [['model', 'period'], ['model', 'discipline'],
                     ['model', 'surface_class'],
                     ['model', 'discipline', 'surface_class'],
                     ['model', 'discipline', 'race_class_group'],
                     ['model', 'discipline', 'surface_class', 'race_class_group'],
                     ['model', 'race_subtype'],
                     ['model', 'period', 'discipline']]:
        for key, data in picks.groupby(grouping, dropna=False):
            group_values = dict(zip(grouping, key if isinstance(key, tuple) else (key,)))
            settled = data[(data.sp_decimal.notna()) & (data.sp_decimal > 1)]
            bets = len(settled)
            wins = int(settled.win.sum())
            returns = float(settled.loc[settled.win == 1, 'sp_decimal'].sum())
            records.append({**group_values, 'grouping': '/'.join(grouping),
                            'bets': bets, 'wins': wins,
                            'strike_pct': round(100*wins/bets, 2) if bets else None,
                            'roi_sp_pct': round(100*(returns/bets-1), 2) if bets else None,
                            'returns_units': round(returns, 3)})
    return pd.DataFrame(records)


def replay(data, g3_predictions):
    all_predictions = []
    for model_name, additions in VARIANTS.items():
        past_weights = {}
        for period, first, last, cutoff in G3.CHECKPOINTS:
            model = fit_model(data, cutoff, BASE + additions, past_weights)
            holdout = data[(data.date >= first) & (data.date <= last)]
            scored = predict(model, holdout, BASE + additions, model_name, period, cutoff)
            all_predictions.append(scored[scored['rank'] == 1].copy())
            updated, _ = G3.hard_example_weights(scored)
            if set(updated).intersection(past_weights):
                raise AssertionError('Reused historical missed-winner observation')
            past_weights.update(updated)
    challenger = pd.concat(all_predictions, ignore_index=True)
    old = g3_predictions
    eligible = (((old.period == '2024_PREQUENTIAL') & (old.model == 'SEED_2023')) |
                ((old.period != '2024_PREQUENTIAL') & (old.model == 'ROLLING_MISSED_WINNER')))
    old = old[eligible & (old['rank'] == 1)].copy()
    old['model'] = 'G3_FROZEN'
    for period, _, _, _ in G3.CHECKPOINTS:
        baseline_ids = set(old.loc[old.period == period, 'race_id'].astype(str))
        for variant in VARIANTS:
            candidate_ids = set(challenger.loc[(challenger.period == period) &
                  (challenger.model == variant), 'race_id'].astype(str))
            if baseline_ids != candidate_ids:
                raise RuntimeError(f'Unmatched G3/G5 evaluation races: {period}, {variant}; '
                                   f'G3={len(baseline_ids)}, G5={len(candidate_ids)}')
    picks = pd.concat([old, challenger], ignore_index=True)
    # Classification built from the original *pre-race* race title/features.
    # Handicap/non-handicap also stays available as its own breakdown.
    def subtype(r):
        kind = str(r['discipline'])
        if kind == 'nhflat': return 'NH Flat'
        if number(r.get('is_nursery')) == 1: return 'Nursery'
        if number(r.get('is_maiden')) == 1: return ('Maiden ' + kind).strip()
        if number(r.get('is_novice')) == 1: return ('Novice ' + kind).strip()
        if number(r.get('is_graded')) == 1: return ('Graded/Listed ' + kind).strip()
        if str(r['race_class_group']) == 'Handicap': return ('Handicap ' + kind).strip()
        return ('Other ' + kind).strip()
    picks['race_subtype'] = picks.apply(subtype, axis=1)
    return picks, summary_frame(picks)


def self_test():
    n = FormNetwork()
    def entry(h, rid, day, finish):
        return {'horse': h, 'horse_source_id': h, 'sire': '', 'dam': '',
                'race_id': rid, 'date': day, 'finish_pos': finish,
                'weight_lbs': 130, 'beaten': max(0, finish-1),
                'distance_f': 6, 'race_type': 'Flat', 'surface': 'AW',
                'going': 'Standard', 'outcome': 'ran'}
    a = [entry('A', 'R1', '2025-01-01', 2), entry('X', 'R1', '2025-01-01', 1),
         entry('P', 'R1', '2025-01-01', 3), entry('Q', 'R1', '2025-01-01', 4)]
    b = [entry('B', 'R2', '2025-01-02', 2), entry('X', 'R2', '2025-01-02', 1),
         entry('Y', 'R2', '2025-01-02', 3), entry('Z', 'R2', '2025-01-02', 4)]
    now = [entry(x, 'R3', '2025-01-04', 0) for x in ('A', 'B', 'C', 'D')]
    n.end_of_day([a])
    assert n.features(now, G.day('2025-01-02'))['ID|A']['g5_nextout_count'] == 0
    n.end_of_day([b])
    result = n.features(now, G.day('2025-01-04'))
    assert result['ID|A']['g5_shared_rivals'] == 1, result
    assert result['ID|A']['g5_shared_external_horses'] == 1, result
    assert result['ID|A']['g5_nextout_count'] >= 1, result
    # The outcome of X on Jan 2 must not be visible when scoring Jan 2.
    assert n.features(now, G.day('2025-01-02'))['ID|A']['g5_nextout_count'] == 0
    # Direct race comparison, which requires both today's runners in a prior field.
    direct = [entry(x, 'R4', '2025-01-05', pos) for x, pos in
              [('A', 1), ('B', 2), ('C', 3), ('D', 4)]]
    n.end_of_day([direct])
    later = n.features(now, G.day('2025-01-06'))
    assert later['ID|A']['g5_direct_rivals'] == 3, later
    print('G5 self-test PASSED: direct, common third horse, next-out, date cutoffs')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['self-test', 'historical'])
    parser.add_argument('--db')
    parser.add_argument('--reference')
    parser.add_argument('--g3-predictions')
    parser.add_argument('--out', default='reports/stage112')
    args = parser.parse_args()
    if args.mode == 'self-test':
        return self_test()
    if not args.db or not args.reference or not args.g3_predictions:
        raise ValueError('Historical DB, verified exact race mapping and original G3 predictions required')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ref, ref_audit = G.load_reference(args.reference, first='2022-01-01', last='2025-12-31')
    existing_start = G.TRAIN_START
    try:
        G.TRAIN_START = '2023-01-01'
        base, _, base_audit, _ = G.collect_historical(
            args.db, first='2022-01-01', last='2025-12-31', reference=ref)
    finally:
        G.TRAIN_START = existing_start
    base = G3.tidy_data(base)
    network, network_audit, ingested = graph_features(args.db, ref)
    data = base.merge(network, on=['race_id', 'identity'], how='left',
                      indicator=True, validate='one_to_one')
    joined_pct = round(100*(data['_merge'] == 'both').mean(), 2)
    if joined_pct < 95:
        raise RuntimeError(f'G5 historical graph join too low: {joined_pct}%')
    data.drop(columns=['_merge'], inplace=True)
    original = pd.read_csv(args.g3_predictions, compression='infer', low_memory=False,
                           usecols=['date', 'race_id', 'identity', 'horse', 'rank',
                                    'win', 'sp_decimal', 'period', 'model',
                                    'discipline', 'surface_class', 'race_class_group',
                                    'is_maiden', 'is_novice', 'is_nursery', 'is_graded'])
    selections, metrics = replay(data, original)
    selections[selections['rank'] == 1].to_csv(
        out/'stage112-g5-rank1-all-periods.csv.gz', index=False, compression='gzip')
    metrics.to_csv(out/'stage112-g5-rank1-by-race-type.csv', index=False)
    audit = {'version': VERSION, 'graph_join_pct': joined_pct,
             'available_feature_pct': {f: round(float(data[f].notna().mean()*100), 2)
                                       for f in NETWORK+STRENGTH},
             'base_audit': base_audit, 'reference_audit': ref_audit,
             'network_audit': network_audit, 'events': ingested,
             'g3_unchanged': True, 'price_input': 'NONE',
             'nextout_guard': 'Only next starts completed before prediction date',
             '2026_live_predictions': 'NOT PUBLISHED: must first reconcile 2026 form coverage'}
    (out/'stage112-g5-audit.json').write_text(json.dumps(audit, indent=2, default=str))
    report = ['# Stage112 — G5 independent odds-blind research', '',
              'Rank 1 only. G3 unchanged. SP return is a hindsight diagnostic, not available pre-off odds.',
              '', '| Model | Period | Rank 1 wins/bets | Strike rate | SP ROI |',
              '|---|---|---:|---:|---:|']
    period_metrics = metrics[metrics.grouping == 'model/period']
    for row in period_metrics.itertuples(index=False):
        report.append(f'| {row.model} | {row.period} | {row.wins}/{row.bets} | '
                      f'{row.strike_pct}% | {row.roi_sp_pct}% |')
    report += ['', 'See stage112-g5-rank1-by-race-type.csv for AW/Turf/Jumps and handicap splits.',
               'The future-scoring pipeline is deliberately withheld until the 2026 missed-form gap is audited.']
    (out/'README.md').write_text('\n'.join(report)+'\n')
    print('\n'.join(report))


if __name__ == '__main__':
    main()
