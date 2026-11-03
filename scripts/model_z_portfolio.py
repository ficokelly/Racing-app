#!/usr/bin/env python3
"""Model Z v1: candidate strategy registry, Stage37 evidence, and paper-status export.

Research/paper only. This script NEVER promotes a strategy, creates betting
selections, places bets, changes any model weights, or rewrites Models A-D.

- Registry: explicitly versioned frozen Stage34 course challengers, no auto-promote.
- Evidence: faithfully mirrors Stage37 output; labels backwards tests retrospective.
- Optional UI navigation link in index.html, added idempotently.
- Output: data/model-z-status.json for the standalone Model Z page.

2026 shadow selection generation is intentionally NOT faked: before Model Z can
publish picks, a separate true pre-off stage34 feature scorer must be tested
against live racecards and odds, with immutable timestamps and no lookahead.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path

VERSION = "model-z-paper-registry-v1"
DIMENSIONS = ("Ability", "Trainer", "Jockey", "Course", "Draw", "Distance", "Ground", "Jumping")
NAV_ANCHOR = '<button type="button" data-section="pnl">📊 P&amp;L</button>'
NAV_LINK = '<a class="model-z-nav" href="./model-z.html" aria-label="Model Z paper portfolio">🧪 Model Z · Paper</a>'
NAV_STYLE = ("\n<!-- MODEL_Z_PAPER_NAV_V1 -->\n<style>\n"
             '#section-buttons .model-z-nav{display:flex;align-items:center;justify-content:center;'
             'min-width:0;width:100%;min-height:52px;padding:9px 7px;'
             'font:inherit;font-size:clamp(11px,3vw,14px);font-weight:650;'
             'border:1px solid var(--rhp-edge);border-radius:12px;'
             'color:var(--rhp-ink);background:var(--rhp-tint);'
             'text-decoration:none;text-align:center;cursor:pointer;overflow-wrap:anywhere}'
             '#section-buttons .model-z-nav:focus-visible{outline:2px solid currentColor;outline-offset:2px}'
             '</style>\n')


def read_json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def validate_registry(r, frozen):
    if r.get("mode") != "RESEARCH_AND_PAPER_ONLY":
        raise ValueError("Model Z must remain in research and paper mode")
    if not r.get("policy", {}).get("manual_promotion_required"):
        raise ValueError("Manual promotion safeguard missing")
    if not r.get("policy", {}).get("never_auto_promote_from_historical_roi"):
        raise ValueError("No auto-promotion safeguard missing")
    if r.get("active_strategy_ids") != []:
        raise ValueError("Model Z v1 must start with zero live/active strategies")
    found = set()
    for s in r["strategies"]:
        if s["id"] in found:
            raise ValueError("Duplicate strategy id: " + s["id"])
        found.add(s["id"])
        if s["status"] != "CANDIDATE" or s["live_enabled"] or s["selection_automation_enabled"]:
            raise ValueError("Model Z v1 requires candidate-only, no automated betting selections")
        key = s["frozen_profile_key"]
        p = frozen.get(key)
        if not p or p["status"] != "VALIDATED_CHALLENGER":
            raise ValueError("Frozen Stage34 profile not found or was not a challenger: " + key)
        for name in DIMENSIONS:
            if abs(float(p["weights_pct"][name]) - float(s["weights_pct"][name])) > 0.00001:
                raise ValueError("Frozen weights changed for " + key + ": " + name)
        if abs(float(p["selection_margin"]) - float(s["selection_margin"])) > 1e-12:
            raise ValueError("Frozen selection margin changed for " + key)
    required = {"stage34-dundalk-aw", "stage34-newmarket-flat"}
    if not required.issubset(found):
        raise ValueError("Model Z must retain the original Dundalk and Newmarket candidates")
    # Additional Stage34 course strategies can be appended to the registry
    # without modifying this engine, provided their published frozen profiles
    # pass the same exact-weight check. They still start as CANDIDATE only.


def stage37_evidence(strategy, report):
    key = strategy["frozen_profile_key"]
    if not report:
        return {"status": "STAGE37_RUNNING_OR_NOT_PUBLISHED", "forward_available": False}
    if report.get("status") != "RESEARCH_ONLY_NOT_LIVE":
        return {"status": "STAGE37_REPORT_INVALID", "forward_available": False}
    item = report.get("by_course", {}).get(key)
    check = report.get("stage34_2025_reproduction_checks", {}).get(key, {})
    if not item:
        return {"status": "COURSE_MISSING_IN_STAGE37", "forward_available": False}
    if check.get("reproduces_published_stage34") is False:
        return {"status": "STAGE34_REPLAY_MISMATCH", "forward_available": False,
                "reproduction": check}
    def comparison(field):
        value = item.get(field, {})
        return {
            "frozen_stage34": value.get("stage34_frozen_course"),
            "original_global_D": value.get("stage30_global_original")
        }
    forward = comparison("2026_forward_if_available")
    num = int(forward.get("frozen_stage34", {}).get("selected", {}).get("races") or 0)
    # Even if 2026 results exist, this source is a retrospective archive replay;
    # it does not prove bets were recorded before the race. Never call it forward paper.
    return {"status": "HISTORICAL_REPLAY_AVAILABLE",
            "reproduction": check,
            "2018_2022_retrospective": comparison("2018_2022_retrospective"),
            "2023_2024_model_selection": comparison("2023_2024_discovery_validation"),
            "2025_already_viewed": comparison("2025_reviewed_audit"),
            "2026_archive_replay_if_any": forward,
            "2026_archive_selected_count": num,
            "forward_available": False,
            "forward_paper_count": 0,
            "independent_forward_validation": "NOT_YET_ESTABLISHED"}


def build_status(registry, stage37):
    items = []
    for s in registry["strategies"]:
        evidence = stage37_evidence(s, stage37)
        items.append({
            "id": s["id"], "name": s["name"], "course": s["course_display"],
            "discipline": s["discipline"], "status": "CANDIDATE",
            "live_enabled": False, "paper_tracking_enabled": True,
            "automated_picks_available": False,
            "model_version": s["model_version"],
            "selection_margin": s["selection_margin"], "weights_pct": s["weights_pct"],
            "frozen_profile_key": s["frozen_profile_key"],
            "stage34_2025": s["stage34_2025"], "notes": s["notes"],
            "stage37": evidence
        })
    return {
        "version": VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "RESEARCH_AND_PAPER_ONLY", "live_models_changed": False,
        "active_strategies": 0, "candidate_strategies": len(items),
        "automated_model_z_picks": [], "total_live_picks": 0,
        "paper_tracking_explanation": "Paper entries logged manually in this browser are not validated Model Z predictions, and are stored only on that device until exported. No automated shadow picks exist yet.",
        "promotion_policy": registry["policy"],
        "strategies": items
    }


def install_nav(index_path):
    p = Path(index_path)
    original = p.read_text(encoding="utf-8")
    if original.count(NAV_ANCHOR) != 1:
        raise ValueError("App navigation changed: refusing to inject Model Z link")
    if original.count(NAV_LINK) > 1:
        raise ValueError("Duplicate Model Z links exist")
    if NAV_LINK in original:
        return False
    updated = original.replace(NAV_ANCHOR, NAV_ANCHOR + NAV_LINK, 1)
    if "MODEL_Z_PAPER_NAV_V1" not in updated:
        updated = updated.replace("</head>", NAV_STYLE + "</head>", 1)
    p.write_text(updated, encoding="utf-8")
    return True


def self_test():
    from tempfile import TemporaryDirectory
    from copy import deepcopy
    fake = {"mode": "RESEARCH_AND_PAPER_ONLY",
            "policy": {"manual_promotion_required": True, "never_auto_promote_from_historical_roi": True},
            "active_strategy_ids": [], "strategies": []}
    prof = {}
    for key, name in (("aw|dundalk", "Dundalk"), ("flat|newmarket", "Newmarket")):
        weights = dict(zip(DIMENSIONS, (10.0, 10.0, 10.0, 20.0, 10.0, 10.0, 20.0, 10.0)))
        prof[key] = {"status": "VALIDATED_CHALLENGER", "weights_pct": weights, "selection_margin": .14}
        fake["strategies"].append({"id": "stage34-dundalk-aw" if name=="Dundalk" else "stage34-newmarket-flat",
            "name": name, "course_display": name, "discipline": key.split("|")[0],
            "status": "CANDIDATE", "live_enabled": False,
            "selection_automation_enabled": False, "frozen_profile_key": key,
            "selection_margin": .14, "weights_pct": deepcopy(weights),
            "model_version": "test", "stage34_2025": {"roi_pct": 10}, "notes": "test"})
    validate_registry(fake, prof)
    # Adding another future course candidate must not require editing engine.
    extra = deepcopy(fake["strategies"][0])
    extra["id"] = "another-frozen-course"
    extra["frozen_profile_key"] = "flat|ascot"
    prof["flat|ascot"] = deepcopy(prof["flat|newmarket"])
    fake["strategies"].append(extra)
    validate_registry(fake, prof)
    fake["strategies"].pop()
    a = build_status(fake, None)
    assert a["active_strategies"]==0 and len(a["strategies"])==2
    assert a["strategies"][0]["stage37"]["forward_available"] is False
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "index.html"
        p.write_text('<html><head></head><body>'+NAV_ANCHOR+'</body></html>',encoding="utf-8")
        assert install_nav(p)
        assert not install_nav(p)
        assert p.read_text().count(NAV_LINK)==1
    fake["strategies"][0]["weights_pct"]["Ground"] = 42
    try:
        validate_registry(fake,prof)
    except ValueError:
        pass
    else:
        raise AssertionError("Changed frozen weights were allowed")
    print("Model Z self-test passed: exact frozen profiles, candidate-only, nav idempotence")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--registry", default="data/model-z-strategies.json")
    ap.add_argument("--stage34", default="reports/stage34/stage34-frozen-track-profiles.json")
    ap.add_argument("--stage37", default="reports/stage37/stage37-summary.json")
    ap.add_argument("--output", default="data/model-z-status.json")
    ap.add_argument("--install-nav", action="store_true")
    ap.add_argument("--index", default="index.html")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    r = read_json(args.registry)
    s34 = read_json(args.stage34)
    validate_registry(r, s34["profiles"]["courses"])
    report37 = read_json(args.stage37) if Path(args.stage37).is_file() else None
    output = build_status(r, report37)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # An unchanged registry/report must not create a daily GitHub commit just
    # because the timer ran again. Keep the original generated timestamp.
    if dest.is_file():
        previous = read_json(dest)
        if {k: v for k, v in previous.items() if k != "generated_at_utc"} == {k: v for k, v in output.items() if k != "generated_at_utc"}:
            output["generated_at_utc"] = previous.get("generated_at_utc", output["generated_at_utc"])
    dest.write_text(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    if args.install_nav:
        print("New navigation link added:", install_nav(args.index))
    print("Model Z status published: candidates", output["candidate_strategies"],
          "active",output["active_strategies"],"Stage37 available", bool(report37))


if __name__ == "__main__":
    main()
