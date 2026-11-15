#!/usr/bin/env python3
"""Stage 138: ONE integrated eight-pillar D challenger; paper research only.

Historic results and comments become eligible strictly AFTER their race day.
2023 trains the initial pillar learners. 2023Q4 is an internal, strictly
forward-scored meta-weight calibration slice (models fit on 2023Q1-Q3).
2024 Q1 .. 2025 Q4 are eight out-of-time quarterly tests: the model and weights
are fixed throughout each quarter, then updated for the NEXT quarter.

The only eight final scoring dimensions are the original frozen D pillars:
Ability, Trainer, Jockey, Course, Draw, Distance, Ground, Jumping.
NLP is an input interpreter within Ability / Jumping / Distance, NEVER pillar 9.
Frozen D and Stage137 are independent baselines; no live files are written.
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
from collections import defaultdict, deque
import numpy as np

import stage137_frozen_d_race_type_walkforward as S

VERSION='stage138-eight-pillar-incident-season-fresh-v1'
PILLARS=tuple(S.PILLARS)
HISTORY_FIRST='2015-01-01'
D_BASELINE_START='2022-01-01'
MODELS=('FROZEN_D','D_RACETYPE_NLP','D_138_EIGHT_PILLARS')
INITIAL_META_START='2023-10-01'
INITIAL_META_END='2023-12-31'
MIN_PILLAR_RACES=700
MIN_LOCAL_META=90
NLP_DISCLAIMER=('Context-aware word/bigram regression plus explicit incident parsing; '
                'NOT a general-purpose LLM or full understanding of racing commentary.')

# Every predictive runner feature has ONE primary pillar home.  In particular,
# frozen_d_score is NOT used in addition to the eight components; the raw
# beaten-distance feature is REMOVED entirely; time/training volume proxies are
# diagnostics, not predictive features.
PILLAR_FEATURES={
 'Ability':[
  'Ability','horse_form_quality3','horse_form_trend','horse_beaten_per_mile3',
  'horse_comment_phrase3','horse_comment_nlp3','horse_nlp_coverage',
  'horse_interference3','horse_runs','last_run_days','past_grade_strength3',
  'past_known_tier_fraction','current_tier_strength','current_tier_known',
  'past_opposition_median3','current_opposition_median','current_opposition_known',
  'same_code_form3','maiden_to_handicap','novice_to_handicap',
  'beginner_to_handicap','last3_maiden_fraction','last3_novice_fraction',
  'past_vs_current_tier_delta','prior_rivals_nextout_quality3',
  'prior_rivals_nextout_winrate3','prior_rivals_nextout_count3',
  'prior_rivals_nextout_coverage3','current_race_experience_stage','current_handicap',
  'current_gr1','current_gr2','current_gr3','field_size','official_rating',
  'weight_lbs','relative_official_rating','relative_weight_lbs',
  'form_quality_confident',
  'effective_form_quality','first_after_break','break_days_current','break_return_quality',
  'break_return_count','break_return_similarity','seasonal_form_delta',
  'seasonal_support','campaign_run_number','seasonal_previous_years',
  'incident_ability_quality3','incident_ability_evidence3','contextual_form_trend',
  'adjusted_margin_quality3','experience_same_code_runs',
  'experience_same_type_runs','trainer_change_for_horse',
 ],
 'Trainer':['Trainer','trainer_return_quality','trainer_return_count',
            'trainer_seasonal_winrate','trainer_seasonal_count',
            'trainer_code_winrate','trainer_code_count'],
 'Jockey':['Jockey','jockey_code_winrate','jockey_code_count',
           'jockey_course_winrate','jockey_course_count'],
 'Course':['Course','horse_course_quality','horse_course_runs'],
 'Draw':['Draw'],
 'Distance':['Distance','horse_near_trip_quality','horse_near_trip_runs',
             'family_trip_quality','family_trip_evidence','nlp_stamina3'],
 'Ground':['Ground','horse_same_ground_quality','horse_same_ground_runs',
           'family_ground_quality','family_ground_evidence',
           'family_aw_quality','family_aw_evidence'],
 'Jumping':['Jumping','horse_jumping_comment3','incident_jump_quality3',
            'incident_jump_evidence3','jumping_nlp_quality3','jump_nlp_coverage',
            'horse_chase_experience','horse_hurdle_experience',
            'family_jump_quality','family_jump_evidence','historical_jump_errors3'],
}
assert tuple(PILLAR_FEATURES)==PILLARS
_used=[x for group in PILLAR_FEATURES.values() for x in group]
assert len(_used)==len(set(_used)), 'A feature cannot feed two pillars'
assert 'horse_beaten3' not in _used and 'frozen_d_score' not in _used

EXTRA_FEATURES=[x for x in _used if x not in S.FEATURES]
# Remove 2022-2025 Stage137's RAW margin and an extra calendar/training-volume
# proxy, neither of which should reach a predictive pillar model.
FEATURES=[x for x in S.FEATURES if x not in ('horse_beaten3','nlp_model_trained_comments','frozen_d_score')]
FEATURES+=EXTRA_FEATURES
FEATURE_POS={key:i for i,key in enumerate(FEATURES)}
assert set(_used)<=set(FEATURES)


def num(x):return S.fnum(x)
def mean(v):return S.avg(v)
def clamp(x,lo,hi):return max(lo,min(hi,x))


def season_distance(d1,d2):
    """Circular day-of-year difference; leap-year safe, not future-aware."""
    a=dt.date.fromisoformat(str(d1)[:10]);b=dt.date.fromisoformat(str(d2)[:10])
    # Month/day in arbitrary non-leap year: Feb 29 is mapped to Feb 28.
    def slot(d):return dt.date(2001,d.month,min(d.day,28 if d.month==2 else 31)).timetuple().tm_yday
    x=abs(slot(a)-slot(b));return min(x,365-x)


def break_band(days):
    if days is None or days<45:return 'NONE'
    if days<90:return '45_89'
    if days<180:return '90_179'
    if days<365:return '180_364'
    return '365_PLUS'


def parse_incident(comment,position,field_size,distance_f,beaten,kind):
    """Interpret explicit words cautiously; no invented running position.

    Return distinct ability/jumping/stamina evidence. Falling when clear late
    can support ability but is always a jumping failure. When race position is
    unspecified, ability stays UNKNOWN, not a fabricated loss or a win.
    """
    t=S.sanitize_comment(comment).lower().replace('’',"'")
    pos=num(position);b=num(beaten);dist=num(distance_f)
    is_jumps=kind in ('CHASE','HURDLE')
    fell=bool(re.search(r'\b(?:fell|unseated|brought down|refused|ran out)\b',t))
    fell_fault=bool(re.search(r'\b(?:fell|unseated|refused|ran out)\b',t))
    mistakes=len(re.findall(r'\b(?:blunder\w*|mistake\w*|not fluent|pecked|jumped poorly|hit the fence|jumped badly)\b',t))
    strong=bool(re.search(r'\b(?:travelled? (?:well|strongly|powerfully)|going (?:well|strongly)|still travelling|smoothly|easily|full of running|stayed on|ran on)\b',t))
    clear=bool(re.search(r'\b(?:clear|well clear|in front|led|leading|going best|disputed lead)\b',t))
    late=bool(re.search(r'\b(?:at (?:the )?(?:last|final|penultimate)|at (?:the )?(?:second|third) last|\b[123] out\b|\btwo out\b|\bthree out\b|approaching (?:the )?last)\b',t))
    early=bool(re.search(r'\b(?:fell|unseated|refused|ran out) (?:at|over) (?:the )?(?:first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b',t))
    interference=bool(re.search(r'\b(?:hampered|blocked|short of room|bumped|checked|denied a clear run|brought down)\b',t))
    # Classifying a fall at 'third last' as 'early' would be incorrect.
    if re.search(r'\b(?:third|3rd) last\b',t):early=False;late=True
    if re.search(r'\b(?:second|2nd) last\b',t):early=False;late=True
    # Lengths PER MILE, normalised (not raw lengths), are the ONLY margin input.
    per_mile=(b / (dist/8)) if b is not None and dist is not None and dist>=4 else None
    if pos is not None and field_size>=2 and 1<=pos<=field_size:
        placing=(field_size-pos)/(field_size-1)
        quality=(.70*placing+.30*math.exp(-per_mile/7.)) if per_mile is not None else placing
        quality=clamp(quality,0,1)
    elif fell and late and strong and clear:
        quality=.66    # PROVISIONAL pre-incident evidence, not assumed winner
    elif fell and late and (strong or clear):
        quality=.56    # One positive cue; cautious proxy
    else:quality=None
    jump=None
    if is_jumps:
        if fell_fault:jump=.04 if mistakes>=2 else .15 if mistakes else .25
        elif mistakes:jump=max(.20,.72-.18*mistakes)
        elif re.search(r'\b(?:jumped well|jumped fluently|jumped cleanly|fluent jumping|jumped accurately)\b',t):jump=.9
    # Brought down is not evidence of poor technique; annotate separately.
    if 'brought down' in t and not fell_fault:jump=None
    stamina=(.75 if strong and late else .62 if re.search(r'\b(?:stayed on|ran on)\b',t) else
             .30 if re.search(r'\b(?:weakened (?:rapidly|badly)|stopped quickly|no extra)\b',t) else None)
    return {'quality':quality,'margin_per_mile':per_mile,'jump':jump,'stamina':stamina,
            'fall':fell,'late':late,'early':early,'strong':strong,'clear':clear,
            'mistakes':mistakes,'interference':interference,
            'ability_from_unfinished':bool(fell and pos is None and quality is not None)}


class ContextNLP(S.PriorCommentNLP):
    """Word/bigram model for ability plus a SEPARATE jumping NLP channel.
    Both channel outputs are assigned inside corresponding original pillars.
    Neither channel is a third-party language model or 9th scoring pillar.
    """
    def __init__(self):
        super().__init__()
        from sklearn.linear_model import SGDRegressor
        self.jump_model=SGDRegressor(loss='squared_error',alpha=.005,eta0=.02,
                                      penalty='l2',random_state=138)
        self.jump_trained=0
    def learn(self,examples):
        super().learn(examples)
        xs=[];ys=[]
        for text,_ in examples:
            z=parse_incident(text,None,8,None,None,'CHASE')
            if text and z['jump'] is not None:
                xs.append(text);ys.append(z['jump'])
        if xs:
            self.jump_model.partial_fit(self.vectorizer.transform(xs),np.asarray(ys,dtype=float))
            self.jump_trained+=len(xs)
    def jumping(self,texts):
        valid=[S.sanitize_comment(t) for t in texts if S.sanitize_comment(t)]
        if not valid or self.jump_trained<100:return None,0
        vals=self.jump_model.predict(self.vectorizer.transform(valid))
        return float(np.clip(np.mean(vals),0,1)),len(valid)


class ContextForm(S.Form):
    def __init__(self):
        super().__init__()
        self.h=defaultdict(lambda:deque(maxlen=36))
        self.family=defaultdict(lambda:deque(maxlen=280))
        self.trainers=defaultdict(lambda:deque(maxlen=400))
        self.jockeys=defaultdict(lambda:deque(maxlen=400))
    def family_signal(self,who,identity,code,going,trip,broad):
        rec=[]
        for role,nm in who:
            if not nm:continue
            rec.extend((role,r) for r in self.family.get((role,nm),()) if r['horse']!=identity)
        def estimate(rows,cap=150):
            vals=[r['quality'] for _,r in rows if r['quality'] is not None]
            if len(vals)<3:return None,len(vals)
            v=mean(vals[-cap:]);base=.45
            return round(base+(v-base)*len(vals)/(len(vals)+12),5),len(vals)
        trip_quality,trip_n=estimate([(rol,r) for rol,r in rec if trip is not None and r['trip'] is not None and abs(r['trip']-trip)<=2.0])
        going_quality,going_n=estimate([(rol,r) for rol,r in rec if going and r['going']==going])
        aw_quality,aw_n=estimate([(rol,r) for rol,r in rec if broad=='AW' and r['broad']=='AW'])
        # Direct CHASE family history preferred. HURDLE family for first-time
        # hurdler; NH_FLAT alone is not silently equivalent to successful jumps.
        jump_quality,jump_n=estimate([(rol,r) for rol,r in rec if code in ('CHASE','HURDLE') and r['code']==code])
        return {'family_trip_quality':trip_quality,'family_trip_evidence':trip_n,
          'family_ground_quality':going_quality,'family_ground_evidence':going_n,
          'family_aw_quality':aw_quality,'family_aw_evidence':aw_n,
          'family_jump_quality':jump_quality,'family_jump_evidence':jump_n}
    def features(self,key,asof,info,nlp,code):
        base=super().features(key,asof,info,nlp,code)
        previous=[p for p in self.h.get(key,()) if p['date']<asof]
        last=previous[-3:]
        ctx=info.get('_by_identity',{}).get(key,{})
        current_gap=(dt.date.fromisoformat(asof)-dt.date.fromisoformat(previous[-1]['date'])).days if previous else None
        fresh=bool(current_gap is not None and current_gap>=45)
        # For a current break, FIRST START AFTER earlier comparable breaks is
        # explicitly preferred INSTEAD of an ordinary last-three form average.
        earlier=[p for p in previous if p.get('prior_gap_days') is not None and
                 p['prior_gap_days']>=45 and p['quality'] is not None and
                 p['date']<asof and p['disc']==code]
        similar=[p for p in earlier if break_band(p['prior_gap_days'])==break_band(current_gap)] if fresh else []
        if fresh:
            relevant=(similar[-3:] if similar else (earlier[-3:] if len(earlier)>=2 else []))
            fresh_quality=mean([p['quality'] for p in relevant])
            # Explicitly MISSING when no useful historic post-break evidence;
            # do not quietly repurpose last three performances.
            effective=fresh_quality
            confidence=len(relevant)/(len(relevant)+4) if relevant else 0.
        else:
            relevant=[];fresh_quality=None
            effective=mean([p['quality'] for p in last]);confidence=len([p for p in last if p['quality'] is not None])/3
        # Historical seasonal form in previous YEARS; current season is not a
        # substitute for a repeatable previous-year effect.
        historical=[p for p in previous if p['date'][:4]<asof[:4] and p['quality'] is not None]
        season=[p for p in historical if season_distance(p['date'],asof)<=45 and p['disc']==code]
        current_going=info.get('_going')
        comp=[p for p in historical if p['disc']==code and
              (not current_going or p.get('going')==current_going)]
        # Compare to similar-going baseline where support exists, avoiding an
        # independent winter bonus for an underlying soft-ground preference.
        baseline=mean([p['quality'] for p in comp]) if len(comp)>=3 else mean([p['quality'] for p in historical if p['disc']==code])
        seasonal_delta=((mean([p['quality'] for p in season])-baseline)*len(season)/(len(season)+6)
                        if len(season)>=2 and baseline is not None else None)
        years=len(set(p['date'][:4] for p in season))
        campaign_num=1
        for p in reversed(previous):
            gap=p.get('prior_gap_days')
            if gap is not None and gap>=60:break
            campaign_num+=1
            if campaign_num>=16:break
        if fresh:campaign_num=1
        # Similar trip/course/ground evidence uses only completed prior races.
        trip=info.get('_trip');course=info.get('_course')
        near=[p for p in previous if trip is not None and p['trip'] is not None and abs(p['trip']-trip)<=2]
        atcourse=[p for p in previous if course and p['course']==course]
        onground=[p for p in previous if current_going and p['going']==current_going]
        same_type=[p for p in previous if p['type']==info.get('race_experience_type')]
        same_code=[p for p in previous if p['disc']==code]
        incidents=[p for p in last if p.get('ability_from_unfinished')]
        margins=[p['margin_quality'] for p in last if p.get('margin_quality') is not None]
        jvals=[p['jump_context'] for p in last if p.get('jump_context') is not None]
        jump_nlp,jump_n=nlp.jumping([p['comment'] for p in last if p['disc'] in ('CHASE','HURDLE')])
        jumping_past=[p for p in previous if p['disc'] in ('CHASE','HURDLE')]
        trainer=str(ctx.get('trainer') or '').strip().upper()
        jockey=str(ctx.get('jockey') or '').strip().upper()
        def record_stats(storage,who,filtered,win=True):
            a=[v for v in storage.get(who,()) if filtered(v)] if who else []
            return (round((sum(int(v['win']) for v in a)+1.2)/(len(a)+10),5),len(a)) if a else (None,0)
        tr_rate,tr_count=record_stats(self.trainers,trainer,lambda x:x['code']==code)
        tr_fresh=[v for v in self.trainers.get(trainer,()) if v['code']==code and v['gap'] is not None and
                  v['gap']>=45 and (not fresh or break_band(v['gap'])==break_band(current_gap))]
        tr_fresh_quality=mean([v['quality'] for v in tr_fresh]) if len(tr_fresh)>=3 else None
        trseason_rate,trseason_count=record_stats(self.trainers,trainer,lambda x:x['code']==code and
                           x['date'][:4]<asof[:4] and season_distance(x['date'],asof)<=45)
        j_rate,j_count=record_stats(self.jockeys,jockey,lambda x:x['code']==code)
        jcourse_rate,jcourse_n=record_stats(self.jockeys,jockey,lambda x:x['course']==course and x['code']==code)
        family=self.family_signal([('sire',ctx.get('sire')),('dam',ctx.get('dam'))],key,code,current_going,trip,info.get('_broad'))
        # Strong direct evidence should progressively supersede pedigree priors.
        def fade_family(signal,own_n):
            val=family.get(signal)
            return (.45+(val-.45)*6/(6+own_n)) if val is not None else None
        family['family_jump_quality']=fade_family('family_jump_quality',len(same_code))
        family['family_trip_quality']=fade_family('family_trip_quality',len(near))
        family['family_ground_quality']=fade_family('family_ground_quality',len(onground))
        family['family_aw_quality']=fade_family('family_aw_quality',sum(p['broad']=='AW' for p in previous))
        base.update({
         'effective_form_quality':effective,'first_after_break':int(fresh),
         'break_days_current':current_gap,'break_return_quality':fresh_quality,
         'break_return_count':len(relevant),'break_return_similarity':confidence if fresh else None,
         'seasonal_form_delta':seasonal_delta,'seasonal_support':len(season),
         'campaign_run_number':campaign_num,'seasonal_previous_years':years,
         'incident_ability_quality3':mean([p['quality'] for p in incidents]),
         'incident_ability_evidence3':len(incidents),
         'contextual_form_trend':(last[-1]['quality']-mean([p['quality'] for p in last[:-1]])
               if len(last)>=2 and last[-1]['quality'] is not None and
                  mean([p['quality'] for p in last[:-1]]) is not None else None),
         'adjusted_margin_quality3':mean(margins),'experience_same_code_runs':len(same_code),
         'experience_same_type_runs':len(same_type),
         'trainer_change_for_horse':int(bool(previous and trainer and previous[-1].get('trainer') and trainer!=previous[-1]['trainer'])),
         'trainer_return_quality':tr_fresh_quality,'trainer_return_count':len(tr_fresh),
         'trainer_seasonal_winrate':trseason_rate,'trainer_seasonal_count':trseason_count,
         'trainer_code_winrate':tr_rate,'trainer_code_count':tr_count,
         'jockey_code_winrate':j_rate,'jockey_code_count':j_count,
         'jockey_course_winrate':jcourse_rate,'jockey_course_count':jcourse_n,
         'horse_course_quality':mean([p['quality'] for p in atcourse]),'horse_course_runs':len(atcourse),
         'horse_near_trip_quality':mean([p['quality'] for p in near]),'horse_near_trip_runs':len(near),
         'horse_same_ground_quality':mean([p['quality'] for p in onground]),'horse_same_ground_runs':len(onground),
         'nlp_stamina3':mean([p['stamina'] for p in last]),
         'incident_jump_quality3':mean(jvals),'incident_jump_evidence3':len(jvals),
         'jumping_nlp_quality3':jump_nlp,'jump_nlp_coverage':jump_n/max(1,len(last)),
         'horse_chase_experience':sum(p['disc']=='CHASE' for p in previous),
         'horse_hurdle_experience':sum(p['disc']=='HURDLE' for p in previous),
         'historical_jump_errors3':sum(p['jump_errors'] for p in last),
         **family,
        })
        # Remove an unrestricted last-three average for the returning horse.
        if fresh:
            # Last-three PERFORMANCE evidence is replaced by returns from
            # comparable earlier layoffs. Race-tier history and experience
            # remain factual context, not a recent-form performance proxy.
            qseries=relevant
            nlp_quality,n_used=nlp.predict([p['comment'] for p in qseries])
            jquality,jn=nlp.jumping([p['comment'] for p in qseries if p['disc'] in ('CHASE','HURDLE')])
            base.update({
                'horse_form_quality3':None,
                'horse_form_trend':None,
                'same_code_form3':None,
                'horse_beaten_per_mile3':mean([p['beaten']/(p['distance_f']/8.)
                                              for p in qseries if p.get('beaten') is not None
                                              and p.get('distance_f') is not None and p['distance_f']>=4]),
                'horse_comment_phrase3':mean([p.get('phrase') for p in qseries]),
                'horse_comment_nlp3':nlp_quality,
                'horse_nlp_coverage':n_used/max(1,len(qseries)),
                'horse_jumping_comment3':mean([p.get('jump_note') for p in qseries]),
                'horse_interference3':mean([p.get('interference') for p in qseries]),
                'incident_ability_quality3':mean([p['quality'] for p in qseries if p.get('ability_from_unfinished')]),
                'incident_ability_evidence3':sum(bool(p.get('ability_from_unfinished')) for p in qseries),
                'contextual_form_trend':None,
                'adjusted_margin_quality3':mean([p.get('margin_quality') for p in qseries]),
                'nlp_stamina3':mean([p.get('stamina') for p in qseries]),
                'incident_jump_quality3':mean([p.get('jump_context') for p in qseries]),
                'incident_jump_evidence3':sum(p.get('jump_context') is not None for p in qseries),
                'jumping_nlp_quality3':jquality,
                'jump_nlp_coverage':jn/max(1,len(qseries)),
                'historical_jump_errors3':sum(p.get('jump_errors',0) for p in qseries),
            })
        # Legacy RAW beaten-distance is never a scoring feature.
        base.pop('horse_beaten3',None)
        return base
    def add(self,rows,asof,info,field_or):
        # Reuse Stage137 opponent matching and form-state updates, then replace
        # its raw-margin quality with context/mile adjusted performance quality.
        _=super().add(rows,asof,info,field_or)
        by=info.get('_by_identity',{})
        current_code=info.get('discipline')
        todays_going=info.get('_going')
        broad=info.get('_broad')
        comments=[]
        import stage108_model_g2 as G
        for r in [z for z in rows if S.is_starter(z)]:
            key=S.ident(r)
            if not key or not self.h[key]:continue
            p=self.h[key][-1]
            historic=self.h[key][-2] if len(self.h[key])>=2 else None
            p['prior_gap_days']=((dt.date.fromisoformat(asof)-dt.date.fromisoformat(historic['date'])).days
                                 if historic else None)
            event=parse_incident(r[22],r[12],len([z for z in rows if S.is_starter(z)]),r[5],
                                 G.beaten_lengths(r[23]),current_code)
            p['quality']=event['quality']
            p['margin_quality']=(math.exp(-event['margin_per_mile']/7.) if event['margin_per_mile'] is not None else None)
            p['jump_context']=event['jump'];p['stamina']=event['stamina']
            p['ability_from_unfinished']=event['ability_from_unfinished']
            p['jump_errors']=event['mistakes']
            p['going']=todays_going;p['trip']=num(r[5]);p['course']=info.get('_course')
            p['broad']=broad;p['trainer']=str(r[14] or '').upper()
            p['type']=info.get('race_experience_type') or 'UNKNOWN'
            # G5 rival corroboration is updated with the same adjusted metric.
            if historic and (historic['race_id'],key) in self.nextout:
                self.nextout[(historic['race_id'],key)]['quality']=event['quality']
            relative={'date':asof,'horse':key,'code':current_code,'broad':broad,
                      'going':todays_going,'trip':num(r[5]),'quality':event['quality'],
                      'win':int(num(r[12])==1),'course':info.get('_course'),
                      'gap':p['prior_gap_days']}
            sire=str(r[18] or '').strip().upper();dam=str(r[19] or '').strip().upper()
            if sire:self.family[('sire',sire)].append(relative)
            if dam:self.family[('dam',dam)].append(relative)
            trainer=str(r[14] or '').strip().upper();jockey=str(r[15] or '').strip().upper()
            if trainer:self.trainers[trainer].append(relative)
            if jockey:self.jockeys[jockey].append(relative)
            comments.append((S.sanitize_comment(r[22]),event['quality']))
        return comments


def _load_and_construct(db,reference):
    """Extend Form to 2015 without modifying ANY of the Stage30 frozen D
    evidence, which still begins at 2022 exactly as in Stage137.
    Temporarily replace in-memory classes; restore them in finally.
    """
    import stage30_gap_aware_abcd as D
    import stage108_model_g2 as G
    orig_form,orig_nlp=S.Form,S.PriorCommentNLP
    orig_features,orig_read_ref,orig_read_races=S.FEATURES,S.read_reference,S.read_races
    orig_cat=S.race_catalogue
    orig_past=D.Past
    class FixedParentPast(orig_past):
        def update(self,rows):
            if rows[0][1]<D_BASELINE_START:return
            return super().update(rows)
    def ref_get(path):return G.load_reference(path,first=HISTORY_FIRST,last='2025-12-31')
    def history(db,first=HISTORY_FIRST,last='2025-12-31'):
        return orig_read_races(db,first=first,last=last)
    def cat(e,region,name,starters):
        d,b,g,t=orig_cat(e,region,name,starters)
        d['_going']=G.going_band(e[6]);d['_trip']=num(e[5]);d['_course']=G.course_key(e[2]);d['_broad']=b
        d['_by_identity']={S.ident(z):{'trainer':z[14],'jockey':z[15],'sire':str(z[18] or '').upper(),
                                 'dam':str(z[19] or '').upper()} for z in starters if S.ident(z)}
        return d,b,g,t
    try:
        S.Form=ContextForm;S.PriorCommentNLP=ContextNLP
        S.FEATURES=FEATURES;S.read_reference=ref_get;S.read_races=history;S.race_catalogue=cat
        D.Past=FixedParentPast
        races,audit,refaudit=S.construct(db,reference)
    finally:
        S.Form=orig_form;S.PriorCommentNLP=orig_nlp;S.FEATURES=orig_features
        S.read_reference=orig_read_ref;S.read_races=orig_read_races;S.race_catalogue=orig_cat
        D.Past=orig_past
    for r in races:
        if r['xf'].shape[1]!=len(FEATURES):raise AssertionError('Pillar feature array mismatch')
    audit['historic_lookback_start']=HISTORY_FIRST
    audit['original_d_evidence_start_preserved']=D_BASELINE_START
    return races,audit,refaudit


def pillar_input(r,pillar):
    indices=[FEATURE_POS[x] for x in PILLAR_FEATURES[pillar]]
    return r['xf'][:,indices]


def fit_pillars(train):
    """Pillar-specific supervised learners; never an independent 50-feature model.
    Only completed training races enter each fit; all final outputs are 8 pillar
    scores, selected by one dated, race-category-specific eight-weight vector.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    if not train:raise RuntimeError('Missing completed training races')
    by=defaultdict(list)
    for r in train:
        for group in dict.fromkeys(('ALL',r['broad'],r['group'])):
            by[group].append(r)
    fitted={};counts={}
    for group,rs in by.items():
        if group!='ALL' and len(rs)<MIN_PILLAR_RACES:continue
        counts[group]=len(rs)
        fitted[group]={}
        outcome=np.concatenate([r['win'] for r in rs])
        if len(set(outcome.tolist()))!=2:continue
        for pillar in PILLARS:
            if pillar=='Draw' and group in ('NH','HURDLE','CHASE','NH_FLAT'):continue
            if pillar=='Jumping' and group in ('FLAT','AW','FLAT_UNKNOWN','NH_FLAT'):continue
            xx=np.vstack([pillar_input(r,pillar) for r in rs])
            # Small, robust learner with native NaN handling. A pillar is NOT
            # allowed to see data assigned to another pillar.
            if np.all(~np.isfinite(xx)):
                continue
            model=HistGradientBoostingClassifier(max_iter=52,max_leaf_nodes=10,
                max_depth=3,min_samples_leaf=120,learning_rate=.05,
                l2_regularization=12,early_stopping=False,random_state=138)
            model.fit(xx,outcome)
            fitted[group][pillar]=model
    if len(fitted.get('ALL',{}))<7:raise RuntimeError('Insufficient global pillar learners')
    return fitted,counts


