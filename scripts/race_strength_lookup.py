#!/usr/bin/env python3
"""Racing Hot Pots research-only race-strength lookup (GB/IRELAND).

Drop-in categorical feature generator; does NOT automatically modify any live model.
Never treats source hierarchy as an official numerical ability rating.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
import pathlib
import re
import sqlite3
import statistics
from collections import Counter

REFERENCE = pathlib.Path(__file__).with_name('race_strength_reference_v1.json')


def load_reference(path: pathlib.Path = REFERENCE):
    v = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    if v['schema_version'] != '1.0.0':
        raise ValueError('Incompatible strength-reference schema')
    return {r['code']:r for r in v['classifications']}


def jurisdiction(raw):
    s = re.sub(r'[^A-Z]','',str(raw or '').upper())
    if s in ('GB','UK','GREATBRITAIN','BRITAIN','ENGLAND','SCOTLAND','WALES'):return 'GB'
    if s in ('IRE','IE','IRL','IRELAND','REPUBLICOFIRELAND'):return 'IRELAND'
    return 'UNKNOWN'


def discipline(raw, title='', surface=''):
    s = (str(raw or '')+' '+str(title or '')).lower()
    if re.search(r'\b(?:nh\s*flat|i\.?n\.?h\.?\s*flat|national\s*hunt\s*flat|bumper)\b',s):return 'NH_FLAT'
    if re.search(r'\b(?:chase|steeplechase|steeple\s*chase)\b',s):return 'CHASE'
    if re.search(r'\bhurdles?\b',s):return 'HURDLE'
    if re.search(r'\b(?:flat|maiden|nursery|auction|stakes|handicap|h\s*\'?\s*cap)\b',s):
        return 'AW' if re.search(r'\b(?:aw|all.?weather|polytrack|tapeta)\b',str(surface or ''),re.I) else 'FLAT'
    return 'UNKNOWN'


def parse_uk_class(s, *, allow_bare=False):
    m=re.search(r'\b(?:class|cls|c)\s*[-:#]?\s*([1-7])\b',s,re.I)
    if m:return int(m.group(1))
    s=s.strip()
    if allow_bare and re.fullmatch('[1-7]',s):return int(s)
    return None


def parse_ranked_tier(event, table=None):
    """Use only explicit pre-race labels; do not infer grade from race type."""
    table=table or load_reference()
    country=jurisdiction(event.get('jurisdiction') or event.get('region_scope') or event.get('country'))
    name=str(event.get('race_name') or event.get('name') or '')
    rtype=str(event.get('race_type') or '')
    rclass=str(event.get('race_class') or event.get('class') or '')
    surf=str(event.get('surface') or '')
    race_code=discipline(event.get('discipline') or rtype,name,surf)
    # Some older data identify discipline only in their source race_type.
    text=' '.join((name,rtype))
    is_handicap=bool(re.search(r'\b(?:handicaps?|h\.?\s*cap|nursery|heritage\s+handicap)\b',text,re.I))
    is_novice=bool(re.search(r'\b(?:novices?|novice\'?s|beginners?)\b',text,re.I))
    is_maiden=bool(re.search(r'\bmaiden\b',text,re.I))
    is_restricted=bool(re.search(r'\b(?:restricted|seller|selling|claimer|claiming|auction)\b',text,re.I))
    d=str(event.get('race_date') or event.get('date') or '')[:10]
    if d and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',d):d=''
    patterns=[];warnings=[];proof=[]
    # Name and clearly designated race-class metadata; 'Grade 1' always takes
    # precedence over generic 'Class 1'. Grade A is NOT Grade 1.
    both=' '.join((name,rtype,rclass))
    m=re.search(r'\b(?:group|grade|grp|gr)\s*[-:.]?\s*([123]|III|II|I)\b',both,re.I)
    if m:
        g={'I':1,'II':2,'III':3}.get(m.group(1).upper()) or int(m.group(1))
        # Guard against false cross-discipline terms: any ambiguous cross-code
        # label requires an explicit authoritative mapping instead of guessing.
        if race_code in ('FLAT','AW'):
            if re.search(r'\b(?:group|grp)\s*[-:.]?\s*'+re.escape(m.group(1))+r'\b',both,re.I):
                patterns.append((f'FLAT_GROUP_{g}','EXPLICIT_GROUP'))
            else:warnings.append('GRADE_LABEL_ON_FLAT_NOT_ASSUMED_GROUP')
        elif race_code in ('HURDLE','CHASE','NH_FLAT'):
            if is_handicap and country=='IRELAND' and g==3:
                patterns.append(('IE_NH_G3_HANDICAP','EXPLICIT_IRISH_GRADED_HANDICAP'))
            elif is_handicap and country=='GB' and g==3:
                if d and d<'2022-10-01':patterns.append(('GB_NH_PREMIER_HANDICAP','GB_PREMIER_HISTORICAL_ALIAS'))
                else:warnings.append('GB_GRADE3_HANDICAP_REQUIRES_EVENT_DATE_OR_MANUAL_VERIFICATION')
            elif is_handicap:
                warnings.append('GRADE_HANDICAP_OTHER_REQUIRES_EXACT_EVENT_VERIFICATION')
            else:patterns.append((f'NH_GRADE_{g}','EXPLICIT_GRADE'))
    if re.search(r'\blisted\b',both,re.I):
        if is_handicap and race_code in ('HURDLE','CHASE') and country=='IRELAND':patterns.append(('IE_NH_LISTED_HANDICAP','EXPLICIT_IRISH_LISTED_HANDICAP'))
        elif is_handicap and race_code in ('HURDLE','CHASE') and country=='GB' and d>='2022-10-01':
            warnings.append('GB_LISTED_HANDICAP_DISCONTINUED_2022_VERIFY')
        elif not is_handicap and race_code in ('FLAT','AW'):patterns.append(('FLAT_LISTED','EXPLICIT_LISTED'))
        elif not is_handicap and race_code in ('HURDLE','CHASE','NH_FLAT'):patterns.append(('NH_LISTED','EXPLICIT_LISTED'))
    # Premium handicap categories are explicitly *different* from graded races.
    if is_handicap and country=='GB' and race_code in ('HURDLE','CHASE') and re.search(r'\bpremier\s+handicap\b',both,re.I):patterns.append(('GB_NH_PREMIER_HANDICAP','EXPLICIT_PREMIER'))
    if is_handicap and country=='IRELAND' and race_code in ('FLAT','AW') and re.search(r'\bpremier\s+handicap\b',both,re.I):patterns.append(('IE_FLAT_PREMIER_HANDICAP','EXPLICIT_PREMIER'))
    if is_handicap and country=='GB' and race_code in ('FLAT','AW') and re.search(r'\bheritage\s+handicap\b',both,re.I):patterns.append(('GB_FLAT_HERITAGE_HANDICAP','EXPLICIT_HERITAGE'))
    # Britain class 1-6. A bare class digit is valid in an explicitly named
    # structured class field only; bracketed digits in a race title are ignored.
    cls_field=parse_uk_class(rclass,allow_bare=True)
    cls_name=parse_uk_class(name,allow_bare=False)
    if country=='GB':
        if cls_field and cls_name and cls_field!=cls_name:
            warnings.append('CONFLICTING_UK_CLASS_TITLE_AND_FIELD')
        elif cls_field or cls_name:
            cls=cls_field or cls_name
            if cls==7 and d and d>='2021-02-01':warnings.append('CLASS7_DISCONTINUED_AFTER_2021')
            else:patterns.append((f'GB_CLASS_{cls}','EXPLICIT_UK_CLASS'))
    elif cls_field or cls_name:
        warnings.append('BRITISH_CLASS_LABEL_ON_IRISH_RACE_NOT_TREATED_AS_OFFICIAL_IRISH_CLASS')
    # Ireland rating-band category, not a Pattern grade. May appear in title
    # or separately provided eligibility band; only applies to handicaps.
    band_source=' '.join((text,str(event.get('rating_band') or '')))
    match=re.search(r'(?<!\d)0\s*[-–]\s*(\d{2,3})(?!\d)',band_source)
    band=int(match.group(1)) if match else None
    if country=='IRELAND' and is_handicap and band:
        sub='IE_FLAT' if race_code in ('FLAT','AW') else 'IE_NH' if race_code in ('HURDLE','CHASE') else ''
        key=f'{sub}_BAND_0_{band}' if sub else ''
        if key in table:patterns.append((key,'EXPLICIT_IRISH_HANDICAP_RATING_BAND'))
        else:warnings.append('UNMAPPED_IRISH_RATING_BAND')
    let=re.search(r'\bgrade\s*([ABC])\b',both,re.I)
    if country=='IRELAND' and is_handicap and race_code in ('HURDLE','CHASE') and let:
        patterns.append(('IE_NH_LETTER_'+let.group(1).upper(),'IRISH_LETTER_GRADE_UNMAPPED'))
    # Only compare strengths WITHIN an appropriate evidence track. Never
    # replace a recognised Pattern grade with a generic Class value.
    if country=='UNKNOWN':warnings.append('COUNTRY_UNVERIFIED')
    if race_code=='UNKNOWN':warnings.append('DISCIPLINE_UNVERIFIED')
    precedence={
        'EXPLICIT_GROUP':0,'EXPLICIT_GRADE':0,'EXPLICIT_LISTED':0,
        'EXPLICIT_IRISH_GRADED_HANDICAP':0,'EXPLICIT_IRISH_LISTED_HANDICAP':1,
        'EXPLICIT_PREMIER':1,'GB_PREMIER_HISTORICAL_ALIAS':1,
        'EXPLICIT_HERITAGE':2,'EXPLICIT_IRISH_HANDICAP_RATING_BAND':3,
        'EXPLICIT_UK_CLASS':4,'IRISH_LETTER_GRADE_UNMAPPED':5,
    }
    patterns=[(k,p) for k,p in patterns if k in table]
    patterns.sort(key=lambda z:(precedence.get(z[1],10),-(table[z[0]].get('provisional_strength_score') or 0)))
    code,method=patterns[0] if patterns else ('UNKNOWN','NO_VERIFIED_STRENGTH')
    row=table[code]
    if code.startswith('GB_CLASS_1'):warnings.append('CLASS1_SUBTIER_UNKNOWN')
    if code=='UNKNOWN' and not warnings:warnings.append('NO_EXPLICIT_GRADE_OR_CLASS')
    confidence=1.0 if method.startswith('EXPLICIT_G') or method=='EXPLICIT_LISTED' else .95 if method in ('EXPLICIT_PREMIER','EXPLICIT_IRISH_GRADED_HANDICAP','EXPLICIT_IRISH_LISTED_HANDICAP','EXPLICIT_HERITAGE') else .85 if method in ('EXPLICIT_UK_CLASS','EXPLICIT_IRISH_HANDICAP_RATING_BAND') else .8 if method=='GB_PREMIER_HISTORICAL_ALIAS' else 0.
    if code=='GB_CLASS_1':confidence=.55
    if any('CONFLICTING_' in z for z in warnings):
        code='UNKNOWN';row=table[code];method='CONFLICTING_METADATA';confidence=0.
    return {
        'jurisdiction':country,'discipline':race_code,'race_date':d or None,
        'race_subtype':'HANDICAP' if is_handicap else 'MAIDEN' if is_maiden else 'NOVICE_OR_BEGINNER' if is_novice else 'OTHER',
        'is_handicap':is_handicap,'is_novice':is_novice,'is_maiden':is_maiden,
        'is_restricted':is_restricted,'handicap_band_upper':band if is_handicap else None,
        'classification_code':code,'classification_label':row['label'],
        'classification_segment':row['segment'],'rank_within_segment':row['rank_within_segment'],
        'provisional_strength_score':row['provisional_strength_score'],
        'classification_confidence':confidence,'evidence_method':method,
        'warnings':warnings,
    }


def opposition_features(preoff_runners):
    """Only declared pre-race official ratings; null if insufficient coverage."""
    records=list(preoff_runners or [])
    vals=[]
    for r in records:
        x=r.get('official_rating',r.get('or'))
        try:v=float(x)
        except (ValueError,TypeError):continue
        if 0<v<210 and math.isfinite(v):vals.append(v)
    vals.sort(reverse=True)
    coverage=len(vals)/len(records) if records else 0.
    return {'field_rating_count':len(vals),'field_rating_coverage':round(coverage,4),
            'field_rating_median':round(statistics.median(vals),2) if coverage>=.5 and len(vals)>=3 else None,
            'top4_mean_preoff_or':round(statistics.mean(vals[:4]),2) if len(vals)>=4 else None,
            'field_rating_warning':'LOW_COVERAGE' if coverage<.5 else None}


def features(event, preoff_runners=None, table=None):
    data=parse_ranked_tier(event,table)
    data.update(opposition_features(preoff_runners))
    return data


def audit_archive(database,reference,out,first='2022-01-01',last='2025-12-31'):
    """Audit exact historical IDs, no historical outcomes or model training."""
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    refs={}
    with open(reference,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            d=r.get('date','')
            if first<=d<=last and r.get('stage2_race_id'):
                refs[r['stage2_race_id']]=(d,r.get('region_scope'),r.get('course'),r.get('race_type'),r.get('race_name'))
    counts=Counter();samples=[]
    with sqlite3.connect('file:'+str(pathlib.Path(database).resolve())+'?mode=ro',uri=True) as db:
        rs=db.execute('SELECT id,race_date,course,race_type,surface,race_class FROM historical_races WHERE race_date>=? AND race_date<=? ORDER BY race_date,id',(first,last))
        for rid,date,course,rtype,surface,rclass in rs:
            if rid not in refs:counts['without_exact_reference']+=1;continue
            rd,region,rcourse,rrtype,rname=refs[rid]
            if date!=rd or re.sub('[^A-Z0-9]','',str(course).upper())!=re.sub('[^A-Z0-9]','',str(rcourse).upper()):
                counts['date_course_conflict']+=1;continue
            o=features({'jurisdiction':region,'race_date':date,'race_name':rname,'race_type':rrtype or rtype,'race_class':rclass,'surface':surface})
            counts['verified_races']+=1
            counts['country_'+o['jurisdiction']]+=1
            counts['classification_'+o['classification_code']]+=1
            counts['unknown_races' if o['classification_code']=='UNKNOWN' else 'known_races']+=1
            if len(samples)<3000:
                samples.append({'date':date,'course':course,'race_name':rname,'race_class':rclass,**o})
    report={'reference_version':'1.0.0','date_range':[first,last],'audit':dict(counts),
            'unknown_fraction':round(counts['unknown_races']/max(1,counts['verified_races']),4),
            'warning':'Classification coverage is not model accuracy. Numeric strength is provisional and not trained.',
            'live_updates':False}
    (out/'race-strength-coverage-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    if samples:
        keys=list(samples[0]);
        with (out/'race-strength-classified-samples.csv').open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in row.items()} for row in samples)
    return report


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--race-name',default='');a.add_argument('--race-type',default='')
    a.add_argument('--race-class',default='');a.add_argument('--jurisdiction',default='GB')
    a.add_argument('--date',default='2026-11-15');a.add_argument('--surface',default='')
    a.add_argument('--audit-db',type=pathlib.Path);a.add_argument('--reference',type=pathlib.Path)
    a.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage135'))
    a.add_argument('--start',default='2022-01-01');a.add_argument('--end',default='2025-12-31')
    z=a.parse_args()
    if z.audit_db:
        if not z.reference:raise SystemExit('--reference CSV required with --audit-db')
        print(json.dumps(audit_archive(z.audit_db,z.reference,z.out,z.start,z.end),indent=2))
    else:
        e={'race_name':z.race_name,'race_type':z.race_type,'race_class':z.race_class,
           'jurisdiction':z.jurisdiction,'date':z.date,'surface':z.surface}
        print(json.dumps(features(e),indent=2))

if __name__=='__main__':main()
