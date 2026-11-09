#!/usr/bin/env python3
"""Stage79: evidence-checked, research-only historical class and 2026 SP recovery.

Original Stage65E database is READ ONLY; candidate additions are saved separately.
No RPR is invented: The Racing API removed that field in June 2026.
"""
from __future__ import annotations
import argparse
import base64
import collections
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

IE = ZoneInfo('Europe/Dublin')
CLASS_IN_TITLE = re.compile(r'(?<![A-Za-z0-9])class\s*([1-7])(?![0-9])', re.I)
COUNTRY = re.compile(r'\s*\((?:GB|IRE|IRL|FR|USA|GER|AUS|NZ|SAF|ITA|ITY|CAN)\)\s*$', re.I)
TRACK_ANNOTATION = re.compile(r'\s*\((?:AW|ALL WEATHER|IRE|IRL|GB)\)', re.I)
SP_PATTERN = re.compile(r'^\s*(?:\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?|EVS|EVENS|EVEN)\s*(?:JF|F|CF|FAV)?\s*$',re.I)


def norm(x):
    x=unicodedata.normalize('NFKD',str(x or ''))
    return re.sub(r'[^a-z0-9]+','', ''.join(z for z in x if not unicodedata.combining(z)).lower())

def horse(x):return norm(COUNTRY.sub('',str(x or '')))
def course(x):return norm(TRACK_ANNOTATION.sub('',str(x or '')))
def valid(x):return x is not None and str(x).strip()!=''

def local_clock(value):
    s=str(value or '').strip()
    if not s:return ''
    try:
        x=dt.datetime.fromisoformat(s.replace('Z','+00:00'))
        if x.tzinfo:x=x.astimezone(IE)
        return x.strftime('%H:%M')
    except ValueError:
        match=re.search(r'(?:^|\D)([012]?\d):([0-5]\d)(?:\D|$)',s)
        return f'{int(match.group(1)):02d}:{match.group(2)}' if match else ''

def class_code(x):
    s=str(x or '').strip()
    if re.fullmatch(r'[1-7]',s):return s
    m=re.fullmatch(r'(?:class[_\s-]*|c)([1-7])',s,re.I)
    return m.group(1) if m else None

def title_class(x):
    matches=set(CLASS_IN_TITLE.findall(str(x or '')))
    return next(iter(matches)) if len(matches)==1 else None

def good_sp(value):
    if not valid(value):return None
    s=str(value).strip().upper()
    if not SP_PATTERN.fullmatch(s):return None
    if re.fullmatch(r'\d+(?:\.\d+)?',s):
        try:
            if float(s)<=1:return None
        except ValueError:return None
    return s

