#!/usr/bin/env python3
"""Stage81: reconcile GB/IE historical race category and discipline evidence.

Reads ONLY the Stage80 research overlay; outputs a SEPARATE research overlay.
No API calls, no current form/racecards/selections or original DB mutations.
Unknown racecourses and disputed source metadata are not promoted.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, re, sqlite3, tempfile

# Known UK and Irish thoroughbred racing venues, including historic closed venues.
# Unknown venues are REVIEW, never presumed foreign or domestic.
# Northern Ireland races are under the Irish racing jurisdiction in this analysis.
GB = '''Aintree|Ascot|Ayr|Bangor-on-Dee|Bath|Beverley|Brighton|Carlisle|Cartmel|Catterick|Catterick Bridge|Chelmsford|Chelmsford City|Cheltenham|Chepstow|Chester|Doncaster|Epsom|Epsom Downs|Exeter|Fakenham|Ffos Las|Folkestone|Fontwell|Goodwood|Hamilton|Hamilton Park|Haydock|Haydock Park|Hereford|Hexham|Huntingdon|Kelso|Kempton|Kempton Park|Leicester|Lingfield|Lingfield Park|Ludlow|Market Rasen|Musselburgh|Newbury|Newcastle|Newmarket|Newton Abbot|Nottingham|Perth|Plumpton|Pontefract|Redcar|Ripon|Salisbury|Sandown|Sandown Park|Sedgefield|Southwell|Stratford|Stratford-on-Avon|Taunton|Thirsk|Towcester|Uttoxeter|Warwick|Wetherby|Wincanton|Windsor|Wolverhampton|Worcester|Yarmouth|Great Yarmouth|York|Great Leighs'''
IE = '''Ballinrobe|Bellewstown|Clonmel|Cork|Curragh|The Curragh|Down Royal|Downpatrick|Dundalk|Fairyhouse|Galway|Gowran Park|Kilbeggan|Killarney|Laytown|Leopardstown|Limerick|Listowel|Mallow|Naas|Navan|Phoenix Park|Punchestown|Roscommon|Sligo|Thurles|Tipperary|Tralee|Tramore|Wexford'''

def norm(x): return re.sub(r'[^a-z0-9]','',str(x or '').lower())
UK_SET={norm(x) for x in GB.split('|')}
IRE_SET={norm(x) for x in IE.split('|')}
assert not (UK_SET & IRE_SET), 'Ambiguous course in both jurisdictions'
FOREIGN_CODES = set('FR AUS USA CAN GER HK HKG BHR QA UAE ITY ITA ARG URU JER SWI SWE SAF RSA JPN NZ NZL BRA BRZ BEL SIN KOR CHN CHI TUR CZE POL HUN SPA ESP DEN NOR NED NLD PER MEX MOR IND KSA SAU QAT BHR ZAF ZIM ZWE'.split())
HOME_CODES = set('GB ENG UK SCO WAL IRE IRL NI NIR'.split())
IGNORABLE = set('AW ALL WEATHER RH JULY NH TURF'.split())
PAREN = re.compile(r'\(([^()]*)\)')

def region(course):
    text=str(course or '').strip()
    annotations=[m.group(1).strip().upper() for m in PAREN.finditer(text)]
    foreign=[x for x in annotations if x in FOREIGN_CODES]
    home=[x for x in annotations if x in HOME_CODES]
    base=norm(PAREN.sub('',text))
    expected='GB' if base in UK_SET else ('IE' if base in IRE_SET else '')
    if foreign:
        return ('FOREIGN','EXPLICIT_FOREIGN_MARKER',base)
    if home:
        explicit='GB' if set(home)&set(['GB','ENG','UK','SCO','WAL']) else 'IE'
        if len({('GB' if x in {'GB','ENG','UK','SCO','WAL'} else 'IE') for x in home})>1:
            return ('REVIEW','CONFLICTING_COUNTRY_MARKERS',base)
        if expected and expected!=explicit:
            return ('REVIEW','MARKER_VENUE_DISAGREEMENT',base)
        if expected:return (expected,'COURSE_AND_MARKER',base)
        return ('REVIEW','UNRECOGNISED_VENUE_WITH_HOME_MARKER',base)
    if expected:return (expected,'VERIFIED_VENUE_WHITELIST',base)
    return ('REVIEW','UNRECOGNISED_VENUE',base)

ABBREV_RULES={
 'handicap':re.compile(r'\b(?:h[.]?cap|h[.]?cp|hdcp|hanidcap|handi[ -]?cap|hcap|handcap|handicap)\b',re.I),
 'novice':re.compile(r'\b(?:nov[.]?|novs[.]?|novices[.]?|novice|novice[s’\']?)\b',re.I),
 'maiden':re.compile(r'\b(?:mdn[.]?|maiden[s]?)\b',re.I),
 'bumper':re.compile(r'\b(?:i[.]?n[.]?h[.]?|n[.]?h[.]?|nh)\s*(?:flat|fl[.]?)\b|\bbumper\b|\bnational\s+hunt\s+flat\b',re.I),
 'hunter_chase':re.compile(r'\b(?:hunters?\s*(?:chase|chse)|hunterschase|hunterschase|foxhunters?\s+chase|foxhunters?\s+trophy)\b',re.I),
 'beginner':re.compile(r'\b(?:beginners?\s*(?:chase|chse)|beg[.]?\s*chase)\b',re.I),
 'seller':re.compile(r'\b(?:selling|sellers?)\b',re.I),
 'claimer':re.compile(r'\b(?:claiming|claimers?)\b',re.I),
 'listed':re.compile(r'\blisted\b',re.I),
}
HURDLE_WORD = re.compile(r'\b(?:hurdles?|hdle|hdles|hdl|hdls|hrdle|hrdles|hurd|hurd[.]|hdle[.]|h[.]?dle)\b',re.I)
CHASE_WORD = re.compile(r'\b(?:chase|chases|chasers|chse|chses|steeplechase|hunterschase|hunterschase|foxhunters?)\b',re.I)
NON_HANDICAP = re.compile(r'\bnon[ -]?handicap\b',re.I)
GRADE_SHORT = re.compile(r'\b(?:grd|grade|gr|group)[.]?\s*(?:[123]|I|II|III)\b',re.I)

# Derive a structured condition from source name; do NOT infer from a sponsor,
# handicap mark, RPR, or ambiguous named historical races.
def refine(name,source_type,old_disc,old_primary,old_flags,old_status):
    title=str(name or '');typ=str(source_type or '');txt=title+' '+typ
    flags=set(old_flags)
    explicit=set()
    for key,rule in ABBREV_RULES.items():
        if rule.search(txt): explicit.add(key)
    if NON_HANDICAP.search(txt):
        explicit.discard('handicap')
        flags.discard('handicap')
    flags|=explicit
    if 'hunter_chase' in explicit and not any(x in explicit for x in ('handicap','novice')):
        flags.add('hunter_chase')
    if 'bumper' in explicit:disc='nh_flat'
    elif CHASE_WORD.search(title):disc='chase'
    elif HURDLE_WORD.search(title):disc='hurdle'
    else:disc=old_disc
    conflicting=bool((CHASE_WORD.search(title) and HURDLE_WORD.search(title)) or
        (disc=='nh_flat' and (CHASE_WORD.search(title) or HURDLE_WORD.search(title))))
    # Existing detailed category has precedence; only upgrade where original
    # name was insufficient or a more-specific, explicit overlapping condition
    # is present. A race is never assumed non-handicap because no handicap word.
    primary=old_primary
    if 'handicap' in flags: primary='handicap'
    elif primary=='unclassified':
        for candidate in ('maiden','novice','beginner','bumper','hunter_chase','seller','claimer','listed'):
            if candidate in flags:
                primary=candidate;break
    grade='' if not GRADE_SHORT.search(title) else 'explicit_grade_abbreviation'
    reasons=[]
    if primary!=old_primary:reasons.append('DETAIL_CATEGORY_FROM_SOURCE_ABBREVIATION')
    if disc!=old_disc:reasons.append('DISCIPLINE_FROM_EXPLICIT_RACE_NAME')
    if flags!=set(old_flags):reasons.append('EXPLICIT_OVERLAPPING_TAGS')
    if grade:reasons.append('GRADE_WORDING_FOR_REVIEW')
    if conflicting:reasons.append('INCONSISTENT_DISCIPLINE_WORDING')
    if old_status.startswith('REVIEW'):
        return ('unclassified','ambiguous',[], 'REVIEW_ORIGINAL_METADATA_CONFLICT','ORIGINAL_STAGE80_REVIEW')
    if conflicting:return ('unclassified','ambiguous',[], 'REVIEW_DISCIPLINE_CONFLICT','INCONSISTENT_SOURCE_TITLE')
    if primary=='unclassified':status='SOURCE_NOT_SPECIFIC_ENOUGH'
    elif primary!=old_primary or disc!=old_disc:status='EXPLICIT_ABBREVIATION'
    else:status='EXPLICIT_SOURCE'
    return (primary,disc,sorted(flags),status,';'.join(reasons) if reasons else 'STAGE80_SOURCE')

def write_csv(p,rows,cols):
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rows)

def build(source,out,limit=0):
    out.mkdir(parents=True,exist_ok=True)
    cx=sqlite3.connect('file:'+source.resolve().as_posix()+'?mode=ro',uri=True)
    cx.execute('pragma query_only=on')
    if cx.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('Source overlay SQLite is corrupt')
    found=cx.execute('select count(*) from historical_race_categories').fetchone()[0]
    if found<500000 and not limit:raise RuntimeError('Stage80 source overlay too small')
    outdb=sqlite3.connect(out/'stage81_ukie_category_research.sqlite')
    outdb.executescript('''PRAGMA journal_mode=DELETE;
        CREATE TABLE category_reconciliation (
          race_key TEXT PRIMARY KEY,race_date TEXT,course TEXT,source_race_id TEXT,
          region TEXT,region_evidence TEXT,runner_rows INTEGER,
          race_name TEXT,source_type TEXT,source_pattern TEXT,source_class TEXT,
          original_category TEXT,final_category TEXT,original_discipline TEXT,final_discipline TEXT,
          flags_json TEXT,classification_status TEXT,evidence_reason TEXT,
          original_evidence_status TEXT);
        CREATE INDEX ix_region ON category_reconciliation(region);
        CREATE INDEX ix_year_region ON category_reconciliation(race_date,region);
        CREATE INDEX ix_category ON category_reconciliation(final_category);
        CREATE TABLE audit_metadata (key TEXT PRIMARY KEY,value TEXT);
    ''')
    stats=collections.Counter();by_year=collections.defaultdict(collections.Counter)
    by_type=collections.defaultdict(collections.Counter)
    by_disc=collections.defaultdict(collections.Counter)
    venue=collections.Counter(); unknown_venue=collections.Counter(); changed_samples=[]; gap_samples=[]; conflict_samples=[]; foreign_samples=[]
    srcquery='''SELECT race_key,race_date,course,source_race_id,runner_rows,race_name,source_type,source_pattern,source_class,
        discipline,surface,primary_category,flags_json,pattern_grade,evidence_status
        FROM historical_race_categories ORDER BY race_date,race_key'''
    sql='INSERT INTO category_reconciliation VALUES('+','.join('?'*19)+')'
    batch=[]
    for row in cx.execute(srcquery):
        key,day,course,rid,n,title,typ,pattern,klass,disc,surf,category,flags_json,pat_grade,status=row
        region_value,region_evidence,_=region(course)
        try:flags=json.loads(flags_json or '[]');flags=flags if isinstance(flags,list) else []
        except ValueError:flags=[]
        newcat,newdisc,newflags,newstat,reason=refine(title,typ,disc,category,flags,status)
        if region_value not in ('GB','IE'):
            newstat=region_evidence if region_value=='REVIEW' else 'OUT_OF_SCOPE_FOREIGN';
        race_year=str(day or '')[:4]
        rec=(key,day,course,rid,region_value,region_evidence,n,title,typ,pattern,klass,
             category,newcat,disc,newdisc,json.dumps(newflags,ensure_ascii=False),newstat,reason,status)
        batch.append(rec)
        stats['all_races']+=1;stats['all_runner_rows']+=int(n or 0)
        if region_value in ('GB','IE'):
            stats['ukie_races']+=1;stats['ukie_runner_rows']+=int(n or 0)
            stats['region_'+region_value+'_races']+=1
            z=by_year[(race_year,region_value)];z['races']+=1;z['runner_rows']+=int(n or 0)
            if category!='unclassified' and status=='EXPLICIT_SOURCE':z['stage80_explicit_races']+=1
            if newcat!='unclassified' and newstat in ('EXPLICIT_SOURCE','EXPLICIT_ABBREVIATION'):
                z['classified_races']+=1;stats['ukie_classified_races']+=1
            else:
                z['unclassified_or_review_races']+=1;stats['ukie_unclassified_or_review']+=1
                if len(gap_samples)<2000:gap_samples.append(dict(year=race_year,region=region_value,date=day,course=course,race_id=rid,race_name=title,source_type=typ,reason=newstat))
            if newstat=='EXPLICIT_ABBREVIATION':
                stats['ukie_abbreviation_enriched_races']+=1
                if len(changed_samples)<2000:changed_samples.append(dict(year=race_year,region=region_value,course=course,race_id=rid,race_name=title,old_category=category,new_category=newcat,old_discipline=disc,new_discipline=newdisc,reason=reason))
            if newcat!=category:stats['ukie_primary_category_changed']+=1
            if newdisc!=disc:stats['ukie_discipline_changed']+=1
            by_type[(region_value,race_year)][newcat]+=1
            by_disc[(region_value,race_year)][newdisc]+=1
            if newstat.startswith('REVIEW'):
                stats['ukie_review_races']+=1
                if len(conflict_samples)<2000:conflict_samples.append(dict(region=region_value,date=day,course=course,race_id=rid,race_name=title,old_discipline=disc,new_discipline=newdisc,status=newstat,source_type=typ))
        elif region_value=='FOREIGN':
            stats['foreign_races']+=1
            if len(foreign_samples)<1000:foreign_samples.append(dict(date=day,course=course,race_id=rid,region_evidence=region_evidence))
        else:
            stats['review_region_races']+=1
            unknown_venue[course]+=1
        venue[(region_value,course)]+=1
        if len(batch)>=15000:
            outdb.executemany(sql,batch);outdb.commit();batch.clear()
        if limit and stats['all_races']>=limit:break
    if batch:outdb.executemany(sql,batch);outdb.commit()
    years=[]
    for (year,r),v in sorted(by_year.items()):
        n=v['races']
        years.append(dict(year=year,region=r,**v,classification_coverage_pct=round(100*v['classified_races']/n,3) if n else 0,stage80_coverage_pct=round(100*v['stage80_explicit_races']/n,3) if n else 0))
    write_csv(out/'ukie_coverage_by_year.csv',years,['year','region','races','runner_rows','stage80_explicit_races','classified_races','unclassified_or_review_races','classification_coverage_pct','stage80_coverage_pct'])
    write_csv(out/'ukie_category_by_year.csv',[dict(region=r,year=y,category=k,races=n) for (r,y),counts in sorted(by_type.items()) for k,n in sorted(counts.items())],['region','year','category','races'])
    write_csv(out/'ukie_discipline_by_year.csv',[dict(region=r,year=y,discipline=k,races=n) for (r,y),counts in sorted(by_disc.items()) for k,n in sorted(counts.items())],['region','year','discipline','races'])
    write_csv(out/'venue_region_inventory.csv',[dict(region=r,course=c,races=n) for (r,c),n in sorted(venue.items(),key=lambda z:(z[0][0],-z[1],z[0][1]))],['region','course','races'])
    write_csv(out/'unknown_venue_review.csv',[dict(course=k,races=v) for k,v in unknown_venue.most_common()],['course','races'])
    write_csv(out/'newly_recognized_sample.csv',changed_samples,['year','region','course','race_id','race_name','old_category','new_category','old_discipline','new_discipline','reason'])
    write_csv(out/'remaining_gaps_sample.csv',gap_samples,['year','region','date','course','race_id','race_name','source_type','reason'])
    write_csv(out/'conflicts_sample.csv',conflict_samples,['region','date','course','race_id','race_name','old_discipline','new_discipline','status','source_type'])
    write_csv(out/'excluded_foreign_sample.csv',foreign_samples,['date','course','race_id','region_evidence'])
    covered=stats['ukie_classified_races'];tot=stats['ukie_races']
    summary={'stage':81,'mode':'READ_ONLY_RESEARCH_OVERLAY','created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
      'source':'Stage80 research category overlay, generated from Stage65E original Kaggle raw_json',
      'source_races_scanned':stats['all_races'],'source_runner_rows_scanned':stats['all_runner_rows'],
      'uk_ie_races':tot,'uk_ie_runner_rows':stats['ukie_runner_rows'],
      'uk_races':stats['region_GB_races'],'irish_races':stats['region_IE_races'],
      'foreign_races_excluded':stats['foreign_races'],'unknown_region_races_for_review':stats['review_region_races'],
      'uk_ie_confidently_classified_races':covered,
      'uk_ie_unclassified_or_review':stats['ukie_unclassified_or_review'],
      'uk_ie_classification_coverage_pct':round(100*covered/tot,3) if tot else 0,
      'uk_ie_abbreviation_enriched_races':stats['ukie_abbreviation_enriched_races'],
      'uk_ie_primary_category_changed':stats['ukie_primary_category_changed'],
      'uk_ie_discipline_changed':stats['ukie_discipline_changed'],
      'uk_ie_conflict_review':stats['ukie_review_races'],
      'no_external_api_calls':True,'source_database_modified':False,'frozen_model_d_modified':False,
      'uk_irish_course_rules':'Conservative known-venue whitelist incl. historic tracks. Northern Ireland grouped under Irish racing jurisdiction. Foreign country codes override matching venue names.',
      'classification_policy':'Only explicit source title/type words, including documented common abbreviations. Unrecognised/ambiguous races remain unclassified, and no numeric Class is manufactured.',
      'limitations':['Stage80 source classifications may contain mistakes; this is text-evidence extraction, not full third-party verification',
                     'Original source includes foreign races; ambiguous venue records excluded from UK/IE until external verification',
                     'A named race without a handicap/maiden/novice marker is not assigned an inferred condition',
                     'No SP, RPR, historical live model, odds or real bets modified or recovered']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    outdb.execute('INSERT OR REPLACE INTO audit_metadata VALUES (?,?)',('summary',json.dumps(summary)))
    outdb.commit();outdb.close();cx.close()
    print(json.dumps(summary,indent=2),flush=True)
    return summary

def self_test():
    assert region('Ascot (AUS)')[0]=='FOREIGN'
    assert region('Ascot')[0]=='GB'
    assert region('Ascot (IRE)')[0]=='REVIEW'
    assert region('Leopardstown (IRE)')[0]=='IE'
    assert region('Wexford (RH) (IRE)')[0]=='IE'
    assert region('Auteuil')[0]=='REVIEW'
    assert region('Auteuil (FR)')[0]=='FOREIGN'
    assert region('Phoenix Park')[0]=='IE'
    assert region('Tralee')[0]=='IE'
    assert region('Towcester')[0]=='GB'
    assert refine('Seagram Nov Hdle','Flat','flat','unclassified',[],'SOURCE_NOT_SPECIFIC_ENOUGH')[1]=='hurdle'
    assert refine('I.N.H. Flat Race','Flat','flat','unclassified',[],'SOURCE_NOT_SPECIFIC_ENOUGH')[1]=='nh_flat'
    assert refine('Maiden Hcap Hurdle','Hurdle','hurdle','maiden',['maiden'],'EXPLICIT_SOURCE')[0]=='handicap'
    assert refine('Hunterschase','Flat','flat','unclassified',[],'SOURCE_NOT_SPECIFIC_ENOUGH')[1]=='chase'
    assert refine('Non-handicap Novice Chase','Chase','chase','novice',['novice'],'EXPLICIT_SOURCE')[0]=='novice'
    assert refine('Cheltenham Trial Hurdle','Hurdle','hurdle','unclassified',[],'SOURCE_NOT_SPECIFIC_ENOUGH')[0]=='unclassified'
    assert refine('Maiden Hurdle','Hurdle','hurdle','unclassified',[],'REVIEW_CONFLICT')[0]=='unclassified'
    with tempfile.TemporaryDirectory() as td:
        f=pathlib.Path(td)/'input.sqlite';cx=sqlite3.connect(f)
        cx.execute('''CREATE TABLE historical_race_categories(race_key TEXT,race_date TEXT,course TEXT,source_race_id TEXT,runner_rows INTEGER,race_name TEXT,source_type TEXT,source_pattern TEXT,source_class TEXT,discipline TEXT,surface TEXT,primary_category TEXT,flags_json TEXT,pattern_grade TEXT,evidence_status TEXT,evidence_json TEXT)''')
        rows=[('a','2020-01-01','Ascot', '1',8,'Nov Hdle','Flat','','','flat','','unclassified','[]','','SOURCE_NOT_SPECIFIC_ENOUGH','{}'),
              ('b','2020-01-01','Ascot (AUS)','2',9,'Maiden Flat','Flat','','','flat','','maiden','["maiden"]','','EXPLICIT_SOURCE','{}'),
              ('c','2020-01-01','Auteuil','3',10,'Nov Hdle','Hurdle','','','hurdle','','unclassified','[]','','SOURCE_NOT_SPECIFIC_ENOUGH','{}'),
              ('d','2020-01-01','Tralee','4',6,'I.N.H. Flat','Flat','','','flat','','unclassified','[]','','SOURCE_NOT_SPECIFIC_ENOUGH','{}')]
        cx.executemany('INSERT INTO historical_race_categories VALUES ('+','.join('?'*16)+')',rows);cx.commit();cx.close()
        r=build(f,pathlib.Path(td)/'out',limit=4)
        assert r['uk_ie_races']==2 and r['foreign_races_excluded']==1 and r['unknown_region_races_for_review']==1,r
        assert r['uk_ie_confidently_classified_races']==2,r
    print('PASS: region precedence, unknown venue, historic NH abbreviations, overlapping categories, immutable source')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=pathlib.Path)
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage81'))
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test();return
    if not a.source:raise SystemExit('Missing --source Stage80 SQLite overlay')
    s=build(a.source,a.out)
    if s['uk_ie_races']<250000:raise RuntimeError('UK/IE identification unexpectedly small: review list and source')

if __name__=='__main__':main()
