#!/usr/bin/env python3
"""Stage133: race-name/class taxonomy audit and strict chronological HPR v0 vs v3.1 replay.

Read only. Historic results/SP used for settlement ONLY, never scoring.
UK/Irish classification is separate from race type and from the proposed rating proxy.
HPR records include non-UK/IE runs, which may inform past form but are not evaluated
as UK/IE target races unless exact historical reference confirms country.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, re, sqlite3, statistics

VERSION='stage133-class-type-hpr-2026-11-15-research-v1'
V31='HPR-v3.1-class-weight-exact-evidence-2026-11-15-research'
COUNTRIES={'GB','IRELAND'}
NR={'nr','nonrunner','non-runner','withdrawn','scratched','dns','non runner'}


def number(x):
    try:
        v=float(x);return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None


def norm(x):
    import unicodedata
    z=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().upper()
    return re.sub('[^A-Z0-9]','',z)


def parse(title,metadata='',country='UNKNOWN'):
    """Labels are evidence, not prediction: race type != class.

    Mark 0-120 as handicap eligibility, not official UK Class N. Grades 1-3
    and Listed are distinguishable from Irish Grade A/B handicaps.
    """
    s=' '.join(str(x or '') for x in (title,metadata));country=country.upper()
    cls=re.search(r'\bclass\s*([1-6])\b',s,re.I)
    group=re.search(r'\b(?:group|grade|gr)\s*([1-3])\b',s,re.I)
    listed=bool(re.search(r'\blisted\b',s,re.I))
    gra=re.search(r'\bgrade\s*([ABC])\b',s,re.I)
    band=re.search(r'(?<!\d)(\d{1,3})\s*[-–]\s*(\d{2,3})(?!\d)',s)
    band_value=None
    if band:
        lo,hi=map(int,band.groups())
        if 0<=lo<hi<=170 and hi-lo<=140:band_value=f'{lo}-{hi}'
    flat=bool(re.search(r'\b(?:nh\s*flat|inh\s*flat|national\s*hunt\s*flat|bumper)\b',s,re.I))
    if flat:code='NH_FLAT'
    elif re.search(r'\bchase\b|\bsteeplechase\b',s,re.I):code='CHASE'
    elif re.search(r'\bhurdle\b|\bhurdles\b',s,re.I):code='HURDLE'
    elif re.search(r'\bflat\b|\bstakes\b|\bmaiden\b|\bnursery\b',s,re.I):code='FLAT'
    else:code='UNKNOWN'
    if re.search(r'\bhandicap\b|\bhcap\b|\bnursery\b',s,re.I):race_type='Handicap'
    elif re.search(r'\bmaiden\b',s,re.I):race_type='Maiden'
    elif re.search(r'\bnovice(?:s|\b)\b|\bnovices\b',s,re.I):race_type='Novice'
    elif flat:race_type='NH Flat'
    elif re.search(r'\bconditions\b',s,re.I):race_type='Conditions'
    else:race_type='Unspecified'
    if group:tier='GRADE_GROUP_'+group.group(1)
    elif listed:tier='LISTED'
    elif gra:tier='IRISH_HANDICAP_GRADE_'+gra.group(1) # NOT a Group/Grade 1-3
    elif cls:tier='UK_CLASS_'+cls.group(1)
    else:tier='UNSPECIFIED'
    if cls:class_status='EXPLICIT_CLASS_'+cls.group(1)
    elif country=='GB':class_status='UK_CLASS_NOT_RECORDED'
    elif country=='IRELAND':class_status='IRISH_UK_CLASS_NOT_APPLICABLE'
    else:class_status='FOREIGN_OR_UNVERIFIED_UK_CLASS_NOT_APPLICABLE'
    return {'country':country,'race_code':code,'race_type':race_type,
            'explicit_uk_class':int(cls.group(1)) if cls else None,
            'race_tier':tier,'handicap_eligibility_or_band':band_value,
            'class_status':class_status,
            'class_evidence':'TITLE_OR_RECORDED_METADATA' if tier!='UNSPECIFIED' else 'NONE',
            'race_type_evidence':'TITLE_OR_RECORDED_METADATA' if race_type!='Unspecified' else 'NONE'}


def safe_hpr_source(path):
    cx=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
    cx.row_factory=sqlite3.Row
    if cx.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('HPR file integrity check failed')
    v={r[0] for r in cx.execute('SELECT DISTINCT model_version FROM hpr_ratings')}
    if v!={V31}:raise RuntimeError(f'Unexpected HPR version {v}; expected {V31}')
    return cx


def load_reference(path):
    import stage108_model_g2 as G
    reference,_=G.load_reference(path,first='1988-01-01',last='2026-12-31')
    return {str(k):v for k,v in reference.items()}


def audit(ref,history,store,out):
    out.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect('file:'+str(history.resolve())+'?mode=ro',uri=True)
    cx.row_factory=sqlite3.Row
    if cx.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Archive failed integrity check')
    summary=collections.Counter();types=collections.Counter();samples=[]
    outpath=out/'stage133-verified-class-and-type-audit.csv'
    header=['date','race_id','course','country','race_title','source_class','source_type','race_code',
            'race_type','explicit_uk_class','race_tier','handicap_eligibility_or_band','class_status','class_evidence']
    with outpath.open('w',newline='',encoding='utf-8') as f:
        wr=csv.DictWriter(f,fieldnames=header);wr.writeheader()
        for r in cx.execute('SELECT id,race_date,course,race_class,race_type FROM historical_races'):
            meta=ref.get(str(r['id']))
            if not meta:summary['historical_races_no_exact_ref']+=1;continue
            date,course,type_from_ref,title,country=meta
            if date!=r['race_date'] or norm(course)!=norm(r['course']):
                summary['exact_reference_date_course_conflict']+=1;continue
            if country not in COUNTRIES:summary['excluded_non_ukie_reference']+=1;continue
            d=parse(title,' '.join(str(z or '') for z in (r['race_class'],r['race_type'],type_from_ref)),country)
            row={'date':date,'race_id':r['id'],'course':course,'country':country,
                'race_title':title,'source_class':r['race_class'],'source_type':r['race_type'],**d}
            wr.writerow({k:row.get(k) for k in header})
            summary[f'{country}_races']+=1
            summary[f'{country}_explicit_uk_class']+=d['explicit_uk_class'] is not None
            summary[f'{country}_explicit_tier']+=d['race_tier']!='UNSPECIFIED'
            summary[f'{country}_rated_band']+=d['handicap_eligibility_or_band'] is not None
            summary[f'{country}_race_type_known']+=d['race_type']!='Unspecified'
            types[(country,d['race_code'],d['race_type'])]+=1
            if len(samples)<25 and d['explicit_uk_class'] is None and country=='IRELAND':
                samples.append({'race_title':title,'race_type':d['race_type'],'class_status':d['class_status']})
    cx.close()
    hc=safe_hpr_source(store)
    status=collections.Counter();unclassified=collections.Counter();foreign_example=[]
    for (raw,) in hc.execute('SELECT payload FROM hpr_ratings'):
        x=json.loads(raw)
        rk=str(x.get('race_key') or '')
        refrow=ref.get(rk[5:]) if rk.startswith('HIST|') else None
        if refrow:
            date,course,code,title,country=refrow
            # An exact race id alone is not enough if the HPR row disagrees on date/course.
            if date!=str(x.get('date')) or norm(course)!=norm(x.get('course')):
                status['hpr_reference_conflict']+=1;continue
            value=parse(title,' '.join(str(k or '') for k in (x.get('race_class'),code,x.get('race_type'))),country)
        else:
            explicit_country='IRELAND' if re.search(r'\(IRE\)',str(x.get('course')),re.I) else (
                'FOREIGN' if re.search(r'\((FR|UAE|GER|USA|AUS|ITY|JPN)\)',str(x.get('course')),re.I) else 'UNVERIFIED')
            value=parse(x.get('race_name'),x.get('race_type'),explicit_country)
        country=value['country'];status[f'hpr_{country}']+=1
        if x.get('class_label')=='UNKNOWN':
            status[f'unknown_class_{country}']+=1
            if value['class_status']=='IRISH_UK_CLASS_NOT_APPLICABLE':status['original_unknown_irish_nonapplicable']+=1
            if value['race_type']!='Unspecified':status['unknown_class_but_race_type_recoverable']+=1
        if country=='FOREIGN' and len(foreign_example)<10:
            foreign_example.append({'course':x.get('course'),'title':x.get('race_name') or x.get('race_type')})
    hc.close()
    with (out/'stage133-classification-by-code.csv').open('w',newline='',encoding='utf-8') as f:
        wr=csv.writer(f);wr.writerow(['country','race_code','race_type','races']);
        for (country,code,kind),n in sorted(types.items()):wr.writerow([country,code,kind,n])
    report={'version':VERSION,'archive':dict(summary),'hpr':dict(status),
            'irish_missing_class_examples':samples,'foreign_history_examples':foreign_example,
            'method':'Official UK class only when explicit; Irish type and Grade/Listed retained without invented British class.',
            'no_live_updates':True}
    (out/'stage133-audit-summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    return report


def parse_sp(value):
    s=str(value or '').strip()
    if not s:return None
    try:
        if '/' in s:
            a,b=s.split('/',1);return 1+float(a)/float(b)
        v=float(s);return v if v>1 else None
    except (TypeError,ValueError,ZeroDivisionError):return None


def race_code(e):
    s=' '.join(str(e.get(k) or '') for k in ('race_type','race_name'))
    x=parse(s)['race_code']
    if x=='UNKNOWN':
        # G2 records short discipline labels in older archive.
        if re.fullmatch(r'\s*flat\s*',s,re.I):return 'FLAT'
    return x


def weighted_hpr(prior,version_key):
    last=prior[-3:]
    if not last:return None
    vals=[number(e.get(version_key)) for e in last]
    if any(v is None for v in vals):return None
    weights=list(range(1,len(vals)+1))
    return sum(a*b for a,b in zip(vals,weights))/sum(weights)


def _runners_archive(cx,rid):
    return [dict(r) for r in cx.execute('''SELECT runner_key,horse_source_id,horse_name,sire,dam,finish_pos,outcome,sp_decimal
           FROM historical_runs WHERE race_id=?''',(rid,))]


def match_runner(e,archive):
    # Exact ID or explicit four-part pedigree only. Never match name alone.
    hid=str(e.get('horse_key') or '').split('|')
    if not hid:return None
    if len(hid)==2 and hid[0]=='ID':
        matches=[r for r in archive if str(r.get('horse_source_id') or '')==hid[1]]
    elif len(hid)==4 and hid[0]=='P':
        matches=[r for r in archive if norm(r.get('horse_name'))==hid[1] and norm(r.get('sire'))==hid[2] and norm(r.get('dam'))==hid[3]]
    else:return None
    return matches[0] if len(matches)==1 else None


def replay(ref,history,store,out,start='2024-01-01',end='2025-12-31'):
    """Compare old/new HPR within strictly verified complete UK/IE race fields.

    Uses only historical HPR before target DATE. All entrants must have a
    previous HPR in same code; exact winner and complete full field required.
    Historical archive odds are strictly settlement-only.
    """
    out.mkdir(parents=True,exist_ok=True)
    cx=safe_hpr_source(store);groups=collections.defaultdict(list)
    for (raw,) in cx.execute('SELECT payload FROM hpr_ratings ORDER BY race_date'):
        e=json.loads(raw);groups[(str(e['date']),str(e['race_key']))].append(e)
    cx.close()
    archive=sqlite3.connect('file:'+str(history.resolve())+'?mode=ro',uri=True)
    archive.row_factory=sqlite3.Row
    prior=collections.defaultdict(list);metrics=collections.Counter();selection=[];reject_examples=[]
    pending=[];last_date=None
    for (date,key),entries in sorted(groups.items()):
        if date!=last_date:
            for e in pending:prior[str(e['horse_key'])].append(e)
            pending=[];last_date=date
        pending.extend(entries)
        if not start<=date<=end:continue
        if not key.startswith('HIST|'):metrics['not_historical_exact_id']+=1;continue
        rid=key[5:];meta=ref.get(rid)
        if not meta:metrics['no_verified_ukie_reference']+=1;continue
        refdate,course,rtype,title,country=meta
        if country not in COUNTRIES:metrics['foreign_target_excluded']+=1;continue
        if date!=refdate or any(norm(e.get('course'))!=norm(course) for e in entries):
            metrics['date_course_identity_conflict']+=1;continue
        n_values={int(number(e.get('field_size')) or 0) for e in entries}
        if len(n_values)!=1 or (n:=next(iter(n_values)))<4 or len(entries)!=n:
            metrics['hpr_field_not_full']+=1;continue
        actual=_runners_archive(archive,rid)
        starters=[r for r in actual if str(r['outcome'] or '').lower() not in NR]
        if len(starters)!=n:
            metrics['archive_field_mismatch']+=1;continue
        matched=[match_runner(e,starters) for e in entries]
        if any(r is None for r in matched) or len({r['runner_key'] for r in matched})!=n:
            metrics['full_field_pedigree_or_id_not_verified']+=1;continue
        winners=[i for i,r in enumerate(matched) if number(r.get('finish_pos'))==1]
        if len(winners)!=1:
            metrics['no_unique_verified_winner']+=1;continue
        code=parse(title,rtype,country)['race_code']
        if code=='UNKNOWN':metrics['race_code_unknown']+=1;continue
        old=[];new=[];usable=True
        for e in entries:
            h=[r for r in prior[str(e['horse_key'])] if str(r['date'])<date and race_code(r)==code]
            if not h:usable=False;break
            old.append(weighted_hpr(h,'original_hpr_v0'))
            new.append(weighted_hpr(h,'hpr'))
        if not usable or any(x is None for x in old+new):
            metrics['same_discipline_prior_unavailable']+=1;continue
        oi=max(range(n),key=lambda i:(old[i],str(entries[i]['horse_key'])))
        ni=max(range(n),key=lambda i:(new[i],str(entries[i]['horse_key'])))
        mtype=parse(title,rtype,country)['race_type']
        typ='Handicap' if mtype=='Handicap' else 'Non-handicap' if mtype in ('Maiden','Novice','NH Flat','Conditions') else 'Unspecified'
        o_sp=parse_sp(matched[oi].get('sp_decimal'))
        n_sp=parse_sp(matched[ni].get('sp_decimal'))
        record={'date':date,'country':country,'course':course,'race_name':title,'race_id':rid,
            'discipline':code,'race_type':typ,'field_size':n,'old_rank1':entries[oi]['horse'],
            'new_rank1':entries[ni]['horse'],'old_preoff_hpr':round(old[oi],2),
            'new_preoff_hpr':round(new[ni],2),'old_win':int(oi==winners[0]),
            'new_win':int(ni==winners[0]),'same_pick':int(oi==ni),
            'old_sp_decimal':o_sp,'new_sp_decimal':n_sp,
            'paired_sp_available':int(o_sp is not None and n_sp is not None),
            'old_return':(o_sp if oi==winners[0] else 0) if o_sp is not None and n_sp is not None else None,
            'new_return':(n_sp if ni==winners[0] else 0) if o_sp is not None and n_sp is not None else None}
        selection.append(record);metrics['eligible_full_field']+=1
    archive.close()
    if not selection:
        # Still create explicit audit; don't fake successful research comparison.
        report={'version':VERSION,'status':'INSUFFICIENT_ELIGIBLE_RACES','metrics':dict(metrics),'start':start,'end':end}
        (out/'stage133-hpr-paired-audit.json').write_text(json.dumps(report,indent=2)+'\n')
        return report
    columns=list(selection[0])
    with (out/'stage133-hpr-paired-races.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(selection)
    categories=collections.defaultdict(list)
    for e in selection:
        categories[('ALL','ALL')].append(e)
        categories[(e['discipline'],e['race_type'])].append(e)
    results=[]
    for (disc,typ),rows in sorted(categories.items()):
        n=len(rows);o=sum(x['old_win'] for x in rows);v=sum(x['new_win'] for x in rows)
        settled=[x for x in rows if x['paired_sp_available']]
        roi=lambda col:round(100*(sum(x[col] for x in settled)/len(settled)-1),2) if settled else None
        results.append({'discipline':disc,'race_type':typ,'races':n,'old_winners':o,'new_winners':v,
            'old_strike_pct':round(100*o/n,2),'new_strike_pct':round(100*v/n,2),
            'changed_selections':sum(not x['same_pick'] for x in rows),'paired_sp_count':len(settled),
            'old_sp_roi_pct':roi('old_return'),'new_sp_roi_pct':roi('new_return')})
    with (out/'stage133-hpr-by-discipline-type.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
    report={'version':VERSION,'status':'RESEARCH_ONLY','period':[start,end],
        'eligible_races':len(selection),'audit':dict(metrics),'summary':results,
        'constraints':['Complete, verified racing field with exact identity and unique winner required.',
            'Every runner must have a past HPR in the same discipline before target calendar date.',
            'Old and new scores ranked with identical last-three same-code aggregation; only rating differs.',
            'ROI uses actual historical SP on both chosen horses and only fully matched settled races.',
            'This is a sparse active-horse archive, not comprehensive all-races historical coverage.',
            'No odds, target-day results or future form used in ranking.'],
        'no_live_updates':True}
    (out/'stage133-hpr-paired-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def self_test():
    assert parse('Class 4 Handicap Hurdle','', 'GB')['explicit_uk_class']==4
    assert parse('Irish Stallion Farms EBF Maiden','', 'IRELAND')['class_status']=='IRISH_UK_CLASS_NOT_APPLICABLE'
    assert parse('Irish Stallion Farms EBF Maiden','', 'IRELAND')['race_type']=='Maiden'
    assert parse('0-120 Handicap Chase','', 'IRELAND')['handicap_eligibility_or_band']=='0-120'
    assert parse('Rated Novice Hurdle','', 'IRELAND')['race_type']=='Novice'
    assert parse('Grade A Handicap Hurdle','', 'IRELAND')['race_tier']=='IRISH_HANDICAP_GRADE_A'
    assert parse('Grade A Handicap Hurdle','', 'IRELAND')['race_tier']!='GRADE_GROUP_1'
    assert parse('Listed Juvenile Hurdle','', 'GB')['race_tier']=='LISTED'
    assert parse('National Hunt Flat Race','', 'IRELAND')['race_code']=='NH_FLAT'
    assert parse('0-120 Handicap','', 'IRELAND')['explicit_uk_class'] is None
    assert weighted_hpr([{'hpr':50},{'hpr':70},{'hpr':90}],'hpr')== (50+140+270)/6
    assert match_runner({'horse_key':'P|FAKE|SIRE|DAM'},[{'runner_key':'1','horse_name':'Fake','sire':'Sire','dam':'Dam'}]) is not None
    assert match_runner({'horse_key':'P|FAKE|SIRE|DAM'},[{'runner_key':'1','horse_name':'Fake','sire':'Wrong','dam':'Dam'}]) is None
    # Full synthetic replay: two dates, sealed older ratings, exact four-runner field.
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        home=pathlib.Path(temp); history=home/'history.sqlite'; store=home/'hpr.sqlite'
        with sqlite3.connect(history) as db:
            db.executescript("""CREATE TABLE historical_runs(race_id TEXT,runner_key TEXT,horse_source_id TEXT,
                horse_name TEXT,sire TEXT,dam TEXT,finish_pos INTEGER,outcome TEXT,sp_decimal REAL);
                CREATE TABLE historical_races(id TEXT,race_date TEXT,course TEXT,race_class TEXT,race_type TEXT);""")
            db.execute('INSERT INTO historical_races VALUES (?,?,?,?,?)',('R2','2025-02-01','Test Course','Class 4','Hurdle'))
            for j in range(4):
                db.execute('INSERT INTO historical_runs VALUES (?,?,?,?,?,?,?,?,?)',
                    ('R2',f'runner{j}',f'h{j}',f'Horse {j}','Sire','Dam',1 if j==1 else j+3,'ran',3.5 if j==1 else 5.))
        with sqlite3.connect(store) as db:
            db.execute('CREATE TABLE hpr_ratings(model_version TEXT,race_date TEXT,payload TEXT)')
            for j in range(4):
                for date,key in [('2024-02-01','R1'),('2025-02-01','R2')]:
                    row={'date':date,'race_key':'HIST|'+key,'horse_key':f'ID|h{j}',
                        'horse':f'Horse {j}','course':'Test Course','race_type':'Hurdle','field_size':4,
                        'original_hpr_v0':[90.,75.,50.,40.][j],
                        'hpr':[70.,95.,50.,40.][j]}
                    db.execute('INSERT INTO hpr_ratings VALUES (?,?,?)',(V31,date,json.dumps(row)))
        testref={'R2':('2025-02-01','Test Course','Hurdle','Class 4 Handicap Hurdle','GB')}
        outcome=replay(testref,history,store,home/'out','2025-02-01','2025-02-01')
        assert outcome['eligible_races']==1,outcome
        assert outcome['summary'][0]['old_winners']==0,outcome
        assert outcome['summary'][0]['new_winners']==1,outcome
        assert outcome['summary'][0]['paired_sp_count']==1,outcome
    print('Stage133 self-test PASS: race type vs country class, graded/Listed, weight bands, IDs, prior scores')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    sub.add_parser('self-test')
    for mode in ('audit','replay'):
        x=sub.add_parser(mode);x.add_argument('--hpr',type=pathlib.Path,required=True)
        x.add_argument('--history',type=pathlib.Path,required=True)
        x.add_argument('--reference',type=pathlib.Path,required=True)
        x.add_argument('--out',type=pathlib.Path,required=True)
        if mode=='replay':x.add_argument('--start',default='2024-01-01');x.add_argument('--end',default='2025-12-31')
    a=p.parse_args()
    if a.mode=='self-test':return self_test()
    refs=load_reference(a.reference)
    if a.mode=='audit':
        report=audit(refs,a.history,a.hpr,a.out)
        print('Stage133 audit:',json.dumps({'archive':report['archive'],'hpr':report['hpr']},indent=2))
    else:
        report=replay(refs,a.history,a.hpr,a.out,a.start,a.end)
        print('Stage133 replay:',json.dumps(report,indent=2)[:18000])

if __name__=='__main__':main()
