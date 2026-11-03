#!/usr/bin/env python3
"""Stage 36 Model D — full-field versus genuinely qualifying best-chance races.

- All eligible UK/IE races in 2021-2025 get ONE top-ranked runner prediction.
- A separate evidence-gated subset is chosen using rules learned in 2021-23,
  checked in 2024, and finally audited in 2025. No odds are selection inputs.
- 2018-2020 pre-race signals are used to learn *course x distance x going*
  ground interactions, with hierarchical shrinkage and sample-size fallback.
- The 1988+ archive contributes only earlier-calendar-day evidence to each race.
- Stage 35's frozen discipline/course/course-trip profiles are the starting
  weights; A/B/C/D live data and files are NEVER modified.
- 2024 was previously used in Stage35's profile promotion, and 2025 was viewed
  by earlier research; neither is claimed as a pristine programme-wide holdout.
"""
from __future__ import annotations
import argparse
import collections as C
import csv
import datetime as dt
import inspect
import json
import math
import statistics
import textwrap
from pathlib import Path
import numpy as np
import stage30_gap_aware_abcd as OLD
import stage34_model_d_course_research as ST34
import stage35_model_d_going_5years as ST35

VERSION="stage36-d-all-vs-best-chance-course-trip-going-v1"
EARLY=(2018,2019,2020)
TRAIN=(2021,2022,2023)
VALID=2024
AUDIT=2025
YEARS=EARLY+TRAIN+(VALID,AUDIT)
GATES=(.08,.12,.16,.20,.26,.32,.40)
ALPHAS=(0.,.08,.16,.24,.32)
BETAS=(0.,.45,.9)
EVIDENCE_FILTERS=("all","no_clear_ground_negative","credible_positive_ground","direct_proven_or_neutral")
RACE_FILTERS=("all","small_field_max8","mid_field_9_to_12","large_field_13plus","course_trip_going_supported")
METRICS=tuple(OLD.METRICS)


def enable_years():
    """Reuse unmodified Stage30 feature generation; alter cohort guard only."""
    changes=(('score','if day.year not in (2023,2024,2025):return None',
              'if day.year not in (2018,2019,2020,2021,2022,2023,2024,2025):return None'),
             ('scan','samples={2023:[],2024:[],2025:[]};pending=[]',
              'samples={2018:[],2019:[],2020:[],2021:[],2022:[],2023:[],2024:[],2025:[]};pending=[]'))
    for method, old, new in changes:
        original=getattr(OLD.Past,method)
        src=textwrap.dedent(inspect.getsource(original))
        if src.count(old)!=1:
            raise RuntimeError('Stage30 anchor changed in '+method+'; refusing unverified re-run')
        ns=dict(vars(OLD))
        exec(compile(src.replace(old,new),'stage36_verified_'+method,'exec'),ns)
        setattr(OLD.Past,method,ns[method])


def prof_tuple(p):
    return ST35.profile_as_tuple(p)


def eligible_source(e):
    source=e['source']
    return 1. if source=='DIRECT_3PLUS_RUNS' else (
           .74 if source=='DIRECT_LIMITED_BLEND_PEDIGREE' else
           .68 if source=='DIRECT_LIMITED' else
           .62 if source=='UNRACED_ON_GOING_SIRE_DAM_INFERENCE' else
           .43 if source=='UNRACED_ON_GOING_SIRE_INFERENCE' else
           .34 if source=='UNRACED_ON_GOING_DAM_INFERENCE' else 0.)


def ground_vector(r):
    return np.asarray([float(e.get('inferred_lift') or 0.)*eligible_source(e)
                       for e in r['ground_evidence']],dtype=np.float64)


def keyhier(r):
    d,c,t,g=r['kind'],r['course_key'],r['trip_key'],r['ground_label']
    return ((d,), (d,g),(d,c,g),(d,t,g),(d,c,t,g))


