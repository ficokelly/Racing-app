#!/usr/bin/env python3
"""Stage131: Class-and-weight aware G4 challenger and HPR-v3 reconstruction.

Research only: no modifications to existing G4 models, HPR shards, or live site.
No SP/odds as predictive inputs. Inputs strictly historical/pre-race when scored.

Commands:
  self-test
  rebuild-hpr --store OLD_HPR_DB --out-dir OUT
  historical-g4 --db HISTORY_DB --reference REFERENCE_CSV \
     --g3-baseline G3_ALL_HORSES_CSV_GZ --g4-baseline G4_RANK1_RANK2_CSV_GZ --out-dir OUT
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import sqlite3
import statistics
import sys
from collections import defaultdict, deque

VERSION = 'stage131-g4-class-weight-research-v1'
HPR_VERSION = 'HPR-v3-class-weight-2026-11-15-research'
BASE_HPR_VERSION = 'HPR-v0-2026-11-10-research'

# Predeclared research priors, not fitted corrections or official performance ratings.
CLASS_POINTS = {1: 10., 2: 7., 3: 4., 4: 0., 5: -3., 6: -6.}
# These non-UK grade values are provisional relative-strength hypotheses.
GRADE_POINTS = {1: 13., 2: 10., 3: 7.}
ADDED_FEATURES = [
    'last3_class_strength','last5_class_strength','best5_class_strength',
    'last3_opponent_or','last3_opponent_or_known',
    'last3_weight_relative','last3_weight_relative_known',
    'current_class_strength','class_change_last','class_change_last_known',
    'last3_class_adjusted_form','last3_class_adjusted_form_known',
    'weight_change_last','weight_change_last_known','mark_change_last',
    'mark_change_last_known','today_weight_relative','today_or_relative',
]


def num(x):
    try:
        if x is None or str(x).strip().lower() in ('','-','none','nan','null'):
            return None
        n=float(x)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):return None


def clamp(x,lo=-100.,hi=100.):return min(hi,max(lo,x))


def avg(values):
    vals=[num(v) for v in values]; vals=[v for v in vals if v is not None]
    return sum(vals)/len(vals) if vals else None


def strength(r):
    """Pre-race race-class signal; missing grades/classes are missing, not Class 4.

    Grade, Group and Listed are checked on pre-race race-name/type metadata;
    UK class 1-6 on class metadata. The highest evidenced tier is used once.
    """
    code=str(r.get('race_class') or r.get('class') or '')
    name=' '.join(str(r.get(k) or '') for k in ('race_name','race_type'))
    signals=[]
    m=re.search(r'\bclass\s*([1-6])\b',code+' '+name,re.I)
    if m:signals.append(('UK_CLASS_'+m.group(1),CLASS_POINTS[int(m.group(1))]))
    for label in (code,name):
        g=re.search(r'\b(?:grade|group)\s*([1-3])\b',label,re.I)
        if g:signals.append(('GRADED_'+g.group(1),GRADE_POINTS[int(g.group(1))]))
        elif re.search(r'\b(?:listed|grade\s*[A-C])\b',label,re.I):
            signals.append(('LISTED',6.))
    return max(signals,key=lambda x:x[1]) if signals else (None,None)


def old_class_bonus(r):
    m=re.search(r'\bclass\s*([1-6])\b',str(r.get('race_class') or ''),re.I)
    return (4-int(m.group(1)))*1.1 if m else 0.


def relative(x,mid):
    a=num(x);b=num(mid)
    return a-b if a is not None and b is not None else None


def safe_key(p):
    return str(p.get('horse_key') or ''),str(p.get('race_key') or ''),str(p.get('date') or '')


def hpr_rebuild(source: pathlib.Path, out: pathlib.Path, daily_dir: pathlib.Path | None = None):
    """Re-rate every distinct existing HPR v0 event into a SEPARATE SQLite store.

    Relative weight and opposition OR use other contemporaneous runners with
    pre-race ratings, never finish results or future records. Prior HPR condition
    pillars remain intact. This is a research index, not calibrated ability.
    """
    out.mkdir(parents=True,exist_ok=True)
    if not source.is_file():raise FileNotFoundError(source)
    dest=out/'hpr-v3-class-weight-research.sqlite'
    if source.resolve()==dest.resolve():raise ValueError('Cannot overwrite original HPR')
    old=sqlite3.connect('file:'+str(source.resolve())+'?mode=ro',uri=True)
    old.row_factory=sqlite3.Row
    if old.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Bad HPR input SQLite')
    expected={'horse_key','race_key','race_date','model_version','revision','payload'}
    columns={x[1] for x in old.execute('PRAGMA table_info(hpr_ratings)')}
    if not expected<=columns:raise ValueError('Wrong HPR input schema')
    rows=old.execute('''SELECT r.payload FROM hpr_ratings r JOIN
      (SELECT horse_key,race_key,model_version,MAX(revision) rev FROM hpr_ratings
       WHERE model_version=? GROUP BY horse_key,race_key,model_version) v
      ON v.horse_key=r.horse_key AND v.race_key=r.race_key
        AND v.model_version=r.model_version AND v.rev=r.revision
      ORDER BY r.race_date,r.horse_key,r.race_key''',(BASE_HPR_VERSION,))
    events=[json.loads(x[0]) for x in rows]
    old.close()
    daily_merge={'new':0,'updated':0,'skipped':0}
    if daily_dir and daily_dir.is_dir():
        by_key={(str(e.get('horse_key')),str(e.get('race_key'))):e for e in events}
        for fn in sorted(daily_dir.glob('20??-??-??.json')):
            d=json.loads(fn.read_text(encoding='utf-8'))
            for e in d.get('ratings',[]):
                if e.get('model_version')!=BASE_HPR_VERSION:continue
                k=(str(e.get('horse_key')),str(e.get('race_key')))
                if not all(k):continue
                if k not in by_key:
                    by_key[k]=e;daily_merge['new']+=1
                elif by_key[k].get('source_digest')!=e.get('source_digest'):
                    by_key[k]=e;daily_merge['updated']+=1
                else:daily_merge['skipped']+=1
        events=list(by_key.values())
    if not events:raise RuntimeError('No original v0 HPR events; cannot claim recalibration')
    # Group only by exact recorded race identity, never name or approximate race time.
    fields=defaultdict(list)
    for e in events:fields[(str(e.get('date')),str(e.get('race_key')))].append(e)
    fieldstats={}
    for key,runners in fields.items():
        rat=[num(x.get('official_rating')) for x in runners]
        wt=[num(x.get('weight_lbs')) for x in runners]
        valid_or=[x for x in rat if x is not None and 0<x<190]
        valid_wt=[x for x in wt if x is not None and 80<x<220]
        full=all(x.get('race_preoff_field_verified') is True for x in runners)
        fieldstats[key]={
            'avg_or':avg(valid_or) if len(valid_or)>=3 else None,
            'avg_weight':avg(valid_wt) if len(valid_wt)>=3 else None,
            'rated_runners':len(valid_or),'weighted_runners':len(valid_wt),
            'field_verified':full,
        }
    counts=collections.Counter();class_coverage=collections.Counter()
    result=[];per=defaultdict(list)
    for e in sorted(events,key=lambda z:(str(z.get('date')),str(z.get('race_key')),str(z.get('horse_key')))):
        key=(str(e.get('date')),str(e.get('race_key')))
        field=fieldstats[key]
        label,klass=strength(e)
        class_coverage[label or 'UNKNOWN']+=1
        # Undo v0 class contribution once before applying a broader hypothesis.
        # v0 quality may also include a field-OR adjustment not fully exposed
        # in its saved payload; preserve that existing component and flag it.
        raw=num(e.get('raw_performance'))
        if raw is None:
            counts['missing_original_quality']+=1
            continue
        klass_delta=(klass-old_class_bonus(e)) if klass is not None else 0.
        mean_or=field['avg_or']
        mean_wt=field['avg_weight']
        # Conservative, bounded. Without >=3 known contemporaneous runners, neutral.
        opposition=clamp((mean_or-105.)*.11,-4.,4.) if mean_or is not None else 0.
        wt_relative=relative(e.get('weight_lbs'),mean_wt)
        carried=clamp(wt_relative*.20,-3.,3.) if wt_relative is not None else 0.
        # Keep 65% observed evidence, 35% prior context with same uplift.
        adjusted_quality=clamp(raw+klass_delta+opposition+carried,0.,100.)
        old_context=num(e.get('pillar_composite'))
        if old_context is None:old_context=raw
        adjust_context=clamp(old_context+klass_delta+opposition+carried,0.,100.)
        rating=round(clamp(.65*adjusted_quality+.35*adjust_context,0.,100.),2)
        old_hpr=num(e.get('hpr'))
        prev=[x for x in per[str(e.get('horse_key'))] if x['date']<str(e.get('date'))]
        prev3=prev[-3:]
        record={**e,'hpr':rating,'model_version':HPR_VERSION,
            'original_hpr_v0':old_hpr,'class_label':label or 'UNKNOWN',
            'class_points':klass,'class_delta_from_v0':round(klass_delta,3),
            'opposition_median_or_proxy':mean_or,
            'opposition_adjustment':round(opposition,3),
            'carried_weight_vs_field_lbs':round(wt_relative,2) if wt_relative is not None else None,
            'weight_adjustment':round(carried,3),
            'raw_class_weight_quality':round(adjusted_quality,3),
            'previous_class_weight_hpr_count':len(prev),
            'previous_class_weight_hpr_3':round(avg([x['hpr'] for x in prev3]),3) if prev3 else None,
            'class_weight_evidence':{
                'field_preoff_verified':field['field_verified'],
                'known_entrant_ratings':field['rated_runners'],
                'known_entrant_weights':field['weighted_runners'],
                'class_known':klass is not None,
                'warning':'Provisional weights; HPR v0 field-OR bonus not independently removable',
            }}
        result.append(record)
        per[str(e.get('horse_key'))].append({'date':str(e.get('date')),'hpr':rating})
        counts['recalibrated']+=1
        if klass is None:counts['class_unknown']+=1
        if mean_or is None:counts['opponent_rating_unknown']+=1
        if wt_relative is None:counts['relative_weight_unknown']+=1
    if len(result)!=len(events):raise RuntimeError('Not all existing HPRs recalibrated; refusing success')
    if dest.exists():dest.unlink()
    con=sqlite3.connect(dest)
    con.executescript('''CREATE TABLE hpr_ratings(
     horse_key TEXT NOT NULL,race_key TEXT NOT NULL,race_date TEXT NOT NULL,
     model_version TEXT NOT NULL,revision INTEGER NOT NULL,source_sha TEXT NOT NULL,
     hpr REAL NOT NULL,confidence TEXT NOT NULL,payload TEXT NOT NULL,
     inserted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
     PRIMARY KEY(horse_key,race_key,model_version,revision));
     CREATE INDEX hpr_horse_date ON hpr_ratings(horse_key,race_date);
     CREATE INDEX hpr_race_date ON hpr_ratings(race_date);
     CREATE TABLE hpr_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    ''')
    con.execute('INSERT INTO hpr_metadata VALUES (?,?)',('version',HPR_VERSION))
    def insert(x):
        raw=json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        return (x['horse_key'],x['race_key'],str(x['date']),HPR_VERSION,1,hashlib.sha256(raw.encode()).hexdigest(),x['hpr'],x.get('confidence') or 'PARTIAL',raw)
    con.executemany('''INSERT INTO hpr_ratings(horse_key,race_key,race_date,model_version,revision,source_sha,hpr,confidence,payload)
       VALUES (?,?,?,?,?,?,?,?,?)''',(insert(x) for x in result))
    con.commit()
    n=con.execute('SELECT COUNT(*) FROM hpr_ratings').fetchone()[0]
    con.close()
    assert n==len(events)
    audit={'version':HPR_VERSION,'original_version':BASE_HPR_VERSION,
           'original_events':len(events),'new_events':n,'daily_merge':daily_merge,
           'earliest_event_date':min(str(x.get('date')) for x in events),
           'latest_event_date':max(str(x.get('date')) for x in events),
           'rows_by_class':dict(class_coverage),
           'coverage':dict(counts),'distinct_horses':len(per),
           'notes':['All earlier ratings left untouched; output is a separate database.',
                    'Research priors must be tested out of sample before promotion.',
                    'No previous-day or same-day results used to compute a race before it happens.',
                    'No odds or SP used as model inputs.',
                    'Missing opposition rating or class is neutral, not assumed weak.',
                    'Original HPR raw form quality included a small class bonus: replaced, not simply double-added.']}
    (out/'stage131-hpr-recalibration-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    with (out/'stage131-hpr-recalibration-sample.csv').open('w',newline='') as f:
        w=csv.writer(f)
        w.writerow(['Date','Horse','Race','Class','Old HPR','New HPR','Delta','Opponent OR','Weight vs field (lb)','Evidence verified'])
        for e in sorted(result,key=lambda x:(x.get('date',''),x.get('horse','')),reverse=True)[:1000]:
            old_=num(e.get('original_hpr_v0'))
            w.writerow([e.get('date'),e.get('horse'),e.get('race_key'),e.get('class_label'),old_,e['hpr'],round(e['hpr']-old_,2) if old_ is not None else '',e.get('opposition_median_or_proxy'),e.get('carried_weight_vs_field_lbs'),e['class_weight_evidence']['field_preoff_verified']])
    return audit


def build_context_state():
    """Add class/weight/opposition history to the IDENTICAL G4 core feature engine."""
    import stage108_model_g2 as G
    class ContextState(G.FormState):
        field_meta={}
        def __init__(self):
            super().__init__()
            self.extra=defaultdict(lambda:deque(maxlen=24))
        def features(self,r,asof,field_size):
            base,prior=super().features(r,asof,field_size)
            key=G.identity(r)
            previous=[x for x in self.extra[key] if x['date']<asof] if key else []
            last3=previous[-3:];last5=previous[-5:]
            _,today_class=strength(r)
            last_class=previous[-1]['class'] if previous else None
            current_weight=num(r.get('weight_lbs') or r.get('lbs'))
            last_weight=previous[-1]['weight'] if previous else None
            current_or=num(r.get('official_rating') or r.get('ofr'))
            last_or=previous[-1]['or'] if previous else None
            past_class=avg([x['class'] for x in last3]);five_class=avg([x['class'] for x in last5])
            past_opponent=avg([x['opponent'] for x in last3]);past_weight_relative=avg([x['relative_weight'] for x in last3])
            form_quality=avg([x['quality_context'] for x in last3])
            base.update({
                'last3_class_strength':past_class,
                'last5_class_strength':five_class,
                'best5_class_strength':max((x['class'] for x in last5 if x['class'] is not None),default=None),
                'last3_opponent_or':past_opponent,
                'last3_opponent_or_known':int(past_opponent is not None),
                'last3_weight_relative':past_weight_relative,
                'last3_weight_relative_known':int(past_weight_relative is not None),
                'current_class_strength':today_class,
                'class_change_last':today_class-last_class if today_class is not None and last_class is not None else None,
                'class_change_last_known':int(today_class is not None and last_class is not None),
                'last3_class_adjusted_form':form_quality,
                'last3_class_adjusted_form_known':int(form_quality is not None),
                'weight_change_last':current_weight-last_weight if current_weight is not None and last_weight is not None else None,
                'weight_change_last_known':int(current_weight is not None and last_weight is not None),
                'mark_change_last':current_or-last_or if current_or is not None and last_or is not None else None,
                'mark_change_last_known':int(current_or is not None and last_or is not None),
                'today_weight_relative':None, # computed only after full-field collection
                'today_or_relative':None,
            })
            return base,prior
        def add(self,r,d,field_size):
            super().add(r,d,field_size)
            key=G.identity(r)
            if not key or str(r.get('outcome') or '').lower() in ('nr','nonrunner','withdrawn','dns','scratched'):return
            _,score=strength(r)
            field=self.field_meta.get(str(r.get('race_id')),{})
            opponent=field.get('avg_or') if field.get('rated_n',0)>=3 else None
            meanwt=field.get('avg_weight') if field.get('weight_n',0)>=3 else None
            wt=num(r.get('weight_lbs') or r.get('lbs'))
            rel=relative(wt,meanwt)
            observed=G.quality(r,field_size)
            # Recorded only after this run: next day's form may draw on it.
            context=(clamp(100.*observed+(score or 0.)+(clamp((opponent-105)*.11,-4,4) if opponent is not None else 0)+(clamp(rel*.2,-3,3) if rel is not None else 0),0,100)
                     if observed is not None else None)
            self.extra[key].append({'date':d,'class':score,'opponent':opponent,
              'relative_weight':rel,'weight':wt,'or':num(r.get('official_rating') or r.get('ofr')),
              'quality_context':context})
    ContextState.__module__=__name__
    ContextState.__name__='ContextState'
    return G,ContextState


def historical_g4(db,reference,g3_baseline,g4_baseline,out):
    import pandas as pd
    import joblib
    import stage109_model_g3 as G3
    import stage111_model_g4 as G4
    G,CState=build_context_state()
    globals()['ContextState']=CState  # joblib models can be restored by importing stage131
    out.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
    # This is all pre-race official rating and declared weight, not finishing form.
    cur=con.execute('''SELECT u.race_id, u.official_rating, u.weight_lbs
       FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
       WHERE r.race_date BETWEEN '2022-01-01' AND '2025-12-31'
       ORDER BY u.race_id''')
    meta={};key=None;ors=[];wts=[]
    def save(x):
        if x is not None:
            meta[str(x)]={'avg_or':avg(ors),'avg_weight':avg(wts),'rated_n':len(ors),'weight_n':len(wts)}
    for rid,or_,wt in cur:
        if rid!=key:
            save(key);key=rid;ors=[];wts=[]
        v=num(or_);w=num(wt)
        if v is not None and 0<v<190:ors.append(v)
        if w is not None and 80<w<220:wts.append(w)
    save(key);con.close()
    CState.field_meta=meta
    ref,refaudit=G.load_reference(reference,first='2022-01-01',last='2025-12-31')
    original_state=G.FormState
    original_train=G.TRAIN_START
    basefeatures=list(G.FEATURES)
    try:
        G.FormState=CState
        G.TRAIN_START=G3.SEED_START
        df,state,audit,_=G.collect_historical(db,first='2022-01-01',last='2025-12-31',reference=ref)
    finally:
        G.FormState=original_state
        G.TRAIN_START=original_train
    if df.empty:raise RuntimeError('No verified matched G4 historical rows')
    # Today's-relative comparisons are valid because OR and carried weights are
    # contemporaneous declared data, independent of the winner.
    for col,target in [('weight_lbs','today_weight_relative'),('official_rating','today_or_relative')]:
        df[target]=pd.to_numeric(df[col],errors='coerce')-df.groupby('race_id')[col].transform(lambda x:pd.to_numeric(x,errors='coerce').mean())
    extra_coverage={x:round(float(df[x].notna().mean())*100,2) for x in ADDED_FEATURES}
    class_by_race_type={}
    for kind,group in df.groupby('race_class_group'):
        class_by_race_type[str(kind)]={
           'runners':len(group),
           'historical_class_known_pct':round(100*group['last3_class_strength'].notna().mean(),2),
           'current_class_known_pct':round(100*group['current_class_strength'].notna().mean(),2),
           'historical_opponent_or_known_pct':round(100*group['last3_opponent_or'].notna().mean(),2),
           'previous_weight_known_pct':round(100*group['weight_change_last'].notna().mean(),2),
        }
    (out/'stage131-g4-feature-coverage-audit.json').write_text(json.dumps({
        'version':VERSION,'source_runners':len(df),
        'total_feature_coverage_pct':extra_coverage,
        'by_race_type':class_by_race_type,
        'missing_data_policy':'Unknown class is never assumed Class 4; historic records are not imputed.',
    },indent=2)+'\n')
    if extra_coverage['current_class_strength']<5:
        raise RuntimeError('Historical class coverage <5%; source class recovery required before pretending to train a class-aware model')
    # Reuse G4's identical train/test checkpoints, learning algorithm, and split;
    # only new PRE-RACE features distinguish this challenger.
    oldfeatures=G.FEATURES
    try:
        G.FEATURES=basefeatures+ADDED_FEATURES
        # The G3 baseline is loaded independently and is never retrained.
        baseline=G4.baseline_load(g3_baseline)
        predictions,period_metrics,training,ledger,clean=G4.run_experiment(df,baseline)
        new=predictions[predictions['rank']==1].copy()
        new['model']=new['model'].str.replace('G4_','G4_CLASS_WEIGHT_',regex=False)
        new.to_csv(out/'stage131-g4-class-weight-rank1.csv.gz',index=False,compression='gzip')
        orig=pd.read_csv(g4_baseline,compression='infer',low_memory=False)
        if 'period' not in orig.columns or 'race_id' not in orig.columns or 'rank' not in orig.columns:raise RuntimeError('Original G4 paired baseline missing essential columns')
        orig=orig[(orig['rank']==1)&orig.model.isin(['G4_SPECIALIST','G4_BLENDED'])].copy()
        if not len(orig):raise RuntimeError('Missing original G4 rank1 baseline')
        orig['model']=orig['model'].str.replace('G4_','G4_CLASS_WEIGHT_',regex=False)
        orig=orig.rename(columns={'horse':'original_horse','win':'original_win','sp_decimal':'original_sp'})
        join=new.merge(orig[['period','model','race_id','original_horse','original_win','original_sp']],on=['period','model','race_id'],how='inner',validate='one_to_one')
        if len(join)<1000:raise RuntimeError(f'Too few paired races for research: {len(join)}')
        join['changed_horse']=join.horse!=join.original_horse
        join.to_csv(out/'stage131-g4-paired-with-frozen.csv.gz',index=False,compression='gzip')
        metrics=[]
        for (period,model,discipline,race_type),block in join.groupby(['period','model','discipline','race_class_group'],dropna=False):
            row={'period':period,'model':model,'discipline':discipline,'race_type':race_type,'paired_races':len(block),'changed_picks':int(block.changed_horse.sum()),
                 'new_wins':int(block.win.sum()),'original_wins':int(block.original_win.sum()),
                 'new_strike_pct':round(100*block.win.mean(),2),'original_strike_pct':round(100*block.original_win.mean(),2)}
            for p,col in [('new','sp_decimal'),('original','original_sp')]:
                price=pd.to_numeric(block[col],errors='coerce')
                valid=price.notna() & (price>1)
                row[p+'_roi_sp_pct']=round(100*((price[valid]*(block.loc[valid,'win' if p=='new' else 'original_win']==1)).sum()/valid.sum()-1),2) if valid.sum() else None
                row[p+'_settled']=int(valid.sum())
            metrics.append(row)
        pd.DataFrame(metrics).to_csv(out/'stage131-g4-by-discipline-racetype.csv',index=False)
        bank=G4.bank_fit(clean,'2025-12-31',ledger)
        joblib.dump({'version':VERSION,'features':G.FEATURES,'bank':bank,'trained_through':'2025-12-31',
                     'policy':'PAPER_ONLY_NO_ODDS','history_coverage':extra_coverage},out/'stage131-g4-class-weight-model.joblib',compress=3)
        report={'version':VERSION,'paired_rows':len(join),'paired_change_count':int(join.changed_horse.sum()),
                'feature_coverage_pct':extra_coverage,'feature_coverage_by_race_type':class_by_race_type,'source_audit':audit,'reference_audit':refaudit,
                'training':training,'note':'Original G4 models untouched; SP is hindsight only; no price inputs; score before race only.'}
        (out/'stage131-g4-audit.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
        return report
    finally:
        G.FEATURES=oldfeatures


def self_test():
    assert strength({'race_class':'Class 1'})[1]>strength({'race_class':'Class 4'})[1]
    assert strength({'race_name':'A Handicap Chase (Class 2)'})[1]==7
    assert strength({'race_class':'Class 4','race_name':'Grade 1 Novices Hurdle'})[1]==13
    assert strength({'race_class':'','race_type':'Maiden Hurdle'})[1] is None
    assert strength({'race_class':'Class 6'})[1]==-6
    assert relative(160,154)==6
    assert old_class_bonus({'race_class':'Class 1'})==3.3 or abs(old_class_bonus({'race_class':'Class 1'})-3.3)<1e-9
    assert relative(None,154) is None
    # Fabricated HPR history, fully offline, tests preservation and chronology.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        db=pathlib.Path(d)/'old.sqlite';con=sqlite3.connect(db)
        con.execute('CREATE TABLE hpr_ratings(horse_key TEXT,race_key TEXT,race_date TEXT,model_version TEXT,revision INTEGER,payload TEXT)')
        for dte in ('2025-01-01','2025-02-01'):
            for i in range(3):
                r={'horse_key':f'h{i}','horse':f'Horse {i}','race_key':dte+'|R1','date':dte,
                  'race_class':'Class 1' if dte=='2025-01-01' else 'Class 4',
                  'official_rating':110+i,'weight_lbs':154+i,'position':i+1,
                  'hpr':60.,'raw_performance':60.,'pillar_composite':60.,
                  'race_preoff_field_verified':True,'confidence':'HIGH'}
                con.execute('INSERT INTO hpr_ratings VALUES (?,?,?,?,?,?)',
                  (r['horse_key'],r['race_key'],dte,BASE_HPR_VERSION,1,json.dumps(r)))
        con.commit();con.close()
        a=hpr_rebuild(db,pathlib.Path(d)/'new')
        assert a['new_events']==6
        new=sqlite3.connect(pathlib.Path(d)/'new'/'hpr-v3-class-weight-research.sqlite')
        assert new.execute('SELECT count(*) FROM hpr_ratings').fetchone()[0]==6
        scores={json.loads(x[0])['date']:json.loads(x[0])['hpr'] for x in new.execute("SELECT payload FROM hpr_ratings WHERE horse_key='h0'")}
        assert scores['2025-01-01']>scores['2025-02-01'],scores
        new.close()
    print('PASS Stage131: class taxonomy, missing evidence, field weights, full HPR rebuild, version preservation')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('self-test')
    h=sub.add_parser('rebuild-hpr');h.add_argument('--store',type=pathlib.Path,required=True);h.add_argument('--out-dir',type=pathlib.Path,required=True);h.add_argument('--daily-dir',type=pathlib.Path)
    g=sub.add_parser('historical-g4');g.add_argument('--db',type=pathlib.Path,required=True);g.add_argument('--reference',type=pathlib.Path,required=True)
    g.add_argument('--g3-baseline',type=pathlib.Path,required=True);g.add_argument('--g4-baseline',type=pathlib.Path,required=True)
    g.add_argument('--out-dir',type=pathlib.Path,required=True)
    a=p.parse_args()
    if a.cmd=='self-test':self_test()
    elif a.cmd=='rebuild-hpr':print(json.dumps(hpr_rebuild(a.store,a.out_dir,a.daily_dir),indent=2))
    elif a.cmd=='historical-g4':print(json.dumps(historical_g4(a.db,a.reference,a.g3_baseline,a.g4_baseline,a.out_dir),indent=2,default=str))

if __name__=='__main__':main()
