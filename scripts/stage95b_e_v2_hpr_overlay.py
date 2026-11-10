#!/usr/bin/env python3
"""Stage95B: research-only all-runner E v2 / E v2 + HPR v2 probability overlay.

Preserve original Stage92 E v2 scores, ranks and probabilities. Read ONLY
immutable artifacts; the separate overlay multiplies baseline probabilities
by a fixed modest HPR log-score adjustment and renormalizes across a complete
matched field. This is a retrospective ablation (NOT independent OOS):
Stage92 E v2 probabilities were calibrated with October 12-23 outcomes.
Never changes live D, D(c), E, HPR v0, or archives. Never uses SP in scoring.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,io,json,math,pathlib,statistics,tempfile,zipfile
from zoneinfo import ZoneInfo

import stage95a_hpr_v2_out_of_sample as A

VERSION='stage95b-e-v2-hpr-overlay-research-v1'
START='2026-10-11';END='2026-10-23'
# Research assumption FIXED, not chosen to maximise ROI/strike rate.
ALPHA=0.50
HPR_TEMPERATURE=40.0  # Prior Stage95A calibration fitted on pre-Oct11, not on target outcomes.
THRESHOLDS=(20,25,30,34,40,42,45,50)
FIELDS=['date','course','time_local','time_utc','source_race_id','discipline','race_type',
        'model','rank','horse','win_probability_pct','finish','won','sp','sp_decimal',
        'price_status','prior_hpr_v2','hpr_form_count','hpr_coverage_pct','sample_status','version']
RUNNER_FIELDS=['date','course','time_local','source_race_id','horse','race_type','discipline',
               'original_e_probability_pct','overlay_probability_pct','hpr_v2_pre_race',
               'hpr_prior_count','winner_verified']


def source_csv(zip_path,member):
    with zipfile.ZipFile(zip_path) as z:
        with z.open(member) as raw:
            if member.endswith('.gz'):
                with gzip.open(raw,'rt',encoding='utf-8-sig',newline='') as f:
                    for row in csv.DictReader(f):yield row
            else:
                with io.TextIOWrapper(raw,encoding='utf-8-sig',newline='') as f:
                    for row in csv.DictReader(f):yield row


def gh_key(date,course,local_clock):
    return (date,A.venue(course),local_clock)


def hpr_races(hpr_zip,quality_zip):
    quality=collections.defaultdict(list)
    for row in source_csv(quality_zip,'stage92-per-run-quality.csv.gz'):
        d=row.get('date','')
        if START<=d<=END:
            k=(d,A.venue(row.get('course')),row.get('off'),A.norm(row.get('horse')))
            quality[k].append(row)
    groups=collections.defaultdict(list)
    for row in source_csv(hpr_zip,'stage94-hpr-v2-research.csv.gz'):
        d=row.get('date','')
        if START<=d<=END:
            k=(d,A.venue(row.get('course')),row.get('off'),A.norm(row.get('horse')))
            src=quality.get(k,[])
            if len(src)!=1 or not row.get('horse_identity') or row['horse_identity']!=src[0]['horse_identity']:
                continue
            if row.get('source_race_id')!=src[0].get('source_race_id'):
                continue
            groups[gh_key(d,row['course'],row['off'])].append((row,src[0]))
    good={}
    for k,entries in groups.items():
        names=[A.norm(r['horse']) for r,s in entries]
        if len(set(names))!=len(names) or len(entries)<3:continue
        rids={r['source_race_id'] for r,s in entries}
        if len(rids)!=1:continue
        declared={s.get('observed_starter_count') for r,s in entries}
        if declared!={str(len(entries))}:continue
        winners=sum(A.position(s.get('position'))==1 for _,s in entries)
        if winners!=1:continue
        good[k]=entries
    return good


def e_races(source_zip):
    groups=collections.defaultdict(list)
    for row in source_csv(source_zip,'output/stage92-race-probabilities.csv'):
        d=str(row.get('date') or '')[:10]
        if not START<=d<=END:continue
        local=A.utc_to_local_clock(d,row.get('time'))
        if not local:continue
        groups[gh_key(d,row.get('course'),local)].append(row)
    return groups


def valid_float(x):
    v=A.num(x)
    return v if v is not None and math.isfinite(v) else None


def overlay(base_probs,hpr_scores,alpha=ALPHA,temp=HPR_TEMPERATURE):
    """Fixed prior correction; neutral when all HPR scores are unavailable.

    p_new is proportional to p_base * exp(alpha * (hpr_i-median)/T).
    Baseline p_i=0 is never artificially made a live contender.
    """
    if temp<=0 or not 0<=alpha<=1:raise ValueError('Invalid overlay parameters')
    if not base_probs or len(base_probs)!=len(hpr_scores):raise ValueError('Mismatched fields')
    if any(p is None or p<0 or not math.isfinite(p) for p in base_probs):raise ValueError('Bad baseline probability')
    total=sum(base_probs)
    if abs(total-1)>0.005:raise ValueError('Original probabilities do not sum to one')
    rated=[h for h in hpr_scores if h is not None]
    median=statistics.median(rated) if rated else 0.
    weights=[p*math.exp(alpha*((h if h is not None else median)-median)/temp)
             for p,h in zip(base_probs,hpr_scores)]
    z=sum(weights)
    if z<=0:raise ValueError('Overlay has no probability mass')
    return [p/z for p in weights]


def race_record(k,hpr,els,prices):
    # Exact entire field: source stable race-id, local off, all runner names and one winner.
    horses={A.norm(r['horse']):(r,s) for r,s in hpr}
    e_names=[A.norm(z['horse']) for z in els]
    if len(set(e_names))!=len(e_names) or set(e_names)!=set(horses):return None,'FIELD_MISMATCH'
    if len(set(z['rank'] for z in els))!=len(els):return None,'NON_UNIQUE_E_RANK'
    if {int(z['rank']) for z in els} != set(range(1,len(els)+1)):return None,'INCOMPLETE_E_RANKING'
    if len({z.get('model') for z in els})!=1:return None,'MULTIPLE_E_VARIANTS'
    if sum(str(z.get('winner_verified'))=='1' for z in els)!=1:return None,'UNKNOWN_E_WINNER'
    probs=[valid_float(z.get('win_probability_pct')) for z in els]
    if any(v is None for v in probs):return None,'MISSING_E_PROBS'
    source_ids={r.get('source_race_id') for r,s in hpr}
    if len(source_ids)!=1 or not next(iter(source_ids)):return None,'UNVERIFIED_RACE_ID'
    rid=next(iter(source_ids))
    hpr_scores=[valid_float(horses[n][0].get('pre_race_hpr_v2')) for n in e_names]
    coverage=sum(v is not None for v in hpr_scores)/len(hpr_scores)
    if coverage<.70:return None,'INSUFFICIENT_HPR_COVERAGE'
    try:
        base=[v/100 for v in probs]
        p_overlay=overlay(base,hpr_scores)
    except ValueError:
        return None,'INVALID_E_PROBS'
    # HPR's neutral filling must not promote an unrated horse into overlay top2.
    ordering=sorted(range(len(els)),key=lambda i:(-p_overlay[i],i))
    if any(hpr_scores[i] is None for i in ordering[:2]):return None,'UNRATED_HORSE_IN_OVERLAY_TOP_TWO'
    for i,z in enumerate(els):
        src=horses[e_names[i]][1]
        if int(z.get('winner_verified') or 0)!=int(A.position(src.get('position'))==1):
            return None,'WINNER_CONFLICT'
    rr=[];sel=[]
    for i,z in enumerate(els):
        source,quality=horses[e_names[i]]
        finish=A.position(quality.get('position'))
        sp,dec,status=A.settle((k[0],k[1],rid,k[2]),z['horse'],finish==1,finish,prices)
        rr.append({'date':k[0],'course':z.get('course'),'time_local':k[2],'source_race_id':rid,
            'horse':z['horse'],'race_type':z.get('race_type'),'discipline':z.get('discipline'),
            'original_e_probability_pct':round(base[i]*100,4),
            'overlay_probability_pct':round(p_overlay[i]*100,4),
            'hpr_v2_pre_race':hpr_scores[i] if hpr_scores[i] is not None else '',
            'hpr_prior_count':source.get('prior_same_code_runs') or 0,
            'winner_verified':int(finish==1),'_sp':sp,'_dec':dec,'_status':status,'_finish':finish})
    for model,order,ps in [
        ('E_V2_ORIGINAL',sorted(range(len(els)),key=lambda i:int(els[i]['rank'])),base),
        ('E_V2_HPR_V2_FIXED_OVERLAY',ordering,p_overlay)]:
        for rank,i in enumerate(order[:2],1):
            row=rr[i]
            sel.append({'date':k[0],'course':row['course'],'time_local':k[2],
                'time_utc':els[i]['time'],'source_race_id':rid,
                'discipline':row['discipline'],'race_type':row['race_type'],
                'model':model,'rank':rank,'horse':row['horse'],
                'win_probability_pct':round(ps[i]*100,4),
                'finish':row['_finish'] if row['_finish'] is not None else 'UNPLACED_OR_UNFINISHED',
                'won':row['winner_verified'],'sp':row['_sp'],'sp_decimal':row['_dec'],
                'price_status':row['_status'],
                'prior_hpr_v2':row['hpr_v2_pre_race'],
                'hpr_form_count':row['hpr_prior_count'],
                'hpr_coverage_pct':round(100*coverage,2),
                'sample_status':'EXPLORATORY_BASELINE_IN_SAMPLE_FOR_E92_CALIBRATION',
                'version':VERSION})
    return (rr,sel),None


def metrics(rows):
    n=len(rows);wins=sum(int(r['won']) for r in rows)
    priced=[r for r in rows if r.get('sp_decimal') is not None]
    pnl=sum(float(r['sp_decimal'])-1 if int(r['won']) else -1. for r in priced)
    pmean=statistics.mean(float(r['win_probability_pct']) for r in rows) if rows else None
    return {'selections':n,'winners':wins,'strike_rate_pct':round(100*wins/n,2) if n else None,
        'priced':len(priced),'unpriced':n-len(priced),'profit_1u':round(pnl,3),
        'sp_roi_pct':round(100*pnl/len(priced),2) if priced else None,
        'mean_predicted_win_pct':round(pmean,2) if pmean is not None else None}


def write_csv(path,rows,columns):
    with open(path,'w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


def run(a):
    out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    summary_e=A.zip_json(a.e92_zip,'output/stage92-summary.json')
    summary_h=A.zip_json(a.hpr_zip,'stage94-summary.json')
    summary_q=A.zip_json(a.quality_zip,'stage92-summary.json')
    summary_95a=A.zip_json(a.stage95a_zip,'stage95a-summary.json')
    if (summary_e.get('stage')!=92 or
        summary_h.get('version')!='hpr-v2-research-2026-11-10' or
        summary_q.get('version')!='stage92-performance-quality-research-v1' or
        summary_95a.get('stage')!='95a'):
        raise RuntimeError('Frozen source version mismatch')
    if summary_95a.get('hpr_temperature')!=HPR_TEMPERATURE:
        raise RuntimeError('HPR temperature has changed from earlier training-only baseline')
    if any(START<=x<=END for x in summary_e.get('holdout_days',[])):
        raise RuntimeError('Unexpected E model training/holdout metadata')
    h=hpr_races(a.hpr_zip,a.quality_zip)
    e=e_races(a.e92_zip)
    prices,pdata=A.sp_map(a.kaggle_source) if a.kaggle_source else ({},{'source_status':'NO_KAGGLE_SETTLEMENT'})
    skipped=collections.Counter();runners=[];selections=[];pairs=0
    for k in sorted(set(h)&set(e)):
        record,status=race_record(k,h[k],e[k],prices)
        if status:
            skipped[status]+=1;continue
        r,s=record
        runners.extend(r);selections.extend(s);pairs+=1
    skipped['no_paired_race_for_E']+=len(set(h)-set(e))
    if pairs<50:
        raise RuntimeError(f'Only {pairs} complete paired E/HPR races; refuse misleading comparison')
    summary=[];thresholds=[];categories=[]
    for model in sorted(set(x['model'] for x in selections)):
        for rank in (1,2,'1+2'):
            cohort=[z for z in selections if z['model']==model and (rank=='1+2' or z['rank']==rank)]
            summary.append({'model':model,'rank':rank,**metrics(cohort)})
            for t in THRESHOLDS:
                subset=[z for z in cohort if z['win_probability_pct']>=t]
                thresholds.append({'model':model,'rank':rank,'min_probability_pct':t,**metrics(subset)})
            for field in ('discipline','race_type'):
                for value in sorted(set(str(x.get(field,'')) for x in cohort)):
                    subsets=[x for x in cohort if str(x.get(field,''))==value]
                    categories.append({'model':model,'rank':rank,'group_by':field,'group':value,**metrics(subsets)})
    # Paired proper scoring rule audit from identical exact-field winner verified.
    scoring={}
    groups=collections.defaultdict(list)
    for r in runners:groups[(r['date'],A.venue(r['course']),r['time_local'])].append(r)
    for label,col in [('E_V2_ORIGINAL','original_e_probability_pct'),
                      ('E_V2_HPR_V2_FIXED_OVERLAY','overlay_probability_pct')]:
        nll=[];brier=[]
        for group in groups.values():
            if sum(z['winner_verified'] for z in group)!=1:raise RuntimeError('Lost winner identity')
            probs=[z[col]/100 for z in group]
            if abs(sum(probs)-1)>.001:raise RuntimeError('Race probabilities do not sum 100')
            nll.append(-math.log(max(1e-12,next(z[col]/100 for z in group if z['winner_verified']))))
            brier.append(sum((z[col]/100-z['winner_verified'])**2 for z in group))
        scoring[label]={'races':len(groups),'mean_log_loss':round(statistics.mean(nll),5),
                        'mean_multiclass_brier':round(statistics.mean(brier),5)}
    write_csv(out/'stage95b-rank1-rank2.csv',selections,FIELDS)
    metric_columns=list(metrics([]))
    write_csv(out/'stage95b-scorecard.csv',summary,['model','rank',*metric_columns])
    write_csv(out/'stage95b-thresholds.csv',thresholds,['model','rank','min_probability_pct',*metric_columns])
    write_csv(out/'stage95b-segments.csv',categories,['model','rank','group_by','group',*metric_columns])
    with gzip.open(out/'stage95b-allrunner.csv.gz','wt',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=RUNNER_FIELDS,extrasaction='ignore');w.writeheader();w.writerows(runners)
    result={
       'stage':'95b','version':VERSION,'status':'READ_ONLY_RESEARCH',
       'race_period':{'start':START,'end':END,'matched_races':pairs,'e92_candidate_races':len(e),
                      'hpr_source_races':len(h),'excluded':dict(skipped)},
       'baseline_e_version':summary_e.get('model'),
       'in_sample_warning': 'E v2 probabilities were calibrated using October 12-23 target outcomes; this is NOT a clean held-out E v2 performance test.',
       'overlay':{'fixed_alpha':ALPHA,'prior_hpr_temperature':HPR_TEMPERATURE,
           'formula':'p_new proportional p_original * exp(alpha*(hpr_past_run_score - median_pre_race_hpr)/temperature)',
           'no_hpr_runners':'neutral median; not penalised',
           'minimum_rated_field_fraction':.70},
       'scorecard':summary,'probability_accuracy':scoring,'price_data':pdata,
       'data_safety':{
          'source_artifacts_pinned':True,'kaggle_source_pinned_v129':bool(a.kaggle_source),
          'predictions_never_use_sp':True,'no_future_race_observations':True,
          'original_models_or_hpr_stores_untouched':True,
          'next_step':'Independent D/Dc/E all-runner replay and prospectively held-out validation.'},
       'limits':['The source E v2 may itself incorporate historical HPR evidence. This is an overlay ablation, not an independent HPR-free vs HPR comparison.',
           'HPR v2 pre-race ratings use strictly earlier completed dates.',
           'No rank tuning or probability threshold selection based on target outcomes.',
           'Rows with ambiguous SP or exact-runner conflicts are excluded from ROI.',
           'This does not implement D/C comment-aware scoring or live deployment.',
           'A clean rolling forward test needs newer comment/margin data than Kaggle v129.']}
    (out/'stage95b-summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'matched_races':pairs,'scorecard':summary,'proper_scores':scoring,'excluded':dict(skipped)},indent=2))
    return result


def self_test():
    p=[.4,.3,.2,.1]
    assert all(abs(x-y)<1e-12 for x,y in zip(overlay(p,[None]*4),p))
    out=overlay(p,[40,20,50,30])
    assert abs(sum(out)-1)<1e-12 and out[0]>p[0] and out[1]<p[1]
    assert overlay([.5,.5],[None,None])==[.5,.5]
    assert metrics([{'won':1,'sp_decimal':3,'win_probability_pct':40},
                    {'won':0,'sp_decimal':4,'win_probability_pct':30}])['sp_roi_pct']==50
    assert metrics([])['sp_roi_pct'] is None
    with tempfile.TemporaryDirectory() as td:
        tmp=pathlib.Path(td)
        # Honest safety gate: missing input files fail, not silently produce zero rows.
        try:
            A.zip_json(tmp/'not-found.zip','anything')
        except FileNotFoundError:pass
        else:raise AssertionError('Missing baseline did not fail')
    print('PASS Stage95B: probability conservation, missing-neutrality, prior-correction, ROI conventions and source gates')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--e92-zip',type=pathlib.Path)
    p.add_argument('--hpr-zip',type=pathlib.Path)
    p.add_argument('--quality-zip',type=pathlib.Path)
    p.add_argument('--stage95a-zip',type=pathlib.Path)
    p.add_argument('--kaggle-source',type=pathlib.Path,help='Pinned v129 CSV directory, only for settlement')
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage95b/output'))
    p.add_argument('--self-test',action='store_true')
    a=p.parse_args()
    if a.self_test:self_test();return
    for attr in ('e92_zip','hpr_zip','quality_zip','stage95a_zip'):
        file=getattr(a,attr)
        if not file or not file.is_file():p.error('--'+attr.replace('_','-')+' must be a real ZIP file')
    if a.kaggle_source and not a.kaggle_source.is_dir():p.error('Kaggle data folder must exist')
    run(a)

if __name__=='__main__':main()
