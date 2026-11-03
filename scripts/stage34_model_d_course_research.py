#!/usr/bin/env python3
"""Stage 34: independent Model D discipline / racecourse / course-trip research.

Research only: this deliberately does NOT alter Stage 33 live Model D, A, B or C.
All form features are the eight Stage 30 pre-race features computed from earlier
calendar dates across the available archive. No odds are predictive inputs.
SP is used AFTER the race only for historical one-unit betting settlements.

Model-selection protocol:
  2023: discover candidate combinations (weights + minimum score lead)
  2024: assess each frozen challenger against its parent, same eligible races
  2025: report results of frozen, promoted or fallback models without tuning
The 2025 split was already seen by previous research, so it is a *new-configuration
audit*, not a genuinely never-viewed program-wide holdout. Forward 2026 remains key.

Each Flat Turf, All-Weather and Jumps discipline gets its own assessment.
Every UK/Irish course gets a separately evaluated course-level profile.
Each course + distance bucket has a narrower optional refinement, but only if
independent sample checks justify it. Insufficient groups fall back.
Older results contribute past form and course/draw diagnostics, never as leaked
post-race features for the target race.
"""
from __future__ import annotations
import argparse
import collections as C
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import stage30_gap_aware_abcd as OLD

VERSION = "stage34-d-course-distance-validation-v1"
METRICS = tuple(OLD.METRICS)
assert METRICS == ("Ability", "Trainer", "Jockey", "Course",
                   "Draw", "Distance", "Ground", "Jumping")
GATES = (0.04, 0.08, 0.14, 0.20, 0.28, 0.36)
BASE_GATE = 0.14

# Distinct distances are essential. Chester 5f and 6f are NOT merged.
def trip_of(kind, furlongs):
    f = OLD.number(furlongs)
    if f is None or f <= 0:
        return "distance_unverified"
    if kind == "jumps":
        if f < 16:
            return "under_16f"
        if f < 20:
            return "16_to_19f"
        if f < 24:
            return "20_to_23f"
        if f < 28:
            return "24_to_27f"
        return "28f_plus"
    if f < 5.5:
        return "5f_or_shorter"
    if f < 6.5:
        return "6f"
    if f < 7.5:
        return "7f"
    if f < 8.5:
        return "8f"
    if f < 9.5:
        return "9f"
    if f < 10.5:
        return "10f"
    if f < 12.5:
        return "11_to_12f"
    if f < 14.5:
        return "13_to_14f"
    return "15f_plus"


