#!/usr/bin/env python3
"""Stage 136: official tier + race eligibility/experience + verifiable field context.

Research version 1. Race type is NOT an official ability tier. British and Irish
official classes/grades remain in stage135 lookup. Type priors are tiny, explicit,
uncalibrated hypotheses for ablation, not claimed official handicap ratings.
No odds, SP, post-race changes or future results are used in predictive features.
"""
from __future__ import annotations
import argparse, csv, json, math, pathlib, re, statistics, sqlite3
from collections import Counter
import race_strength_lookup as R

VERSION='stage136-maiden-novice-type-matrix-v1'
# Parse once: this lookup is called for hundreds of thousands of historical runs.
DEFAULT_REFERENCE=R.load_reference()
SOURCE_LINKS={
 'BHA_RACE_TYPES':'https://www.britishhorseracing.com/regulation/glossary-of-race-types/',
 'BHA_HANDICAPPING':'https://www.britishhorseracing.com/regulation/guide-to-handicapping/',
 'BHA_INITIAL_MARKS':'https://www.britishhorseracing.com/regulation/guide-to-handicapping/translating-performance-figures-initial-handicap-rating/',
 'BHA_HISTORICAL_STANDARDS':'https://www.britishhorseracing.com/regulation/handicapping-tools/',
 'HRI_RACE_CATEGORIES':'https://www.hri-ras.ie/getmedia/7692c79c-c4b5-4d3b-b939-46c580a7fe27/NH-Sep-Dec-%28Provisional-Summary%29.pdf?v=20251002170000',
 'HRI_2026_MIN_VALUES':'https://www.hri.ie/HRI/media/HRI/Comms/Documents/2026-Minimum-Race-Values.pdf',
}
# Values for an *experimental sensitivity test*, in HPR points (0 to 100).
# In particular: MAIDEN +0.5 DOES NOT imply stronger than handicaps.
# Never use these as a replacement for an unknown class/grade.
TYPE_PRIOR={
 'GRADED_OR_GROUP':0., 'LISTED':0., 'OPEN_CONDITIONS':1., 'OPEN_RATED':0.75,
 'MAIDEN':0.5,'RATED_MAIDEN':0.5,'AUCTION_MAIDEN':0.,'RESTRICTED_MAIDEN':0.,
 'NOVICE':0.75,'RATED_NOVICE':1.,'NOVICE_HANDICAP':0.,
 'BEGINNERS_CHASE':0.5,'RATED_BEGINNERS_CHASE':0.75,'GRADUATION_CHASE':0.5,
 'JUVENILE_HURDLE':0.25,'JUVENILE_MAIDEN_HURDLE':0.25,'ACADEMY_HURDLE':0.,
 'OPEN_HANDICAP':0.,'NURSERY_HANDICAP':0.,'MAIDEN_HANDICAP':0.,
 'CLASSIFIED_STAKES':-0.5,'SELLING':-1.5,'CLAIMING':-1.5,
 'BUMPER_MAIDEN':0.,'BUMPER_OPEN':0.,'BUMPER_ONE_WINNER':0.5,
 'BUMPER_TWO_WINNERS':0.75,'HUNTER_CHASE':0.,'RESTRICTED':0.,
 'UNKNOWN':0.,
}
# Eligibility GROUP (0 non-winner restricted, 1 novice/development, 2 open/mixed).
# This does NOT mean an actual horse has won previously (unrated maidens may
# enter an open handicap after qualifying runs), and is NOT race strength.
ELIGIBILITY_STAGE={
 'MAIDEN':0,'RATED_MAIDEN':0,'AUCTION_MAIDEN':0,'RESTRICTED_MAIDEN':0,
 'BEGINNERS_CHASE':0,'RATED_BEGINNERS_CHASE':0,'JUVENILE_MAIDEN_HURDLE':0,
 'BUMPER_MAIDEN':0,'NOVICE':1,'RATED_NOVICE':1,'NOVICE_HANDICAP':1,
 'JUVENILE_HURDLE':1,'ACADEMY_HURDLE':1,'GRADUATION_CHASE':1,
 'BUMPER_ONE_WINNER':1,'BUMPER_TWO_WINNERS':2,
 'OPEN_HANDICAP':2,'NURSERY_HANDICAP':1,'MAIDEN_HANDICAP':0,
 'OPEN_CONDITIONS':2,'OPEN_RATED':2,'GRADED_OR_GROUP':2,'LISTED':2,
 'CLASSIFIED_STAKES':2,'SELLING':2,'CLAIMING':2,'HUNTER_CHASE':2,
 'BUMPER_OPEN':1,'RESTRICTED':1,'UNKNOWN':None,
}
PATTERNS={
 'MAIDEN':r'\bmaidens?\b',
 'NOVICE':r'\bnovices?\b|\bnovices\b',
 'BEGINNER':r'\bbeginners?\b',
 'HANDICAP':r'\bhandicaps?\b|\bh\.?\s*cap\b|\bnursery\b',
 'RATED':r'\brated\b',
 'JUVENILE':r'\bjuvenile\b|\b3\s*[- ]?y(?:ear)?[- ]?o(?:ld)?\b',
 'AUCTION':r'\b(?:median\s+auction|auction)\b',
 'RESTRICTED':r'\brestricted\b',
 'CONDITIONS':r'\bconditions?\b|\bweight[- ]for[- ]age\b|\ballowance\b',
 'CLASSIFIED':r'\bclassified\b',
 'SELLING':r'\bselling\b|\bseller\b',
 'CLAIMING':r'\bclaiming\b|\bclaimer\b',
 'HUNTER':r'\bhunter\b',
 'GRADUATION':r'\bgraduation\b',
 'ACADEMY':r'\bacademy\b',
 'BUMPER':r'\bbumper\b|\bnational\s+hunt\s+flat\b|\bi\.?n\.?h\.?\s+flat\b|\bnh\s+flat\b',
 'OPEN':r'\bopen\b',
}

