#!/usr/bin/env python3
"""Safe, single-call PuntersEdge GB/IE form coverage diagnostic.

No credentials or full third-party response are persisted.
GitHub Actions: secret PUNTERSEDGE_API_KEY must already be configured.
Uses one GET /v1/racing/form request (normally 3 credits).
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://api.puntersedge.online/v1/racing/form"


def latest_race_from_odds(path: Path):
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("prices", []) if isinstance(data, dict) else []
    candidates = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        country = str(row.get("country") or "").upper()
        race_id = row.get("race_id")
        if country not in ("GB", "IE", "IRE") or not race_id:
            continue
        obj = candidates.setdefault(str(race_id), {
            "date": str(row.get("race_time") or ""), "course": str(row.get("course") or ""),
            "runners": set(), "race_id": str(race_id),
        })
        obj["runners"].add(str(row.get("horse") or ""))
    if not candidates:
        return None
    ranked = sorted(candidates.values(), key=lambda x: (x["date"], len(x["runners"])), reverse=True)
    return ranked[0]


def report_from_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object from API")
    runners = payload.get("runners")
    if not isinstance(runners, list):
        raise ValueError("API response has no runners list")
    sample = []
    all_history_dates = []
    num_with_runs = 0
    num_with_last10 = 0
    num_with_rating = 0
    keysets = Counter()
    for row in runners:
        if not isinstance(row, dict):
            continue
        runs = row.get("runs") if isinstance(row.get("runs"), list) else []
        valid_runs = [r for r in runs if isinstance(r, dict)]
        dates = [str(r["date"]) for r in valid_runs if r.get("date")]
        all_history_dates.extend(dates)
        if valid_runs:
            num_with_runs += 1
        if row.get("last10") not in (None, "", []):
            num_with_last10 += 1
        if row.get("rating") not in (None, "", []):
            num_with_rating += 1
        keysets.update(row.keys())
        if len(sample) < 8:
            sample.append({
                "horse": row.get("name"),
                "rating": row.get("rating"),
                "last10": row.get("last10"),
                "runs_count": len(valid_runs),
                "runs_total": row.get("runs_total"),
                "oldest_run_date_visible": min(dates) if dates else None,
            })
    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "PuntersEdge /v1/racing/form (one race)",
        "race": {
            "race_id": payload.get("race_id"), "venue": payload.get("venue"),
            "meeting_date": payload.get("meeting_date"), "race_number": payload.get("race_number"),
            "card_source": payload.get("card_source"), "form_as_at": payload.get("form_as_at"),
        },
        "coverage": {
            "region": src.get("region"), "coverage_from": src.get("coverage_from"),
            "coverage_to": src.get("coverage_to"),
            "runners": len(runners), "runners_with_detailed_runs": num_with_runs,
            "runners_with_form_figures": num_with_last10,
            "runners_with_rating": num_with_rating,
            "earliest_run_in_this_response": min(all_history_dates) if all_history_dates else None,
            "field_names": sorted(keysets.keys()),
        },
        "sample_runners": sample,
        "note": "Only shows pre-race details held by this endpoint. The coverage_from date limits historical depth; it is not a complete career record for GB/IE.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--race-id", default=None)
    parser.add_argument("--venue", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--race-number", type=int, default=None)
    parser.add_argument("--local-sample", help="Offline JSON response for schema testing; no API call")
    parser.add_argument("--output", default="data/puntersedge-form-probe.json")
    args = parser.parse_args()
    if args.local_sample:
        payload = json.loads(Path(args.local_sample).read_text(encoding="utf-8"))
    else:
        key = os.environ.get("PUNTERSEDGE_API_KEY")
        if not key:
            raise SystemExit("Missing GitHub secret PUNTERSEDGE_API_KEY")
        if args.race_id:
            params = {"race_id": args.race_id, "runs": 20}
        elif args.venue and args.date and args.race_number:
            params = {"venue": args.venue, "meeting_date": args.date,
                      "race_number": args.race_number, "runs": 20}
        else:
            candidate = latest_race_from_odds(Path("data/latest-odds.json"))
            if not candidate:
                raise SystemExit("No GB/IE race in data/latest-odds.json. Provide --venue, --date and --race-number")
            print("Auto-selected course:", candidate["course"], "scheduled:", candidate["date"])
            params = {"race_id": candidate["race_id"], "runs": 20}
        req = Request(BASE + "?" + urlencode(params), headers={
            "X-API-Key": key, "Accept": "application/json", "User-Agent": "RacingHotPots/1.0-form-probe",
        })
        try:
            with urlopen(req, timeout=35) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise SystemExit(f"PuntersEdge returned HTTP {exc.code} (response body hidden to protect credentials)") from None
        except (URLError, TimeoutError) as exc:
            raise SystemExit(f"Network error while calling form endpoint: {type(exc).__name__}") from None
    report = report_from_payload(payload)
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Wrote summary:", dest)
    print("Race:", report["race"])
    print("Coverage:", {k: v for k, v in report["coverage"].items() if k != "field_names"})
    print("Sample:", report["sample_runners"][:3])
    print("No API key or raw response was written to the report.")


if __name__ == "__main__":
    main()
