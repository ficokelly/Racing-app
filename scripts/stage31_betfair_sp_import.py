#!/usr/bin/env python3
"""Stage 31 | Research-only Betfair SP aggregate CSV -> separately audited SQLite.

Requires separately confirmed data rights for remote downloads. Only GB/IE WIN
markets. Betfair's BSP, PPWAP and MORNINGWAP are not minute-specific executable
quotes: the first is determined at the off, and the latter two are aggregates.
No prices are written to the public repo or fed into live strategies.

Python 3.11, standard library only. Run --self-test before any actual import.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import io
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://promo.betfair.com/betfairsp/prices"
FIRST_DATE = dt.date(2008, 5, 28)
FIELDS = ("EVENT_ID", "MENU_HINT", "EVENT_NAME", "EVENT_DT", "SELECTION_ID",
          "SELECTION_NAME", "WIN_LOSE", "BSP", "PPWAP", "MORNINGWAP")
PRICE_FIELDS = ("BSP", "PPWAP", "MORNINGWAP", "PPMAX", "PPMIN",
                "MORNINGTRADEDVOL", "PPTRADEDVOL")
TRACK_ALIASES = {
    "ling": "lingfield", "leopa": "leopardstown", "sedg": "sedgefield",
    "wolv": "wolverhampton", "kempt": "kempton", "newc": "newcastle",
    "chelt": "cheltenham", "asc": "ascot", "sand": "sandown",
    "newm": "newmarket", "catt": "catterick", "cattbridge": "catterick",
    "weth": "wetherby", "warw": "warwick", "worc": "worcester",
    "nav": "navan", "naas": "naas", "curr": "curragh", "cur": "curragh",
    "dun": "dundalk", "downr": "downroyal", "downp": "downpatrick",
    "fairy": "fairyhouse", "punch": "punchestown", "gowa": "gowranpark",
    "york": "york", "good": "goodwood", "donc": "doncaster",
    "chep": "chepstow", "bng": "bangorondee", "bang": "bangorondee",
    "thur": "thurles", "tipp": "tipperary", "cork": "cork",
    "slig": "sligo", "limer": "limerick", "limerick": "limerick",
}


def normalize_horse(value: str) -> str:
    """Mirror Stage 2 historical importer horse_name_norm convention."""
    s = str(value or "")
    s = re.sub(r"\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ)\)\s*$", "", s, flags=re.I)
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", s)
                  .encode("ascii", "ignore").decode().upper())


def normalize_course(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.lower().replace("the ", ""))


def parse_menu(value: str) -> tuple[str, str]:
    """Betfair sample: 'GB / Ling 19th Nov'; abbreviated tracks are normal."""
    s = str(value or "")
    m = re.search(r"\b(GB|UK|IRE|IRL|IE)\s*/\s*([^/]+)", s, re.I)
    if not m:
        return "", ""
    country = "GB" if m.group(1).upper() in ("GB", "UK") else "IE"
    track = re.sub(r"\s+\d{1,2}(?:st|nd|rd|th)?\b.*$", "", m.group(2), flags=re.I)
    track = normalize_course(track)
    return country, TRACK_ALIASES.get(track, track)


def track_agrees(betfair: str, historical: str) -> bool:
    h = normalize_course(historical)
    b = normalize_course(betfair)
    h = TRACK_ALIASES.get(h, h)
    b = TRACK_ALIASES.get(b, b)
    return bool(b and h and (b == h or (len(b) >= 4 and h.startswith(b))
                              or (len(h) >= 4 and b.startswith(h))))


def parse_day_time(value: str) -> dt.date | None:
    value = str(value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
                "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None


def valid_price(value: str, bsp: bool = False) -> float | None:
    try:
        n = float(str(value or "").replace(",", ""))
        if n >= 1.01 and math.isfinite(n) and (not bsp or n < 1001):
            return n
    except (TypeError, ValueError):
        pass
    return None


def valid_volume(value: str) -> float | None:
    try:
        n = float(str(value or "").replace(",", ""))
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None


def open_history(path: str | None) -> sqlite3.Connection | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Historical SQLite not found: {path}")
    conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)
    rows = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"historical_races", "historical_runs"}.issubset(rows):
        raise RuntimeError("Historical SQLite missing Stage 2 racing tables")
    return conn


def history_for_day(conn: sqlite3.Connection | None, day: str) -> dict[str, dict]:
    """One indexed date lookup, not an expensive full-history join per market."""
    if not conn:
        return {}
    query = """
       SELECT race.id, race.course, run.horse_name_norm, run.finish_pos
       FROM historical_races race
       JOIN historical_runs run ON run.race_id = race.id
       WHERE race.race_date = ?
    """
    races: dict[str, dict] = {}
    for rid, course, horse, finishing in conn.execute(query, (day,)):
        r = races.setdefault(rid, {"course": course, "names": set(), "winners": set()})
        nm = normalize_horse(horse)
        if nm:
            r["names"].add(nm)
            if finishing == 1:
                r["winners"].add(nm)
    return races


def choose_match(event: dict, history: dict[str, dict]) -> tuple[str | None, str, float, int]:
    """Conservative joining: multiple overlapping names and nonambiguous runner set.

    Race result is deliberately *not* used to select the winning candidate; it
    is an independent consistency check after runner-based identity matching.
    """
    names = event["names"]
    if not history:
        return None, "history_missing", 0.0, 0
    candidates = []
    for rid, h in history.items():
        overlap = names & h["names"]
        n = len(overlap)
        if n < 2:
            continue
        track_ok = track_agrees(event["track"], h["course"])
        denominator = min(len(names), len(h["names"])) or 1
        common_fraction = n / denominator
        event_fraction = n / max(1, len(names))
        historical_fraction = n / max(1, len(h["names"]))
        if common_fraction < .55 or event_fraction < .55 or historical_fraction < .50:
            continue
        if not track_ok and (event["track"] or common_fraction < .85):
            continue
        confidence = 0.46 * common_fraction + 0.22 * event_fraction + 0.19 * historical_fraction + 0.13 * int(track_ok)
        candidates.append((confidence, n, rid, h))
    candidates.sort(reverse=True)
    if not candidates:
        return None, "no_reliable_match", 0.0, 0
    best = candidates[0]
    if len(candidates) > 1 and best[0] - candidates[1][0] < .11:
        return None, "ambiguous_race", best[0], best[1]
    observed_winners = event["winners"]
    if observed_winners and best[3]["winners"] and not observed_winners.intersection(best[3]["winners"]):
        return None, "winner_mismatch", best[0], best[1]
    if best[0] < .63:
        return None, "low_confidence", best[0], best[1]
    return best[2], "matched", best[0], best[1]


def create_output(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS source_files (
      filename TEXT PRIMARY KEY, day TEXT, region TEXT, status TEXT, rows_count INTEGER,
      note TEXT, collected_utc TEXT);
    CREATE TABLE IF NOT EXISTS betfair_markets (
      market_key TEXT PRIMARY KEY, event_id TEXT, event_dt TEXT, race_day TEXT,
      region TEXT, menu_hint TEXT, event_name TEXT, extracted_track TEXT,
      hist_race_id TEXT, match_status TEXT, confidence REAL, overlap_count INTEGER,
      runner_count INTEGER);
    CREATE TABLE IF NOT EXISTS betfair_prices (
      market_key TEXT NOT NULL, selection_id TEXT NOT NULL,
      horse_name TEXT, horse_norm TEXT, outcome TEXT,
      bsp REAL, preoff_wap REAL, morning_wap REAL, preoff_max REAL, preoff_min REAL,
      morning_traded_vol REAL, preoff_traded_vol REAL,
      PRIMARY KEY(market_key,selection_id),
      FOREIGN KEY(market_key) REFERENCES betfair_markets(market_key));
    CREATE INDEX IF NOT EXISTS betfair_market_day_idx ON betfair_markets(race_day,region);
    CREATE INDEX IF NOT EXISTS betfair_historical_race_idx ON betfair_markets(hist_race_id);
    CREATE INDEX IF NOT EXISTS betfair_price_norm_idx ON betfair_prices(horse_norm);
    CREATE TABLE IF NOT EXISTS import_metadata (key TEXT PRIMARY KEY,value TEXT);
    """)


