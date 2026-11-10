#!/usr/bin/env python3
"""Stage 80: read-only, evidence-driven race category extraction for 1988–2026.

Reads the independently verified Stage65E historical_runs database plus locally
archived 2026 result JSON. Never edits source DB, production app, selections or
Model D. Keeps UK numerical class, discipline, race conditions, and pattern grade
as separate concepts. No historical API required.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, hashlib, json, pathlib, re, sqlite3, tempfile

SRC = 'Kaggle deltaromeo UK/IE v129 / preserved Stage65E raw_json'
CONDITION_PATTERNS = {
    'handicap': r'\bhandicap(?:s)?\b|\bhandi\s*cap\b|\bhcap\b|\bhdcp\b',
    'nursery': r'\bnurser(?:y|ies)\b',
    'maiden': r'\bmaidens?\b',
    'novice': r"\bnovice(?:s|s['’])?\b|\bnovices\b",
    'beginner': r"\bbeginners?\b",
    'seller': r'\bsell(?:ing|er|ers)\b',
    'claimer': r'\bclaim(?:ing|er|ers)\b',
    'conditions': r'\bconditions?\s+(?:stakes?|race|hurdle|chase)\b',
    'auction': r'\bauction\b',
    'classified': r'\bclassified\b',
    'juvenile': r'\bjuvenile\b',
    'hunter_chase': r"\bhunters?\s+chase\b|\bhunter(?:s|s['’])?\s+steeplechase\b",
    'amateur': r'\bamateurs?\b',
    'apprentice': r'\bapprentices?\b',
    'conditional_jockey': r'\bconditional\s+jockeys?\b',
    'bumper': r'\bbumper\b|\bnational\s+hunt\s+flat\b',
    'listed': r'\blisted\b',
}
FLAGS = tuple(CONDITION_PATTERNS)
REGEX = {key: re.compile(pattern, re.I) for key,pattern in CONDITION_PATTERNS.items()}
GRADE = re.compile(r'\b(grade|group)\s*(?:[123]|I{1,3})\b', re.I)
SHORT_GRADE = re.compile(r'^G([123])$',re.I)
CLASS = re.compile(r'\bclass\s*([1-7])\b',re.I)
CH = re.compile(r'\bchase\b|\bsteeplechase\b',re.I)
HU = re.compile(r'\bhurdles?\b',re.I)
NH_FLAT = re.compile(r'\bnh\s*flat\b|\bnational\s+hunt\s+flat\b|\bbumper\b',re.I)
FLAT = re.compile(r'\bflat\b',re.I)
AW = re.compile(r'\ball[ -]?weather\b|\bpolytrack\b|\btapeta\b|\b(aw)\b',re.I)
TURF = re.compile(r'\bturf\b',re.I)
WS = re.compile(r'\s+')


def cleaned(v): return WS.sub(' ',str(v or '').strip())
def evidence(v): return cleaned(v)[:400]
def safe_json(raw):
    try:
        value=json.loads(raw) if isinstance(raw,str) else raw
        return value if isinstance(value,dict) else {}
    except (TypeError,ValueError): return {}

def unique(values):
    dedup={}
    for val in sorted(values, key=lambda x: (cleaned(x).casefold(), cleaned(x))):
        normalized=cleaned(val)
        if normalized: dedup.setdefault(normalized.casefold(), normalized)
    return sorted(dedup.values())

def classify(name,source_type,pattern,surface,course,source_class):
    """Facts only: explicit text, separate overlapping tags, no inferred non-handicap."""
    name,source_type,pattern,surface,course = [cleaned(v) for v in (name,source_type,pattern,surface,course)]
    tags={key for key,regex in REGEX.items() if regex.search(name)}
    if 'nursery' in tags:tags.add('handicap')
    # Clear race-condition wording may be carried in source type, but don't
    # mistake words like 'Flat' or 'Chase' for a detailed condition.
    for key in ('handicap','maiden','novice','beginner','bumper','listed','seller','claimer'):
        if REGEX[key].search(source_type): tags.add(key)
    if 'nursery' in tags:tags.add('handicap')
    if re.search(r'\bnon[ -]?handicap\b',name,re.I):
        tags.discard('handicap')
        tags.add('explicit_non_handicap')
    # Discipline is not a mutually exclusive race-condition category.
    subtype=source_type+' '+name
    disciplines=set()
    if NH_FLAT.search(subtype):disciplines.add('nh_flat')
    if CH.search(source_type):disciplines.add('chase')
    if HU.search(source_type):disciplines.add('hurdle')
    if not disciplines and CH.search(name):disciplines.add('chase')
    if not disciplines and HU.search(name):disciplines.add('hurdle')
    if not disciplines and FLAT.search(source_type) and not NH_FLAT.search(subtype):disciplines.add('flat')
    if not disciplines and AW.search(source_type) and not NH_FLAT.search(subtype):disciplines.add('flat')
    disc=next(iter(disciplines)) if len(disciplines)==1 else ('ambiguous' if disciplines else 'unknown')
    if disc=='nh_flat':tags.add('bumper')
    # An AW course annotation is evidence. Do not infer turf merely because AW
    # is not explicitly mentioned in the source.
    if disc=='flat':
        if AW.search(surface) or AW.search(source_type) or re.search(r'\(\s*AW\s*\)',course,re.I): surf='all_weather'
        elif TURF.search(surface): surf='turf'
        else:surf='unknown'
    else:surf='not_applicable' if disc in ('chase','hurdle','nh_flat') else 'unknown'
    groups=set()
    for text in (name,pattern):
        for m in GRADE.finditer(text):
            n=re.search(r'([123]|I{1,3})\s*$',m.group(0),re.I).group(1).upper()
            level={'I':'1','II':'2','III':'3'}.get(n,n)
            groups.add(m.group(1).lower()+' '+level)
    p=SHORT_GRADE.fullmatch(pattern)
    if p and disc in ('flat','hurdle','chase','nh_flat'):
        groups.add(('group ' if disc=='flat' else 'grade ')+p.group(1))
    if re.search(r'\blisted\b',pattern,re.I):tags.add('listed')
    # Grade / Group is pattern level, not a UK numerical race class.
    if 'handicap' in tags: primary='handicap'
    elif 'maiden' in tags: primary='maiden'
    elif 'novice' in tags: primary='novice'
    elif 'beginner' in tags: primary='beginner'
    elif groups: primary='graded_or_group'
    elif 'listed' in tags: primary='listed'
    elif 'bumper' in tags: primary='bumper'
    elif 'hunter_chase' in tags: primary='hunter_chase'
    elif 'seller' in tags: primary='seller'
    elif 'claimer' in tags: primary='claimer'
    elif 'conditions' in tags: primary='conditions'
    elif 'classified' in tags: primary='classified'
    elif 'auction' in tags: primary='auction'
    else:primary='unclassified'
    grade=next(iter(groups)) if len(groups)==1 else ('CONFLICT' if len(groups)>1 else '')
    # Existing numerical class is recorded; never manufacture from patterns.
    class_text=cleaned(source_class)
    title_classes=set(CLASS.findall(name))
    source_numeric=re.fullmatch(r'(?:Class\s*)?([1-7])',class_text,re.I)
    title_disagrees=bool(source_numeric and title_classes and title_classes!={source_numeric.group(1)})
    return dict(discipline=disc,surface=surf,primary_category=primary,flags=sorted(tags),
                pattern_grade=grade,numerical_class_original=class_text,
                title_class_candidates=sorted(title_classes),title_class_conflict=title_disagrees)


def init_overlay(path):
    cx=sqlite3.connect(path)
    cx.executescript('''
    PRAGMA journal_mode=DELETE;
    CREATE TABLE IF NOT EXISTS historical_race_categories (
      race_key TEXT PRIMARY KEY, race_date TEXT, course TEXT, source_race_id TEXT,
      runner_rows INTEGER, race_name TEXT, source_type TEXT, source_pattern TEXT,
      source_class TEXT, discipline TEXT, surface TEXT, primary_category TEXT,
      flags_json TEXT, pattern_grade TEXT, evidence_status TEXT, evidence_json TEXT);
    CREATE INDEX IF NOT EXISTS idx_hist_day ON historical_race_categories(race_date);
    CREATE INDEX IF NOT EXISTS idx_hist_cat ON historical_race_categories(primary_category);
    CREATE TABLE IF NOT EXISTS archived_recent_race_categories (
      race_key TEXT PRIMARY KEY, race_date TEXT, course TEXT, source_race_id TEXT,
      race_name TEXT, source_type TEXT, source_class TEXT, discipline TEXT,
      surface TEXT, primary_category TEXT, flags_json TEXT, pattern_grade TEXT,
      evidence_status TEXT, source_pattern TEXT);
    ''')
    return cx


def new_bucket():return dict(n=0,names=set(),types=set(),patterns=set(),classes=set(),
                             surfaces=set(),source_examples=[],invalid_raw=0)

def note(bucket,row):
    day,track,rid,typ,cls,raw,src_file,src_line=row
    bucket['n']+=1
    data=safe_json(raw)
    if not data:bucket['invalid_raw']+=1
    for key,origin in [('names',data.get('race_name')),('types',typ or data.get('type')),
                       ('patterns',data.get('pattern')),('classes',cls or data.get('class')),
                       ('surfaces',data.get('surface'))]:
        v=cleaned(origin)
        if v:bucket[key].add(v)
    if len(bucket['source_examples'])<3:bucket['source_examples'].append([src_file,src_line])


def flatten(key,b,stats):
    day,track,rid=key
    names=unique(b['names']);types=unique(b['types']);patterns=unique(b['patterns']);classes=unique(b['classes']);surfaces=unique(b['surfaces'])
    conflicts=[k for k,vals in [('race_name',names),('race_type',types),('pattern',patterns),('race_class',classes),('surface',surfaces)] if len(vals)>1]
    value=lambda xs: xs[0] if len(xs)==1 else ''
    category=classify(value(names),value(types),value(patterns),value(surfaces),track,value(classes))
    status='REVIEW_CONFLICT' if conflicts else 'EXPLICIT_SOURCE'
    if conflicts:
        # No category should be presented as verified if race identity aggregates
        # conflicting metadata. Preserve the source contradictions for review.
        category.update(discipline='ambiguous',surface='unknown',primary_category='unclassified',flags=[],pattern_grade='')
    elif category['pattern_grade']=='CONFLICT':
        status='REVIEW_PATTERN_GRADE_CONFLICT'
        category.update(discipline='ambiguous',surface='unknown',primary_category='unclassified',flags=[],pattern_grade='')
    elif category['primary_category']=='unclassified':status='SOURCE_NOT_SPECIFIC_ENOUGH'
    if b['invalid_raw']:
        status='REVIEW_INVALID_RAW_JSON'
        category.update(discipline='ambiguous',surface='unknown',primary_category='unclassified',flags=[],pattern_grade='')
    if category['title_class_conflict']:stats['title_vs_numerical_class_conflicts']+=1
    year=str(day or '')[:4]
    stats['historical_runner_rows']+=b['n'];stats['historical_races']+=1
    stats['by_year'][year]['races']+=1;stats['by_year'][year]['runners']+=b['n']
    stats['by_year'][year]['race_'+status]+=1
    stats['by_year'][year]['runner_'+status]+=b['n']
    if category['primary_category']!='unclassified' and status=='EXPLICIT_SOURCE':
        stats['historical_races_classified']+=1;stats['historical_runners_classified']+=b['n']
    else:stats['historical_unclassified_races']+=1
    stats['by_year_category'][year][category['primary_category']]+=1
    stats['by_year_discipline'][year][category['discipline']]+=1
    stats['categories'][category['primary_category']]+=1
    stats['category_runners'][category['primary_category']]+=b['n']
    stats['disciplines'][category['discipline']]+=1
    stats['statuses'][status]+=1
    for flag in category['flags']:stats['flags'][flag]+=1
    if conflicts:
        stats['metadata_conflict_races']+=1
        if len(stats['conflict_examples'])<1000:stats['conflict_examples'].append(dict(date=day,course=track,race_id=rid,
             fields=';'.join(conflicts),race_names=' | '.join(names)[:350],types=' | '.join(types)[:200]))
    if category['primary_category']=='unclassified' and len(stats['unclassified_examples'])<1000:
        stats['unclassified_examples'].append(dict(date=day,course=track,race_id=rid,
            race_name=value(names),source_type=value(types),source_pattern=value(patterns),reason=status))
    details={'source':SRC,'original_fields':['race_name','type','pattern','class','surface'],
             'source_rows':b['source_examples'],'metadata_conflicts':conflicts,
             'all_names':names[:5],'all_types':types[:5],'all_patterns':patterns[:5],
             'title_class_candidates_not_promoted':category['title_class_candidates']}
    rid_hash=hashlib.sha256(json.dumps([day,track,rid],ensure_ascii=False).encode()).hexdigest()
    return (rid_hash,day,track,rid,b['n'],value(names),value(types),value(patterns),value(classes),
            category['discipline'],category['surface'],category['primary_category'],
            json.dumps(category['flags'],ensure_ascii=False),category['pattern_grade'],status,
            json.dumps(details,ensure_ascii=False,separators=(',',':')))


def historical_pass(db,overlay,stats,max_races=0):
    read=sqlite3.connect('file:'+db.resolve().as_posix()+'?mode=ro',uri=True)
    read.execute('PRAGMA query_only=ON')
    if read.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Historical SQLite failed quick_check')
    sql='''SELECT race_date,course,race_id,race_type,race_class,raw_json,source_file,source_line
           FROM historical_runs ORDER BY race_date,course,race_id'''
    insert='INSERT INTO historical_race_categories VALUES ('+','.join(['?']*16)+')'
    current=None;bucket=None;batch=[]
    def flush():
        if bucket is not None:batch.append(flatten(current,bucket,stats))
        if len(batch)>=10000:
            overlay.executemany(insert,batch);overlay.commit();batch.clear()
    for row in read.execute(sql):
        key=tuple(row[:3])
        if current is not None and key!=current:
            flush()
            if max_races and stats['historical_races']>=max_races:break
            if stats['historical_races']%100000==0:
                print('Classified',stats['historical_races'],'historical races',flush=True)
            bucket=new_bucket()
        if bucket is None:bucket=new_bucket()
        current=key;note(bucket,row)
    else:flush()
    if batch:overlay.executemany(insert,batch);overlay.commit()
    read.close()


def recent_pass(root,overlay,stats):
    folder=root/'data/racing-results'
    files=sorted(folder.glob('????-??-??.json'))
    groups={}
    for file in files:
        try:data=json.loads(file.read_text(encoding='utf-8'))
        except (OSError,ValueError):stats['recent_unreadable_files']+=1;continue
        races=data.get('results',[]) if isinstance(data,dict) else []
        if not isinstance(races,list):stats['recent_invalid_results_files']+=1;continue
        stats['recent_files']+=1
        for race in races:
            if not isinstance(race,dict):continue
            day=str(race.get('date') or file.stem)[:10];track=cleaned(race.get('course'))
            rid=cleaned(race.get('race_id'))
            if not day or not track or not rid:stats['recent_races_without_identity']+=1;continue
            key=(day,track,rid)
            record=(cleaned(race.get('race_name')),cleaned(race.get('type') or race.get('race_type')),
                    cleaned(race.get('class') or race.get('race_class')),cleaned(race.get('pattern')),
                    cleaned(race.get('surface')))
            groups.setdefault(key,set()).add(record)
    insert='INSERT INTO archived_recent_race_categories VALUES ('+','.join(['?']*14)+')'
    batch=[]
    for (day,track,rid),records in sorted(groups.items()):
        stats['recent_distinct_races']+=1
        if len(records)>1:
            status='REVIEW_CONFLICT';name=typ=cls=pat=surf='';stats['recent_conflicting_races']+=1
        else:
            name,typ,cls,pat,surf=next(iter(records));status='EXPLICIT_SOURCE'
        cat=classify(name,typ,pat,surf,track,cls)
        if status=='REVIEW_CONFLICT' or cat['pattern_grade']=='CONFLICT':
            if cat['pattern_grade']=='CONFLICT':status='REVIEW_PATTERN_GRADE_CONFLICT'
            cat.update(discipline='ambiguous',surface='unknown',primary_category='unclassified',flags=[],pattern_grade='')
        elif cat['primary_category']=='unclassified':status='SOURCE_NOT_SPECIFIC_ENOUGH'
        if cat['primary_category']!='unclassified':stats['recent_classified_races']+=1
        stats['recent_categories'][cat['primary_category']]+=1
        key=hashlib.sha256(json.dumps([day,track,rid],ensure_ascii=False).encode()).hexdigest()
        batch.append((key,day,track,rid,name,typ,cls,cat['discipline'],cat['surface'],
                      cat['primary_category'],json.dumps(cat['flags']),cat['pattern_grade'],status,pat))
        if len(batch)>=10000:overlay.executemany(insert,batch);overlay.commit();batch.clear()
    if batch:overlay.executemany(insert,batch);overlay.commit()


def write_csv(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def generate_reports(out,stats,overlay):
    years=[]
    for y,c in sorted(stats['by_year'].items()):
        total=c['races'];unknown=c.get('race_SOURCE_NOT_SPECIFIC_ENOUGH',0)
        review=sum(n for k,n in c.items() if k.startswith('race_REVIEW_'))
        years.append(dict(year=y,races=total,runner_rows=c['runners'],
                          confidently_classified_races=total-unknown-review,
                          unclassified_races=unknown,review_races=review,
                          classification_coverage_pct=round(100*(total-unknown-review)/total,2) if total else 0))
    write_csv(out/'classification_by_year.csv',years,
              ['year','races','runner_rows','confidently_classified_races','unclassified_races','review_races','classification_coverage_pct'])
    write_csv(out/'category_by_year.csv',[dict(year=year,category=cat,races=n) for year,cs in sorted(stats['by_year_category'].items()) for cat,n in sorted(cs.items())],
              ['year','category','races'])
    write_csv(out/'discipline_by_year.csv',[dict(year=year,discipline=disc,races=n) for year,cs in sorted(stats['by_year_discipline'].items()) for disc,n in sorted(cs.items())],
              ['year','discipline','races'])
    write_csv(out/'race_category_counts.csv',[
        dict(category=k,races=v,runner_rows=stats['category_runners'][k]) for k,v in stats['categories'].most_common()],
        ['category','races','runner_rows'])
    write_csv(out/'review_conflicts_sample.csv',stats['conflict_examples'],
              ['date','course','race_id','fields','race_names','types'])
    write_csv(out/'unclassified_sample.csv',stats['unclassified_examples'],
              ['date','course','race_id','race_name','source_type','source_pattern','reason'])
    summary={'stage':80,'mode':'READ_ONLY_RESEARCH_ONLY','generated_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'source':SRC,'historical_races':stats['historical_races'],
        'historical_runner_rows':stats['historical_runner_rows'],
        'historical_races_with_verified_detailed_category':stats['historical_races_classified'],
        'historical_runner_rows_with_verified_detailed_category':stats['historical_runners_classified'],
        'historical_races_unclassified_or_review':stats['historical_unclassified_races'],
        'metadata_conflict_races':stats['metadata_conflict_races'],
        'original_class_vs_title_disagreement_races':stats['title_vs_numerical_class_conflicts'],
        'categories':dict(stats['categories']),'category_runner_rows':dict(stats['category_runners']),
        'disciplines':dict(stats['disciplines']),'statuses':dict(stats['statuses']),
        'overlapping_subtype_counts':dict(stats['flags']),
        'recent_archive_days':stats['recent_files'],'recent_distinct_races':stats['recent_distinct_races'],
        'recent_detailed_categories':dict(stats['recent_categories']),
        'recent_conflicting_races':stats['recent_conflicting_races'],
        'numerical_class_policy':'Original class is preserved as source evidence only; disputed Class N in race title is not automatically filled.',
        'category_policy':'Explicit original race-name/source-type conditions only; no guessed non-handicap, no inferred turf, no grade treated as UK class.',
        'model_d_changed':False,'live_records_changed':False,'api_calls':0,
        'limitations':['Original source may itself contain inaccurate classification; mismatches flagged for review',
          'Unclassified does not mean non-handicap',
          'Multiple overlapping categories are retained as flags; primary category is for reporting',
          'Archived recent races are separate from historical Kaggle source and are not blindly merged',
          'SP and RPR recovery are separate priorities and are not claimed by Stage 80']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    overlay.execute('CREATE TABLE IF NOT EXISTS audit_metadata (key TEXT PRIMARY KEY,value TEXT)')
    overlay.execute('INSERT OR REPLACE INTO audit_metadata VALUES (?,?)',('summary',json.dumps(summary)))
    overlay.commit()
    return summary


def self_test():
    cases=[
        ('Juvenile Novice Handicap Hurdle','Hurdle','','','Kempton','Class 4','hurdle','handicap',['novice','handicap','juvenile']),
        ('Irish Stallion Farms EBF Maiden Stakes','Flat','','','Leopardstown','','flat','maiden',['maiden']),
        ('Novice Chase','Chase','','','Aintree','','chase','novice',['novice']),
        ('National Hunt Flat Race','NH Flat','','','Newbury','','nh_flat','bumper',['bumper']),
        ('Some Race','Flat','Group 1','','York','','flat','graded_or_group',[]),
        ('Listed Stakes','Flat','','','Ascot','','flat','listed',['listed']),
        ('Nursery Stakes','Flat','','','Kempton (AW)','','flat','handicap',['nursery','handicap']),
        ('Unnamed Cup','Flat','','','Newmarket','','flat','unclassified',[]),
        ('Something Group 2','Flat','','','Newmarket','Class 3','flat','graded_or_group',[]),
        ('Beginners Chase','Chase','','','Naas','','chase','beginner',['beginner']),
        ('Mares Maiden Hurdle','Hurdle','','','Wexford','','hurdle','maiden',['maiden']),
    ]
    for name,typ,pattern,surface,track,cls,disc,primary,tags in cases:
        got=classify(name,typ,pattern,surface,track,cls)
        assert got['discipline']==disc,(name,got)
        assert got['primary_category']==primary,(name,got)
        assert set(tags)<=set(got['flags']),(name,got)
    assert classify('Generic NH Race','NH Flat','','','Newbury','')['primary_category']=='bumper'
    assert classify('Hurdle Race','Hurdle','','','Wincanton','')['primary_category']=='unclassified'
    assert classify('Restricted Non-Handicap','Flat','','','Ascot','')['primary_category']=='unclassified'
    assert classify('Flat Stakes','Flat','','','Kempton (AW)','')['surface']=='all_weather'
    assert classify('Flat Stakes','Flat','','','Newmarket','')['surface']=='unknown'
    assert classify('Flat Group 1','Flat','','','York','Class 5')['title_class_conflict']==False
    assert classify('Flat Class 4 Handicap','Flat','','','York','Class 5')['title_class_conflict']
    with tempfile.TemporaryDirectory() as base:
        path=pathlib.Path(base)/'tiny.sqlite';cx=sqlite3.connect(path)
        cx.execute('CREATE TABLE historical_runs (race_date TEXT,course TEXT,race_id TEXT,race_type TEXT,race_class TEXT,raw_json TEXT,source_file TEXT,source_line INTEGER)')
        rows=[('2025-01-01','Aintree','100','Chase','',json.dumps({'race_name':'Novice Handicap Chase'}),'sample.csv',2),
              ('2025-01-01','Aintree','100','Chase','',json.dumps({'race_name':'Novice Handicap Chase'}),'sample.csv',3),
              ('2025-01-02','Naas','200','Hurdle','',json.dumps({'race_name':'Maiden Hurdle'}),'sample.csv',4),
              ('2025-01-02','Naas','200','Hurdle','',json.dumps({'race_name':'Handicap Hurdle'}),'sample.csv',5)]
        cx.executemany('INSERT INTO historical_runs VALUES (?,?,?,?,?,?,?,?)',rows);cx.commit();cx.close()
        out=pathlib.Path(base)/'out';out.mkdir();overlay=init_overlay(out/'overlay.sqlite')
        s=make_stats();historical_pass(path,overlay,s)
        assert s['historical_races']==2,s
        assert s['historical_races_classified']==1,s
        assert s['metadata_conflict_races']==1,s
        root=pathlib.Path(base)/'app'
        (root/'data/racing-results').mkdir(parents=True)
        (root/'data/racing-results/2026-11-03.json').write_text(json.dumps({'results':[
            {'date':'2026-11-03','course':'Dundalk (AW)','race_id':'r1','race_name':'Nursery Handicap',
             'type':'Flat','class':'Class 5','pattern':'','surface':'All Weather'}]}))
        recent_pass(root,overlay,s)
        assert s['recent_distinct_races']==1,s
        assert s['recent_classified_races']==1,s
        assert overlay.execute('select surface from archived_recent_race_categories').fetchone()[0]=='all_weather'
        summary=generate_reports(out,s,overlay)
        assert summary['model_d_changed'] is False
        overlay.close()
    print('PASS: classifications, overlapping handicap/novice tags, source conflicts, AW caution, no writes to original database')


def make_stats():
    return collections.defaultdict(int,{
        'by_year':collections.defaultdict(collections.Counter),'by_year_category':collections.defaultdict(collections.Counter),
        'by_year_discipline':collections.defaultdict(collections.Counter),'categories':collections.Counter(),
        'category_runners':collections.Counter(),'disciplines':collections.Counter(),
        'statuses':collections.Counter(),'flags':collections.Counter(),
        'recent_categories':collections.Counter(),'conflict_examples':[],'unclassified_examples':[]})


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=pathlib.Path)
    p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage80'))
    p.add_argument('--self-test',action='store_true')
    args=p.parse_args()
    if args.self_test:self_test();return
    if args.db is None:raise SystemExit('--db is required unless --self-test')
    args.out.mkdir(parents=True,exist_ok=True)
    overlay=init_overlay(args.out/'race_category_research.sqlite')
    stats=make_stats()
    historical_pass(args.db,overlay,stats)
    recent_pass(args.root,overlay,stats)
    summary=generate_reports(args.out,stats,overlay)
    overlay.close()
    if summary['historical_runner_rows']<5_000_000:
        raise RuntimeError('Historical runner count suspiciously small; refusing completion')
    print(json.dumps({k:v for k,v in summary.items() if k in ('stage','historical_races','historical_runner_rows','historical_races_with_verified_detailed_category','historical_races_unclassified_or_review','metadata_conflict_races','categories','disciplines','recent_distinct_races','recent_detailed_categories','api_calls')},indent=2),flush=True)

if __name__=='__main__': main()
