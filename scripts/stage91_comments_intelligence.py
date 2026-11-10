#!/usr/bin/env python3
"""Stage 91 - UK/IE historical running-comment evidence, research only.

Read exact Kaggle CSV rows; do not modify databases, model ratings or selections.
Emit extracted signals and carefully parsed beaten margins for Stage92/93 review.
A comment describes a completed race and MUST NOT be used to predict that race.
Original commentary is NOT copied into the artifacts; licensing still needs review.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import random
import re
import sys
import tempfile
import unicodedata
from zoneinfo import ZoneInfo

VERSION = 'stage91-comment-evidence-v1'
DATASET = 'deltaromeo/horse-racing-results-ukireland-2015-2025'
DATASET_VERSION = 129
FIELDS_NEEDED = {'date', 'course', 'horse', 'pos', 'comment', 'ovr_btn', 'btn'}
LOCALE = ZoneInfo('Europe/Dublin')

# These are evidence TAGS, not learned scores. A rule match is not a causal claim.
# Keep matched terms short and auditable; suppress simple negations.
RULES = {
    'strong_finish': [r'\bstayed on(?: strongly| well| late)?\b', r'\bran on(?: well| strongly| late)?\b',
                      r'\bfinished (?:well|strongly|fast)\b', r'\bkept on (?:strongly|well)\b',
                      r'\bfinished with (?:a )?rattle\b'],
    'weakened': [r'\bweakened\b', r'\bfaded\b', r'\btired (?:rapidly|quickly|late)\b',
                 r'\bno extra\b', r'\bsoon beaten\b', r'\bweary\b'],
    'interference': [r'\bhampered\b', r'\bdenied (?:a )?clear run\b',
                     r'\b(?:no|not a|without a) clear run\b', r'\bshort of room\b',
                     r'\bblocked\b', r'\bchecked\b', r'\bbumped\b',
                     r'\b(?:forced|carried) wide\b', r'\bcrowded\b'],
    'slow_start': [r'\bslowly away\b', r'\bmissed (?:the )?break\b',
                   r'\bawkward(?:ly)? away\b', r'\bslow(?:ly)? (?:to )?start\b'],
    'jumping_error': [r'\bblundered\b', r'\b(?:bad|serious) mistake\b',
                      r'\b(?:made a )?mistake(?: at| \d)\b', r'\bnot fluent\b',
                      r'\bjumped (?:badly|poorly|left|right)\b', r'\bpecked on landing\b'],
    'jumping_positive': [r'\bjumped (?:well|fluently)\b', r'\bgood jump\b'],
    'raced_keenly': [r'\b(?:raced|ran) freely\b', r'\bpulled hard\b', r'\bkeen early\b',
                     r'\b(?:raced|was) keen\b'],
    'effort_conserved': [r'\beased (?:down|up)\b', r'\bnot knocked about\b',
                         r'\bnot (?:fully )?extended\b'],
    'ridden_out': [r'\b(?:ridden|pushed) out\b'],
    'held_up': [r'\bheld up\b', r'\bfrom (?:the )?rear\b', r'\bin rear\b'],
    'prominent': [r'\bprominent\b', r'\btracked (?:the )?leaders\b',
                  r'\bmade all\b', r'\bdisputed (?:the )?lead\b'],
    'outpaced': [r'\boutpaced\b', r'\bstruggling to keep up\b',
                 r'\bunable to quicken\b'],
}
COMPILED = {tag: tuple(re.compile(p, re.I) for p in phrases) for tag, phrases in RULES.items()}
CANCEL_WORDS = re.compile(r'\b(?:not|never|didnt|didn\'t|hardly|wasnt|wasn\'t|without|failed to)\s+$', re.I)
# Some negative rules contain their own negation and should not be suppressed.
NEGATION_EXEMPT = {'no extra', 'not fluent', 'no clear run', 'not a clear run',
                   'without a clear run', 'not knocked about', 'not extended',
                   'not fully extended'}


def cleaned(value):
    s = unicodedata.normalize('NFKC', str(value or '')).lower()
    s = s.replace('’', "'")
    return re.sub(r'\s+', ' ', s).strip()


def signals_from_comment(value):
    text = cleaned(value)
    if not text:
        return [], 'NO_COMMENT', 0
    tags = []
    for tag, patterns in COMPILED.items():
        matched = False
        for pattern in patterns:
            for m in pattern.finditer(text):
                phrase = m.group(0)
                prefix = text[max(0, m.start() - 16):m.start()]
                if phrase not in NEGATION_EXEMPT and CANCEL_WORDS.search(prefix):
                    continue
                matched = True
                break
            if matched:
                break
        if matched:
            tags.append(tag)
    # Avoid treating a mixed passage as unambiguously good or bad.
    mixed = 'strong_finish' in tags and 'weakened' in tags
    if not tags:
        assessment = 'UNCLASSIFIED'
    elif mixed:
        assessment = 'MIXED_FINISHING_EVIDENCE'
    elif 'strong_finish' in tags and 'weakened' not in tags:
        assessment = 'POSITIVE_FINISHING_EVIDENCE'
    elif 'weakened' in tags and 'strong_finish' not in tags:
        assessment = 'NEGATIVE_FINISHING_EVIDENCE'
    else:
        assessment = 'OTHER_CONTEXT_ONLY'
    return sorted(tags), assessment, int(mixed)


def parse_margin(raw, is_winner=False):
    """Return (lengths or None, reason). 'dist' is deliberately NOT guessed."""
    if is_winner:
        return 0.0, 'WINNER_ASSUMED_ZERO'
    # Replace vulgar fractions BEFORE NFKC: NFKC turns ½ into a different fraction slash.
    raw_text = str(raw or '').lower().strip()
    raw_text = raw_text.replace('½', ' 1/2').replace('¼', ' 1/4').replace('¾', ' 3/4')
    s = cleaned(raw_text)
    if not s:
        return None, 'MISSING'
    aliases = {'nse': .06, 'nose': .06, 'snk': .12,
               'shd': .10, 'sh': .10, 'short head': .10,
               'hd': .20, 'head': .20, 'nk': .30, 'neck': .30,
               'dh': 0.0, 'dead heat': 0.0}
    if s in aliases:
        return aliases[s], 'APPROXIMATE_SHORT_MARGIN'
    if s in ('dist', 'distance', 'dis', 'many', 'many lengths', 'well beaten'):
        return None, 'UNQUANTIFIED_LONG_MARGIN'
    s = re.sub(r'\s*(?:lengths?|lens?|l)\s*$', '', s).strip()
    m = re.fullmatch(r'(\d+)\s+(\d+)/(\d+)', s)
    if m and int(m.group(3)):
        v = int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    else:
        m = re.fullmatch(r'(\d+)/(\d+)', s)
        if m and int(m.group(2)):
            v = int(m.group(1)) / int(m.group(2))
        else:
            try:
                if not re.fullmatch(r'\d+(?:\.\d+)?', s):
                    return None, 'UNPARSEABLE'
                v = float(s)
            except ValueError:
                return None, 'UNPARSEABLE'
    if not 0 <= v <= 200:
        return None, 'OUT_OF_RANGE'
    return round(v, 4), 'NUMERIC'


def numeric_placing(raw):
    s = str(raw or '').strip()
    m = re.fullmatch(r'0*(\d+)(?:st|nd|rd|th)?', s, re.I)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def furlongs(raw):
    s = cleaned(raw).replace(' ', '')
    m = re.fullmatch(r'(\d+)m(?:(\d+)f)?(?:\d+y)?', s)
    if m:
        return float(8 * int(m.group(1)) + int(m.group(2) or 0))
    m = re.fullmatch(r'(\d+(?:\.\d+)?)f', s)
    if m:
        return float(m.group(1))
    return None


def horse_identity(row):
    # Never join careers solely by horse name.
    horse = cleaned(row.get('horse'))
    sire, dam = cleaned(row.get('sire')), cleaned(row.get('dam'))
    if horse and sire and dam:
        source = '|'.join([horse, sire, dam])
        return 'PEDIGREE_SHA256_' + hashlib.sha256(source.encode()).hexdigest()[:24], 'PEDIGREE_CANDIDATE_KEY_NOT_VERIFIED'
    return '', 'UNVERIFIED_NO_PEDIGREE'


def annotate(row):
    comment = row.get('comment') or ''
    pos = numeric_placing(row.get('pos'))
    overall, overall_status = parse_margin(row.get('ovr_btn'), is_winner=pos == 1)
    behind_previous, previous_status = parse_margin(row.get('btn'), is_winner=pos == 1)
    tags, assessment, mixed = signals_from_comment(comment)
    hid, hstatus = horse_identity(row)
    contextual = []
    if pos is not None and pos >= 4 and overall is not None and overall <= 2.0:
        contextual.append('CLOSE_FINISH_DESPITE_LOW_POSITION')
    if pos is not None and pos > 1 and overall is not None and overall >= 20.0:
        contextual.append('WELL_BEATEN_IN_DISTANCE')
    if overall is None:
        contextual.append('OVERALL_MARGIN_UNKNOWN')
    if 'strong_finish' in tags and 'outpaced' in tags:
        contextual.append('POTENTIAL_STAMINA_OR_PACE_NOTE_NOT_PROVEN')
    if 'strong_finish' in tags and 'weakened' in tags:
        contextual.append('CONFLICTING_COMMENT_SIGNALS')
    if 'interference' in tags:
        contextual.append('POSSIBLE_EXCUSE_NEEDS_REVIEW')
    if 'jumping_error' in tags:
        contextual.append('POSSIBLE_JUMPING_COST_NEEDS_REVIEW')
    # Comment and margin are post-race observations. No prediction/ROI/rating here.
    is_comment_available = bool(cleaned(comment))
    confidence = ('NO_COMMENT' if not is_comment_available else
                  'REVIEW' if mixed else 'RULE_MATCH' if tags else 'UNCLASSIFIED_COMMENT')
    return {
        'date': str(row.get('date') or '')[:10], 'course': row.get('course') or '',
        'off': row.get('off') or '', 'source_race_id': row.get('race_id') or '',
        'horse': row.get('horse') or '', 'horse_identity': hid,
        'identity_quality': hstatus, 'position': pos if pos is not None else '',
        'finish_raw': str(row.get('pos') or ''), 'going': row.get('going') or '',
        'race_distance': row.get('dist') or '',
        'distance_furlongs_approx': furlongs(row.get('dist')) or '',
        'race_type': row.get('type') or '', 'race_class': row.get('class') or '',
        'overall_beaten_lengths': overall if overall is not None else '',
        'overall_margin_status': overall_status,
        'behind_previous_lengths': behind_previous if behind_previous is not None else '',
        'previous_margin_status': previous_status,
        'comment_available': int(is_comment_available),
        'comment_sha256': hashlib.sha256(str(comment).encode()).hexdigest() if is_comment_available else '',
        'signals': '|'.join(tags), 'assessment': assessment,
        'context_flags': '|'.join(contextual), 'interpretation_quality': confidence,
    }


COLS = list(annotate({'date': '', 'horse': ''}).keys())


def source_paths(folder):
    return sorted(p for p in pathlib.Path(folder).rglob('*.csv')
                  if not p.name.lower().startswith(('readme', 'metadata')))


def process(source_dir, out, begin, end, max_reviews=100, ukie_only=True):
    import stage86_hpr_engine as hpr
    out.mkdir(parents=True, exist_ok=True)
    reports = collections.Counter()
    months = collections.defaultdict(collections.Counter)
    signals = collections.Counter()
    margin_status = collections.Counter()
    reviews = []
    rng = random.Random(91)
    sample_seen = 0
    stream = out / 'stage91-per-run-evidence.csv.gz'
    with gzip.open(stream, 'wt', encoding='utf-8', newline='', compresslevel=6) as gz:
        writer = csv.DictWriter(gz, fieldnames=COLS)
        writer.writeheader()
        for path in source_paths(source_dir):
            reports['csv_files_seen'] += 1
            with path.open(encoding='utf-8-sig', errors='replace', newline='') as fh:
                reader = csv.DictReader(fh)
                if not FIELDS_NEEDED.issubset(set(reader.fieldnames or [])):
                    reports['unrecognised_csv_files'] += 1
                    continue
                for raw in reader:
                    date = str(raw.get('date') or '')[:10]
                    if date < begin or date > end:
                        continue
                    try:
                        dt.date.fromisoformat(date)
                    except ValueError:
                        reports['invalid_dates'] += 1
                        continue
                    if ukie_only and hpr.course(raw.get('course')) not in hpr.UKIE_COURSES:
                        reports['outside_known_ukie_course_list'] += 1
                        continue
                    if not raw.get('horse'):
                        reports['no_horse'] += 1
                        continue
                    feature = annotate(raw)
                    feature['date'] = date
                    writer.writerow(feature)
                    reports['rated_historical_runs'] += 1
                    monthly = months[date[:7]]
                    monthly['runs'] += 1
                    if feature['comment_available']:
                        reports['comments_present'] += 1
                        monthly['comments_present'] += 1
                    if feature['overall_margin_status'] in ('NUMERIC','APPROXIMATE_SHORT_MARGIN','WINNER_ASSUMED_ZERO'):
                        reports['parsed_margin'] += 1
                        monthly['parsed_margin'] += 1
                    if feature['interpretation_quality'] == 'RULE_MATCH':
                        reports['interpretable_comments'] += 1
                    if feature['interpretation_quality'] == 'REVIEW':
                        reports['mixed_comments_needing_review'] += 1
                    if feature['horse_identity']:
                        reports['pedigree_key_available'] += 1
                    if feature['position'] == '':
                        reports['unclassified_or_nonfinish_position'] += 1
                    margin_status[feature['overall_margin_status']] += 1
                    for tag in filter(None, feature['signals'].split('|')):
                        signals[tag] += 1
                    # Fixed-seed reservoir sample; no original text leaked.
                    if feature['comment_available'] and (feature['signals'] or feature['context_flags']):
                        sample_seen += 1
                        if len(reviews) < max_reviews:
                            reviews.append(feature.copy())
                        else:
                            i = rng.randint(0, sample_seen - 1)
                            if i < max_reviews:
                                reviews[i] = feature.copy()
    with (out / 'stage91-monthly-coverage.csv').open('w', encoding='utf-8', newline='') as fh:
        wr = csv.writer(fh)
        wr.writerow(['month', 'runner_rows', 'comments_present', 'comments_missing',
                     'overall_margin_parsed', 'overall_margin_not_parsed'])
        for month, c in sorted(months.items()):
            wr.writerow([month, c['runs'], c['comments_present'], c['runs'] - c['comments_present'],
                         c['parsed_margin'], c['runs'] - c['parsed_margin']])
    with (out / 'stage91-review-sample.csv').open('w', encoding='utf-8', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=COLS)
        writer.writeheader()
        writer.writerows(reviews)
    n = reports['rated_historical_runs']
    if n == 0:
        raise RuntimeError('No qualifying UK/IE historical runner rows in date window; do not treat empty audit as successful')
    report = {
        'stage': 91, 'version': VERSION, 'dataset': DATASET, 'dataset_version': DATASET_VERSION,
        'scope': {'from': begin, 'through': end, 'region': 'UK/IE known courses' if ukie_only else 'all source courses'},
        'counts': dict(reports), 'comment_signal_counts': dict(sorted(signals.items())),
        'margin_parse_status': dict(sorted(margin_status.items())),
        'coverage_pct': {
            'comment_available': round(100 * reports['comments_present'] / n, 3) if n else 0,
            'overall_margin_parseable_including_winners': round(100 * reports['parsed_margin'] / n, 3) if n else 0,
            'comments_with_rule_matches_of_all_runs': round(100 * reports['interpretable_comments'] / n, 3) if n else 0,
            'pedigree_matching_key_present': round(100 * reports['pedigree_key_available'] / n, 3) if n else 0,
        },
        'notes': [
            'Only evidence tags and contextual flags are extracted: this is NOT HPR v2 and no horse win probabilities are assigned.',
            'Overall beaten margin (ovr_btn) is used for distance to winner, while btn is kept separately for distance behind prior runner.',
            'The source may use nonnumeric margins such as dist; these remain unquantified, not guessed.',
            'Comment interpretation uses transparent provisional phrase rules, not a trained language model; examples require manual validation.',
            'No individual comment text is republished; verify source licence before wider usage.',
            'Pedigree-based keys are matching candidates, not independently verified IDs; unkeyed histories must not join by name.',
            'Ground and trip remain context for Stage93; never infer suitability causally from one run.',
            'Target race comments are post-race information and can only affect later predictions, never that race.',
            'Frozen Models D and D(c), E, and HPR v0 were not changed.',
        ],
        'files': {'features': stream.name, 'monthly': 'stage91-monthly-coverage.csv', 'sample': 'stage91-review-sample.csv'},
    }
    (out / 'stage91-summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'stage':91, 'total_runs': n, 'comments_present': reports['comments_present'],
                      'parsed_overall_margin': reports['parsed_margin'],
                      'comments_with_unmixed_rule_evidence': reports['interpretable_comments'],
                      'flags_need_review': reports['mixed_comments_needing_review']}, indent=2), flush=True)
    return report


def self_test():
    # Every test uses fictional horses, no copyrighted commentary or claims about actual runs.
    tests = [
        ('Stayed on strongly inside final furlong', 'strong_finish'),
        ('Weakened quickly 2 out', 'weakened'),
        ('Denied a clear run, stayed on', 'interference'),
        ('Raced freely, pulled hard', 'raced_keenly'),
        ('Mistake 3 out, not fluent', 'jumping_error'),
        ('Slowly away, held up', 'slow_start'),
        ('Never weakened', None),
        ("Did not stay on", None),
        ('No extra late on', 'weakened'),
        ('Stayed on but weakened close home', 'strong_finish'),
    ]
    for phrase, code in tests:
        tags, _, _ = signals_from_comment(phrase)
        if code:
            assert code in tags, (phrase, tags)
        else:
            assert not ('weakened' in tags or 'strong_finish' in tags), (phrase, tags)
    assert 'MIXED' in signals_from_comment('Stayed on but weakened')[1]
    assert parse_margin('2 1/2L')[0] == 2.5
    assert parse_margin('1½L')[0] == 1.5
    assert parse_margin('3/4 L')[0] == .75
    assert parse_margin('hd')[0] == .2
    assert parse_margin('dist')[0] is None
    assert parse_margin('') == (None, 'MISSING')
    assert numeric_placing('PU') is None
    assert numeric_placing('7th') == 7
    assert furlongs('1m 4f') == 12
    assert furlongs('2m') == 16
    one = annotate({'date':'2026-09-21','horse':'Horse A','pos':'7','ovr_btn':'2L', 'comment':'Stayed on strongly'})
    two = annotate({'date':'2026-09-21','horse':'Horse B','pos':'7','ovr_btn':'29L','comment':'Weakened'})
    assert 'CLOSE_FINISH_DESPITE_LOW_POSITION' in one['context_flags']
    assert 'WELL_BEATEN_IN_DISTANCE' in two['context_flags']
    # The test also works outside the full repository; normal GitHub runs
    # import the real, unchanged stage86_hpr_engine UK/IE course definitions.
    try:
        import stage86_hpr_engine  # noqa: F401
    except ModuleNotFoundError:
        import types
        sys.modules['stage86_hpr_engine'] = types.SimpleNamespace(
            course=lambda x: re.sub(r'[^A-Z0-9]', '', re.sub(r'\((?:AW|IRE|GB)\)', '', str(x), flags=re.I).upper()),
            UKIE_COURSES={'ASCOT', 'DUNDALK'})
    with tempfile.TemporaryDirectory() as tmp:
        root=pathlib.Path(tmp)
        folder=root/'source'; folder.mkdir()
        with (folder/'races.csv').open('w',encoding='utf-8',newline='') as fh:
            writer=csv.DictWriter(fh,fieldnames=['date','course','off','race_id','horse','sire','dam','pos','comment','ovr_btn','btn','going','dist','type','class'])
            writer.writeheader()
            writer.writerow({'date':'2026-09-21','course':'Ascot','off':'14:00','race_id':'xyz','horse':'Horse A',
                             'sire':'Alpha','dam':'Beta','pos':'7','comment':'Stayed on strongly','ovr_btn':'2L',
                             'going':'Good','dist':'1m 4f'})
            writer.writerow({'date':'2026-09-21','course':'Ascot','off':'14:00','race_id':'xyz','horse':'Horse B',
                             'sire':'Gamma','dam':'Delta','pos':'7','comment':'Weakened','ovr_btn':'29L',
                             'going':'Soft','dist':'1m 4f'})
            writer.writerow({'date':'2026-09-21','course':'Santa Anita','off':'14:00','race_id':'other','horse':'Horse C',
                             'pos':'1','comment':'Made all','ovr_btn':'0'})
        r=process(folder,root/'output','2026-09-20','2026-09-22',max_reviews=20)
        assert r['counts']['rated_historical_runs']==2,r
        assert r['counts']['outside_known_ukie_course_list']==1
        assert r['counts']['comments_present']==2
    print('PASS: Stage 91 parsing, rules, safety and sample audit')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=pathlib.Path)
    parser.add_argument('--dataset-version',type=int,default=DATASET_VERSION)
    parser.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage91'))
    parser.add_argument('--days',type=int,default=365,help='0 means all available history')
    parser.add_argument('--as-of',type=str,default=dt.datetime.now(LOCALE).date().isoformat())
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--all-courses',action='store_true',help='Diagnostic only: do not limit to known UK/IE course list')
    args=parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.days<0 or args.days>20000:parser.error('days must be 0 to 20000')
    try: end=dt.date.fromisoformat(args.as_of)
    except ValueError:parser.error('as-of must be YYYY-MM-DD')
    begin=(end-dt.timedelta(days=args.days-1)).isoformat() if args.days else '1988-01-01'
    if args.source_dir:
        source=args.source_dir
    else:
        import kagglehub
        source=pathlib.Path(kagglehub.dataset_download(DATASET+'/versions/'+str(args.dataset_version)))
    if not source.exists():parser.error('source path not found')
    process(source,args.out,begin,end.isoformat(),ukie_only=not args.all_courses)


if __name__ == '__main__':
    main()