def tocsv(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def prepare_overlay(out):
    cx=sqlite3.connect(out/'stage79_enrichment.sqlite')
    cx.executescript('''
      CREATE TABLE IF NOT EXISTS recovered_values (
        source_file TEXT NOT NULL, source_line INTEGER NOT NULL,
        field TEXT NOT NULL, original_value TEXT, recovered_value TEXT NOT NULL,
        evidence_kind TEXT NOT NULL, evidence_source TEXT NOT NULL,
        evidence_race TEXT NOT NULL, verified_at TEXT NOT NULL,
        PRIMARY KEY (source_file,source_line,field));
      CREATE INDEX IF NOT EXISTS ix_field ON recovered_values(field);
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT);
    ''')
    return cx


def record(cx, row, field, replacement, kind, source, race_identity, audit):
    if not replacement:return
    path,line,original=row['source_file'],row['source_line'],row.get(field)
    if valid(original):return
    cx.execute('''INSERT OR IGNORE INTO recovered_values VALUES (?,?,?,?,?,?,?,?,?)''',
               (path,line,field,original,replacement,kind,source,race_identity,
                dt.datetime.now(dt.timezone.utc).isoformat()))
    if cx.execute('SELECT changes()').fetchone()[0]:audit['enriched_'+field]+=1


def source_class_pass(db,overlay,audit):
    """Recover only explicitly recorded classes from identical race or literal race title."""
    # One race id can appear on multiple courses/dates; never propagate just race_id.
    race_known=collections.defaultdict(set)
    for day,track,rid,val in db.execute('''SELECT race_date,course,race_id,race_class FROM historical_runs
                                          WHERE race_class IS NOT NULL AND trim(race_class)<>'' '''):
        code=class_code(val)
        if code:race_known[(day,track,rid)].add(code)
    audit['distinct_races_with_explicit_class']=len(race_known)
    validated_title=collections.Counter()
    # The title method must first be audited against known classes (not a model guess).
    for v,r in db.execute('''SELECT race_class,raw_json FROM historical_runs
                              WHERE race_class IS NOT NULL AND trim(race_class)<>'' '''):
        code=class_code(v)
        if not code:continue
        try:doc=json.loads(r)
        except (ValueError,TypeError):continue
        title=title_class(doc.get('race_name'))
        if title:
            validated_title['tested']+=1
            if title!=code:validated_title['conflicts']+=1
    audit['explicit_title_validation_tested']=validated_title['tested']
    audit['explicit_title_validation_conflicts']=validated_title['conflicts']
    # Fail closed if the same title convention disagrees with an existing class.
    use_title=validated_title['tested']>=20 and validated_title['conflicts']==0
    audit['explicit_title_recovery_enabled']=use_title
    qry='''SELECT source_file,source_line,race_date,course,race_id,race_class,raw_json
           FROM historical_runs WHERE race_class IS NULL OR trim(race_class)='' '''
    for path,line,day,track,rid,original,raw in db.execute(qry):
        audit['missing_class_examined']+=1
        key=(day,track,rid)
        known=race_known.get(key,set())
        code=next(iter(known)) if len(known)==1 else None
        evidence=None
        if code:evidence='same_original_race_verified_class'
        if not code and use_title:
            try:doc=json.loads(raw)
            except (TypeError,ValueError):doc={}
            code=title_class(doc.get('race_name'))
            if code:evidence='literal_Class_N_in_original_race_name'
        if len(known)>1:audit['race_class_conflict_skipped']+=1;continue
        if not code:continue
        record(overlay,{'source_file':path,'source_line':line,'race_class':original},
               'race_class','Class '+code,evidence,'Original Kaggle CSV v129',
               '|'.join(map(str,key)),audit)
        if audit['missing_class_examined']%200000==0:
            overlay.commit();print('Reviewed',audit['missing_class_examined'],'historical missing class rows',flush=True)
    overlay.commit()


def build_2026_lookup(db,audit):
    """Exact race/date/venue/local-off source identity, with observed winner to confirm."""
    races=collections.defaultdict(lambda:{'race_ids':set(),'winners':set(),'runners':collections.defaultdict(list),
                                    'has_missing_sp':False,'has_missing_class':False})
    query='''SELECT source_file,source_line,race_date,course,race_id,horse,finish,race_class,sp,raw_json
             FROM historical_runs WHERE race_date >= '2026-01-01' AND race_date < '2027-01-01' '''
    for path,line,day,track,rid,name,finish,cls,sp,raw in db.execute(query):
        try:doc=json.loads(raw)
        except (TypeError,ValueError):doc={}
        slot=(str(day),course(track),local_clock(doc.get('off')))
        if not slot[0] or not slot[1] or not slot[2]:audit['no_source_race_slot']+=1;continue
        obj=races[slot];obj['race_ids'].add(str(rid))
        nm=horse(name)
        if str(finish).strip().upper() in ('1','1ST','1='):obj['winners'].add(nm)
        if not valid(sp):obj['has_missing_sp']=True;audit['2026_source_missing_sp']+=1
        if not valid(cls):obj['has_missing_class']=True
        obj['runners'][nm].append({'source_file':path,'source_line':line,'race_class':cls,'sp':sp,'name':name,'finish':finish})
    return races


def fetch_api_day(day,usr,pwd,max_calls,left,errors):
    results=[];offset=0;page_size=500;seen=set()
    creds=base64.b64encode((usr+':'+pwd).encode('utf-8')).decode('ascii')
    while True:
        if left[0]>=max_calls:
            errors['API_REQUEST_BUDGET_REACHED']+=1;break
        params=[('start_date',day),('end_date',day),('limit',str(page_size)),('skip',str(offset)),
                ('region','gb'),('region','ire')]
        url='https://api.theracingapi.com/v1/results?'+urllib.parse.urlencode(params)
        request=urllib.request.Request(url,headers={'Authorization':'Basic '+creds,
                                                     'Accept':'application/json','User-Agent':'RHP-Stage79-evidence-audit/1.0'})
        left[0]+=1
        try:
            with urllib.request.urlopen(request,timeout=35) as r:doc=json.load(r)
        except urllib.error.HTTPError as e:
            errors['HTTP_'+str(e.code)]+=1
            if e.code in (401,403,404,422):errors['FATAL_ACCESS_ERROR']+=1
            break
        except (OSError,ValueError) as e:
            errors['NETWORK_OR_FORMAT_ERROR']+=1;break
        batch=doc if isinstance(doc,list) else (doc.get('results',[]) if isinstance(doc,dict) else [])
        if not isinstance(batch,list):errors['WRONG_RESULTS_SHAPE']+=1;break
        ids=[]
        for r in batch:
            if isinstance(r,dict):
                ident=str(r.get('race_id') or '')
                if ident and ident in seen:continue
                if ident:seen.add(ident)
                ids.append(r)
        results.extend(ids)
        if not batch:break
        offset+=len(batch)
        # Providers may impose a page-size cap below our requested limit.
        declared_total=doc.get('total',doc.get('count')) if isinstance(doc,dict) else None
        try:declared_total=int(declared_total) if declared_total is not None else None
        except (TypeError,ValueError):declared_total=None
        if declared_total is not None:
            if offset>=declared_total:break
        elif len(batch)<page_size:
            break
        if offset>=1500:errors['DAY_PAGINATION_SAFETY_LIMIT']+=1;break
        time.sleep(.27)
    return results


def provider_pass(db,overlay,root,lookup,audit,max_calls,api_days):
    user=os.environ.get('RACING_API_USERNAME','').strip()
    pwd=os.environ.get('RACING_API_PASSWORD','').strip()
    days=collections.Counter(k[0] for k,v in lookup.items() if v['has_missing_sp'] or v['has_missing_class'])
    days=sorted(days, key=lambda d:(-days[d],d))
    if api_days>0:days=days[:api_days]
    requests=[0];errors=collections.Counter();samples=[]
    if not user or not pwd:errors['NO_RACING_API_CREDENTIALS']+=1;days=[]
    for idx,day in enumerate(days):
        batch=fetch_api_day(day,user,pwd,max_calls,requests,errors)
        audit['api_dates_queried']+=1
        audit['api_races_returned']+=len(batch)
        for api_race in batch:
            if str(api_race.get('status','')).lower() in ('interim','abandoned','cancelled','void'):
                audit['nonfinal_api_race_skipped']+=1;continue
            if str(api_race.get('region','')).upper() not in ('GB','IRE','IE',''):
                continue
            slot=(str(api_race.get('date') or day)[:10],course(api_race.get('course')),
                  local_clock(api_race.get('off_dt') or api_race.get('off')))
            source=lookup.get(slot)
            if not source: audit['external_race_not_exactly_matched']+=1;continue
            if len(source['race_ids'])!=1:
                audit['source_race_id_ambiguous']+=1;continue
            api_runners=api_race.get('runners') or []
            if not isinstance(api_runners,list):continue
            source_winners=source['winners']
            api_winners={horse(r.get('horse') or r.get('name')) for r in api_runners
                         if isinstance(r,dict) and str(r.get('position')).strip().upper() in ('1','1ST','1=')}
            if len(source_winners)!=1 or len(api_winners)!=1 or source_winners!=api_winners:
                audit['winner_validation_failed']+=1;continue
            matched=0
            for runner in api_runners:
                if not isinstance(runner,dict):continue
                nm=horse(runner.get('horse') or runner.get('name'))
                candidates=source['runners'].get(nm,[])
                if len(candidates)!=1 or not nm:continue
                row=candidates[0]
                matchref=f'{slot[0]}|{slot[1]}|{slot[2]}|{nm}'
                raw_sp=good_sp(runner.get('sp'))
                if raw_sp and not valid(row['sp']) and str(row['finish']).strip().upper() not in ('NR','N/R','NONRUNNER','NON RUNNER','VOID','WD','WITHDRAWN','SCRATCHED','DNS') and str(runner.get('status','')).lower() not in ('nr','nonrunner','non_runner','scratched','withdrawn'):
                    record(overlay,row,'sp',raw_sp,'verified_external_final_result_sp',
                           'The Racing API /v1/results',matchref,audit)
                matched+=1
            audit['external_runner_name_matches']+=matched
            cls=class_code(api_race.get('class') or api_race.get('race_class'))
            if cls:
                original_classes={class_code(row['race_class']) for rs in source['runners'].values() for row in rs if valid(row['race_class'])}
                if original_classes and original_classes!={cls}:
                    audit['api_class_disagrees_with_existing_class']+=1
                    continue
                for rs in source['runners'].values():
                    for row in rs:
                        if not valid(row['race_class']):
                            record(overlay,row,'race_class','Class '+cls,'verified_external_race_class',
                                   'The Racing API /v1/results', '|'.join(slot),audit)
        if idx%10==0:overlay.commit()
        if requests[0]>=max_calls or errors.get('FATAL_ACCESS_ERROR'):break
        time.sleep(.27)
    overlay.commit()
    audit['api_calls']=requests[0]
    audit['api_days_considered']=len(days)
    audit['api_errors']=dict(errors)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=pathlib.Path,required=True)
    p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage79'))
    p.add_argument('--max-api-calls',type=int,default=650)
    p.add_argument('--api-days',type=int,default=0,help='0 = all dates with a 2026 missing field')
    p.add_argument('--no-api',action='store_true')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    if not a.db.is_file():raise SystemExit('Stage65E SQLite unavailable')
    db=sqlite3.connect('file:'+a.db.resolve().as_posix()+'?mode=ro',uri=True)
    db.execute('pragma query_only=on')
    if db.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('Source database corrupt')
    overlay=prepare_overlay(a.out)
    audit=collections.Counter()
    source_class_pass(db,overlay,audit)
    lookup=build_2026_lookup(db,audit)
    if not a.no_api:provider_pass(db,overlay,a.root,lookup,audit,a.max_api_calls,a.api_days)
    else:audit['api_disabled']=1
    rows=overlay.execute('''SELECT field,evidence_kind,count(*) FROM recovered_values GROUP BY field,evidence_kind ORDER BY field,evidence_kind''').fetchall()
    report={'stage':79,'generated_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'mode':'READ_ONLY_RESEARCH_OVERLAY','source_database':'Stage65E validated against Kaggle version 129',
            'priority':{'race_class_all_years':1911689,'sp_2026':1320,'rpr_2026':68159},
            'candidate_counts':[{'field':x,'evidence_kind':k,'count':n} for x,k,n in rows],
            'audit':dict(audit),'api_errors':audit.get('api_errors',{}),
            'rpr_status':'NOT_RECOVERED: The Racing API removed the RPR field in June 2026. An in-house performance_rating must never be labelled RPR.',
            'class_policy':'Only consistent same-race original data, explicitly written Class N in the source title after cross-validation, or verified external race class; Irish non-classed races are not assigned a UK class.',
            'sp_policy':'Only final-source SP matched by exact date, venue, off-time, runner and published winner; no bookmaker snapshot or forecast prices.',
            'production_changes':0,
            'next_step':'Review candidates and source access; approve only verified, licensed evidence before optional separate research-model use.'}
    overlay.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('summary',json.dumps(report)))
    overlay.commit();overlay.close();db.close()
    (a.out/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    tocsv(a.out/'candidate_counts.csv',report['candidate_counts'],['field','evidence_kind','count'])
    print(json.dumps(report,indent=2,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