def filenames_for(day: dt.date, region: str) -> tuple[str, ...]:
    d = day.strftime("%d%m%Y")
    # The current directory listing uses ukwin/irewin, older files sometimes
    # use just uk/ire; try only the two known naming conventions.
    return (f"dwbfprices{region}win{d}.csv", f"dwbfprices{region}{d}.csv")


def fetch_day_file(day: dt.date, region: str, timeout: int = 40) -> tuple[str, bytes | None, str]:
    for filename in filenames_for(day, region):
        url = f"{BASE_URL}/{filename}"
        request = urllib.request.Request(url, headers={
            "User-Agent": "RacingHotPots-Stage31-PersonalResearch/1.0",
            "Accept": "text/csv,text/plain,*/*",
        })
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = response.read(3_000_000 + 1)
                    if len(data) > 3_000_000:
                        return filename, None, "file_exceeds_3mb_limit"
                    if not data.strip():
                        return filename, b"", "empty_file"
                    # Reject HTML interstitial / access denied messages.
                    header = data[:256].lower()
                    if b"<html" in header or b"<!doctype" in header:
                        return filename, None, "unexpected_html"
                    return filename, data, "downloaded"
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    break
                if e.code in (401, 403, 429):
                    raise RuntimeError(f"Betfair HTTP {e.code}; stop rather than bypass access restrictions") from e
                if attempt == 2:
                    return filename, None, f"http_{e.code}"
            except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
                if attempt == 2:
                    return filename, None, f"network_{type(e).__name__}"
            time.sleep(0.6 * (attempt + 1))
    return filenames_for(day, region)[0], None, "not_found"


