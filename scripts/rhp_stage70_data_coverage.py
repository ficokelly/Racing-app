#!/usr/bin/env python3
"""Stage70: prospective UK/IE race-card, D(c), result and form coverage audit.

Never creates race results, horse rankings or missing identities. Missing upstream
information is explicitly reported as a coverage gap or unknown. The audit looks
only at declared cards already in the repository, not a fictional fixture calendar.
"""
from __future__ import annotations
import argparse,collections,datetime as dt,json,pathlib,re
from zoneinfo import ZoneInfo
ROOT=pathlib.Path(__file__).resolve().parent.parent
UTC=dt.timezone.utc
IRISH=ZoneInfo('Europe/Dublin')

def data(path,default):
    try:return json.loads((ROOT/path).read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):return default

def norm(s):
    s=re.sub(r'\s*\((?:aw|all.weather)\)\s*$','',str(s or ''),flags=re.I)
    return re.sub('[^a-z0-9]','',s.casefold())

def stamp(s):
    try:
        d=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    except (TypeError,ValueError):return None

def clock(s):
    d=stamp(s)
    if d:return d.strftime('%H:%M')
    match=re.search(r'\b\d{2}:\d{2}\b',str(s or ''))
    return match.group(0) if match else ''

def race_key(day,course,time):return (str(day)[:10],norm(course),clock(time))

def is_nr(row):
    if any(row.get(k) is True for k in ('non_runner','is_non_runner','is_withdrawn','withdrawn','scratched')):return True
    return str(row.get('runner_status') or row.get('status') or '').lower().replace('-','') in ('nr','nonrunner','withdrawn','scratched','dns') or str(row.get('number') or '').upper()=='NR'

