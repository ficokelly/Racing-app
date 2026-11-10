#!/usr/bin/env python3
"""Stage 94 — HPR v2, strictly chronological research prototype.

Inputs: Stage92 completed-race quality and Stage93 PRE-RACE condition / breeding
features. Exports per-race pre- and post-race experimental HPR v2 history, not
production rankings or calibrated winning chances.

This never writes to an existing HPR store, result, live app, or D/D(c)/E code.
Every pre-race score uses strictly earlier racing DATES only.
Stage92 score already includes comment tags: do not add comment bonuses twice.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import gzip
import json
import math
import pathlib
import re
import statistics
import tempfile

VERSION = 'hpr-v2-research-2026-11-10'
S92 = 'stage92-performance-quality-research-v1'
S93 = 'stage93-conditions-pedigree-research-v2'
REQ92 = {'date','course','off','source_race_id','horse','horse_identity','race_type',
         'quality_score','evidence_confidence','review_flags','comment_tags','score_version'}
REQ93 = {'date','course','off','source_race_id','horse','horse_identity',
         'ground_quality_delta_shrunk','trip_quality_delta_shrunk',
         'breeding_ground_signal','breeding_ground_confidence',
         'breeding_trip_signal','breeding_trip_confidence',
         'overall_evidence','version'}
COLUMNS = [
  'date','course','off','source_race_id','horse','horse_identity','race_type',
  'stage92_quality','stage92_confidence','prior_same_code_runs',
  'pre_race_hpr_v2','pre_race_confidence','ground_ability_correction',
  'trip_ability_correction','total_ability_correction',
  'completed_run_hpr_v2','post_race_hpr_v2','post_race_confidence',
  'ground_prior','trip_prior','breeding_ground_signal','breeding_trip_signal',
  'comment_tags','review_flags','status','version'
]


def norm(x):
    return re.sub(r'[^A-Z0-9]','',str(x or '').upper())


def stable_key(row):
    return (str(row.get('date') or '')[:10],norm(row.get('course')),
            str(row.get('off') or '').strip(),str(row.get('source_race_id') or '').strip(),
            str(row.get('horse_identity') or '').strip(),norm(row.get('horse')))


def number(x):
    try:
        if x is None or str(x).strip()=='':return None
        value=float(x)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):return None


def clamp(value, low, high):
    return min(high,max(low,value))


def category(raw):
    s=str(raw or '').lower()
    if 'chase' in s:return 'CHASE'
    if 'hurdle' in s:return 'HURDLE'
    if 'nh flat' in s or 'bumper' in s or 'national hunt flat' in s:return 'NH_FLAT'
    if 'flat' in s:return 'FLAT'
    return 'UNKNOWN'


def conf_from_count(n):
    if not n:return 'NO_PRIOR_FORM'
    if n<3:return 'LOW_SAMPLE'
    if n<5:return 'MODERATE_SAMPLE'
    return 'FIVE_PLUS_RUNS_NOT_CALIBRATED'


def weighted_recent(values):
    """Prefer latest five date-eligible runs, linear recency weights 1..5."""
    series=list(values)[-5:]
    if not series:return None
    w=list(range(1,len(series)+1))
    return round(sum(v*i for v,i in zip(series,w))/sum(w),2)


def directional_breeding(signal, confidence):
    """A *small* speculative condition correction, not causal inheritance."""
    if signal not in ('POSITIVE','NEGATIVE'):return 0.
    conf=str(confidence or '')
    magnitude=1.5 if conf=='TWO_PARENT_CORROBORATION_PROVISIONAL' else (
              .65 if conf=='ONE_PARENT_DIRECTIONAL_ONLY' else 0.)
    # Favorable target conditions inflate observed performance relative to
    # unknown neutral setting; do NOT claim these are verified adjustments.
    return -magnitude if signal=='POSITIVE' else magnitude


def neutral_condition_correction(evidence):
    """Bounded, speculative normalisation of observed performance quality.

    Corrections shrink strongly, use only pre-race evidence, never SP or odds.
    Existing horse comparisons outrank breeding; do not double count.
    """
    own_ground=number(evidence.get('ground_quality_delta_shrunk'))
    own_trip=number(evidence.get('trip_quality_delta_shrunk'))
    if own_ground is not None:
        ground=clamp(-.22*own_ground,-3.0,3.0)
        ground_kind='SELF_COMPARISON_PROVISIONAL'
    else:
        ground=directional_breeding(evidence.get('breeding_ground_signal'),evidence.get('breeding_ground_confidence'))
        ground_kind='BREEDING_DIRECTIONAL_ONLY' if ground else 'UNKNOWN_NEUTRAL'
    if own_trip is not None:
        trip=clamp(-.22*own_trip,-3.0,3.0)
        trip_kind='SELF_COMPARISON_PROVISIONAL'
    else:
        trip=directional_breeding(evidence.get('breeding_trip_signal'),evidence.get('breeding_trip_confidence'))
        trip_kind='BREEDING_DIRECTIONAL_ONLY' if trip else 'UNKNOWN_NEUTRAL'
    total=clamp(ground+trip,-4.0,4.0)
    return round(ground,2),round(trip,2),round(total,2),ground_kind,trip_kind


def evaluate(target, evidence, past):
    base=number(target.get('quality_score'))
    confidence=target.get('evidence_confidence') or 'UNKNOWN'
    usable=base is not None and base>=0 and base<=100
    pre=weighted_recent(past)
    ground,trip,adjust,gr,dr=neutral_condition_correction(evidence)
    # Stage92 is already a placing+beaten-distance+comment index. Keep that
    # evidence primary (80%); condition correction contributes at most +/-0.8.
    completed=round(clamp(base + .20*adjust,0,100),2) if usable else None
    after=weighted_recent([*past,completed]) if completed is not None else pre
    status='SCORED' if completed is not None else 'UNSCORED_INSUFFICIENT_RACE_EVIDENCE'
    if target.get('review_flags') and completed is not None:
        status='SCORED_WITH_REVIEW_FLAGS'
    return {
        'date':target.get('date'),'course':target.get('course'),
        'off':target.get('off'),'source_race_id':target.get('source_race_id'),
        'horse':target.get('horse'),'horse_identity':target.get('horse_identity'),
        'race_type':target.get('race_type'),
        'stage92_quality':base if base is not None else '',
        'stage92_confidence':confidence,
        'prior_same_code_runs':len(past),
        'pre_race_hpr_v2':pre if pre is not None else '',
        'pre_race_confidence':conf_from_count(len(past)),
        'ground_ability_correction':ground,
        'trip_ability_correction':trip,
        'total_ability_correction':adjust,
        'completed_run_hpr_v2':completed if completed is not None else '',
        'post_race_hpr_v2':after if after is not None else '',
        'post_race_confidence':conf_from_count(len(past)+(1 if completed is not None else 0)),
        'ground_prior':gr,'trip_prior':dr,
        'breeding_ground_signal':evidence.get('breeding_ground_signal') or '',
        'breeding_trip_signal':evidence.get('breeding_trip_signal') or '',
        'comment_tags':target.get('comment_tags') or '',
        'review_flags':target.get('review_flags') or '',
        'status':status,'version':VERSION,
    }


def read_rows(path, required, ver, column):
    with gzip.open(path,'rt',encoding='utf-8',newline='') as f:
        reader=csv.DictReader(f)
        missing=required-set(reader.fieldnames or [])
        if missing:raise RuntimeError(f'{path.name} lacks {sorted(missing)}')
        rows=list(reader)
    if not rows:raise RuntimeError(f'{path.name} is empty')
    mismatches=sum(bool(x.get(column)) and x[column]!=ver for x in rows)
    if mismatches:raise RuntimeError(f'{path.name} has {mismatches} unexpected source versions')
    return rows


def process(stage92_path,stage93_path,out):
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    base=read_rows(stage92_path,REQ92,S92,'score_version')
    evidence=read_rows(stage93_path,REQ93,S93,'version')
    e_map=collections.defaultdict(list)
    for e in evidence:e_map[stable_key(e)].append(e)
    t_map=collections.defaultdict(list)
    for r in base:t_map[stable_key(r)].append(r)
    usable_pairs=[]
    bad=collections.Counter()
    for k,lst in t_map.items():
        if len(lst)!=1:
            bad['duplicate_stage92_key']+=len(lst)
            continue
        e=e_map.get(k,[])
        if len(e)!=1:
            bad['stage93_missing_or_ambiguous']+=1
            continue
        target=lst[0]
        if not str(target.get('horse_identity') or '').startswith('PEDIGREE_SHA256_'):
            bad['horse_identity_not_stable_candidate']+=1
            continue
        if category(target.get('race_type'))=='UNKNOWN':
            bad['race_type_unknown']+=1
            continue
        if number(target.get('quality_score'))!=number(e[0].get('observed_quality_after_race')):
            bad['observed_quality_mismatch']+=1
            continue
        usable_pairs.append((target,e[0]))
    bad['orphan_stage93_rows']=sum(len(lst) for k,lst in e_map.items() if k not in t_map)
    usable_pairs.sort(key=lambda x:(x[0]['date'],x[0].get('course',''),x[0].get('off',''),x[0].get('horse','')))
    history=collections.defaultdict(list)
    counts=collections.Counter();scores=[];pre_values=[];review=[]
    path=out/'stage94-hpr-v2-research.csv.gz'
    with gzip.open(path,'wt',encoding='utf-8',newline='',compresslevel=6) as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS);writer.writeheader()
        index=0
        while index<len(usable_pairs):
            date=usable_pairs[index][0]['date']
            end=index
            while end<len(usable_pairs) and usable_pairs[end][0]['date']==date:end+=1
            updates=[]
            for target,e in usable_pairs[index:end]:
                identity=target['horse_identity']
                c=category(target.get('race_type'))
                prior=list(history[(identity,c)])
                result=evaluate(target,e,prior)
                writer.writerow(result)
                counts['output_rows']+=1
                if result['completed_run_hpr_v2']!='':
                    counts['post_race_scored']+=1
                    scores.append(result['completed_run_hpr_v2'])
                    updates.append((identity,c,result['completed_run_hpr_v2']))
                else:counts['unscored']+=1
                if result['pre_race_hpr_v2']!='':
                    counts['pre_race_rating_available']+=1
                    pre_values.append(result['pre_race_hpr_v2'])
                if result['ground_prior']!='UNKNOWN_NEUTRAL':counts['ground_adjustment_evidence']+=1
                if result['trip_prior']!='UNKNOWN_NEUTRAL':counts['trip_adjustment_evidence']+=1
                if len(review)<150 and (result['ground_ability_correction'] or result['trip_ability_correction'] or result['review_flags']):
                    review.append(result)
            # Apply all date outcomes together: race later the same day cannot
            # receive any earlier same-day run outcome in pre_race_hpr_v2.
            for identity,c,score in updates:
                history[(identity,c)].append(score)
            index=end
    with (out/'stage94-review-sample.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader();w.writerows(review)
    report={
        'stage':94,'version':VERSION,'status':'RESEARCH_ONLY_NO_MODEL_CHANGES',
        'source_stage92':stage92_path.name,'source_stage93':stage93_path.name,
        'input_rows':{'stage92':len(base),'stage93':len(evidence)},
        'matching_exclusions':dict(bad),'counts':dict(counts),
        'post_run_mean_score':round(statistics.mean(scores),2) if scores else None,
        'pre_race_mean_score':round(statistics.mean(pre_values),2) if pre_values else None,
        'formula':{
            'observed_run_quality':'Stage92 completed-race quality, including comments only ONCE',
            'ground_condition_correction':'min/max( -0.22 * strictly prior same-horse ground delta, +/-3); otherwise small sire/damsire directional correction',
            'trip_condition_correction':'min/max( -0.22 * strictly prior same-horse trip delta, +/-3); otherwise small sire/damsire directional correction',
            'total_correction_bounds':[-4,4],
            'completed_run_hpr_v2':'clamp(Stage92_quality + 0.20 * total_correction,0,100)',
            'pre_race_hpr_v2':'recency weighted mean of last up to 5 completed HPR-v2 runs on prior DAYS, within race discipline',
            'post_race_hpr_v2':'same recency weighted rolling mean including newly completed run',
        },
        'safety':{
            'source_is_read_only':True,'same_day_outcomes_excluded_pre_race':True,
            'no_odds_sp':True,'no_live_changes':True,'not_calibrated_probability':True,
        },
        'limitations':[
            'All HPR v2 numerical formula weights are conservative research hypotheses, NOT statistically fitted or validated.',
            'Stage92 quality derives partly from finish placing; may strongly correlate with winner status without predicting it.',
            'Cross-race performance quality is not yet normalised for class, opposition, pace and track; do not call this an official ability rating.',
            'Stage93 breeding signal measures historic association, not causation; condition adjustment is speculative.',
            'Pedigree identities are candidates, not independent verified horse IDs; mismatches are excluded.',
            'Source snapshot ends 2026-10-23; it cannot cover subsequent races without richer data.',
            'Stage95 must evaluate strictly pre-race features on disjoint held-out dates, compare unchanged HPR v0 and D/E, and calibrate win probabilities.',
            'No modification to D, D(c), E, original HPR, production racecards or results.',
        ],
    }
    (out/'stage94-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if not counts['post_race_scored']:
        raise RuntimeError('No scored rows: refusing success on empty audit')
    print(json.dumps({'stage':94,'scored_runs':counts['post_race_scored'],
                      'pre_race_ratings':counts['pre_race_rating_available'],
                      'exclusions':dict(bad)},indent=2),flush=True)
    return report


def self_test():
    assert neutral_condition_correction({'ground_quality_delta_shrunk':'-10','trip_quality_delta_shrunk':'5'})[2]>0
    assert neutral_condition_correction({'breeding_ground_signal':'NEGATIVE','breeding_ground_confidence':'TWO_PARENT_CORROBORATION_PROVISIONAL'})[0]==1.5
    assert directional_breeding('POSITIVE','ONE_PARENT_DIRECTIONAL_ONLY')==-0.65
    assert weighted_recent([10,20,30])==round((10+40+90)/6,2)
    assert weighted_recent([]) is None
    horse={'date':'2026-09-03','horse_identity':'PEDIGREE_SHA256_x','horse':'Horse A','race_type':'Flat','quality_score':'70',
           'evidence_confidence':'PROVISIONAL_STRUCTURED','score_version':S92}
    context={'breeding_ground_signal':'UNKNOWN','breeding_trip_signal':'UNKNOWN','version':S93}
    first=evaluate(horse,context,[])
    assert first['pre_race_hpr_v2']==''
    assert first['post_race_hpr_v2']==70
    second=evaluate(horse,context,[70])
    assert second['pre_race_hpr_v2']==70
    with tempfile.TemporaryDirectory() as tmp:
        root=pathlib.Path(tmp);p92=root/'s92.gz';p93=root/'s93.gz';out=root/'out'
        rows92=[];rows93=[]
        for date,q in [('2026-09-01',40),('2026-09-01',50),('2026-09-02',60)]:
            i=len(rows92)
            key={'date':date,'course':'Ascot','off':f'1{i}:00','source_race_id':f'race{i}',
                 'horse':'Horse A','horse_identity':'PEDIGREE_SHA256_test'}
            rows92.append({**key,'race_type':'Flat','quality_score':str(q),
                           'evidence_confidence':'PROVISIONAL_STRUCTURED','score_version':S92,
                           'comment_tags':'','review_flags':''})
            rows93.append({**key,'version':S93,'observed_quality_after_race':str(q),
                           'ground_quality_delta_shrunk':'','trip_quality_delta_shrunk':'',
                           'breeding_ground_signal':'UNKNOWN','breeding_ground_confidence':'INSUFFICIENT',
                           'breeding_trip_signal':'UNKNOWN','breeding_trip_confidence':'INSUFFICIENT',
                           'overall_evidence':'NO_PRIOR_SCORED_HISTORY'})
        for path,rows in [(p92,rows92),(p93,rows93)]:
            with gzip.open(path,'wt',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=sorted(set().union(*(set(row) for row in rows))));w.writeheader();w.writerows(rows)
        report=process(p92,p93,out)
        with gzip.open(out/'stage94-hpr-v2-research.csv.gz','rt',newline='',encoding='utf-8') as f:
            records=list(csv.DictReader(f))
        assert records[0]['pre_race_hpr_v2']==''
        assert records[1]['pre_race_hpr_v2']==''
        assert records[2]['pre_race_hpr_v2']=='46.67',records[2]
        assert report['counts']['post_race_scored']==3
    print('PASS Stage94: exact joins, version gates, bounded neutralisation, same-day isolation, pre/post chronology')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage92',type=pathlib.Path)
    p.add_argument('--stage93',type=pathlib.Path)
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage94/output'))
    p.add_argument('--self-test',action='store_true')
    args=p.parse_args()
    if args.self_test:self_test();return
    if not args.stage92 or not args.stage92.exists():p.error('Stage92 per-run artifact required')
    if not args.stage93 or not args.stage93.exists():p.error('Stage93 pre-race artifact required')
    process(args.stage92,args.stage93,args.out)

if __name__=='__main__':main()
