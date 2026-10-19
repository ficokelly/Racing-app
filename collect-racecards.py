"""Collect UK/Irish racecards for Racing Hot Pots using the free Racing API.

Preserves the existing 'runners' schema and adds optional race metadata for
race headings. No credentials are written into the published JSON.
"""
import os
import json
import base64
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

USERNAME = os.environ["RACING_API_USERNAME"]
PASSWORD = os.environ["RACING_API_PASSWORD"]
BASE = "https://api.theracingapi.com/v1/racecards/free"
credentials = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

all_runners = []
RACE_FIELDS = (
    "race_id", "race_name", "race_class", "pattern", "prize", "distance",
    "distance_f", "distance_round", "going", "going_detailed", "age_band",
    "field_size", "rating_band", "race_status", "sex_restriction",
)
RUNNER_FIELDS = (
    "form", "age", "lbs", "draw", "number", "ofr", "headgear",
    "owner", "sex", "last_run", "sire", "dam",
)

for day in ("today", "tomorrow"):
    skip = 0
    while True:
        query = urllib.parse.urlencode({"day": day, "limit": 500, "skip": skip})
        request = urllib.request.Request(
            BASE + "?" + query,
            headers={"Authorization": "Basic " + credentials, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.load(response)
        racecards = data.get("racecards", [])
        for race in racecards:
            if race.get("region") not in ("GB", "IRE"):
                continue
            metadata = {field: race.get(field) for field in RACE_FIELDS}
            for runner in race.get("runners", []):
                item = {
                    "date": race.get("date"),
                    "course": race.get("course"),
                    "race_time": race.get("off_dt"),
                    "type": race.get("type"),
                    "surface": race.get("surface"),
                    "horse": runner.get("horse"),
                    "horse_id": runner.get("horse_id"),
                    "trainer": runner.get("trainer"),
                    "trainer_id": runner.get("trainer_id"),
                    "jockey": runner.get("jockey"),
                    "jockey_id": runner.get("jockey_id"),
                }
                item.update(metadata)
                item.update({field: runner.get(field) for field in RUNNER_FIELDS})
                all_runners.append(item)
        if len(racecards) < 500:
            break
        skip += 500
        time.sleep(2)
    time.sleep(2)

output = {
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "source": "The Racing API",
    "runners": all_runners,
}
Path("data").mkdir(exist_ok=True)
Path("data/racecards.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
print(f"Saved {len(all_runners)} UK/Ireland racecard runners with race metadata")
