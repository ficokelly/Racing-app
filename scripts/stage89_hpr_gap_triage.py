#!/usr/bin/env python3
"""Stage89 Model E gap triage, research-only.

Reproduce full-field coverage exclusions for the same 30-day backtest using
historical pre-off Git racecards and strictly older HPR records. Inspect gaps
in verified beaten-distance observations WITHOUT filling or guessing them.

NEVER edits original records, source SQLite, stored HPR, Models D/D(c), or
live app. A source URL is only a review lead, not permission to scrape it.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, sqlite3, sys, tempfile
from zoneinfo import ZoneInfo

SCRIPTS=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPTS))

STOP={'nr','non-runner','nonrunner','withdrawn','scratched','dns','did not start'}
FIELDS=['date','course','time','race_type','field_size','status','usable_hpr','form_applicable',
        'form_covered','missing_stable_id','missing_hpr_form_horses','no_prior_hpr_horses',
        'missing_hpr_count','hpr_coverage_pct','source_race_key']
MARGINS=['date','course','time','horse','horse_key','race_key','race_id','position',
         'source','raw_beaten','review_reference_url','review_reference_status','action']


def read_csv(path):
    if not pathlib.Path(path).is_file():return []
    with open(path,'r',encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def write_csv(path,rows,fields):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)


def validate_reference(candidate):
    """Return an optional HTTPS review link; never proof of distance."""
    s=str(candidate or '').strip()
    return s if s.startswith('https://') and len(s)<=1000 and '\n' not in s and '\r' not in s else ''


def race_rows(results,cards,conn,start,end,hpr):
    grouped=collections.defaultdict(list)
    for card in cards:grouped[hpr.race_key(card)].append(card)
    result=[];reasons=collections.Counter();missing_runners=collections.Counter()
    cache={}
    for key,race in sorted(results.items()):
        date=hpr.day(race.get('date') or race.get('off_dt'))
        if date is None or not start<=date<=end:continue
        cards_here=[x for x in grouped.get(key,[]) if str(x.get('race_status','')).lower() not in STOP]
        if not cards_here:
            reasons['NO_PREOFF_CARD']+=1
            continue
        settled={hpr.norm(z.get('horse')):z for z in race['runners']}
        full=[x for x in cards_here if hpr.norm(x.get('horse')) in settled]
        if len(full)<2 or {hpr.norm(x.get('horse')) for x in full}!={hpr.norm(x.get('horse')) for x in race['runners']}:
            reasons['FIELD_UNVERIFIED_OR_CHANGED']+=1
            continue
        applicable=covered=has_hpr=missing_id=0
        missing_form=[];no_prior=[]
        for x in full:
            name=str(x.get('horse') or '')
            horse_key=hpr.identity(x)
            prior=[]
            if horse_key:
                ck=(horse_key,str(date))
                if ck not in cache:cache[ck]=hpr.previous_ratings(conn,horse_key,str(date))
                prior=cache[ck]
            else:missing_id+=1
            # Replicate Stage86's published full-field eligibility predicate.
            had_prior=bool(str(x.get('form') or '').strip(' -')) or hpr.number(x.get('last_run')) is not None
            if had_prior:
                applicable+=1
                covered+=int(bool(prior))
            if prior:has_hpr+=1
            else:
                no_prior.append(name)
                missing_runners['MISSING_STABLE_ID' if not horse_key else 'NO_RATED_PRIOR_RUN']+=1
                if had_prior:missing_form.append(name)
        coverage=round(100*covered/applicable,2) if applicable else None
        eligible=(len(full)>=2 and has_hpr==len(full) and (not applicable or covered/applicable>=.90))
        status='ELIGIBLE_FULL_FIELD_HPR' if eligible else 'INSUFFICIENT_FULL_FIELD_HPR'
        reasons[status]+=1
        result.append({'date':str(date),'course':race.get('course'),'time':hpr.off(race.get('off_dt') or race.get('off')),
          'race_type':race.get('type') or race.get('race_name') or '',
          'field_size':len(full),'status':status,'usable_hpr':has_hpr,
          'form_applicable':applicable,'form_covered':covered,'missing_stable_id':missing_id,
          'missing_hpr_form_horses':'; '.join(missing_form),'no_prior_hpr_horses':'; '.join(no_prior),
          'missing_hpr_count':len(no_prior),'hpr_coverage_pct':coverage,
          'source_race_key':key})
    return result,dict(reasons),dict(missing_runners)


def audited_margin_queue(source_rows,root,out,hpr):
    """Reference-only pairing with same exact UK/IE event and runner.

    Missing distances cannot be recovered from an empty string. Validate a
    cited result-page URL only when the exact date/course/off plus unique
    runner and position agree with archived final result; no data is scraped.
    """
    final={}
    sources=[root/'data/race-results.json',*(sorted((root/'data/racing-results').glob('*.json')))]
    for p in sources:
        v=hpr.read_json(p,{}) or {}
        for race in v.get('results') or v.get('races') or []:
            if not isinstance(race,dict) or str(race.get('status') or '').lower()!='final':continue
            url=validate_reference(race.get('verified_source'))
            if not url:continue
            key=hpr.race_key(race)
            for runner in race.get('runners',[]):
                if not isinstance(runner,dict):continue
                horse=hpr.norm(runner.get('horse'))
                if not horse:continue
                value=hpr.number(runner.get('position'))
                # Conflicting result versions cannot establish a unique lead.
                signature=(key,horse)
                if signature in final and final[signature]!=(value,url):
                    final[signature]=(None,'')
                else:final[signature]=(value,url)
    output=[];references=0;blank=0;by_date=collections.Counter();by_source=collections.Counter()
    for row in source_rows:
        raw=str(row.get('raw_beaten') or '').strip()
        blank+=int(not raw)
        key=str(row.get('race_key') or '')
        horse=hpr.norm(row.get('horse'))
        pair=final.get((key,horse))
        pos=hpr.number(row.get('position'))
        url=''
        if pair and pair[1] and pos is not None and pair[0]==pos:url=pair[1]
        references+=bool(url)
        by_date[str(row.get('date') or '')]+=1
        by_source[str(row.get('source') or '')]+=1
        output.append({'date':row.get('date'),'course':row.get('course'),
           'time':key.split('|')[-1] if key and not key.startswith('HIST|') else '',
           'horse':row.get('horse'),'horse_key':row.get('horse_key'),
           'race_key':key,'race_id':row.get('race_id'),'position':row.get('position'),
           'source':row.get('source'),'raw_beaten':raw,'review_reference_url':url,
           'review_reference_status':'EXACT_RACE_RUNNER_PAGE_REFERENCE_ONLY' if url else 'NO_MATCHED_PAGE_REFERENCE',
           'action':'VERIFY_WITH_AUTHORISED_SOURCE;DO_NOT_IMPUTE_MARGIN'})
    write_csv(out/'stage89-margin-source-queue.csv',output,MARGINS)
    return {'total_missing_margin_records':len(output),'raw_margin_blanks':blank,
            'usable_exact_race_runner_reference_urls':references,
            'automatically_recovered_distances':0,
            'source_breakdown':dict(by_source),
            'most_affected_dates':by_date.most_common(15),
            'note':'URLs are review leads only. No race distances recovered or scored.'}


def audit(root,db,ratings,stage87_queue,out,asof,days):
    import stage86_hpr_engine as hpr
    start=asof-dt.timedelta(days=days);end=asof-dt.timedelta(days=1)
    earliest=max(start-dt.timedelta(days=90),dt.date(2026,10,8))
    results,bad=hpr.load_results(root,earliest,end)
    snapshots=hpr.card_dates(root,earliest,end,git_preoff=True)
    cards=[x for v in snapshots.values() for x in v]
    with sqlite3.connect('file:'+str(ratings.resolve())+'?mode=ro',uri=True) as conn:
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Stage86 research HPR SQLite corrupt')
        race_data,reasons,runner_reasons=race_rows(results,cards,conn,start,end,hpr)
    write_csv(out/'stage89-race-coverage.csv',race_data,FIELDS)
    ranked=sorted((z for z in race_data if z['status']=='INSUFFICIENT_FULL_FIELD_HPR'),
                  key=lambda z:(-len(z['missing_hpr_form_horses'].split('; ')) if z['missing_hpr_form_horses'] else 0,
                                -z['missing_hpr_count'],z['date'],z['course'],z['time']))
    write_csv(out/'stage89-priority-races.csv',ranked,FIELDS)
    margin=audited_margin_queue(read_csv(stage87_queue),root,out,hpr)
    report={'stage':89,'scope':{'as_of':str(asof),'start':str(start),'end':str(end),'days':days,'target_races':'UK_IE'},
            'model':'E_RESEARCH_ONLY','full_field_hpr':{'total_matched_card_races':len(race_data),
                'status':reasons,'missing_runner_causes':runner_reasons,
                'priority_races':len(ranked),
                'warnings':['Missing pre-race HPR does not prove missing historical form; true debutants may be legitimate.',
                            'All HPR lookups use races dated before the target race.']},
            'margin_evidence':margin,'invalid_source_races':bad,
            'safety':'NO_CODE_OR_DATABASE_MODIFICATION;NO_ODDS_IN_PREOFF_SELECTIONS;NO_AUTOMATIC_MARGIN_RECOVERY',
            'recommendation':'Prioritise verified full-field horse histories; verify distances externally with race identity and usage rights before applying to HPR.'}
    hpr.save_json(out/'stage89-gap-report.json',report)
    print(json.dumps({'date_window':report['scope'],'race_status':reasons,
                      'missing_runner_causes':runner_reasons,
                      'beaten_distance_gap_count':margin['total_missing_margin_records'],
                      'beaten_distances_automatically_recovered':0},indent=2))
    return report


def self_test():
    assert validate_reference('https://example.org/result/123')=='https://example.org/result/123'
    assert validate_reference('http://example.org/')==''
    assert validate_reference('https://bad\n/path')==''
    with tempfile.TemporaryDirectory() as td:
        p=pathlib.Path(td)/'rows.csv';write_csv(p,[{'date':'2026-11-09','horse':'Horse'}],['date','horse'])
        assert len(read_csv(p))==1
        assert read_csv(p)[0]['horse']=='Horse'
    class H:
        @staticmethod
        def norm(x):return str(x or '').upper()
        @staticmethod
        def race_key(x):return x['date']+'|'+x['course'].upper()+'|'+x['off']
        @staticmethod
        def day(x):return dt.date.fromisoformat(str(x)[:10])
        @staticmethod
        def number(x):
            try:return float(x)
            except (TypeError,ValueError):return None
        @staticmethod
        def identity(x):return x.get('horse_id','')
        @staticmethod
        def previous_ratings(conn,k,date):return conn.get(k,[])
        @staticmethod
        def off(x):return x
        @staticmethod
        def read_json(p,default=None):
            try:return json.loads(pathlib.Path(p).read_text())
            except (ValueError,OSError):return default
    race={'date':'2026-11-09','course':'Ascot','off':'12:00','runners':[{'horse':'One'},{'horse':'Two'},{'horse':'Three'}]}
    cards=[dict(date='2026-11-09',off='12:00',course='Ascot',horse=n,horse_id=n.lower(),form='123')
           for n in ('One','Two','Three')]
    rows,reasons,missing=race_rows({H.race_key(race):race},cards,
             {'one':[{'hpr':50}],'two':[{'hpr':40}],'three':[]},
             dt.date(2026,11,9),dt.date(2026,11,9),H)
    assert reasons['INSUFFICIENT_FULL_FIELD_HPR']==1 and rows[0]['missing_hpr_count']==1
    assert missing['NO_RATED_PRIOR_RUN']==1
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td);(root/'data/racing-results').mkdir(parents=True)
        (root/'data/race-results.json').write_text(json.dumps({'races':[dict(race,status='final',
           verified_source='https://example.org/results/1',runners=[{'horse':'One','position':2}])]}))
        queue=[{'date':'2026-11-09','course':'Ascot','horse':'One',
          'race_key':'2026-11-09|ASCOT|12:00','position':'2','raw_beaten':''}]
        sample=audited_margin_queue(queue,root,root/'out',H)
        assert sample['usable_exact_race_runner_reference_urls']==1
        assert sample['automatically_recovered_distances']==0
    print('PASS: Stage89 full-field coverage, missing evidence, exact race references, no invented distances')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--self-test',action='store_true')
    ap.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    ap.add_argument('--db',type=pathlib.Path)
    ap.add_argument('--ratings',type=pathlib.Path)
    ap.add_argument('--stage87-queue',type=pathlib.Path)
    ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage89'))
    ap.add_argument('--as-of',type=str,default='')
    ap.add_argument('--days',type=int,default=30)
    a=ap.parse_args()
    if a.self_test:self_test();return
    if not all(p is not None and p.is_file() for p in (a.db,a.ratings,a.stage87_queue)):
        ap.error('--db, --ratings and --stage87-queue must all exist')
    if not 1<=a.days<=90:ap.error('--days must be 1..90')
    import stage86_hpr_engine as hpr
    asof=hpr.day(a.as_of) if a.as_of else dt.datetime.now(ZoneInfo('Europe/Dublin')).date()
    if not asof:ap.error('Invalid as-of date')
    a.output.mkdir(parents=True,exist_ok=True)
    audit(a.root,a.db,a.ratings,a.stage87_queue,a.output,asof,a.days)

if __name__=='__main__':main()
