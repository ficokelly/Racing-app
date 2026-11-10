#!/usr/bin/env python3
"""Stage 90: audit the latest Kaggle result CSV against recent Hot Pots result gaps.

READ ONLY. No model, historical database, results or live app changes.
Candidate records are review leads and must not be merged automatically.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
import tempfile
import unicodedata
import urllib.request
from zoneinfo import ZoneInfo

DATASET = 'deltaromeo/horse-racing-results-ukireland-2015-2025'
LOCAL = ZoneInfo('Europe/Dublin')
BASELINE = {'version':129,'source_runner_rows':5113709,'last_result':'2026-10-23',
            'missing_comments':825975,'missing_btn':4302}

def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '').upper())
    return re.sub('[^A-Z0-9]', '', ''.join(ch for ch in s if not unicodedata.combining(ch)))

def course(s):
    return norm(re.sub(r'\((?:AW|IRE|GB|UK)\)', '', str(s or ''), flags=re.I))

def raceclock(s):
    s = str(s or '').strip()
    if 'T' in s:
        try:
            t = dt.datetime.fromisoformat(s.replace('Z','+00:00'))
            return t.astimezone(LOCAL).strftime('%H:%M') if t.tzinfo else ''
        except ValueError: return ''
    m = re.search(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)', s)
    return f'{int(m.group(1)):02d}:{m.group(2)}' if m else ''

def racekey(d, c, t, h):
    return (str(d or '')[:10], course(c), raceclock(t), norm(h))

def kaggle_metadata():
    url = 'https://www.kaggle.com/api/v1/datasets/view/' + DATASET
    req = urllib.request.Request(url, headers={'Accept':'application/json', 'User-Agent':'RacingHotPots-Research-Audit/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            info = json.loads(resp.read(350000).decode('utf-8'))
        return {'status':'ok','version':info.get('currentVersionNumber'),
                'last_updated':info.get('lastUpdated'),'size_bytes':info.get('totalBytes')}
    except Exception as exc:
        return {'status':'unavailable','reason':type(exc).__name__,'version':None}

def load_recent(root, first, last):
    recent = collections.defaultdict(list)
    stats = collections.Counter()
    for file in sorted((root/'data/racing-results').glob('*.json')):
        if not first <= file.stem[:10] <= last: continue
        try: data = json.loads(file.read_text(encoding='utf-8'))
        except (ValueError, OSError): stats['unreadable_files'] += 1; continue
        for race in data.get('results', []):
            if race.get('status') != 'final': continue
            date = str(race.get('date') or file.stem)[:10]
            off = race.get('off_dt') or race.get('off') or race.get('off_time')
            for runner in race.get('runners', []):
                pos = runner.get('position')
                if not str(pos or '').isdigit() or int(pos) <= 0: continue
                stats['finished_recent_runner_rows'] += 1
                key = racekey(date,race.get('course'),off,runner.get('horse'))
                if not all(key):
                    stats['missing_matching_identity'] += 1; continue
                has_comment = bool(str(runner.get('comment') or runner.get('comments') or '').strip())
                has_margin = bool(str(runner.get('overall_beaten') or runner.get('beaten') or runner.get('margin') or '').strip())
                recent[key].append({'position':int(pos), 'missing_comment':not has_comment,
                                    'missing_overall_beaten':not has_margin})
    return recent, dict(stats)

def audit(source_dir,root,out,first,last,metadata):
    out.mkdir(parents=True,exist_ok=True)
    recent,stats = load_recent(root,first,last)
    coverage = collections.defaultdict(collections.Counter)
    possible = collections.defaultdict(list)
    source_files = []
    earliest = latest = None
    rejected_dates = 0
    for file in sorted(source_dir.rglob('*.csv')):
        if file.name.lower().startswith(('readme','metadata')): continue
        count = 0
        with file.open(newline='',encoding='utf-8-sig',errors='replace') as fh:
            reader = csv.DictReader(fh)
            if not {'date','course','off','horse','pos'}.issubset(set(reader.fieldnames or [])): continue
            for lineno,row in enumerate(reader,2):
                if not row.get('horse'): continue
                d = str(row.get('date') or '')[:10]
                try: dt.date.fromisoformat(d)
                except ValueError: rejected_dates += 1; continue
                count += 1
                earliest = min(earliest,d) if earliest else d
                latest = max(latest,d) if latest else d
                c = coverage[d[:7]]
                c['runners'] += 1
                for fld in ('btn','ovr_btn','comment'):
                    c['with_'+fld] += int(bool(str(row.get(fld) or '').strip()))
                if first <= d <= last:
                    key = racekey(d,row.get('course'),row.get('off'),row.get('horse'))
                    if key in recent:
                        fingerprint = hashlib.sha256(json.dumps({field:row.get(field) for field in ('date','course','off','horse','pos','btn','ovr_btn','comment')},sort_keys=True).encode()).hexdigest()
                        possible[key].append({'pos':str(row.get('pos') or ''),
                                              'overall_beaten_available':bool(str(row.get('ovr_btn') or '').strip()),
                                              'comment_available':bool(str(row.get('comment') or '').strip()),
                                              'file':file.name,'line':lineno,'fingerprint':fingerprint})
        source_files.append({'name':file.name,'rows':count})
    with (out/'stage90-coverage-by-month.csv').open('w',newline='',encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['month','runners','distance_present','distance_missing','comments_present','comments_missing'])
        for ym,c in sorted(coverage.items()):
            n=c['runners']
            w.writerow([ym,n,c['with_ovr_btn'],n-c['with_ovr_btn'],c['with_comment'],n-c['with_comment']])
    queue,needs_review = [],collections.Counter()
    for key,records in sorted(recent.items()):
        if len(records) != 1:
            needs_review['recent_duplicate_or_conflicting_runner'] += 1; continue
        runner = records[0]
        if not (runner['missing_comment'] or runner['missing_overall_beaten']): continue
        choices = possible.get(key,[])
        if len(choices) != 1:
            needs_review['not_exactly_one_kaggle_candidate'] += 1; continue
        match = choices[0]
        if match['pos'] != str(runner['position']):
            needs_review['conflicting_finishing_position'] += 1; continue
        comment = runner['missing_comment'] and match['comment_available']
        beaten = runner['missing_overall_beaten'] and match['overall_beaten_available']
        if comment or beaten:
            queue.append({'date':key[0],'course':key[1],'off':key[2],'horse':key[3],
                          'position':runner['position'],'new_comment':int(comment),
                          'new_overall_beaten':int(beaten),'source_file':match['file'],
                          'source_line':match['line'],'source_row_sha256':match['fingerprint'],
                          'status':'CANDIDATE_ONLY_VERIFY_LICENSE_AND_EXACT_IDENTITY'})
    with (out/'stage90-recovery-candidates.csv').open('w',newline='',encoding='utf-8') as fh:
        fields=['date','course','off','horse','position','new_comment','new_overall_beaten',
                'source_file','source_line','source_row_sha256','status']
        w = csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(queue)
    total=collections.Counter()
    for vals in coverage.values(): total.update(vals)
    report = {'stage':90,'safety':'READ_ONLY_NO_IMPORT_NO_MODEL_EDITS',
              'dataset':DATASET,'publisher_metadata':metadata,'previous_v129':BASELINE,
              'latest_data':{'earliest':earliest,'latest':latest,'rows':total['runners'],
                             'missing_overall_beaten':total['runners']-total['with_ovr_btn'],
                             'missing_comment':total['runners']-total['with_comment'],
                             'invalid_dates':rejected_dates,'files':source_files},
              'window':{'from':first,'through':last},'recent_results':{'slots':len(recent),**stats},
              'recovery_candidates':{'runner_rows':len(queue),
                                     'margin':sum(x['new_overall_beaten'] for x in queue),
                                     'comments':sum(x['new_comment'] for x in queue),
                                     'unmatched':dict(needs_review)},
              'matching':'Strict date, normalized course, LOCAL OFF and horse, plus exact finishing position. No name-only career joins.',
              'warnings':['Candidate rows are not verified imports; source IDs and license must be checked.',
                          'The newest Kaggle release may still be stale. A re-download cannot recover unpublished data.',
                          'No source comments or beaten values are copied into this report.',
                          'No changes made to historical HPR, D, D(c), E, odds or racecards.']}
    (out/'stage90-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'publisher_version':metadata.get('version'),
                      'latest_source_date':latest,'candidate_runners':len(queue),
                      'candidate_comments':report['recovery_candidates']['comments'],
                      'candidate_distances':report['recovery_candidates']['margin']},indent=2))
    return report

def selftest():
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td)
        source=root/'source'; source.mkdir()
        (root/'data/racing-results').mkdir(parents=True)
        (source/'races.csv').write_text('date,course,off,horse,pos,btn,ovr_btn,comment\n'
            '2026-11-09,Dundalk (AW) (IRE),18:30,Test Runner,3,1,2,Stayed on\n',encoding='utf-8')
        (root/'data/racing-results/2026-11-09.json').write_text(json.dumps({'results':[
            {'date':'2026-11-09','course':'Dundalk (AW)','off_dt':'2026-11-09T18:30:00Z',
             'status':'final','runners':[{'horse':'Test Runner','position':3,'status':'ran'}]}]}))
        r=audit(source,root,root/'out','2026-11-09','2026-11-10',{'version':130})
        assert r['recovery_candidates']['runner_rows']==1
        assert r['recovery_candidates']['comments']==1
        assert r['recovery_candidates']['margin']==1
    print('PASS Stage 90 audit self-test')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    ap.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage90'))
    ap.add_argument('--since',default='2026-10-24')
    ap.add_argument('--as-of',default=dt.datetime.now(LOCAL).date().isoformat())
    ap.add_argument('--dataset-dir',type=pathlib.Path,default=None)
    ap.add_argument('--self-test',action='store_true')
    args=ap.parse_args()
    if args.self_test: selftest(); return
    if dt.date.fromisoformat(args.since)>dt.date.fromisoformat(args.as_of):
        ap.error('since must be before or equal to as-of')
    meta={'status':'local_dataset'} if args.dataset_dir else kaggle_metadata()
    if args.dataset_dir: source=args.dataset_dir
    else:
        import kagglehub
        source=pathlib.Path(kagglehub.dataset_download(DATASET))
    audit(source,args.root,args.out,args.since,args.as_of,meta)
if __name__=='__main__':main()
