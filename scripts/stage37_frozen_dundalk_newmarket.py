#!/usr/bin/env python3
"""Stage 37 — replay *unchanged* Stage34 Model D course challengers.

  Dundalk / AW   : Stage34 course profile, exact eight-factor weights and gate
  Newmarket / Flat: Stage34 course profile, exact eight-factor weights and gate

One top pick per eligible historical race, plus the frozen-gate selection subset.
Original Stage30 global Model D is scored on the SAME races. No post-race odds
enter predictions; SP is used only to settle hypothetical 1-unit win bets.

2018-2022: RETROSPECTIVE stress test (weights chosen using 2023, so NOT unseen).
2023: profile discovery; 2024: profile validation; 2025: previously viewed audit.
2026: assess only if the independently supplied verified SQLite contains results.
If not, write a no-data status and preserve frozen weights for a future audit.

NO live app, bet placement, Betfair, model weights or racecard data are changed.
"""
from __future__ import annotations

import argparse
import collections as C
import csv
import inspect
import json
import math
from pathlib import Path
import textwrap

import numpy as np
import stage30_gap_aware_abcd as BASE
import stage34_model_d_course_research as ST34
import stage35_model_d_going_5years as ST35

VERSION = "stage37-frozen-stage34-dundalk-newmarket-v1"
TARGETS = {"aw|dundalk": ("aw", "dundalk"),
           "flat|newmarket": ("flat", "newmarket")}
YEARS = tuple(range(2018, 2027))
STAGE34_YEARS = (2023, 2024, 2025)
WEIGHTS = tuple(BASE.METRICS)


def enable_years_for_audit():
    """Extend pre-existing Stage30 date checks, without changing its scoring.

    The only allowed modifications are an explicit cohort inclusion list, an
    expanded date-index dictionary, and SQL's final cutoff. Fail if upstream
    method changes. Stage30 scanner still updates form *after* calendar day.
    """
    changes = (
        ("score", (
            ("if day.year not in (2023,2024,2025):return None",
             "if day.year not in (2018,2019,2020,2021,2022,2023,2024,2025,2026):return None"),
        )),
        ("scan", (
            ("samples={2023:[],2024:[],2025:[]};pending=[]",
             "samples={2018:[],2019:[],2020:[],2021:[],2022:[],2023:[],2024:[],2025:[],2026:[]};pending=[]"),
            ("WHERE r.race_date < '2026-01-01' ORDER BY r.race_date,r.id",
             "WHERE r.race_date < '2027-01-01' ORDER BY r.race_date,r.id"),
        )),
    )
    for method, alterations in changes:
        original = getattr(BASE.Past, method)
        source = textwrap.dedent(inspect.getsource(original))
        for old, new in alterations:
            if source.count(old) != 1:
                raise RuntimeError(f"Stage30 {method} contract changed near {old!r}; refusing unsafe replay")
            source = source.replace(old, new)
        scope = dict(vars(BASE))
        exec(compile(source, f"stage37_checked_replay_{method}", "exec"), scope)
        setattr(BASE.Past, method, scope[method])


def cohort(year):
    if year <= 2022:
        return "RETROSPECTIVE_PRE_CONFIGURATION_NOT_INDEPENDENT"
    if year == 2023:
        return "STAGE34_DISCOVERY"
    if year == 2024:
        return "STAGE34_ACCEPTANCE_VALIDATION"
    if year == 2025:
        return "STAGE34_ALREADY_VIEWED_AUDIT"
    return "FORWARD_2026_IF_RESULTS_VERIFIED_IN_SQLITE"


def load_configs(stage34_path, stage30_path):
    report34 = json.loads(Path(stage34_path).read_text(encoding="utf-8"))
    report30 = json.loads(Path(stage30_path).read_text(encoding="utf-8"))
    if report30.get("status") != "RESEARCH_ONLY":
        raise ValueError("The original Stage30 report was not the expected research-only report")
    d = report30["model_results"]["D"]
    if d.get("chosen_combination") != "D-064-gap14":
        raise ValueError("The original frozen Stage30 Model D baseline has changed")
    original = np.asarray([float(d["weights_pct"][k]) for k in WEIGHTS], dtype=float)
    original = ST34.normalize(original)
    original_gate = float(d["selection_margin"])
    if abs(original_gate - .14) > 1.e-10:
        raise ValueError("Original Model D selection margin was not 0.14")
    profiles = report34.get("profiles", {}).get("courses", {})
    frozen = {}
    for key in TARGETS:
        model = profiles.get(key)
        if not model or model.get("status") != "VALIDATED_CHALLENGER":
            raise ValueError(f"Missing validated Stage34 course challenger: {key}")
        vals = [float(model["weights_pct"][x]) for x in WEIGHTS]
        if abs(sum(vals)-100) > 0.015:
            raise ValueError("Invalid frozen Stage34 weights in " + key)
        weights = ST34.normalize(vals)
        gate = float(model["selection_margin"])
        if not .0 < gate < 1:
            raise ValueError("Invalid frozen Stage34 selection margin for " + key)
        frozen[key] = {"weights": weights, "gate": gate,
                       "original_profile": model}
    return frozen, {"weights": original, "gate": original_gate}, report34, d