def predict_eight(fitted,r):
    """One scorer per pillar. Scores are within-race centered logits.
    All other signals are suppressed before the final rank calculation.
    """
    score=np.zeros((len(r['horse']),8),dtype=np.float32)
    chosen={}
    for i,pillar in enumerate(PILLARS):
        if pillar=='Draw' and r['broad']=='NH':continue
        if pillar=='Jumping' and (r['broad']!='NH' or r['kind']=='NH_FLAT'):continue
        predictor=None;source=None
        for group in dict.fromkeys((r['group'],r['broad'],'ALL')):
            pred=fitted.get(group,{}).get(pillar)
            if pred is not None:predictor=pred;source=group;break
        if predictor is None:raise RuntimeError('No trained model for '+pillar)
        pp=np.clip(predictor.predict_proba(pillar_input(r,pillar))[:,1],1e-5,1-1e-5)
        logits=np.log(pp/(1-pp))
        centered=logits-logits.mean()
        den=max(.30,float(centered.std()))
        score[:,i]=np.clip(centered/den,-4,4)
        chosen[pillar]=source
    return score,chosen


def weights_for(evidence,r,objective='SR'):
    """Choose among fixed Stage137 eight-pillar combinations using only
    PRIOR, OUT-OF-FOLD quarter forecasts (not in-sample fitted predictions).
    Main Stage138 ranking ALWAYS optimises win strike-rate, never SP ROI.
    """
    assert objective=='SR'
    for group in S.hierarchy(r):
        rows=[x for x in evidence if group in S.hierarchy(x['race'])]
        if len(rows)<MIN_LOCAL_META:continue
        broad='NH' if r['broad']=='NH' else r['broad']
        grid=S.CANDIDATES.copy()
        inactive=4 if broad=='NH' else 7
        grid[:,inactive]=0
        if r['kind']=='NH_FLAT':grid[:,7]=0
        grid/=np.maximum(grid.sum(axis=1,keepdims=True),1e-12)
        wins=np.zeros(len(grid),dtype=np.float64)
        priced=np.zeros(len(grid),dtype=np.float64)
        returns=np.zeros(len(grid),dtype=np.float64)
        for x in rows:
            # Only selection scores generated WITHOUT training on this race.
            choices=np.argmax(x['scores']@grid.T,axis=0)
            won=choices==x['race']['winner_idx'];wins+=won
            sp=x['race']['sp'][choices]
            good=np.isfinite(sp)&(sp>1)&(sp<1001)
            priced+=good
            returns+=np.where(won&good,np.minimum(sp,21.),0.)
        parent=S.mask_weights(S.FROZEN,broad)
        if r['kind']=='NH_FLAT':parent[7]=0;parent/=parent.sum()
        concentration=np.sum((grid-parent)**2,axis=1)
        objective_values=wins/len(rows)-.012*concentration
        best=int(np.argmax(objective_values))
        roi=np.divide(returns,np.maximum(priced,1))-1
        return grid[best],{'group':group,'source':'PREVIOUS_OUT_OF_FOLD',
            'meta_races':len(rows),'candidate':best,
            'prior_strike_pct':round(100*wins[best]/len(rows),2),
            'prior_capped_sp_roi_pct':round(100*roi[best],2) if priced[best] else None,
            'best_roi_candidate_diagnostic_only':int(np.argmax(roi)),
            'current_model_objective':'WIN_STRIKE_RATE',
            'candidate_count':len(grid)}
    fallback=S.mask_weights(S.FROZEN,r['broad'])
    if r['kind']=='NH_FLAT':fallback[7]=0;fallback/=fallback.sum()
    return fallback,{'group':'FROZEN_FALLBACK',
             'source':'INSUFFICIENT_PRIOR_OUT_OF_FOLD_RACES','meta_races':0,
             'candidate':0,'current_model_objective':'WIN_STRIKE_RATE'}