class CoursePast(OLD.Past):
    """Keep Stage 30 scoring identical; append study-only metadata and draw audit."""
    def __init__(self):
        super().__init__({})
        self.draw_stats = C.defaultdict(lambda: {
            "races": 0, "runners": [0, 0, 0], "wins": [0, 0, 0],
            "expected_wins": [0., 0., 0.], "priced": [0, 0, 0],
            "net_sp_units": [0., 0., 0.]})
        self.oldest_draw_date = None
        self.newest_draw_date = None

    def score(self, rows):
        result = super().score(rows)
        if result is not None:
            result["course_key"] = OLD.track(rows[0][2])
            result["trip_key"] = trip_of(result["kind"], rows[0][5])
            result["distance_f"] = OLD.number(rows[0][5])
        return result

    def update(self, rows):
        self._record_draw(rows)
        return super().update(rows)

    def _record_draw(self, rows):
        first = rows[0]
        date = OLD.date(first[1])
        course = OLD.track(first[2])
        if not date or course not in OLD.TRACKS:
            return
        discipline = OLD.style(first)
        if discipline == "jumps":
            return
        group = [r for r in rows if OLD.started(r)]
        # Require a full field of known unique drawn stalls. Missing stalls cannot
        # be interpreted as a low or high draw and are not imputed.
        if len(group) < 8 or sum(r[12] == 1 for r in group) != 1:
            return
        drawn = [(OLD.number(r[21]), r) for r in group]
        if any(n is None or n <= 0 for n, _ in drawn):
            return
        if len({n for n, _ in drawn}) != len(drawn):
            return
        drawn.sort(key=lambda a: a[0])
        n = len(drawn)
        window = ("before_2023" if date.year < 2023
                  else "2023_discovery" if date.year == 2023
                  else "2024_validation" if date.year == 2024
                  else "2025_audit" if date.year == 2025
                  else "after_2025")
        trip = trip_of(discipline, first[5])
        # Separate overall-course and course-distance observations.
        for t in ("ALL_TRIPS", trip):
            row = self.draw_stats[(discipline, course, t, window)]
            row["races"] += 1
            for rank, (_, horse) in enumerate(drawn):
                bucket = min(2, 3 * rank // n)  # low / middle / high
                won = horse[12] == 1
                row["runners"][bucket] += 1
                row["wins"][bucket] += int(won)
                row["expected_wins"][bucket] += 1 / n
                sp = OLD.number(horse[20])
                if sp and math.isfinite(sp) and 1.01 <= sp <= 5000:
                    row["priced"][bucket] += 1
                    row["net_sp_units"][bucket] += sp * int(won) - 1
        s = str(date)
        if self.oldest_draw_date is None or s < self.oldest_draw_date:
            self.oldest_draw_date = s
        if self.newest_draw_date is None or s > self.newest_draw_date:
            self.newest_draw_date = s


def digest_seed(*parts):
    d = "|".join(str(x) for x in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(d).digest()[:8], "big")


def normalize(weights):
    v = np.asarray(weights, dtype=float)
    if v.shape != (8,) or not np.isfinite(v).all() or any(v < 0):
        raise ValueError("Invalid eight-factor weights")
    s = float(v.sum())
    if s <= 0:
        raise ValueError("Zero weights")
    return v / s


def pool(parent, scope, key, n):
    """Deterministic candidates centred on parent, plus exploratory variants."""
    rng = np.random.default_rng(digest_seed(VERSION, scope, key))
    parent = normalize(parent)
    out = [parent.copy(), np.ones(8) / 8]
    # Test concentration on each metric without allowing unbounded 100% weights.
    for i in range(8):
        p = parent * 0.40
        p[i] += 0.60
        out.append(normalize(p))
    # Multiple strengths of shrinkage around a sensible prior.
    while len(out) < n:
        i = len(out)
        if i % 4 == 0:
            candidate = rng.dirichlet(np.maximum(0.20, parent * 8))
        elif i % 4 == 1:
            candidate = rng.dirichlet(np.maximum(0.20, parent * 30))
        elif i % 4 == 2:
            candidate = parent * np.exp(rng.normal(0, 0.50, 8))
        else:
            candidate = rng.dirichlet(np.ones(8) * 1.8)
        out.append(normalize(candidate))
    return out[:n]


def effective_weights(candidate, parent, count, scope):
    if scope == "discipline":
        share = min(0.94, count / (count + 150.))
    elif scope == "course":
        share = min(0.70, count / (count + 175.))
    else:
        share = min(0.45, count / (count + 260.))
    return normalize(share * normalize(candidate) +
                     (1 - share) * normalize(parent)), round(float(share), 4)


def forecast(races, weights):
    """Reuses Stage 30's frozen scoring formula and safety masking."""
    return OLD.prediction(races, np.asarray(weights, dtype=np.float32), "D")


def performance(races, weights, gate):
    return OLD.evaluate(forecast(races, weights), gate)


def sufficient(metrics, n, minimum, coverage=85.):
    return (n >= minimum and metrics["priced_bets"] >= minimum and
            (metrics.get("price_coverage_pct") or 0) >= coverage and
            metrics.get("roi_pct") is not None)


def robust_objective(m):
    """Conservative historic profitability; stress-test large-priced winners."""
    if not m["priced_bets"]:
        return -1.e6
    vals = (m["roi_pct"], m["roi_capped_20to1_pct"],
            m["roi_without_largest_winner_pct"])
    if any(v is None for v in vals):
        return -1.e6
    # Cap backtest impact from single large-SP winner.
    return (0.50 * vals[0] + 0.30 * vals[1] + 0.20 * vals[2]
            - 18. / math.sqrt(m["priced_bets"]))


def choose(parent, base_gate, y23, y24, scope, key):
    """2023 picks one candidate; 2024 independently accepts/rejects it.

    No 2025 labels enter any choice or promotion check. 2024 does not select a
    different candidate; it tests just the candidate frozen on 2023.
    """
    sizes = {"discipline": (300, 300, 95, 95, 160),
             "course": (50, 50, 22, 22, 150),
             "trip": (35, 35, 13, 13, 100)}
    min_races23, min_races24, min_bets23, min_bets24, draws = sizes[scope]
    decision = {"scope": scope, "key": key, "races_2023": len(y23),
                "races_2024": len(y24), "candidates_tested": 0,
                "weights_pct": weights_pct(parent), "selection_margin": base_gate,
                "status": "PARENT_FALLBACK", "shrinkage_share": 0.,
                "improvement_roi_pp_2024": None,
                "discovery": None, "validation": None}
    if len(y23) < min_races23 or len(y24) < min_races24:
        decision["status"] = "INSUFFICIENT_RACES"
        return decision
    parent24 = performance(y24, parent, base_gate)
    parent23 = performance(y23, parent, base_gate)
    decision["parent_2024"] = parent24
    decision["parent_2023"] = parent23
    candidates = pool(parent, scope, key, draws)
    best = None
    gates = (base_gate,) + tuple(g for g in GATES if g != base_gate)
    for w in candidates:
        blended, share = effective_weights(w, parent, len(y23), scope)
        predictions = forecast(y23, blended)
        for gate in gates:
            metric = OLD.evaluate(predictions, gate)
            decision["candidates_tested"] += 1
            if not sufficient(metric, len(y23), min_bets23):
                continue
            v = robust_objective(metric)
            # Prefer some coverage over tiny sample maximisation.
            v += 1.5 * math.log1p(metric["priced_bets"])
            if best is None or v > best["objective"]:
                best = {"objective": v, "weights": blended,
                        "gate": gate, "discovery": metric, "share": share}
    if best is None:
        decision["status"] = "NO_STABLE_2023_CANDIDATE"
        return decision
    validate = performance(y24, best["weights"], best["gate"])
    decision["candidate_weights_pct"] = weights_pct(best["weights"])
    decision["candidate_margin"] = best["gate"]
    decision["discovery"] = best["discovery"]
    decision["validation"] = validate
    decision["shrinkage_share"] = best["share"]
    decision["improvement_roi_pp_2024"] = (round(validate["roi_pct"] -
                                              parent24["roi_pct"], 3)
                                          if validate["roi_pct"] is not None and
                                             parent24["roi_pct"] is not None else None)
    # A small 2024 sample cannot promote a track-only forecast. Even with
    # enough data, demand non-inferiority on strike and capped ROI.
    if not sufficient(validate, len(y24), min_bets24):
        decision["status"] = "2024_VALIDATION_INSUFFICIENT"
        return decision
    if not sufficient(parent24, len(y24), max(8, min_bets24 // 2)):
        decision["status"] = "PARENT_2024_COMPARISON_INSUFFICIENT"
        return decision
    v_roi = float(validate["roi_pct"])
    p_roi = float(parent24["roi_pct"])
    v_cap = float(validate["roi_capped_20to1_pct"])
    p_cap = float(parent24["roi_capped_20to1_pct"])
    v_without = float(validate["roi_without_largest_winner_pct"])
    p_without = float(parent24["roi_without_largest_winner_pct"])
    strike = float(validate["strike_pct"])
    parent_strike = float(parent24["strike_pct"])
    # This evidence check is conservative but cannot prove future profits.
    pass_check = (v_roi >= p_roi + 3. and
                  v_cap >= p_cap - 3. and
                  v_without >= p_without - 3. and
                  strike >= parent_strike - 4. and
                  validate["priced_bets"] >= 0.45 * parent24["priced_bets"])
    if pass_check:
        decision["status"] = "VALIDATED_CHALLENGER"
        decision["weights_pct"] = weights_pct(best["weights"])
        decision["selection_margin"] = best["gate"]
    else:
        decision["status"] = "2024_NO_ROBUST_IMPROVEMENT"
    return decision


def weights_pct(w):
    w = normalize(w)
    return {key: round(float(value * 100), 5) for key, value in zip(METRICS, w)}


def from_pct(profile):
    return normalize([float(profile["weights_pct"][m]) for m in METRICS])


def profile_key(r):
    return str(r["kind"]), str(r["course_key"])


def report(samples, past, prior, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    samples = {year: [r for r in rows if r["kind"] in
                       ("flat", "aw", "jumps") and
                       r["course_key"] in OLD.TRACKS] for year, rows in samples.items()}
    for year in (2023, 2024, 2025):
        if len(samples[year]) < 500:
            raise RuntimeError(f"Insufficient verified {year} sample: {len(samples[year])}")
    parent_weights = from_pct(prior)
    global_base_gate = float(prior["selection_margin"])
    grouped = {}
    for year in (2023, 2024, 2025):
        kind = C.defaultdict(list)
        course = C.defaultdict(list)
        trip = C.defaultdict(list)
        for r in samples[year]:
            kind[r["kind"]].append(r)
            course[profile_key(r)].append(r)
            trip[(r["kind"], r["course_key"], r["trip_key"])].append(r)
        grouped[year] = {"discipline": kind, "course": course, "trip": trip}

    plans = {}
    # Disciplines: each independently explores 160 weights x 6 gates.
    for kind in ("flat", "aw", "jumps"):
        p = choose(parent_weights, global_base_gate,
                   grouped[2023]["discipline"][kind],
                   grouped[2024]["discipline"][kind], "discipline", kind)
        plans[("discipline", kind)] = p
        print("Discipline", kind, p["status"], "candidates", p["candidates_tested"], flush=True)

    # Every course receives its own investigation, including no-evidence statuses.
    course_keys = sorted(set(grouped[2023]["course"]) | set(grouped[2024]["course"]) |
                         set(grouped[2025]["course"]))
    for kind, course in course_keys:
        parent = plans[("discipline", kind)]
        p = choose(from_pct(parent), float(parent["selection_margin"]),
                   grouped[2023]["course"][(kind, course)],
                   grouped[2024]["course"][(kind, course)],
                   "course", f"{kind}|{course}")
        p["discipline"] = kind
        p["course"] = course
        p["races_2025"] = len(grouped[2025]["course"][(kind, course)])
        plans[("course", kind, course)] = p

    # Course x trip: only consider a separate, strongly shrunk configuration if
    # there are enough results IN THAT exact course/discipline/trip combination.
    trip_keys = sorted(set(grouped[2023]["trip"]) | set(grouped[2024]["trip"]) |
                       set(grouped[2025]["trip"]))
    for kind, course, trip in trip_keys:
        parent = plans[("course", kind, course)]
        p = choose(from_pct(parent), float(parent["selection_margin"]),
                   grouped[2023]["trip"][(kind, course, trip)],
                   grouped[2024]["trip"][(kind, course, trip)],
                   "trip", f"{kind}|{course}|{trip}")
        p["discipline"] = kind
        p["course"] = course
        p["trip"] = trip
        p["races_2025"] = len(grouped[2025]["trip"][(kind, course, trip)])
        plans[("trip", kind, course, trip)] = p

    rows = []
    base_rows = []
    scope_counts = C.Counter()
    def selection_for(race):
        kind, course, trip = race["kind"], race["course_key"], race["trip_key"]
        for pkey in (("trip", kind, course, trip),
                     ("course", kind, course),
                     ("discipline", kind)):
            p = plans.get(pkey)
            if p and p["status"] == "VALIDATED_CHALLENGER":
                return pkey[0], from_pct(p), float(p["selection_margin"])
        return "global_fallback", parent_weights, global_base_gate

    # Run each 2025 race exactly once. 2025 is NEVER used for picking profiles.
    for r in samples[2025]:
        scope, w, gate = selection_for(r)
        a = forecast([r], w)[0]
        b = forecast([r], parent_weights)[0]
        scope_counts[scope] += 1
        for out_rows, result, threshold, level in (
            (rows, a, gate, scope),
            (base_rows, b, global_base_gate, "frozen_stage30_global")):
            race, ix, won, sp, gap = result
            out_rows.append({"date": race["date"], "race_id": race["race_id"],
                "course": race["course"], "discipline": race["kind"],
                "trip": race["trip_key"], "scope": level,
                "horse": race["horse"][ix], "won": won,
                "sp_decimal": round(sp, 4) if math.isfinite(sp) else "",
                "lead": round(gap, 6), "selection_margin": threshold,
                "selected": int(gap >= threshold)})

    def measure(group, selected=True):
        items = [x for x in group if not selected or x["selected"]]
        priced = [x for x in items if isinstance(x["sp_decimal"], (int, float)) and
                  1.01 <= x["sp_decimal"] <= 5000]
        bets = len(priced)
        wins = sum(int(x["won"]) for x in items)
        profit = sum(float(x["sp_decimal"]) * int(x["won"]) - 1 for x in priced)
        return {"races": len(items), "wins": wins,
                "strike_pct": round(100 * wins / len(items), 3) if items else None,
                "priced_bets": bets,
                "price_coverage_pct": round(100 * bets / len(items), 2) if items else None,
                "profit_units": round(profit, 3),
                "roi_pct": round(100 * profit / bets, 3) if bets else None}
    all_summary = {"hierarchical": measure(rows, False),
                   "hierarchical_selected": measure(rows),
                   "frozen_stage30": measure(base_rows, False),
                   "frozen_stage30_selected": measure(base_rows)}
    by_kind = {}
    for kind in ("flat", "aw", "jumps"):
        by_kind[kind] = {
            "hierarchical_all": measure([r for r in rows if r["discipline"] == kind], False),
            "hierarchical_selected": measure([r for r in rows if r["discipline"] == kind]),
            "stage30_all": measure([r for r in base_rows if r["discipline"] == kind], False),
            "stage30_selected": measure([r for r in base_rows if r["discipline"] == kind])}
    by_course = {}
    course_csv = []
    for kind, course in course_keys:
        p = plans[("course", kind, course)]
        current = [r for r in rows if (r["discipline"], OLD.track(r["course"])) == (kind, course)]
        baseline = [r for r in base_rows if (r["discipline"], OLD.track(r["course"])) == (kind, course)]
        by_course[f"{kind}|{course}"] = {
            "profile_status": p["status"], "races_2025": len(current),
            "hierarchical_selected": measure(current),
            "frozen_stage30_selected": measure(baseline)}
        course_csv.append({"discipline": kind, "course": course,
            "status": p["status"], "races_2023": p["races_2023"],
            "races_2024": p["races_2024"], "races_2025": p["races_2025"],
            "2024_roi_delta_pp": p["improvement_roi_pp_2024"],
            "candidates_tested": p["candidates_tested"],
            "selected_2025": measure(current)["races"],
            "selected_2025_roi_pct": measure(current)["roi_pct"],
            "global_2025_roi_pct": measure(baseline)["roi_pct"],
            "margin": p["selection_margin"], **p["weights_pct"]})

    trip_csv = []
    for kind, course, trip in trip_keys:
        p = plans[("trip", kind, course, trip)]
        trip_csv.append({"discipline": kind, "course": course, "trip": trip,
            "status": p["status"], "races_2023": p["races_2023"],
            "races_2024": p["races_2024"], "races_2025": p["races_2025"],
            "2024_roi_delta_pp": p["improvement_roi_pp_2024"],
            "candidates_tested": p["candidates_tested"],
            "margin": p["selection_margin"], **p["weights_pct"]})

    # Expose independent, course-specific factor associations. For each race,
    # compare the winning runner's *pre-race* signal to the mean of the other
    # starters. No results are used as a prediction input for that race.
    # These contrasts are descriptive; they do NOT establish causation or ROI.
    factor_rows = []
    for depth in ("course", "trip"):
        keys = (course_keys if depth == "course" else trip_keys)
        for key in keys:
            by_year = {y: grouped[y][depth][key] for y in (2023, 2024, 2025)}
            for j, metric in enumerate(METRICS):
                aggregate = {}
                for y, races in by_year.items():
                    values = []
                    labels = C.Counter()
                    identified = 0
                    for race in races:
                        mat = race["x"][:, :8]
                        if len(mat) < 2 or j >= mat.shape[1]:
                            continue
                        winner = int(race["winning_index"])
                        if not 0 <= winner < len(mat):
                            continue
                        v = float(mat[winner, j])
                        peers = (float(mat[:, j].sum()) - v) / (len(mat) - 1)
                        if not math.isfinite(v) or not math.isfinite(peers):
                            continue
                        values.append(v - peers)
                        label = race["sources"][winner][j]
                        if label not in ("population_prior", "not_applicable",
                                         "not_applicable_or_unavailable"):
                            identified += 1
                            labels[label] += 1
                    aggregate[y] = {"n": len(values),
                        "winner_advantage": round(float(np.mean(values)), 6)
                                             if values else None,
                        "prior_evidence_pct": round(100 * identified / len(values), 2)
                                              if values else None,
                        "leading_sources": ";".join(f"{lab}:{count}"
                            for lab, count in labels.most_common(3))}
                a = aggregate[2023]["winner_advantage"]
                b = aggregate[2024]["winner_advantage"]
                consistent = (aggregate[2023]["n"] >= 30 and
                              aggregate[2024]["n"] >= 30 and
                              a is not None and b is not None and
                              abs(a) >= 0.01 and abs(b) >= 0.01 and a * b > 0)
                factor_rows.append({"discipline": key[0], "course": key[1],
                    "trip": key[2] if depth == "trip" else "ALL_TRIPS",
                    "level": depth, "metric": metric,
                    "races_2023": aggregate[2023]["n"],
                    "winner_vs_field_signal_2023": a,
                    "races_2024": aggregate[2024]["n"],
                    "winner_vs_field_signal_2024": b,
                    "races_2025": aggregate[2025]["n"],
                    "winner_vs_field_signal_2025": aggregate[2025]["winner_advantage"],
                    "2023_winner_has_prior_evidence_pct": aggregate[2023]["prior_evidence_pct"],
                    "2024_winner_has_prior_evidence_pct": aggregate[2024]["prior_evidence_pct"],
                    "2024_leading_historical_sources": aggregate[2024]["leading_sources"],
                    "status": "DIRECTION_REPLICATED_2023_24" if consistent else
                              "WEAK_INCONSISTENT_OR_LOW_SAMPLE"})

    # Lifetime track draw sample, including dates before 2023, is an independent
    # diagnostic; never influences fitted weights / gates / 2024 promotion.
    draw_csv = []
    for (kind, course, trip, window), record in sorted(past.draw_stats.items()):
        row = {"discipline": kind, "course": course, "trip": trip, "period": window,
               "qualifying_races": record["races"],
               "runners": sum(record["runners"]),
               "source": "historical verified starters and drawn stalls"}
        for ix, bucket in enumerate(("low", "middle", "high")):
            starts, wins = record["runners"][ix], record["wins"][ix]
            exp = record["expected_wins"][ix]
            row.update({f"{bucket}_runs": starts, f"{bucket}_wins": wins,
                f"{bucket}_win_pct": round(100 * wins / starts, 3) if starts else None,
                f"{bucket}_expected_wins": round(exp, 2),
                f"{bucket}_over_expected": round(wins / exp, 3) if exp else None,
                f"{bucket}_sp_roi_pct": round(100 * record["net_sp_units"][ix] /
                                             record["priced"][ix], 3)
                                           if record["priced"][ix] else None})
        row["evidence_status"] = ("SAMPLE_SUFFICIENT_FOR_DESCRIPTIVE_BIAS"
                                  if record["races"] >= 100 else
                                  "LIMITED_OBSERVATIONS")
        draw_csv.append(row)

    def write_csv(filename, items):
        if not items:
            return
        with (out / filename).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(items[0]))
            writer.writeheader()
            writer.writerows(items)

    write_csv("stage34-course-weightings.csv", course_csv)
    write_csv("stage34-course-trip-weightings.csv", trip_csv)
    write_csv("stage34-draw-bias-by-trip.csv", draw_csv)
    write_csv("stage34-course-factor-trends.csv", factor_rows)
    write_csv("stage34-2025-all-race-forecasts.csv", rows)
    write_csv("stage34-2025-frozen-stage30-baseline.csv", base_rows)
    by_scope = {
        "disciplines": {str(k[-1]): v for k, v in plans.items() if k[0] == "discipline"},
        "courses": {f"{k[1]}|{k[2]}": v for k, v in plans.items() if k[0] == "course"},
        "course_trips": {f"{k[1]}|{k[2]}|{k[3]}": v for k, v in plans.items() if k[0] == "trip"}}
    (out / "stage34-frozen-track-profiles.json").write_text(
        json.dumps({"version": VERSION, "status": "RESEARCH_ONLY_NOT_LIVE",
                    "tested_years": [2023, 2024, 2025],
                    "model_d_metrics": METRICS, "selection_process":
                    "2023 candidate discovery; 2024 acceptance; 2025 audit",
                    "profiles": by_scope}, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    summary = {
        "version": VERSION, "status": "RESEARCH_ONLY",
        "live_model_updated": False, "betfair_required": False,
        "notes": [
            "2025 is not reused to select weights or gates, but has already been viewed in older analyses, so results are not a program-wide unseen holdout.",
            "All 2023-25 races use the eight historical Stage30 strictly pre-race category signals; older archive runs supply evidence.",
            "Predicted win probabilities are not generated. These are relative research scores, not calibrated probabilities.",
            "2024 validation is not a causal proof of edge; 2025 and forward-only real selections must confirm patterns.",
            "SP is used for settlement only. No final price, market odds or Betfair API contributes to pre-race selection.",
            "Unknown stalls, incomplete fields, unverified distances and non-starters are excluded from draw-bias statistics.",
            "Model D's course weights fall back to broader models when sample size or 2024 comparison is insufficient.",
            "Track layout alone does not prove positive return; draw effects vary by course and distance.",
            "Winner-versus-field factor signals are descriptive correlations; course-specific factor direction is called replicated only if both 2023 and 2024 have at least 30 races with the same meaningful sign."
        ],
        "source_audit": {"historical_rows_scanned": None,
                          "available_draw_start": past.oldest_draw_date,
                          "available_draw_end": past.newest_draw_date,
                          "historical_draw_groups": len(past.draw_stats),
                          "factor_trend_rows": len(factor_rows)},
        "races_by_year": {str(year): len(samples[year]) for year in (2023, 2024, 2025)},
        "weight_combinations_attempted": sum(p["candidates_tested"] for p in plans.values()),
        "gate_levels_tested": len(GATES),
        "course_profiles": len(course_keys),
        "course_trip_profiles": len(trip_keys),
        "validation_status_count": dict(C.Counter(p["status"] for p in plans.values())),
        "2025_applied_scope_counts": dict(scope_counts),
        "2025_results": all_summary, "2025_by_discipline": by_kind,
        "2025_by_course": by_course,
        "chester_5f_6f_note": "Chester's 5f/6f bias must be judged using actual draw-stall samples, and never pre-assumed."}
    (out / "stage34-summary.json").write_text(json.dumps(
        summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print("Study complete:", len(course_keys), "course profiles,",
          len(trip_keys), "course-distance profiles; trials",
          summary["weight_combinations_attempted"], flush=True)
    print("2025 all/selected:", json.dumps(all_summary), flush=True)
    return summary


def self_test():
    assert trip_of("flat", 5) == "5f_or_shorter"
    assert trip_of("flat", 6) == "6f"
    assert trip_of("flat", 7) == "7f"
    assert trip_of("aw", 6) == "6f"
    assert trip_of("jumps", 21) == "20_to_23f"
    assert len(pool(np.ones(8), "course", "chester", 24)) == 24
    p = normalize([1] * 8)
    a, t = effective_weights(np.array([8] + [0] * 7), p, 100, "course")
    assert 0.0 < t < 0.7 and abs(sum(a) - 1) < 1e-8
    assert abs(sum(from_pct({"weights_pct": weights_pct(p)})) - 1) < 1e-8
    # The draw test must not invent race winners or count non-runners.
    q = CoursePast()
    row = ["id", "2024-05-01", "Chester", "flat", "turf", 5,
           "good", "3", "horse", "horse", "", "", 1,
           "finished", "trainer", "jockey", 76, 4, "", "", 4.0, 1, ""]
    horses = [tuple(row[:8] + [f"horse{i}", f"horse{i}"] + row[10:12] +
                    [1 if i == 0 else i + 1] + row[13:21] +
                    [i + 1] + row[22:]) for i in range(9)]
    q._record_draw(horses)
    assert q.draw_stats[("flat", "chester", "5f_or_shorter",
                         "2024_validation")]["wins"][0] == 1
    print("Stage34 self-test passed: hierarchy, 5f/6f split, score weights, draw sample")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", help="Stage2 verified historical SQLite archive")
    parser.add_argument("--output", default="stage34_report")
    parser.add_argument("--stage30-report", default="reports/stage30/stage30-summary.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.db:
        parser.error("--db historical SQLite archive required")
    previous = json.loads(Path(args.stage30_report).read_text(encoding="utf-8"))
    if previous.get("status") != "RESEARCH_ONLY":
        raise SystemExit("Expected audited Stage30 prior")
    stage30_d = previous.get("model_results", {}).get("D", {})
    if stage30_d.get("chosen_combination") != "D-064-gap14":
        raise SystemExit("Stage30 historical D baseline changed: refusing comparison")
    past = CoursePast()
    samples, audit = past.scan(args.db)
    summary = report(samples, past, stage30_d, args.output)
    summary["source_audit"].update(audit)
    summary_path = Path(args.output) / "stage34-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n",
                            encoding="utf-8")


if __name__ == "__main__":
    main()
