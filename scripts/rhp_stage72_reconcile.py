#!/usr/bin/env python3
"""Stage72: verified race recovery, exclusion of non-run races, and person-data audit.

Only existing result, form, collector and audit files may change. Frozen model
scores, selections, racecards and historical predictions are never modified.
All manual result records are supported by dated external results pages.
"""
from __future__ import annotations
import argparse
import base64
import collections
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT=pathlib.Path(__file__).resolve().parent.parent
DATA=ROOT/'data'
ARCHIVE=DATA/'racing-results'
COVERAGE=ROOT/'scripts/rhp_stage70_data_coverage.py'
COLLECTOR=ROOT/'collect-puntersedge-results.py'
UTC=dt.timezone.utc

SOURCE_DUNDALK='https://www.racingtv.com/results/2026-11-04/dundalk/1915'
SOURCE_FFOS='https://www.racingtv.com/results/2026-11-08/ffos-las/1612'
SOURCE_NEWBURY='https://www.sportinglife.com/racing/racecards/2026-11-05/newbury/live-show/947094/grahame-church-memorial-mares-novices-hurdle-gbb-race'
SOURCE_NOTTINGHAM='https://www.racingtv.com/racecourses/uk/nottingham/results'
SOURCE_WINCANTON='https://www.racingtv.com/racecards/2026-11-07/wincanton/1535'


def read(path,default=None):
    path=pathlib.Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def save(path,val):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(val,indent=2,ensure_ascii=False)+'\n'
    if not path.exists() or path.read_text(encoding='utf-8')!=text:
        path.write_text(text,encoding='utf-8')
        return True
    return False

def compact(val):
    s=unicodedata.normalize('NFKD',str(val or ''))
    return re.sub('[^a-z0-9]','', ''.join(c for c in s if not unicodedata.combining(c)).casefold())

def horse(val):
    return compact(re.sub(r'\s*\((?:GB|IRE|IRL|FR|USA|GER|AUS|NZ|SAF|CAN|ITA)\)\s*$','',str(val or ''),flags=re.I))

def course(val):
    s=re.sub(r'\s*\((?:AW|ALL WEATHER|IRE|IRL|GB)\)','',str(val or ''),flags=re.I)
    s=compact(s)
    return {'chelmsfordcity':'chelmsford'}.get(s,s)

def clock(val):
    try:
        t=dt.datetime.fromisoformat(str(val).replace('Z','+00:00'))
        if t.tzinfo is None:t=t.replace(tzinfo=UTC)
        return t.astimezone(UTC).strftime('%H:%M')
    except (TypeError,ValueError):
        match=re.search(r'\b(\d\d:\d\d)\b',str(val or ''))
        return match.group(1) if match else ''

def key(r):
    return (str(r.get('date') or '')[:10],course(r.get('course')),clock(r.get('off_dt') or r.get('race_time') or r.get('off')))

def pos(x):
    s=str(x or '').strip(); return int(s) if s.isdecimal() and int(s)>0 else None

def winner(r):
    wins=[horse(z.get('horse')) for z in r.get('runners',[]) if isinstance(z,dict) and pos(z.get('position'))==1]
    return wins[0] if len(wins)==1 else None


def row(name,position,trainer,jockey,sp):
    return {'horse':name,'position':position,'status':'scratched' if position is None and sp is None else 'pu' if position=='PU' else 'ran',
            'trainer':trainer,'jockey':jockey,'sp':sp}

