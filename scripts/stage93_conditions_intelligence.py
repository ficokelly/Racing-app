#!/usr/bin/env python3
"""Stage 93 — pre-race ground, distance and breeding suitability *evidence*.

Consumes Stage92 ex-post quality evidence and independently links the exact
Kaggle source row to sire and damsire via Stage91 pedigree-hash identity. For every horse-race record, uses only
that horse's strictly earlier race dates. No target-day or later observations,
SP, odds, D/D(c)/E predictions, or live HPR data are read or changed.

This is NOT a trained predictor or proof of causal ground/trip effects. Small
samples are labelled insufficient; no untried-trip probability is invented.
Original comments never appear in the output, only Stage91 evidence tags.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import re
import unicodedata
import datetime as dt
import gzip
import json
import math
import pathlib
import statistics
import tempfile

VERSION = 'stage93-conditions-pedigree-research-v2'
REQUIRED = {'date', 'course', 'horse', 'horse_identity', 'race_type', 'going',
            'quality_score', 'comment_tags', 'race_distance_furlongs'}
COLUMNS = [
    'date','course','off','source_race_id','horse','horse_identity','race_type',
    'going','going_group','surface_group','trip_furlongs','observed_quality_after_race',
    'historical_runs_before_date','historical_usable_quality_before_date',
    'prior_code_performances','prior_code_mean_quality','prior_code_recent_quality',
    'previous_same_ground_runs','previous_other_ground_runs',
    'ground_quality_delta_shrunk','ground_evidence','ground_notes',
    'previous_near_trip_runs','previous_other_trip_runs','trip_quality_delta_shrunk',
    'trip_evidence','trip_projection','trip_projection_evidence',
    'breeding_link_status','sire_name','damsire_name',
    'sire_ground_n_same','sire_ground_n_other','sire_trip_n_near','sire_trip_n_other',
    'damsire_ground_n_same','damsire_ground_n_other','damsire_trip_n_near','damsire_trip_n_other',
    'breeding_ground_signal','breeding_ground_confidence','breeding_trip_signal',
    'breeding_trip_confidence','breeding_ground_evidence','breeding_trip_evidence',
    'overall_evidence','notes','version'
]


def number(value):
    if value is None or str(value).strip() == '':
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def going_group(raw):
    """Return surface, common ground group. Never call unknown ground 'good'."""
    g = ' '.join(str(raw or '').strip().lower().replace('-', ' ').split())
    if not g:
        return ('UNKNOWN', 'UNKNOWN')
    if 'standard' in g:
        if 'slow' in g: return ('AW', 'STANDARD_TO_SLOW')
        if 'fast' in g: return ('AW', 'STANDARD_TO_FAST')
        return ('AW', 'STANDARD')
    # Turf categories. UK and Irish Going may be described differently.
    if 'good to yielding' in g: return ('TURF','GOOD_TO_YIELDING')
    if 'yielding to soft' in g: return ('TURF','YIELDING_TO_SOFT')
    if 'good to firm' in g: return ('TURF','GOOD_TO_FIRM')
    if 'good to soft' in g: return ('TURF','GOOD_TO_SOFT')
    if 'yielding' in g: return ('TURF','YIELDING')
    if 'heavy' in g: return ('TURF','HEAVY')
    if 'soft' in g: return ('TURF','SOFT')
    if 'firm' in g: return ('TURF','FIRM')
    if g == 'good' or g.startswith('good '): return ('TURF','GOOD')
    return ('UNKNOWN','UNKNOWN')


def code(raw):
    s = str(raw or '').strip().lower()
    if 'chase' in s: return 'CHASE'
    if 'hurdle' in s: return 'HURDLE'
    if 'nh flat' in s or 'national hunt flat' in s or 'bumper' in s: return 'NH_FLAT'
    if 'flat' in s: return 'FLAT'
    return 'UNKNOWN'


def safe_date(value):
    try: return dt.date.fromisoformat(str(value or '')[:10])
    except ValueError: return None


def shrink_delta(positive, comparative, pseudo=6, cap=15):
    if len(positive) < 2 or len(comparative) < 2:
        return None
    diff = statistics.mean(positive) - statistics.mean(comparative)
    effective = len(positive) * len(comparative) / (len(positive) + len(comparative))
    return round(max(-cap, min(cap, diff * effective / (effective + pseudo))), 2)


def prior_mean(rows):
    scored = [number(x.get('quality_score')) for x in rows]
    values = [v for v in scored if v is not None]
    return round(statistics.mean(values), 2) if values else None


def recent_mean(rows):
    # History only; evenly-weighted last three completed same-code starts.
    subset = [number(r.get('quality_score')) for r in rows]
    values = [v for v in subset if v is not None]
    return round(statistics.mean(values[-3:]), 2) if values else None


def analyze_preoff(row, history):
    """history rows MUST have date strictly less than current row date."""
    date = safe_date(row.get('date'))
    assert date is not None
    assert all(safe_date(p.get('date')) < date for p in history)
    horse_key = str(row.get('horse_identity') or '')
    surface, ground = going_group(row.get('going'))
    discipline = code(row.get('race_type'))
    trip = number(row.get('race_distance_furlongs'))
    # Stage91 pedigree keys identify *candidates* for one horse, not an
    # independent source-verified identity. No horse-name-only joins.
    can_join = horse_key.startswith('PEDIGREE_SHA256_') and discipline != 'UNKNOWN'
    prior = [p for p in history if code(p.get('race_type')) == discipline] if can_join else []
    usable = [p for p in prior if number(p.get('quality_score')) is not None]
    notes = []
    if not can_join: notes.append('UNVERIFIED_IDENTITY_OR_DISCIPLINE')
    if surface == 'UNKNOWN': notes.append('GOING_UNVERIFIED')
    if trip is None or trip <= 0: notes.append('TRIP_UNVERIFIED')
    if not usable: notes.append('NO_SAME_CODE_PRIOR_QUALITY')

    same = []; other = []
    if surface != 'UNKNOWN' and usable:
        for p in usable:
            p_surface,p_ground = going_group(p.get('going'))
            score = number(p.get('quality_score'))
            if p_surface != surface or p_ground == 'UNKNOWN': continue
            if p_ground == ground: same.append(score)
            else: other.append(score)
    ground_delta = shrink_delta(same, other)
    if surface == 'UNKNOWN': ground_evidence = 'UNKNOWN_GOING'
    elif len(same) >= 2 and len(other) >= 2:
        ground_evidence = 'PROVISIONAL_COMPARATIVE_4PLUS' if len(same) >= 4 and len(other) >= 4 else 'LOW_SAMPLE_COMPARATIVE'
    elif same: ground_evidence = 'SAME_GROUND_OBSERVED_NO_RELIABLE_CONTRAST'
    else: ground_evidence = 'UNTRIED_OR_UNAVAILABLE_GROUND'
    ground_notes = 'CONFOUNDED_BY_CLASS_PACE_FITNESS_TRACK_AND_TRIP' if ground_delta is not None else 'INSUFFICIENT_COMPARISON'

    near=[]; elsewhere=[]
    # Compare trips only with same discipline AND known comparable surface.
    trip_history=[]
    if trip and surface != 'UNKNOWN':
        for p in usable:
            p_trip = number(p.get('race_distance_furlongs'))
            p_surface,_ = going_group(p.get('going'))
            if p_trip is None or p_trip < 4 or p_surface != surface: continue
            trip_history.append(p)
            if abs(p_trip - trip) <= .75: near.append(number(p.get('quality_score')))
            elif abs(p_trip - trip) >= 1.5: elsewhere.append(number(p.get('quality_score')))
    trip_delta = shrink_delta(near, elsewhere)
    if trip is None: trip_evidence = 'UNKNOWN_TRIP'
    elif len(near) >= 2 and len(elsewhere) >= 2: trip_evidence = 'PROVISIONAL_COMPARATIVE'
    elif near: trip_evidence = 'SOME_TRIP_EXPERIENCE_NO_RELIABLE_CONTRAST'
    else: trip_evidence = 'UNTRIED_OR_UNAVAILABLE_TRIP'

    projection = 'NO_JUSTIFIED_PROJECTION'
    detail = 'NO_EXTRAPOLATED_WIN_PROBABILITY'
    # Only project untried trip *direction* from strictly older running tags.
    # This is a hypothesis only; breeding/pace/fitness not covered here.
    if trip and trip_history and not near:
        past_trips = [number(p.get('race_distance_furlongs')) for p in trip_history]
        last = trip_history[-6:]
        strong = sum('strong_finish' in str(p.get('comment_tags') or '').split('|') for p in last)
        fading = sum('weakened' in str(p.get('comment_tags') or '').split('|') for p in last)
        outpaced = sum('outpaced' in str(p.get('comment_tags') or '').split('|') for p in last)
        # Stable pattern requires multiple previous comments on same code/surface.
        if trip > max(past_trips) + .75:
            if strong >= 2 and strong > fading:
                projection = 'LONGER_TRIP_POSSIBLE_STAMINA_POSITIVE'
            elif fading >= 2 and fading > strong:
                projection = 'LONGER_TRIP_POSSIBLE_STAMINA_CAUTION'
            else:
                projection = 'LONGER_TRIP_UNTRIED_INSUFFICIENT'
        elif trip < min(past_trips) - .75:
            if outpaced >= 2:
                projection = 'SHORTER_TRIP_POSSIBLE_SPEED_CAUTION'
            elif fading >= 2:
                projection = 'SHORTER_TRIP_POSSIBLE_STAMINA_RELIEF'
            else:
                projection = 'SHORTER_TRIP_UNTRIED_INSUFFICIENT'
        else:
            projection = 'TRIP_WITHIN_HISTORY_RANGE_BUT_EXACT_TRIP_UNTRIED'
        detail = f'PRIOR_SAME_SURFACE_CODE_N={len(trip_history)};STRONG_FINISH_LAST6={strong};WEAKENED_LAST6={fading};OUTPACED_LAST6={outpaced};BREEDING_NOT_EVALUATED'
    elif trip and trip_history:
        projection = 'DIRECT_NEAR_TRIP_HISTORY_AVAILABLE'
        detail = f'PRIOR_NEAR_TRIP_N={len(near)};NO_EXTRAPOLATION'

    if not can_join: overall='NO_STABLE_MATCH'
    elif not usable: overall='NO_PRIOR_SCORED_HISTORY'
    elif ground_delta is not None and trip_delta is not None: overall='COMPARATIVE_GROUND_AND_TRIP_EVIDENCE'
    elif ground_delta is not None: overall='GROUND_COMPARISON_ONLY'
    elif trip_delta is not None: overall='TRIP_COMPARISON_ONLY'
    else: overall='PRIOR_FORM_ONLY_INSUFFICIENT_CONDITION_CONTRAST'

    q = number(row.get('quality_score'))
    return {
        'date':str(date),'course':row.get('course') or '', 'off':row.get('off') or '',
        'source_race_id':row.get('source_race_id') or '', 'horse':row.get('horse') or '',
        'horse_identity':horse_key, 'race_type':row.get('race_type') or '',
        'going':row.get('going') or '', 'going_group':ground,'surface_group':surface,
        'trip_furlongs':trip if trip is not None else '',
        'observed_quality_after_race':q if q is not None else '',
        'historical_runs_before_date':len(history) if can_join else 0,
        'historical_usable_quality_before_date':sum(number(p.get('quality_score')) is not None for p in history) if can_join else 0,
        'prior_code_performances':len(usable),
        'prior_code_mean_quality':prior_mean(usable) if usable else '',
        'prior_code_recent_quality':recent_mean(usable) if usable else '',
        'previous_same_ground_runs':len(same),'previous_other_ground_runs':len(other),
        'ground_quality_delta_shrunk':ground_delta if ground_delta is not None else '',
        'ground_evidence':ground_evidence,'ground_notes':ground_notes,
        'previous_near_trip_runs':len(near),'previous_other_trip_runs':len(elsewhere),
        'trip_quality_delta_shrunk':trip_delta if trip_delta is not None else '',
        'trip_evidence':trip_evidence,'trip_projection':projection,
        'trip_projection_evidence':detail,'overall_evidence':overall,
        'notes':'|'.join(notes),'version':VERSION,
    }


# Stage91's candidate identity hash is intentionally re-derived to validate
# the exact source race/runner join; it is not a cross-provider verified ID.
def cleaned(v):
    txt = unicodedata.normalize('NFKC', str(v or '')).lower().replace('’', "'")
    return re.sub(r'\s+', ' ', txt).strip()


def pedigree_hash(raw):
    horse,sire,dam=(cleaned(raw.get(k)) for k in ('horse','sire','dam'))
    if not horse or not sire or not dam: return ''
    value='|'.join((horse,sire,dam)).encode('utf-8')
    return 'PEDIGREE_SHA256_'+hashlib.sha256(value).hexdigest()[:24]


def source_identity_key(row):
    # Exact source race ID plus date/course/runner; never name-only.
    return (str(row.get('date') or '')[:10],
            re.sub('[^A-Z0-9]','',str(row.get('course') or '').upper()),
            str(row.get('source_race_id') or row.get('race_id') or '').strip(),
            re.sub('[^A-Z0-9]','',str(row.get('horse') or '').upper()))


def link_kaggle_pedigrees(records, source_dir):
    """Map Stage92 rows to the exact historical Kaggle CSV row and Stage91 hash.

    Returning only parent names is intentional: no copying original commentary
    or reusing any target race outcome as a predictive feature.
    """
    keys=collections.defaultdict(list)
    for i,row in enumerate(records):
        if str(row.get('horse_identity') or '').startswith('PEDIGREE_SHA256_'):
            keys[source_identity_key(row)].append(i)
    matches=collections.defaultdict(list)
    scanned=0
    first=min(x['date'] for x in records)
    last=max(x['date'] for x in records)
    for path in sorted(pathlib.Path(source_dir).rglob('*.csv')):
        if path.name.lower().startswith(('readme','metadata')):continue
        with path.open(encoding='utf-8-sig',errors='replace',newline='') as fh:
            reader=csv.DictReader(fh)
            if not {'date','course','horse','race_id','sire','dam','damsire'}.issubset(reader.fieldnames or []):continue
            for raw in reader:
                date=str(raw.get('date') or '')[:10]
                if not first<=date<=last:continue
                key=source_identity_key(raw)
                if key not in keys:continue
                h=pedigree_hash(raw)
                for idx in keys[key]:
                    if h and h==str(records[idx].get('horse_identity') or ''):
                        matches[idx].append((cleaned(raw.get('sire')),cleaned(raw.get('damsire'))))
                        scanned+=1
    result={}
    totals=collections.Counter()
    for idx,row in enumerate(records):
        pair=matches.get(idx,[])
        if len(pair)!=1:
            status='NO_EXACT_PEDIGREE_LINK' if not pair else 'MULTIPLE_SOURCE_ROWS_REVIEW'
            result[id(row)]={'sire':'','damsire':'','status':status}
            totals[status]+=1
        else:
            sire,ds=pair[0]
            status='SIRE_DAMSIRE_MATCHED' if sire and ds else 'SIRE_ONLY_MATCHED' if sire else 'NO_SIRE'
            result[id(row)]={'sire':sire,'damsire':ds,'status':status}
            totals[status]+=1
    totals['matched_source_rows']=scanned
    return result,dict(totals)


def sibling_contrast(observations, target_horse, condition, target, min_per_side=6, min_horses=3):
    """Pre-off, siblings only; parent associations are *not* causal claims.

    observations: (horse_identity, ground_group, furlongs, quality).
    """
    eligible=[r for r in observations if r[0]!=target_horse]
    if condition=='ground':
        left=[r for r in eligible if r[1]==target]
        right=[r for r in eligible if r[1] not in ('UNKNOWN','') and r[1]!=target]
    else:
        if target is None:return ('UNKNOWN',0,0,0,'NO_TARGET_TRIP')
        left=[r for r in eligible if r[2] is not None and abs(r[2]-target)<=1]
        right=[r for r in eligible if r[2] is not None and abs(r[2]-target)>=2]
    nl,nr=len(left),len(right)
    ul=len(set(r[0] for r in left));ur=len(set(r[0] for r in right))
    if nl<min_per_side or nr<min_per_side or ul<min_horses or ur<min_horses:
        return ('INSUFFICIENT',nl,nr,min(ul,ur),f'REQUIRES_{min_per_side}_RUNS_AND_{min_horses}_OFFSPRING_PER_GROUP')
    # Shrink strongly toward neutral; per-parent sample-size bias/confounding
    # make this a directional prior ONLY, not a calibrated win probability.
    diff=statistics.mean(r[3] for r in left)-statistics.mean(r[3] for r in right)
    effective=nl*nr/(nl+nr)
    delta=diff*effective/(effective+20)
    sig='POSITIVE' if delta>=2 else 'NEGATIVE' if delta<=-2 else 'NEUTRAL'
    return (sig,nl,nr,min(ul,ur),f'SHRUNK_ASSOCIATION_{delta:.2f}_QUALITY_POINTS')


def breeding_feature(row, link, parent_history):
    """Evaluate sire and damsire using progeny completed before target DATE."""
    surname=str(row.get('horse_identity') or '')
    surface,ground=going_group(row.get('going'))
    discipline=code(row.get('race_type'))
    trip=number(row.get('race_distance_furlongs'))
    base={'breeding_link_status':link['status'],'sire_name':link['sire'],
          'damsire_name':link['damsire']}
    ground_votes=[];trip_votes=[]
    for kind,pname in (('sire',link['sire']),('damsire',link['damsire'])):
        obs=parent_history.get((kind,pname,discipline,surface),[]) if pname and discipline!='UNKNOWN' and surface!='UNKNOWN' else []
        if ground!='UNKNOWN':
            gg=sibling_contrast(obs,surname,'ground',ground)
        else:gg=('UNKNOWN',0,0,0,'NO_GOING')
        tg=sibling_contrast(obs,surname,'trip',trip)
        base[kind+'_ground_n_same']=gg[1];base[kind+'_ground_n_other']=gg[2]
        base[kind+'_trip_n_near']=tg[1];base[kind+'_trip_n_other']=tg[2]
        base[kind+'_ground_status']=gg[0]
        base[kind+'_trip_status']=tg[0]
        if gg[0] in ('POSITIVE','NEGATIVE','NEUTRAL'):ground_votes.append(gg[0])
        if tg[0] in ('POSITIVE','NEGATIVE','NEUTRAL'):trip_votes.append(tg[0])
    for attr,votes in (('ground',ground_votes),('trip',trip_votes)):
        if not votes:
            sig='UNKNOWN';conf='INSUFFICIENT_PARENT_OFFSPRING_EVIDENCE'
        elif len(votes)==2 and votes[0]!=votes[1] and 'NEUTRAL' not in votes:
            sig='CONFLICTING';conf='DISAGREEING_SIRE_DAMSIRE'
        elif len(votes)==2 and votes[0]==votes[1]:
            sig=votes[0];conf='TWO_PARENT_CORROBORATION_PROVISIONAL'
        else:
            sig=next((x for x in votes if x!='NEUTRAL'),'NEUTRAL')
            conf='ONE_PARENT_DIRECTIONAL_ONLY'
        base['breeding_'+attr+'_signal']=sig
        base['breeding_'+attr+'_confidence']=conf
        base['breeding_'+attr+'_evidence']=';'.join(k+':'+base[k+'_'+attr+'_status'] for k in ('sire','damsire'))
    # All inferred breeding preferences are hypotheses; do not convert into
    # numerical win probabilities or treat them as validated individual traits.
    return {k:base.get(k,'') for k in COLUMNS if k.startswith(('breeding_','sire_','damsire_'))}


def add_to_parent_history(row, link, parent_history):
    q=number(row.get('quality_score'))
    horse=str(row.get('horse_identity') or '')
    surface,going=going_group(row.get('going'))
    discipline=code(row.get('race_type'))
    trip=number(row.get('race_distance_furlongs'))
    if q is None or not horse.startswith('PEDIGREE_SHA256_') or surface=='UNKNOWN' or discipline=='UNKNOWN':return
    for kind,pname in (('sire',link['sire']),('damsire',link['damsire'])):
        if pname:parent_history[(kind,pname,discipline,surface)].append((horse,going,trip,q))


def process(input_file, output_folder, max_rows=0, pedigree_source=None):
    output_folder=pathlib.Path(output_folder)
    output_folder.mkdir(parents=True,exist_ok=True)
    records=[]
    with gzip.open(input_file,'rt',encoding='utf-8',newline='') as source:
        reader=csv.DictReader(source)
        if not REQUIRED.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(f'Expected Stage92 fields missing: {sorted(REQUIRED-set(reader.fieldnames or []))}')
        for row in reader:
            if not safe_date(row.get('date')): continue
            records.append(row)
            if max_rows and len(records)>=max_rows:break
    if not records: raise RuntimeError('No Stage92 rows; abort rather than report an empty audit')
    # Sorting by date means target-day finishes never reach the pre-race history.
    records.sort(key=lambda r:(r['date'],r.get('course',''),r.get('off',''),r.get('horse','')))
    links,link_stats=link_kaggle_pedigrees(records,pedigree_source) if pedigree_source is not None else ({id(r):{'sire':'','damsire':'','status':'NO_PEDIGREE_SOURCE'} for r in records},{'NO_PEDIGREE_SOURCE':len(records)})
    history=collections.defaultdict(list)
    parent_history=collections.defaultdict(list)
    stats=collections.Counter()
    ground_counts=collections.Counter(); trip_counts=collections.Counter()
    sample=[]
    dest=output_folder/'stage93-prerace-condition-evidence.csv.gz'
    with gzip.open(dest,'wt',encoding='utf-8',newline='',compresslevel=6) as file:
        writer=csv.DictWriter(file,fieldnames=COLUMNS)
        writer.writeheader()
        cursor=0
        while cursor < len(records):
            current=records[cursor]['date']
            next_cursor=cursor
            while next_cursor<len(records) and records[next_cursor]['date']==current:
                next_cursor+=1
            # Evaluate every record for the day BEFORE writing any same-day outcomes.
            todays=records[cursor:next_cursor]
            for row in todays:
                identity = str(row.get('horse_identity') or '')
                candidate_history=history[identity] if identity.startswith('PEDIGREE_SHA256_') else []
                feature=analyze_preoff(row,candidate_history)
                breed=breeding_feature(row,links[id(row)],parent_history)
                feature.update(breed)
                writer.writerow(feature)
                stats['total_rows']+=1
                if feature['historical_usable_quality_before_date']:
                    stats['with_prior_quality']+=1
                if feature['ground_quality_delta_shrunk']!='':stats['ground_comparative']+=1
                if feature['trip_quality_delta_shrunk']!='':stats['trip_comparative']+=1
                if feature['trip_projection'].startswith(('LONGER_TRIP_POSSIBLE','SHORTER_TRIP_POSSIBLE')):
                    stats['untried_trip_hypothesis']+=1
                stats['overall_'+feature['overall_evidence']]+=1
                stats['breeding_ground_'+feature['breeding_ground_signal']]+=1
                stats['breeding_trip_'+feature['breeding_trip_signal']]+=1
                ground_counts[feature['going_group']]+=1
                trip_counts[feature['trip_projection']]+=1
                if len(sample)<150 and (feature['ground_quality_delta_shrunk']!='' or
                                        feature['trip_quality_delta_shrunk']!='' or
                                        feature['trip_projection'].startswith(('LONGER_TRIP_POSSIBLE','SHORTER_TRIP_POSSIBLE'))):
                    sample.append(feature)
            for row in todays:
                identity=str(row.get('horse_identity') or '')
                if identity.startswith('PEDIGREE_SHA256_'):
                    history[identity].append(row)
                add_to_parent_history(row,links[id(row)],parent_history)
            cursor=next_cursor
    with (output_folder/'stage93-review-sample.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader();w.writerows(sample)
    result={
        'stage':93,'version':VERSION,
        'source':'Stage92 per-run quality; NO future-date leakage',
        'status':'RESEARCH_ONLY_NO_MODEL_CHANGES',
        'start_date':records[0]['date'],'end_date':records[-1]['date'],
        'counts':dict(stats),'pedigree_link_audit':link_stats,'ground_distribution':dict(sorted(ground_counts.items())),
        'trip_projection_distribution':dict(sorted(trip_counts.items())),
        'rules':{
            'strictly_earlier_days_only':True,
            'join':'Stage91 pedigree candidate hash only, never horse name alone',
            'ground_comparison':'same discipline and same surface; at least 2 matching and 2 other historical performances; shrink difference',
            'trip_comparison':'same discipline and same surface; near within 0.75f versus other >=1.5f; at least 2 each',
            'untried_trip':'directional candidate label from repeated previous tags; no numerical prediction or rating adjustment',
            'fallback':'unverified evidence remains unknown',
            'breeding':'sire and damsire progeny, same race code+surface, prior completed dates only, exclude target horse, >=6 target and >=6 comparison runs from >=3 offspring each side; strong shrinkage',
        },
        'limitations':[
            'Association is not causation: ground/trip means are confounded by opposition, pace, class, fitness, changing ability and rider.',
            'Pedigree is matched from exact Kaggle row and Stage91 pedigree candidate hash; no horse-name-only joins.',
            'Breeding is a directional prior from sibling results, NOT proven inheritance, a causal effect or a win probability.',
            'Breeding analyses may be limited by only 365 days of Stage92 scored runs and by incorrect source pedigree identity.',
            'No pre-race win probability, ROI, tuned score or HPR v2 rating is generated.',
            'Stage92 observed field sizes are provisional and flagged.',
            'Most recent races after 23 October 2026 remain absent from this Kaggle source.',
            'Stage91 pedigree hashes are candidates for identity matching; no independent confirmation.',
            'Every same-day race uses information from previous DAYS only; outcome after this race cannot affect its own feature.',
            'Source licensing and comment interpretation accuracy must be validated before model training or deployment.',
            'No changes to frozen D, D(c), original E or HPR v0.'
        ],
    }
    (output_folder/'stage93-summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'total':stats['total_rows'],'with_prior_quality':stats['with_prior_quality'],
                      'ground_comparative':stats['ground_comparative'],'trip_comparative':stats['trip_comparative'],
                      'untried_trip_hypothesis':stats['untried_trip_hypothesis']},indent=2))
    return result


def self_test():
    assert going_group('Good to Soft')==('TURF','GOOD_TO_SOFT')
    assert going_group('Standard to Slow')==('AW','STANDARD_TO_SLOW')
    assert going_group('Soft')==('TURF','SOFT')
    assert going_group('Heavy')==('TURF','HEAVY')
    assert going_group('')==('UNKNOWN','UNKNOWN')
    assert shrink_delta([80], [40,40]) is None
    assert shrink_delta([80,80], [40,40])>0
    assert number('nan') is None
    # Breeding conditions are calculated from OTHER offspring, never target,
    # and only past-date records have been added to parent_history.
    mock_obs=[('SIB'+str(i),'SOFT',12.0,80.0) for i in range(8)]+[('SIB'+str(i),'GOOD',8.0,40.0) for i in range(8)]
    mock_hist={('sire','father','FLAT','TURF'):mock_obs,
               ('damsire','grandpa','FLAT','TURF'):mock_obs}
    target={'horse_identity':'TARGET','race_type':'Flat','going':'Soft','race_distance_furlongs':12}
    values=breeding_feature(target,{'sire':'father','damsire':'grandpa','status':'SIRE_DAMSIRE_MATCHED'},mock_hist)
    assert values['breeding_ground_signal']=='POSITIVE',values
    assert values['breeding_trip_signal']=='POSITIVE',values
    assert values['breeding_ground_confidence']=='TWO_PARENT_CORROBORATION_PROVISIONAL'
    assert breeding_feature(target,{'sire':'','damsire':'','status':'NO_EXACT_PEDIGREE_LINK'},mock_hist)['breeding_trip_signal']=='UNKNOWN'
    assert sibling_contrast([('h'+str(i%8),'SOFT',12,80) for i in range(8)]+[('h'+str(i%8),'GOOD',8,40) for i in range(8)],'target','ground','SOFT')[0]=='POSITIVE'
    assert sibling_contrast([('h'+str(i%8),'SOFT',12,80) for i in range(8)]+[('h'+str(i%8),'GOOD',8,40) for i in range(8)],'target','trip',12)[0]=='POSITIVE'
    with tempfile.TemporaryDirectory() as tmp:
        root=pathlib.Path(tmp)
        inp=root/'stage92.csv.gz'
        def r(date,name,going,trip,q,tags,identity='PEDIGREE_SHA256_testing'):
            return {'date':date,'course':'Ascot','off':'14:00','source_race_id':'r'+date+name,
                    'horse':name,'horse_identity':identity,'race_type':'Flat','going':going,
                    'quality_score':q,'comment_tags':tags,'race_distance_furlongs':trip}
        rows=[
            r('2026-09-01','Horse 1','Good','8',80,'strong_finish'),
            r('2026-09-02','Horse 1','Soft','8',20,'weakened'),
            r('2026-09-03','Horse 1','Good','8',80,'strong_finish'),
            r('2026-09-04','Horse 1','Soft','8',20,'weakened'),
            r('2026-09-05','Horse 1','Good','10',85,'strong_finish'),
            r('2026-09-06','Horse 1','Soft','10',25,''),
            r('2026-09-07','Horse 1','Good','12',60,'',identity='PEDIGREE_SHA256_testing'),
            r('2026-09-07','Horse 1','Good','14',90,'',identity='PEDIGREE_SHA256_testing'),
            r('2026-09-08','Horse 1','Good','14',40,''),
        ]
        with gzip.open(inp,'wt',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        report=process(inp,root/'out')
        with gzip.open(root/'out/stage93-prerace-condition-evidence.csv.gz','rt',newline='',encoding='utf-8') as f:
            data=list(csv.DictReader(f))
        first=data[0]
        assert first['historical_runs_before_date']=='0'
        seventh=[x for x in data if x['date']=='2026-09-07']
        assert len(seventh)==2
        assert all(x['historical_runs_before_date']=='6' for x in seventh),seventh
        assert all(x['ground_quality_delta_shrunk'] not in ('','0') for x in seventh)
        assert any(x['trip_projection']=='LONGER_TRIP_POSSIBLE_STAMINA_POSITIVE' for x in seventh),seventh
        assert any(x['historical_runs_before_date']=='8' for x in data if x['date']=='2026-09-08')
        assert report['counts']['total_rows']==9
    print('PASS Stage93: strict chronology, same-day isolation, ground evidence, untried-trip hypotheses, no assumed evidence')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=pathlib.Path,help='Stage92 stage92-per-run-quality.csv.gz')
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage93/output'))
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--max-rows',type=int,default=0,help='Development only; 0 means all rows')
    p.add_argument('--pedigree-source',type=pathlib.Path,default=None,help='Kaggle v129 downloaded CSV directory, read-only')
    a=p.parse_args()
    if a.self_test:self_test();return
    if not a.input or not a.input.is_file():p.error('Stage92 --input .csv.gz required')
    if a.max_rows<0:p.error('--max-rows must be nonnegative')
    if not a.pedigree_source or not a.pedigree_source.is_dir():p.error('--pedigree-source required, must be a directory')
    process(a.input,a.out,a.max_rows,a.pedigree_source)

if __name__=='__main__':
    main()
