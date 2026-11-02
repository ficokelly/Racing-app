#!/usr/bin/env python3
"""Capture upstream runner withdrawal status without an extra API request.

Wrap the existing collectors so their original outputs, paid-API usage,
credentials, bookmaker prices and archive logic stay unchanged.
"""
from __future__ import annotations
import argparse,datetime as dt,json,os,runpy
from pathlib import Path

FLAG_KEYS=('is_non_runner','non_runner','is_withdrawn','withdrawn','scratched','isNonRunner','nonRunner','isWithdrawn','isScratched')
STATUS_KEYS=('status','runner_status','horse_status','result_status','runnerStatus','horseStatus')

def capture(mode,root):
    original=json.load;observations=[]
    def wrapped(fp,*args,**kwargs):
        d=original(fp,*args,**kwargs)
        races=(d if isinstance(d,list) else d.get('racecards',[]) if mode=='racecards' else d.get('races',d.get('data',d.get('results',[])))) if isinstance(d,(dict,list)) else []
        if not isinstance(races,list):races=[]
        for race in races:
            if not isinstance(race,dict):continue
            if mode=='odds' and race.get('country') not in ('GB','IE'):continue
            if mode=='racecards' and race.get('region') not in ('GB','IRE'):continue
            for runner in race.get('runners',[]):
                if not isinstance(runner,dict):continue
                details={k:runner.get(k) for k in (*FLAG_KEYS,*STATUS_KEYS) if k in runner}
                if not details:continue
                details.update({'date':race.get('date'), 'course':race.get('course') if mode=='racecards' else race.get('venue'),
                               'race_time':race.get('off_dt') if mode=='racecards' else race.get('start_time'),
                               'horse':runner.get('horse') if mode=='racecards' else runner.get('name')})
                observations.append(details)
        return d
    json.load=wrapped
    start=Path.cwd()
    try:
        os.chdir(root)
        script='collect-racecards.py' if mode=='racecards' else 'collect-odds.py'
        runpy.run_path(script,run_name='__main__')
    finally:
        json.load=original
        os.chdir(start)
    dest=root/'data'/('racecards.json' if mode=='racecards' else 'latest-odds.json')
    payload=json.loads(dest.read_text())
    if mode=='odds':
        payload['runners_status']=observations
    else:
        def normal(s):return ''.join(c for c in str(s or '').casefold() if c.isalnum())
        def tkey(r):return (normal(r.get('course')),str(r.get('race_time') or '')[:16],normal(r.get('horse')))
        observed={tkey(r):r for r in observations}
        count=0
        for r in payload.get('runners',[]):
            # More conservative: match same race timestamp and horse, not just name.
            info=observed.get(tkey(r))
            if not info:continue
            for k in (*FLAG_KEYS,*STATUS_KEYS):
                if k in info:r[k]=info[k];count+=1
        print('Stage26 upstream racecard status keys preserved:',count)
    dest.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n')
    print('Stage26 explicit runner status snapshots:',len(observations),'mode:',mode)

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('odds','racecards'));p.add_argument('--repo',default='.')
    a=p.parse_args();capture(a.mode,Path(a.repo).resolve())
if __name__=='__main__':main()