DUNDALK_ROWS=[
    ('The Rock Factor',1,'Emmet Mullins','Ben Coen','11/2'),
    ('Robot Rock',2,'A J Martin','Gary Carroll','12/1'),
    ('One Cool County',3,'P J F Murphy','Gavin Ryan','17/2'),
    ('Connova',4,'S Lavery','Robbie Colgan','6/1'),
    ('Dancing Steve',5,'A McGuinness','Camillus Byrne','3/1'),
    ('Lisnadill',6,'J C McConnell','Nicola Burns','66/1'),
    ('Miss Glenville',7,'Paul Power','Adam Grant','40/1'),
    ('Suerte',8,'S Lavery','Siobhan Rutledge','18/1'),
    ('Pettus Bridge',9,'Mrs D Foster','Rory Mulligan','9/2'),
    ('Star Mind',10,"Gerard O'Leary","Danny O'Leary",'11/1'),
    ('Jess Abi',11,'M Fahey','Reese Holohan','20/1'),
    ('Zitkala Sa',12,'E Sheehy','Seamie Heffernan','50/1'),
    ('Onyeisi',13,'K Thornton','J Cleary','33/1'),
    ('Thompson Gunner',14,'J C McConnell',"Jessica O'Gorman",'8/1'),
    ('Kings Gesture',None,None,None,None),
    ('My First Rose',None,None,None,None),
    ('Sarfarcat',None,None,None,None),
]
FFOS_ROWS=[
    ('Rula Bula',1,"J & A O'Neill","Jonjo O'Neill Jr.",'13/8'),
    ('Pure Carbon',2,'Harry Derham',"Paul O'Brien",'13/8'),
    ('Imperial Bede',3,"J & A O'Neill",'Harry Cobden','10/1'),
    ('Centara','PU','P F Nicholls','Jay Tidball','3/1'),
    ('A Moments Madness',None,"F O'Brien",'Mr Albi Tufnell',None),
]

EXCEPTIONS=[
    {'day':'2026-11-04','course':'Nottingham','time':'14:23','disposition':'UNRUN_DUPLICATE_CARD',
     'reason':'No separate official 14:23 race; result listing has a 13:50 novice race and 14:20 handicap. Do not infer a winner.',
     'source':SOURCE_NOTTINGHAM},
    {'day':'2026-11-07','course':'Wincanton','time':'15:35','disposition':'ABANDONED',
     'reason':'Official Racing TV race page records abandoned.', 'source':SOURCE_WINCANTON},
]


def verified_result(day,venue,off,region,rows,source):
    return {'course':venue,'date':day,'off_dt':f'{day}T{off}:00Z','region':region,'status':'final',
            'source':'Stage72 independently verified published result','verified_source':source,
            'verification_method':'Public full result with runner order, official SP and identities',
            'runners':[row(*a) for a in rows]}

RESTORATIONS=[
    verified_result('2026-11-04','Dundalk','19:15','IRE',DUNDALK_ROWS,SOURCE_DUNDALK),
    verified_result('2026-11-08','Ffos Las','16:12','GB',FFOS_ROWS,SOURCE_FFOS),
]


def ensure_results(report):
    for target in RESTORATIONS:
        day=target['date'];path=ARCHIVE/(day+'.json')
        doc=read(path,{'date':day,'results':[]})
        rows=doc.get('results')
        if not isinstance(rows,list):raise RuntimeError('Invalid archived races on '+day)
        exact=[r for r in rows if key(r)==key(target)]
        if len(exact)>1:raise RuntimeError('Ambiguous existing archived slot: '+str(key(target)))
        if exact:
            existing=exact[0]
            if winner(existing)!=winner(target):raise RuntimeError('Winner conflict: '+str(key(target)))
            old={horse(z.get('horse')):z for z in existing.get('runners',[])}
            for z in target['runners']:
                prior=old.get(horse(z['horse']))
                if prior and pos(prior.get('position')) and pos(z.get('position')) and pos(prior['position'])!=pos(z['position']):
                    raise RuntimeError('Runner finishing position conflict: '+z['horse'])
            if len(old)<len(target['runners']):
                existing['runners']=target['runners'];existing['source']=target['source'];existing['verified_source']=target['verified_source']
                existing['status']='final';save(path,doc);report['enriched_existing_races'].append(key(target))
            else:report['already_saved_results'].append(key(target))
        else:
            rows.append(target)
            doc['date']=day
            doc['source']=doc.get('source') or 'Racing API archive + verified external results'
            save(path,doc);report['restored_races'].append(key(target))


