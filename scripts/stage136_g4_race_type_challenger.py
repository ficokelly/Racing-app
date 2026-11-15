#!/usr/bin/env python3
"""Stage136 race-type-aware G4 challenger, research only, exact chronological baseline.

Ablation compared against BOTH original G4 and Stage131 class/weight G4, using
identical 2024/25 race IDs and results. Extra features are pre-race race-type
and field-strength facts, plus chronologically prior runs. No odds as inputs.
"""
from __future__ import annotations
import argparse,collections,csv,json,pathlib,sqlite3
from collections import defaultdict,deque
import stage136_race_type_strength as T
import stage131_class_weight_hpr as S131
VERSION='stage136-type-aware-g4-oos-v1'
ADDED_FEATURES=[
 'race_official_tier','race_official_tier_known','race_experience_stage',
 'race_experience_known','race_is_maiden','race_is_novice','race_is_beginner',
 'race_is_juvenile','race_is_restricted','race_is_rated',
 'race_is_seller','race_is_claimer','race_is_bumper',
 'race_field_or_median','race_field_or_median_known','race_field_or_coverage',
 'race_field_top4_or',
 'past3_official_tier','past3_known_tier_fraction','past3_field_or_median',
 'past3_maiden_fraction','past3_novice_fraction','past3_beginner_fraction',
 'past3_handicap_fraction','past3_experience_stage',
 'maiden_to_handicap','novice_to_handicap','beginners_to_handicap',
 'field_or_change_last','same_segment_strength_change',
]

def metadata(db):
    """Read *declared* OR/weight, never finishing position/market or winners."""
    import statistics
    cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    cur=cx.execute('''SELECT u.race_id,u.official_rating,u.weight_lbs
        FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
        WHERE r.race_date BETWEEN '2022-01-01' AND '2025-12-31'
        ORDER BY u.race_id''')
    out={};key=None;ors=[];wts=[];field=0
    def put(x):
        if x is not None:
            top=sorted(ors,reverse=True)[:4]
            out[str(x)]={'avg_or':sum(ors)/len(ors) if ors else None,
                  'avg_weight':sum(wts)/len(wts) if wts else None,
                  'rated_n':len(ors),'weight_n':len(wts),
                  'field_size':field,'median_or':statistics.median(ors) if len(ors)>=3 and len(ors)>=.5*field else None,
                  'top4_or':sum(top)/4 if len(top)>=4 and len(ors)>=.5*field else None,
                  'rated_coverage':len(ors)/field if field else 0.}
    for rid,official,weight in cur:
        if rid!=key:
            put(key);key=rid;ors=[];wts=[];field=0
        field+=1
        o=S131.num(official);w=S131.num(weight)
        if o is not None and 0<o<190:ors.append(o)
        if w is not None and 80<w<220:wts.append(w)
    put(key);cx.close()
    return out

def build_context_state(meta):
    import stage108_model_g2 as G
    _,Base=S131.build_context_state()
    Base.field_meta=meta
    class ContextState136(Base):
        field_data=meta
        def __init__(self):
            super().__init__()
            self.type_history=defaultdict(lambda:deque(maxlen=24))
        def event(self,r):
            z={**r,'jurisdiction':r.get('region_scope') or r.get('jurisdiction') or r.get('country')}
            return T.type_strength(z)
        def features(self,r,asof,field_size):
            base,prior=super().features(r,asof,field_size)
            this=self.event(r)
            record=self.field_data.get(str(r.get('race_id')),{})
            horse=G.identity(r);hist=[x for x in self.type_history[horse] if x['date']<asof] if horse else []
            p3=hist[-3:];prev=hist[-1] if hist else None
            def mean(key):return S131.avg([x.get(key) for x in p3])
            this_grade=S131.num(this.get('official_tier_score'))
            prev_grade=prev.get('tier') if prev else None
            this_segment=str(this.get('classification_segment') or '')
            # Subtract rank ONLY within same formal classification segment.
            segdelta=(this_grade-prev_grade if this_grade is not None and prev_grade is not None
                      and prev.get('segment')==this_segment else None) if prev else None
            this_type=this['race_experience_type']
            prior_maiden=bool(prev and prev.get('maiden'))
            prior_novice=bool(prev and prev.get('novice'))
            prior_beginner=bool(prev and prev.get('beginner'))
            now_hcap=bool(this.get('is_handicap'))
            om=record.get('median_or')
            base.update({
                'race_official_tier':this_grade,
                'race_official_tier_known':int(this_grade is not None),
                'race_experience_stage':this.get('experience_stage'),
                'race_experience_known':int(this_type!='UNKNOWN'),
                'race_is_maiden':this['is_maiden'],'race_is_novice':this['is_novice'],
                'race_is_beginner':this['is_beginner'],'race_is_juvenile':this['is_juvenile'],
                'race_is_restricted':this['is_restricted'],'race_is_rated':this['is_rated_race'],
                'race_is_seller':this['is_seller'],'race_is_claimer':this['is_claimer'],
                'race_is_bumper':this['is_bumper'],
                'race_field_or_median':om,
                'race_field_or_median_known':int(om is not None),
                'race_field_or_coverage':record.get('rated_coverage'),
                'race_field_top4_or':record.get('top4_or'),
                'past3_official_tier':mean('tier'),
                'past3_known_tier_fraction':sum(x.get('tier') is not None for x in p3)/len(p3) if p3 else None,
                'past3_field_or_median':mean('median_or'),
                'past3_maiden_fraction':mean('maiden'),
                'past3_novice_fraction':mean('novice'),
                'past3_beginner_fraction':mean('beginner'),
                'past3_handicap_fraction':mean('handicap'),
                'past3_experience_stage':mean('experience_stage'),
                'maiden_to_handicap':int(prior_maiden and now_hcap),
                'novice_to_handicap':int(prior_novice and now_hcap),
                'beginners_to_handicap':int(prior_beginner and now_hcap),
                'field_or_change_last':om-prev['median_or'] if prev and om is not None and prev.get('median_or') is not None else None,
                'same_segment_strength_change':segdelta,
            })
            return base,prior
        def add(self,r,d,field_size):
            super().add(r,d,field_size)
            if str(r.get('outcome') or '').lower() in ('nr','non-runner','nonrunner','withdrawn','dns','scratched'):return
            horse=G.identity(r)
            if not horse:return
            this=self.event(r)
            record=self.field_data.get(str(r.get('race_id')),{})
            self.type_history[horse].append({'date':d,'tier':S131.num(this.get('official_tier_score')),
                'segment':str(this.get('classification_segment') or ''),
                'median_or':record.get('median_or'),
                'maiden':this['is_maiden'],'novice':this['is_novice'],
                'beginner':this['is_beginner'],'handicap':this['is_handicap'],
                'experience_stage':this.get('experience_stage')})
    return G,ContextState136


