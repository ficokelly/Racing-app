
import json
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Racing Hot Pots — Trainer & Jockey Strike Rates
# Calculate every period from 1 to 14 days.

RESULTS_DIR = Path("data/racing-results")
OUTPUT_FILE = Path("data/strike-rates.json")

PERIODS = range(1, 15)
TODAY = datetime.now(ZoneInfo("Europe/Dublin")).date()

NON_RUNNERS = {
    "NR",
    "N/R",
    "NON RUNNER",
    "NON-RUNNER",
    "WD",
    "WITHDRAWN",
}

def valid_runner(position):
    position = str(position or "").strip().upper()
    return bool(position) and position not in NON_RUNNERS


def load_results():
    races = {}
    earliest = TODAY - timedelta(days=13)

    for path in sorted(RESULTS_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as file:
            data = json.load(file)

        for race in data.get("results", []):
            if str(race.get("region", "")).upper() not in (
                "GB", "IRE"
            ):
                continue

            race_id = race.get("race_id")
            race_date = race.get("date")

            if not race_id or not race_date:
                continue

            try:
                race_day = date.fromisoformat(race_date)
            except (ValueError, TypeError):
                continue

            if not earliest <= race_day <= TODAY:
                continue

            # Prevent counting a race twice.
            races[(race_date, race_id)] = race

    return list(races.values())


def calculate_period(races, days):
    cutoff = TODAY - timedelta(days=days - 1)

    trainers = defaultdict(
        lambda: {"name": "", "runs": 0, "wins": 0}
    )

    jockeys = defaultdict(
        lambda: {"name": "", "rides": 0, "wins": 0}
    )

    race_count = 0
    runner_count = 0
    dates_found = set()

    for race in races:
        race_day = date.fromisoformat(race["date"])

        if race_day < cutoff:
            continue

        race_count += 1
        dates_found.add(race["date"])

        for runner in race.get("runners", []):
            position = str(
                runner.get("position", "")
            ).strip().upper()

            if not valid_runner(position):
                continue

            runner_count += 1
            won = position == "1"

            trainer_id = runner.get("trainer_id")
            trainer_name = runner.get("trainer")

            if trainer_id and trainer_name:
                record = trainers[trainer_id]
                record["name"] = trainer_name
                record["runs"] += 1
                record["wins"] += int(won)

            jockey_id = runner.get("jockey_id")
            jockey_name = runner.get("jockey")

            if jockey_id and jockey_name:
                record = jockeys[jockey_id]
                record["name"] = jockey_name
                record["rides"] += 1
                record["wins"] += int(won)

    for record in trainers.values():
        record["strike_rate"] = round(
            100 * record["wins"] / record["runs"], 1
        )

    for record in jockeys.values():
        record["strike_rate"] = round(
            100 * record["wins"] / record["rides"], 1
        )

    return {
        "days": days,
        "races": race_count,
        "runners": runner_count,
        "dates_available": sorted(dates_found),
        "trainers": dict(trainers),
        "jockeys": dict(jockeys),
    }


def main():
    races = load_results()

    output = {
        "as_of": TODAY.isoformat(),
        "source": "The Racing API",
        "max_days": 14,
        "periods": {},
    }

    for days in PERIODS:
        statistics = calculate_period(races, days)
        output["periods"][str(days)] = statistics

        print(
            f"{days:2} days: "
            f"{statistics['races']} races, "
            f"{statistics['runners']} runners, "
            f"{len(statistics['trainers'])} trainers, "
            f"{len(statistics['jockeys'])} jockeys"
        )

    OUTPUT_FILE.parent.mkdir(
        parents=True, exist_ok=True
    )

    OUTPUT_FILE.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print(f"Saved statistics to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
