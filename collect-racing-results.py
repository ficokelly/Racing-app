
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import re

API_URL = "https://api.theracingapi.com/v1/results/today/free"
USERNAME = os.environ["RACING_API_USERNAME"]
PASSWORD = os.environ["RACING_API_PASSWORD"]

credentials = base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()
).decode()

headers = {
    "Authorization": f"Basic {credentials}",
    "Accept": "application/json",
}

results = {}
skip = 0
limit = 100

while True:
    params = urlencode({
        "region": ["gb", "ire"],
        "limit": limit,
        "skip": skip,
    }, doseq=True)

    request = Request(
        f"{API_URL}?{params}",
        headers=headers
    )

    with urlopen(request, timeout=30) as response:
        data = json.load(response)

    races = data.get("results") or []

    for race in races:
        if race.get("region", "").upper() not in ("GB", "IRE"):
            continue

        race_id = race.get("race_id")
        if not race_id:
            continue

        runners = race.get("runners") or []

        if not runners:
            continue

        results[race_id] = race

    skip += len(races)

    if not races or skip >= data.get("total", 0):
        break

    time.sleep(1.1)

if not results:
    raise RuntimeError("No UK or Irish results returned.")

by_date = {}

for race in results.values():
    date = race["date"]
    by_date.setdefault(date, []).append(race)

folder = Path("data/racing-results")
folder.mkdir(parents=True, exist_ok=True)

for date, races in by_date.items():
    path = folder / f"{date}.json"

    existing = {}

    def stable_key(race):
        """Old archived results may have NO race_id. Never crash or discard them.
        Prefer date/course/off slot; fall back to the provider's actual ID.
        A synthetic merge key is never mistaken for a provider race ID.
        """
        course = re.sub(r"[^a-z0-9]+", "", str(race.get("course") or "").lower())
        off = str(race.get("off_dt") or race.get("race_time") or race.get("off") or "")
        day = str(race.get("date") or date)
        # Use the scheduled start to reconcile archived entries with a new API ID.
        if day and course and off:
            return ("slot", day, course, off[11:16] if "T" in off else off)
        if race.get("race_id"):
            return ("id", str(race["race_id"]))
        # Preserve distinct legacy observations even when metadata is incomplete.
        return ("legacy", day, course, off, str(race.get("race_name") or ""))

    if path.exists():
        old_data = json.loads(path.read_text())
        for race in old_data.get("results", []):
            if not isinstance(race, dict):
                continue
            existing[stable_key(race)] = race

    for race in races:
        key = stable_key(race)
        prior = existing.get(key)
        # Preserve the fuller set of runners if the new API response is partial.
        if prior and len(prior.get("runners") or []) > len(race.get("runners") or []):
            merged = dict(prior)
            if race.get("race_id") and not merged.get("race_id"):
                merged["race_id"] = race["race_id"]
            existing[key] = merged
        else:
            existing[key] = race

    output = {
        "date": date,
        "source": "The Racing API",
        "collected_at": datetime.now().astimezone().isoformat(),
        "results": list(existing.values()),
    }

    path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False)
    )

    print(
        f"Saved {len(existing)} races to {path}"
    )