def fit_interactions(early):
    """Historical winner/field association fit *only* before 2021.

    This is not a 2018-2020 price ROI optimization. Every group is shrunk toward
    an upper-level association, with a min 25 complete races for local adoption.
    """
    observations=C.defaultdict(lambda:[0,0.,0.])
    skipped=C.Counter()
    for race in early:
        if not race['ground_label']:
            skipped['going_unknown']+=1
            continue
        a=ground_vector(race)
        if a.size<2 or not np.isfinite(a).all():
            skipped['incomplete_field']+=1
            continue
        # The mean ground advantage possessed by the historical winner over
        # others in that field, measured at the time (strictly pre-race).
        w=int(race['winning_index'])
        diff=float(a[w]-np.mean(np.delete(a,w)))
        diff=max(-2.,min(2.,diff))
        for k in keyhier(race):
            v=observations[k]
            v[0]+=1
            v[1]+=diff
            v[2]+=diff*diff
    return observations,skipped


def interaction_for(r, fitted):
    """Track x exact furlongs x official going, with sparse fallbacks."""
    cur=0.
    scope='neutral'
    n=0
    # Roots -> specific children. Each child shrinks to its parent.
    for i,k in enumerate(keyhier(r)):
        count,sums,_=fitted.get(k,(0,0.,0.))
        cutoff=(50,45,35,35,25)[i]
        if count<cutoff:
            continue
        prior=(100,80,75,75,65)[i]
        cur=(float(sums)+prior*cur)/(count+prior)
        scope=('discipline','discipline_going','course_going',
               'trip_going','course_trip_going')[i]
        n=count
    # Slope .02 -.30 depending on what was actually supported by the archive.
    # A positive association can reinforce going suitability; a negative one
    # can suppress it, but may never invert the ground signal direction.
    mult=max(.65,min(1.35,1+1.4*cur))
    return mult,scope,n


def base_params(r,profiles,prior):
    d,c,t=r['kind'],r['course_key'],r['trip_key']
    for key in (f'trip|{d}|{c}|{t}',f'course|{d}|{c}',f'discipline|{d}'):
        p=profiles.get(key)
        if p and p.get('status')=='2024_PASSED_RESEARCH_CHECKS':
            return key.split('|')[0],prof_tuple(p)
    return 'stage35_original_fallback',prior


def scored(r,params,alpha,beta,interactions):
    """All ranking inputs are historical pre-race, never SP or winner labels."""
    w,mode,_margin=params
    w=ST34.normalize(w).copy()
    if r['kind']=='jumps':w[4]=0.
    else:w[7]=0.
    w/=max(1.e-9,w.sum())
    x=r['x'] if mode=='original_ground' else r['ground_mode_matrices'][mode]
    base=np.asarray(x[:,:8],dtype=np.float64)@w
    g=ground_vector(r)
    ground_mult,backoff,observations=interaction_for(r,interactions)
    # Higher course/distance values modestly reinforce credible suitability,
    # but never make ground inference on nonexistent horse/pedigree evidence.
    course=np.tanh((x[:,3]+x[:,5])/2.)
    synergy=np.clip(1+beta*course,.55,1.45)
    adjusted=base+alpha*ground_mult*synergy*(g-float(np.mean(g)))
    order=np.argsort(-adjusted,kind='stable')
    ix=int(order[0]);other=int(order[1])
    lead=max(0.,float(adjusted[ix]-adjusted[other]))
    ev=r['ground_evidence'][ix]
    return ix,lead,ev,backoff,observations,float(adjusted[ix]),float(adjusted[other])


def evidence_admissible(e,mode):
    if mode=='all':return True
    lift=float(e.get('inferred_lift') or 0.)
    support=eligible_source(e)
    if mode=='no_clear_ground_negative':
        return support==0 or lift>=-.065
    if mode=='credible_positive_ground':
        return support>=.34 and lift>=.04 and bool(e.get('has_credible_positive_ground_evidence'))
    if mode=='direct_proven_or_neutral':
        return (support==0 or (e.get('source')=='DIRECT_3PLUS_RUNS' and lift>=0.))
    raise ValueError('Unknown admissibility filter '+mode)


