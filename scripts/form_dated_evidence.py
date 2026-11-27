from pathlib import Path
import json, collections
from scripts import stage32_form_rebuild as F
REGISTRY=Path('data/form-dated-evidence.json')

def read(p): return json.loads(p.read_text()) if p.exists() else {}
def save(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n');tmp.replace(p)
def racekey(r):
    return (str(r.get('date') or '')[:10],F.course_key(r.get('course')),F.off_key(r.get('off_dt') or r.get('race_time') or r.get('off')))
def verified_dash(day,co,horse,rid,known):
    # A dated meeting is eligible only if every published winner and field size matches a unique Kaggle race.
    ev=read(REGISTRY).get('meetings',{}).get(day+'|'+co)
    if not ev:return None
    groups=collections.defaultdict(dict)
    for (c,h),x in known.items():
        if c==co:groups[x['race']][h]=x
    lookup={}
    for winner,size,nr in ev['races']:
        w=F.horse_key(winner)
        matches=[key for key,rs in groups.items() if w in rs and rs[w]['outcome']==1 and len(rs)==size]
        if len(matches)!=1 or matches[0] in lookup:return None
        lookup[matches[0]]={F.horse_key(h) for h in nr}
    if set(lookup)!=set(groups):return None
    if rid in lookup and horse in lookup[rid] and (co,horse) not in known:return 'externally_verified_nonrunner'
    # Some exports use a separate race ID for the declaration-only copy. A named withdrawal is safe only if
    # unique across the independently verified meeting and not a starter anywhere in it.
    if (co,horse) not in known and sum(horse in names for names in lookup.values())==1:return 'externally_verified_nonrunner'
    return None

def apply():
    ev=read(REGISTRY); assert ev['schema']=='form-dated-evidence-v1'
    p=Path('data/rhp-stage72-race-exceptions.json');doc=read(p);existing=doc.setdefault('races',[])
    keys={(x['day'],F.course_key(x['course']),x['time']) for x in existing}
    for x in ev['abandoned']:
        k=(x['day'],F.course_key(x['course']),x['time'])
        if k not in keys:existing.append(x);keys.add(k)
    save(p,doc)
    ledger=read(F.AUDIT_FILE).get('coverage_guard',{}).get('expected_races',{})
    report=[]
    for r in ev['final_results']:
        day,co,off=racekey(r); k='|'.join((day,co,off)); card=ledger.get(k,{})
        p=Path('data/racing-results')/(day+'.json');doc=read(p); current=doc.get('results',[])
        rows=r['runners']; starters={F.horse_key(x['horse']) for x in rows if F.is_starter(x)}; allhorses={F.horse_key(x['horse']) for x in rows}
        assert len(starters)==r['external_verification']['starters'] and len(allhorses)==len(rows)
        # STAGE213_DEAD_HEAT_GUARD: exact independently verified Bangor dead heat only.
        expected_winners=2 if (day,co,off)==('2026-11-23','bangorondee','13:05') and r.get('external_verification',{}).get('dead_heat') is True and {F.horse_key(x['horse']) for x in rows if F.is_win(x)}=={'digdeep','theywantme'} else 1
        assert sum(F.is_win(x) for x in rows)==expected_winners and all(x.get('trainer') and x.get('jockey') for x in rows if F.is_starter(x))
        same=[x for x in current if racekey(x)==(day,co,off) and x.get('status')!='superseded']
        overlaps=[x for x in current if x.get('status')!='superseded' and F.course_key(x.get('course'))==co and starters & {F.horse_key(z.get('horse')) for z in x.get('runners',[]) if F.is_starter(z)}]
        if same or overlaps:
            report.append({'race':k,'status':'EXISTING_OR_OVERLAPPING_RESULT_PRESERVED'});continue
        # No inferred horse IDs or new race identity. Require exact match to every known starter and no extra active declarations.
        if not card or not starters.issubset(set(card)) or set(h for h,s in card.items() if s!='NR')-allhorses:
            report.append({'race':k,'status':'HELD_CARD_MISMATCH','missing_from_card':sorted(starters-set(card)),'unresolved_card_horses':sorted(set(h for h,s in card.items() if s!='NR')-allhorses)});continue
        # A stale withdrawal flag cannot be used to invent an actual starter. Published final result is authoritative.
        current.append(r);doc['results']=current;doc['date']=day;save(p,doc)
        report.append({'race':k,'status':'ADDED','starters':len(starters)})
    save(Path('reports/stage32/form-dated-recovery.json'),{'results':report,'abandoned_races_verified':len(ev['abandoned']),'note':'Only exact dated result fields are added. Existing archive records are not overwritten; raw Kaggle data not published.'})
    print(json.dumps(report,indent=2))
if __name__=='__main__':apply()