def amend_newbury_race(r):
    if key(r)!=('2026-11-05','newbury','13:52'):return False
    rows=r.get('runners') or []
    if winner(r)!=horse('Royal Response'):
        raise RuntimeError('Unexpected 13:52 Newbury winner; cannot apply stewards amendment')
    exact={horse(x.get('horse')):x for x in rows}
    if not {'connellscross','princessday'}<=set(exact):
        raise RuntimeError('Newbury amended-race horses absent')
    a,b=exact['connellscross'],exact['princessday']
    values=(pos(a.get('position')),pos(b.get('position')))
    if values not in ((3,4),(4,3)):
        raise RuntimeError('Unknown pre-amendment Newbury positions: '+str(values))
    changed=values!=(3,4)
    if changed:
        a['position']=3;b['position']=4
    if r.get('stewards_amended_result_source')!=SOURCE_NEWBURY:
        r['stewards_amended_result_source']=SOURCE_NEWBURY
        r['stewards_amendment']='Connells Cross placed 3rd; Princess Day placed 4th after interference'
        changed=True
    if r.get('status')!='final':
        if r.get('status') not in (None,''):raise RuntimeError('Conflicting race status on Newbury')
        r['status']='final';changed=True
    return changed


def ensure_newbury(report):
    for path in (ARCHIVE/'2026-11-05.json', DATA/'race-results.json'):
        doc=read(path,{})
        rows=doc.get('results',doc.get('races',[]))
        if not isinstance(rows,list):continue
        affected=0
        for r in rows:
            if key(r)==('2026-11-05','newbury','13:52'):
                if amend_newbury_race(r):affected+=1
        if affected:
            save(path,doc)
            report['newbury_sources_amended'].append(path.as_posix())


def install_collector_hook(report):
    # Preserve the stewards' revised positions even when the results provider
    # returns its older, pre-enquiry finishing order in a later nightly refresh.
    marker='corrected,not_found=apply_verified_race_finishes(kept,strict=False)'
    hook=("\n# Stage72: preserve documented amended result in subsequent provider refreshes.\n"
          "from scripts.rhp_stage72_reconcile import amend_newbury_race\n"
          "for _stage72_race in kept:\n"
          "    amend_newbury_race(_stage72_race)\n")
    if not COLLECTOR.exists():raise RuntimeError('PuntersEdge results collector missing')
    s=COLLECTOR.read_text(encoding='utf-8')
    if 'from scripts.rhp_stage72_reconcile import amend_newbury_race' in s:return
    if s.count(marker)!=1:raise RuntimeError('Collector source changed, refusing uncertain patch')
    COLLECTOR.write_text(s.replace(marker,marker+hook,1),encoding='utf-8')
    report['collector_amendment_guard_installed']=True


def install_coverage_exceptions(report):
    # Keep the two void/abandoned races as historical card observations, but
    # exclude them from the missing-WINNER count. Never write a fictitious
    # result record for a race that was not run.
    s=COVERAGE.read_text(encoding='utf-8')
    marker="    missing_dc=[];awaiting_dc=[];missing_results=[];insufficient=[];withdrawn=[]"
    replacement=("    missing_dc=[];awaiting_dc=[];missing_results=[];insufficient=[];withdrawn=[]\n"
                 "    non_run_races=[]\n"
                 "    exceptions={(str(e.get('day')),norm(e.get('course')),str(e.get('time'))):e\n"
                 "                for e in data('data/rhp-stage72-race-exceptions.json',{}).get('races',[])}")
    if 'non_run_races=[]' not in s:
        if s.count(marker)!=1:raise RuntimeError('Stage70 coverage script changed (init), refusing uncertain patch')
        s=s.replace(marker,replacement,1)
    marker2="        if result_for(key,horses):record['results_found']+=1"
    replacement2=("        if key in exceptions:\n"
                  "            non_run_races.append(dict(exceptions[key]))\n"
                  "        elif result_for(key,horses):record['results_found']+=1")
    if 'non_run_races.append(dict(exceptions[key]))' not in s:
        if s.count(marker2)!=1:raise RuntimeError('Stage70 coverage script changed (match), refusing uncertain patch')
        s=s.replace(marker2,replacement2,1)
    marker3="        'missing_verified_results_for_historic_declared_races':missing_results,"
    replacement3=("        'missing_verified_results_for_historic_declared_races':missing_results,\n"
                  "        'historical_races_not_run':non_run_races,")
    if "'historical_races_not_run':non_run_races" not in s:
        if s.count(marker3)!=1:raise RuntimeError('Stage70 coverage script changed (report), refusing uncertain patch')
        s=s.replace(marker3,replacement3,1)
    if s!=COVERAGE.read_text(encoding='utf-8'):
        COVERAGE.write_text(s,encoding='utf-8')
        report['coverage_exceptions_hook_installed']=True
    save(DATA/'rhp-stage72-race-exceptions.json',{'verified_at':'2026-11-09','races':EXCEPTIONS,
        'note':'Non-run racecards are not missing result records; do not fabricate winners.'})


