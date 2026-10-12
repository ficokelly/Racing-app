#!/usr/bin/env python3
"""Archive verifiable T-15 Power Favs from existing files; zero API calls."""
import json, re, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
OUT=DATA/'power-favs-results.json'

def dt(s):
    try:
        v=datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return v.astimezone(timezone.utc) if v.tzinfo else None
    except (ValueError,TypeError,OverflowError): return None

def norm(s):
    s=str(s or '').lower().strip()
    s=re.sub(r'\s*\((?:gb|ire|fr|usa|ger|aus|can|nz|jpn|ity)\)\s*$','',s)
    return re.sub(r'[^a-z0-9]','',s)

def course_norm(s):
    return norm(re.sub(r'\s*\((?:aw|a\.w\.)\)\s*$','',str(s or ''),flags=re.I))

def number(v):
    try:
        x=float(v)
        return x if 1 < x < 10000 else None
    except (ValueError,TypeError):return None

def load(path,default):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return default

def main():
    history=load(DATA/'odds-history.json',[])
    archive=load(OUT,{'version':1,'place_definition':'Top 3 finishers (not bookmaker each-way terms)','selections':[]})
    saved={(s['date'],s['race_id'],norm(s['horse'])):s for s in archive.get('selections',[])}
    # Exact timestamp of a bookmaker's observed quote, not merely file collection time.
    observations=defaultdict(lambda:defaultdict(list))
    for snap in history:
        sat=dt(snap.get('collected_at'))
        if not sat:continue
        for p in snap.get('prices',[]):
            if p.get('stale') is True:continue
            at=dt(p.get('collected_at') or snap.get('collected_at'))
            off=dt(p.get('race_time'))
            price=number(p.get('decimal_odds'))
            if not (at and off and price and p.get('race_id') and p.get('horse') and p.get('bookmaker')):continue
            # Reject observations dated after the snapshot itself.
            if at>sat+timedelta(seconds=90):continue
            market=(str(p['race_id']),str(p['bookmaker']).lower().replace('_','').replace(' ',''))
            observations[market][at].append((p,price,off))
    # First recorded price strictly earlier than each candidate observation.
    series=defaultdict(list)
    for market, times in observations.items():
        for at, quotes in times.items():
            for p,price,off in quotes:
                series[(market,norm(p['horse']))].append((at,price))
    for k in series:series[k].sort()
    for market,times in observations.items():
        for at,quotes in sorted(times.items()):
            # Must have a real observation in [T-20,T-15], never at T-14 or later.
            valid=[(p,price,off) for p,price,off in quotes if 15 <= (off-at).total_seconds()/60 <=20]
            if len(valid)<2:continue
            # All runners in a market must share the same race time.
            byoff=defaultdict(list)
            for q in valid:byoff[q[2]].append(q)
            for off,group in byoff.items():
                horses={norm(p['horse']):(p,price) for p,price,_ in group}
                if len(horses)<2:continue
                ordered=sorted(horses.values(),key=lambda z:z[1]); (fav,cur),(runner,second)=ordered[:2]
                if not (cur<3 and second>=4 and cur<second):continue
                points=series[(market,norm(fav['horse']))]
                prior=next(((t,v) for t,v in points if t<at),None)
                opening=number(fav.get('opening_odds'))
                baseline=prior[1] if prior else opening
                if baseline is None or baseline<=cur:continue
                date=off.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Dublin')).date().isoformat()
                ident=(date,str(fav['race_id']),norm(fav['horse']))
                candidate={
                    'date':date,'race_id':str(fav['race_id']),'horse':fav['horse'],
                    'course':fav.get('course',''),'race_time':off.isoformat(),
                    'observed_at':at.isoformat(),'minutes_before_off':round((off-at).total_seconds()/60,2),
                    'bookmaker':fav['bookmaker'],'odds':cur,'second_favourite_odds':second,
                    'earlier_odds':baseline,'baseline_source':'recorded' if prior else 'api_open_unverified',
                    'status':'Pending','position':None,
                }
                if ident not in saved or (dt(candidate['observed_at'])>dt(saved[ident]['observed_at']) and saved[ident]['status']=='Pending'):
                    saved[ident]=candidate
    # Settlement: prefer exact race ID; else uniquely matching course and off time.
    results_by_day={}
    for path in (DATA/'racing-results').glob('????-??-??.json') if (DATA/'racing-results').exists() else []:
        raw=load(path,{})
        results_by_day[path.stem]=raw.get('results',[])
    for s in saved.values():
        if s['status'] not in ('Pending','Unmatched'):continue
        races=results_by_day.get(s['date'],[])
        off=dt(s['race_time'])
        matches=[r for r in races if str(r.get('race_id'))==s['race_id']]
        if not matches:
            matches=[r for r in races if course_norm(r.get('course'))==course_norm(s['course']) and dt(r.get('off_dt')) and off and abs((dt(r['off_dt'])-off).total_seconds())<=120]
        if len(matches)!=1:continue
        runners=[r for r in matches[0].get('runners',[]) if norm(r.get('horse'))==norm(s['horse'])]
        if len(runners)!=1:continue
        pos=str(runners[0].get('position','')).strip().upper()
        if pos.isdigit():
            n=int(pos);s['position']=pos;s['status']='Won' if n==1 else 'Placed' if n<=3 else 'Lost'
        elif pos in ('NR','NON RUNNER','NON-RUNNER','WD','WITHDRAWN'):
            s['position']=pos;s['status']='Non-runner'
        elif pos in ('PU','F','UR','BD','RO','RR','DSQ','DQ','SU','REF','RET'):
            s['position']=pos;s['status']='Lost'
        # Unknown codes remain pending rather than falsely treated as losers.
    archive['selections']=sorted(saved.values(),key=lambda s:(s['date'],s['race_time'],s['horse']),reverse=True)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(archive,indent=2,ensure_ascii=False)+'\n')
    print(f"Archived {len(saved)} Power Favs; " + ', '.join(f"{v}: {sum(s['status']==v for s in saved.values())}" for v in ('Won','Placed','Lost','Pending','Non-runner')))

if __name__=='__main__':main()
