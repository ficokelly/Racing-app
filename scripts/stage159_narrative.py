"""Stage159: truthful driver-led prose for frozen daily specialist rankings.

Only source-run input-group impacts recorded at scoring time can be described as
model drivers. Racecard observations may be supporting context, NEVER asserted to
be the reason for a model ranking without driver evidence. No odds/results used.
"""
from __future__ import annotations
import collections
import math
import re


METHODS={
 'tree_median_sensitivity': 'For this non-linear model, each input group was replaced with a neutral value from this race to check how its score responds. These sensitivity checks are not causal or additive.',
 'exact_logistic_logodds': 'These are exact fitted-model log-odds contributions relative to the average runner in this race; the model can also include interaction terms.',
 'exact_eight_pillar_additive': 'These are the exact weighted contributions of the eight original racing pillars, measured against the average runner in this race.',
}


def fmt(v):
    try:
        n=float(v)
        return f'{n:.3f}' if math.isfinite(n) else 'unavailable'
    except (TypeError,ValueError):return 'unavailable'


def join(items):
    if not items:return ''
    if len(items)==1:return items[0]
    if len(items)==2:return items[0]+' and '+items[1]
    return ', '.join(items[:-1])+', and '+items[-1]


def pretty_group(label):
    return str(label).replace('_',' ').lower()


def parse_drivers(horse):
    data=horse.get('model_drivers')
    if not isinstance(data,dict) or data.get('method') not in METHODS:return None
    def pairs(key):
        out=[]
        for item in data.get(key,[])[:6]:
            if not isinstance(item,list) or len(item)!=2:continue
            try:
                amount=float(item[1]);name=pretty_group(item[0])
                if math.isfinite(amount) and name and abs(amount)>1e-5:out.append((name,amount))
            except (TypeError,ValueError):pass
        return out
    return {'method':data['method'],'positive':pairs('positive'),'negative':pairs('negative')}


def names(parts,limit=3):return join([s for s,_ in parts[:limit]])


def score_text(h):return fmt(h.get('research_score'))


def supportive(facts,limit=2):
    # Facts derived from racecards, *not* claimed to drive the specialist.
    parts=[v.strip() for v in facts if isinstance(v,str) and len(v.strip())>4]
    return join(parts[:limit])


def form_evidence(c):
    raw=c.get('form') or c.get('recent_form') or c.get('form_figures') or ''
    if isinstance(raw,list):raw=''.join(str(x) for x in raw)
    digits=re.findall(r'[1-9FUP0]',str(raw).upper())
    if not digits:return ''
    tail=digits[-4:]
    if tail[-1]=='1':return 'a victory on the most recent form figure'
    if tail[-1]=='2':return 'a second-place finish last time'
    if tail[-1]=='3':return 'a third-place finish last time'
    if tail[-1] in ('F','U','P'):return 'a non-completion on the latest recorded outing'
    wins=tail.count('1')
    return f'{wins} win'+('s' if wins!=1 else '')+f' in the last {len(tail)} listed form figures' if wins else ''


def factual_context(c):
    evidence=[]
    form=form_evidence(c)
    if form:evidence.append(form)
    try:
        rating=float(c.get('ofr') or c.get('official_rating'))
        if math.isfinite(rating):evidence.append(f'an official rating of {rating:g}')
    except (TypeError,ValueError):pass
    try:
        last=int(c.get('days_since_last_run') or c.get('last_run'))
        if last>60:evidence.append(f'{last} days since its last run')
    except (TypeError,ValueError):pass
    return evidence


def horse_sentence(h,leader,field_size,third,card):
    name=str(h['horse']);rank=int(h.get('rank') or 0)
    data=parse_drivers(h)
    prefix=f'{name} is ranked #{rank} of {field_size} by the selected specialist (model rating {score_text(h)}).'
    if rank==1 and third:
        prefix=f'{name} leads this field with a model rating of {score_text(h)}.'
    if rank==2:prefix=f'{name} is second with a model rating of {score_text(h)}, behind {leader["horse"]} ({score_text(leader)}).'
    if data:
        pos=data['positive'];neg=data['negative']
        bits=[prefix]
        if pos:
            if data['method']=='tree_median_sensitivity':bits.append(f'The biggest score-raising sensitivities are {names(pos)} when those inputs are compared with a field-median replacement.')
            elif data['method']=='exact_eight_pillar_additive':bits.append(f'The strongest positive weighted pillars relative to the field are {names(pos)}.')
            else:bits.append(f'The strongest positive fitted-model contributions relative to this field are {names(pos)}.')
        else:bits.append('No single logged input group shows a substantial positive advantage over the field reference, so the ranking cannot be assigned to one verified factor.')
        if neg:bits.append(f'Weaker factors within the same model comparison include {names(neg,2)}.')
        if rank>=3:bits.append(f'The combined rating still falls below {leader["horse"]}, even where individual inputs look favourable.')
        if rank in (1,2):
            factual=supportive(factual_context(card),2)
            if factual:bits.append(f'Separately, the racecard records {factual}; those facts are not necessarily the features responsible for the model difference.')
        return ' '.join(bits)
    # A published rank with no feature impacts is not a licence to invent a cause.
    facts=supportive(factual_context(card),2)
    return (prefix+(' The available racecard also shows '+facts+'.' if facts else '')+
            ' Feature-level model impacts were not saved for this specialist run, so an exact reason for its rank cannot yet be verified.')


