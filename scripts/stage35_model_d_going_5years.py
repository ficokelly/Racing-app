#!/usr/bin/env python3
"""Stage 35: five-year (2021-25), course/trip and going/breeding Model D research.

Research-only, no Betfair, no live model modifications. Stage 30 category features
use strictly *previous-day* results from the entire available history. Stage 35
extends scored cohorts to 2021-25, learns candidates on 2021-23, independently
validates on 2024, and reports a frozen 2025 audit. Historic SP is ONLY used
for settling hypothetical win bets. Breeding inference relies on recorded
previous progeny outcomes; unknown parents / sparse samples stay neutral.

2025 was already viewed by earlier Stage 30/34 research: don't claim it is a
never-before-seen programme-wide test. Forward-only 2026 remains necessary.
"""
from __future__ import annotations
import argparse
import collections as C
import csv
import datetime as dt
import hashlib
import inspect
import json
import math
from pathlib import Path
import re
import textwrap
import numpy as np
import stage30_gap_aware_abcd as OLD
import stage34_model_d_course_research as PREV

VERSION = "stage35-d-five-year-ground-pedigree-v1"
YEARS = (2021, 2022, 2023, 2024, 2025)
DISCOVERY = (2021, 2022, 2023)
WEIGHTS = tuple(OLD.METRICS)
MODES = ("original_ground", "direct_or_pedigree", "ground_emphasis")
GATES = (0.04, 0.08, 0.14, 0.20, 0.28, 0.36)
assert WEIGHTS == ("Ability", "Trainer", "Jockey", "Course", "Draw", "Distance", "Ground", "Jumping")


def extend_stage30_years():
    """Extend ONLY Stage30 cohort guard/dictionary, not its past-form algorithm.

    The checked-in upstream functions are the source of truth. Exact assertions
    ensure changes stop rather than silently diverging if they are edited.
    Modified function code exists only in this research Python process.
    """
    alterations = (
        ("score", "if day.year not in (2023,2024,2025):return None",
         "if day.year not in (2021,2022,2023,2024,2025):return None"),
        ("scan", "samples={2023:[],2024:[],2025:[]};pending=[]",
         "samples={2021:[],2022:[],2023:[],2024:[],2025:[]};pending=[]"),
    )
    for method, old, new in alterations:
        original = getattr(OLD.Past, method)
        source = textwrap.dedent(inspect.getsource(original))
        if source.count(old) != 1:
            raise RuntimeError(f"Stage30 {method} date anchor changed: refusing unsafe five-year replay")
        compiled = compile(source.replace(old, new), f"stage35_patched_{method}", "exec")
        namespace = dict(vars(OLD))
        exec(compiled, namespace)  # Local, checked-in Stage30 function, exact-anchor edit only.
        setattr(OLD.Past, method, namespace[method])


def norm(s):
    return OLD.normal(s)


def parent_name(s):
    """Blank and placeholder pedigree records must never become one fake sire/dam."""
    name = norm(s)
    if name in ('', 'unknown', 'none', 'null', 'not known', 'unrecorded',
                'not recorded', 'na', 'n a', 'no sire', 'no dam', 'tbc'):
        return ''
    return name if len(name) >= 2 else ''


def exact_trip(discipline, furlongs):
    """Nominal integer furlongs per course; sparse trips fall back to course.

    Unlike Stage34's pooled 11-12f/13-14f buckets, 11f and 12f now have
    independent profiles, as do 16f, 17f, 18f, and so on over jumps.
    """
    f = OLD.number(furlongs)
    if f is None or not math.isfinite(f) or f < 4 or f > 40:
        return 'distance_unverified'
    return f'{int(math.floor(f + 0.5))}f'


