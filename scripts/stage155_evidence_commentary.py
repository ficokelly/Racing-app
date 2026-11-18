#!/usr/bin/env python3
"""Grounded race/horse commentary for Stage153 ROI specialists.

Only consumes pre-off racecard fields and existing independently scored Stage141
research evidence. Never re-ranks horses or infers C&D/surface history or a
handicap edge from odds or the winning result. The score is NOT calibrated win %.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import math
import pathlib
import re
import statistics
from typing import Any


def norm(value: Any) -> str:
    return ''.join(ch for ch in str(value or '').casefold() if ch.isalnum())


def number(value: Any):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def trueish(value: Any) -> bool:
    return str(value or '').strip().casefold() in ('true', 'yes', '1', 'y')


def count_field(row, names):
    for key in names:
        val = number(row.get(key))
        if val is not None and val >= 0 and val.is_integer():
            return int(val)
    return None


def clock(value: Any) -> str:
    value = str(value or '')
    if not value: return ''
    try:
        return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(dt.timezone.utc).strftime('%H:%M')
    except (ValueError, TypeError):
        return value[:5]


def form_evidence(value: Any):
    """Racecard form is printed oldest to newest; 0 indicates unplaced.

    Never treat letters (F, U, P) as numeric placings. We do not attempt to infer
    distance, going, race class or current mark from a raw form string.
    """
    raw = str(value or '').strip().upper()
    symbols = [c for c in raw if c in '0123456789FUPBRS']
    last = symbols[-1] if symbols else ''
    digits = [int(c) for c in symbols if c in '0123456789']
    recent = digits[-5:]
    wins = sum(x == 1 for x in recent)
    places = sum(1 <= x <= 3 for x in recent)
    latest = ({'1':'won the latest recorded start','2':'finished second last time',
               '3':'was third on the latest recorded start',
               'F':'fell on the latest recorded start','U':'unseated on the latest recorded start',
               'P':'was pulled up on the latest recorded start',
               '0':'was unplaced on the latest recorded start'}.get(last))
    if not latest and last and last in '456789':
        latest = f'finished {last}th last time' if last != '8' else 'finished eighth last time'
    return {'recent': recent, 'wins': wins, 'places': places,
            'latest': latest, 'last': last, 'count': len(recent)}


def source_context(source_root: pathlib.Path, date: str):
    """Attach evidence from Stage141 source rows, without using its rankings as picks."""
    out = {}
    roots = [pathlib.Path(p) for p in source_root] if isinstance(source_root,(list,tuple)) else [pathlib.Path(source_root)]
    paths = sorted(f for root in roots if root.exists() for f in root.rglob('stage141-today-all-runners.csv'))
    model_order = {'D137_HPR_ABILITY_OR_WEIGHT':0, 'D137_HPR_ABILITY_GRADE_OR_WEIGHT':1}
    for path in paths:
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if row.get('date') != date: continue
                key = (date, norm(row.get('meeting')), row.get('time_utc','')[:5], norm(row.get('horse')))
                old = out.get(key)
                if old is None or model_order.get(row.get('model'),99)<model_order.get(old.get('model'),99):
                    out[key] = row
    return out


def cards_context(cards: dict, date: str):
    out = {}
    for row in cards.get('runners', []):
        if row.get('date')!=date:continue
        k=(date,norm(row.get('course')),clock(row.get('race_time')),norm(row.get('horse')))
        out[k]=row
    return out


def subject_facts(row: dict, source: dict, group: str, peers: list[dict]):
    """Separate observed positives/cautions from model's unexplained weightings."""
    positives=[];cautions=[];neutral=[];evidence=[]
    form=form_evidence(row.get('form'))
    if form['latest']:
        if form['last']=='1':positives.append('a win on the latest recorded outing');evidence.append('recent_win')
        elif form['last'] in ('2','3'):positives.append('a runner-up finish last time' if form['last']=='2' else 'third place last time');evidence.append('recent_place')
        elif form['last'] in ('F','U','P','0'):cautions.append({'F':'a fall on the latest recorded start','U':'an unseated rider last time','P':'a pulled-up effort last time','0':'an unplaced finish last time'}[form['last']]);evidence.append('latest_setback')
        else:neutral.append(form['latest'])
    if form['count']>=3:
        if form['wins']>=1:
            wins_text = 'a win' if form['wins']==1 else f'{form["wins"]} wins'
            positives.append(f'{wins_text} in the last {form["count"]} recorded finishes');evidence.append('recent_winning_form')
        elif form['places']>=3:
            positives.append(f'{form["places"]} top-three finishes in the last {form["count"]} completed outings');evidence.append('recent_consistency')
        elif form['places']==0 and form['count']>=4:
            cautions.append(f'no top-three finish in the last {form["count"]} recorded completed outings');evidence.append('poor_recent_form')
    cd=count_field(row,('course_distance_wins','course_and_distance_wins','cd_wins','course_distance_win_count'))
    course=count_field(row,('course_wins','course_win_count'))
    distance=count_field(row,('distance_wins','distance_win_count'))
    awwins=count_field(row,('aw_wins','all_weather_wins'))
    turfwins=count_field(row,('turf_wins',))
    if cd and cd>0:
        positives.append(f'{cd} verified win{"s" if cd != 1 else ""} over this course and distance');evidence.append('cd_wins')
    elif trueish(row.get('course_distance_winner')) or trueish(row.get('cd_winner')):
        positives.append('a recorded course-and-distance win');evidence.append('cd_win_flag')
    else:
        if course and course>0:
            positives.append(f'{course} recorded win{"s" if course!=1 else ""} at this course');evidence.append('course_wins')
        if distance and distance>0:
            positives.append(f'{distance} recorded win{"s" if distance!=1 else ""} at the distance');evidence.append('distance_wins')
    if group.startswith('aw') or group=='auction restricted':
        if awwins and awwins>0:
            positives.append(f'{awwins} verified all-weather win{"s" if awwins!=1 else ""}');evidence.append('aw_wins')
        elif awwins==0 and turfwins and turfwins>0:
            cautions.append(f'{turfwins} recorded turf win{"s" if turfwins!=1 else ""}, but none recorded on the all-weather');evidence.append('surface_gap')
    rating=number(row.get('ofr') or row.get('official_rating') or source.get('or'))
    ors=[number(p['card'].get('ofr') or p['card'].get('official_rating') or p['context'].get('or')) for p in peers]
    ors=[r for r in ors if r is not None]
    if rating is not None:
        if len(ors)>=3 and rating==max(ors) and max(ors)>min(ors):
            positives.append(f'{"joint-highest" if ors.count(max(ors))>1 else "highest"} published official mark in the field ({rating:g})');evidence.append('or_high')
        elif len(ors)>=3 and rating<max(ors)-10:
            neutral.append(f'officially rated {rating:g}, {max(ors)-rating:g}lb below the highest known mark');evidence.append('or_gap')
        else:
            neutral.append(f'official mark {rating:g}')
    hpr=number(source.get('hpr'))
    hprs=[number(p['context'].get('hpr')) for p in peers]
    hprs=[h for h in hprs if h is not None]
    if hpr is not None:
        if len(hprs)>=3 and hpr>=max(hprs)-0.0001:
            positives.append(f'top published historical performance rating in this field ({hpr:.1f})');evidence.append('hpr_high')
        elif len(hprs)>=3 and hpr <= statistics.median(hprs)-7:
            cautions.append(f'historical performance figure {hpr:.1f} is below the median among rated runners');evidence.append('hpr_low')
        else:neutral.append(f'historical performance rating {hpr:.1f}')
    is_handicap='handicap' in group or 'nursery' in group
    win_mark=number(source.get('last_handicap_winning_mark') or row.get('last_handicap_winning_mark'))
    if is_handicap and rating is not None and win_mark is not None:
        delta=round(rating-win_mark,1)
        if delta<=-1:
            positives.append(f'a current mark {abs(delta):g}lb below its last recorded winning handicap mark ({win_mark:g})');evidence.append('below_winning_mark')
        elif delta==0:
            positives.append(f'a return to its last recorded winning handicap mark ({win_mark:g})');evidence.append('back_winning_mark')
        elif delta>=5:
            cautions.append(f'current mark of {rating:g} is {delta:g}lb above its last verified winning handicap mark');evidence.append('above_winning_mark')
    # Distinctive verified race suitability/handicap evidence comes first; do not bury it behind generic form.
    def priority(item):
        t=item.casefold()
        if 'course and distance' in t: return 0
        if 'winning handicap mark' in t: return 1
        if 'all-weather win' in t or 'turf win' in t: return 2
        if 'at this course' in t or 'at the distance' in t: return 3
        if 'historical performance' in t or 'official mark' in t: return 4
        return 5
    positives.sort(key=priority)
    return {'positives':list(dict.fromkeys(positives)), 'cautions':list(dict.fromkeys(cautions)),
            'neutral':list(dict.fromkeys(neutral)), 'evidence':list(dict.fromkeys(evidence)),
            'form':form, 'hpr':hpr, 'or':rating}


