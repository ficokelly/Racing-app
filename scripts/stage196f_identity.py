"""Resolve scoring identities without changing declarations or frozen model artifacts."""
from collections import defaultdict
from datetime import date, timedelta
from functools import cache
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

ROLES = {"trainer": "trn", "jockey": "jky"}
TITLE = re.compile(r"^(?:(?:mr|mrs|miss|ms|dr|sir|lady|dame)(?:\.\s*|\s+))+", re.I)
CLAIM = re.compile(r"(?:\s*\((?:[1-9]|1[0-4])\s*(?:lb|lbs)?\)|\s+(?:[1-9]|1[0-4])\s*lbs?)$", re.I)
MULLINS_ID = "trn_13275"
MULLINS_NAMES = ("W. P. Mullins", "Mr W. P. Mullins")


def frozen_key(name):
    ascii_name = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


def clean(role, name):
    name = str(name or "").strip()
    if role == "jockey":
        name = CLAIM.sub("", name)
    return TITLE.sub("", name).strip()


def person_id(role, value):
    value = str(value or "").strip()
    return value if re.fullmatch(ROLES[role] + r"_\d+", value) else ""


def archive_people(directory, target):
    """Prior final provider results supply identity evidence only."""
    for path in sorted(directory.glob("????-??-??.json")):
        if path.stem >= target:
            continue
        for race in json.loads(path.read_text()).get("results", []):
            if race.get("status") != "final":
                continue
            yield from race.get("runners", [])


