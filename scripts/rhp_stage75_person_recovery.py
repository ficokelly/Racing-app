#!/usr/bin/env python3
"""Stage75: manually corroborated result-person recovery and exact alias exceptions.

Only fills missing trainer and jockey strings on 11 individually sourced finished
runners; never touches placings, SP, model ranks, prior person values or other
races. Stage32 exceptions are limited to 22 explicit, source-tagged name aliases.
If validation fails, the GitHub workflow will not publish any changes.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parent.parent
STAGE32 = ROOT / 'scripts/stage32_form_rebuild.py'
REPORT = ROOT / 'data/rhp-stage75-identity-evidence.json'

# Human-checked dated post-race results, never pre-race declarations.
# Last integer is the actual, pre-existing archived finishing position.
CONFIRMED = [
    ('2026-10-27','Wolverhampton','19:00','Bessie Wallis',6,'Michael Attwater','Rossa Ryan',
     'https://www.racingpost.com/results/513/wolverhampton-aw/2026-10-27/929609/'),
    ('2026-10-27','Wolverhampton','18:30','Twitch',1,'Craig Benton','Josephine Gordon',
     'https://www.racingpost.com/results/513/wolverhampton-aw/2026-10-27/932094/'),
    ('2026-10-27','Wolverhampton','18:00','Maury',3,'Craig Benton','Georgia Dobie',
     'https://m.attheraces.com/racecard/Wolverhampton/27-October-2026/1800'),
    ('2026-10-27','Bangor-on-Dee','14:46','Masked Dance',6,'Mel Rowley','Charlie Deutsch',
     'https://www.racingpost.com/results/4/bangor-on-dee/2026-10-27/929336/'),
    ('2026-10-27','Newcastle','13:26','The Kings Soldier',3,'P Winks','Harry Reed',
     'https://www.irishracing.com/galway-races/horse/The-Kings-Soldier-IRE/832427'),
    ('2026-11-04','Dundalk','17:45','Allez Magh Glas',7,'M J Tynan','Nathan Crosse',
     'https://www.racingpost.com/results/1138/dundalk-aw/2026-11-04/932370/'),
    ('2026-11-04','Chepstow','16:10','Uncle Cc',2,'Joe Tizzard','Freddie Gingell',
     'https://www.racingpost.com/results/12/chepstow/2026-11-04/930473/'),
    ('2026-11-04','Nottingham','15:23','Al Jolson',4,'Simon Crisford','Harry Davies',
     'https://www.racingpost.com/results/40/nottingham/2026-11-04/930686/'),
    ('2026-11-04','Wexford','14:37','Butty O Brien',9,'Cian Collins','Eoin Walsh',
     'https://www.racingpost.com/results/201/wexford/2026-11-04/932392/'),
    ('2026-11-04','Nottingham','13:20','Wiltshire Wonder',11,'Bernard Llewellyn & Robert Williams','Callum Shepherd',
     'https://www.racingpost.com/results/40/nottingham/2026-11-04/930684/'),
    ('2026-11-04','Musselburgh','12:35','Unlawfilly',5,'Chris Timmons','Shane Gray',
     'https://www.racingpost.com/results/16/musselburgh/2026-11-04/930676/'),
]

# Exactly scoped alias exceptions for two independently corroborated Stage72
# races. Every one is tied to a UNIQUE horse, date, course and off time, plus
# a recorded result person name and a matching existing racecard person ID.
# This DOES NOT imply two trainers/jockeys are equal in other races.
ALIASES = {
 '2026-11-04|dundalk|19:15|robotrock': ('jockey',),
 '2026-11-04|dundalk|19:15|connova': ('trainer',),
 '2026-11-04|dundalk|19:15|dancingsteve': ('trainer','jockey'),
 '2026-11-04|dundalk|19:15|lisnadill': ('trainer','jockey'),
 '2026-11-04|dundalk|19:15|missglenville': ('jockey',),
 '2026-11-04|dundalk|19:15|suerte': ('trainer',),
 '2026-11-04|dundalk|19:15|pettusbridge': ('trainer','jockey'),
 '2026-11-04|dundalk|19:15|starmind': ('trainer','jockey'),
 '2026-11-04|dundalk|19:15|jessabi': ('trainer','jockey'),
 '2026-11-04|dundalk|19:15|onyeisi': ('trainer','jockey'),
 '2026-11-04|dundalk|19:15|thompsongunner': ('trainer','jockey'),
 '2026-11-08|ffoslas|16:12|rulabula': ('trainer',),
 '2026-11-08|ffoslas|16:12|imperialbede': ('trainer',),
 '2026-11-08|ffoslas|16:12|centara': ('trainer','jockey'),
}
assert sum('trainer' in x for x in ALIASES.values()) == 12
assert sum('jockey' in x for x in ALIASES.values()) == 10


def clean(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    return re.sub('[^a-z0-9]', '', ''.join(x for x in value if not unicodedata.combining(x)).casefold())

def horse(value):
    return clean(re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|SAF|ITA|ITY)\)\s*$', '',str(value or ''),flags=re.I))

def course(value):
    return clean(re.sub(r'\s*\((?:AW|ALL WEATHER|IRE|IRL|GB)\)', '',str(value or ''),flags=re.I))

def off(value):
    s = str(value or '')
    try:
        z = dt.datetime.fromisoformat(s.replace('Z','+00:00'))
        if z.tzinfo is None: z=z.replace(tzinfo=dt.timezone.utc)
        return z.astimezone(dt.timezone.utc).strftime('%H:%M')
    except ValueError:
        m=re.search(r'\b\d\d:\d\d\b',s)
        return m.group() if m else ''

def recover_people():
    changed = set()
    writes = []
    proof = []
    for day in sorted(set(x[0] for x in CONFIRMED)):
        path = ROOT / 'data/racing-results' / (day + '.json')
        document = json.loads(path.read_text(encoding='utf-8'))
        for x in [v for v in CONFIRMED if v[0]==day]:
            day0, co, tm, name, position, trainer, jockey, url = x
            races=[r for r in document['results'] if r.get('date')==day0 and course(r.get('course'))==course(co)
                   and off(r.get('off_dt') or r.get('race_time'))==tm]
            if len(races)!=1: raise RuntimeError(f'Unsafe race match for {name}: {len(races)}')
            race=races[0]
            runners=[r for r in race.get('runners',[]) if horse(r.get('horse'))==horse(name)]
            if len(runners)!=1: raise RuntimeError(f'Unsafe runner match for {name}: {len(runners)}')
            row=runners[0]
            if str(row.get('position'))!=str(position):
                raise RuntimeError(f'Result finishing position changed for {name}; refused')
            # Do not replace a pre-existing conflicting identity: manual review required.
            for field,value in (('trainer',trainer),('jockey',jockey)):
                original=str(row.get(field) or '').strip()
                if original and clean(original)!=clean(value):
                    raise RuntimeError(f'{field} disagreement for {name}: refused')
                if not original:
                    row[field]=value
                    changed.add(day)
            evidence={'source_url':url,'method':'manual_post_race_result_review',
                      'verified_on':'2026-11-09','horse':name,'position':position}
            if row.get('stage75_person_evidence') != evidence:
                row['stage75_person_evidence']=evidence
                changed.add(day)
            proof.append({'day':day,'course':co,'time':tm,'horse':name,'position':position,
                          'trainer':trainer,'jockey':jockey,'result_url':url})
        if day in changed:
            writes.append((path,document))
    if len(proof)!=len(CONFIRMED): raise RuntimeError('Evidence record count mismatch')
    for path,document in writes:
        path.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return proof,sorted(changed)


def patch_form_engine():
    source=STAGE32.read_text(encoding='utf-8')
    start='def get_best_rows(as_of: dt.date, cache: dict, audit: collections.Counter):'
    helper='''# Stage75: only the listed, result-sourced runner/person-name aliases are allowed.
# A pre-race person record cannot override an observed result or prove a
# substitution; the recorded result name stays authoritative.
STAGE75_PERSON_ALIASES = __STAGE75_ALIAS_LITERAL__

def stage75_approved_result_alias(race, runner, ref, kind):
    if race.get('source') != 'Stage72 independently verified published result':
        return False
    if not ref or ref.get('identity_conflict') or not ref.get(kind+'_id'):
        return False
    if not runner.get(kind) or not ref.get(kind):
        return False
    k = runner_key(str(race.get('date') or '')[:10], race.get('course'),
                   race.get('off_dt') or race.get('race_time') or race.get('off'),
                   runner.get('horse'))
    return kind in STAGE75_PERSON_ALIASES.get(k, ())

'''.replace('__STAGE75_ALIAS_LITERAL__',repr(ALIASES))
    if 'def stage75_approved_result_alias' not in source:
        if source.count(start)!=1: raise RuntimeError('Stage32 changed: cannot locate safe helper insertion')
        source=source.replace(start,helper+start,1)
    oldt="""                    else:
                        trainer=''
                        audit['result_identity_trainer_conflict_withheld'] += 1"""
    newt="""                    elif stage75_approved_result_alias(race, runner, ref, 'trainer'):
                        # Result source is independently verified and the exact
                        # pre-race horse/person identity supplies the provider ID.
                        # Preserve the actual result name, never the card name.
                        audit['stage75_verified_trainer_alias_kept'] += 1
                    else:
                        trainer=''
                        audit['result_identity_trainer_conflict_withheld'] += 1"""
    oldj="""                    else:
                        jockey=''
                        audit['result_identity_jockey_conflict_withheld'] += 1"""
    newj="""                    elif stage75_approved_result_alias(race, runner, ref, 'jockey'):
                        audit['stage75_verified_jockey_alias_kept'] += 1
                    else:
                        jockey=''
                        audit['result_identity_jockey_conflict_withheld'] += 1"""
    for label,old,new in [('trainer',oldt,newt),('jockey',oldj,newj)]:
        if new in source:
            continue
        if source.count(old)!=1:raise RuntimeError('Stage32 changed: refusing ambiguous patch '+label)
        source=source.replace(old,new,1)
    if 'stage75_approved_result_alias' not in source:
        raise RuntimeError('Stage75 source patch not installed')
    # Compile before touching the real model engine.
    compile(source,str(STAGE32),'exec')
    if source != STAGE32.read_text(encoding='utf-8'):
        STAGE32.write_text(source,encoding='utf-8')
        return True
    return False


def selftest():
    assert len(CONFIRMED)==11
    assert len(ALIASES)==14
    assert off('2026-11-04T13:20:00Z')=='13:20'
    assert horse('Allez Magh Glas (IRE)')=='allezmaghglas'
    assert course('Dundalk (AW) (IRE)')=='dundalk'
    assert sum('trainer' in x for x in ALIASES.values())==12
    assert sum('jockey' in x for x in ALIASES.values())==10
    print('Stage75 source controls and aliases validated')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--self-test',action='store_true')
    args=ap.parse_args()
    if args.self_test: selftest(); return
    source_changed=patch_form_engine()
    recovered,dates=recover_people()
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'stage':'75',
            'scope':'11 post-race corroborated trainer/jockey pairs + 22 approved alias observations',
            'human_verified_runner_pairs':len(recovered),'verified_results':recovered,
            'trainer_aliases_approved':12,'jockey_aliases_approved':10,
            'modified_result_days':dates,'form_engine_updated':source_changed,
            'safety':'No winner, placing, SP, model weights, odds, racecards, or selection data changed. No conflicts overridden.',
            'limitation':'Zero unmatched person records will not imply complete worldwide racing coverage or independently verified full finishing orders.'}
    REPORT.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('human_verified_runner_pairs','trainer_aliases_approved','jockey_aliases_approved','modified_result_days','form_engine_updated')},indent=2))

if __name__=='__main__': main()
