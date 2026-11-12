#!/usr/bin/env python3
"""Stage105c — reproducible 12 Nov 2026, 10:40 Ireland ROI-first E+HPR ML replay.

Only 10:25 Git-proven odds/racecards may affect today's predictions. No later
bookmaker prices, racecards, race results or SP. Stage105b is not modified.
"""
from __future__ import annotations
import argparse,collections,datetime as dt,json,pathlib,subprocess,sys

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage105_e_hpr_roi_all_races as BASE

TARGET="2026-11-12"
ODDS_SHA="0f216fba04a2667248b02399557b8e2a53eac771"
CARDS_SHA="f47a29990f2b00310ec7fff1e609020986740335"
UTC=dt.timezone.utc
LOCK=dt.datetime(2026,11,12,10,40,tzinfo=UTC)
ODDS_EXPECTED=dt.datetime(2026,11,12,10,25,39,tzinfo=UTC)
CARDS_EXPECTED=dt.datetime(2026,11,12,10,25,56,tzinfo=UTC)

def git_blob_and_time(sha,path,expected):
 try:
  blob=subprocess.run(["git","show",sha+":"+path],cwd=ROOT,capture_output=True,
                      text=True,check=True,timeout=40).stdout
  stamp=subprocess.run(["git","show","-s","--format=%ct",sha],cwd=ROOT,capture_output=True,
                       text=True,check=True,timeout=20).stdout.strip()
  published=dt.datetime.fromtimestamp(int(stamp),tz=UTC)
  if published!=expected or published>LOCK:
   raise ValueError("Pinned source has unexpected commit timestamp")
  return json.loads(blob),published
 except (ValueError,OSError,subprocess.SubprocessError) as exc:
  raise RuntimeError("Cannot verify historical "+path+" at SHA "+sha) from exc

def decision_cutoff(day):
 if day!=TARGET:raise RuntimeError("Pinned experiment only allows 12 Nov 2026")
 return LOCK

def pinned_cards(day,now):
 import stage86_hpr_engine as HPR
 raw,committed=git_blob_and_time(CARDS_SHA,"data/racecards.json",CARDS_EXPECTED)
 if BASE.asof_cutoff(now,day)<committed:
  raise RuntimeError("Cannot use racecards before their recorded publication")
 grouped=collections.defaultdict(list)
 for c in HPR.flat_cards(raw):
  if str(c.get("date") or c.get("race_time") or "")[:10]!=day:continue
  course=BASE.S101.norm(c.get("course"));off=BASE.S101.clock(c.get("race_time"))
  if not course or not off or not c.get("horse") or BASE.is_nonrunner(c):continue
  grouped[(day,course,off)].append(c)
 entries=[];issues=[]
 for key,cards in sorted(grouped.items(),key=lambda x:(x[0][2],x[0][1])):
  if len(cards)<2:
   issues.append({"race_key":"|".join(key),"reason":"FEWER_THAN_TWO_DECLARED_RUNNERS"});continue
  if len({BASE.S101.norm(c["horse"]) for c in cards})!=len(cards):
   issues.append({"race_key":"|".join(key),"reason":"DUPLICATE_RUNNER_NAMES"});continue
  stub={"date":day,"course":cards[0]["course"],
        "runners":[{"horse":c["horse"],"position":""} for c in cards]}
  entries.append({"date":day,"course":cards[0]["course"],"time":key[2],
                  "key":key,"cards":cards,"result":stub,
                  "race_name":cards[0].get("race_name") or "",
                  "snapshot":"git_pinned_1025","snapshot_commit":committed})
 return entries,issues,committed