def going_bucket(value, discipline):
    """Do not fold good-to-firm into firm, or good-to-soft into soft.

    Exact official categorical going only. AW has different buckets from turf.
    'In places' text is not itself additional ground evidence.
    """
    raw = str(value or "").lower().split("(")[0].strip()
    raw = re.sub(r"\s+", " ", re.sub(r"[^a-z\s/-]", " ", raw))
    if discipline == "aw":
        if "standard to slow" in raw or "standard/slow" in raw:
            return "standard_to_slow"
        if "standard to fast" in raw or "standard/fast" in raw:
            return "standard_to_fast"
        if "standard" in raw:
            return "standard"
        if "slow" in raw:
            return "slow"
        if "fast" in raw:
            return "fast"
        return None
    if "soft to heavy" in raw or "heavy to soft" in raw:
        return "soft_to_heavy"
    if "heavy" in raw:
        return "heavy"
    if "yielding to soft" in raw or "soft to yielding" in raw:
        return "yielding_to_soft"
    if "good to soft" in raw or "soft to good" in raw:
        return "good_to_soft"
    if "good to yielding" in raw or "yielding to good" in raw:
        return "good_to_yielding"
    if "good to firm" in raw or "firm to good" in raw:
        return "good_to_firm"
    if re.search(r"\byielding\b", raw):
        return "yielding"
    if re.search(r"\bsoft\b", raw):
        return "soft"
    if re.search(r"\bfirm\b", raw):
        return "firm"
    if re.search(r"\bgood\b", raw):
        return "good"
    return None


def stats_lift(condition, overall, prior):
    """Ratio of realized/field-size-expected wins by going vs all going.

    Beta-like shrinkage avoids claiming certainty from one winner. The overall
    family rate removes much of general sire/horse ability from suitability.
    """
    n, wins, placed, expected = condition
    n0, w0, p0, e0 = overall
    if n <= 0 or n0 <= 0 or expected <= 0 or e0 <= 0:
        return 0.
    conditional = (wins + prior) / (expected + prior)
    baseline = (w0 + prior) / (e0 + prior)
    win_lift = math.log(max(.01, conditional / baseline))
    # Placed form contributes modestly when a horse has not yet won on the
    # going. It is measured relative to its overall placement rate.
    placed_ground = (placed + .25 * prior) / (n + prior)
    placed_general = (p0 + .25 * prior) / (n0 + prior)
    place_lift = math.log(max(.01, placed_ground / max(.01, placed_general)))
    return max(-1.20, min(1.20, .75*win_lift + .25*place_lift))


def stable_seed(*parts):
    s = "|".join(map(str, parts)).encode()
    return int.from_bytes(hashlib.sha256(s).digest()[:8], "big")


