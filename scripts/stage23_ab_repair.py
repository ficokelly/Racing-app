#!/usr/bin/env python3
"""Racing Hot Pots Stage 23: exhaustive A/B *coverage* without inventing decisions.

The existing A and B selection algorithms are unchanged.  Stage17 selects from
Stage13 verified research; this wrapper adds the rest of the declared race fields
as explicit unassessed/no-selection entries. Model A published probabilities are
linked only from a VALIDATED complete-field source. Model B form scores are not
calibrated probabilities.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, json, os, re
from pathlib import Path
from zoneinfo import ZoneInfo
TZ = ZoneInfo('Europe/Dublin')
UTC=dt.timezone.utc

def load(path, default=None):
    try:return json.loads(Path(path).read_text(encoding='utf8'))
    except (OSError,ValueError):return {} if default is None else default

def save(path, obj):
    path=Path(path);path.parent.mkdir(exist_ok=True,parents=True)
    tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf8');os.replace(tmp,path)

def norm(v):return ''.join(x.lower() for x in str(v or '') if x.isalnum())
def course(v):return norm(re.sub(r'\([^)]*\)','',str(v or '')))
def clock(v):
    try:return dt.datetime.fromisoformat(str(v).replace('Z','+00:00')).astimezone(TZ).strftime('%H:%M')
    except (ValueError,TypeError,OverflowError):return str(v or '')[:5]
def race_key(x):return (str(x.get('date') or ''), course(x.get('course')),str(x.get('time') or clock(x.get('race_time')))[:5])
def nonrunner(x):
    if any(x.get(k) is True for k in ('is_non_runner','non_runner','withdrawn','scratched')):return True
    return norm(x.get('status') or x.get('runner_status')) in ('nr','nonrunner','nonstarter','scratched','withdrawn')
def cat(x):
    t=norm(x.get('type') or x.get('race_type'));s=norm(x.get('surface'));n=norm(x.get('race_name'))
    if any(k in t or k in n for k in ('jumps','hurdle','chase','steeple','bumper')):return 'jumps'
    if s in ('aw','allweather','tapeta','polytrack','synthetic') or '(aw)' in str(x.get('course','')).lower():return 'aw'
    return 'flat' if 'flat' in t or s in ('turf','grass') else 'unknown'
def upcoming(r,now):
    try:
        d=dt.datetime.fromisoformat(str(r.get('race_time','')).replace('Z','+00:00'))
        if not d.tzinfo:d=d.replace(tzinfo=TZ)
        return d.astimezone(UTC)>now+dt.timedelta(minutes=3)
    except (ValueError,TypeError):return False

def augment(root,now=None):
    root=Path(root);data=root/'data';now=now or dt.datetime.now(UTC)
    cards=load(data/'racecards.json',{'runners':[]})
    ab=load(data/'strategy-ab-selections.json',{})
    prob=load(data/'strategy-probabilities.json',{})
    stage=load(data/'strategy-selections.json',{})
    day=ab.get('day') or stage.get('day') or now.astimezone(TZ).date().isoformat()
    groups=collections.defaultdict(list)
    for x in cards.get('runners',[]):
        if x.get('date')==day and x.get('course') and x.get('race_time') and x.get('horse') and not nonrunner(x):
            groups[race_key(x)].append(x)
    if not groups:
        raise RuntimeError('No declared racecards for '+day+'; refusing to erase A/B research with empty data')
    if not ab.get('models'):
        raise RuntimeError('Stage17 A/B build must run before coverage repair')
    valid=prob.get('state')=='VALIDATED' and prob.get('day')==day
    pindex={race_key(r):r for r in prob.get('races',[])} if valid else {}
    stageindex={race_key(r):r for r in stage.get('races',[]) if r.get('date',day)==day}
    bform=[];valid_A=0;researched_B=0
    for model in ('A','B'):
        previous=ab['models'].get(model,{}).get('races',[])
        index={race_key(r):r for r in previous}
        rebuilt=[]
        for k,field in sorted(groups.items(),key=lambda z:(z[0][0],z[0][2],z[0][1])):
            sample=field[0];older=index.get(k)
            prepared={
                'date':day,'course':sample['course'],'time':clock(sample['race_time']),'race_time':sample['race_time'],
                'race_name':sample.get('race_name'),'event_name':sample.get('race_name'),
                'field_size':len(field),'race_type':cat(sample), 'status':'UPCOMING' if upcoming(sample,now) else 'OFF_OR_FINISHED',
                'model':model,'selection':None,'selection_status':'NO_SELECTION' if upcoming(sample,now) else 'OFF',
                'model_validation':'RESEARCH_NOT_AVAILABLE',
                'decision':{'reason':'NO_VERIFIED_WHOLE_FIELD_RESEARCH','leader':None},
            }
            if older:prepared.update(older)
            src=stageindex.get(k,{})
            if model=='A':
                pf=pindex.get(k,{})
                rr=pf.get('runners') or []
                wanted={norm(x['horse']) for x in field}
                correct=bool(pf.get('complete_field') and len(rr)==len(field) and {norm(x.get('horse')) for x in rr}==wanted
                    and all(isinstance(x.get('probability'),(int,float)) and 0<x['probability']<=1 for x in rr)
                    and abs(sum(x['probability'] for x in rr)-1)<.001)
                prepared['probability_complete']=correct
                prepared['model_a_probability_ranking']=sorted(({'horse':x['horse'],'win_probability':x['probability']} for x in rr),key=lambda x:-x['win_probability']) if correct else []
                prepared['model_validation']='VALIDATED' if correct else 'NOT_VALIDATED'
                if correct:
                    valid_A+=1
                    if not older or not older.get('decision',{}).get('leader'):
                        ranked=sorted(rr,key=lambda x:-x['probability']);prepared['decision']={
                            'reason':'COMPLETE_FIELD_PROBABILITIES_ONLY_NO_VERIFIED_BETTING_EDGE',
                            'leader':ranked[0]['horse'],'second':ranked[1]['horse'] if len(ranked)>1 else None,
                            'gap':round(100*(ranked[0]['probability']-ranked[1]['probability']),2) if len(ranked)>1 else 0,
                            'metric':'VALIDATED_PROBABILITY'}
                if not correct:
                    prepared['selection']=None;prepared['selection_status']='NO_MODEL_PROBABILITIES' if upcoming(sample,now) else 'OFF'
            else:
                ranked=src.get('ranked_runners') or []
                covered=bool(len(ranked)==len(field) and len({norm(x.get('horse')) for x in ranked})==len(field)
                    and {norm(x.get('horse')) for x in ranked}=={norm(x['horse']) for x in field})
                prepared['research_complete']=covered
                if covered:researched_B+=1
                prepared['ranked_runners']=[{'horse':z.get('horse'),'score':z.get('score'),
                    'positive_count':z.get('positive_count'),'negative_count':z.get('negative_count'),
                    'form_runs':z.get('form_runs'),'form_wins':z.get('form_wins'),
                    'historical_rank':z.get('rank')} for z in ranked] if covered else []
                prepared['verdict']=prepared.get('verdict') or ('A complete historical named-race/form comparison was published. The score is a research ranking, not a win probability.' if covered else 'No verified same-event or comparable-course research for the whole field. Model B has not selected a horse; missing evidence is not negative form.')
                if not older:prepared['decision']={'reason':'NO_VERIFIED_COMPARABLE_HISTORICAL_CANDIDATE' if not covered else 'RESEARCH_ONLY_NO_BETTING_SELECTION'}
                bform.append({'date':day,'course':sample['course'],'time':clock(sample['race_time']),'race_time':sample['race_time'],
                    'race_type':cat(sample),'complete_field':covered,'match_basis':prepared.get('match_basis'),
                    'runners':prepared['ranked_runners'],'state':'RESEARCH_SCORE_NOT_CALIBRATED' if covered else 'NO_VERIFIED_B_RESEARCH',
                    'verdict':prepared['verdict']})
            rebuilt.append(prepared)
        ab['models'][model]={'races':rebuilt}
    ab['coverage_repaired_at']=now.isoformat();ab['racecards_races']=len(groups)
    ab['coverage_note']='Full daily race coverage; no bet selection fabricated. A: only validated full-field probabilities. B: historical score, never a win percentage.'
    save(data/'strategy-ab-selections.json',ab)
    save(data/'strategy-b-probabilities.json',{'version':1,'day':day,'generated_at':now.isoformat(),
        'state':'RESEARCH_RANKING_ONLY_NOT_PROBABILITIES','races':bform})
    return {'day':day,'total_races':len(groups),'model_A_validated_races':valid_A,'model_B_verified_research_races':researched_B,
        'model_A_selections':sum(1 for r in ab['models']['A']['races'] if r.get('selection_status')=='SELECTED'),
        'model_B_selections':sum(1 for r in ab['models']['B']['races'] if r.get('selection_status')=='SELECTED')}

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',default='.');a=p.parse_args()
    print('Stage 23 A/B full-field coverage:',json.dumps(augment(a.repo)))
if __name__=='__main__':main()