def race_sentence(race,ranked):
    first,second=ranked[:2]
    one=first['horse'];two=second['horse'];third=ranked[2] if len(ranked)>2 else None
    group=str(race.get('race_type') or 'race').replace('_',' ').lower()
    intro=(f'{race.get("nap_nb")} — ' if race.get('nap_nb') else '')+(f'The {group} specialist puts {one} first (rating {score_text(first)}), '
           f'followed by {two} ({score_text(second)})'+
           (f' and {third["horse"]} ({score_text(third)})' if third else '')+'.')
    d1=parse_drivers(first);d2=parse_drivers(second)
    if d1 and d2:
        a,b=d1['positive'],d2['positive']
        bits=[intro]
        if a:bits.append(f'The leading model inputs supporting {one} are {names(a,3)}.')
        if b:bits.append(f'{two} earns its place chiefly through {names(b,3)}.')
        shared={name for name,_ in a}&{name for name,_ in b}
        if shared and len(shared)<len(a)+len(b):
            bits.append('Both horses have some positive signals in common, but their overall combination and strength differ.')
        if third:
            bits.append(f'The next-best score belongs to {third["horse"]}; the model still rates both leaders higher on its full set of inputs.')
        bits.append(METHODS[d1['method']])
        return ' '.join(bits)
    return (intro+' The published model output confirms their order but does not yet contain '
            'complete feature-level impacts for both leaders. Racecard trends alone cannot establish why '
            'the trained model preferred these two over the field.')


def upgrade(feed,cards):
    # Check all published selections/scores before and after this prose-only pass.
    snapshot=lambda:[(r.get('date'),r.get('course'),r.get('time'),
                      r.get('selection',{}).get('horse') if isinstance(r.get('selection'),dict) else None,
                      [(h.get('horse'),h.get('rank'),h.get('research_score')) for h in r.get('runners',[])])
                     for r in feed.get('races',[])]
    before=snapshot()
    card_map={}
    for c in cards.get('runners',[]):
        if not isinstance(c,dict):continue
        key=(str(c.get('date') or '')[:10],clean(c.get('course')),clock(c.get('race_time')),clean(c.get('horse')))
        card_map[key]=c
    audited=collections.Counter()
    for race in feed.get('races',[]):
        if not race.get('selection') or len(race.get('runners',[]))<2:continue
        peers=sorted(race['runners'],key=lambda h:int(h.get('rank') or 999))
        lead=peers[0];third=peers[2] if len(peers)>2 else None
        for h in peers:
            key=(str(race.get('date') or '')[:10],clean(race.get('course')),str(race.get('time') or '')[:5],clean(h.get('horse')))
            h['commentary']=horse_sentence(h,lead,len(peers),third,card_map.get(key,{}))
            if h is lead and race.get('nap_nb'):h['commentary']=str(race['nap_nb'])+' — '+h['commentary']
            h['commentary_method']=parse_drivers(h)['method'] if parse_drivers(h) else 'unattributed_fallback'
            audited['drivers' if parse_drivers(h) else 'unattributed']+=1
        race['commentary']=race_sentence(race,peers)
        race['commentary_status']='stage159-input-attribution-v1'
        audited['races']+=1
    assert before==snapshot(),'Stage159 prose modified an existing model rank, score or selection'
    feed['stage159_commentary_audit']={'version':'input-attribution-v1',**dict(audited),
                                      'commentary_never_changes_selection':True,
                                      'nonlinear_sensitivity_is_not_causation':True}
    return dict(audited)


def clean(v):return ''.join(x for x in str(v or '').lower() if x.isalnum())
def clock(v):
    s=str(v or '')
    if 'T' in s:s=s.split('T',1)[1]
    return s[:5]


def self_test():
    rows=[{'horse':'A','rank':1,'research_score':0.7,'model_drivers':{'method':'tree_median_sensitivity','positive':[['recent form and consistency',0.04]],'negative':[['draw record',-0.01]]}},
          {'horse':'B','rank':2,'research_score':0.5,'model_drivers':{'method':'tree_median_sensitivity','positive':[['trainer history',0.03]],'negative':[]}},
          {'horse':'C','rank':3,'research_score':0.3}]
    data={'races':[{'date':'2026-11-19','course':'Thurles','time':'12:08','race_type':'beginners chase','selection':{'horse':'A'},'runners':rows}]}
    upgrade(data,{'runners':[]})
    assert 'recent form' in data['races'][0]['commentary']
    assert 'trainer history' in data['races'][0]['commentary']
    assert data['races'][0]['runners'][0]['rank']==1
    return True

if __name__=='__main__':
    self_test();print('Stage159 commentary synthetic checks passed')