class GroundPast(PREV.CoursePast):
    """Long-history, before-the-day ground and parent-progeny observations.

    Additional track & ground keys never include final-year data during scoring
    of an earlier target. A scorer sees only days added by update().
    """
    def __init__(self):
        super().__init__()
        self.ground_counts = C.defaultdict(lambda: [0, 0, 0, 0.])
        self.progeny_children = C.defaultdict(set)
        self.suitability_counts = C.Counter()

    def _key(self, role, discipline, identity, going):
        return role, discipline, identity, going

    def _get(self, role, discipline, identity, going):
        if not identity or not going:
            return (0, 0, 0, 0.)
        return self.ground_counts.get(self._key(role, discipline, identity, going), (0, 0, 0, 0.))

    def _child_count(self, role, discipline, identity, going):
        return len(self.progeny_children.get(self._key(role, discipline, identity, going), ()))

    def _record_ground(self, rows):
        race = rows[0]
        discipline = OLD.style(race)
        ground = going_bucket(race[6], discipline)
        if ground is None:
            return
        group = [r for r in rows if OLD.started(r)]
        if len(group) < 2:
            return
        expected = 1. / len(group)
        for r in group:
            horse = self.identity(r)
            sire, dam = parent_name(r[18]), parent_name(r[19])
            won, placed = r[12] == 1, r[12] is not None and 1 <= r[12] <= 3
            for role, ident in (("horse", horse), ("sire", sire), ("dam", dam)):
                if not ident:
                    continue
                for g in (ground, "ALL"):
                    row = self.ground_counts[self._key(role, discipline, ident, g)]
                    row[0] += 1
                    row[1] += int(won)
                    row[2] += int(placed)
                    row[3] += expected
                if role != "horse":
                    self.progeny_children[self._key(role, discipline, ident, ground)].add(horse)

    def update(self, rows):
        # This is called only after the racing calendar day changes, following
        # the original Stage 30 no-same-day-peeking scanner.
        super().update(rows)
        self._record_ground(rows)

    def _ground_evidence(self, r, discipline, ground):
        horse, sire, dam = self.identity(r), parent_name(r[18]), parent_name(r[19])
        exact = self._get("horse", discipline, horse, ground)
        total = self._get("horse", discipline, horse, "ALL")
        se = self._get("sire", discipline, sire, ground)
        sa = self._get("sire", discipline, sire, "ALL")
        de = self._get("dam", discipline, dam, ground)
        da = self._get("dam", discipline, dam, "ALL")
        sire_children = self._child_count("sire", discipline, sire, ground)
        dam_children = self._child_count("dam", discipline, dam, ground)
        # Direct evidence: three previous runs merits a label; however not a
        # declaration that a horse likes it. That requires observed results.
        direct_n = exact[0]
        direct = stats_lift(exact, total, prior=1.5)
        sire_valid = se[0] >= 18 and sire_children >= 4
        dam_valid = de[0] >= 10 and dam_children >= 3
        sire_effect = stats_lift(se, sa, prior=6.) if sire_valid else 0.
        dam_effect = stats_lift(de, da, prior=4.) if dam_valid else 0.
        sire_weight = (min(.65, se[0] / (se[0] + 70.)) if sire_valid else 0.)
        dam_weight = (min(.50, de[0] / (de[0] + 45.)) if dam_valid else 0.)
        breed = ((sire_weight * sire_effect + dam_weight * dam_effect) /
                 (sire_weight + dam_weight) if sire_weight + dam_weight else 0.)
        # Use a weak pedigree prior for 1-2 direct runs, stronger direct data
        # for 3+ runs; no evidence must not become a positive signal.
        direct_share = (0. if direct_n == 0 else
                        min(.88, (direct_n / (direct_n + 4.))))
        refined = direct_share * direct + (1-direct_share) * breed
        if direct_n >= 3:
            source = "DIRECT_3PLUS_RUNS"
        elif direct_n in (1,2):
            source = "DIRECT_LIMITED_BLEND_PEDIGREE" if sire_valid or dam_valid else "DIRECT_LIMITED"
        elif sire_valid and dam_valid:
            source = "UNRACED_ON_GOING_SIRE_DAM_INFERENCE"
        elif sire_valid:
            source = "UNRACED_ON_GOING_SIRE_INFERENCE"
        elif dam_valid:
            source = "UNRACED_ON_GOING_DAM_INFERENCE"
        else:
            source = "NO_SUPPORTED_GOING_EVIDENCE"
        return refined, {
            "going": ground, "source": source,
            "horse_runs_on_going": exact[0], "horse_wins_on_going": exact[1],
            "horse_places_on_going": exact[2],
            "sire_progeny_runs_on_going": se[0], "sire_unique_progeny_on_going": sire_children,
            "dam_progeny_runs_on_going": de[0], "dam_unique_progeny_on_going": dam_children,
            "sire_eligible": bool(sire_valid), "dam_eligible": bool(dam_valid),
            "direct_lift": round(direct, 5), "pedigree_lift": round(breed, 5),
            "inferred_lift": round(refined, 5),
            "has_credible_positive_ground_evidence": bool(refined > .08 and (direct_n >= 3 or sire_valid or dam_valid))
        }

    def score(self, rows):
        race = super().score(rows)
        if race is None:
            return None
        discipline = race["kind"]
        race["trip_key"] = exact_trip(discipline, rows[0][5])
        ground = going_bucket(rows[0][6], discipline)
        starters = [r for r in rows if OLD.started(r)]
        evidence = []
        suitability = []
        for runner in starters:
            if ground:
                g, e = self._ground_evidence(runner, discipline, ground)
            else:
                g, e = 0., {"going": None, "source": "GOING_UNVERIFIED",
                             "horse_runs_on_going": 0, "horse_wins_on_going": 0,
                             "horse_places_on_going": 0,
                             "sire_progeny_runs_on_going": 0,
                             "sire_unique_progeny_on_going": 0,
                             "dam_progeny_runs_on_going": 0,
                             "dam_unique_progeny_on_going": 0,
                             "inferred_lift": 0., "has_credible_positive_ground_evidence": False}
            evidence.append(e)
            suitability.append(g)
            self.suitability_counts[e["source"]] += 1
        original = np.array(race["x"], copy=True)
        suitability = np.asarray(suitability, dtype=np.float32)
        modes = {}
        for mode, old_part, new_part in (("direct_or_pedigree", .30, .70),
                                         ("ground_emphasis", .15, 1.30)):
            mat = original.copy()
            mat[:,6] = np.clip(old_part*original[:,6] + new_part*suitability, -1.8, 1.8)
            modes[mode] = mat
        race["ground_mode_matrices"] = modes
        race["ground_evidence"] = evidence
        race["ground_label"] = ground
        race["year"] = int(str(race["date"])[:4])
        return race