def predict_list(races,profiles,prior,interaction,alpha,beta):
    records=[]
    for r in races:
        scope,params=base_params(r,profiles,prior)
        ix,lead,e,backoff,n,top,runnerup=scored(r,params,alpha,beta,interaction)
        sp=OLD.number(r['sp'][ix])
        records.append({'race':r,'horse':r['horse'][ix],
                        'winning_index':ix,'won':int(ix==r['winning_index']),
                        'sp':float(sp) if sp is not None and math.isfinite(sp) and 1.01<=sp<=5000 else None,
                        'lead':lead,'ground_source':e['source'],
                        'ground_lift':float(e.get('inferred_lift') or 0.),
                        'ground_has_support':eligible_source(e)>0,
                        'profile_scope':scope,'profile_mode':params[1],
                        'incumbent_gate':float(params[2]),
                        'interaction_source':backoff,'interaction_observations':n,
                        'interaction_multiplier':interaction_for(r,interaction)[0],
                        'field_size':len(r['horse']),
                        'ground_method':params[1],
                        'ground_evidence':e,'top_score':top,'next_score':runnerup})
    return records


def stats(items):
    priced=[x for x in items if x.get('sp') is not None]
    winners=[x for x in priced if x['won']]
    n=len(items);m=len(priced)
    profit=sum(x['sp']*x['won']-1 for x in priced)
    cap=sum(min(x['sp'],21.)*x['won']-1 for x in priced)
    largest=max((x['sp']-1 for x in winners),default=0.)
    weeks=C.Counter((dt.date.fromisoformat(x['race']['date']).isocalendar().year,
                     dt.date.fromisoformat(x['race']['date']).isocalendar().week)
                    for x in items)
    return {'races':n,'wins':sum(x['won'] for x in items),
            'strike_pct':round(100*sum(x['won'] for x in items)/n,3) if n else None,
            'priced_bets':m,'price_coverage_pct':round(100*m/n,3) if n else None,
            'profit_units':round(profit,3),
            'sp_roi_pct':round(100*profit/m,3) if m else None,
            'roi_sp_capped_at_20to1_pct':round(100*cap/m,3) if m else None,
            'roi_excluding_biggest_winner_pct':round(100*(profit-largest)/m,3) if m else None,
            'observed_weeks':len(weeks),'weeks_with_10plus':sum(v>=10 for v in weeks.values()),
            'avg_bets_per_calendar_week':round(n/max(1.,(max((x['race']['year'] for x in items),default=0)-
                                      min((x['race']['year'] for x in items),default=0)+1)*52.14),2)}


def race_admissible(row,mode):
    if mode=='all':return True
    field=row['field_size']
    if mode=='small_field_max8':return field<=8
    if mode=='mid_field_9_to_12':return 9<=field<=12
    if mode=='large_field_13plus':return field>=13
    if mode=='course_trip_going_supported':
        return (row['interaction_source']=='course_trip_going' and
                row['interaction_observations']>=25 and
                row['interaction_multiplier']>1.02)
    raise ValueError('Unknown race-specific selection filter '+mode)


def selected(items,gate,filtering,race_filter='all'):
    return [r for r in items if r['lead']>=gate and
            evidence_admissible(r['ground_evidence'],filtering) and
            race_admissible(r,race_filter)]


def objective(s):
    if s['priced_bets']<1 or s['sp_roi_pct'] is None:return -1.e6
    # Favour both winner-identification and profitability; penalise dependence
    # on very big SP winners and tiny samples.
    return (.36*s['sp_roi_pct'] + .22*s['roi_sp_capped_at_20to1_pct'] +
            .12*s['roi_excluding_biggest_winner_pct'] + .30*s['strike_pct'] -
            30/math.sqrt(s['priced_bets']))