def historical_api_enrichment(report,all_dates=False):
    username=os.getenv('RACING_API_USERNAME','').strip(); password=os.getenv('RACING_API_PASSWORD','').strip()
    if not username or not password:
        report['api_status']='skipped_credentials_unavailable'
        return
    auth='Basic '+base64.b64encode(f'{username}:{password}'.encode()).decode()
    # Check the last 21 days: includes the 14-day strike-rate interval and
    # the 25-31 October legacy provenance window, not the full database.
    today=dt.date.fromisoformat(os.getenv('RHP_REPAIR_ASOF','2026-11-09'))
    dates=[today-dt.timedelta(days=n) for n in range(21)]
    stats=collections.Counter();errors=[];updated=set()
    available={}
    for day in dates:
        path=ARCHIVE/(day.isoformat()+'.json')
        if path.exists():available[path]=read(path,{})
    if not available:
        report['api_status']='no_saved_archives'
        return
    for date in sorted(dates):
        # Single day limits avoid scanning all historical results; no secrets written to disk.
        skip=0
        for page in range(4):
            query=urllib.parse.urlencode({'start_date':date.isoformat(),'end_date':date.isoformat(),
                      'region':['gb','ire'],'limit':100,'skip':skip},doseq=True)
            req=urllib.request.Request('https://api.theracingapi.com/v1/results?'+query,
                     headers={'Authorization':auth,'Accept':'application/json','User-Agent':'RacingHotPots-Stage72/1.0'})
            try:
                with urllib.request.urlopen(req,timeout=25) as resp:
                    payload=json.load(resp)
            except urllib.error.HTTPError as exc:
                report['api_status']='unavailable_http_'+str(exc.code)
                errors.append({'day':date.isoformat(),'error':'HTTP '+str(exc.code)})
                report['api_errors']=errors
                return
            except (OSError,TimeoutError,ValueError) as exc:
                errors.append({'day':date.isoformat(),'error':type(exc).__name__})
                break
            rows=payload.get('results',[]) if isinstance(payload,dict) else []
            if not isinstance(rows,list):errors.append({'day':date.isoformat(),'error':'invalid_structure'});break
            stats['source_races_checked']+=len(rows)
            target=available.get(ARCHIVE/(date.isoformat()+'.json'),{})
            archived=target.get('results',[])
            for incoming in rows:
                if not isinstance(incoming,dict) or not isinstance(incoming.get('runners'),list):continue
                exact=[r for r in archived if key(r)==key(incoming)]
                if len(exact)!=1:continue
                old=exact[0]
                if winner(incoming) is None or winner(incoming)!=winner(old):
                    stats['skipped_winner_conflict']+=1;continue
                persons={horse(x.get('horse')):x for x in incoming['runners'] if isinstance(x,dict)}
                common=0;contradictory=False
                for runner in old.get('runners',[]):
                    other=persons.get(horse(runner.get('horse')))
                    if not other:continue
                    common+=1
                    p,q=pos(runner.get('position')),pos(other.get('position'))
                    if p and q and p!=q:
                        # Newbury amended placing is managed from an official
                        # stewards source separately; provider may be stale.
                        contradictory=True;break
                if contradictory or common<2:
                    stats['skipped_position_or_identity_conflict']+=1;continue
                changed=False
                for runner in old.get('runners',[]):
                    other=persons.get(horse(runner.get('horse')))
                    if not other:continue
                    for person in ('trainer','jockey'):
                        field=person+'_id'
                        val=str(other.get(person) or '').strip()
                        prev=str(runner.get(person) or '').strip()
                        if val and not prev:
                            runner[person]=val;changed=True;stats['enriched_'+person]+=1
                        elif val and prev and compact(val)!=compact(prev):
                            stats['person_name_disagreements']+=1
                        if val and not prev and other.get(field):
                            runner[field]=str(other[field])
                if changed:updated.add(ARCHIVE/(date.isoformat()+'.json'))
                if old.get('status') not in ('final',):
                    old['status']='final';old['result_classification_source']='stage72_api_independently_crosschecked'
                    updated.add(ARCHIVE/(date.isoformat()+'.json'))
                    stats['provider_crosschecked_final']+=1
            skip+=len(rows)
            if not rows or len(rows)<100:break
            time.sleep(.28)
        # Persist day only after all matches checked, avoiding unrelated overwrites.
        if ARCHIVE/(date.isoformat()+'.json') in updated:
            save(ARCHIVE/(date.isoformat()+'.json'),available[ARCHIVE/(date.isoformat()+'.json')])
    report['api_status']='completed' if not errors else 'partial'
    report['api_errors']=errors
    report['api_stats']=dict(stats)


