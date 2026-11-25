"""Dated evidence for duplicate declarations and authoritative final person identities."""
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import json

DECLARATION_SOURCE='https://www.sportinglife.com/racing/racecards/2026-11-19/thurles/live-show/949080/irish-stallion-farms-ebf-beginners-chase'
RESULT_SOURCE='https://www.racingpost.com/results/198/thurles/2026-11-19/933391/'
# 12:05 declaration is not a second race; 12:08 final contains eight of the fourteen.
FINISH={'doctorsteinberg':1,'fortdino':2,'courtcompliance':3,'walksinjune':4,'franciscanrock':5,'farnahoerebel':6,'firecoral':7,'stedebonnet':'NONFINISH'}
DECLARED=set(FINISH)|{'countyfinal','hecantdance','notimetowait','portauthority','timesareachanging','yeshil'}

def thurles_duplicate(day, records, known):
    if day!='2026-11-19': return set()
    d=[(c,h,r) for c,h,r in records if c=='thurles' and str(r)=='949080']
    if len(d)!=len(DECLARED) or {h for c,h,r in d}!=DECLARED:return set()
    # Every final starter and placing must be present in exactly one other source race.
    final=[known.get(('thurles',h)) for h in FINISH]
    if any(x is None for x in final):return set()
    ids={str(x['race']) for x in final}
    if len(ids)!=1 or '949080' in ids:return set()
    rid=next(iter(ids))
    actual={h:x['outcome'] for (c,h),x in known.items() if c=='thurles' and str(x['race'])==rid}
    if actual!=FINISH:return set()
    if any(('thurles',h) in known for h in DECLARED-set(FINISH)):return set()
    return set(d)

@lru_cache(maxsize=1)
def evidence():
    p=Path('data/form-dated-evidence.json')
    return json.loads(p.read_text()) if p.exists() else {}

def verified_final_person(race, runner, kind, F):
    # A URL or source label alone is insufficient. Require exact matching archived
    # final race and independently reviewed horse, placing, trainer and rider.
    if kind not in ('trainer','jockey') or race.get('status')!='final':return False
    if race.get('source')!='Stage V5 independently published result':return False
    meta=race.get('external_verification') or {}
    if not meta.get('source_url') or not meta.get('source_race_id'):return False
    key=lambda r:(str(r.get('date') or '')[:10], F.course_key(r.get('course')), F.off_key(r.get('off_dt') or r.get('race_time') or r.get('off')))
    def signature(rows):
        return sorted((F.horse_key(h.get('horse')),str(h.get('position') or '').upper(),F.person_key(h.get('trainer')),F.person_key(h.get('jockey'))) for h in rows if F.is_starter(h))
    for original in evidence().get('final_results',[]):
        if key(original)!=key(race) or original.get('external_verification')!=meta:continue
        a,b=signature(original.get('runners',[])),signature(race.get('runners',[]))
        if a!=b or len(a)!=meta.get('starters') or len({x[0] for x in a})!=len(a):return False
        entry=(F.horse_key(runner.get('horse')),str(runner.get('position') or '').upper(),F.person_key(runner.get('trainer')),F.person_key(runner.get('jockey')))
        return bool(runner.get(kind)) and entry in a
    return False