def tune_discipline(kind,train,valid,profiles,prior,interactions):
    """Freeze ONE policy via 2021-23, then report a separate 2024 check.

    The stage35 weights had already been promoted with 2024 information, so this
    is a comparative research check, not an independent nested validation.
    """
    t=[r for r in train if r['kind']==kind]
    v=[r for r in valid if r['kind']==kind]
    if len(t)<750 or len(v)<250:
        raise RuntimeError('Insufficient full racing discipline data for '+kind)
    opts=[]
    best=None
    trial_count=0
    for alpha in ALPHAS:
        for beta in BETAS:
            raw=predict_list(t,profiles,prior,interactions,alpha,beta)
            for gate in GATES:
                for flt in EVIDENCE_FILTERS:
                    for rf in RACE_FILTERS:
                        trial_count+=1
                        sel=selected(raw,gate,flt,rf)
                        m=stats(sel)
                        # At least ~5 bets/week *over three years*. Prefer 10/week
                        # but never force the quota by lowering a qualification bar.
                        if m['priced_bets']<260 or (m['price_coverage_pct'] or 0)<90:
                            continue
                        byyear=[]
                        for year in TRAIN:
                            yearm=stats([z for z in sel if z['race']['year']==year])
                            byyear.append(yearm)
                        if any(z['priced_bets']<45 for z in byyear):
                            continue
                        score=objective(m)
                        # Rolling-year stability discourages a one-year lucky fluke.
                        if sum((z['roi_sp_capped_at_20to1_pct'] or -100)<-40 for z in byyear)>1:
                            score-=16.
                        score+=min(2.,m['avg_bets_per_calendar_week']/10.)
                        if best is None or score>best['score']:
                            best={'alpha':alpha,'beta':beta,'gate':gate,'filter':flt,'race_filter':rf,
                                  'train':m,'train_by_year':{str(y):z for y,z in zip(TRAIN,byyear)},
                                  'score':score}
    incumbent_t=predict_list(t,profiles,prior,interactions,0.,0.)
    incumbent_v=predict_list(v,profiles,prior,interactions,0.,0.)
    baseline_train=stats([x for x in incumbent_t if x['lead']>=x['incumbent_gate']])
    baseline_valid=stats([x for x in incumbent_v if x['lead']>=x['incumbent_gate']])
    profile={'discipline':kind,'training_races':len(t),'validation_races':len(v),
             'trials':trial_count,'baseline_training_selected':baseline_train,
             'baseline_2024_selected':baseline_valid,'status':'TRAINING_NO_STABLE_CHALLENGER'}
    if best is None:
        profile['selected_params']={'alpha':0.,'beta':0.,'gate':None,'filter':'incumbent_per_race','race_filter':'all'}
        return profile
    valid_raw=predict_list(v,profiles,prior,interactions,best['alpha'],best['beta'])
    validate=stats(selected(valid_raw,best['gate'],best['filter'],best['race_filter']))
    profile.update({'candidate_training':best['train'],
                    'candidate_training_by_year':best['train_by_year'],
                    'candidate_2024':validate,
                    'candidate_params':{k:best[k] for k in ('alpha','beta','gate','filter','race_filter')}})
    # No tuning on 2025; validation is a yes/no guard, never a search of 2024
    # alternative parameters. Do not require positive ROI: simply report loss.
    improved=(validate['priced_bets']>=60 and
              (validate['price_coverage_pct'] or 0)>=90 and
              validate['sp_roi_pct'] is not None and
              baseline_valid['sp_roi_pct'] is not None and
              validate['sp_roi_pct']>=baseline_valid['sp_roi_pct']+2. and
              validate['roi_sp_capped_at_20to1_pct']>=baseline_valid['roi_sp_capped_at_20to1_pct']-3. and
              validate['roi_excluding_biggest_winner_pct']>=baseline_valid['roi_excluding_biggest_winner_pct']-3. and
              (validate['strike_pct'] or 0)>=(baseline_valid['strike_pct'] or 0)-2. and
              validate['priced_bets']>=.35*baseline_valid['priced_bets'])
    profile['status']='2024_PASSED_COMPARATIVE_CHECK' if improved else '2024_REJECTED_KEEP_FROZEN_STAGE35'
    profile['selected_params']=(profile['candidate_params'] if improved else
                                {'alpha':0.,'beta':0.,'gate':None,'filter':'incumbent_per_race','race_filter':'all'})
    return profile