def readable(items, limit):
    a=items[:limit]
    if not a:return ''
    if len(a)==1:return a[0]
    return ', '.join(a[:-1])+' and '+a[-1]


def horse_writeup(race, horse, peers, facts):
    horse_name=horse['horse']; rank=int(horse['rank']);total=len(peers)
    leader=peers[0]['race_horse']['horse']
    if rank==1:lead=f'{horse_name} leads this {race["race_type"]} on the selected specialist’s ranking.'
    elif rank==2:lead=f'{horse_name} is the specialist’s second choice behind {leader}.'
    elif rank<=min(4,total):lead=f'{horse_name} ranks {rank} of {total}, outside the leading pair in the selected specialist’s scores.'
    else:lead=f'{horse_name} is ranked {rank} of {total}, rather than among the leading specialist choices.'
    lines=[lead]
    pos=facts['positives'];neg=facts['cautions'];neu=facts['neutral']
    if pos:lines.append('The positive evidence includes '+readable(pos,2)+'.')
    if neg:lines.append('The reservations include '+readable(neg,2)+'.')
    if rank!=1 and not neg and not pos and neu:
        lines.append('The individual information available is '+readable(neu,2)+', but this alone does not explain the specialist score.')
    elif pos and not neg and neu and len(pos)<2:
        lines.append('Additional context: '+readable(neu,1)+'.')
    if rank in (1,2):
        other=peers[1]['race_horse'] if rank==1 else peers[0]['race_horse']
        otherfacts=peers[1]['facts'] if rank==1 else peers[0]['facts']
        a=facts['form'];b=otherfacts['form']
        if a['count']>=3 and b['count']>=3 and a['wins']>b['wins']:
            lines.append(f'Its recent finishing sequence includes more wins than {other["horse"]} ({a["wins"]} versus {b["wins"]} across the last five recorded finishes).')
        elif a['count']>=3 and b['count']>=3 and a['places']<b['places']:
            lines.append(f'{other["horse"]} has the stronger recent placing count ({b["places"]} versus {a["places"]}), a point worth weighing against this order.')
        elif facts['hpr'] is not None and otherfacts['hpr'] is not None:
            diff=facts['hpr']-otherfacts['hpr']
            if diff>5:lines.append(f'Its recorded performance figure is {diff:.1f} higher than {other["horse"]}’s.')
            elif diff<-5:lines.append(f'{other["horse"]} has the higher historical performance figure by {-diff:.1f}, despite this model order.')
    if not pos and not neg and not neu:
        lines.append('No verified course, surface or recent-result comparison is available for this runner; the ranking should not be presented as proof of any one advantage.')
    return ' '.join(lines)