def historical(db,reference,g3,original_g4,stage131_picks,out):
    import pandas as pd
    import numpy as np
    import joblib
    import stage109_model_g3 as G3
    import stage111_model_g4 as G4
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    meta=metadata(db)
    G,State=build_context_state(meta)
    refs,a=G.load_reference(reference,first='2022-01-01',last='2025-12-31')
    orig_state,orig_start=G.FormState,G.TRAIN_START
    try:
        G.FormState=State;G.TRAIN_START=G3.SEED_START
        df,_,audit,_=G.collect_historical(db,first='2022-01-01',last='2025-12-31',reference=refs)
    finally:
        G.FormState=orig_state;G.TRAIN_START=orig_start
    if df.empty:raise RuntimeError('No verified historical races')
    for col,target in [('weight_lbs','today_weight_relative'),('official_rating','today_or_relative')]:
        df[target]=pd.to_numeric(df[col],errors='coerce')-df.groupby('race_id')[col].transform(lambda x:pd.to_numeric(x,errors='coerce').mean())
    # Only current-preoff-or-past evidence: no target result, future, market or SP.
    allfeatures=list(G.FEATURES)
    added=list(S131.ADDED_FEATURES)+ADDED_FEATURES
    if len(set(allfeatures+added))!=len(allfeatures+added):raise RuntimeError('Duplicated feature')
    c={k:round(float(df[k].notna().mean())*100,2) for k in added}
    if float(df['race_experience_known'].mean())<.10:raise RuntimeError('Insufficient type-recognised historical races')
    baseline=G4.baseline_load(g3)
    try:
        G.FEATURES=allfeatures+added
        predictions,metrics,training,ledger,clean=G4.run_experiment(df,baseline)
        # Freeze the research candidate for later prospective testing; no live publication.
        fitted=G4.bank_fit(clean,'2025-12-31',ledger)
        joblib.dump({'version':VERSION,'features':list(G.FEATURES),'bank':fitted,
                     'trained_through':'2025-12-31','policy':'PAPER_ONLY_NO_ODDS'},
                    out/'stage136-g4-type-strength-challenger.joblib',compress=3)
    finally:G.FEATURES=allfeatures
    head=predictions[predictions['rank']==1].copy()
    head['model']=head['model'].str.replace('G4_','G4_STAGE136_',regex=False)
    head.to_csv(out/'stage136-g4-rank1.csv.gz',index=False,compression='gzip')
    orig=pd.read_csv(original_g4,compression='infer',low_memory=False)
    orig=orig[(orig['rank']==1)&orig.model.isin(['G4_SPECIALIST','G4_BLENDED'])].copy()
    orig['model']=orig['model'].str.replace('G4_','G4_STAGE136_',regex=False)
    orig=orig.rename(columns={'horse':'original_horse','win':'original_win','sp_decimal':'original_sp'})
    joined=head.merge(orig[['period','model','race_id','original_horse','original_win','original_sp']],
                    on=['period','model','race_id'],how='inner',validate='one_to_one')
    if len(joined)<50000:raise RuntimeError('Paired original G4 comparison too small: '+str(len(joined)))
    joined['changed_vs_original']=joined.horse!=joined.original_horse
    if stage131_picks and pathlib.Path(stage131_picks).is_file():
        prior=pd.read_csv(stage131_picks,compression='infer',low_memory=False)
        prior=prior[(prior['rank']==1)&prior.model.isin(['G4_CLASS_WEIGHT_SPECIALIST','G4_CLASS_WEIGHT_BLENDED'])].copy()
        prior['model']=prior.model.str.replace('G4_CLASS_WEIGHT_','G4_STAGE136_',regex=False)
        prior=prior.rename(columns={'horse':'stage131_horse','win':'stage131_win','sp_decimal':'stage131_sp'})
        joined=joined.merge(prior[['period','model','race_id','stage131_horse','stage131_win','stage131_sp']],
                           on=['period','model','race_id'],how='left',validate='one_to_one')
        if joined['stage131_horse'].notna().sum()<50000:raise RuntimeError('Incomplete Stage131 paired baseline')
    joined.to_csv(out/'stage136-g4-paired-all.csv.gz',index=False,compression='gzip')
    result=[]
    for (period,model,disc,typ),part in joined.groupby(['period','model','discipline','race_class_group'],dropna=False):
        s={'period':period,'model':model,'discipline':disc,'race_type':typ,'paired_races':len(part),
           'new_wins':int(part.win.sum()),'original_wins':int(part.original_win.sum()),
           'new_strike_pct':round(100*part.win.mean(),2),'original_strike_pct':round(100*part.original_win.mean(),2),
           'changed_vs_original':int(part.changed_vs_original.sum())}
        for key,horse,won,sp in [('new','horse','win','sp_decimal'),('original','original_horse','original_win','original_sp')]:
            prices=pd.to_numeric(part[sp],errors='coerce');ok=prices.notna()&(prices>1)
            s[key+'_settled']=int(ok.sum())
            s[key+'_roi_sp_pct']=round(100*((prices[ok]*(part.loc[ok,won]==1)).sum()/ok.sum()-1),2) if ok.sum() else None
        if 'stage131_horse' in part.columns:
            prices=pd.to_numeric(part.stage131_sp,errors='coerce');ok=prices.notna()&(prices>1)
            s['stage131_wins']=int(part.stage131_win.sum())
            s['stage131_strike_pct']=round(100*part.stage131_win.mean(),2)
            s['stage131_roi_sp_pct']=round(100*((prices[ok]*(part.loc[ok,'stage131_win']==1)).sum()/ok.sum()-1),2) if ok.sum() else None
            s['changed_vs_stage131']=int((part.horse!=part.stage131_horse).sum())
        result.append(s)
    pd.DataFrame(result).to_csv(out/'stage136-g4-by-discipline-race-type.csv',index=False)
    report={'version':VERSION,'paired_selections':len(joined),'race_count':int(joined.race_id.nunique()),
        'changed_vs_original':int(joined.changed_vs_original.sum()),
        'stage131_present':'stage131_horse' in joined,
        'stage131_changes':int((joined.horse!=joined.stage131_horse).sum()) if 'stage131_horse' in joined else None,
        'feature_coverage_pct':c,'race_type_counts':dict(collections.Counter(df['race_name'].astype(str).str.lower().str.contains('maiden').map({True:'maiden',False:'other'}))),
        'data_audit':audit,'reference_audit':a,
        'preoff_only':True,'odds_as_model_input':False,'no_live_updates':True,
        'warning':'Out-of-time backtest, not prospective bettable odds or proven model superiority.'}
    (out/'stage136-g4-audit.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
    print('STAGE136 G4:',json.dumps({k:report[k] for k in ('paired_selections','race_count','changed_vs_original','stage131_changes','stage131_present')}),flush=True)
    return report


def self_test():
    assert len(ADDED_FEATURES)>=20 and len(set(ADDED_FEATURES))==len(ADDED_FEATURES)
    assert T.parse_experience({'race_name':'Maiden Hurdle'})['race_experience_type']=='MAIDEN'
    assert T.parse_experience({'race_name':'Novices Handicap Chase'})['race_experience_type']=='NOVICE_HANDICAP'
    print('Stage136 G4 feature tests PASS:',len(ADDED_FEATURES),'extra strictly pre-race features')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('self-test');h=sub.add_parser('historical')
    for k in ('db','reference','g3','original-g4','out'):h.add_argument('--'+k,type=pathlib.Path,required=True)
    h.add_argument('--stage131-picks',type=pathlib.Path)
    a=p.parse_args()
    if a.cmd=='self-test':return self_test()
    historical(a.db,a.reference,a.g3,a.original_g4,a.stage131_picks,a.out)

if __name__=='__main__':main()
