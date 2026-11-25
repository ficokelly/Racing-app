"""Conservative rolling results coverage: saved cards are a floor, not an external census."""
import datetime as dt
import json
from pathlib import Path
import re


def read(path):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def apply_guard(output, report, root=Path('.')):
    try:
        from scripts.stage32_form_rebuild import horse_key, course_key, off_key
        from scripts.rhp_stage70_data_coverage import is_nr
    except ModuleNotFoundError:
        from stage32_form_rebuild import horse_key, course_key, off_key
        from rhp_stage70_data_coverage import is_nr
    root = Path(root)
    end = dt.date.fromisoformat(output['as_of'])
    start = end - dt.timedelta(days=13)
    prior = read(root/'reports/stage32/form-rebuild-audit.json')
    ledger = prior.get('coverage_guard', {}).get('expected_races', {})
    ledger = {k:v for k,v in ledger.items() if start.isoformat() <= k.split('|')[0] <= end.isoformat()}
    # Keep known cards even when the source archive rolls forward. Only explicit
    # non-runner flags remove a declaration; absence alone is not evidence.
    for p in ('data/racecards-archive.json','data/racecards.json'):
        for r in read(root/p).get('runners', []):
            day = str(r.get('date') or r.get('race_time') or '')[:10]
            co = course_key(r.get('course')); off = off_key(r.get('race_time') or r.get('off_dt'))
            name = horse_key(r.get('horse'))
            if not (start.isoformat() <= day <= end.isoformat() and co and off and name): continue
            key = '|'.join((day,co,off))
            people = ledger.setdefault(key, {})
            people[name] = 'NR' if is_nr(r) else 'declared'
    exceptions = set()
    for e in read(root/'data/rhp-stage72-race-exceptions.json').get('races', []):
        if e.get('source') and e.get('disposition') in ('ABANDONED','UNRUN_DUPLICATE_CARD','VOID','CANCELLED'):
            exceptions.add('|'.join((str(e.get('day')),course_key(e.get('course')),off_key(e.get('time')))))
    byday = {}
    for i in range(14):
        day = (start + dt.timedelta(days=i)).isoformat()
        candidates = []
        for race in read(root/'data/racing-results'/f'{day}.json').get('results', []):
            if race.get('status') != 'final': continue
            if str(race.get('date') or day)[:10] != day: continue
            if str(race.get('region') or '').upper() not in ('','GB','IRE','IE'): continue
            co = course_key(race.get('course')); off = off_key(race.get('off_dt') or race.get('race_time') or race.get('off'))
            names = {horse_key(x.get('horse')) for x in race.get('runners', []) if x.get('horse')}
            if co and off and names: candidates.append((co,off,names))
        expected = {k:v for k,v in ledger.items() if k.startswith(day+'|') and k not in exceptions}
        missing = []; unresolved = []; claimed = set()
        for k, people in expected.items():
            _,co,off = k.split('|')
            active = {n for n,s in people.items() if s != 'NR'}
            if not active: continue
            exact = [j for j,(c,o,n) in enumerate(candidates) if c==co and o==off and n & active]
            # Don't silently map two cards to the same result. Exact slots only;
            # shifted off-times are reviewable rather than guessed.
            if len(exact)!=1 or exact[0] in claimed:
                missing.append(k); continue
            j=exact[0]; claimed.add(j)
            gap=sorted(active-candidates[j][2])
            if gap: unresolved.append({'race':k,'declared_horses_without_result_or_withdrawal':gap})
        byday[day]={'expected_saved_races':len(expected),'final_archive_races':len(candidates),
                    'missing_or_ambiguous_races':missing,'unresolved_declarations':unresolved,
                    'fixture_coverage_unknown':not bool(expected),
                    'unmatched_final_archive_races':len(candidates)-len(claimed)}
    for n,p in output['periods'].items():
        lower=(end-dt.timedelta(days=int(n)-1)).isoformat()
        selected=[v for d,v in byday.items() if d>=lower]
        approved=set(p.get('kaggle_days',[]))
        effective=[v for d,v in byday.items() if d>=lower and d not in approved]
        p['effective_form_coverage']={'api_missing_or_ambiguous_races':sum(len(v['missing_or_ambiguous_races']) for v in effective),'api_unresolved_declarations':sum(len(x['declared_horses_without_result_or_withdrawal']) for v in effective for x in v['unresolved_declarations']),'api_unknown_fixture_days':sum(v['fixture_coverage_unknown'] for v in effective),'approved_kaggle_days':sorted(approved),'note':'Approved Kaggle days replace API aggregates; raw archive gaps remain in coverage_guard. External complete fixture census not established.'}
        missing=sum(len(v['missing_or_ambiguous_races']) for v in selected)
        unresolved=sum(len(x['declared_horses_without_result_or_withdrawal']) for v in selected for x in v['unresolved_declarations'])
        unknown=sum(v['fixture_coverage_unknown'] for v in selected)
        gaps=sum((p.get('identity_gaps') or {}).values())
        p['coverage_complete']=False if missing or unresolved or unknown or gaps or len(p.get('dates_available',[]))<int(n) else None
        p['coverage_note']='Partial or externally unverified UK/IE results; never a certified complete strike rate.'
        p['coverage_guard']={'missing_or_ambiguous_races':missing,'unresolved_declarations':unresolved,'unknown_fixture_days':unknown,'identity_gaps':gaps}
    report['coverage_guard']={'expected_races':ledger,'by_day':byday,'scope':'Saved cards and dated final archive only; no independent complete fixture census. Unknown withdrawals are not counted as starters.'}
    report['status']='AUDITED_PARTIAL_COVERAGE' if output['periods']['14']['coverage_complete'] is False else 'NO_KNOWN_GAPS_EXTERNAL_COVERAGE_UNVERIFIED'
    print('FORM_COVERAGE_GUARD',json.dumps(output['periods']['14']['coverage_guard']))
