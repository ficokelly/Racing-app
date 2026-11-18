#!/usr/bin/env python3
"""Only Stage153 ROI Specialists publication notifications; append durable review history.

No horse notifications or betting alerts. The shared JSON is an audit; clearing from
an iPhone is a local viewing preference and never destroys the central history.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FEED = DATA / "strategy-roi-combined.json"
STATE = DATA / "roi-notification-state.json"
HISTORY = DATA / "roi-notifications.json"
APP_ID = "1bbabde1-23d9-4e6a-9c97-eacf144d332b"
TARGET = "https://ficokelly.github.io/Racing-app/?tab=power&updates=1"
DUBLIN = ZoneInfo("Europe/Dublin")
UTC = dt.timezone.utc


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def slug(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def first_two(race):
    runners = [x for x in (race.get("runners") or []) if isinstance(x, dict)]
    ranked = sorted((x for x in runners if str(x.get("rank", "")).isdigit()), key=lambda x: int(x["rank"]))
    first = race.get("selection") if isinstance(race.get("selection"), dict) else {}
    top = str(first.get("horse") or (ranked[0].get("horse") if ranked else "") or "")
    second = next((str(x.get("horse") or "") for x in ranked if int(x["rank"]) == 2), "")
    # Do not describe a withdrawn horse as a valid new selection.
    if race.get("selection_status") != "RESEARCH_ONLY":
        top = str(first.get("horse") or "")
    return top, second


def race_snapshot(feed):
    result = {}
    for race in (feed.get("races") or []):
        if not isinstance(race, dict):
            continue
        day = str(race.get("date") or feed.get("day") or "")
        course = str(race.get("course") or "").strip()
        time = str(race.get("time") or "").strip()
        if not time and race.get("race_time"):
            try:
                timestamp = dt.datetime.fromisoformat(str(race["race_time"]).replace("Z", "+00:00"))
                time = timestamp.astimezone(DUBLIN).strftime("%H:%M")
            except ValueError:
                pass
        if not day or not course or not time:
            continue
        key = "|".join([day, slug(course), time[:5]])
        a, b = first_two(race)
        result[key] = {
            "date": day, "course": course, "time": time[:5],
            "race_type": str(race.get("race_type") or ""),
            "model": str(race.get("source_model") or ""),
            "rank1": a, "rank2": b,
            "nap_nb": str(race.get("nap_nb") or ""),
        }
    return result


def current_feed():
    feed = read_json(FEED, {})
    now = dt.datetime.now(DUBLIN)
    date = str(feed.get("day") or "")
    allowed = {now.date().isoformat(), (now.date() + dt.timedelta(days=1)).isoformat()}
    if date not in allowed:
        print("NO_CURRENT_DAY_FEED", date, "expected", sorted(allowed))
        return None
    at = str(feed.get("generated_at") or "")
    try:
        generated = dt.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        print("MISSING_GENERATED_AT")
        return None
    if generated.tzinfo is None or generated.astimezone(UTC) > dt.datetime.now(UTC) + dt.timedelta(minutes=3):
        print("INVALID_FEED_TIME", at)
        return None
    races = race_snapshot(feed)
    if not any(r["rank1"] for r in races.values()):
        print("NO_PUBLISHED_RANK1", date)
        return None
    return date, at, races


def change_rows(previous, current):
    changes = []
    # A race may be added after the first published assessment or removed after a cancellation.
    for key in sorted(set(previous) | set(current)):
        old, new = previous.get(key) or {}, current.get(key) or {}
        before = (str(old.get("rank1") or ""), str(old.get("rank2") or ""))
        after = (str(new.get("rank1") or ""), str(new.get("rank2") or ""))
        if before == after:
            continue
        if not any(before + after):
            continue
        race = new or old
        changes.append({
            "race_key": key,
            "meeting": race.get("course", ""), "time": race.get("time", ""),
            "race_type": race.get("race_type", ""), "model": race.get("model", ""),
            "rank1_before": before[0], "rank1_after": after[0],
            "rank2_before": before[1], "rank2_after": after[1],
            "reason": "Field or specialist reassessment; see the latest racecard",
        })
    return changes


def make_event(day, stamp, previous, current, first):
    now = dt.datetime.now(UTC)
    local = now.astimezone(DUBLIN)
    selected = sum(bool(x["rank1"]) for x in current.values())
    changes = [] if first else change_rows(previous, current)
    count = len(changes)
    if first:
        dayword = "Tomorrow's" if day == (local.date() + dt.timedelta(days=1)).isoformat() else "Today's"
        title = f"{dayword} Hot Pots selections are live"
        summary = f"{selected} races assessed. Open Hot Pots to see all selections, NAPs and NBs."
    else:
        title = f"Selections have been reviewed — {count} change{'s' if count != 1 else ''} made"
        summary = ("No selection changes from the previous published assessment." if count == 0 else
                   f"{count} race{'s' if count != 1 else ''} updated. Tap to see which selections changed.")
    idtext = f"{day}|{stamp}|{'first' if first else 'review'}"
    identifier = hashlib.sha256(idtext.encode("utf-8")).hexdigest()[:24]
    return {"id": identifier, "date": day, "created_at": now.isoformat(),
            "feed_generated_at": stamp, "kind": "first" if first else "review",
            "title": title, "summary": summary,
            "selection_count": selected, "change_count": count, "changes": changes,
            "push_status": "pending"}


def send_push(event, key, subscription):
    if not key:
        return {"status": "pending", "reason": "ONESIGNAL_API_KEY not configured"}
    # The same event ID uses the same OneSignal idempotency key across retries.
    dedup = hashlib.sha256(f"hot-pots:stage154:{event['id']}".encode("utf-8")).digest()[:16]
    idempotency = str(uuid.UUID(bytes=dedup, version=4))
    payload = {
        "app_id": APP_ID,
        "idempotency_key": idempotency,
        **({"include_subscription_ids": [subscription]} if subscription else
           {"included_segments": ["Subscribed Users"]}),
        "headings": {"en": event["title"]},
        "contents": {"en": event["summary"]},
        "web_url": TARGET,
        "data": {"tab": "power", "updates": "1", "event_id": event["id"],
                 "selection_date": event["date"], "changes": event["change_count"]},
    }
    request = urllib.request.Request(
        "https://api.onesignal.com/notifications",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": "Key " + key, "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if result.get("errors") or not result.get("id"):
            return {"status": "pending", "reason": str(result)[:300]}
        return {"status": "sent", "provider_id": result["id"]}
    except (OSError, ValueError, TimeoutError, urllib.error.URLError) as exc:
        return {"status": "pending", "reason": str(exc)[:300]}


def main():
    state = read_json(STATE, {"days": {}})
    history = read_json(HISTORY, {"version": 1, "events": []})
    events = list(history.get("events") or [])
    original_state = json.dumps(state, sort_keys=True, ensure_ascii=False)
    original_history = json.dumps(history, sort_keys=True, ensure_ascii=False)
    feed = current_feed()
    if feed:
        day, stamp, current = feed
        state.setdefault("days", {})
        before = state["days"].get(day)
        if not before or str(before.get("generated_at")) != stamp:
            first = before is None
            previous = (before or {}).get("races") or {}
            event = make_event(day, stamp, previous, current, first)
            if not any(x.get("id") == event["id"] for x in events):
                events.insert(0, event)
                print("NOTIFICATION_REVIEW_RECORDED", event["title"], "changes", event["change_count"])
            state["days"][day] = {"generated_at": stamp, "races": current}
        else:
            print("ALREADY_REVIEWED_FEED", stamp)
    # Independently retry pending deliveries; even if no fresh Stage153 run happened.
    secret = os.environ.get("ONESIGNAL_API_KEY", "").strip()
    subscriber = os.environ.get("ONESIGNAL_SUBSCRIPTION_ID", "").strip()
    for event in reversed(events):
        if event.get("push_status") == "sent":
            continue
        if event.get("push_status") not in ("pending", None):
            continue
        try:
            created = dt.datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            created = dt.datetime.min.replace(tzinfo=UTC)
        if (dt.datetime.now(UTC) - created.astimezone(UTC)) > dt.timedelta(hours=2):
            event["push_status"] = "expired"
            event["push_error"] = "Notification no longer timely; preserved in app history"
            continue
        result = send_push(event, secret, subscriber)
        event["push_status"] = result["status"]
        if result.get("provider_id"):
            event["provider_id"] = result["provider_id"]
            event.pop("push_error", None)
            print("PUSH_SENT", event["title"])
        else:
            event["push_error"] = result.get("reason", "Unknown delivery error")
            print("PUSH_PENDING", event["title"], event["push_error"])
            # Try again on the next workflow run; avoid hammering the service.
            if not secret:
                break
    # Keep 60 days / 150 events without erasing local viewer preferences.
    cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=60)).isoformat()
    events = [x for x in events if str(x.get("created_at") or "") >= cutoff][:150]
    state["days"] = {k: v for k, v in (state.get("days") or {}).items()
                     if k >= (dt.datetime.now(DUBLIN).date() - dt.timedelta(days=3)).isoformat()}
    proposed = {"version": 1, "events": events}
    proposed["updated_at"] = history.get("updated_at", "")
    if json.dumps(proposed, sort_keys=True, ensure_ascii=False) != original_history:
        proposed["updated_at"] = dt.datetime.now(UTC).isoformat()
    if json.dumps(state, sort_keys=True, ensure_ascii=False) != original_state:
        write_json(STATE, state)
    if json.dumps(proposed, sort_keys=True, ensure_ascii=False) != original_history:
        write_json(HISTORY, proposed)
    print("NOTIFICATION_HISTORY_EVENTS", len(events), "PENDING_PUSH", sum(e.get("push_status") == "pending" for e in events))


if __name__ == "__main__":
    main()
