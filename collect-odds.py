
import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ["PUNTERSEDGE_API_KEY"]

API_URL = (
    "https://api.puntersedge.online/v1/racing/next-to-go"
    "?country=GB,IE&category=horse&num_races=200"
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

# Diagnostic: log field names only, never values, tokens, or runner details.
if races and isinstance(races[0], dict):
    print("Available race fields:", sorted(races[0].keys()))

# Diagnostic: print only runner field names, not personal data or API values.
for diagnostic_race in races:
    if not isinstance(diagnostic_race, dict):
        continue
    diagnostic_runners = diagnostic_race.get("runners")
    if not isinstance(diagnostic_runners, list):
        continue
    diagnostic_runner = next((item for item in diagnostic_runners if isinstance(item, dict)), None)
    if diagnostic_runner is not None:
        print("Available runner fields:", sorted(diagnostic_runner.keys()))
        break


def race_field(race, *names):
    for name in names:
        value = race.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

# Classification uses only fields in the existing odds API response.
# Do not guess a surface at mixed turf/AW venues or infer flat from missing jump keywords.
import re

AW_ONLY = {
    "dundalk", "chelmsford", "chelmsford city", "wolverhampton",
}
AW_MIXED = {"kempton", "kempton park", "lingfield", "lingfield park", "newcastle", "southwell"}
# Known turf-only courses; mixed Flat/Jumps courses are safe here because
# discipline is established separately using race name / short distance.
TURF_ONLY = {
    "aintree", "ascot", "ayr", "ballinrobe", "bangor", "bangor-on-dee",
    "bath", "beverley", "brighton", "carlisle", "cartmel", "catterick",
    "catterick bridge", "chepstow", "chester", "clonmel", "cork",
    "curragh", "down royal", "downpatrick", "epsom", "epsom downs",
    "exeter", "fairyhouse", "ffos las", "fontwell", "galway", "goodwood",
    "gowran park", "hamilton", "hamilton park", "haydock", "haydock park",
    "hexham", "huntingdon", "kelso", "killarney", "kilbeggan",
    "leicester", "leopardstown", "limerick", "listowel", "ludlow",
    "market rasen", "musselburgh", "naas", "navan", "newbury",
    "newmarket", "nottingham", "perth", "plumpton", "pontefract",
    "punchestown", "redcar", "ripon", "roscommon", "salisbury",
    "sandown", "sandown park", "sedgefield", "sherwood", "sligo",
    "stratford", "stratford-on-avon", "taunton", "thirsk", "thurles",
    "tipperary", "tramore", "uttoxeter", "warwick", "wetheby",
    "wetherby", "wexford", "windsor", "wincanton", "worcester",
    "yarmouth", "york",
}


def classify_race(race):
    name = str(race.get("race_name") or "").lower()
    venue = str(race.get("venue_canonical") or race.get("venue") or "").strip().lower()
    venue = re.sub(r"\s+", " ", venue)
    explicit_type = race_field(race, "race_type", "run_type", "raceType", "discipline", "type")
    explicit_surface = race_field(race, "surface", "track_surface", "trackSurface", "going_surface")
    t = (explicit_type or "").lower()
    surface = (explicit_surface or "").lower()

    # Explicit jump labels take precedence over distance/course guesses.
    jumps = bool(re.search(r"\b(hrd|hurdle|hurdles|chs|chase|chases|steeplechase|nhf|nh flat|national hunt flat|bumper|jumps)\b", name + " " + t))
    if jumps:
        return "jumps", surface if surface in ("turf", "aw") else "turf", "race_name_jump"

    # Flat races at standard sprint/middle distances; jump racing is not run at
    # these short distances. Longer races require explicit flat evidence.
    short_distance = bool(re.search(r"\b(?:[5-7]f|1m(?:[0-6]f)?)\b", name))
    flat = bool(re.search(r"\bflat\b", t + " " + name)) or short_distance
    if not flat:
        return None, None, "ambiguous_discipline"

    if surface in ("aw", "all-weather", "all weather", "synthetic", "tapeta", "polytrack", "fibresand"):
        return "flat", "aw", "explicit_surface"
    if surface in ("turf", "grass"):
        return "flat", "turf", "explicit_surface"
    if venue in AW_ONLY:
        return "flat", "aw", "aw_only_course"
    if venue in TURF_ONLY:
        return "flat", "turf", "turf_only_course"
    # Mixed courses require explicit AW/turf metadata; don't invent it.
    return "flat", None, "ambiguous_surface"


classification_counts = {}
for race in races:
    if isinstance(race, dict) and race.get("country") in ("GB", "IE"):
        typ, surf, reason = classify_race(race)
        label = f"{typ or 'unknown'}/{surf or 'unknown'} ({reason})"
        classification_counts[label] = classification_counts.get(label, 0) + 1
print("Race classification summary:", classification_counts)

collected_at = datetime.now(timezone.utc).isoformat()
records = []

for race in races:
    if not isinstance(race, dict):
        continue

    if race.get("country") not in ("GB", "IE"):
        continue

    if race.get("category") != "horse":
        continue

    classified_type, classified_surface, _ = classify_race(race)

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
                "race_type": classified_type,
                "surface": classified_surface,
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