class IdentityPlan:
    def __init__(self, role_map):
        self.roles = role_map

    @classmethod
    def build(cls, active, archived, registry):
        names = {role: defaultdict(set) for role in ROLES}
        explicit = {role: [] for role in ROLES}

        def observe(row):
            for role in ROLES:
                name = row.get(role)
                ident = person_id(role, row.get(role + "_id"))
                if ident and frozen_key(clean(role, name)):
                    names[role][ident].add(str(name))

        for row in active:
            observe(row)
        for row in archived:
            observe(row)
        names["trainer"][MULLINS_ID].update(MULLINS_NAMES)
        if registry["schema"] != "form-person-aliases-v1":
            raise ValueError("Unexpected verified person alias registry")
        for entry in registry["entries"]:
            if not entry["evidence_url"].startswith("https://"):
                raise ValueError("Person alias without verified evidence")
            explicit[entry["kind"]].append((entry["alias"], entry["canonical"]))

        result = {}
        for role, observations in names.items():
            links = defaultdict(set)
            evidence = defaultdict(set)
            preferred = set()

            def connect(first, second):
                links[first].add(second)
                links[second].add(first)

            for ident, variants in observations.items():
                for name in variants:
                    key = frozen_key(clean(role, name))
                    connect(ident, key)
                    evidence[key].add(name)
            for alias, canonical in explicit[role]:
                ka = frozen_key(clean(role, alias))
                kc = frozen_key(clean(role, canonical))
                if not ka or not kc:
                    raise ValueError("Empty verified alias key")
                connect(ka, kc)
                evidence[ka].add(alias)
                evidence[kc].add(canonical)
                preferred.add(clean(role, canonical))

            by_id, by_cosmetic, conflicts = {}, {}, {}
            unseen = set(links)
            while unseen:
                seed = min(unseen)
                stack, component = [seed], set()
                while stack:
                    node = stack.pop()
                    if node not in component:
                        component.add(node)
                        stack.extend(links[node] - component)
                unseen -= component
                ids = sorted(node for node in component if node in observations)
                keys = component - set(ids)
                if len(ids) > 1:
                    # Frozen models use names as keys. Separate verified IDs and leave
                    # the historical name-only observations unassigned in this component.
                    conflicts.update({key: ids for key in keys})
                    by_id.update({ident: "RHP verified " + ident for ident in ids})
                    continue
                if not ids:
                    continue
                candidates = {clean(role, value) for key in keys for value in evidence[key]}
                if role == "trainer" and ids[0] == MULLINS_ID:
                    canonical = MULLINS_NAMES[0]
                else:
                    canonical = min(candidates, key=lambda value: (
                        value not in preferred, -len(value.split()), -len(value), value.casefold(), value,
                    ))
                by_id[ids[0]] = canonical
                by_cosmetic.update({key: canonical for key in keys})
            result[role] = {"ids": by_id, "names": by_cosmetic, "conflicts": conflicts}
        return cls(result)

    def canonical(self, role, name, ident=""):
        view = self.roles[role]
        ident = person_id(role, ident)
        if ident:
            return view["ids"].get(ident, name)
        return view["names"].get(frozen_key(clean(role, name)), name)

    def scoring_row(self, row):
        return {**row, **{
            role: self.canonical(role, row.get(role), row.get(role + "_id"))
            for role in ROLES
        }}

    def apply_history(self, path):
        counts = {}
        with sqlite3.connect(path) as con:
            columns = {row[1] for row in con.execute("pragma table_info(historical_runs)")}
            for role in ROLES:
                id_column = next((c for c in (role + "_id", role + "_source_id") if c in columns), None)
                source = id_column or "''"
                @cache
                def scoring_name(name, ident):
                    return self.canonical(role, name, ident)

                con.create_function("scoring_name", 2, scoring_name, deterministic=True)
                counts[role] = con.execute(
                    f"update historical_runs set {role}=scoring_name({role},{source}) "
                    f"where {role} is not scoring_name({role},{source})"
                ).rowcount
        return counts

    def apply_hpr(self, path, target):
        changes = 0
        with sqlite3.connect(path) as con:
            cursor = con.execute("select rowid, payload from hpr_ratings where race_date < ?", (target,))
            while batch := cursor.fetchmany(1000):
                updates = []
                for key, payload in batch:
                    row = json.loads(payload)
                    clean_row = self.scoring_row(row)
                    if any(clean_row.get(role) != row.get(role) for role in ROLES):
                        updates.append((json.dumps({**row, **{r: clean_row[r] for r in ROLES}},
                                                   ensure_ascii=False, separators=(",", ":")), key))
                con.executemany("update hpr_ratings set payload=? where rowid=?", updates)
                changes += len(updates)
        return changes

    def apply_g_state(self, g, state):
        # Old fitted form states store a deque per name and omit source IDs.
        mappings = {}
        for role in ROLES:
            buckets = getattr(state, role + "s")
            mapping = {}
            variants = defaultdict(set)
            # Frozen keys no longer retain punctuation or spacing. Build exact
            # candidate spellings from the verified role mapping and reject collisions.
            for key, canonical in self.roles[role]["names"].items():
                spellings = [key, *[title + key for title in
                             ("mr", "mrs", "miss", "ms", "dr", "sir", "lady", "dame")]]
                if role == "jockey":
                    spellings += [word + suffix for word in spellings for claim in range(1, 15)
                                  for suffix in (str(claim), f"{claim}lb", f"{claim}lbs")]
                for old in spellings:
                    variants[old].add(g.norm(canonical))
            for old, candidates in variants.items():
                if len(candidates) == 1 and old not in self.roles[role]["conflicts"]:
                    (target,) = candidates
                    source_key = g.norm(old)
                    if source_key != target:
                        mapping[source_key] = target

            grouped = defaultdict(list)
            for old, canonical in mapping.items():
                if old in buckets:
                    grouped[canonical].extend(buckets.pop(old))
            for canonical, additions in grouped.items():
                bucket = buckets[canonical]
                # Deques can share event references with the horse buckets.
                ordered = sorted({id(event): event for event in [*bucket, *additions]}.values(),
                                 key=lambda event: event["date"])
                bucket.clear()
                bucket.extend(ordered)
            mappings[role] = mapping

        for bucket in state.horses.values():
            for event in bucket:
                for role, mapping in mappings.items():
                    if event.get(role) in mapping:
                        event[role] = mapping[event[role]]


