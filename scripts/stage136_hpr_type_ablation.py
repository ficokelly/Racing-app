#!/usr/bin/env python3
"""Stage136 HPR: strict chronological full-field Stage134C score ablation.

Reconstructs identical source, eligibility and original v0 ratings, but adds a
VERY small, predeclared race-type prior to Stage134C v3.1 per-race scores.
Compare by identical race IDs against IMMUTABLE Stage134C completed predictions.
No target-race SP, target result or future records used in scoring. Research only.
"""
from __future__ import annotations
import argparse,collections,csv,json,pathlib
import stage134c_full_field_hpr_replay as B
import stage136_race_type_strength as T
import stage131_class_weight_hpr as S
VERSION='stage136-hpr-type-prior-chronological-oos-v1'

def type_score(v31_score,record):
    evidence=T.parse_experience({'race_name':record.get('race_type'),'race_type':record.get('race_type'),
                                 'surface':record.get('surface')})
    # An elite Grade/Group/Listed race remains elite regardless of novice status.
    # Never award an extra race-strength premium just because it is a novice.
    title=str(record.get('race_type') or '')
    import re
    points=(0. if re.search(r'\b(?:grade|group|listed)\s*(?:[1-3]|i{1,3})?\b',title,re.I)
            else -1.5 if evidence['is_seller'] or evidence['is_claimer']
            else evidence['experimental_type_prior_hpr'])
    return round(S.clamp(v31_score+points,0.,100.),2),evidence['race_experience_type']

def summary(frame, prefix):
    import pandas as pd
    result=[]
    for (p,disc,kind),block in frame.groupby(['period','discipline','type'],dropna=False):
        out={'period':p,'discipline':disc,'race_type':kind,'races':len(block)}
        for label,winner,price in [('v0','old_win','old_sp'),('v31','base_new_win','base_new_sp'),
                                   ('type136','type_new_win','type_new_sp'),
                                   ('v31_today','base_today_win','base_today_sp'),
                                   ('type136_today','type_today_win','type_today_sp')]:
            x=pd.to_numeric(block[price],errors='coerce');ok=x.notna()&(x>1)
            w=pd.to_numeric(block[winner],errors='coerce')
            out[label+'_wins']=int(w.sum());out[label+'_strike_pct']=round(100*w.mean(),2)
            # All ROI methods share exactly the same SP settlement subset.
            settled=block['common_sp_settled'].astype(bool)&ok
            ret=(x[settled]*(w[settled]==1)).sum()
            out[label+'_sp_roi_pct']=round(100*(ret/settled.sum()-1),2) if settled.sum() else None
        out['common_sp_count']=int(block.common_sp_settled.sum())
        out['type_changes_vs_v31']=int((block.new_horse!=block.base_new_horse).sum())
        result.append(out)
    return result