def audit_provenance(report):
    # 108 legacy records lack a payload-level source label. Check their actual
    # recorded race content and report uncertainty. Do not assign provenance.
    unknown=[]
    for path in ARCHIVE.glob('2026-10-??.json'):
        if not ('2026-10-25'<=path.stem<='2026-10-31'):continue
        d=read(path,{})
        if 'racing api' in str(d.get('source','')).lower():continue
        for r in d.get('results',[]):
            if isinstance(r,dict) and r.get('status') not in ('final',):
                unknown.append({'day':path.stem,'course':r.get('course'),
                     'off':clock(r.get('off_dt') or r.get('off')),'race_id':r.get('race_id'),
                     'winner_recorded':bool(winner(r)),'has_horse_and_person_ids':bool(r.get('runners') and
                       any(z.get('horse_id') and z.get('jockey_id') and z.get('trainer_id') for z in r['runners']))})
    report['legacy_provenance_unresolved_count']=len(unknown)
    report['legacy_provenance_samples']=unknown[:15]


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--no-api',action='store_true')
    args=p.parse_args()
    if args.self_test:
        assert len(DUNDALK_ROWS)==17 and sum(x[1] is not None for x in DUNDALK_ROWS)==14
        assert len(FFOS_ROWS)==5 and sum(x[1] is not None for x in FFOS_ROWS)==4
        for race in RESTORATIONS:
            assert len([x for x in race['runners'] if pos(x['position'])==1])==1
        assert winner(RESTORATIONS[0])==horse('The Rock Factor')
        assert winner(RESTORATIONS[1])==horse('Rula Bula')
        newbury={'date':'2026-11-05','course':'Newbury','off_dt':'2026-11-05T13:52:00Z',
                 'runners':[{'horse':'Royal Response','position':1},{'horse':'Princess Day','position':3},
                            {'horse':'Connells Cross','position':4}]}
        assert amend_newbury_race(newbury)
        assert [z['position'] for z in newbury['runners']]==[1,4,3]
        print('Stage72 tests passed')
        return
    report={'generated_at':dt.datetime.now(UTC).isoformat(),'stage':'72',
            'restored_races':[],'enriched_existing_races':[],'already_saved_results':[],
            'newbury_sources_amended':[],'collector_amendment_guard_installed':False,
            'coverage_exceptions_hook_installed':False,
            'frozen_models':'untouched: no rankings, selections, model weights or pre-off data edited'}
    ensure_results(report)
    ensure_newbury(report)
    install_collector_hook(report)
    install_coverage_exceptions(report)
    if not args.no_api:historical_api_enrichment(report)
    else:report['api_status']='skipped_by_option'
    audit_provenance(report)
    report['audit_reminder']='Missing trainer/jockey counts represent remaining source data gaps; never guessed.'
    save(DATA/'rhp-stage72-repair-report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('legacy_provenance_samples',)},indent=2))

if __name__=='__main__':main()