def summarize(rows, selected=False):
    part = [r for r in rows if not selected or r["selected"]]
    priced = [r for r in part if r["sp_decimal"] is not None]
    n = len(part)
    b = len(priced)
    won = sum(r["won"] for r in part)
    profit = sum(r["won"] * r["sp_decimal"] - 1 for r in priced)
    capped = sum(r["won"] * min(r["sp_decimal"],21.) - 1 for r in priced)
    win_profit = sorted((r["sp_decimal"]-1 for r in priced if r["won"]), reverse=True)
    without = profit - win_profit[0] if win_profit else profit
    return {"races": n, "wins": won,
            "strike_pct": round(100*won/n, 3) if n else None,
            "priced_bets": b,
            "sp_coverage_pct": round(100*b/n, 3) if n else None,
            "profit_units": round(profit, 3),
            "sp_roi_pct": round(100*profit/b, 3) if b else None,
            "sp_roi_capped_20to1_pct": round(100*capped/b, 3) if b else None,
            "sp_roi_without_top_winner_pct": round(100*without/b, 3) if b else None,
            "average_selections_per_week": round(n/52.14, 2)}


def metrics_for_models(records):
    return {name: {"all":summarize([r for r in records if r["model"] == name]),
                   "selected":summarize([r for r in records if r["model"] == name],True)}
            for name in ("stage34_frozen_course", "stage30_global_original")}


def row_from_prediction(pred, key, label, threshold):
    race, ix, won, price, gap = pred
    price = (float(price) if math.isfinite(price) and 1.01<=price<=5000 else None)
    evidence = race["ground_evidence"][ix] if race.get("ground_evidence") else {}
    return {"cohort":cohort(int(race["year"])), "year":int(race["year"]),
            "date":str(race["date"]), "race_id":str(race["race_id"]),
            "course":str(race["course"]), "course_key":race["course_key"],
            "discipline":race["kind"], "track_model":key,
            "distance":race.get("trip_key", "distance_unverified"),
            "going":race.get("ground_label") or "going_unverified",
            "field_size":len(race["horse"]),
            "model":label, "selection":str(race["horse"][ix]),
            "won":int(won),"sp_decimal":price,
            "score_lead":round(float(gap),7),"frozen_gate":threshold,
            "selected":int(gap>=threshold),
            "ground_evidence":evidence.get("source", "unverified"),
            "horse_previous_runs_on_going":evidence.get("horse_runs_on_going",0),
            "sire_offspring_runs_on_going":evidence.get("sire_progeny_runs_on_going",0),
            "dam_offspring_runs_on_going":evidence.get("dam_progeny_runs_on_going",0)}


