#!/usr/bin/env python3
"""Choose due Irish-time D(c) run; never waste a historical rebuild when not due."""
import argparse,datetime as dt,json,os,pathlib
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parent.parent
IE=ZoneInfo('Europe/Dublin')

def read(p,default):
    try:return json.loads(pathlib.Path(p).read_text())
    except (FileNotFoundError,ValueError):return default

def main():
    p=argparse.ArgumentParser();p.add_argument('--day',default='');p.add_argument('--event',default='schedule');a=p.parse_args()
    now=dt.datetime.now(IE);today=now.date();tomorrow=today+dt.timedelta(days=1)
    cards=read(ROOT/'data/racecards.json',{}).get('runners',[])
    dates={str(r.get('date') or r.get('race_time') or '')[:10] for r in cards if r.get('course') and r.get('horse')}
    schedule=read(ROOT/'data/strategy-dc-schedule.json',{}).get('runs',{})
    feed=read(ROOT/'data/strategy-dc-probabilities.json',{})
    produced={r.get('date') for r in feed.get('races',[]) if r.get('runners')}
    # A runner withdrawn since the latest pre-off assessment needs a new D(c)
    # field assessment even if the next configured clock slot is not due yet.
    from rhp_stage68_settle import course as course_key,horse as horse_key
    def nonrunner(r):
        status=''.join(c for c in str(r.get('runner_status') or r.get('status') or '').lower() if c.isalpha())
        return r.get('non_runner') is True or r.get('is_non_runner') is True or str(r.get('number') or '').upper()=='NR' or status in ('nr','nonrunner','withdrawn','scratched')
    published={}
    for race in feed.get('races',[]):
        published[(race.get('date'),course_key(race.get('course')),str(race.get('time') or ''))]=race
    def changed_fields(day):
        fields={}
        for r in cards:
            if str(r.get('date') or r.get('race_time') or '')[:10]!=day.isoformat():continue
            if not r.get('horse') or not r.get('course') or not r.get('race_time') or nonrunner(r):continue
            label=r['race_time']
            try:
                off=dt.datetime.fromisoformat(str(label).replace('Z','+00:00'))
                if off.tzinfo is None:off=off.replace(tzinfo=dt.timezone.utc)
                if off <= now.astimezone(dt.timezone.utc)+dt.timedelta(minutes=2):continue
                hhmm=off.astimezone(IE).strftime('%H:%M')
            except (ValueError,TypeError):continue
            key=(day.isoformat(),course_key(r['course']),hhmm)
            fields.setdefault(key,set()).add(horse_key(r['horse']))
        for key,names in fields.items():
            snapshot=published.get(key)
            if snapshot and len(names)>=2 and names!={horse_key(z.get('horse')) for z in snapshot.get('runners',[])}:
                return True
        return False
    def has_cards(day):return day.isoformat() in dates
    def has_unrun_cards(day):
        for r in cards:
            if str(r.get('date') or r.get('race_time') or '')[:10]!=day.isoformat():continue
            try:
                off=dt.datetime.fromisoformat(str(r.get('race_time')).replace('Z','+00:00'))
                if off.tzinfo is None:off=off.replace(tzinfo=dt.timezone.utc)
                if off>now.astimezone(dt.timezone.utc)+dt.timedelta(minutes=2):return True
            except (TypeError,ValueError):continue
        return False
    target=None;slot=''
    if a.day:
        requested=dt.date.fromisoformat(a.day)
        if has_cards(requested):target=requested;slot='manual-'+now.strftime('%Y%m%d%H%M')
    elif a.event=='workflow_dispatch':
        # A manual dispatch is also used after a confirmed non-runner;
        # never leave a changed field with a stale ranking.
        if has_unrun_cards(today):target=today;slot='manual-reassessment'
        elif has_unrun_cards(tomorrow):target=tomorrow;slot='manual-reassessment'
    else:
        # Catch up overdue slots after a GitHub cron delay; do not run every
        # older missed slot, since it would duplicate the same snapshot.
        # Prioritise an on-time 20:00/22:00 tomorrow-card decision over a
        # delayed catch-up for today's 14:00 slot, after today's racing.
        if now.hour>=20 and has_cards(tomorrow):
            due=[h for h in (20,22) if now.hour>=h]
            h=max(due)
            key=tomorrow.isoformat()+'|'+f'{h:02d}:00'
            if key not in schedule:target=tomorrow;slot=f'{h:02d}:00'
        # First available next-day card must publish before any missed current-day
        # catch-up. It is never silently substituted with old Model D rankings.
        if target is None and has_unrun_cards(tomorrow) and tomorrow.isoformat() not in produced:
            target=tomorrow;slot='first-cards'
        if target is None and has_unrun_cards(today) and changed_fields(today):
            target=today;slot='field-change-'+now.strftime('%Y%m%d%H%M')
        if target is None and has_unrun_cards(tomorrow) and changed_fields(tomorrow):
            target=tomorrow;slot='field-change-'+now.strftime('%Y%m%d%H%M')
        eligible_today=[h for h in (6,8,10,12,14) if now.hour>=h]
        if target is None and has_unrun_cards(today) and eligible_today and now.hour<18:
            h=max(eligible_today);key=today.isoformat()+'|'+f'{h:02d}:00'
            if key not in schedule:target=today;slot=f'{h:02d}:00'
    active=bool(target)
    print('Irish time:',now.isoformat(timespec='seconds'),'slot:',slot or 'none','target:',target or 'none','run:',active)
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:
            f.write('run='+str(active).lower()+'\n')
            f.write('day='+(target.isoformat() if target else '')+'\n')
            f.write('slot='+slot+'\n')

if __name__=='__main__':main()