def read_csv_bytes(data: bytes, filename: str, expected_day: dt.date, region: str) -> tuple[list[dict], str]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace")))
    cols = {str(x or "").upper().strip() for x in reader.fieldnames or []}
    missing = sorted(set(FIELDS) - cols)
    if missing:
        return [], f"missing_csv_fields:{','.join(missing)}"
    out = []
    for entry in reader:
        row = {str(k).upper().strip(): str(v or "").strip() for k, v in entry.items() if k is not None}
        if not row.get("SELECTION_NAME") or not row.get("EVENT_ID"):
            continue
        event_day = parse_day_time(row.get("EVENT_DT", ""))
        if event_day != expected_day:
            # Betfair file can be dated the day after an overnight meeting.
            # Preserve true race date for linking, but never import dates with
            # a huge discrepancy; unmatched rows need manual investigation.
            if not event_day or abs((event_day - expected_day).days) > 1:
                continue
        hint_region, _ = parse_menu(row.get("MENU_HINT", ""))
        if hint_region and hint_region != ("GB" if region == "uk" else "IE"):
            continue
        row["_RACE_DAY"] = event_day.isoformat()
        row["_REGION"] = "GB" if region == "uk" else "IE"
        row["_FILENAME"] = filename
        out.append(row)
    return out, "parsed"