def metrics(rows):
    n=len(rows);wins=sum(x['won'] for x in rows)
    priced=[x for x in rows if x['sp'] is not None and 1<x['sp']<1001]
    ret=sum(x['sp'] for x in priced if x['won'])
    capped=sum(min(x['sp'],21) for x in priced if x['won'])
    return {'races':n,'winners':wins,'strike_pct':round(100*wins/n,2) if n else None,
        'priced':len(priced),'roi_sp_pct':round(100*(ret/len(priced)-1),2) if priced else None,
        'roi_sp_capped20to1_pct':round(100*(capped/len(priced)-1),2) if priced else None}


def write_rows(path,rows,compressed=False):
    if not rows:raise RuntimeError('No rows for '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    opener=gzip.open if compressed else open
    with opener(path,'wt',newline='',encoding='utf-8') as f:
        fields=list(dict.fromkeys(k for row in rows for k in row))
        wr=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');wr.writeheader();wr.writerows(rows)


def frozen_predictions(r):
    vals=r['x8']@S.mask_weights(S.FROZEN,'NH' if r['d_original_style']=='jumps' else 'FLAT')
    return np.argsort(-vals,kind='stable')


def compare_stage137(source,ours):
    """Fail closed if frozen D no longer selects identical runners; baseline
    original race IDs, winner identity, date and race must match Stage137.
    Stage137 original NLP selections are imported only for paired reporting.
    """
    import pandas as pd
    base=pd.read_csv(source,compression='infer',low_memory=False)
    base=base[base['model'].isin(('FROZEN_D','D_RACETYPE_NLP'))].copy()
    base['race_id']=base['race_id'].astype(str)
    orig=base[base['model']=='FROZEN_D']
    selected={(x['race_id'],x['model']):x for x in ours}
    if len(orig)!=sum(x['model']=='FROZEN_D' for x in ours):
        raise RuntimeError('Race count mismatch versus Stage137 frozen comparator')
    for x in orig.itertuples():
        got=selected.get((str(x.race_id),'FROZEN_D'))
        if not got or got['horse']!=x.horse or got['date']!=x.date or got['won']!=int(x.won):
            raise RuntimeError(f'Frozen D selection drift / mismatch: race {x.race_id}')
    paired=[];changes=0
    stage137_index={str(x.race_id):x for x in base[base.model=='D_RACETYPE_NLP'].itertuples()}
    for x in ours:
        if x['model']!='D_138_EIGHT_PILLARS':continue
        src=stage137_index.get(x['race_id'])
        if src is None:raise RuntimeError('Missing Stage137 NLP exact race '+x['race_id'])
        changes+=int(x['horse']!=src.horse)
        paired.append({**x,'stage137_horse':src.horse,'stage137_won':int(src.won),
            'stage137_sp':src.sp,'changed_from_stage137':int(x['horse']!=src.horse)})
    return paired,{'frozen_baseline_exact_match':True,'stage137_paired_races':len(paired),
                   'new_vs_stage137_changed_races':changes}


def walkforward(races,out,stage137):
    train=[r for r in races if '2023-01-01'<=r['date']<'2023-10-01']
    meta=[r for r in races if INITIAL_META_START<=r['date']<=INITIAL_META_END]
    if len(train)<400 or len(meta)<120:raise RuntimeError('2023 Q1-Q3 / Q4 split too small')
    # 2023 Q4 gets genuine prequential pillar scores from models fit only through
    # 2023 Q3. It is NOT a reported test year, merely dated weight calibration.
    model,counts=fit_pillars(train)
    prior=[]
    for r in meta:
        scores,_=predict_eight(model,r)
        prior.append({'race':r,'scores':scores,'origin':'2023Q4_PREQUEL'})
    predictions=[];folds=[];weights_log=[]
    for period,start,end in S.PERIODS:
        training=[r for r in races if '2023-01-01'<=r['date']<start]
        test=[r for r in races if start<=r['date']<=end]
        if len(test)<200:raise RuntimeError('Insufficient races in '+period)
        fit,counts=fit_pillars(training)
        decisions={}
        # Weights are fixed before the quarter for every category: never
        # reselect weights based on test-quarter outcomes.
        for r in test:
            k=(r['subtype'],r['group'],r['broad'])
            if k not in decisions:decisions[k]=weights_for(prior,r)
        for k,(w,meta_) in decisions.items():
            weights_log.append({'period':period,'subtype':k[0],'code':k[1],'broad':k[2],
              **meta_,**{p:round(float(z),6) for p,z in zip(PILLARS,w)}})
        next_ledger=[];bank_counts=collections.Counter()
        for r in test:
            a,contributors=predict_eight(fit,r)
            w,decision=decisions[(r['subtype'],r['group'],r['broad'])]
            picks=np.argsort(-(a@w),kind='stable')
            old=frozen_predictions(r)
            for model_name,order in [('FROZEN_D',old),('D_138_EIGHT_PILLARS',picks)]:
                i,j=int(order[0]),int(order[1]);v=num(r['sp'][i]);price=v if v is not None and math.isfinite(v) else None
                predictions.append({'period':period,'trained_through':str(dt.date.fromisoformat(start)-dt.timedelta(days=1)),
                 'date':r['date'],'race_id':r['race_id'],'course':r['course'],'country':r['country'],
                 'race_name':r['race_name'],'broad':r['broad'],'discipline':r['kind'],
                 'subtype':r['subtype'],'official_tier':r['official_tier'],
                 'race_type':r['type'],'model':model_name,'horse':r['horse'][i],
                 'second_horse':r['horse'][j], 'won':int(i==r['winner_idx']),
                 'rank2_won':int(j==r['winner_idx']), 'sp':price,'runner_count':len(r['horse']),
                 'winner':r['horse'][r['winner_idx']],
                 'pillar_weights_group':decision['group'] if model_name=='D_138_EIGHT_PILLARS' else 'FROZEN',
                 'pillar_model_sources':json.dumps(contributors) if model_name=='D_138_EIGHT_PILLARS' else '{}',
                 'pillar_weights':json.dumps({p:round(float(w[k]),5) for k,p in enumerate(PILLARS)}) if model_name=='D_138_EIGHT_PILLARS' else '{}',
                 'rank1_pillar_scores':json.dumps({p:round(float(a[i,k]),5) for k,p in enumerate(PILLARS)}) if model_name=='D_138_EIGHT_PILLARS' else '{}',
                 'rank2_pillar_scores':json.dumps({p:round(float(a[j,k]),5) for k,p in enumerate(PILLARS)}) if model_name=='D_138_EIGHT_PILLARS' else '{}',
                 'rank1_weighted_pillar_contributions':json.dumps({p:round(float(a[i,k]*w[k]),5) for k,p in enumerate(PILLARS)}) if model_name=='D_138_EIGHT_PILLARS' else '{}'})
            next_ledger.append({'race':r,'scores':a,'origin':period})
            bank_counts.update(contributors.values())
        folds.append({'period':period,'trained_through':str(dt.date.fromisoformat(start)-dt.timedelta(days=1)),
                      'trained_races':len(training),'quarter_races':len(test),
                      'previous_out_of_fold_meta_races':len(prior),
                      'pillar_model_bank_races':counts,'pillar_selection_bank_usage':dict(bank_counts)})
        prior.extend(next_ledger)
        print('Completed Stage138',period,'trained',len(training),'tested',len(test),
              'available OOF meta',len(prior),flush=True)
    assert len(folds)==8
    # Derive paired Stage137 comparator, then add its fixed archived predictions
    # into the common reporting cohort. Never alter Stage137 horses/winners.
    paired,guard=compare_stage137(stage137,predictions)
    import pandas as pd
    orig=pd.read_csv(stage137,compression='infer',low_memory=False)
    old=orig[orig.model=='D_RACETYPE_NLP'].copy()
    keys={p['race_id'] for p in predictions if p['model']=='FROZEN_D'}
    old=old[old.race_id.astype(str).isin(keys)]
    for item in old.to_dict('records'):
        p={'period':item['period'],'date':item['date'],'race_id':str(item['race_id']),
           'course':item.get('course'),'country':item.get('country'),
           'race_name':item.get('race_name'),'broad':item.get('broad'),
           'discipline':item.get('discipline'),'subtype':item.get('subtype'),
           'official_tier':item.get('official_tier'),'race_type':item.get('type'),
           'model':'D_RACETYPE_NLP','horse':item['horse'],
           'second_horse':item.get('second_horse'),'won':int(item['won']),
           'rank2_won':int(item.get('rank2_won') or 0),
           'sp':num(item.get('sp')),'runner_count':int(item['runner_count']),
           'winner':item['winner'],'trained_through':item.get('trained_through'),
           'pillar_weights_group':'STAGE137_ARCHIVED','pillar_model_sources':'{}',
           'pillar_weights':'{}','rank1_pillar_scores':'{}','rank2_pillar_scores':'{}',
           'rank1_weighted_pillar_contributions':'{}'}
        predictions.append(p)
    if len(old)!=len(paired):raise RuntimeError('Stage137 comparison dropped races')
    by=defaultdict(list)
    for row in predictions:
        for group_key,key in [('ALL','ALL'),('YEAR',row['date'][:4]),('QUARTER',row['period']),
                              ('BROAD',row['broad']),('DISCIPLINE',row['discipline']),
                              ('RACE_TYPE',row['subtype']),('OFFICIAL_TIER',row['official_tier']),
                              ('BROAD_RACE_TYPE',str(row['broad'])+'|'+str(row['subtype'])),
                              ('COUNTRY',row['country']),('COURSE',row['course'])]:
            by[(row['model'],group_key,key)].append(row)
    summary=[{'model':m,'breakdown':br,'group':g,**metrics(rows)} for (m,br,g),rows in sorted(by.items())]
    write_rows(out/'stage138-full-quarters-rank1-rank2.csv.gz',predictions,True)
    write_rows(out/'stage138-paired-versus-stage137.csv.gz',paired,True)
    write_rows(out/'stage138-period-discipline-racetype.csv',summary)
    write_rows(out/'stage138-quarterly-eight-pillar-weights.csv',weights_log)
    (out/'stage138-fold-train-audit.json').write_text(json.dumps(folds,indent=2)+'\n')
    return summary,folds,guard,predictions


def early_baseline_guard(races,stage137):
    """Check frozen D race/horse identities BEFORE costly quarterly fitting.
    An apparent improvement due to a changed baseline must never pass.
    """
    import pandas as pd
    archived=pd.read_csv(stage137,compression='infer',usecols=['model','race_id','horse','date','won'],low_memory=False)
    archived=archived[archived.model=='FROZEN_D']
    saved={str(x.race_id):(str(x.date),str(x.horse),int(x.won)) for x in archived.itertuples()}
    actual={}
    for r in races:
        if r['date']<'2024-01-01':continue
        i=int(frozen_predictions(r)[0]);actual[r['race_id']]=(r['date'],r['horse'][i],int(i==r['winner_idx']))
    if len(saved)!=len(actual):
        raise RuntimeError(f'Frozen D cohort drift: Stage137 {len(saved)} vs Stage138 {len(actual)} races')
    for race_id,tup in saved.items():
        if actual.get(race_id)!=tup:
            raise RuntimeError('Frozen D unchanged-source proof failed: '+race_id)
    return {'preflight_original_d_exact_match':True,'matched_races':len(actual)}


def audit_feature_coverage(races,out):
    """Observed historic runner feature availability; sample counts are not
    accuracy. Missing values remain NaN, never fabricated zero performance.
    """
    keys=('effective_form_quality','first_after_break','break_return_quality',
          'seasonal_form_delta','seasonal_previous_years','incident_ability_quality3',
          'incident_jump_quality3','jumping_nlp_quality3','family_jump_quality',
          'family_trip_quality','family_ground_quality','family_aw_quality',
          'adjusted_margin_quality3','current_tier_strength',
          'past_opposition_median3','horse_comment_nlp3')
    data=[]
    for group in ['ALL',*sorted(set(x['broad'] for x in races))]:
        rr=[x for x in races if group=='ALL' or x['broad']==group]
        counts={k:0 for k in keys};n=0;breaks=0;break_supported=0
        for race in rr:
            x=race['xf'];n+=len(x)
            for k in keys:
                counts[k]+=int(np.isfinite(x[:,FEATURE_POS[k]]).sum())
            b=x[:,FEATURE_POS['first_after_break']]
            eligible=np.isfinite(b)&(b>0)
            breaks+=int(eligible.sum())
            supported=np.isfinite(x[:,FEATURE_POS['break_return_quality']])
            break_supported+=int(np.sum(eligible&supported))
        for k in keys:
            data.append({'group':group,'feature':k,'runners':n,
                         'known':counts[k],
                         'coverage_pct':round(100*counts[k]/n,2) if n else 0,
                         'returning_runners':breaks,'returning_with_historic_comparable':break_supported})
    write_rows(out/'stage138-feature-coverage.csv',data)
    return {'all_runner_rows':next(x['runners'] for x in data if x['group']=='ALL'),
        'returning_runners':next(x['returning_runners'] for x in data if x['group']=='ALL'),
        'returning_with_comparable_break':next(x['returning_with_historic_comparable'] for x in data if x['group']=='ALL'),
        'global_feature_coverage_pct':{x['feature']:x['coverage_pct'] for x in data if x['group']=='ALL'}}


def research(db,reference,stage137,out):
    import stage30_gap_aware_abcd as D
    orig=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    if orig['chosen_combination']!='D-064-gap14':raise RuntimeError('Frozen D identity changed')
    exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
    if not np.allclose([orig['weights_pct'][x] for x in PILLARS],exact*100,atol=.011):
        raise RuntimeError('Original frozen D weights changed')
    S.FROZEN[:]=exact;S.CANDIDATES=S.candidate_weights()
    races,audit,refaudit=_load_and_construct(db,reference)
    if len(races)<15000:raise RuntimeError('Insufficient exact dated history')
    baseline_preflight=early_baseline_guard(races,stage137)
    print('Frozen D identity preflight PASS',baseline_preflight,flush=True)
    out.mkdir(parents=True,exist_ok=True)
    coverage=audit_feature_coverage(races,out)
    sums,folds,guard,rows=walkforward(races,out,stage137)
    overall={m:next(x for x in sums if x['model']==m and x['breakdown']=='ALL') for m in MODELS}
    report={'version':VERSION,'paper_only':True,'writes_to_live_app':False,
      'original_frozen_d_weight_id':'D-064-gap14','frozen_d_unchanged':True,
      'final_scoring_dimensions':list(PILLARS),'final_scoring_dimension_count':8,
      'criteria_all_assigned_to_a_pillar':True,'independent_nlp_ninth_pillar':False,
      'raw_beaten_distance_predictor':False,'market_prices_used_to_choose_model_weights':False,
      'history_start':HISTORY_FIRST,'training_2023_races':sum(x['date'][:4]=='2023' for x in races),
      'test_quarters':[x['period'] for x in folds],
      'identical_evaluated_races':overall['FROZEN_D']['races'],
      'source_audit':audit,'reference_audit':refaudit,'baseline_guard':guard,
      'frozen_d_early_preflight':baseline_preflight,
      'feature_coverage':coverage,
      'overall':overall,'pillar_features':PILLAR_FEATURES,'folds':folds,
      'warnings':[NLP_DISCLAIMER,
       '2015 onward historic form is used only for Stage138; original D evidence remains 2022 onward as in Stage137.',
       'The fixed original D recipe was historically chosen using 2024 outcomes; 2024 comparisons are not independently pristine.',
       'Some historical grades, comments and breeding are incomplete; unknowns are left missing.',
       'Seasonal and incident-derived signals are hypotheses; a late fall is not treated as a guaranteed win.',
       '2024-2025 quarters are historical walk-forward tests of a recipe developed after seeing other historical results.',
       'Final SP is used only to settle retrospective performance; not a bettable pre-off price.',
       'A prospective 2026 paper cohort is required before promotion.']}
    (out/'stage138-summary.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print('STAGE138_OVERALL',json.dumps(overall),flush=True)
    return report


def self_test():
    assert len(PILLARS)==8 and len(FEATURES)==len(set(FEATURES))
    assert 'horse_beaten3' not in FEATURES and 'frozen_d_score' not in FEATURES
    assert 'horse_comment_nlp3' in PILLAR_FEATURES['Ability']
    assert 'jumping_nlp_quality3' in PILLAR_FEATURES['Jumping']
    assert 'nlp_stamina3' in PILLAR_FEATURES['Distance']
    a=parse_incident('blundered at 2nd, mistake at third, fell at the 3rd',None,10,24,None,'CHASE')
    b=parse_incident('travelled strongly, led and clear when fell at the last',None,10,24,None,'CHASE')
    assert a['quality'] is None and a['jump']<b['jump'] and a['early']
    assert b['quality'] is not None and b['late'] and b['clear'] and b['jump']<.3
    c=parse_incident('brought down at the last',None,10,24,None,'CHASE')
    assert c['jump'] is None and c['quality'] is None
    d=parse_incident('stayed on',7,10,8,2,'FLAT')
    e=parse_incident('stayed on',7,10,8,29,'FLAT')
    assert d['quality']>e['quality'] and d['margin_per_mile']==2
    assert abs(season_distance('2023-12-25','2024-01-10')-16)<1e-7
    assert break_band(185)=='180_364'
    state=ContextForm()
    assert not state.features('not-a-horse','2024-01-01',{'race_experience_type':'MAIDEN'},
                              type('Fake',(),{'predict':lambda *a:(None,0),'jumping':lambda *a:(None,0)})(), 'FLAT')['first_after_break']
    print('STAGE138 SELFTEST PASS: eight pillars, no raw margins, falls/stamina, season cycle, breaks, safe missing evidence')


def main():
    p=argparse.ArgumentParser();subs=p.add_subparsers(dest='task',required=True)
    subs.add_parser('self-test')
    a=subs.add_parser('walkforward')
    a.add_argument('--db',type=pathlib.Path,required=True)
    a.add_argument('--reference',type=pathlib.Path,required=True)
    a.add_argument('--stage137',type=pathlib.Path,required=True)
    a.add_argument('--out',type=pathlib.Path,required=True)
    args=p.parse_args()
    if args.task=='self-test':self_test();return
    research(args.db,args.reference,args.stage137,args.out)

if __name__=='__main__':main()