def parse_experience(event):
    """Return independently auditable race *eligibility* categories (not strength)."""
    s=' '.join(str(event.get(x) or '') for x in ('race_name','race_type','name','type'))
    flags={k:bool(re.search(regex,s,re.I)) for k,regex in PATTERNS.items()}
    disc=R.discipline(str(event.get('race_type') or ''),s,str(event.get('surface') or ''))
    # Covers a common Irish 'INH Flat Race' bumper spelling.
    if flags['BUMPER'] or disc=='NH_FLAT':
        if flags['MAIDEN']:code='BUMPER_MAIDEN'
        elif re.search(r'\bwinners?\s+of\s+two\b',s,re.I):code='BUMPER_TWO_WINNERS'
        elif re.search(r'\bwinners?\s+of\s+one\b',s,re.I):code='BUMPER_ONE_WINNER'
        else:code='BUMPER_OPEN'
    elif flags['HANDICAP']:
        if flags['NOVICE']:code='NOVICE_HANDICAP'
        elif flags['MAIDEN']:code='MAIDEN_HANDICAP'
        elif re.search(r'\bnursery\b',s,re.I):code='NURSERY_HANDICAP'
        else:code='OPEN_HANDICAP'
    elif flags['HUNTER'] and disc=='CHASE':code='HUNTER_CHASE'
    elif flags['GRADUATION'] and disc=='CHASE':code='GRADUATION_CHASE'
    elif flags['BEGINNER'] and disc=='CHASE':code='RATED_BEGINNERS_CHASE' if flags['RATED'] else 'BEGINNERS_CHASE'
    elif flags['ACADEMY'] and disc=='HURDLE':code='ACADEMY_HURDLE'
    elif flags['JUVENILE'] and disc=='HURDLE':code='JUVENILE_MAIDEN_HURDLE' if flags['MAIDEN'] else 'JUVENILE_HURDLE'
    elif flags['NOVICE']:code='RATED_NOVICE' if flags['RATED'] else 'NOVICE'
    elif flags['MAIDEN']:
        if flags['RESTRICTED']:code='RESTRICTED_MAIDEN'
        elif flags['AUCTION']:code='AUCTION_MAIDEN'
        elif flags['RATED']:code='RATED_MAIDEN'
        else:code='MAIDEN'
    elif flags['CLASSIFIED']:code='CLASSIFIED_STAKES'
    elif flags['SELLING']:code='SELLING'
    elif flags['CLAIMING']:code='CLAIMING'
    elif flags['RESTRICTED']:code='RESTRICTED'
    elif flags['RATED']:code='OPEN_RATED'
    elif flags['CONDITIONS']:code='OPEN_CONDITIONS'
    else:code='UNKNOWN'
    # SELLING/CLAIMING and other restrictions can overlay existing handicaps.
    # Return modifiers to the model without falsely overriding its main handicap type.
    return {'race_experience_type':code,
            'experience_stage':ELIGIBILITY_STAGE.get(code),
            'is_maiden':int(flags['MAIDEN']),
            'is_novice':int(flags['NOVICE']),
            'is_beginner':int(flags['BEGINNER']),
            'is_juvenile':int(flags['JUVENILE']),
            'is_handicap':int(flags['HANDICAP']),
            'is_restricted':int(any(flags[k] for k in ('RESTRICTED','AUCTION','SELLING','CLAIMING','HUNTER'))),
            'is_rated_race':int(flags['RATED']),
            'is_seller':int(flags['SELLING']),
            'is_claimer':int(flags['CLAIMING']),
            'is_bumper':int(flags['BUMPER']),
            'experimental_type_prior_hpr':TYPE_PRIOR[code],
            'evidence':'EXPLICIT_RACE_NAME' if code!='UNKNOWN' else 'TYPE_NOT_ESTABLISHED'}