def race_writeup(race, peers):
    a,b=peers[:2];one=a['race_horse']['horse'];two=b['race_horse']['horse'];
    af=a['facts'];bf=b['facts'];name=race.get('race_type') or 'race'
    lines=[f'{one} heads {two} in this {name} assessment.']
    if af['positives']:
        lines.append(f'The recorded case for {one} includes {readable(af["positives"],2)}.')
    elif af['neutral']:
        lines.append(f'The available background for {one} includes {readable(af["neutral"],1)}; the model’s exact feature contributions have not been published.')
    else:
        lines.append(f'No independently verified horse-level factor is available that isolates why the specialist favours {one}.')
    if bf['positives']:
        lines.append(f'{two} has a separate claim: {readable(bf["positives"],2)}.')
    elif bf['neutral']:
        lines.append(f'{two} brings {readable(bf["neutral"],1)}, although that does not by itself establish the relative ranking.')
    if af['cautions']:
        lines.append(f'Against the favourite in these ratings: {readable(af["cautions"],1)}.')
    elif bf['cautions']:
        lines.append(f'A caution about the main challenger is {readable(bf["cautions"],1)}.')
    if not any((af['positives'], bf['positives'], af['cautions'], bf['cautions'])):
        lines.append('This specialist ordering is not a claim that either horse has a verified course-and-distance edge or is well handicapped.')
    # The app already labels these as research ratings; avoid a repeated boilerplate sentence.
    return ' '.join(lines)