def weights_from_pct(item):
    return PREV.normalize([float(item["weights_pct"][k]) for k in WEIGHTS])


def performance(predictions, gate):
    return OLD.evaluate(predictions, gate)


def forecast(races, weights, mode):
    weights = PREV.normalize(weights)
    rows = []
    for race in races:
        active = weights.copy()
        if race["kind"] == "jumps":
            active[4] = 0.
        else:
            active[7] = 0.
        active /= max(1e-9, float(active.sum()))
        vectors = race["x"] if mode == "original_ground" else race["ground_mode_matrices"][mode]
        scores = vectors[:,:8] @ active
        idx = int(np.argmax(scores))
        second = float(np.partition(scores, -2)[-2])
        gap = float(scores[idx] - second)
        sp = float(race["sp"][idx])
        rows.append((race, idx, int(idx == race["winning_index"]), sp, gap))
    return rows


def safe_roi(metric):
    return float(metric["roi_pct"]) if metric.get("roi_pct") is not None else -500.


def objective(metric):
    if metric.get("priced_bets", 0) <= 0:
        return -1.e8
    roi = float(metric["roi_pct"])
    cap = float(metric["roi_capped_20to1_pct"])
    no_max = float(metric["roi_without_largest_winner_pct"])
    return .48*roi + .32*cap + .20*no_max - 20. / math.sqrt(metric["priced_bets"])


def min_data(scope):
    # Training = three full years; validation = one full year.
    return {"discipline": (1800, 210, 500, 65, 128),
            "course": (160, 40, 40, 16, 96),
            "trip": (95, 27, 18, 12, 64)}[scope]


def unique_candidates(parent, scope, key, count):
    return PREV.pool(parent, "stage35_"+scope, key, count)