def audit(now=None):
    now=now or dt.datetime.now(UTC)
    today=now.astimezone(IRISH).date()
    raw=data('data/racecards-archive.json',{}).get('runners',[])+data('data/racecards.json',{}).get('runners',[])
    grouped=collections.defaultdict(dict)
    for x in raw:
        if not isinstance(x,dict) or not x.get('horse') or not x.get('course'):continue
        day=str(x.get('date') or x.get('race_time') or '')[:10]
        try:daydate=dt.date.fromisoformat(day)
        except ValueError:continue
        off=x.get('race_time') or x.get('off_dt')
        key=race_key(day,x['course'],off)
        if not key[2]:continue
        grouped[key][norm(x['horse'])]=x # newer live card overrides old snapshot
    feed=data('data/strategy-dc-probabilities.json',{})
    rankings={race_key(r.get('date'),r.get('course'),r.get('race_time') or r.get('time')):r for r in feed.get('races',[]) if isinstance(r,dict)}
    form=data('data/strike-rates.json',{})
    archive=collections.defaultdict(dict)
    root=ROOT/'data/racing-results'
    for day in (today-dt.timedelta(days=n) for n in range(0,15)):
        document=data('data/racing-results/'+day.isoformat()+'.json',{})
        for x in document.get('results',[]):
            if x.get('status')=='final':
                archive[day.isoformat()][race_key(day.isoformat(),x.get('course'),x.get('off_dt') or x.get('race_time'))]=x
    days=collections.defaultdict(lambda:{'declared_races':0,'declared_horses':0,'active_races':0,'d_c_ranked':0,'results_found':0})
    missing_dc=[];awaiting_dc=[];missing_results=[];insufficient=[];withdrawn=[]
    for key,horses in sorted(grouped.items()):
        day,course,time=key
        daydate=dt.date.fromisoformat(day)
        if daydate<today-dt.timedelta(days=14) or daydate>today+dt.timedelta(days=2):continue
        allrows=list(horses.values())
        active=[h for h in allrows if not is_nr(h)]
        record=days[day]
        record['declared_races']+=1;record['declared_horses']+=len(allrows)
        if len(active)>=2:record['active_races']+=1
        rank=rankings.get(key)
        if rank and len(rank.get('runners',[]))>=2:
            record['d_c_ranked']+=1
        elif len(active)>=2 and daydate>=today:
            item={'day':day,'course':allrows[0]['course'],'time':time,'active_runners':len(active)}
            # Future cards can legitimately await their next scoring slot.
            if feed.get('day')==day:missing_dc.append(item)
            else:awaiting_dc.append(item)
        if 0<len(active)<2:insufficient.append({'day':day,'course':allrows[0]['course'],'time':time,'active_runners':len(active)})
        nr=[x['horse'] for x in allrows if is_nr(x)]
        if nr:withdrawn.append({'day':day,'course':allrows[0]['course'],'time':time,'horses':nr})
        if key in archive.get(day,{}):record['results_found']+=1
        # Only previous calendar days are checked as fully complete; same-day
        # results can be delayed by the upstream publisher.
        elif daydate<today:
            missing_results.append({'day':day,'course':allrows[0]['course'],'time':time})
    # A day with no declared archived races is UNKNOWN, not 'no racing'.
    expected_form=[];missing_form=[]
    for n in range(14):
        day=(today-dt.timedelta(days=n)).isoformat()
        if days.get(day,{}).get('declared_races',0):
            expected_form.append(day)
            if day not in (form.get('periods',{}).get('14',{}).get('dates_available') or []):missing_form.append(day)
    gaps=form.get('periods',{}).get('14',{}).get('identity_gaps') or {}
    report={
        'generated_at':now.isoformat(),'audit_scope':'Saved UK/IE racecards and archived verified results, not complete external fixture calendar',
        'as_of_irish_date':today.isoformat(),'racecard_source_timestamp':data('data/racecards.json',{}).get('collected_at'),
        'd_c_generated_at':feed.get('generated_at'),'d_c_scored_date':feed.get('day'),
        'by_day':dict(sorted(days.items())),'missing_rankings_for_already_scored_date':missing_dc,
        'racecards_waiting_for_next_d_c_scoring_slot':awaiting_dc,
        'missing_verified_results_for_historic_declared_races':missing_results,
        'missing_form_dates_with_declared_races':missing_form,
        'races_with_fewer_than_two_active_runners':insufficient,
        'confirmed_non_runners':withdrawn,
        'unmatched_result_person_identities':gaps,
        'important_limitations':[
          'An unrecorded race cannot be detected unless it appears in another saved card or results source.',
          'A recorded calendar date does not establish complete result coverage for that day.',
          'Unmatched trainer/jockey identities are excluded from strike rates, never guessed.',
          'Missing D(c) predictions for a past race are not rebuilt using its finishing result.'
        ]}
    report['warnings_count']=len(missing_dc)+len(missing_results)+len(missing_form)+len(awaiting_dc)
    report['status']='REVIEW_GAPS' if report['warnings_count'] else 'NO_GAPS_DETECTED_WITHIN_SAVED_SOURCES'
    return report

def self_test():
    assert race_key('2026-11-10','Lingfield (AW)','2026-11-10T12:30:00Z')==('2026-11-10','lingfield','12:30')
    assert is_nr({'horse':'A','runner_status':'NR'})
    assert not is_nr({'horse':'A','status':'ran'})
    assert clock('2026-11-10T12:30:00Z')=='12:30'
    print('Stage70 coverage self-test passed')

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',default='data/rhp-stage70-coverage.json');p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test();return
    value=audit()
    path=ROOT/a.out;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':value['status'],'days':value['by_day'],'missing_scores':len(value['missing_rankings_for_already_scored_date']),
           'upcoming_scores':len(value['racecards_waiting_for_next_d_c_scoring_slot']),
           'missing_declared_historic_results':len(value['missing_verified_results_for_historic_declared_races']),
           'missing_form_dates':value['missing_form_dates_with_declared_races'],
           'unmatched_people':value['unmatched_result_person_identities'],'report':str(path)},indent=2))
if __name__=='__main__':main()
