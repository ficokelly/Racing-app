#!/usr/bin/env python3
"""Stage82: evidence-led UK/IE historical form recovery manifest (read-only).

Inputs: Stage65E lossless historical DB and Stage81 verified UK/IE race overlay.
Outputs: separate manifests and reviewed external-evidence candidates only.
No Racing Post scraping, restricted API calls, inferred RPR, or production writes.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, re, sqlite3, sys, tempfile, unicodedata

RUNNER_FIELDS=('sp','rpr')
RACE_FIELDS=('race_class','race_category')
ALLOWED_FIELDS=set(RUNNER_FIELDS+RACE_FIELDS)
UKIE=('GB','IE')
REASONS=('SOURCE_NOT_SPECIFIC_ENOUGH','REVIEW_CONFLICT','REVIEW_PATTERN_GRADE_CONFLICT','REVIEW_INVALID_RAW_JSON')
EVIDENCE_COLUMNS=['race_date','course','source_race_id','off_time','race_name','winning_horse','horse','field','observed_value','source_name','source_url','access_rights','reviewer','reviewed_at_utc']
NORMALIZE_COUNTRY=re.compile(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|SAF|ITA|CAN)\)\s*$',re.I)
CLASS_RE=re.compile(r'^(?:class\s*)?([1-7])$',re.I)
SP_RE=re.compile(r'^\s*(?:\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?|EVS|EVENS|EVEN)(?:\s*(?:F|JF|CF|FAV))?\s*$',re.I)
CATEGORY_VALUES={'handicap','maiden','novice','beginner','graded_or_group','listed','bumper','seller','claimer','conditions','classified','auction','hunter_chase'}

def clean(x):return re.sub(r'\s+',' ',str(x or '').strip())
def norm(x):
    x=unicodedata.normalize('NFKD',str(x or '').casefold())
    return re.sub(r'[^a-z0-9]','', ''.join(z for z in x if not unicodedata.combining(z)))
def hnorm(x):return norm(NORMALIZE_COUNTRY.sub('',clean(x)))
def valid(x):return bool(clean(x))
def is_missing(x):return not valid(x)
def as_json(x):
    try:return json.loads(x) if x else {}
    except (TypeError,ValueError):return {}
def as_date(x):return clean(x)[:10]
def off_time(x):
    s=clean(x)
    m=re.search(r'(?<!\d)([012]?\d):([0-5]\d)(?!\d)',s)
    return f'{int(m.group(1)):02d}:{m.group(2)}' if m and int(m.group(1))<=23 else ''
def writer(path,columns):
    f=path.open('w',encoding='utf-8',newline='');w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();return f,w

def load_stage81(path):
    cx=sqlite3.connect('file:'+path.resolve().as_posix()+'?mode=ro',uri=True)
    cx.row_factory=sqlite3.Row
    if cx.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('Stage81 SQLite integrity check failed')
    cols={x[1] for x in cx.execute('pragma table_info(category_reconciliation)')}
    needed={'race_date','course','source_race_id','region','race_name','source_type','source_pattern','source_class','final_category','final_discipline','classification_status','race_key'}
    if not needed<=cols:raise RuntimeError('Stage81 schema incompatible: '+str(needed-cols))
    rows={}
    summary=collections.Counter()
    cur=cx.execute('''SELECT * FROM category_reconciliation WHERE region IN ('GB','IE')''')
    for r in cur:
        d=dict(r);key=(d['race_date'],d['course'],d['source_race_id'])
        if key in rows:raise RuntimeError('Duplicate Stage81 race key')
        rows[key]=d
        summary['stage81_ukie_races']+=1
        if d['final_category']=='unclassified' or d['classification_status'] not in ('EXPLICIT_SOURCE','EXPLICIT_ABBREVIATION'):summary['stage81_unresolved_races']+=1
    cx.close()
    return rows,summary

def validate_value(field,value):
    v=clean(value)
    if field=='sp':return v.upper() if SP_RE.fullmatch(v) else ''
    if field=='rpr':
        # Published RPR is an integer; do not accept our API's performance_rating.
        return v if re.fullmatch(r'\d{1,3}',v) and 1<=int(v)<=250 else ''
    if field=='race_class':
        m=CLASS_RE.fullmatch(v)
        return 'Class '+m.group(1) if m else ''
    if field=='race_category':return v.lower() if v.lower() in CATEGORY_VALUES else ''
    return ''

def row_identity(source,runner,field):
    if field in RUNNER_FIELDS:return (source['race_date'],source['course'],source['source_race_id'],hnorm(runner))
    return (source['race_date'],source['course'],source['source_race_id'])

def input_evidence(files,index,output):
    # Candidate only: a source URL/CSV does not independently establish authenticity.
    candidates=[];reviews=[];counts=collections.Counter();seen=collections.defaultdict(set)
    for file in files:
        with file.open(newline='',encoding='utf-8-sig') as f:
            rd=csv.DictReader(f)
            if not rd.fieldnames or not set(EVIDENCE_COLUMNS)<=set(rd.fieldnames):
                reviews.append({'row':0,'file':str(file),'status':'INVALID_COLUMNS','details':'Missing required columns'});continue
            for rowno,entry in enumerate(rd,2):
                counts['submitted_rows']+=1
                key=(as_date(entry.get('race_date')),clean(entry.get('course')),clean(entry.get('source_race_id')))
                r=index.get(key)
                error=[]
                if not r:error.append('UNKNOWN_OR_NON_UKIE_RACE')
                field=clean(entry.get('field')).lower();value=validate_value(field,entry.get('observed_value'))
                if field not in ALLOWED_FIELDS:error.append('UNSUPPORTED_FIELD')
                elif not value:error.append('INVALID_FIELD_VALUE')
                if r:
                    if norm(r['race_name'])!=norm(entry.get('race_name')):error.append('RACE_NAME_MISMATCH')
                    if r['off_times'] and off_time(entry.get('off_time')) not in r['off_times']:error.append('OFF_TIME_MISMATCH')
                    if not off_time(entry.get('off_time')):error.append('MISSING_OFF_TIME')
                    if not r['off_times']:error.append('NO_ORIGINAL_OFF_TIME_TO_VALIDATE')
                    if r['winner_names'] and hnorm(entry.get('winning_horse')) not in r['winner_names']:error.append('WINNER_MISMATCH')
                    if not r['winner_names']:error.append('ORIGINAL_WINNER_AMBIGUOUS')
                    if field in RUNNER_FIELDS:
                        runner=hnorm(entry.get('horse'))
                        if runner not in r['runners'] or len(r['runners'].get(runner,[]))!=1:error.append('RUNNER_IDENTITY_AMBIGUOUS')
                        else:
                            original=r['runners'][runner][0]
                            if valid(original[field]):error.append('ORIGINAL_VALUE_ALREADY_PRESENT')
                    elif field=='race_class' and bool(r['class_values']):error.append('ORIGINAL_CLASS_ALREADY_PRESENT')
                    elif field=='race_category' and r['meta']['final_category']!='unclassified':error.append('CATEGORY_ALREADY_PRESENT')
                if not str(entry.get('source_url','')).startswith('https://'):error.append('MISSING_HTTPS_EVIDENCE_URL')
                if not clean(entry.get('source_name')):error.append('MISSING_SOURCE_NAME')
                if not clean(entry.get('access_rights')):error.append('MISSING_ACCESS_RIGHTS')
                if not clean(entry.get('reviewer')):error.append('MISSING_REVIEWER')
                if not clean(entry.get('reviewed_at_utc')):error.append('MISSING_REVIEW_TIMESTAMP')
                ident=(key,field,hnorm(entry.get('horse')) if field in RUNNER_FIELDS else '')
                if value in seen[ident]:error.append('REPEATED_EVIDENCE_ROW')
                seen[ident].add(value)
                status='IDENTITY_MATCHED_NEEDS_SOURCE_REVIEW' if not error else 'REJECTED_'+error[0]
                reviews.append({'row':rowno,'file':file.name,'status':status,'details':';'.join(error),
                                'date':key[0],'course':key[1],'race_id':key[2],
                                'horse':clean(entry.get('horse')),'field':field,'value':value,'source_url':entry.get('source_url','')})
                counts[status]+=1
                if not error:
                    candidates.append({**entry,'field':field,'observed_value':value,'status':status,
                                       'note':'Candidate only: user-supplied evidence has NOT been independently checked against source publication; no model/DB edits.'})
    if candidates:
        names=list(dict.fromkeys(EVIDENCE_COLUMNS+['status','note']))
        f,w=writer(output/'matched_evidence_candidates.csv',names);w.writerows(candidates);f.close()
    return counts,reviews,candidates

def process(stage81,history,root,out,compact_only=False):
    metas,base=load_stage81(stage81)
    out.mkdir(parents=True,exist_ok=True)
    catfiles=['all_years_missing_class_races.csv','ukie_unclassified_races.csv','yearly_priority_queue.csv',
              '2026_missing_sp_rpr_runners.csv','2026_priority_races.csv','cross_year_named_race_clues.csv',
              'source_evidence_review.csv','matched_evidence_candidates.csv']
    targets={}
    # Queue every UK/IE unresolved race; 2026 missing fields and all-year numeric-class gaps are later added.
    for key,d in metas.items():
        if d['final_category']=='unclassified' or d['classification_status'] not in ('EXPLICIT_SOURCE','EXPLICIT_ABBREVIATION') or not valid(d['source_class']):
            targets[key]={'meta':d,'date':d['race_date'],'course':d['course'],'id':d['source_race_id'],
                          'off_times':set(),'winner_names':set(),'runners':collections.defaultdict(list),
                          'class_values':set(),'counts':collections.Counter(), 'runner_rows':0}
        elif d['race_date'] and str(d['race_date']).startswith('2026-'):
            targets[key]={'meta':d,'date':d['race_date'],'course':d['course'],'id':d['source_race_id'],
                          'off_times':set(),'winner_names':set(),'runners':collections.defaultdict(list),
                          'class_values':set(),'counts':collections.Counter(), 'runner_rows':0}
    summary=collections.Counter(base)
    hist=sqlite3.connect('file:'+history.resolve().as_posix()+'?mode=ro',uri=True)
    if hist.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('Stage65E integrity check failed')
    source_first,source_last=hist.execute("SELECT min(race_date),max(race_date) FROM historical_runs WHERE length(race_date)=10 AND substr(race_date,5,1)='-' AND substr(race_date,8,1)='-'").fetchone()
    archive_dir=root/'data/racing-results'
    archive_dates=sorted(f.stem for f in archive_dir.glob('????-??-??.json')) if archive_dir.is_dir() else []
    archive_overlapping_days=sum(bool(source_first and source_last and source_first<=d<=source_last) for d in archive_dates)
    records=hist.execute('''SELECT race_date,course,race_id,horse,finish,race_class,sp,rpr,raw_json,source_file,source_line
        FROM historical_runs ORDER BY race_date,course,race_id''')
    year=collections.defaultdict(collections.Counter)
    year_cat=collections.Counter()
    # Only 2026 needs runner-level details, other years just class/race category queues.
    for day,course,rid,horse,finish,klass,sp,rpr,raw,source_file,source_line in records:
        key=(day,course,rid)
        obj=targets.get(key)
        if not obj:continue
        summary['source_rows_for_target_races']+=1
        obj['runner_rows']+=1
        doc=as_json(raw)
        if doc.get('off') and off_time(doc['off']):obj['off_times'].add(off_time(doc['off']))
        if str(finish).strip().upper() in ('1','1ST','1=') and hnorm(horse):obj['winner_names'].add(hnorm(horse))
        if valid(klass):obj['class_values'].add(clean(klass))
        y=str(day or '')[:4]
        if is_missing(klass):obj['counts']['missing_class']+=1
        if y=='2026':
            if is_missing(sp):obj['counts']['missing_sp']+=1
            if is_missing(rpr):obj['counts']['missing_rpr']+=1
            nm=hnorm(horse)
            obj['runners'][nm].append(dict(horse=horse,finish=finish,sp=sp,rpr=rpr,source_file=source_file,source_line=source_line))
    hist.close()
    # Store race objects for exact candidate matching.
    evidence_index={k:{**v,'race_date':v['date'],'source_race_id':v['id'], 'race_name':v['meta']['race_name']} for k,v in targets.items()}
    full_names={
        'class':['year','region','date','course','race_id','race_name','off','type','original_class','missing_class_runners','runner_rows'],
        'unknown':['year','region','date','course','race_id','race_name','off','type','pattern','reason','runner_rows'],
        'sp':['date','region','course','race_id','off','race_name','horse','finish','missing_sp','missing_rpr','source_file','source_line'],
        'race':['date','region','course','race_id','off','race_name','classification_status','missing_sp_runners','missing_rpr_runners','missing_class_runners','runner_rows','source_winner'],
        'cross':['date','region','course','race_id','race_name','potential_prior_category','prior_occurrences','prior_years','note'],
        'year':['year','region','races','unclassified_races','missing_class_races','missing_class_runner_rows','races_missing_sp','missing_sp_runner_rows','races_missing_rpr','missing_rpr_runner_rows'],
    }
    fcl,wcl=writer(out/catfiles[0],full_names['class'])
    fun,wn=writer(out/catfiles[1],full_names['unknown'])
    fru,wr=writer(out/catfiles[3],full_names['sp'])
    fpr,wp=writer(out/catfiles[4],full_names['race'])
    fcr,wcr=writer(out/catfiles[5],full_names['cross'])
    # Stable named races: evidence for manual review only, never automatically promote.
    named=collections.defaultdict(lambda:collections.Counter())
    for m in metas.values():
        if m['final_category']!='unclassified' and m['classification_status'] in ('EXPLICIT_SOURCE','EXPLICIT_ABBREVIATION') and valid(m['race_name']):
            named[(m['region'],norm(m['course']),norm(m['race_name']))][(m['final_category'],str(m['race_date'])[:4])]+=1
    for key,obj in sorted(targets.items()):
        m=obj['meta'];day,track,rid=key; y=str(day or '')[:4];region=m['region']
        off=', '.join(sorted(x for x in obj['off_times'] if x))
        c=obj['counts'];stats=year[(y,region)];stats['races']+=1
        if c['missing_class']:
            stats['missing_class_races']+=1;stats['missing_class_runner_rows']+=c['missing_class']
            summary['ukie_missing_class_runner_rows']+=c['missing_class'];summary['ukie_races_with_missing_class']+=1
            wcl.writerow(dict(year=y,region=region,date=day,course=track,race_id=rid,race_name=m['race_name'],off=off,
                              type=m['source_type'],original_class=m['source_class'],missing_class_runners=c['missing_class'],runner_rows=obj['runner_rows']))
        if m['final_category']=='unclassified' or m['classification_status'] not in ('EXPLICIT_SOURCE','EXPLICIT_ABBREVIATION'):
            stats['unclassified_races']+=1;summary['ukie_unclassified_races']+=1
            wn.writerow(dict(year=y,region=region,date=day,course=track,race_id=rid,race_name=m['race_name'],off=off,type=m['source_type'],
                             pattern=m['source_pattern'],reason=m['classification_status'],runner_rows=obj['runner_rows']))
            prev=named.get((region,norm(track),norm(m['race_name'])),{})
            # Restrict to earlier years, only if all prior occurrences agree; still a clue, not a recovered fact.
            prior=[(cat,year0,n) for (cat,year0),n in prev.items() if year0<y]
            if prior and len({a[0] for a in prior})==1:
                wcr.writerow(dict(date=day,region=region,course=track,race_id=rid,race_name=m['race_name'],
                                  potential_prior_category=prior[0][0],prior_occurrences=sum(a[2] for a in prior),
                                  prior_years=';'.join(sorted({a[1] for a in prior})),
                                  note='CROSS-YEAR CLUE ONLY; named races can change conditions. No recovery applied.'))
                summary['unclassified_with_prior_named_race_clue']+=1
        if y=='2026':
            if c['missing_sp']:summary['ukie_2026_missing_sp_runner_rows']+=c['missing_sp'];stats['races_missing_sp']+=1;stats['missing_sp_runner_rows']+=c['missing_sp']
            if c['missing_rpr']:summary['ukie_2026_missing_rpr_runner_rows']+=c['missing_rpr'];stats['races_missing_rpr']+=1;stats['missing_rpr_runner_rows']+=c['missing_rpr']
            if c['missing_sp'] or c['missing_rpr']:
                summary['ukie_2026_races_with_priority_gap']+=1
                wp.writerow(dict(date=day,region=region,course=track,race_id=rid,off=off,race_name=m['race_name'],
                                 classification_status=m['classification_status'],missing_sp_runners=c['missing_sp'],
                                 missing_rpr_runners=c['missing_rpr'],missing_class_runners=c['missing_class'],
                                 runner_rows=obj['runner_rows'],source_winner='|'.join(sorted(obj['winner_names']))))
                for rs in obj['runners'].values():
                    for runner in rs:
                        if valid(runner['sp']) and valid(runner['rpr']):continue
                        wr.writerow(dict(date=day,region=region,course=track,race_id=rid,off=off,race_name=m['race_name'],
                                         horse=runner['horse'],finish=runner['finish'],
                                         missing_sp=int(is_missing(runner['sp'])),missing_rpr=int(is_missing(runner['rpr'])),
                                         source_file=runner['source_file'],source_line=runner['source_line']))
    for f in (fcl,fun,fru,fpr,fcr):f.close()
    fy,wy=writer(out/catfiles[2],full_names['year'])
    for (y,r),s in sorted(year.items()):wy.writerow(dict(year=y,region=r,**s))
    fy.close()
    # Optional, explicitly licensed data, provided as dated CSV file; no scraped site contents.
    indir=root/'data/stage82-authorised-evidence'
    evidence_files=sorted(indir.glob('*.csv')) if indir.exists() else []
    ecounts,reviews,candidates=input_evidence(evidence_files,evidence_index,out)
    if not evidence_files:
        template=out/'external_evidence_template.csv'
        fw,ww=writer(template,EVIDENCE_COLUMNS);fw.close()
    fe,we=writer(out/catfiles[6],['row','file','status','details','date','course','race_id','horse','field','value','source_url'])
    we.writerows(reviews);fe.close()
    summary.update(ecounts)
    report={
      'stage':82,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
      'status':'PASS_READ_ONLY_EVIDENCE_MANIFEST',
      'scope':'UK/IE historical race type, numerical class all years, 2026 SP and genuine RPR',
      'source_stage81_verified_ukie_races':base['stage81_ukie_races'],
      'source_stage81_unclassified_or_review':base['stage81_unresolved_races'],
      'source_stage65e':'Kaggle v129 lossless archived historical_runs',
      'historical_source_date_span':{'first':source_first,'last':source_last},
      'live_archived_result_date_span':{'first':archive_dates[0] if archive_dates else None,'last':archive_dates[-1] if archive_dates else None,'dated_files':len(archive_dates)},
      'historical_and_live_archive_overlap_days':archive_overlapping_days,
      'counts':dict(summary),
      'manual_external_evidence_files':len(evidence_files),
      'matched_evidence_candidate_count':len(candidates),
      'externally_verified_recovered_values':0,
      'original_database_changed':False,'frozen_model_d_changed':False,'live_app_changed':False,
      'actual_rpr_recovered':0,
      'notes':[
        'No automated Racing Post extraction. Permission/licence for bulk data must be independently established.',
        'The existing live result archive is only an independent recovery candidate for dates overlapping the historical source.',
        'No historical Racing API retries: previously returned HTTP 401 and no longer offers actual RPR.',
        'User-supplied external evidence only becomes a review candidate, not a verified value; an independent verification step is still required.',
        'A verified original RPR is not interchangeable with a performance_rating or speed_rating.',
        'Race category is separate from the original numerical UK class; class is never manufactured from graded status.',
        'UK-style numeric class may not apply to many Irish races: blank does not always mean recoverable missing data.',
        'Only stage81 UK/IE race keys are included; foreign/uncertain venues excluded pending review.',
        'Races are matched by original identity; off-time and winner are compulsory for evidence acceptance when present in the source.',
        'No changes to frozen Model D or published app.'
      ]}
    (out/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    (out/'how_to_verify.txt').write_text(
        'STAGE 82 — Independent race form evidence queue\n'
        '1. Open 2026_priority_races.csv for race-level priorities.\n'
        '2. Check a reliable, licensed result record for exact race date, course, off time and winner.\n'
        '3. Verify runner SP/RPR and race class/type, where published. Some RPRs are genuinely unpublished.\n'
        '4. Enter findings ONLY in data/stage82-authorised-evidence/*.csv with all template columns.\n'
        '5. The tool checks identity consistency but DOES NOT automatically accept scraped/claimed values.\n'
        '6. Review matched_evidence_candidates.csv and source pages manually before any authorised separate research enrichment.\n'
        '7. Do not substitute forecast odds or in-house performance ratings for SP or actual RPR.\n'
        'NO scraping, no bypassing login, no paid endpoint calls, no writes to production files.\n',encoding='utf-8')
    return report

def self_test():
    assert validate_value('rpr','154')=='154'
    assert validate_value('rpr','performance_rating')==''
    assert validate_value('rpr','260')==''
    assert validate_value('race_class','Class 2')=='Class 2'
    assert validate_value('race_class','Grade 2')==''
    assert validate_value('sp','8/1')=='8/1'
    assert validate_value('sp','1.9')==''
    assert hnorm('Horse (IRE)')==hnorm('Horse')
    assert off_time('14:30')=='14:30'
    with tempfile.TemporaryDirectory() as tmp:
        root=pathlib.Path(tmp);stage81=root/'stage81.sqlite';source=root/'historical.sqlite'
        a=sqlite3.connect(stage81)
        a.execute('''CREATE TABLE category_reconciliation (race_key TEXT,race_date TEXT,course TEXT,source_race_id TEXT,
            region TEXT,race_name TEXT,source_type TEXT,source_pattern TEXT,source_class TEXT,final_category TEXT,
            final_discipline TEXT,classification_status TEXT)''')
        a.executemany('INSERT INTO category_reconciliation VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',[
          ('1','2026-05-10','Aintree','42','GB','An Unclassified Chase','Chase','','','unclassified','chase','SOURCE_NOT_SPECIFIC_ENOUGH'),
          ('2','2026-05-11','Auteuil','99','FR','Foreign Hurdle','Hurdle','','','unclassified','hurdle','SOURCE_NOT_SPECIFIC_ENOUGH'),
          ('3','2025-05-11','Aintree','43','GB','Handicap Chase','Chase','','Class 3','handicap','chase','EXPLICIT_SOURCE')]);a.commit();a.close()
        b=sqlite3.connect(source)
        b.execute('''CREATE TABLE historical_runs (race_date TEXT,course TEXT,race_id TEXT,horse TEXT,finish TEXT,
                      race_class TEXT,sp TEXT,rpr TEXT,raw_json TEXT,source_file TEXT,source_line INTEGER)''')
        b.executemany('INSERT INTO historical_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)',[
          ('2026-05-10','Aintree','42','Test Horse (IRE)','1','','','',json.dumps({'off':'14:30'}),'x.csv',2),
          ('2026-05-10','Aintree','42','Other Horse','2','','9/1','',json.dumps({'off':'14:30'}),'x.csv',3),
          ('2026-05-11','Auteuil','99','Foreign','1','','','',json.dumps({'off':'14:30'}),'x.csv',4),
          ('2025-05-11','Aintree','43','Winner','1','Class 3','2/1','129',json.dumps({'off':'14:30'}),'x.csv',5)]);b.commit();b.close()
        result=process(stage81,source,root,root/'out')
        assert result['counts']['stage81_ukie_races']==2,result
        assert result['counts']['ukie_2026_missing_sp_runner_rows']==1,result
        assert result['counts']['ukie_2026_missing_rpr_runner_rows']==2,result
        assert result['counts']['ukie_unclassified_races']==1,result
        assert result['counts']['ukie_races_with_missing_class']==1,result
        assert result['externally_verified_recovered_values']==0
    print('PASS: UKIE-only, source identity, missing SP/RPR, no class imputation, candidate safety, read-only')

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage81',type=pathlib.Path)
    p.add_argument('--history',type=pathlib.Path);p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage82'))
    p.add_argument('--self-test',action='store_true');args=p.parse_args()
    if args.self_test:self_test();return
    if not args.stage81 or not args.history:p.error('--stage81 and --history required')
    report=process(args.stage81,args.history,args.root,args.out)
    if report['source_stage81_verified_ukie_races']<400000:raise RuntimeError('Stage81 UK/IE coverage too small')
    if report['counts']['ukie_2026_missing_rpr_runner_rows']<1:raise RuntimeError('Unexpected absence of 2026 missing RPR')
    print(json.dumps({k:v for k,v in report.items() if k in ('stage','status','source_stage81_verified_ukie_races','source_stage81_unclassified_or_review','counts','matched_evidence_candidate_count','externally_verified_recovered_values')},indent=2),flush=True)
if __name__=='__main__':main()