def choose(parent, training, validation, scope, key):
    """Freeze one candidate on 2021-23; accept/reject it using only 2024."""
    min_train_races, min_train_selections, min_valid_races, min_valid_selections, nweights = min_data(scope)
    out = {"scope":scope,"key":key,"races_2021_23":len(training),
           "races_2024":len(validation),"status":"PARENT_FALLBACK",
           "trials":0,"selected_weight_pct":PREV.weights_pct(parent[0]),
           "selected_ground_method":parent[1],"selected_margin":parent[2]}
    if len(training) < min_train_races or len(validation) < min_valid_races:
        out["status"]="INSUFFICIENT_RACE_COUNTS"
        return out
    # Parent 2024 is a frozen comparator; do not reselect baseline using 2024.
    incumbent = performance(forecast(validation, parent[0], parent[1]), parent[2])
    out["parent_2024"] = incumbent
    candidates = unique_candidates(parent[0], scope, key, nweights)
    best = None
    for raw in candidates:
        blended, share = PREV.effective_weights(raw, parent[0], len(training), scope)
        for method in MODES:
            if scope == "trip" and method == "original_ground" and np.allclose(blended,parent[0]):
                pass
            predicted = forecast(training, blended, method)
            for gate in GATES:
                m = performance(predicted, gate)
                out["trials"] += 1
                if m["priced_bets"] < min_train_selections or m["price_coverage_pct"] is None or m["price_coverage_pct"] < 85:
                    continue
                # Penalise a strategy that earns nearly all profits in 1 year.
                yearly = [performance([p for p in predicted if p[0]["year"] == y], gate)
                          for y in DISCOVERY]
                yearly_roi = [x["roi_capped_20to1_pct"] for x in yearly if x["priced_bets"] >= 6]
                penalty = 0. if len(yearly_roi) < 2 else max(0., -sum(v > -30 for v in yearly_roi) + 2) * 1.5
                val = objective(m) - penalty + math.log1p(m["priced_bets"])
                if best is None or val > best["value"]:
                    best={"value":val,"weights":blended,"method":method,
                          "margin":gate,"shrinkage":share,"train":m,
                          "yearly_roi_2021_23":{str(y):yearly[i]["roi_pct"] for i,y in enumerate(DISCOVERY)}}
    if best is None:
        out["status"]="NO_PRICED_TRAINING_CANDIDATE"
        return out
    val = performance(forecast(validation,best["weights"],best["method"]),best["margin"])
    out.update({"candidate_weight_pct":PREV.weights_pct(best["weights"]),
                "candidate_ground_method":best["method"],
                "candidate_margin":best["margin"],"training":best["train"],
                "training_year_roi":best["yearly_roi_2021_23"],
                "validation":val,"shrinkage":best["shrinkage"]})
    if val["priced_bets"] < min_valid_selections or (val["price_coverage_pct"] or 0.) < 85:
        out["status"] = "2024_VALIDATION_TOO_SPARSE"
        return out
    if incumbent["priced_bets"] < max(8, min_valid_selections // 2):
        out["status"] = "2024_BASELINE_TOO_SPARSE"
        return out
    if val["roi_pct"] is None or incumbent["roi_pct"] is None:
        out["status"] = "2024_NO_SETTLEMENT_EVIDENCE"
        return out
    # All comparisons done before the 2025 final audit. Conservative
    # guard against a single huge SP price driving outperformance.
    accept = (val["roi_pct"] >= incumbent["roi_pct"] + 4.0
              and val["roi_capped_20to1_pct"] >= incumbent["roi_capped_20to1_pct"] - 2.0
              and val["roi_without_largest_winner_pct"] >= incumbent["roi_without_largest_winner_pct"] - 2.0
              and (val["strike_pct"] or 0.) >= (incumbent["strike_pct"] or 0.) - 4.0
              and val["priced_bets"] >= .45 * incumbent["priced_bets"])
    out["validation_delta_roi_pp"] = round(val["roi_pct"]-incumbent["roi_pct"],3)
    if accept:
        out["status"]="2024_PASSED_RESEARCH_CHECKS"
        out["selected_weight_pct"]=PREV.weights_pct(best["weights"])
        out["selected_ground_method"]=best["method"]
        out["selected_margin"]=best["margin"]
    else:
        out["status"]="2024_DID_NOT_REPLICATE"
    return out


def profile_as_tuple(profile):
    return (PREV.normalize([profile["selected_weight_pct"][m] for m in WEIGHTS]),
            profile["selected_ground_method"], float(profile["selected_margin"]))


def summary_of(predictions, only_selected):
    selected = [p for p in predictions if not only_selected or p["selected"]]
    priced = [p for p in selected if p["sp"] is not None and 1.01 <= p["sp"] <= 5000]
    profit = sum((p["sp"]*p["won"]-1.) for p in priced)
    wins = sum(p["won"] for p in selected)
    return {"races":len(selected),"wins":wins,"strike_pct":round(100*wins/len(selected),3) if selected else None,
            "priced_bets":len(priced),"profit_units":round(profit,3),
            "sp_roi_pct":round(100*profit/len(priced),3) if priced else None}


def write_csv(dest, rows):
    if not rows:
        return
    with dest.open("w",newline="",encoding="utf-8") as f:
        writer = csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_report(samples, past, prior, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for year in YEARS:
        if len(samples.get(year,[]))<500:
            raise RuntimeError(f"Missing full 2021-2025 sample for {year}: {len(samples.get(year,[]))}")
    old=weights_from_pct(prior)
    original=(old,"original_ground",float(prior["selection_margin"]))
    disciplines=("flat","aw","jumps")
    train=[]
    for year in DISCOVERY:train.extend(samples[year])
    valid=samples[2024]
    test=samples[2025]
    partitions={}
    for part,races in (("train",train),("valid",valid),("test",test)):
        gp={"discipline":C.defaultdict(list),"course":C.defaultdict(list),"trip":C.defaultdict(list)}
        for race in races:
            k,c,t=race["kind"],race["course_key"],race["trip_key"]
            gp["discipline"][k].append(race)
            gp["course"][(k,c)].append(race)
            gp["trip"][(k,c,t)].append(race)
        partitions[part]=gp
    plans={}
    for disc in disciplines:
        plans[("discipline",disc)]=choose(original,partitions["train"]["discipline"][disc],
               partitions["valid"]["discipline"][disc],"discipline",disc)
        print("Discipline",disc,plans[("discipline",disc)]["status"],flush=True)
    courses=sorted(set(partitions["train"]["course"]) | set(partitions["valid"]["course"]) | set(partitions["test"]["course"]))
    for disc, course in courses:
        upstream=profile_as_tuple(plans[("discipline",disc)])
        plans[("course",disc,course)]=choose(upstream,partitions["train"]["course"][(disc,course)],
               partitions["valid"]["course"][(disc,course)],"course",f"{disc}|{course}")
    trips=sorted(set(partitions["train"]["trip"]) | set(partitions["valid"]["trip"]) | set(partitions["test"]["trip"]))
    for disc,course,trip in trips:
        upstream=profile_as_tuple(plans[("course",disc,course)])
        plans[("trip",disc,course,trip)]=choose(upstream,
            partitions["train"]["trip"][(disc,course,trip)],
            partitions["valid"]["trip"][(disc,course,trip)],
            "trip",f"{disc}|{course}|{trip}")

    def find_profile(race):
        d,c,t=race["kind"],race["course_key"],race["trip_key"]
        for key in (("trip",d,c,t),("course",d,c),("discipline",d)):
            p=plans[key]
            if p["status"]=="2024_PASSED_RESEARCH_CHECKS":
                return key[0], profile_as_tuple(p)
        return "original_fallback",original

    actual=[];baseline=[];evidence_rows=[]
    coverage=C.Counter()
    for race in test:
        scope, params=find_profile(race)
        coverage[scope]+=1
        for name, tupleparams, dest in ((scope,params,actual),
                                        ("original_stage30",original,baseline)):
            w,method,margin=tupleparams
            one=forecast([race],w,method)[0]
            raw_race,idx,won,price,gap=one
            dest.append({"date":race["date"],"race_id":race["race_id"],
                         "discipline":race["kind"],"course":race["course_key"],
                         "trip":race["trip_key"],"going":race["ground_label"],
                         "horse":race["horse"][idx],
                         "scope":name,"mode":method,"won":won,
                         "sp":price if math.isfinite(price) else None,
                         "score_lead":round(gap,6),"margin":margin,
                         "selected":bool(gap>=margin),
                         "ground_source":race["ground_evidence"][idx]["source"]})
        # Evidence is recorded for the final model's picked horse, not just winners.
        forecast_row=actual[-1]
        if forecast_row["selected"]:
            ev=race["ground_evidence"][forecast([race],params[0],params[1])[0][1]]
            evidence_rows.append({"date":race["date"],"course":race["course_key"],
                    "trip":race["trip_key"],"horse":forecast_row["horse"],
                    "win":forecast_row["won"],"sp":forecast_row["sp"],
                    **ev})
    per_discipline={}
    for kind in disciplines:
        mine=[r for r in actual if r["discipline"]==kind]
        base=[r for r in baseline if r["discipline"]==kind]
        per_discipline[kind]={"new_all":summary_of(mine,False),"new_selected":summary_of(mine,True),
                         "original_all":summary_of(base,False),"original_selected":summary_of(base,True)}
    group_evidence={}
    for category in sorted({r["ground_source"] for r in actual}):
        rr=[r for r in actual if r["ground_source"]==category]
        # For the baseline comparison, use the identical set of race IDs,
        # so improvements aren't fabricated by changing the denominator.
        ids={r["race_id"] for r in rr}
        old_matching=[r for r in baseline if r["race_id"] in ids]
        group_evidence[category]={"new_all":summary_of(rr,False),
                                  "new_selected":summary_of(rr,True),
                                  "old_all_same_races":summary_of(old_matching,False),
                                  "old_selected_same_races":summary_of(old_matching,True)}
    by_going={}
    ground_csv=[]
    ground_keys=sorted({(r["discipline"],r["course"],r["going"]) for r in actual},
                       key=lambda z:tuple(str(v or '') for v in z))
    for disc,course,going in ground_keys:
        group=[r for r in actual if (r["discipline"],r["course"],r["going"])==(disc,course,going)]
        orig=[r for r in baseline if (r["discipline"],r["course"],r["going"])==(disc,course,going)]
        ns,os=summary_of(group,True),summary_of(orig,True)
        ground_csv.append({"discipline":disc,"course":course,"going":going or "UNKNOWN",
                           "2025_races":len(group),"2025_new_selections":ns["races"],
                           "2025_new_strike_pct":ns["strike_pct"],
                           "2025_new_sp_roi_pct":ns["sp_roi_pct"],
                           "2025_original_selections":os["races"],
                           "2025_original_sp_roi_pct":os["sp_roi_pct"]})
    for disc,going in sorted({(r["discipline"],r["going"]) for r in actual},
                             key=lambda z:tuple(str(v or '') for v in z)):
        a=[r for r in actual if (r["discipline"],r["going"])==(disc,going)]
        b=[r for r in baseline if (r["discipline"],r["going"])==(disc,going)]
        by_going[f"{disc}|{going}"]={"new_selected":summary_of(a,True),
                                     "original_selected":summary_of(b,True)}
    by_course={}
    for disc,course in courses:
        a=[r for r in actual if (r["discipline"],r["course"])==(disc,course)]
        b=[r for r in baseline if (r["discipline"],r["course"])==(disc,course)]
        by_course[f"{disc}|{course}"]={"new_selected":summary_of(a,True),
                                       "original_selected":summary_of(b,True)}
    results={"version":VERSION,"status":"RESEARCH_ONLY_NOT_LIVE","betfair_required":False,
             "training_years":list(DISCOVERY),"validation_year":2024,"audit_year":2025,
             "history_form_from":"earliest_available_pre_race",
             "races_per_year":{str(y):len(samples[y]) for y in YEARS},
             "courses":len(courses),"course_distance_profiles":len(trips),
             "weight_threshold_ground_combinations_attempted":sum(x["trials"] for x in plans.values()),
             "ground_methods_tested":list(MODES),
             "profile_status_counts":dict(C.Counter(p["status"] for p in plans.values())),
             "profiles_used_2025":dict(coverage),
             "2025_results":{"new_all":summary_of(actual,False),
                             "new_selected":summary_of(actual,True),
                             "original_all":summary_of(baseline,False),
                             "original_selected":summary_of(baseline,True)},
             "2025_by_discipline":per_discipline,
             "2025_by_ground_source":group_evidence,
             "2025_by_going":by_going,
             "2025_by_course":by_course,
             "going_evidence_counts_during_scoring":dict(past.suitability_counts),
             "warnings":[
                 "2025 was seen in older Stage30/34 research and is a frozen new-configuration audit, not a never-viewed holdout.",
                 "Official going labels are imperfect proxies for real race surface conditions; no GoingStick or sectional data inferred.",
                 "Direct horse going evidence is separate from tentative sire/dam progeny inference; absence of evidence is neutral.",
                 "Sire/dam suitability is normalized relative to their all-going progeny form and shrunk; breeding cannot guarantee suitability.",
                 "For results, SP settles one-unit hypothetical win bets; no market/SP feature affects rankings.",
                 "The longest available pre-race historical archive (not just 2021-25) is used for past runner features.",
                 "At least two 2024 validation checks and sparse-sample fallback prevent automatic model promotion.",
                 "No live Model A, B, C or D forecasts or app data are modified."
             ]}
    (output/"stage35-summary.json").write_text(json.dumps(results,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    weights_rows=[]
    for key,p in plans.items():
        row={"scope":key[0],"profile":"|".join(str(x) for x in key[1:]),
             "status":p["status"],"training_races_2021_23":p["races_2021_23"],
             "validation_races_2024":p["races_2024"],"combinations":p["trials"],
             "selected_ground_method":p["selected_ground_method"],
             "selected_margin":p["selected_margin"],
             "2024_roi_improvement_pp":p.get("validation_delta_roi_pp"),
             **p["selected_weight_pct"]}
        weights_rows.append(row)
    write_csv(output/"stage35-five-year-course-distance-weights.csv",weights_rows)
    write_csv(output/"stage35-2025-forecasts.csv",actual)
    write_csv(output/"stage35-2025-original-d.csv",baseline)
    write_csv(output/"stage35-2025-going-evidence.csv",evidence_rows)
    write_csv(output/"stage35-course-going-performance.csv",ground_csv)
    (output/"stage35-frozen-profiles.json").write_text(json.dumps({
        "version":VERSION,"status":"NOT_LIVE","profiles":{
            "|".join(str(x) for x in k):v for k,v in plans.items()}},indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("Test 2025:",json.dumps(results["2025_results"]),flush=True)
    print("Profiles",len(courses),"courses",len(trips),"course-trips",results["weight_threshold_ground_combinations_attempted"],"trials",flush=True)
    return results


def self_test():
    # Verify source compatibility before downloading the large SQLite archive.
    extend_stage30_years()
    assert going_bucket("Good to Soft (Soft in places)","flat")=="good_to_soft"
    assert going_bucket("Good to Firm","flat")=="good_to_firm"
    assert going_bucket("Good to Yielding","flat")=="good_to_yielding"
    assert going_bucket("Soft","jumps")=="soft"
    assert going_bucket("Standard to Slow","aw")=="standard_to_slow"
    assert going_bucket("Standard","aw")=="standard"
    assert going_bucket("unrecorded","flat") is None
    a=(3,1,2,.3);b=(10,2,4,1.)
    assert math.isfinite(stats_lift(a,b,1.5))
    p=GroundPast()
    assert p._ground_evidence(("id","2023-01-01","Bath","flat","turf",5,"soft","",
                               "h","h","GB","",None,"","t","j",70,4,"s","d",None,1,""),"flat","soft")[1]["source"]=="NO_SUPPORTED_GOING_EVIDENCE"
    assert exact_trip("flat",5)=="5f"
    assert exact_trip("flat",11)=="11f"
    assert exact_trip("flat",12)=="12f"
    assert exact_trip("jumps",18)=="18f"
    assert parent_name('Unknown') == ''
    assert parent_name('None') == '' 
    print("Stage35 self-test passed: ground categories, neutral missing breeding, 5f course trip")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",help="Stage2 verified historical racing SQLite archive")
    parser.add_argument("--output",default="stage35_report")
    parser.add_argument("--stage30-report",default="reports/stage30/stage30-summary.json")
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test();return
    if not args.db:
        parser.error("--db required")
    old=json.loads(Path(args.stage30_report).read_text(encoding="utf-8"))
    if old.get("status")!="RESEARCH_ONLY" or old.get("model_results",{}).get("D",{}).get("chosen_combination")!="D-064-gap14":
        raise RuntimeError("Expected original Stage30 D frozen comparator")
    extend_stage30_years()
    past=GroundPast()
    samples,audit=past.scan(args.db)
    summary=run_report(samples,past,old["model_results"]["D"],args.output)
    summary["source_audit"]=audit
    (Path(args.output)/"stage35-summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n",encoding="utf-8")

if __name__=="__main__":
    main()