def replay(samples, stage34, benchmark, report34, d30, output):
    out = Path(output)
    out.mkdir(parents=True,exist_ok=True)
    rows = []
    by_course = {}
    checks = {}
    for key, (discipline, track) in TARGETS.items():
        course_rows=[]
        for year in YEARS:
            matches = [r for r in samples.get(year,[]) if
                       r["kind"]==discipline and r["course_key"]==track]
            for r in matches:
                for label, profile in (("stage34_frozen_course",stage34[key]),
                                       ("stage30_global_original",benchmark)):
                    p=BASE.prediction([r],np.asarray(profile["weights"],dtype=np.float32),"D")[0]
                    course_rows.append(row_from_prediction(p,key,label,profile["gate"]))
        rows.extend(course_rows)
        by_year={}
        for year in YEARS:
            part=[r for r in course_rows if r["year"]==year]
            if not part and year==2026:
                by_year[str(year)]={"status":"NO_VERIFIED_2026_RESULTS_IN_SQLITE",
                                   "comparisons":None}
            else:
                by_year[str(year)]={"status":cohort(year),"comparisons":metrics_for_models(part)}
        by_course[key]={"historical_races_by_year":{
            str(y):len([x for x in course_rows if x["year"]==y and x["model"]=="stage34_frozen_course"])
            for y in YEARS}, "yearly":by_year,
            "2018_2022_retrospective":metrics_for_models([r for r in course_rows if 2018<=r["year"]<=2022]),
            "2023_2024_discovery_validation":metrics_for_models([r for r in course_rows if 2023<=r["year"]<=2024]),
            "2025_reviewed_audit":metrics_for_models([r for r in course_rows if r["year"]==2025]),
            "2026_forward_if_available":metrics_for_models([r for r in course_rows if r["year"]==2026])}
        expected=report34.get("profiles",{}).get("courses",{}).get(key,{})
        expected_2025=report34.get("_stage34_by_course",{}).get(key)
        actual=by_year["2025"]["comparisons"]["stage34_frozen_course"]["selected"]
        check={"selected_races_actual":actual["races"],
               "selected_wins_actual":actual["wins"],
               "selected_roi_actual":actual["sp_roi_pct"],
               "published_stage34_selected":expected_2025,
               "reproduces_published_stage34":None}
        if expected_2025:
            exp=expected_2025["hierarchical_selected"]
            check["reproduces_published_stage34"]=(
                actual["races"] == exp["races"] and
                actual["wins"] == exp["wins"] and
                actual["priced_bets"] == exp["priced_bets"] and
                abs((actual["sp_roi_pct"] or 0)-exp["roi_pct"])<0.015)
        checks[key]=check
    def csvwrite(filename,data):
        if not data:return
        with (out/filename).open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]))
            w.writeheader();w.writerows(data)
    rows.sort(key=lambda r:(r["year"],r["date"],r["course_key"],r["race_id"],r["model"]))
    csvwrite("stage37-frozen-all-races-2018-2026.csv",rows)
    csvwrite("stage37-frozen-selected-races-2018-2026.csv",
             [r for r in rows if r["selected"]])
    comparisons=[]
    for key in TARGETS:
        for year in YEARS:
            for label in ("stage34_frozen_course", "stage30_global_original"):
                r=[x for x in rows if x["track_model"]==key and x["year"]==year and x["model"]==label]
                for subset in ("all","selected"):
                    comparisons.append({"course":key,"year":year,"period":cohort(year),
                        "model":label,"subset":subset,**summarize(r,subset=="selected")})
    csvwrite("stage37-year-by-year-head-to-head.csv",comparisons)
    conditions=[]
    for key in TARGETS:
        course_rows=[r for r in rows if r["track_model"]==key]
        for dimension in ("distance", "going", "ground_evidence", "field_size"):
            labels=sorted(set(str(r[dimension]) if dimension!="field_size" else
                              ("2_to_7" if r["field_size"]<=7 else
                               "8_to_11" if r["field_size"]<=11 else
                               "12_plus") for r in course_rows))
            for label in labels:
                for period,years in (("2018_2022_RETROSPECTIVE",tuple(range(2018,2023))),
                                     ("2023_2024_FITTING",(2023,2024)),
                                     ("2025_REVIEWED_AUDIT",(2025,)),
                                     ("2026_FORWARD_IF_VERIFIED",(2026,))):
                    part=[r for r in course_rows if r["year"] in years and
                          (str(r[dimension]) if dimension!="field_size" else
                           "2_to_7" if r["field_size"]<=7 else
                           "8_to_11" if r["field_size"]<=11 else "12_plus") == label]
                    for name in ("stage34_frozen_course","stage30_global_original"):
                        x=[r for r in part if r["model"]==name]
                        metric=summarize(x,True)
                        if metric["races"]:
                            conditions.append({"course":key,"dimension":dimension,"value":label,
                                "period":period,"model":name,"descriptive_only":True,
                                "minimum_30_selections":metric["races"]>=30,**metric})
    csvwrite("stage37-conditions-descriptive-not-optimised.csv",conditions)
    frozen_snapshot={
        "version":VERSION,"status":"EXACT_FROZEN_STAGE34_WEIGHTS_RESEARCH_ONLY",
        "models":{k:{"weights_pct":stage34[k]["original_profile"]["weights_pct"],
                      "selection_margin":stage34[k]["gate"],
                      "frozen_from_stage":"stage34-frozen-track-profiles.json",
                      "status_at_original_selection":stage34[k]["original_profile"]["status"]}
                  for k in TARGETS},
        "global_original":{"weights_pct":d30["weights_pct"],"selection_margin":benchmark["gate"]},
        "important":"2023 and 2024 are NOT new unseen evidence; 2025 was already reviewed. 2018-2022 is a backwards replay, not a forward holdout."}
    (out/"stage37-unchanged-frozen-models.json").write_text(
        json.dumps(frozen_snapshot,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    forward_count=sum(v["historical_races_by_year"]["2026"] for v in by_course.values())
    summary={"version":VERSION,"status":"RESEARCH_ONLY_NOT_LIVE",
             "betfair_required":False,"global_original_changed":False,
             "frozen_course_models_modified":False,
             "years":list(YEARS),"2026_verified_races_in_source":forward_count,
             "forward_2026_status":("VERIFIED_RESULTS_PRESENT_IN_SOURCE" if forward_count else
                                    "NO_VERIFIED_2026_RESULTS_IN_STAGE2_SQLITE"),
             "stage34_2025_reproduction_checks":checks,
             "by_course":by_course,
             "caveats":[
                 "2018-22 is a backwards stress-test with model weights discovered in 2023, NOT independent prospective validation.",
                 "2023 selection year, 2024 validation year and 2025 previously viewed audit do not provide fresh unseen evidence.",
                 "No 2026 race result or ROI may be asserted unless verified historical starter, outcome and SP records exist in input SQLite.",
                 "One top-ranked runner per eligible race; selected subset requires exact immutable Stage34 score-gap gate.",
                 "No prices, SP, market movement or winner labels are used in pre-race rankings. SP settles one-unit hypothetical win bets only.",
                 "All ground/going/distance/field-size breakdowns are DESCRIPTIVE only, not used to choose new rules.",
                 "Winnings are hypothetical SP returns, not realizable bookmaker ROI; price availability differs by race.",
                 "Do not infer 2026 live profitability from pre-2026 historical performance. Forward shadow logging would be separate.",
                 "Stage37 never edits live app, model weights, or bookmaker settings."]}
    (out/"stage37-summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return summary


def self_test():
    assert YEARS[0]==2018 and YEARS[-1]==2026
    assert cohort(2018).startswith("RETROSPECTIVE") and cohort(2024).startswith("STAGE34_ACCEPTANCE")
    base={"model":"stage34_frozen_course","won":0,"selected":0,"sp_decimal":5.,
          "year":2020,"date":"2020-04-15","course_key":"dundalk"}
    a=dict(base,selected=1,won=1,sp_decimal=3.)
    b=dict(base,selected=1,won=0,sp_decimal=5.)
    x=summarize([a,b],selected=True)
    assert x["races"]==2 and x["wins"]==1 and x["profit_units"]==1 and x["sp_roi_pct"]==50
    assert summarize([base],selected=True)["races"]==0
    assert abs(float(ST34.normalize([7,10,6,18,18,9,20,12]).sum())-1)<1.e-9
    print("Stage37 self-test passed: unchanged cohort labels, one-unit SP, selected/all, weights")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",help="verified Stage2 SQLite historical archive")
    parser.add_argument("--stage34-profiles",default="reports/stage34/stage34-frozen-track-profiles.json")
    parser.add_argument("--stage34-summary",default="reports/stage34/stage34-summary.json")
    parser.add_argument("--stage30-summary",default="reports/stage30/stage30-summary.json")
    parser.add_argument("--output",default="stage37_report")
    parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args()
    if args.self_test:
        self_test();return
    if not args.db:
        parser.error("--db is required")
    stage34,benchmark,profiles,stage30=load_configs(args.stage34_profiles,args.stage30_summary)
    prior34=json.loads(Path(args.stage34_summary).read_text(encoding="utf-8"))
    profiles["_stage34_by_course"]=prior34.get("2025_by_course",{})
    enable_years_for_audit()
    history=ST35.GroundPast()
    samples,audit=history.scan(args.db)
    print("Scanned UK/IE archive:",audit,"course replay target years:",
          {year:len(samples.get(year,[])) for year in YEARS},flush=True)
    if any(len(samples.get(year,[]))<250 for year in (2018,2019,2020,2021,2022,2023,2024,2025)):
        raise RuntimeError("Missing or sparse 2018-2025 historic racing source; refusing misleading comparison")
    summary=replay(samples,stage34,benchmark,profiles,stage30,args.output)
    summary["source_audit"]=audit
    output=Path(args.output)/"stage37-summary.json"
    output.write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print("2025 Stage34 reproduction:",json.dumps(summary["stage34_2025_reproduction_checks"]),flush=True)
    for name in TARGETS:
        print(name,"retrospective 2018-22:",
              json.dumps(summary["by_course"][name]["2018_2022_retrospective"]),flush=True)
    print("2026 status:",summary["forward_2026_status"],flush=True)
    # Integrity protects against inadvertently claiming the wrong Stage34 model.
    failed=[name for name,check in summary["stage34_2025_reproduction_checks"].items()
            if check["reproduces_published_stage34"] is False]
    if failed:
        raise RuntimeError("Stage34 2025 profile replay differs from original report: "+str(failed)+
                           ". Output preserved for audit; do NOT use conclusions until investigated.")


if __name__=="__main__":
    main()