def make_records(races,configs,profiles,prior,interactions,show_full):
    by_disc=C.defaultdict(list)
    for r in races:by_disc[r['kind']].append(r)
    final=[]
    for kind in ('flat','aw','jumps'):
        cfg=configs[kind]['selected_params']
        alpha,beta=cfg['alpha'],cfg['beta']
        batch=predict_list(by_disc[kind],profiles,prior,interactions,alpha,beta)
        for row in batch:
            row['best_chance']=(row['lead']>=row['incumbent_gate'] if
                      cfg['filter']=='incumbent_per_race' else
                      row['lead']>=cfg['gate'] and evidence_admissible(row['ground_evidence'],cfg['filter'])
                      and race_admissible(row,cfg['race_filter']))
            row['shortlist_reason']=('validated_best_chance' if cfg['filter']!='incumbent_per_race' else
                                     'stage35_conservative_fallback')
        final.extend(batch)
    final.sort(key=lambda x:(x['race']['date'],str(x['race']['race_id'])))
    return final


def row_for_csv(r):
    e=r['ground_evidence']
    return {'date':r['race']['date'],'race_id':r['race']['race_id'],
            'discipline':r['race']['kind'],'course':r['race']['course_key'],
            'furlongs':r['race']['trip_key'],'going':r['race']['ground_label'] or 'UNKNOWN',
            'horse':r['horse'],'won':r['won'],'sp_decimal':r['sp'],
            'score_lead':round(r['lead'],6),'model_score':round(r['top_score'],6),
            'next_score':round(r['next_score'],6),
            'selected_best_chance':r['best_chance'],
            'selection_rule':r['shortlist_reason'],'scope':r['profile_scope'],
            'field_size':r['field_size'],
            'ground_method':r['ground_method'],'ground_source':r['ground_source'],
            'horse_ground_runs':e.get('horse_runs_on_going',0),
            'horse_ground_wins':e.get('horse_wins_on_going',0),
            'sire_progeny_runs_ground':e.get('sire_progeny_runs_on_going',0),
            'dam_progeny_runs_ground':e.get('dam_progeny_runs_on_going',0),
            'ground_suitability_lift':round(r['ground_lift'],6),
            'interaction_scope':r['interaction_source'],
            'interaction_historical_races':r['interaction_observations'],
            'course_trip_going_signal_multiplier':round(r['interaction_multiplier'],6)}


def write_csv(p,items):
    if not items:
        return
    with Path(p).open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(items[0]))
        writer.writeheader();writer.writerows(items)


