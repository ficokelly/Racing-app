#!/usr/bin/env python3
"""Stage 92: research-only completed-race quality index from Stage91 evidence.

Not a trained predictor, HPR replacement, official rating or win probability.
Uses no pre-off prices or SP; never changes any D/D(c)/E/HPR database.
Completed-race observations may inform only strictly LATER-race predictions.
Original racing commentary is not reproduced; feature tags only.
"""
from __future__ import annotations
import argparse, collections, csv, gzip, json, math, pathlib, re, tempfile
from statistics import mean

VERSION = 'stage92-performance-quality-research-v1'
INPUT_COLS = {'date','course','off','horse','source_race_id','position','finish_raw',
              'overall_beaten_lengths','overall_margin_status','signals',
              'comment_available','interpretation_quality','distance_furlongs_approx'}
EXCLUDE = {'NR', 'NON RUNNER', 'NON-RUNNER', 'WITHDRAWN', 'SCRATCHED', 'DNS'}
POSITIONS = {'POSITIVE_FINISHING_EVIDENCE','NEGATIVE_FINISHING_EVIDENCE'}
OUTPUT_COLS = [
 'date','course','off','source_race_id','horse','horse_identity','position',
 'observed_starter_count','field_size_status','race_type','going','race_distance',
 'race_distance_furlongs','overall_beaten_lengths','margin_status',
 'normalized_margin_per_mile','placement_component','margin_component',
 'comment_adjustment','quality_score','quality_band','evidence_confidence',
 'comment_tags','interpretation_quality','review_flags','score_version',
]


def parse_positive_integer(value):
    try:
        v=int(str(value).strip())
        return v if v>0 else None
    except (ValueError,TypeError):
        return None


def num(value):
    if value in ('',None): return None
    try:
        v=float(value)
        return v if math.isfinite(v) and v >= 0 else None
    except (ValueError,TypeError):
        return None


