
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ["PUNTERSEDGE_API_KEY"]

API_URL = (
    "https://api.puntersedge.online/v1/racing/next-to-go"
    "?country=GB,IE&category=horse"
)

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "odds-history.json"
LATEST_FILE = DATA_DIR / "latest-odds.json"

DATA_DIR.mkdir(exist_ok=True)

request = urllib.request.Request(
    API_URL,
    headers={"X-API-Key": API_KEY}
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = json.load(response)
        print("API status:", response.status)
        print(
            "Credits remaining:",
            response.headers.get("X-Credits-Remaining", "unknown")
        )
except urllib.error.HTTPError as error:
    print("API error:", error.code)
    raise

# The tested endpoint returns a list of races.
# Also accept an object containing a races/data list.
if isinstance(raw, list):
    races = raw
elif isinstance(raw, dict):
    races = next(
        (
            value for key, value in raw.items()
            if key in ("races", "data", "results")
            and isinstance(value, list)
        ),
        []
    )
else:
    races = []

collected_at = datetime.now(timezone.utc).isoformat()
records = []

for race in races:
    if not isinstance(race, dict):
        continue

    if race.get("country") not in ("GB", "IE"):
        continue

    if race.get("category") != "horse":
        continue

    for runner in race.get("runners", []):
        if not isinstance(runner, dict):
            continue

        for bookmaker in runner.get("bookmakers", []):
            if not isinstance(bookmaker, dict):
                continue

            price = bookmaker.get("win_price")

            if price is None:
                continue

            records.append({
                "collected_at": collected_at,
                "race_id": race.get("race_id"),
                "course": race.get("venue"),
                "race_time": race.get("start_time"),
                "country": race.get("country"),
                "horse": runner.get("name"),
                "bookmaker": bookmaker.get("key"),
                "decimal_odds": price,
                "opening_odds": bookmaker.get("open_price"),
                "price_fluctuations": bookmaker.get("flucs"),
                "bookmaker_updated_at": bookmaker.get("last_update"),
                "stale": bookmaker.get("stale")
            })

snapshot = {
    "collected_at": collected_at,
    "races_returned": len(races),
    "prices_collected": len(records),
    "prices": records
}

LATEST_FILE.write_text(
    json.dumps(snapshot, indent=2),
    encoding="utf-8"
)

if HISTORY_FILE.exists():
    history = json.loads(
        HISTORY_FILE.read_text(encoding="utf-8")
    )
else:
    history = []

history.append(snapshot)

HISTORY_FILE.write_text(
    json.dumps(history, indent=2),
    encoding="utf-8"
)

print("Races returned:", len(races))
print("Bookmaker prices saved:", len(records))
print("Snapshots in history:", len(history))
print("Collection time:", collected_at)

if not races:
    print("WARNING: No races returned by the API.")