def run(history,reference,base,out,history_start='2022-01-01',start='2024-01-01',end='2025-12-31'):
    import pandas as pd
    base=pathlib.Path(base);out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    src=base/'stage134-hpr-paired-races.csv'
    if not src.is_file():raise FileNotFoundError('Stage134C baseline CSV missing: '+str(src))
    original=B.v31
    categories=collections.Counter()
    def revised(h0,record,points,field_or,field_weight):
        a=original(h0,record,points,field_or,field_weight)
        b,code=type_score(a,record)
        categories[code]+=1
        return b
    tmp=out/'candidate';tmp.mkdir(exist_ok=True)
    try:
        B.v31=revised
        audit=B.replay(history,reference,tmp,history_start,start,end)
    finally:B.v31=original
    candidate=tmp/'stage134-hpr-paired-races.csv'
    if audit['eligible_races']<1000 or not candidate.is_file():
        raise RuntimeError('Insufficient full-field historical replay; no performance comparison')
    old=pd.read_csv(src,low_memory=False);new=pd.read_csv(candidate,low_memory=False)
    cols=['date','race_id','discipline','type','country','old_horse','old_win','old_sp',
          'new_horse','new_win','new_sp','target_horse','target_win','target_sp']
    for p in (old,new):
        missing=set(cols)-set(p.columns)
        if missing:raise RuntimeError('Missing paired fields '+str(missing))
        if p.duplicated(['date','race_id']).any():raise RuntimeError('Duplicate historical race identity')
    b=old[cols].rename(columns={'new_horse':'base_new_horse','new_win':'base_new_win','new_sp':'base_new_sp',
                                'target_horse':'base_today_horse','target_win':'base_today_win','target_sp':'base_today_sp'})
    n=new[cols].rename(columns={'new_horse':'new_horse','new_win':'type_new_win','new_sp':'type_new_sp',
                                'target_horse':'type_today_horse','target_win':'type_today_win','target_sp':'type_today_sp'})
    m=b.merge(n,on=['date','race_id'],suffixes=('','_new'),how='outer',validate='one_to_one',indicator=True)
    if len(m)!=len(old) or not (m._merge=='both').all():
        raise RuntimeError('Historical race sets differ; do not compare sample-different ROI')
    for k in ('discipline','type','country','old_horse','old_win'):
        if not (m[k].astype(str)==m[k+'_new'].astype(str)).all():
            raise RuntimeError('Baseline or original v0 mismatch: '+k)
    m=m.drop(columns=[k+'_new' for k in ('discipline','type','country','old_horse','old_win','old_sp')]+['_merge'])
    m['period']=m.date.astype(str).str[:4]
    pairs=['old_sp','base_new_sp','type_new_sp','base_today_sp','type_today_sp']
    m['common_sp_settled']=pd.concat([pd.to_numeric(m[c],errors='coerce').gt(1) for c in pairs],axis=1).all(axis=1)
    allblock=m.copy();allblock['period']='ALL'
    d=pd.concat([allblock,m],ignore_index=True)
    s=summary(d,'all')
    # Global aggregation across all disciplines and all class groups.
    for period,block in d.groupby('period'):
        add=block.copy();add['discipline']='ALL';add['type']='ALL'
        s+=summary(add,'overall')
    for period,block in d.groupby(['period','discipline']):
        add=block.copy();add['type']='ALL'
        s+=summary(add,'discipline')
    path=out/'stage136-hpr-type-paired-races.csv.gz'
    m.to_csv(path,index=False,compression='gzip')
    pd.DataFrame(s).to_csv(out/'stage136-hpr-type-by-discipline.csv',index=False)
    report={'version':VERSION,'paired_races':len(m),'stage134c_baseline':'IMMUTABLE_COMPARED_BY_EXACT_RACE_ID',
            'changed_v31_to_type':int((m.base_new_horse!=m.new_horse).sum()),
            'type_priors':dict(T.TYPE_PRIOR),'historical_scoring_type_counts':dict(categories),
            'overall':next((r for r in s if r['period']=='ALL' and r['discipline']=='ALL' and r['race_type']=='ALL'),None),
            'audit_replay':audit['audit'],'no_odds_as_predictor':True,'no_live_updates':True,
            'caution':'Type priors were predeclared research hypotheses, NOT official class points, NOT guaranteed superiority.'}
    (out/'stage136-hpr-type-audit.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print('STAGE136 HPR:',json.dumps({k:report[k] for k in ('paired_races','changed_v31_to_type','overall')},indent=2),flush=True)
    return report


def self_test():
    # Stage134C original self-test runs independently in the GitHub workflow.
    # This test validates only the new Stage136 type ablation.
    v=T.parse_experience({'race_name':'Maiden Hurdle'})['experimental_type_prior_hpr']
    assert v==0.5
    assert type_score(80.,{'race_type':'Maiden Hurdle'})[0]==80.5
    assert type_score(80.,{'race_type':'Class 5 Handicap Hurdle'})[0]==80.
    assert type_score(99.8,{'race_type':'Rated Novice Hurdle'})[0]==100.
    assert type_score(70.,{'race_type':'Grade 1 Novice Hurdle'})[0]==70.
    assert type_score(80.,{'race_type':'Selling Handicap'})[0]==78.5
    print('Stage136 HPR type-prior ablation self-tests PASS')


def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='cmd',required=True)
    s.add_parser('self-test');q=s.add_parser('run')
    for label in ('history','reference','baseline-dir','out'):q.add_argument('--'+label,type=pathlib.Path,required=True)
    q.add_argument('--history-start',default='2022-01-01');q.add_argument('--start',default='2024-01-01');q.add_argument('--end',default='2025-12-31')
    a=p.parse_args()
    if a.cmd=='self-test':self_test()
    else:run(a.history,a.reference,a.baseline_dir,a.out,a.history_start,a.start,a.end)

if __name__=='__main__':main()