def stage208_events(g, shared_db, reference, day, declarations):
    """Return verified missing-form candidates, using the original Stage 208 gates."""
    from stage112_form_coverage_reconcile import valid_run
    from scripts.stage203_identity_recovery import exact, expected

    by_name = defaultdict(list)
    for row in declarations:
        by_name[g.norm(row["horse"])].append(row)
    if not by_name:
        return {}, []
    previous_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    references, _ = g.load_reference(reference, first="2022-01-01", last=previous_day)
    events, quarantine, fields, seen = defaultdict(list), [], {}, set()
    query = """
        SELECT r.id AS race_id, r.race_date AS date, r.course, r.race_type,
               r.surface, r.distance_f, r.going, r.race_class, u.runner_key,
               u.horse_name AS horse, u.horse_source_id, u.sire, u.dam,
               u.trainer, u.jockey, u.age, u.sex, u.official_rating, u.weight_lbs,
               u.draw, u.finish_pos, u.outcome, u.beaten, u.race_comment,
               u.rpr, u.topspeed, u.sp_decimal
        FROM historical_runs u JOIN historical_races r ON r.id = u.race_id
        WHERE r.race_date >= '2022-01-01' AND r.race_date < ? AND wanted(u.horse_name)
        ORDER BY r.race_date, r.id, u.runner_key
    """
    with sqlite3.connect(f"{Path(shared_db).resolve().as_uri()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.create_function("wanted", 1, lambda name: int(g.norm(name) in by_name), deterministic=True)
        for raw in con.execute(query, (day,)):
            row = dict(raw)
            d, rid = row["date"], row["race_id"]
            key = g.identity(row)
            found = [card for card in by_name[g.norm(row["horse"])]
                     if key and key == g.identity(card)
                     and exact(card, {**row, "horse_id": row["horse_source_id"]})]
            if len(found) != 1 or not valid_run(row):
                continue
            if (key, d, rid) in seen:
                raise ValueError(f"Duplicate verified historical event for {key} on {d} in {rid}")
            seen.add((key, d, rid))
            meta = references.get(rid)
            reason = ""
            if not meta or meta[0] != d or g.course_key(meta[1]) != g.course_key(row["course"]) or meta[4] not in ("GB", "IRELAND"):
                reason = "UNVERIFIED_REFERENCE"
            else:
                if rid not in fields:
                    starters = [dict(item) for item in con.execute(
                        """select finish_pos, outcome, horse_name, horse_source_id, sire, dam
                           from historical_runs where race_id=?""", (rid,)) if valid_run(dict(item))]
                    identities = [g.identity(item) for item in starters]
                    positions = sorted(int(p) for item in starters
                                       if (p := g.num(item["finish_pos"])) is not None and p >= 1)
                    fields[rid] = (len(starters) if len(starters) >= 2 and all(identities)
                                   and len(set(identities)) == len(starters) and positions
                                   and positions == list(range(1, len(positions) + 1)) else 0)
                if not fields[rid]:
                    reason = "FIELD_SANITY_FAILED"
            if reason:
                quarantine.append({"horse": row["horse"], "date": d, "race_id": rid, "reason": reason})
                continue
            enriched = {**row, "race_name": meta[3], "race_type": meta[2] or row["race_type"],
                        "course": meta[1], "region_scope": meta[4]}
            events[key].append((enriched, fields[rid]))
    return {g.identity(r): {"expected": expected(r, day), "events": events.get(g.identity(r), [])}
            for r in declarations if g.identity(r)}, quarantine


def apply_stage208(g, state, plan):
    inserted = []
    for key, entry in plan.items():
        old = list(state.horses.get(key, ()))
        dates = {event["date"].isoformat() for event in old}
        missing_last_run = entry["expected"] and entry["expected"] not in dates
        if old and not missing_last_run:
            continue
        by_day = defaultdict(list)
        for row, field_size in entry["events"]:
            if row["date"] not in dates:
                by_day[row["date"]].append((row, field_size))
        additions = []
        limit = state.horses[key].maxlen
        for day, rows in sorted(by_day.items(), reverse=True):
            if len(old) + len(additions) >= limit:
                break
            if len(rows) != 1:
                continue
            row, size = rows[0]
            scratch = g.FormState()
            scratch.add(row, g.day(day), size)
            (event,) = scratch.horses[key]
            additions.append(event)
            inserted.append({"identity": key, "date": day, "race_id": row["race_id"],
                             "horse": row["horse"], "source": "VERIFIED_SHARED_HORSE_ONLY"})
        if additions:
            bucket = state.horses[key]
            bucket.clear()
            bucket.extend(sorted([*old, *additions], key=lambda event: event["date"]))
            if len(bucket) != len(old) + len(additions):
                raise RuntimeError("Stage 208 would evict original G history")
    return inserted


def prepare_g_state(g, state, cards):
    """Run once per in-memory scorer state, after the original HPR update."""
    if getattr(state, "_stage196f_prepared", False):
        return
    root = Path(os.environ["STAGE196F_SCORING_PLAN"])
    plan = json.loads(root.read_text())
    IdentityPlan(plan["people"]).apply_g_state(g, state)
    if plan["stage208"] and os.environ.get("STAGE196F_FAMILY") == "G2_G3":
        keys = {g.identity(row) for row in g.flat_cards(cards)}
        selected = {key: value for key, value in plan["stage208"].items() if key in keys}
        inserted = apply_stage208(g, state, selected)
        path = Path(os.environ["STAGE196F_OVERLAY_AUDIT"])
        path.write_text(json.dumps(inserted, indent=2) + "\n")
    state._stage196f_prepared = True
