"""Explain pre-off frozen ranking inputs without changing predictions or using results/odds.

A signed group measure is NOT a causal explanation of the underlying historical process.
For tree learners it is a controlled field-median substitution sensitivity; groups
can interact and measures are not additive. For linear logistic models it is the
exact additive log-odds contribution relative to the field mean. For eight-pillar
linear scores it is the exact weighted contribution relative to the field mean.
"""
from __future__ import annotations
import math
import re
import warnings
import numpy as np

PILLARS=('Ability','Trainer','Jockey','Course','Draw','Distance','Ground','Jumping')
GROUPS={
 'ability and HPR':('Ability',), 'trainer history':('Trainer',),
 'jockey history':('Jockey',), 'course record':('Course',),
 'draw record':('Draw',), 'distance record':('Distance',),
 'ground record':('Ground',), 'jumping record':('Jumping',),
 'eight-pillar historical rating':('frozen_d_score',),
 'recent form and consistency':('horse_form_quality3','horse_form_trend','horse_runs','last_run_days','same_code_form3','form_quality_confident'),
 'beaten distances and race comments':('horse_beaten3','horse_beaten_per_mile3','horse_comment_phrase3','horse_comment_nlp3','horse_nlp_coverage','horse_jumping_comment3','horse_interference3'),
 'past race class and opposition':('past_grade_strength3','past_known_tier_fraction','past_opposition_median3','past_vs_current_tier_delta','prior_rivals_nextout_quality3','prior_rivals_nextout_winrate3','prior_rivals_nextout_count3','prior_rivals_nextout_coverage3'),
 'official rating and carried weight':('official_rating','weight_lbs','relative_official_rating','relative_weight_lbs'),
 'race experience and conditions':('current_tier_strength','current_tier_known','current_opposition_median','current_opposition_known','current_race_experience_stage','current_handicap','current_gr1','current_gr2','current_gr3','field_size','maiden_to_handicap','novice_to_handicap','beginner_to_handicap','last3_maiden_fraction','last3_novice_fraction','nlp_model_trained_comments'),
}


def group_for_name(name: str):
    n=str(name or '').strip(); lower=n.casefold()
    for label,features in GROUPS.items():
        if n in features: return label
    if lower in {p.casefold() for p in PILLARS}:return lower+' history'
    # The handicap variants append Stage141's weight and official-mark columns.
    if lower.startswith('weight_') or lower in {
        'or_weight_equiv_vs_field', 'or_weight_equiv_vs_best', 'or_weight_equiv_vs_worst',
        'or_weight_equiv_mean_known', 'or_relative_weight_minus_rating_diff',
    }:return 'official rating and carried weight'
    if lower.startswith('or_'):return 'handicap mark and ratings history'
    # G5 and historical-trend fusion input names differ from the frozen 50-feature model.
    if any(x in lower for x in ('rival','nextout','network','graph','opposition')):return 'quality of previous rivals'
    if any(x in lower for x in ('trainer','stable')):return 'trainer history'
    if any(x in lower for x in ('jockey','rider')):return 'jockey history'
    if any(x in lower for x in ('course','track')):return 'course record'
    if 'draw' in lower or 'stall' in lower:return 'draw record'
    if any(x in lower for x in ('distance','furlong','trip')):return 'distance record'
    if any(x in lower for x in ('ground','going')):return 'ground record'
    if any(x in lower for x in ('jump','hurdle','chase')):return 'jumping record'
    if any(x in lower for x in ('official','rating','weight','handicap','or_','_or')):return 'official rating and carried weight'
    if any(x in lower for x in ('trend','form','run','finish','beaten','recent','comment')):return 'recent form and consistency'
    if any(x in lower for x in ('hpr','ability','speed','class','grade','tier','strength')):return 'ability and race-class history'
    if any(x in lower for x in ('experience','debut','sire','dam','breed','race_type')):return 'experience and breeding'
    return 'other model inputs'


def _feature_groups(names):
    result={}
    for i,name in enumerate(names):result.setdefault(group_for_name(name),[]).append(i)
    # Opaque miscellaneous variables do not warrant a specific causal story.
    result.pop('other model inputs',None)
    return result


def _pack(deltas,method,units,limit=4):
    good=sorted(((label,float(value)) for label,value in deltas.items()
                 if math.isfinite(float(value)) and float(value)>1e-5),key=lambda z:-z[1])[:limit]
    bad=sorted(((label,float(value)) for label,value in deltas.items()
                if math.isfinite(float(value)) and float(value)<-1e-5),key=lambda z:z[1])[:2]
    return {'method':method,'units':units,'positive':[[name,round(val,5)] for name,val in good],
            'negative':[[name,round(val,5)] for name,val in bad],
            'all_groups_tested':len(deltas)}


