#!/usr/bin/env python3
"""Apply source-verified final result corrections for eight previously unresolved
D-model picks. Does NOT infer missing positions, manufacture new winners, or
change frozen model rankings. Safe to rerun after provider refreshes.
"""
from __future__ import annotations
import argparse, datetime as dt, json, pathlib, re, unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
FEED = ROOT/'data/race-results.json'
PROVENANCE = ROOT/'data/stage69-verified-outcome-sources.json'

# External complete results independently reviewed on 9 November 2026.
# Corrections are constrained by date, course, advertised off, horse and SP.
CORRECTIONS = [
  dict(date='2026-11-04', course='Chepstow', time='15:05', horse='Karton Plein', status='pu', sp='4/1', source='https://www.racingpost.com/results/12/chepstow/2026-11-04/930471/'),
  dict(date='2026-11-04', course='Wexford', time='13:33', horse='The Besty Woman', status='pu', sp='7/4', source='https://www.racingpost.com/results/201/wexford/2026-11-04/932390/'),
  dict(date='2026-11-04', course='Wexford', time='14:37', horse='Reflectionist', status='f', sp='14/1', source='https://www.racingpost.com/results/201/wexford/2026-11-04/932392/'),
  dict(date='2026-11-05', course='Newcastle', time='14:07', horse='Thorneylands', status='f', sp='11/8', source='https://www.racingpost.com/results/37/newcastle/2026-11-05/930491/'),
  dict(date='2026-11-07', course='Uttoxeter', time='12:50', horse='Cinquenta', status='ur', sp='9/4', source='https://www.racingpost.com/results/84/uttoxeter/2026-11-07/932289/'),
  dict(date='2026-11-07', course='Uttoxeter', time='15:05', horse='No No Fizz', status='pu', sp='25/1', source='https://www.racingpost.com/results/84/uttoxeter/2026-11-07/932294/'),
  dict(date='2026-11-09', course='Stratford', time='14:50', horse='Service Minimum', status='pu', sp='9/2', source='https://www.racingpost.com/results/67/stratford/2026-11-09/932520/'),
  dict(date='2026-11-09', course='Stratford', time='15:55', horse='Apollon Forlonge', status='pu', sp='13/8', source='https://www.racingpost.com/results/67/stratford/2026-11-09/932521/'),
]
STRATFORD_FINISH = [
    {'horse':'Zero Tolerance', 'position':1,'status':'ran','sp':'15/2'},
    {'horse':'Count Adhemar', 'position':2,'status':'ran','sp':'4/1'},
    {'horse':"Can't Resist It", 'position':3,'status':'ran','sp':'4/1'},
    {'horse':'Klassy Pearl', 'position':4,'status':'ran','sp':'9/1'},
    {'horse':'Crossgales King', 'position':5,'status':'ran','sp':'13/2'},
    {'horse':'Apollon Forlonge','position':None,'status':'pu','sp':'13/8'},
]

def key(s):
    s=unicodedata.normalize('NFKD',str(s or ''))
    s=''.join(ch for ch in s if not unicodedata.combining(ch))
    s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AW)\)\s*$','',s,flags=re.I)
    return re.sub(r'[^a-z0-9]','',s.casefold())

def off(t):
    if not t:return ''
    try:
        d=dt.datetime.fromisoformat(str(t).replace('Z','+00:00'))
        return d.astimezone(dt.timezone.utc).strftime('%H:%M') if d.tzinfo else d.strftime('%H:%M')
    except ValueError:return str(t)[:5]

def patch(races, strict=False):
    updated=[];missing=[]
    today=dt.datetime.now(dt.timezone.utc).date()
    for c in CORRECTIONS:
        # Do not resurrect an old race after the collector's 30-day retention.
        if (today-dt.date.fromisoformat(c['date'])).days>30:continue
        matches=[r for r in races if isinstance(r,dict) and r.get('status')=='final'
                 and r.get('date')==c['date'] and key(r.get('course'))==key(c['course'])
                 and off(r.get('off_dt') or r.get('race_time'))==c['time']]
        if not matches and c['horse']=='Apollon Forlonge':
            race={'course':'Stratford','date':'2026-11-09','off_dt':'2026-11-09T15:55:00Z',
                  'status':'final','runners':[dict(z) for z in STRATFORD_FINISH],
                  'verified_source':c['source']}
            races.append(race);matches=[race];updated.append('Stratford 15:55 complete externally verified result')
        if len(matches)!=1:
            missing.append(f"{c['date']} {c['course']} {c['time']}: {len(matches)} final races")
            continue
        race=matches[0]
        horses=[z for z in race.get('runners',[]) if key(z.get('horse'))==key(c['horse'])]
        if len(horses)!=1:
            missing.append(f"{c['horse']}: {len(horses)} exact matching horses")
            continue
        horse=horses[0]
        existing=key(horse.get('status'));wanted=key(c['status'])
        # Never override a conflicting explicit terminal-status or known position.
        if horse.get('position') not in (None,'') or existing not in ('','ran','finished','unplaced',wanted):
            if existing!=wanted:raise RuntimeError('Conflicting result, refuses overwrite: '+c['horse'])
        recorded_sp=str(horse.get('sp') or '').strip().lower().rstrip('f')
        if recorded_sp and recorded_sp!=c['sp'].lower():
            raise RuntimeError('SP differs from externally verified source: '+c['horse']+' '+recorded_sp)
        if horse.get('status')!=c['status']:
            horse['status']=c['status'];horse['sp']=horse.get('sp') or c['sp'];horse['verified_status_source']=c['source']
            updated.append(c['horse']+' '+c['status'].upper())
    if strict and missing:raise RuntimeError('Missing exact verified match: '+'; '.join(missing))
    return updated,missing

def main():
    a=argparse.ArgumentParser();a.add_argument('--strict',action='store_true');args=a.parse_args()
    if not FEED.exists():raise SystemExit('Final results feed missing')
    payload=json.loads(FEED.read_text());races=payload.get('races')
    if not isinstance(races,list):raise SystemExit('Invalid final results payload')
    changed,missing=patch(races,args.strict)
    PROVENANCE.write_text(json.dumps({'reviewed_on':'2026-11-09','items':CORRECTIONS,
             'note':'Specific manual corrections supported by published full race results; no guessed placings.'},indent=2)+'\n')
    if changed:
        FEED.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print('verified corrections:',len(changed),'remaining unmatched:',len(missing))
    for z in changed:print('  +',z)
    for z in missing:print('  !',z)

if __name__=='__main__':main()