def norm(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def race_key(row):
    """Source race ID is mandatory; no ambiguous course/time-only race merges."""
    rid=str(row.get('source_race_id') or '').strip()
    date=str(row.get('date') or '')[:10]
    venue=norm(row.get('course'))
    if not (rid and date and venue):return None
    return (date,venue,rid)


def with_score(row, field_size, duplicate=False):
    pos=parse_positive_integer(row.get('position'))
    margin=num(row.get('overall_beaten_lengths'))
    tags=set(filter(None,str(row.get('signals') or '').split('|')))
    mixed=('strong_finish' in tags and 'weakened' in tags)
    furlongs=num(row.get('distance_furlongs_approx'))
    code=str(row.get('race_type') or '').lower()
    is_jump=bool(re.search(r'chase|hurdle|national hunt|nh flat|bumper',code))
    flags=[]
    if duplicate:flags.append('DUPLICATED_RACE_HORSE')
    if field_size<2:flags.append('FIELD_TOO_SMALL_OR_UNVERIFIED')
    if pos is None:flags.append('NON_FINISH_OR_UNCLASSIFIED')
    if pos is not None and pos>field_size:flags.append('POSITION_EXCEEDS_OBSERVED_FIELD')
    if margin is None:flags.append('NO_NUMERIC_OVERALL_MARGIN')
    if margin is not None and pos is not None and pos>1 and margin==0:flags.append('ZERO_MARGIN_NON_WINNER_REVIEW')
    if row.get('overall_margin_status')=='WINNER_ASSUMED_ZERO' and pos!=1:
        flags.append('WINNER_MARGIN_STATUS_CONFLICT')
    if row.get('overall_margin_status') not in {'NUMERIC','APPROXIMATE_SHORT_MARGIN','WINNER_ASSUMED_ZERO'}:
        flags.append('MARGIN_STATUS_UNRELIABLE')
    if mixed:flags.append('CONFLICTING_COMMENT_FINISH')
    if 'interference' in tags:flags.append('POSSIBLE_INTERFERENCE_EXCUSE')
    if 'jumping_error' in tags:flags.append('JUMPING_ERROR_MAY_REDUCE_ABILITY_ESTIMATE')
    if not furlongs or furlongs<5:flags.append('TRIP_UNVERIFIED')
    if not row.get('horse_identity'):flags.append('HORSE_IDENTITY_NOT_PROVEN')

    # An observed starter count from source rows is provisional, and not an
    # official declared field. Exclude partial/ambiguous race evidence from scores.
    serious={'DUPLICATED_RACE_HORSE','FIELD_TOO_SMALL_OR_UNVERIFIED',
             'NON_FINISH_OR_UNCLASSIFIED','POSITION_EXCEEDS_OBSERVED_FIELD',
             'NO_NUMERIC_OVERALL_MARGIN','ZERO_MARGIN_NON_WINNER_REVIEW',
             'WINNER_MARGIN_STATUS_CONFLICT','MARGIN_STATUS_UNRELIABLE'}
    placement=margin_component=adjusted=quality=norm_margin=None
    if not (set(flags)&serious):
        placement=100.0*(field_size-pos)/(field_size-1)
        # Normalise by race trip. In 3m NH races a 20L margin is not treated as
        # directly equivalent to a 20L deficit over 5f, while retaining signal.
        # Missing/short trip uses an 8f neutral reference, flagged for review.
        effective_furlongs=furlongs if furlongs and furlongs>=5 else 8.0
        norm_margin=round(margin*8.0/effective_furlongs,3)
        typical_scale=10.0 if is_jump else 7.0
        margin_component=100.0*math.exp(-norm_margin/typical_scale)
        baseline=.48*placement+.52*margin_component
        # Restricted +/−8-point modifier. Excuses stay flags, not invented
        # lengths recovered. Mixed/eased-down phrases do not force upgrades.
        modifier=0.0
        if not mixed:
            if 'strong_finish' in tags:modifier+=4.0
            if 'weakened' in tags:modifier-=4.0
        if 'outpaced' in tags:modifier-=2.0
        if 'jumping_error' in tags:modifier-=1.0
        if 'slow_start' in tags:modifier-=1.0
        if 'raced_keenly' in tags:modifier-=1.0
        # Interference is a possible excuse, not proven lost lengths.
        modifier=max(-8.0,min(8.0,modifier))
        adjusted=round(modifier,2)
        quality=round(max(0.,min(100.,baseline+modifier)),2)
    if quality is None:band='UNSCORED'
    elif quality>=80:band='VERY_STRONG_EX_POST'
    elif quality>=60:band='STRONG_EX_POST'
    elif quality>=40:band='MIXED_EX_POST'
    else:band='WEAK_EX_POST'
    # A high confidence tag requires both a comment and numerical margin;
    # candidate pedigree key is NOT confirmed cross-source horse identity.
    if quality is None:confidence='INSUFFICIENT'
    elif not row.get('comment_available') or str(row.get('comment_available'))=='0':confidence='LIMITED_NO_COMMENT'
    elif mixed or 'TRIP_UNVERIFIED' in flags or 'HORSE_IDENTITY_NOT_PROVEN' in flags:
        confidence='REVIEW'
    elif row.get('interpretation_quality')!='RULE_MATCH':confidence='LIMITED_UNCLASSIFIED_COMMENT'
    else:confidence='PROVISIONAL_STRUCTURED'
    out={
        'date':row.get('date'),'course':row.get('course'),'off':row.get('off'),
        'source_race_id':row.get('source_race_id'),'horse':row.get('horse'),
        'horse_identity':row.get('horse_identity'),'position':pos or '',
        'observed_starter_count':field_size,'field_size_status':'OBSERVED_NOT_OFFICIAL',
        'race_type':row.get('race_type'),'going':row.get('going'),
        'race_distance':row.get('race_distance'),'race_distance_furlongs':furlongs if furlongs is not None else '',
        'overall_beaten_lengths':margin if margin is not None else '',
        'margin_status':row.get('overall_margin_status'),
        'normalized_margin_per_mile':norm_margin if norm_margin is not None else '',
        'placement_component':round(placement,2) if placement is not None else '',
        'margin_component':round(margin_component,2) if margin_component is not None else '',
        'comment_adjustment':adjusted if adjusted is not None else '',
        'quality_score':quality if quality is not None else '',
        'quality_band':band,'evidence_confidence':confidence,
        'comment_tags':'|'.join(sorted(tags)),
        'interpretation_quality':row.get('interpretation_quality'),
        'review_flags':'|'.join(flags),'score_version':VERSION,
    }
    return out


def is_starter(row):
    return str(row.get('finish_raw') or '').strip().upper() not in EXCLUDE


def process(source, out, from_date=None, through=None):
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    rows=collections.defaultdict(list)
    stats=collections.Counter();by_code=collections.defaultdict(collections.Counter)
    with gzip.open(source,'rt',encoding='utf-8',newline='') as fin:
        reader=csv.DictReader(fin)
        if not INPUT_COLS.issubset(set(reader.fieldnames or [])):
            raise RuntimeError('Stage91 expected schema absent; refusing incomplete evidence')
        for r in reader:
            d=str(r.get('date') or '')[:10]
            if (from_date and d<from_date) or (through and d>through):continue
            stats['input_rows']+=1
            k=race_key(r)
            if k is None:
                stats['missing_race_id']+=1;continue
            rows[k].append(r)
    review=[]
    with gzip.open(out/'stage92-per-run-quality.csv.gz','wt',encoding='utf-8',newline='') as fout:
        writer=csv.DictWriter(fout,fieldnames=OUTPUT_COLS);writer.writeheader()
        for key,rr in sorted(rows.items()):
            # Exclude non-runners from observed field count; still score
            # non-finishers only as UNSCORED, never force a margin.
            starters=[x for x in rr if is_starter(x)]
            count=len(starters)
            horse_counts=collections.Counter(norm(x.get('horse')) for x in rr)
            for row in rr:
                duplicate=horse_counts[norm(row.get('horse'))]>1
                result=with_score(row,count,duplicate)
                writer.writerow(result)
                stats['output_rows']+=1
                stats['scored' if result['quality_score']!='' else 'unscored']+=1
                stats['confidence_'+result['evidence_confidence']]+=1
                stats['band_'+result['quality_band']]+=1
                if result['quality_score']!='':
                    c=by_code[str(row.get('race_type') or 'UNKNOWN')]
                    c['scored']+=1;c['sum_score_cents']+=int(round(100*float(result['quality_score'])))
                if result['review_flags']:
                    for flag in result['review_flags'].split('|'):
                        stats['flag_'+flag]+=1
                if len(review)<120 and result['review_flags']:
                    review.append(result)
    with (out/'stage92-review-sample.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=OUTPUT_COLS);writer.writeheader();writer.writerows(review)
    code_summary=[{'race_type':x,'scored':c['scored'],
                   'mean_provisional_quality':round(c['sum_score_cents']/100/c['scored'],2)}
                  for x,c in sorted(by_code.items(), key=lambda kv:-kv[1]['scored'])]
    report={'stage':92,'version':VERSION,'status':'RESEARCH_ONLY_NO_MODEL_CHANGES',
      'source':str(pathlib.Path(source).name),'scope':{'from':from_date,'through':through},
      'counts':dict(stats),'race_types':code_summary,
      'formula':{'placement_weight':0.48,'overall_margin_weight':0.52,
        'normalized_margin':'overall_beaten_lengths * 8 / race_furlongs',
        'comment_modifier_bounded_points':[-8,8],
        'jumper_margin_scale':10,'other_margin_scale':7},
      'notes':[
        'Exploratory ex-post performance-quality index only, NOT a win probability, HPR v2, or empirically fitted score.',
        'Field size is observed runners from source rows, not verified official declarations; duplicate races are flagged.',
        'Missing/non-numeric margins are not imputed; unfinished runners do not receive a quality score.',
        'One interference report is a possible excuse only; no invented compensating lengths are added.',
        'A horse on different ground/trip is not yet rated for suitability; Stage93 tests within-horse evidence.',
        'No source commentary text is republished; only tags from Stage91 retained.',
        'Source licence and editorial comment use must be checked before deployment.',
        'Use only earlier dates when later evaluating pre-race selection accuracy. Frozen D, D(c), E, HPR unchanged.',
      ]}
    (out/'stage92-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if not stats['scored']:
        raise RuntimeError('No eligible scored runners; do not treat empty output as success')
    print(json.dumps({'stage':92,'total':stats['output_rows'],'scored':stats['scored'],'unscored':stats['unscored'],
                      'requires_trip_review':stats['flag_TRIP_UNVERIFIED'],
                      'possible_interference':stats['flag_POSSIBLE_INTERFERENCE_EXCUSE']},indent=2))
    return report


def self_test():
    def sample(name,pos,margin,comment,source='NUMERIC'):
        return {'date':'2026-09-17','course':'Ascot','source_race_id':'r-1','off':'14:00','horse':name,
                'horse_identity':'PEDIGREE_test_'+name,'position':str(pos),'finish_raw':str(pos),
                'overall_beaten_lengths':str(margin),'overall_margin_status':source,
                'distance_furlongs_approx':'12','race_distance':'1m 4f','race_type':'Flat',
                'signals':comment,'interpretation_quality':'RULE_MATCH','comment_available':'1'}
    a=with_score(sample('Horse A',7,2,'strong_finish'),10)
    b=with_score(sample('Horse B',7,29,'weakened'),10)
    assert a['quality_score']>b['quality_score']+25,(a,b)
    assert a['margin_component']>b['margin_component']
    assert a['overall_beaten_lengths']==2
    assert with_score(sample('Horse C',7,'','weakened','MISSING'),10)['quality_score']==''
    assert with_score(sample('Horse D',2,0,'strong_finish'),10)['quality_score']==''
    assert with_score(sample('Horse E',2,5,'strong_finish|weakened'),10)['comment_adjustment']==0
    assert with_score(sample('Horse F',2,5,'jumping_error'),10)['comment_adjustment']<=0
    assert with_score(sample('Horse G',2,5,'interference'),10)['comment_adjustment']==0
    assert with_score(sample('Horse A',7,2,'strong_finish'),10,True)['quality_score']==''
    with tempfile.TemporaryDirectory() as td:
        root=pathlib.Path(td)
        src=root/'input.csv.gz'
        cols=sorted(INPUT_COLS|{'race_type','going','race_distance','horse_identity'})
        with gzip.open(src,'wt',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
            w.writerows([sample('Horse A',7,2,'strong_finish'),sample('Horse B',7,29,'weakened')]+
                        [sample('Horse '+str(i),i,5,'') for i in range(1,7)])
        result=process(src,root/'out')
        assert result['counts']['scored']==8,result
    print('PASS Stage92: margin/placing contrast, missing evidence, contradictory comments, identity and source-read tests')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=pathlib.Path,help='Stage91 per-run evidence CSV.GZ')
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage92'))
    p.add_argument('--from-date',default='',help='YYYY-MM-DD inclusive')
    p.add_argument('--through',default='',help='YYYY-MM-DD inclusive')
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test();return
    if not a.input or not a.input.is_file():p.error('--input requires existing Stage91 artifact')
    process(a.input,a.out,a.from_date or None,a.through or None)

if __name__=='__main__':main()
