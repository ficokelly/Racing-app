#!/usr/bin/env python3
"""Shared pre-off eligibility for declared UK/Irish runner rows.

A missing jockey, odds, OR or recent-form string is NOT evidence of a withdrawal.
In Ireland, an unactivated reserve is identified by an explicit R-number
(e.g. R27); a reserve that is officially promoted must lose that designation
in an updated provider card before it becomes selectable.
"""
from __future__ import annotations
from collections import defaultdict
import re

NONRUNNER = {'nr','nonrunner','nonstarter','withdrawn','scratched','didnotstart','dns','wd','notrunning'}
RESERVE = {'reserve','res','reserved','standby','reservepending'}
CANCELLED_RACE = {'abandoned','cancelled','canceled','void','result','finished'}
TRUTHY = {'yes','true','1','y'}

def normal(x):
    return re.sub('[^a-z0-9]', '', str(x or '').casefold())

def positive(x):
    return x is True or (not isinstance(x,bool) and normal(x) in TRUTHY)

def eligibility(row):
    if not isinstance(row,dict):return 'NON_RUNNER'
    number=str(row.get('number') or '').strip().upper()
    if normal(row.get('race_status')) in CANCELLED_RACE:return 'RACE_NOT_RUNNING'
    for k in ('is_non_runner','non_runner','is_withdrawn','withdrawn','scratched',
              'isNonRunner','nonRunner','isWithdrawn','isScratched'):
        if positive(row.get(k)):return 'NON_RUNNER'
    if number in ('NR','N/R','WD','DNS'):return 'NON_RUNNER'
    for k in ('runner_status','status','horse_status','result_status','runnerStatus','horseStatus'):
        if normal(row.get(k)) in NONRUNNER:return 'NON_RUNNER'
    if re.fullmatch(r'R\d+',number) or number in ('R','RES','RESERVE'):
        return 'RESERVE'
    for k in ('is_reserve','reserve_pending','isReserve','is_reserve_runner'):
        if positive(row.get(k)):return 'RESERVE'
    for k in ('reserve_status','runner_status','status','horse_status','runnerStatus','horseStatus'):
        if normal(row.get(k)) in RESERVE:return 'RESERVE'
    if normal(row.get('jockey')) in RESERVE:return 'RESERVE'
    return 'ACTIVE'

def active_runner(row):return eligibility(row)=='ACTIVE'

def _race_key(row):
    return (str(row.get('date') or '')[:10],normal(row.get('course')),
            str(row.get('race_time') or '')[:19],str(row.get('race_id') or ''))

def apply_to_racecards(payload):
    rows=payload.get('runners',[]) if isinstance(payload,dict) else []
    groups=defaultdict(list); counts=defaultdict(int)
    for r in rows:
        if isinstance(r,dict):groups[_race_key(r)].append(r)
    for race_rows in groups.values():
        active=sum(active_runner(r) for r in race_rows)
        completed=all(eligibility(r)=='RACE_NOT_RUNNING' for r in race_rows)
        for r in race_rows:
            kind=eligibility(r)
            if kind=='NON_RUNNER':
                r['non_runner']=True
                r['runner_status']='NR'
            elif kind=='RESERVE':
                r['reserve_pending']=True
                r['runner_status']='RESERVE'
            # Preserve original field_size in completed historical races.
            if not completed:r['field_size']=str(active)
            counts[kind]+=1
    payload['eligibility_audit']={
        'source':'explicit_provider_status_or_runner_number',
        'active':counts['ACTIVE'],'non_runner':counts['NON_RUNNER'],
        'unconfirmed_reserves':counts['RESERVE'],
        'race_not_running':counts['RACE_NOT_RUNNING'],
        'races_checked':len(groups),
    }
    return payload

def self_test():
    assert eligibility({'number':'NR','race_status':'declared'})=='NON_RUNNER'
    assert eligibility({'number':'R27','race_status':'declared'})=='RESERVE'
    assert eligibility({'number':'R28','race_status':'declared'})=='RESERVE'
    assert eligibility({'runner_status':'withdrawn'})=='NON_RUNNER'
    assert eligibility({'is_reserve':True})=='RESERVE'
    assert eligibility({'race_status':'cancelled'})=='RACE_NOT_RUNNING'
    assert eligibility({'number':'12','race_status':'declared','jockey':''})=='ACTIVE'
    assert eligibility({'number':'12','race_status':'declared','form':''})=='ACTIVE'
    x={'runners':[dict(date='2026-11-15',race_id='r',course='Navan',race_time='2026-11-15T12:40:00Z',horse='A',number='1'),
                  dict(date='2026-11-15',race_id='r',course='Navan',race_time='2026-11-15T12:40:00Z',horse='B',number='R27'),
                  dict(date='2026-11-15',race_id='r',course='Navan',race_time='2026-11-15T12:40:00Z',horse='C',number='NR')]}
    apply_to_racecards(x)
    assert x['eligibility_audit']['active']==1
    assert [r['field_size'] for r in x['runners']]==['1','1','1']
    assert x['runners'][1]['reserve_pending'] and x['runners'][2]['non_runner']
    return True

if __name__=='__main__':
    self_test()
    print('Stage129 shared eligibility tests passed')