def pinned_prices(self,now):
 snapshot,committed=git_blob_and_time(ODDS_SHA,"data/latest-odds.json",ODDS_EXPECTED)
 cutoff=BASE.asof_cutoff(now,TARGET)
 collected=BASE.S102.timestamp(snapshot.get("collected_at"))
 if collected is None or collected>committed or committed>cutoff:
  raise RuntimeError("Odds were not available before the 10:40 cutoff")
 self.add_snapshot(snapshot,"git_pinned_1025_"+ODDS_SHA[:12],committed)
 if self.audit["today_wh_rows"]<20:
  raise RuntimeError("Pinned odds lack sufficient William Hill runner quotes")
 self.audit["pinned_odds_sha"]=ODDS_SHA
 self.audit["pinned_racecards_sha"]=CARDS_SHA
 self.audit["pinned_collected_at"]=collected.isoformat()
 self.audit["pinned_commit_utc"]=committed.isoformat()
 self.audit["selection_lock_utc"]=LOCK.isoformat()
 self.audit["source_policy"]="ONLY_GIT_PINNED_1025_ODDS_NO_FUTURE_QUOTES"
 return self

BASE.morning_cutoff=decision_cutoff
BASE.snapshot_date_cards=pinned_cards
BASE.TodayPriceArchive.load_today=pinned_prices

def self_test():
 assert decision_cutoff(TARGET)==LOCK
 assert BASE.asof_cutoff(LOCK+dt.timedelta(minutes=20),TARGET)==LOCK
 assert BASE.asof_cutoff(LOCK-dt.timedelta(minutes=1),TARGET)<LOCK
 assert ODDS_EXPECTED<CARDS_EXPECTED<LOCK
 assert "get_asof" in BASE.TodayPriceArchive.__dict__
 print("PASS Stage105c pinned snapshot / 10:40 cutoff invariants")

def main():
 p=argparse.ArgumentParser(description=__doc__)
 sub=p.add_subparsers(dest="command",required=True)
 sub.add_parser("self-test")
 r=sub.add_parser("run")
 r.add_argument("--date",default=TARGET)
 r.add_argument("--db",required=True)
 r.add_argument("--mapping",required=True)
 r.add_argument("--ratings",required=True)
 r.add_argument("--output",default="reports/stage105c/output")
 r.add_argument("--now",default=None)
 a=p.parse_args()
 if a.command=="self-test":self_test();return
 now=BASE.as_datetime(a.now) if a.now else dt.datetime.now(UTC)
 if now is None or now<LOCK or now.date()!=LOCK.date():
  raise RuntimeError("Only run on 12 Nov after 10:40 Ireland; no backdated live claim")
 BASE.run(a)
 out=pathlib.Path(a.output)
 auditfile=out/"stage105-audit.json"
 audit=json.loads(auditfile.read_text())
 assert audit["selection_cutoff_utc"]==LOCK.isoformat()
 audit["version"]="stage105c-pinned-1025-odds-1040-decision"
 audit["locked_odds_sha"]=ODDS_SHA
 audit["locked_racecards_sha"]=CARDS_SHA
 audit["locked_odds_commit_utc"]=ODDS_EXPECTED.isoformat()
 audit["locked_racecards_commit_utc"]=CARDS_EXPECTED.isoformat()
 audit["is_replay"]=True
 audit["notes"].append("Reproduced at fixed 10:40 Ireland as-of cutoff; only pinned 10:25 odds/racecards.")
 auditfile.write_text(json.dumps(audit,indent=2)+"\n")
 readme=out/"README.md"
 readme.write_text("# STAGE105c — immutable 10:40 E+HPR selections\n\n"
    "Odds source: 10:25:39 UTC Git commit "+ODDS_SHA+"\n\n"
    "Racecards: 10:25:56 UTC Git commit "+CARDS_SHA+"\n\n"
    "Selection decision: 10:40 UTC, 12 Nov 2026. "
    "No later odds or results used. Morning predictions are preserved.\n\n"+
    readme.read_text())
 print("STAGE105c verified",audit["race_count"],"races,",
       audit["available_wh_runner_quotes"],"William Hill runner prices,",
       audit["roi_qualified_bets"],"ROI-qualified.")
if __name__=="__main__":main()