def forest_sensitivity(x,raw_scores,estimator,names):
    """Frozen-estimator sensitivity to replacing one group with field median.

    This is a local counterfactual sensitivity, not feature SHAP, not a
    decomposition of the score, and it must not be called a causal attribution.
    """
    x=np.asarray(x,dtype=float)
    scores=np.asarray(raw_scores,dtype=float)
    if x.ndim!=2 or len(scores)!=len(x) or len(names)!=x.shape[1]:
        raise ValueError('Stage159 sensitivity requires complete aligned pre-off fields')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        med=np.nanmedian(x,axis=0)
    deltas=[{} for _ in range(len(x))]
    # Compare leaders to the runner-up and other runners to the horse just ahead.
    order=np.argsort(-scores,kind='stable')
    comparison=np.empty(len(x),dtype=int)
    if len(order)>1:
        comparison[order[0]]=order[1]
        comparison[order[1:]]=order[:-1]
    else:
        comparison[:]=order
    for label,ids in _feature_groups(names).items():
        valid=[i for i in ids if math.isfinite(float(med[i]))]
        if not valid:continue
        changed=x.copy();changed[:,valid]=med[valid]
        alternate=estimator.predict_proba(changed)[:,1]
        if len(alternate)!=len(scores) or not np.all(np.isfinite(alternate)):
            raise RuntimeError('Model sensitivity returned invalid outputs')
        for i in range(len(x)):
            deltas[i][label]=scores[i]-alternate[i]
    output=[]
    for i,d in enumerate(deltas):
        item=_pack(d,'tree_median_sensitivity','raw model score')
        if len(order)>1:
            rival=comparison[i]
            margin={label:delta-deltas[rival][label] for label,delta in d.items()}
            item['rank_comparison']=_pack(margin,'tree_median_sensitivity','raw model score gap')
        output.append(item)
    return output


def logistic_contributions(x,scaler,estimator,names):
    """Exact additive logistic contributions to log-odds vs race-field mean."""
    arr=np.asarray(x,dtype=float)
    if arr.ndim!=2 or arr.shape[1]!=len(names):raise ValueError('Unaligned logistic feature matrix')
    if not np.all(np.isfinite(arr)):raise ValueError('Nonfinite logistic input')
    coef=np.asarray(estimator.coef_[0],dtype=float)
    scale=np.asarray(scaler.scale_,dtype=float)
    if len(coef)!=len(names) or len(scale)!=len(names):raise ValueError('Logistic coefficient count mismatch')
    contribution=(arr-np.mean(arr,axis=0))*(coef/scale)
    groups=_feature_groups(names)
    return [_pack({label:float(np.sum(contribution[i,ids])) for label,ids in groups.items()},
                  'exact_logistic_logodds','log-odds') for i in range(len(arr))]


def weighted_pillar_contributions(pillars,weights,names=PILLARS):
    """Exact additive weighted eight-pillar score contributions vs field mean."""
    x=np.asarray(pillars,dtype=float);w=np.asarray(weights,dtype=float)
    if x.ndim!=2 or x.shape[1]!=8 or w.shape!=(8,) or not np.all(np.isfinite(x)):
        raise ValueError('Pillar contribution input invalid')
    parts=(x-np.mean(x,axis=0))*w
    return [_pack({str(names[k]).lower()+' pillar':float(parts[i,k]) for k in range(8)},
                  'exact_eight_pillar_additive','weighted score') for i in range(len(x))]



def _safe(method, fn, n):
    try:
        return fn()
    except Exception as exc:
        import sys
        print('STAGE159_EXPLANATION_WITHHELD',method,type(exc).__name__,str(exc)[:250],file=sys.stderr,flush=True)
        return [{'method':'unattributed_fallback','units':'none','positive':[],
                 'negative':[],'all_groups_tested':0} for _ in range(n)]


def safe_forest(x,raw_scores,estimator,names):
    return _safe('nonlinear_group_sensitivity',lambda:forest_sensitivity(x,raw_scores,estimator,names),len(x))


def safe_logistic(x,scaler,estimator,names):
    return _safe('exact_logistic',lambda:logistic_contributions(x,scaler,estimator,names),len(x))


def safe_pillars(x,weights):
    return _safe('exact_weighted_eight_pillars',lambda:weighted_pillar_contributions(x,weights),len(x))


def self_test():
    class Stub:
        def predict_proba(self,x):
            a=np.asarray(x,dtype=float)[:,0];p=1/(1+np.exp(-a))
            return np.column_stack([1-p,p])
    x=np.array([[2.,.1],[1.,.2],[0.,.3]])
    p=Stub().predict_proba(x)[:,1]
    out=forest_sensitivity(x,p,Stub(),['Ability','Trainer'])
    assert out[0]['positive'][0][0]=='ability and HPR' and out[0]['method']=='tree_median_sensitivity'
    assert out[0]['rank_comparison']['positive'][0][0]=='ability and HPR'
    assert out[2]['rank_comparison']['negative'][0][0]=='ability and HPR'
    handicap=safe_forest(x,p,Stub(),['weight_today_rel_field','or_last_handicap_win_mark'])
    assert handicap[0]['positive'][0][0]=='official rating and carried weight'
    assert group_for_name('or_last_handicap_win_mark')=='handicap mark and ratings history'
    class L:coef_=np.array([[2.,-1.]])
    class Scaler:scale_=np.array([1.,2.])
    lin=logistic_contributions(x,Scaler(),L(),['Ability','Trainer'])
    assert lin[0]['method']=='exact_logistic_logodds' and lin[0]['positive'][0][0]=='ability and HPR'
    weighted=weighted_pillar_contributions(np.stack([np.ones(8),np.zeros(8)]),np.ones(8)/8)
    assert weighted[0]['method']=='exact_eight_pillar_additive'
    assert np.allclose(np.sum((np.stack([np.ones(8),np.zeros(8)])-0.5)*np.ones(8)/8,axis=1),[.5,-.5])
    return True

if __name__=='__main__':
    self_test()
    print('Stage159 explainability synthetic checks passed')