def evaluate(samples,prior35,stage30_d,outdir):
    out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
    profiles=prior35['profiles']
    original=(ST34.normalize([stage30_d['weights_pct'][m] for m in METRICS]),
              'original_ground',float(stage30_d['selection_margin']))
    early=[r for y in EARLY for r in samples[y]]
    discovery=[r for y in TRAIN for r in samples[y]]
    valid=samples[VALID];audit=samples[AUDIT]
    if any(len(samples[y])<500 for y in YEARS):
        raise RuntimeError('Insufficient historical years: '+str({y:len(samples[y]) for y in YEARS}))
    associations,skipped=fit_interactions(early)
    configs={d:tune_discipline(d,discovery,valid,profiles,original,associations)
             for d in ('flat','aw','jumps')}
    for d,c in configs.items():
        print('Research policy',d,c['status'],c['selected_params'],flush=True)
    chosen=make_records(audit,configs,profiles,original,associations,True)
    # Stage35 comparator - frozen course/distance weightings with its per-race
    # score lead. Stage30 historical original is a second independent baseline.
    stage35=predict_list(audit,profiles,original,associations,0.,0.)
    all_original=[]
    for r in audit:
        ix,lead,e,backoff,n,ts,ns=scored(r,original,0.,0.,associations)
        sp=OLD.number(r['sp'][ix])
        all_original.append({'race':r,'horse':r['horse'][ix],
                             'won':int(ix==r['winning_index']),
                             'sp':float(sp) if sp is not None and math.isfinite(sp) and 1.01<=sp<=5000 else None,
                             'lead':lead,'incumbent_gate':original[2],
                             'ground_evidence':e,'ground_source':e['source']})
    sel=[r for r in chosen if r['best_chance']]
    old_selected=[r for r in all_original if r['lead']>=r['incumbent_gate']]
    stage35_selected=[r for r in stage35 if r['lead']>=r['incumbent_gate']]
    result={'version':VERSION,'status':'RESEARCH_ONLY_NOT_LIVE','betfair_required':False,
            'archive_form_history':'earliest_pre_race_date_available',
            'interaction_fitting_years':list(EARLY),
            'best_chance_rule_fitting_years':list(TRAIN),
            'comparative_validation_year':VALID,'audit_year':AUDIT,
            'races_by_year':{str(y):len(samples[y]) for y in YEARS},
            'course_x_distance_x_going_associations':len(associations),
            'association_skips':dict(skipped),
            'all_races_2025':len(audit),
            '2025':{'original_D_all':stats(all_original),
                    'original_D_selected':stats(old_selected),
                    'stage35_all':stats(stage35),
                    'stage35_selected':stats(stage35_selected),
                    'stage36_all':stats(chosen),
                    'stage36_best_chance':stats(sel)},
            'profile_selection':configs,
            'by_discipline_2025':{},'by_ground_source_2025':{},'by_going_2025':{},
            'by_course_2025':{},'by_course_distance_2025':{},
            'by_month_2025':{},'by_field_size_2025':{},'2021_2025_yearly_full_vs_best_chance':{},
            'caveats':['A top-ranked horse is assessed in every eligible race. Selected races are a DISTINCT evidence-gated subset, not the all-races denominator.',
                       'The observed win rate of all-race picks is NOT their estimated win probability; raw score leads are not calibrated probabilities.',
                       'Stage35 weights used 2024 to promote profiles; therefore 2024 here is a comparative check, not fully independent nested validation.',
                       '2025 was already viewed in prior Model D research. Real forward 2026 selections are required to verify profitability.',
                       'No market odds are selection features. SP is used only after the historical race for one-unit hypothetical returns.',
                       'Interaction effects use strictly pre-2021 race records, with course-trip-going fallback and shrinkage; missing evidence is neutral.',
                       'No enforced 10-per-week selection quota: no bet is preferred to an unsupported selection.',
                       'No live Models A, B, C or D are updated by this research run.']}
    for d in ('flat','aw','jumps'):
        group=[x for x in chosen if x['race']['kind']==d]
        old=[x for x in all_original if x['race']['kind']==d]
        old35=[x for x in stage35 if x['race']['kind']==d]
        result['by_discipline_2025'][d]={
            'original_all':stats(old),'original_selected':stats([x for x in old if x['lead']>=x['incumbent_gate']]),
            'stage35_all':stats(old35),'stage35_selected':stats([x for x in old35 if x['lead']>=x['incumbent_gate']]),
            'stage36_all':stats(group),'stage36_best_chance':stats([x for x in group if x['best_chance']])}
    for source in sorted({x['ground_source'] for x in chosen}):
        group=[x for x in chosen if x['ground_source']==source]
        result['by_ground_source_2025'][source]={
            'all':stats(group),'best_chance':stats([x for x in group if x['best_chance']])}
    for kind,going in sorted({(x['race']['kind'],x['race']['ground_label'] or 'UNKNOWN') for x in chosen}):
        a=[x for x in chosen if (x['race']['kind'],x['race']['ground_label'] or 'UNKNOWN')==(kind,going)]
        result['by_going_2025'][f'{kind}|{going}']={'all':stats(a),'best_chance':stats([x for x in a if x['best_chance']])}
    course_trip_ground=[]
    for key in sorted({(x['race']['kind'],x['race']['course_key'],x['race']['trip_key'],
                         x['race']['ground_label'] or 'UNKNOWN') for x in chosen}):
        subset=[x for x in chosen if (x['race']['kind'],x['race']['course_key'],x['race']['trip_key'],
                                      x['race']['ground_label'] or 'UNKNOWN')==key]
        a,b=stats(subset),stats([x for x in subset if x['best_chance']])
        course_trip_ground.append({'discipline':key[0],'course':key[1],'distance':key[2],
               'going':key[3], 'all_races':a['races'],'all_winners':a['wins'],
               'all_strike_pct':a['strike_pct'],'all_sp_roi_pct':a['sp_roi_pct'],
               'best_chance_selections':b['races'],'best_chance_winners':b['wins'],
               'best_chance_strike_pct':b['strike_pct'],
               'best_chance_sp_roi_pct':b['sp_roi_pct']})
    write_csv(out/'stage36-course-distance-going-2025.csv',course_trip_ground)
    for k in sorted({(x['race']['kind'],x['race']['course_key']) for x in chosen}):
        a=[x for x in chosen if (x['race']['kind'],x['race']['course_key'])==k]
        result['by_course_2025']['|'.join(k)]={'all':stats(a),'best_chance':stats([x for x in a if x['best_chance']])}
    for k in sorted({(x['race']['kind'],x['race']['course_key'],x['race']['trip_key']) for x in chosen}):
        a=[x for x in chosen if (x['race']['kind'],x['race']['course_key'],x['race']['trip_key'])==k]
        result['by_course_distance_2025']['|'.join(k)]={'all':stats(a),'best_chance':stats([x for x in a if x['best_chance']])}
    for band,lower,upper in [('2_to_8',2,8),('9_to_12',9,12),('13plus',13,1000)]:
        a=[x for x in chosen if lower<=x['field_size']<=upper]
        result['by_field_size_2025'][band]={'all':stats(a),'best_chance':stats([x for x in a if x['best_chance']])}
    for month in range(1,13):
        a=[x for x in chosen if int(str(x['race']['date'])[5:7])==month]
        result['by_month_2025'][str(month)]={'all':stats(a),'best_chance':stats([x for x in a if x['best_chance']])}
    yearly=[]
    for year in TRAIN+(VALID,AUDIT):
        if year==AUDIT:
            rr=chosen
            o=all_original
            ss=stage35
        else:
            year_races=samples[year]
            rr=make_records(year_races,configs,profiles,original,associations,True)
            ss=predict_list(year_races,profiles,original,associations,0.,0.)
            o=[]
            for race in year_races:
                idx,lead,e,_,_,_,_=scored(race,original,0.,0.,associations)
                sp=OLD.number(race['sp'][idx])
                o.append({'race':race,'won':int(idx==race['winning_index']),
                          'sp':float(sp) if sp is not None and math.isfinite(sp) and 1.01<=sp<=5000 else None,
                          'lead':lead,'incumbent_gate':original[2]})
        record={'year':year,'cohort_type':'training_in_sample' if year in TRAIN else
                '2024_comparative_validation_profiles_selected_using_2024' if year==VALID else
                '2025_historical_audit_previously_viewed'}
        for label,items in (('original_all',o),
                            ('original_selected',[x for x in o if x['lead']>=x['incumbent_gate']]),
                            ('stage35_all',ss),
                            ('stage35_selected',[x for x in ss if x['lead']>=x['incumbent_gate']]),
                            ('stage36_all',rr),
                            ('stage36_best_chance',[x for x in rr if x['best_chance']])):
            record[label]=stats(items)
            yearly.append({'year':year,'data_status':record['cohort_type'],
                           'policy':label,**record[label]})
        result['2021_2025_yearly_full_vs_best_chance'][str(year)]=record
    write_csv(out/'stage36-2021-2025-yearly-comparison.csv',yearly)
    (out/'stage36-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    write_csv(out/'stage36-2025-all-races.csv',[row_for_csv(x) for x in chosen])
    write_csv(out/'stage36-2025-best-chance.csv',[row_for_csv(x) for x in sel])
    compare=[]
    for d in ('flat','aw','jumps'):
        for model,category in (('Original D','original_all'),('Original D','original_selected'),
                               ('Stage 35','stage35_all'),('Stage 35','stage35_selected'),
                               ('Stage 36','stage36_all'),('Stage 36','stage36_best_chance')):
            q=result['by_discipline_2025'][d][category]
            compare.append({'discipline':d,'model':model,'cohort':category,**q})
    write_csv(out/'stage36-2025-comparison.csv',compare)
    rows=[]
    for k,v in sorted(associations.items(),key=lambda kv:(len(kv[0]),str(kv[0]))):
        rows.append({'scope':len(k),'key':'|'.join(str(z or 'UNKNOWN') for z in k),
                     'early_races':v[0],'winner_minus_field_ground_signal':round(v[1]/v[0],6) if v[0] else None})
    write_csv(out/'stage36-pre2021-track-trip-ground-signals.csv',rows)
    (out/'stage36-frozen-best-chance-rules.json').write_text(json.dumps({
        'version':VERSION,'status':'RESEARCH_ONLY_NOT_LIVE',
        'year_rules_frozen_before':AUDIT,
        'fitted_race_interactions_pre2021':True,
        'profile_selection':configs},indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print('Full versus best-chance summary:',json.dumps(result['2025'],indent=2),flush=True)
    return result


def self_test():
    enable_years()
    assert len(GATES)>=5 and len(ALPHAS)>=4 and len(BETAS)>=3
    assert len(EVIDENCE_FILTERS)>=4 and len(RACE_FILTERS)>=5
    assert eligible_source({'source':'NO_SUPPORTED_GOING_EVIDENCE'})==0
    assert eligible_source({'source':'DIRECT_3PLUS_RUNS'})==1
    assert evidence_admissible({'source':'NO_SUPPORTED_GOING_EVIDENCE','inferred_lift':0},'no_clear_ground_negative')
    assert not evidence_admissible({'source':'DIRECT_3PLUS_RUNS','inferred_lift':-.4},'no_clear_ground_negative')
    rec={'kind':'flat','course_key':'chester','trip_key':'5f','ground_label':'good_to_soft',
         'ground_evidence':[{'inferred_lift':.25,'source':'DIRECT_3PLUS_RUNS'},
                            {'inferred_lift':-.1,'source':'NO_SUPPORTED_GOING_EVIDENCE'}],
         'winning_index':0}
    fit,skipped=fit_interactions([rec]*90)
    m,k,n=interaction_for(rec,fit)
    assert .65<=m<=1.35 and k=='course_trip_going' and n==90
    assert len(keyhier(rec))==5
    assert race_admissible({'field_size':6},'small_field_max8')
    assert not race_admissible({'field_size':12},'small_field_max8')
    tiny={'date':'2025-01-01','year':2025,'kind':'flat'}
    x={'race':tiny,'won':1,'sp':3.0}
    met=stats([x])
    assert met['profit_units']==2 and met['strike_pct']==100 and met['sp_roi_pct']==200
    print('Stage36 self-tests passed: pre-2021 interactions, evidenced ground, all vs selected metrics')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',help='Stage2 verified racing historical SQLite')
    p.add_argument('--stage30-report',default='reports/stage30/stage30-summary.json')
    p.add_argument('--stage35-profiles',default='reports/stage35/stage35-frozen-profiles.json')
    p.add_argument('--output',default='stage36_report')
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:
        self_test();return
    if not a.db:p.error('--db required')
    stage30=json.loads(Path(a.stage30_report).read_text(encoding='utf-8'))
    stage35=json.loads(Path(a.stage35_profiles).read_text(encoding='utf-8'))
    if stage30.get('status')!='RESEARCH_ONLY' or stage30.get('model_results',{}).get('D',{}).get('chosen_combination')!='D-064-gap14':
        raise RuntimeError('Unexpected original D comparator; stopping')
    if stage35.get('version')!='stage35-d-five-year-ground-pedigree-v1' or stage35.get('status')!='NOT_LIVE':
        raise RuntimeError('Unexpected Stage35 frozen weights; stopping')
    enable_years()
    past=ST35.GroundPast()
    samples,audit=past.scan(a.db)
    result=evaluate(samples,stage35,stage30['model_results']['D'],a.output)
    result['source_audit']=audit
    (Path(a.output)/'stage36-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')

if __name__=='__main__':main()
