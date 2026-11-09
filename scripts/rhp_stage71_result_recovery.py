#!/usr/bin/env python3
"""Stage71 safe final-status classification of previously archived Racing API results.

The result must have provider provenance, a race ID, a unique recorded winner,
distinct named runners and known finishing positions or statuses. Where a separate
PuntersEdge result is saved, finishing positions must not conflict. Never invents
race identities, odds, placings, winners or any model predictions.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, pathlib, re, unicodedata
from zoneinfo import ZoneInfo

ROOT=pathlib.Path(__file__).resolve().parent.parent
UTC=dt.timezone.utc
COUNTRY=re.compile(r'\s*\((?:GB|IRE|IRL|FR|USA|GER|AUS|NZ|SAF|ITA|ITY|CAN)\)\s*$',re.I)
COURSE=re.compile(r'\s*\((?:AW|ALL WEATHER|IRE|IRL|GB)\)',re.I)
TERMINAL={'PU','F','RO','UR','BD','R','NR','DQ','DSQ','RR','VOID','WD','DNS','REF','P','RET','CO','S','SU'}


def compact(s):
    x=unicodedata.normalize('NFKD',str(s or ''))
    return re.sub('[^a-z0-9]','', ''.join(c for c in x if not unicodedata.combining(c)).casefold())


def horse(s): return compact(COUNTRY.sub('',str(s or '')))


def course(s):
    n=compact(COURSE.sub('',str(s or '')))
    return {'chelmsfordcity':'chelmsford'}.get(n,n)


def minute(value):
    try:
        x=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return (x if x.tzinfo else x.replace(tzinfo=UTC)).astimezone(UTC).strftime('%H:%M')
    except (ValueError,TypeError):
        m=re.search(r'\b(\d\d:\d\d)\b',str(value or ''))
        return m.group(1) if m else ''


def identity(r):
    return (str(r.get('date') or '')[:10],course(r.get('course')),
            minute(r.get('off_dt') or r.get('race_time') or r.get('off')))


def pos(v):
    if isinstance(v,bool): return None
    s=str(v or '').strip()
    return int(s) if s.isdecimal() and int(s)>0 else None


def winner(r):
    v=[horse(x.get('horse')) for x in r.get('runners',[])
       if isinstance(x,dict) and pos(x.get('position'))==1]
    return v[0] if len(v)==1 else None


def validate(r,day,source,today):
    if r.get('status') not in (None,''):return 'already_has_status'
    if day>today or str(r.get('date') or '')[:10]!=day:return 'invalid_day'
    if 'racing api' not in str(source or '').lower():return 'unconfirmed_archive_provenance'
    if not str(r.get('race_id') or '').startswith('rac_'):return 'missing_source_race_id'
    if str(r.get('region') or '').upper() not in ('GB','IE','IRE'):return 'unsupported_region'
    k=identity(r)
    if not k[1] or not k[2]:return 'invalid_race_identity'
    rows=r.get('runners')
    if not isinstance(rows,list) or len(rows)<2:return 'insufficient_runners'
    names=[horse(x.get('horse')) if isinstance(x,dict) else '' for x in rows]
    if not all(names) or len(set(names))!=len(names):return 'duplicate_or_missing_runner'
    if not winner(r):return 'ambiguous_winner'
    for x in rows:
        raw=str(x.get('position') or '').strip().upper()
        if pos(raw) is None and raw not in TERMINAL:
            # An explicit known terminal status is also valid if position is null.
            status=compact(x.get('status'))
            if status not in {'nr','nonrunner','dns','scratched','withdrawn','pulledup','pu','f','fell',
                              'unseatedrider','ur','broughtdown','bd','refused','r','ranout','ro',
                              'disqualified','dq','unplaced','finishedunplaced'}:
                return 'unknown_finishing_status'
        if not x.get('trainer') or not x.get('jockey'):return 'missing_person_identity'
    return None


def compare(a,b):
    if winner(a)!=winner(b):return 'winner_conflict'
    matches={horse(x.get('horse')):x for x in b.get('runners',[]) if isinstance(x,dict)}
    common=0
    for x in a['runners']:
        y=matches.get(horse(x.get('horse')))
        if y is None:continue
        common+=1
        p,q=pos(x.get('position')),pos(y.get('position'))
        if p is not None and q is not None and p!=q:return 'finishing_position_conflict'
    if common<2:return 'insufficient_runner_overlap'
    return None


def recover(root=ROOT,apply=True,report_path=None,as_of=None):
    today=as_of or dt.datetime.now(ZoneInfo('Europe/Dublin')).date().isoformat()
    root=pathlib.Path(root)
    feed=root/'data/race-results.json'
    external=json.loads(feed.read_text(encoding='utf-8')) if feed.exists() else {}
    finals=collections.defaultdict(list)
    for r in external.get('races',[]):
        if isinstance(r,dict) and r.get('status')=='final':finals[identity(r)].append(r)
    documents={}
    slots=collections.defaultdict(list)
    for path in sorted((root/'data/racing-results').glob('????-??-??.json')):
        if path.stem>=today or path.stem<(dt.date.fromisoformat(today)-dt.timedelta(days=30)).isoformat():continue
        payload=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload.get('results'),list):continue
        documents[path]=payload
        for r in payload['results']:
            if isinstance(r,dict):slots[identity(r)].append(r)
    report={'mode':'apply' if apply else 'dry-run','generated_at':dt.datetime.now(UTC).isoformat(),
            'missing_final_flag':0,'newly_validated':0,'provider_cross_checked':0,
            'archive_only_validated':0,'already_final':0,'held_for_review':[],
            'by_day':{},'method':'Source-validated Racing API classification with PuntersEdge cross-check where available',
            'model_safety':'No model weights, rankings, selections, SPs or finishing positions modified'}
    changed=set()
    for path,payload in documents.items():
        counts=collections.Counter()
        for r in payload['results']:
            if not isinstance(r,dict):continue
            if r.get('status')=='final':report['already_final']+=1;continue
            if r.get('status') not in (None,''):continue
            report['missing_final_flag']+=1
            counts['missing_final_flag']+=1
            issue=validate(r,path.stem,payload.get('source'),today)
            key=identity(r)
            if not issue:
                others=slots[key]
                if any(winner(x) and winner(x)!=winner(r) for x in others if x is not r):
                    issue='contradictory_archive_duplicate'
            if not issue:
                providers=finals.get(key,[])
                if len(providers)>1:
                    matched=[x for x in providers if winner(x)==winner(r)]
                    if len(matched)!=len(providers):issue='conflicting_provider_records'
                    else:issue=next((problem for x in matched if (problem:=compare(r,x))),None)
                elif providers:issue=compare(r,providers[0])
            if issue:
                counts['held']+=1
                report['held_for_review'].append({'day':path.stem,'course':r.get('course'),
                    'time':key[2],'race_id':r.get('race_id'),'reason':issue})
                continue
            source='provider_cross_checked' if finals.get(key) else 'archive_only_validated'
            counts[source]+=1
            report[source]+=1
            report['newly_validated']+=1
            if apply:
                r['status']='final'
                r['result_classification_source']='stage71_'+source
                r['completion_validation']='Existing recorded race result validated; rankings, runners, positions and odds unchanged'
                changed.add(path)
        if counts:report['by_day'][path.stem]=dict(counts)
    if apply:
        for path in changed:
            path.write_text(json.dumps(documents[path],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report['modified_daily_archives']=len(changed)
    report['unresolved_classification_count']=len(report['held_for_review'])
    if report_path:
        path=root/report_path
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def self_test():
    a={'race_id':'rac_test','date':'2026-11-02','region':'GB',
       'course':'Dundalk (AW) (IRE)','off_dt':'2026-11-02T13:30:00Z',
       'runners':[{'horse':'A (FR)','position':'1','trainer':'Trainer','jockey':'Jockey'},
                  {'horse':'B (IRE)','position':'PU','trainer':'Trainer','jockey':'Jockey'}]}
    assert identity(a)==('2026-11-02','dundalk','13:30')
    assert validate(a,'2026-11-02','Racing API results','2026-11-09') is None
    b={**a,'status':'final','runners':[{'horse':'A','position':1},{'horse':'B','position':None,'status':'pu'}]}
    assert compare(a,b) is None
    bad={**b,'runners':[{'horse':'A','position':2},{'horse':'B','position':1}]}
    assert compare(a,bad)=='winner_conflict'
    print('Stage71 guarded recovery self-test passed')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--report',default='data/rhp-stage71-recovery.json')
    opts=parser.parse_args()
    if opts.self_test:self_test();return
    report=recover(apply=not opts.dry_run,report_path=opts.report if not opts.dry_run else None)
    print(json.dumps({k:report[k] for k in ('missing_final_flag','newly_validated',
        'provider_cross_checked','archive_only_validated','unresolved_classification_count')},indent=2))

if __name__=='__main__':main()