def type_strength(event,preoff_runners=None,table=None):
    """Three separate signals; do NOT equate type with official class.

    No field OR guess when less than three ratings / 50% of runners verified.
    Opponent OR remains on its native code scale (FLAT vs HURDLE vs CHASE).
    """
    official=R.features(event,preoff_runners,table if table is not None else DEFAULT_REFERENCE)
    exp=parse_experience(event)
    z={**official,**exp}
    # Stage135 score is a provisional ordinal for KNOWN official tiers ONLY.
    z['official_tier_score']=official.get('provisional_strength_score')
    z['known_official_tier']=int(official.get('classification_code')!='UNKNOWN')
    z['preoff_opposition_median_or']=official.get('field_rating_median')
    z['opposition_rating_usable']=int(official.get('field_rating_median') is not None)
    z['standalone_strength_from_type']=None
    z['type_prior_is_unvalidated']=True
    return z


def save_reference_files(out):
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    data={'version':VERSION,'values_provisional':True,'type_priors':TYPE_PRIOR,
          'eligibility_stage':ELIGIBILITY_STAGE,'sources':SOURCE_LINKS,
          'rules':['Official race grade/class is never replaced by race type.',
                   'Unrated maidens and novices have unknown opposition OR; do not assign zero.',
                   'Small type HPR priors must be tested out-of-time against a zero-prior ablation.',
                   'Novice handicap, juvenile handicap and maiden handicap remain handicaps.',
                   'Juvenile and novice are orthogonal to pattern grade.',
                   'Do not interpret handicap eligibility as quality ranking.']}
    (out/'stage136-experience-type-reference.json').write_text(json.dumps(data,indent=2)+'\n')
    with (out/'stage136-experience-type-points.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['race_type','eligibility_stage_not_strength','experimental_hpr_prior_points','standalone_race_strength','notes'])
        for k,v in TYPE_PRIOR.items():
            w.writerow([k,ELIGIBILITY_STAGE.get(k),v,'UNKNOWN_WITHOUT_EVIDENCE',
                         'A small research perturbation; NEVER official class or Grade'])
    return data


def test():
    cases=[
      ('Novices Limited Handicap Chase (GBB)','NOVICE_HANDICAP',1,1),
      ('Rated Novice Hurdle','RATED_NOVICE',0,1),
      ('Open Maiden Hurdle','MAIDEN',0,0),
      ('Irish Stallion Farms EBF Beginners Chase','BEGINNERS_CHASE',0,0),
      ('Rated Beginners Steeplechase','RATED_BEGINNERS_CHASE',0,0),
      ('Maiden Auction Stakes','AUCTION_MAIDEN',0,0),
      ('Median Auction Maiden Stakes','AUCTION_MAIDEN',0,0),
      ('Juvenile Maiden Hurdle','JUVENILE_MAIDEN_HURDLE',0,0),
      ('Novice Hurdle (Grade 1)','NOVICE',0,1),
      ('Class 6 Handicap Flat','OPEN_HANDICAP',1,2),
      ('Classified Stakes','CLASSIFIED_STAKES',0,2),
      ('Nursery Handicap','NURSERY_HANDICAP',1,1),
      ('Juvenile Novice Handicap Hurdle','NOVICE_HANDICAP',1,1),
      ('Maiden National Hunt Flat Race','BUMPER_MAIDEN',0,0),
      ('INH Flat Race winners of one','BUMPER_ONE_WINNER',0,1),
      ('Bumper winners of two','BUMPER_TWO_WINNERS',0,2),
      ('Claiming Race','CLAIMING',0,2),
      ('Selling Handicap','OPEN_HANDICAP',1,2),
      ('Hunter Chase','HUNTER_CHASE',0,2),
    ]
    for title,expect,cap,stage in cases:
        got=parse_experience({'race_name':title,'race_type':'Chase' if 'Chase' in title else 'Hurdle' if 'Hurdle' in title else 'Flat'})
        assert got['race_experience_type']==expect,(title,got)
        assert got['is_handicap']==cap,(title,got)
        assert got['experience_stage']==stage,(title,got)
    maiden=type_strength({'jurisdiction':'IRELAND','race_name':'Maiden Hurdle','race_type':'Hurdle','race_class':'','date':'2025-01-01'})
    assert maiden['race_experience_type']=='MAIDEN' and maiden['official_tier_score'] is None,maiden
    group=type_strength({'jurisdiction':'IRELAND','race_name':'Novice Hurdle (Grade 1)','race_type':'Hurdle','date':'2025-01-01'})
    assert group['race_experience_type']=='NOVICE' and group['classification_code']=='NH_GRADE_1',group
    assert type_strength({'jurisdiction':'GB','race_name':'Class 4 Maiden Hurdle','race_type':'Hurdle','race_class':'Class 4'})['official_tier_score']==40
    good=type_strength({'jurisdiction':'IRELAND','race_name':'Maiden Hurdle','race_type':'Hurdle'},
         [{'official_rating':118},{'official_rating':112},{'official_rating':120},{'official_rating':None}])
    assert good['preoff_opposition_median_or']==118,good
    assert type_strength({'jurisdiction':'IRELAND','race_name':'Maiden Hurdle','race_type':'Hurdle'},
         [{'official_rating':118},{'official_rating':None},{'official_rating':None},{'official_rating':None}])['preoff_opposition_median_or'] is None
    assert max(map(abs,TYPE_PRIOR.values()))<=1.5
    print('Stage136 PASS:',len(cases),'race types and 5 tier/opposition/neutrality rules')


def audit_taxonomy(history,reference,out,start='2022-01-01',end='2025-12-31'):
    """Joint class/type coverage from exact verified UK/Irish race identities."""
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    refs={}
    with open(reference,encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            d=r.get('date','')
            if start<=d<=end and r.get('stage2_race_id'):
                refs[r['stage2_race_id']]=(d,str(r.get('region_scope') or ''),r.get('course'),r.get('race_type'),r.get('race_name'))
    counts=Counter();samples=[]
    cx=sqlite3.connect('file:'+str(pathlib.Path(history).resolve())+'?mode=ro',uri=True)
    try:
        rows=cx.execute('SELECT id,race_date,course,race_type,surface,race_class FROM historical_races WHERE race_date BETWEEN ? AND ? ORDER BY race_date,id',(start,end))
        for rid,date,course,rtype,surface,rclass in rows:
            ref=refs.get(rid)
            if not ref:counts['reference_missing']+=1;continue
            d,country,rcourse,rt,name=ref
            if date!=d or R.jurisdiction(country) not in ('GB','IRELAND'):
                counts['date_or_jurisdiction_unverified']+=1;continue
            if re.sub('[^A-Z0-9]','',str(course).upper())!=re.sub('[^A-Z0-9]','',str(rcourse).upper()):
                counts['course_identity_conflict']+=1;continue
            event={'jurisdiction':country,'race_name':name,'race_type':rt or rtype,'race_class':rclass,'surface':surface,'date':date}
            result=type_strength(event)
            counts['verified_races']+=1
            counts['country_'+result['jurisdiction']]+=1
            counts['type_'+result['race_experience_type']]+=1
            counts['tier_'+result['classification_code']]+=1
            counts['race_type_known' if result['race_experience_type']!='UNKNOWN' else 'race_type_unknown']+=1
            counts['official_tier_known' if result['known_official_tier'] else 'official_tier_unknown']+=1
            if not result['known_official_tier'] and result['race_experience_type']!='UNKNOWN':
                counts['type_known_strength_unverified']+=1
            if len(samples)<4000 and (not result['known_official_tier'] or result['race_experience_type']!='UNKNOWN'):
                samples.append({'date':date,'country':country,'course':course,'race_name':name,'race_class':rclass,
                    'race_type':result['race_experience_type'],'experience_stage':result['experience_stage'],
                    'official_tier':result['classification_code'],'official_tier_score':result['official_tier_score'],
                    'provisional_hpr_type_prior':result['experimental_type_prior_hpr']})
    finally:cx.close()
    report={'version':VERSION,'period':[start,end],'counts':dict(counts),
        'no_live_updates':True,'no_results_or_odds_used':True,
        'warning':'Type recognition is not proof of race strength or predictive value.'}
    (out/'stage136-class-experience-coverage.json').write_text(json.dumps(report,indent=2)+'\n')
    if samples:
        with (out/'stage136-type-audit-samples.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(samples[0]));w.writeheader();w.writerows(samples)
    print('STAGE136 TAXONOMY',json.dumps({k:v for k,v in counts.items() if k in ('verified_races','race_type_known','official_tier_known','type_known_strength_unverified')},indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit-db',type=pathlib.Path)
    p.add_argument('--reference',type=pathlib.Path)
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage136/output'))
    p.add_argument('--start',default='2022-01-01');p.add_argument('--end',default='2025-12-31')
    a=p.parse_args();test();save_reference_files(a.out)
    if a.audit_db:
        if not a.reference:p.error('--reference required for --audit-db')
        audit_taxonomy(a.audit_db,a.reference,a.out,a.start,a.end)

if __name__=='__main__':main()