def enrich(feed: dict, cards: dict, sources: pathlib.Path):
    day=feed['day']
    card_map=cards_context(cards,day)
    context_map=source_context(sources,day)
    audit=collections.Counter()
    for race in feed.get('races', []):
        if race.get('date')!=day or not race.get('selection') or len(race.get('runners',[]))<2:
            audit['not_scored']+=1;continue
        c=norm(race.get('course'));t=race.get('time','')[:5]
        peers=[]
        for hr in race.get('runners', []):
            k=(day,c,t,norm(hr.get('horse')))
            card=card_map.get(k,{})
            context=context_map.get(k,{})
            peers.append({'race_horse':hr, 'card':card, 'context':context})
        peers.sort(key=lambda p:int(p['race_horse'].get('rank') or 9999))
        for item in peers:
            item['facts']=subject_facts(item['card'],item['context'],race.get('race_type',''),peers)
        for item in peers:
            runner=item['race_horse'];facts=item['facts']
            runner['commentary']=horse_writeup(race,runner,peers,facts)
            runner['commentary_evidence_fields']=facts['evidence']
            if facts['evidence']:audit['runners_with_specific_evidence']+=1
            else:audit['runners_with_limited_evidence']+=1
        race['commentary']=race_writeup(race,peers)
        race['commentary_status']='individual-evidence-v1'
        race['commentary_evidence']={
            'rank1':peers[0]['facts']['evidence'],
            'rank2':peers[1]['facts']['evidence']}
        audit['races_enriched']+=1
    feed['commentary_audit']={'version':'stage155-grounded-individual-commentary-v1',**dict(audit),
        'source':'Existing racecard form and explicitly recorded course/distance evidence, plus the pre-off Stage141 independent historical candidate metrics. No result or odds input and no change in ranks.'}
    return dict(audit)


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--feed',type=pathlib.Path,required=True)
    p.add_argument('--cards',type=pathlib.Path,required=True)
    p.add_argument('--sources',type=pathlib.Path,required=True)
    a=p.parse_args()
    feed=json.loads(a.feed.read_text())
    cards=json.loads(a.cards.read_text())
    before=[(r['course'],r['time'],r.get('selection',{}).get('horse') if r.get('selection') else None,
             [(x.get('horse'),x.get('rank'),x.get('research_score')) for x in r.get('runners',[])]) for r in feed.get('races',[])]
    audit=enrich(feed,cards,a.sources)
    after=[(r['course'],r['time'],r.get('selection',{}).get('horse') if r.get('selection') else None,
            [(x.get('horse'),x.get('rank'),x.get('research_score')) for x in r.get('runners',[])]) for r in feed.get('races',[])]
    assert before==after, 'Commentary must never affect frozen specialist picks or scores'
    a.feed.write_text(json.dumps(feed,ensure_ascii=False,indent=2)+'\n')
    print('STAGE155_GROUNDED_COMMENTARY',json.dumps(audit,sort_keys=True))


if __name__=='__main__':main()
