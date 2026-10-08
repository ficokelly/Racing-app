
import os
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

USERNAME = os.environ["RACING_API_USERNAME"]
PASSWORD = os.environ["RACING_API_PASSWORD"]
BASE = "https://api.theracingapi.com/v1/racecards/free"

credentials = base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()
).decode()

all_runners = []

for day in ("today", "tomorrow"):
    skip = 0

    while True:
        query = urllib.parse.urlencode({
            "day": day,
            "limit": 500,
            "skip": skip
        })

        request = urllib.request.Request(
            BASE + "?" + query,
            headers={
                "Authorization": "Basic " + credentials,
                "Accept": "application/json"
            }
        )

        with urllib.request.urlopen(request, timeout=40) as response:
            data = json.load(response)

        racecards = data.get("racecards", [])

        for race in racecards:
            if race.get("region") not in ("GB", "IRE"):
                continue

            for runner in race.get("runners", []):
                all_runners.append({
                    "date": race.get("date"),
                    "course": race.get("course"),
                    "race_time": race.get("off_dt"),
                    "horse": runner.get("horse"),
                    "horse_id": runner.get("horse_id"),
                    "trainer": runner.get("trainer"),
                    "trainer_id": runner.get("trainer_id"),
                    "jockey": runner.get("jockey"),
                    "jockey_id": runner.get("jockey_id")
                })

        if len(racecards) < 500:
            break

        skip += 500

output = {
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "source": "The Racing API",
    "runners": all_runners
}

Path("data").mkdir(exist_ok=True)
Path("data/racecards.json").write_text(
    json.dumps(output, indent=2),
    encoding="utf-8"
)

print(f"Saved {len(all_runners)} UK/Ireland racecard runners")