def store_day(conn: sqlite3.Connection, hist: sqlite3.Connection | None,
              rows: list[dict], report: collections.Counter) -> None:
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        identity = (row["_REGION"], row["_RACE_DAY"], row["EVENT_ID"], row["EVENT_DT"], row.get("EVENT_NAME", ""))
        key = "|".join(identity)
        grouped[key].append(row)
    history_cache: dict[str, dict] = {}
    for key, entries in grouped.items():
        first = entries[0]
        race_day = first["_RACE_DAY"]
        if race_day not in history_cache:
            history_cache[race_day] = history_for_day(hist, race_day)
        country, extracted_track = parse_menu(first.get("MENU_HINT", ""))
        names = {normalize_horse(e["SELECTION_NAME"]) for e in entries} - {""}
        winners = {normalize_horse(e["SELECTION_NAME"]) for e in entries
                   if e.get("WIN_LOSE", "") in ("1", "2")}
        event = {"names": names, "winners": winners, "track": extracted_track}
        rid, status, confidence, overlap = choose_match(event, history_cache[race_day])
        # No track in source? Require unusually strong overlap to trust it.
        if rid and not extracted_track and confidence < .82:
            rid, status = None, "track_missing"
        report[status] += 1
        conn.execute("""INSERT OR REPLACE INTO betfair_markets VALUES
             (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (key, first["EVENT_ID"], first["EVENT_DT"], race_day,
             first["_REGION"], first.get("MENU_HINT", ""), first.get("EVENT_NAME", ""),
             extracted_track, rid, status, round(confidence, 5), overlap, len(names)))
        for e in entries:
            sid = e.get("SELECTION_ID", "") or normalize_horse(e["SELECTION_NAME"])
            conn.execute("""INSERT OR REPLACE INTO betfair_prices VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)""", (key, sid, e["SELECTION_NAME"],
                normalize_horse(e["SELECTION_NAME"]), e.get("WIN_LOSE", ""),
                valid_price(e.get("BSP", ""), bsp=True), valid_price(e.get("PPWAP", "")),
                valid_price(e.get("MORNINGWAP", "")), valid_price(e.get("PPMAX", "")),
                valid_price(e.get("PPMIN", "")), valid_volume(e.get("MORNINGTRADEDVOL", "")),
                valid_volume(e.get("PPTRADEDVOL", ""))))
            report["runner_rows"] += 1
            if rid:
                report["linked_runner_rows"] += 1
            if valid_price(e.get("BSP", ""), bsp=True):
                report["bsp_rows"] += 1
            if valid_price(e.get("PPWAP", "")):
                report["preoff_wap_rows"] += 1
            if valid_price(e.get("MORNINGWAP", "")):
                report["morning_wap_rows"] += 1


def one_day(day: dt.date, region: str, input_dir: Path | None,
            online: bool) -> tuple[str, bytes | None, str]:
    if input_dir:
        for filename in filenames_for(day, region):
            p = input_dir / filename
            if p.is_file():
                data = p.read_bytes()
                return filename, data, "local" if data else "empty_file"
        return filenames_for(day, region)[0], None, "not_found_local"
    if online:
        return fetch_day_file(day, region)
    raise RuntimeError("Supply --input-dir or --download --rights-confirmed")


def calendar(start: dt.date, end: dt.date):
    current = max(FIRST_DATE, start)
    end = min(end, dt.date.today() - dt.timedelta(days=1))
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def run(args) -> dict:
    if args.download and not args.rights_confirmed:
        raise SystemExit("Remote downloading disabled: confirm your Betfair data rights and supply --rights-confirmed")
    if args.download and args.input_dir:
        raise SystemExit("Use either --download or --input-dir, not both")
    if not args.download and not args.input_dir:
        raise SystemExit("Supply a directory of authorized Betfair CSVs or --download --rights-confirmed")
    if not args.history:
        raise SystemExit("Historical race database is required to produce audited runner matches")
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if end < start or start.year < 2008:
        raise SystemExit("Invalid date range; archive begins 2008-05-28")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    hist = open_history(args.history)
    dest = output / "stage31-betfair-prices.sqlite"
    con = sqlite3.connect(dest)
    create_output(con)
    con.execute("INSERT OR REPLACE INTO import_metadata VALUES (?,?)",
                ("source_url", "https://promo.betfair.com/betfairsp/prices"))
    con.execute("INSERT OR REPLACE INTO import_metadata VALUES (?,?)",
                ("permissions", "User-confirmed personal/internal use only; do not publish licensed price data"))
    con.execute("INSERT OR REPLACE INTO import_metadata VALUES (?,?)",
                ("price_semantics", "BSP=at off; PPWAP=preoff aggregate; MORNINGWAP=before 11AM GMT aggregate"))
    con.commit()
    report = collections.Counter()
    errors = []
    day_count = 0
    file_count = 0
    for day in calendar(start, end):
        day_count += 1
        if args.max_days and day_count > args.max_days:
            break
        day_files = []
        for region in ("uk", "ire"):
            filename, data, status = one_day(day, region, Path(args.input_dir) if args.input_dir else None, args.download)
            file_count += 1
            rows = []
            if data is not None and data:
                rows, parse_status = read_csv_bytes(data, filename, day, region)
                status = parse_status if parse_status != "parsed" else status
                if status.startswith("missing_csv_fields"):
                    errors.append({"date": day.isoformat(), "file": filename, "error": status})
                if rows:
                    day_files.extend(rows)
            report["files_" + status.split(":")[0]] += 1
            con.execute("INSERT OR REPLACE INTO source_files VALUES (?,?,?,?,?,?,?)",
                        (filename, day.isoformat(), region, status, len(rows), "",
                         dt.datetime.now(dt.timezone.utc).isoformat()))
            if args.download:
                time.sleep(args.delay)
        if day_files:
            store_day(con, hist, day_files, report)
        con.commit()
        if day_count % 30 == 0:
            print(f"Processed {day_count} days / {file_count} source files / "
                  f"{report['runner_rows']} runner prices / {report['matched']} matched races", flush=True)
    summary = {
        "status": "RESEARCH_ONLY",
        "models_changed": False,
        "data_published_to_public_repo": False,
        "source": "Betfair historical SP daily aggregate CSV files",
        "source_url": "https://promo.betfair.com/betfairsp/prices",
        "requested_start": args.start,
        "requested_end": args.end,
        "earliest_supported": FIRST_DATE.isoformat(),
        "days_checked": min(day_count, args.max_days) if args.max_days else day_count,
        "source_files_checked": file_count,
        "counts": dict(sorted(report.items())),
        "warnings": [
            "BSP is at-off settlement, NOT a pre-race bettable price.",
            "PPWAP and MORNINGWAP are aggregates, NOT prices available at specific minutes.",
            "Data is subject to Betfair use and redistribution restrictions; store privately.",
            "No matching decisions use the race's winning outcome; winners only validate matching consistency.",
            "Unmatched or ambiguous races are NOT forced into the historical database.",
            "No 5/10/30-minute quote or executable backtest may be inferred from these files.",
        ],
        "file_errors": errors[:250],
        "errors_omitted": max(0, len(errors) - 250),
    }
    (output / "stage31-summary.json").write_text(json.dumps(summary, indent=2))
    with (output / "stage31-coverage.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("day", "region", "filename", "status", "rows"))
        writer.writerows(con.execute("SELECT day, region, filename, status, rows_count FROM source_files ORDER BY day,region"))
    con.close()
    hist.close()
    print(json.dumps({"days": summary["days_checked"], "files": file_count,
                      "matched_races": report["matched"], "runner_rows": report["runner_rows"],
                      "output": str(dest)}, indent=2), flush=True)
    return summary



def protect_private_archive(action: str, source: str, target: str) -> None:
    """Authenticated private archival. Never place plaintext price DB in Actions artifacts.

    Requires optional `cryptography` package and STAGE31_ARCHIVE_KEY secret.
    File format: b'STAGE31\x00' + 16-byte salt + 12-byte nonce + AES-GCM payload.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    password = os.environ.get("STAGE31_ARCHIVE_KEY", "")
    if len(password) < 24:
        raise SystemExit("STAGE31_ARCHIVE_KEY must be set to a strong secret (24+ characters)")
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.is_file():
        raise SystemExit("Archive source not found: " + source)
    magic = b"STAGE31\x00"
    aad = b"RacingHotPots Betfair research private archive v1"
    if action == "encrypt":
        salt, nonce = os.urandom(16), os.urandom(12)
        key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode())
        payload = AESGCM(key).encrypt(nonce, source_path.read_bytes(), aad)
        target_path.write_bytes(magic + salt + nonce + payload)
        print("Encrypted private Stage 31 archive:", target_path.name, "bytes", target_path.stat().st_size)
    elif action == "decrypt":
        data = source_path.read_bytes()
        if not data.startswith(magic):
            raise SystemExit("Not a Stage 31 encrypted archive")
        prefix = len(magic)
        salt, nonce, payload = data[prefix:prefix+16], data[prefix+16:prefix+28], data[prefix+28:]
        key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode())
        target_path.write_bytes(AESGCM(key).decrypt(nonce, payload, aad))
        print("Decrypted Stage 31 private archive:", target_path)
    else:
        raise ValueError(action)


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="stage31-") as root:
        root = Path(root)
        historic = root / "history.sqlite"
        c = sqlite3.connect(historic)
        c.executescript("""
        CREATE TABLE historical_races (id TEXT PRIMARY KEY,race_date TEXT,course TEXT);
        CREATE TABLE historical_runs (race_id TEXT,horse_name_norm TEXT,finish_pos INTEGER);
        CREATE INDEX hist_date_idx ON historical_races(race_date);
        CREATE INDEX hist_run_race_idx ON historical_runs(race_id);
        """)
        c.executemany("INSERT INTO historical_races VALUES (?,?,?)", [
            ("r1", "2025-08-01", "Lingfield"), ("r2", "2025-08-01", "Lingfield")])
        c.executemany("INSERT INTO historical_runs VALUES (?,?,?)", [
            ("r1", "FLYINGSTAR", 1), ("r1", "BOLDHERO", 2), ("r1", "CAREFULLAD", 3),
            ("r2", "SEAFLAME", 2), ("r2", "LIGHTNINGBOLT", 1), ("r2", "SILVERWING", 3)])
        c.commit()
        c.close()
        p = root / "dwbfpricesukwin01082025.csv"
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(FIELDS) + ["PPMAX","PPMIN","MORNINGTRADEDVOL","PPTRADEDVOL"])
            w.writeheader()
            for index, name in enumerate(("Flying Star", "Bold Hero", "Careful Lad"), start=1):
                w.writerow({"EVENT_ID":"789", "MENU_HINT":"GB / Ling 1st Aug", "EVENT_NAME":"6f Hcap",
                            "EVENT_DT":"01/08/2025 15:10", "SELECTION_ID":index,"SELECTION_NAME":name,
                            "WIN_LOSE":int(index == 1), "BSP": "1001" if index == 3 else "3.4",
                            "PPWAP":"3.8", "MORNINGWAP":"4.5", "PPTRADEDVOL":"1234"})
        args = argparse.Namespace(download=False, input_dir=str(root), rights_confirmed=False,
                                  history=str(historic), start="2025-08-01", end="2025-08-01",
                                  max_days=0, output=str(root / "output"), delay=0.0)
        s = run(args)
        assert s["counts"]["matched"] == 1, s
        assert s["counts"]["linked_runner_rows"] == 3, s
        assert s["counts"]["bsp_rows"] == 2, s
        o = sqlite3.connect(root / "output" / "stage31-betfair-prices.sqlite")
        assert o.execute("SELECT hist_race_id FROM betfair_markets").fetchone()[0] == "r1"
        assert o.execute("SELECT COUNT(*) FROM betfair_prices WHERE bsp IS NULL").fetchone()[0] == 1
        o.close()
        assert normalize_horse("Flying Star (IRE)") == "FLYINGSTAR"
        assert parse_menu("GB / Ling 1st Aug") == ("GB", "lingfield")
        assert parse_day_time("01/08/2025 15:10") == dt.date(2025, 8, 1)
        print("Stage 31 self-test PASS: parser, BSP 1001 sentinel, matching, outputs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--history", help="Existing Stage 2 historical_ukie_staging.sqlite")
    parser.add_argument("--input-dir", help="Directory of already-authorized daily Betfair CSVs")
    parser.add_argument("--download", action="store_true", help="Download GB/IRE WIN archives from Betfair")
    parser.add_argument("--rights-confirmed", action="store_true", help="Confirm permitted use/license before network access")
    parser.add_argument("--output", default="stage31_output")
    parser.add_argument("--max-days", type=int, default=0, help="Limit for initial trial; 0 means whole date range")
    parser.add_argument("--delay", type=float, default=0.7, help="Seconds between Betfair HTTP requests")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--encrypt-private", nargs=2, metavar=("SOURCE_SQLITE", "OUTPUT_AESGCM"))
    parser.add_argument("--decrypt-private", nargs=2, metavar=("SOURCE_AESGCM", "OUTPUT_SQLITE"))
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.encrypt_private:
        protect_private_archive("encrypt", *args.encrypt_private)
        return
    if args.decrypt_private:
        protect_private_archive("decrypt", *args.decrypt_private)
        return
    run(args)

if __name__ == "__main__":
    main()
