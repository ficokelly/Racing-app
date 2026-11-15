#!/usr/bin/env python3
"""Stage137: Frozen D vs date-safe race-type pillar tuning and NLP-form challenger.

2022: historical state warm-up; 2023: initial model training; 2024/25:
quarterly PREQUENTIAL predictions, only AFTER each quarter retrain using outcomes.
Original Stage30 D 8 pillar inputs and 8 historical weights are IMMUTABLE.
Never modifies the app or model D. Prices/SP never enter scoring features.
The ROI-tuned *research weight branch* selects parameters from preceding SP
settlements; its candidate-race prices are never predictor inputs.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,json,math,pathlib,re,sqlite3,statistics,sys
from collections import defaultdict,deque
import numpy as np

VERSION='stage137-frozen-d-racetype-nlp-walkforward-v1'
PILLARS=('Ability','Trainer','Jockey','Course','Draw','Distance','Ground','Jumping')
# Verified immutable Stage30 D-064-gap14 / reports/stage30/stage30-summary.json.
FROZEN=np.asarray([13.93,8.96,5.60,17.77,12.25,11.17,25.03,5.28],dtype=float)
FROZEN/=FROZEN.sum()
FROZEN_ID='D-064-gap14'
HISTORY_START='2022-01-01'
PERIODS=[(f'{y}Q{q}',f'{y}-{(q-1)*3+1:02d}-01',
          str(dt.date(y+1,1,1)-dt.timedelta(days=1)) if q==4 else str(dt.date(y,q*3+1,1)-dt.timedelta(days=1)))
         for y in (2024,2025) for q in (1,2,3,4)]
FEATURES=list(PILLARS)+['frozen_d_score','horse_form_quality3','horse_form_trend',
    'horse_beaten3','horse_beaten_per_mile3','horse_comment_phrase3','horse_comment_nlp3','horse_nlp_coverage',
    'horse_jumping_comment3','horse_interference3',
    'horse_runs','last_run_days','past_grade_strength3','past_known_tier_fraction',
    'current_tier_strength','current_tier_known','past_opposition_median3',
    'current_opposition_median','current_opposition_known','same_code_form3',
    'maiden_to_handicap','novice_to_handicap','beginner_to_handicap',
    'last3_maiden_fraction','last3_novice_fraction','past_vs_current_tier_delta',
    'prior_rivals_nextout_quality3','prior_rivals_nextout_winrate3',
    'prior_rivals_nextout_count3','prior_rivals_nextout_coverage3',
    'current_race_experience_stage','current_handicap','current_gr1',
    'current_gr2','current_gr3','field_size','official_rating','weight_lbs',
    'relative_official_rating','relative_weight_lbs','nlp_model_trained_comments',
    'form_quality_confident']
MODELS=('FROZEN_D','D_PILLAR_SR','D_PILLAR_ROI','D_RACETYPE_NLP')
MIN_LOCAL=100
MIN_ML_LOCAL_RACES=180


def fnum(x):
    try:
        z=float(x)
        return z if math.isfinite(z) else None
    except (TypeError,ValueError):return None

def avg(xs):
    v=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return sum(v)/len(v) if v else None

def sanitize_comment(x):
    z=str(x or '').strip()
    if not z or z.lower() in ('nan','none','n/a'):return ''
    return z[:800]

def ident(row):
    import stage108_model_g2 as G
    return G.identity({'horse':row[9],'horse_source_id':row[11],
                       'sire':row[18],'dam':row[19]})

def read_reference(path):
    import stage108_model_g2 as G
    ref,summary=G.load_reference(path,first=HISTORY_START,last='2025-12-31')
    return ref,summary

def race_catalogue(e, region, name, starters):
    import stage136_race_type_strength as T
    import stage108_model_g2 as G
    event={'race_name':name,'race_type':e[3],'race_class':e[7],
           'race_date':e[1],'jurisdiction':region,'surface':e[4]}
    d=T.type_strength(event,[{'official_rating':z[16]} for z in starters])
    kind=d['discipline'];type_=d['race_experience_type'];tier=d['classification_code']
    hcap=bool(d['is_handicap'])
    # Distinguish genuine AW surfaces from unknown turf. G2 knows Standard,
    # Tapeta, Polytrack, venue AW-only evidence and preserves unknowns.
    if kind in ('AW','FLAT'):
        _,aw,_=G.category({'race_name':name,'race_type':e[3],
                           'surface':e[4],'course':e[2]})
        broad='AW' if aw is True or kind=='AW' else ('FLAT' if aw is False else 'FLAT_UNKNOWN')
    else:broad='NH'
    if kind in ('HURDLE','CHASE'):
        if tier.startswith('NH_GRADE_'):
            subtype=f'{kind}_GRADE_{tier.rsplit("_",1)[-1]}'
        elif hcap:subtype=f'{kind}_HANDICAP'
        elif type_ in ('BEGINNERS_CHASE','RATED_BEGINNERS_CHASE'):
            subtype='CHASE_BEGINNERS'
        elif 'NOVICE' in type_:subtype=f'{kind}_NOVICE'
        elif 'MAIDEN' in type_:subtype=f'{kind}_MAIDEN'
        else:subtype=f'{kind}_OTHER'
    elif kind=='NH_FLAT':subtype='NH_BUMPER'
    else:
        cls='HANDICAP' if hcap else 'MAIDEN' if 'MAIDEN' in type_ else 'NOVICE' if 'NOVICE' in type_ else 'OTHER'
        subtype=f'{broad}_{cls}'
    group=kind if kind in ('HURDLE','CHASE','NH_FLAT') else broad
    return d,broad,group,subtype


def mask_weights(w,broad):
    v=np.asarray(w,dtype=float).copy()
    if broad=='NH':v[4]=0.
    else:v[7]=0.
    return v/max(1e-12,float(v.sum()))


def candidate_weights():
    """Fixed before seeing test results, near-parent diverse 8-pillar grid."""
    rng=np.random.default_rng(137)
    cand=[FROZEN.copy()]
    for i in range(8):
        w=FROZEN.copy();w[i]*=2.5;w/=w.sum();cand.append(w)
        w=FROZEN.copy();w[i]*=.35;w/=w.sum();cand.append(w)
    for width in (.15,.35,.65):
        for z in rng.dirichlet(14*FROZEN,size=32):
            w=(1-width)*FROZEN+width*z
            cand.append(w/w.sum())
    return np.asarray(cand,dtype=np.float32)

CANDIDATES=candidate_weights()


def learn_weights(training,mode,broad):
    """Only completed PRIOR races. One win-bet at historical SP per race."""
    if len(training)<MIN_LOCAL:return None
    combos=CANDIDATES.copy()
    ix=4 if broad=='NH' else 7
    combos[:,ix]=0.
    combos/=combos.sum(axis=1)[:,None]
    wins=np.zeros(len(combos));returns=np.zeros(len(combos));priced=np.zeros(len(combos))
    for race in training:
        scores=race['x8']@combos.T
        idx=np.argmax(scores,axis=0)
        won=idx==race['winner_idx'];wins+=won
        sp=np.asarray(race['sp'])[idx]
        good=np.isfinite(sp)&(sp>1)&(sp<1001)
        priced+=good
        returns+=np.where(good & won,np.minimum(sp,21.),0.)
    if mode=='SR':
        # Maximise prior strike rate, shrink excessively concentrated weights.
        obj=wins/len(training) - .008*np.sum((combos-mask_weights(FROZEN,broad))**2,axis=1)
    else:
        # Past capped-SP reward, with a strong strike-rate preference and
        # complexity penalty to reduce sensitivity to long-priced outliers.
        roi=np.divide(returns,np.maximum(priced,1))-1.
        obj=roi + .35*(wins/len(training)) - .04*np.sum((combos-mask_weights(FROZEN,broad))**2,axis=1)
        obj=np.where(priced>=max(75,.7*len(training)),obj,-1e6)
    best=int(np.argmax(obj))
    candidate_metrics=[{'candidate_id':i,'training_races':len(training),
      'training_wins':int(wins[i]),'training_strike_pct':round(100*wins[i]/len(training),3),
      'priced':int(priced[i]),'training_capped_sp_roi_pct':round(100*(returns[i]/priced[i]-1),3) if priced[i] else None,
      'training_objective':round(float(obj[i]),6)} for i in range(len(combos))]
    return {'candidate_metrics':candidate_metrics,'weights':combos[best].copy(),'candidate_id':best,'in_sample_races':len(training),
            'in_sample_wins':int(wins[best]),'in_sample_roi_sp_capped_pct':
              round(100*(returns[best]/priced[best]-1),2) if priced[best] else None,
            'objective':float(obj[best])}


def hierarchy(race):
    return tuple(dict.fromkeys((race['subtype'],race['group'],race['broad'],'ALL')))


def fit_weight_banks(training):
    subsets=defaultdict(list)
    for r in training:
        for key in hierarchy(r):subsets[key].append(r)
    banks={}
    for k,rs in subsets.items():
        if len(rs)<MIN_LOCAL:continue
        broad=rs[0]['broad'] if k!='ALL' else 'NH' # For ALL fallback use global unmasked weights.
        if k=='ALL':continue
        banks[k]={x:learn_weights(rs,x,broad) for x in ('SR','ROI')}
    return banks


def select_weights(bank,race,mode):
    for key in hierarchy(race):
        got=bank.get(key,{}).get(mode)
        if got:return got['weights'],key,got
    return mask_weights(FROZEN,race['broad']), 'FROZEN_FALLBACK',None


class PriorCommentNLP:
    """Online supervised word/bigram NLP. No future-day updates or external API.

    Text vectoriser encodes negation/context bigrams beyond fixed keyword matching,
    but this is *not* a large language model or true human comprehension.
    """
    def __init__(self):
        from sklearn.feature_extraction.text import HashingVectorizer
        from sklearn.linear_model import SGDRegressor
        self.vectorizer=HashingVectorizer(n_features=2**15,ngram_range=(1,2),
                         alternate_sign=True,strip_accents='unicode',norm='l2')
        self.model=SGDRegressor(loss='squared_error',penalty='l2',alpha=.002,
                    learning_rate='invscaling',eta0=.03,random_state=137)
        self.trained=0
    def learn(self,examples):
        usable=[(text,float(q)) for text,q in examples if text and q is not None and math.isfinite(float(q))]
        if not usable:return
        x=self.vectorizer.transform([a for a,_ in usable]);y=np.asarray([b for _,b in usable],dtype=float)
        self.model.partial_fit(x,y)
        self.trained+=len(usable)
    def predict(self,texts):
        valid=[sanitize_comment(v) for v in texts if sanitize_comment(v)]
        if not valid or self.trained<100:return None,0
        x=self.vectorizer.transform(valid)
        p=np.clip(self.model.predict(x),0,1)
        return float(np.mean(p)),len(valid)


class Form:
    def __init__(self):
        self.h=defaultdict(lambda:deque(maxlen=8))
        # A later opponent's first subsequent result is released to form only
        # after its completed race. Exactly the G5 idea, not the frozen G5 code.
        self.race_opponents={}
        self.nextout={}
    def features(self,key,asof,info,nlp,code):
        previous=[p for p in self.h.get(key,()) if p['date']<asof]
        last=previous[-3:];allq=[p['quality'] for p in previous[-5:] if p['quality'] is not None]
        nl,nlc=nlp.predict([p['comment'] for p in last])
        pe=[p for p in last if p['disc']==code]
        rivals=[];total_rivals=0
        for prior in last:
            others=[k for k in self.race_opponents.get(prior.get('race_id'),()) if k!=key]
            total_rivals+=len(others)
            for other in others:
                event=self.nextout.get((prior.get('race_id'),other))
                if event and event['date']<asof:rivals.append(event)
        now_tier=fnum(info.get('official_tier_score'))
        past_tier=avg([p['tier'] for p in last])
        current_segment=info.get('classification_segment')
        same=[p['tier'] for p in last if p['segment']==current_segment and p['tier'] is not None]
        delta=(now_tier-avg(same)) if same and now_tier is not None else None
        last_type=previous[-1]['type'] if previous else ''
        hcap=bool(info.get('is_handicap'))
        return {
          'horse_form_quality3':avg([p['quality'] for p in last]),
          'horse_form_trend':float(allq[-1]-avg(allq[:-1])) if len(allq)>=2 else None,
          'horse_beaten3':avg([p['beaten'] for p in last]),
          'horse_beaten_per_mile3':avg([p['beaten']/max(0.5,p['distance_f']/8.)
                                     for p in last if p['beaten'] is not None and p['distance_f'] is not None]),
          'horse_jumping_comment3':avg([p['jump_note'] for p in last]),
          'horse_interference3':avg([p['interference'] for p in last]),
          'horse_comment_phrase3':avg([p['phrase'] for p in last]),
          'horse_comment_nlp3':nl,'horse_nlp_coverage':nlc/max(1,len(last)),
          'horse_runs':len(previous),'last_run_days':(dt.date.fromisoformat(asof)-dt.date.fromisoformat(previous[-1]['date'])).days if previous else None,
          'past_grade_strength3':past_tier,
          'past_known_tier_fraction':sum(p['tier'] is not None for p in last)/len(last) if last else None,
          'past_opposition_median3':avg([p['opponent_or'] for p in last]),
          'same_code_form3':avg([p['quality'] for p in pe]),
          'maiden_to_handicap':int(hcap and 'MAIDEN' in last_type),
          'novice_to_handicap':int(hcap and 'NOVICE' in last_type),
          'beginner_to_handicap':int(hcap and 'BEGINNER' in last_type),
          'last3_maiden_fraction':sum('MAIDEN' in p['type'] for p in last)/len(last) if last else None,
          'last3_novice_fraction':sum('NOVICE' in p['type'] for p in last)/len(last) if last else None,
          'past_vs_current_tier_delta':delta,
          'prior_rivals_nextout_quality3':avg([z['quality'] for z in rivals]),
          'prior_rivals_nextout_winrate3':avg([z['won'] for z in rivals]),
          'prior_rivals_nextout_count3':len(rivals),
          'prior_rivals_nextout_coverage3':len(rivals)/total_rivals if total_rivals else None,
          'form_quality_confident':int(len([p for p in last if p['quality'] is not None])>=2),
        }
    def add(self,rows,asof,info,field_or):
        import stage108_model_g2 as G
        import stage30_gap_aware_abcd as D
        starter_rows=[z for z in rows if is_starter(z)]
        participants=[ident(r) for r in starter_rows if ident(r)]
        for r in starter_rows:
            key=ident(r)
            if not key:continue
            comment=sanitize_comment(r[22])
            b=G.beaten_lengths(r[23]);q=G.quality({'finish_pos':r[12],'outcome':r[13],'beaten':r[23]},len(starter_rows))
            # The FIRST next-out result corroborates or weakens the previous
            # contest only when that next-out race has actually finished.
            prev=self.h[key][-1] if self.h[key] else None
            if prev and (prev['race_id'],key) not in self.nextout:
                self.nextout[(prev['race_id'],key)]={'date':asof,'quality':q,'won':int(fnum(r[12])==1)}
            self.h[key].append({'date':asof,'race_id':str(r[0]),'quality':q,'beaten':b,
              'distance_f':fnum(r[5]),
              'jump_note':D.jumping_note(comment) if info.get('discipline') in ('HURDLE','CHASE') else None,
              'interference':int(bool(re.search(r'\b(?:hampered|short of room|blocked|bumped|checked|denied a clear run)\b',comment,re.I))) if comment else None,
              'comment':comment,'phrase':G.comment_signal(comment) if comment else None,
              'tier':fnum(info.get('official_tier_score')),
              'segment':info.get('classification_segment'),
              'type':info.get('race_experience_type') or 'UNKNOWN',
              'disc':info.get('discipline'),'opponent_or':field_or})
        self.race_opponents[str(rows[0][0])]=participants
        return [(sanitize_comment(r[22]),G.quality({'finish_pos':r[12],'outcome':r[13],'beaten':r[23]},len(starter_rows))) for r in starter_rows]


def is_starter(r):
    import stage30_gap_aware_abcd as D
    return D.started(r)


def read_races(db,first='2022-01-01',last='2025-12-31'):
    cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    cx.execute('PRAGMA temp_store=MEMORY')
    sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
       u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
       u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment,u.beaten,u.weight_lbs
       FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
       WHERE r.race_date BETWEEN ? AND ? ORDER BY r.race_date,r.id'''
    cur=cx.execute(sql,(first,last))
    rows=[];prev=None
    for row in cur:
        key=(row[1],row[0]);
        if prev is not None and key!=prev:
            yield rows;rows=[]
        rows.append(row);prev=key
    if rows:yield rows
    cx.close()


def construct(db,ref_path):
    import stage30_gap_aware_abcd as D
    reference,refaudit=read_reference(ref_path)
    dstate=D.Past({});form=Form();nlp=PriorCommentNLP()
    races=[];audit=collections.Counter();pending=[];current_day=None
    held_comments=[];nlp_quarter=None
    def apply_day():
        if not pending:return
        comments=[]
        for r,info,fieldor in pending:
            dstate.update(r)
            comments.extend(form.add(r,r[0][1],info,fieldor))
        # Only the training period (2022-23) updates NLP daily. During every
        # 2024/25 evaluation quarter NLP parameters stay FROZEN; its labels
        # become training examples only once that quarter is completed.
        if current_day is not None and current_day>='2024-01-01':
            held_comments.extend(comments)
        else: nlp.learn(comments)
    for entries in read_races(db):
        day=entries[0][1]
        if current_day is not None and day!=current_day:
            apply_day();pending=[]
        current_day=day
        new_quarter=(day[:4]+'Q'+str((int(day[5:7])-1)//3+1)) if day>='2024-01-01' else 'TRAINING'
        if nlp_quarter is not None and new_quarter!=nlp_quarter and held_comments:
            nlp.learn(held_comments)
            held_comments=[]
        nlp_quarter=new_quarter
        rid=str(entries[0][0]);ref=reference.get(rid)
        if not ref:
            audit['unreferenced']+=1;continue
        rd,venue,rtype,title,region=ref
        if day!=rd or D.track(entries[0][2])!=D.track(venue):
            audit['conflicting_reference']+=1;continue
        if region not in ('GB','IRELAND') or D.track(entries[0][2]) not in D.TRACKS:
            audit['foreign_or_unknown_jurisdiction']+=1;continue
        audit['verified_races']+=1
        starters=[r for r in entries if is_starter(r)]
        info,broad,group,subtype=race_catalogue(entries[0],region,title,starters)
        med=info.get('preoff_opposition_median_or');field_or=fnum(med)
        if len(starters)<2:
            audit['small_fields']+=1;continue
        old=dstate.score(entries)
        if old is not None and day>='2023-01-01' and len(starters)>=4:
            features=[];key_ok=True
            ormean=avg([r[16] for r in starters if fnum(r[16]) is not None]);wtmean=avg([r[24] for r in starters if fnum(r[24]) is not None])
            for idx,r in enumerate(starters):
                key=ident(r)
                if not key:key_ok=False;break
                d8=old['x'][idx,:8].astype(float)
                x=dict(zip(PILLARS,d8));x['frozen_d_score']=float(d8@mask_weights(FROZEN,'NH' if old['kind']=='jumps' else 'FLAT'))
                x.update(form.features(key,day,info,nlp,info.get('discipline')))
                x.update({'current_tier_strength':fnum(info.get('official_tier_score')),
                  'current_tier_known':int(info.get('known_official_tier',False)),
                  'current_opposition_median':field_or,
                  'current_opposition_known':int(field_or is not None),
                  'current_race_experience_stage':fnum(info.get('experience_stage')),
                  'current_handicap':int(bool(info.get('is_handicap'))),
                  'current_gr1':int(info.get('classification_code') in ('NH_GRADE_1','FLAT_GROUP_1')),
                  'current_gr2':int(info.get('classification_code') in ('NH_GRADE_2','FLAT_GROUP_2')),
                  'current_gr3':int(info.get('classification_code') in ('NH_GRADE_3','FLAT_GROUP_3')),
                  'field_size':len(starters),'official_rating':fnum(r[16]),
                  'weight_lbs':fnum(r[24]),
                  'relative_official_rating':fnum(r[16])-ormean if fnum(r[16]) is not None and ormean is not None else None,
                  'relative_weight_lbs':fnum(r[24])-wtmean if fnum(r[24]) is not None and wtmean is not None else None,
                  'nlp_model_trained_comments':min(100000,nlp.trained)/100000})
                features.append([x.get(k) if x.get(k) is not None else np.nan for k in FEATURES])
            if key_ok and len(features)==len(starters) and len(starters)==len(old['horse']):
                races.append({'date':day,'race_id':rid,'course':entries[0][2],
                    'race_name':title,'country':region,'kind':info['discipline'],
                    'type':info.get('race_experience_type'), 'official_tier':info.get('classification_code'),
                    'broad':broad,'group':group,'subtype':subtype,'d_original_style':old['kind'],
                    'x8':old['x'][:,:8].astype(np.float32),
                    'xf':np.asarray(features,dtype=np.float32),
                    'win':np.asarray([int(x[12]==1) for x in starters],dtype=np.int8),
                    'sp':np.asarray([fnum(x[20]) or np.nan for x in starters]),
                    'horse':list(old['horse']),'identity':[ident(r) for r in starters],
                    'winner_idx':int(old['winning_index'])})
                audit['eligible_model_races']+=1
                if ('NH' if old['kind']=='jumps' else 'FLAT')!=('NH' if broad=='NH' else 'FLAT'):
                    audit['baseline_vs_reclassified_discipline_mismatch']+=1
                if info.get('known_official_tier'):audit['known_tier_races']+=1
                if info.get('race_experience_type')!='UNKNOWN':audit['known_type_races']+=1
                if info.get('discipline')=='CHASE' and subtype=='CHASE_GRADE_1':audit['grade1_chases']+=1
            else:audit['missing_ident_or_field_disagreement']+=1
        pending.append((entries,info,field_or))
        if audit['verified_races']%10000==0:print('Historical races: verified',audit['verified_races'],'eligible',audit['eligible_model_races'], 'NLP training comments',nlp.trained,flush=True)
    if pending:apply_day()
    return races,dict(audit),refaudit


def flatten(races):
    x=np.vstack([r['xf'] for r in races]).astype(np.float32)
    y=np.concatenate([r['win'] for r in races])
    assert len(x)==len(y)
    return x,y


def trained_classifier(train):
    from sklearn.ensemble import HistGradientBoostingClassifier
    x,y=flatten(train)
    if len(x)<1000 or len(set(y.tolist()))!=2:return None
    clf=HistGradientBoostingClassifier(max_iter=70,max_leaf_nodes=12,max_depth=4,
             min_samples_leaf=85,l2_regularization=15.,learning_rate=.05,
             early_stopping=True,validation_fraction=.12,n_iter_no_change=12,random_state=137)
    clf.fit(x,y)
    return clf


def train_banks(train):
    """Hierarchical fallback: exact subtype -> code -> broad -> pooled."""
    banks={}
    banks['ALL']=trained_classifier(train)
    subsets=defaultdict(list)
    for r in train:
        for group in dict.fromkeys((r['broad'],r['group'],r['subtype'])):subsets[group].append(r)
    for group,rows in subsets.items():
        if group=='ALL' or len(rows)<MIN_ML_LOCAL_RACES:continue
        if group in banks:continue
        banks[group]=trained_classifier(rows)
    return {key:val for key,val in banks.items() if val is not None}


def predict_bank(banks,r):
    for group in hierarchy(r):
        if group in banks:
            p=banks[group].predict_proba(r['xf'])[:,1]
            return np.asarray(p),group
    raise RuntimeError('No supported pooled ML bank')


def metrics(picks):
    n=len(picks);win=sum(p['won'] for p in picks);priced=[p for p in picks if p['sp'] is not None and 1.<p['sp']<1001]
    ret=sum(p['sp'] for p in priced if p['won'])
    cap=sum(min(p['sp'],21) for p in priced if p['won'])
    return {'races':n,'winners':win,'strike_pct':round(win/n*100,2) if n else None,
       'priced':len(priced),'price_coverage_pct':round(100*len(priced)/n,2) if n else None,
       'roi_sp_pct':round(100*(ret/len(priced)-1),2) if priced else None,
       'roi_sp_capped_20_1_pct':round(100*(cap/len(priced)-1),2) if priced else None}


def make_summary(picks):
    groups=defaultdict(list)
    for p in picks:
        for key,group in [('ALL','ALL'),('YEAR',p['date'][:4]),('QUARTER',p['period']),
                          ('BROAD',p['broad']),('CODE',p['group']),('RACE_TYPE',p['subtype']),
                          ('EXPERIENCE',p['type']),('OFFICIAL_TIER',p['official_tier']),
                          ('YEAR_BROAD',p['date'][:4]+'|'+p['broad']),
                          ('BROAD_TYPE',p['broad']+'|'+p['subtype'])]:
            groups[(p['model'],key,group)].append(p)
    for p in picks:
        if p.get('frozen_gate_eligible'):
            groups[(p['model'],'FROZEN_D_GATED','ALL')].append(p)
            groups[(p['model'],'FROZEN_D_GATED_BROAD',p['broad'])].append(p)
    return [{'model':k[0],'breakdown':k[1],'group':k[2],**metrics(v)} for k,v in sorted(groups.items())]


def run_walk(races,out):
    train_first=[r for r in races if '2023-01-01'<=r['date']<='2023-12-31']
    if len(train_first)<500:raise RuntimeError('Insufficient 2023 training samples')
    predictions=[];weights_rows=[];candidate_rows=[];fold_audit=[]
    for period,start,end in PERIODS:
        train=[r for r in races if '2023-01-01'<=r['date']<start]
        test=[r for r in races if start<=r['date']<=end]
        if len(test)<200:raise RuntimeError(f'Incomplete quarter {period}: {len(test)} eligible races')
        print('Training quarter',period,'using',len(train),'prior races; test',len(test),flush=True)
        banks=fit_weight_banks(train)
        ml=train_banks(train)
        training_through=str(dt.date.fromisoformat(start)-dt.timedelta(days=1))
        fold_audit.append({'period':period,'train_through':training_through,
         'train_races':len(train),'quarter_races':len(test),
         'weights_groups':sorted(banks),'trained_nlp_specialists':sorted(ml)})
        for group,mods in banks.items():
            for opt,obj in mods.items():
                if not obj:continue
                for item in obj['candidate_metrics']:
                    candidate_rows.append({'period':period,'training_through':training_through,
                       'trained_group':group,'target':opt,'selected_for_next_quarter':int(item['candidate_id']==obj['candidate_id']),**item})
                weights_rows.append({'period':period,'training_through':training_through,'trained_group':group,
                    'target':opt,'candidate':obj['candidate_id'],'n_train_races':obj['in_sample_races'],
                    'in_sample_wins':obj['in_sample_wins'],
                    'in_sample_roi_sp_capped_pct':obj['in_sample_roi_sp_capped_pct'],
                    **{p:round(float(v),6) for p,v in zip(PILLARS,obj['weights'])}})
        for r in test:
            # Exact Stage30 D style mask, even if newly recovered NH Flat
            # metadata corrects a historical flat/jump classification.
            oldstyle='NH' if r['d_original_style']=='jumps' else 'FLAT'
            alternatives={'FROZEN_D':(r['x8']@mask_weights(FROZEN,oldstyle),'FROZEN_D')}
            for opt in ('SR','ROI'):
                w,source,details=select_weights(banks,r,opt)
                alternatives['D_PILLAR_'+opt]=(r['x8']@w,source)
            p,source=predict_bank(ml,r)
            alternatives['D_RACETYPE_NLP']=(p,source)
            frozen_vals=alternatives['FROZEN_D'][0]
            frozen_lead=float(np.sort(frozen_vals)[-1]-np.sort(frozen_vals)[-2])
            frozen_gate=int(frozen_lead>=.14)
            for model,(vals,source) in alternatives.items():
                idx=int(np.argmax(vals))
                second=int(np.argsort(-vals,kind='stable')[1])
                sp=float(r['sp'][idx]) if np.isfinite(r['sp'][idx]) else None
                predictions.append({'period':period,'date':r['date'],'race_id':r['race_id'],
                 'course':r['course'],'country':r['country'],'race_name':r['race_name'],
                 'discipline':r['kind'],'broad':r['broad'],'group':r['group'],
                 'subtype':r['subtype'],'type':r['type'],'official_tier':r['official_tier'],
                 'model':model,'bank':source,'horse':r['horse'][idx],
                 'second_horse':r['horse'][second], 'selected_score':round(float(vals[idx]),6),
                 'sp':sp,'won':int(idx==r['winner_idx']),
                 'winner':r['horse'][r['winner_idx']],
                 'rank2_won':int(second==r['winner_idx']),
                 'runner_count':len(r['horse']),'trained_through':training_through,
                 'frozen_d_score_lead':round(frozen_lead,6),'frozen_gate_eligible':frozen_gate})
        print('Completed',period,{'m':len(ml),'w':len(banks),'preds':len(test)*len(MODELS)},flush=True)
    sums=make_summary(predictions)
    write_csv(out/'stage137-quarterly-predictions.csv.gz',predictions,compressed=True)
    write_csv(out/'stage137-period-discipline-racetype.csv',sums)
    write_csv(out/'stage137-optimized-eight-pillar-weights.csv',weights_rows)
    write_csv(out/'stage137-all-candidate-pillar-results.csv.gz',candidate_rows,compressed=True)
    (out/'stage137-checkpoint-audit.json').write_text(json.dumps(fold_audit,indent=2)+'\n')
    return predictions,sums,fold_audit


def write_csv(path,rows,compressed=False):
    import gzip
    if not rows:raise RuntimeError('Empty output '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    op=gzip.open if compressed else open
    with op(path,'wt',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),extrasaction='ignore');w.writeheader();w.writerows(rows)


def research(db,reference,out):
    import stage30_gap_aware_abcd as D
    global CANDIDATES
    orig=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert orig['chosen_combination']==FROZEN_ID and abs(float(orig['selection_margin'])-.14)<1e-7
    # Recover the actual unrounded frozen weights, not a reconstruction from
    # the published two-decimal percentage display.
    exact=np.asarray(D.candidates('D')[64],dtype=float)
    exact/=exact.sum()
    assert np.allclose([orig['weights_pct'][k] for k in PILLARS],exact*100,atol=.011), 'Frozen D weights altered'
    FROZEN[:]=exact
    CANDIDATES=candidate_weights()
    races,audit,refs=construct(db,reference)
    if len(races)<15000:raise RuntimeError(f'Too few exact UK/IE race fields for full walk-forward: {len(races)}')
    assert all(not (p['date']<'2023-01-01' or p['date']>'2025-12-31') for p in races)
    out.mkdir(parents=True,exist_ok=True)
    predictions,sums,fold=run_walk(races,out)
    summary={'version':VERSION,'status':'PAPER_ONLY_FROZEN_D_RETAINED',
      '2023_train_races':len([r for r in races if r['date'][:4]=='2023']),
      'evaluation_quarters':[x[0] for x in PERIODS],
      'evaluated_races':sum(p['model']=='FROZEN_D' for p in predictions),
      'actual_frozen_d_id':FROZEN_ID,'actual_frozen_d_weights':dict(zip(PILLARS,map(float,FROZEN))),
      'source_audit':audit,'reference_audit':refs,
      'overall':{m:next(x for x in sums if x['model']==m and x['breakdown']=='ALL') for m in MODELS},
      'checkpoint_coverage':fold,'live_models_modified':False,'no_odds_as_candidate_features':True,
      'roi_training_uses_only_past_final_sp':True,
      'caveats':['The historical original D weight combination was selected using 2024 results, so comparison in 2024 is not a pristine frozen-baseline holdout.',
       'The NLP component is online-trained word/bigram language regression from previously completed comments: NOT an LLM with full semantic comprehension.',
       'When official tier is not proven, it remains unknown and grade-specific specialists fall back to broader models.',
       'ROI of a simulated 1 unit win bet at final SP is a hindsight benchmark, not a tradable price.',
       'Repeated quarterly testing is prequential for the challenger, but model structures and features were developed after viewing other historical data.']}
    (out/'stage137-summary.json').write_text(json.dumps(summary,indent=2,default=str)+'\n')
    print('STAGE137_SUMMARY',json.dumps({'counts':audit,'overall':summary['overall']},default=str),flush=True)
    return summary


def self_test():
    assert len(PILLARS)==8
    assert FROZEN_ID=='D-064-gap14' and abs(FROZEN.sum()-1)<1e-10
    assert len(PERIODS)==8 and PERIODS[0]==('2024Q1','2024-01-01','2024-03-31')
    assert PERIODS[-1]==('2025Q4','2025-10-01','2025-12-31')
    assert np.allclose(mask_weights(FROZEN,'NH')[4],0.)
    assert np.allclose(mask_weights(FROZEN,'AW')[7],0.)
    e1={'date':'2023-01-02','broad':'NH','x8':np.asarray([[1,0,0,0,0,0,0,0],[0,1,0,0,0,0,0,0]],dtype=float),'winner_idx':0,'sp':[4.,3.]}
    mini=[e1]*150
    for mode in ('SR','ROI'):
        x=learn_weights(mini,mode,'NH')
        assert x and len(x['weights'])==8 and x['in_sample_races']==150
    # Verify official grade + development stage remain separate and type-specific.
    import stage136_race_type_strength as T
    d,b,g,s=race_catalogue(('R','2024-01-01','Ascot','Chase','Turf',24,'Good','Class 1'),
       'GB','Grade 1 Novice Chase',[(None,)*24]*5)
    assert s=='CHASE_GRADE_1' and d['race_experience_type']=='NOVICE',(s,d)
    d,b,g,s=race_catalogue(('R','2024-01-01','Ascot','Hurdle','Turf',16,'Good','Class 4'),
       'GB','Class 4 Handicap Hurdle',[(None,)*24]*5)
    assert s=='HURDLE_HANDICAP'
    # NLP is trained only on completed-comment examples, predicts different language.
    nlp=PriorCommentNLP()
    nlp.learn([('stayed on strongly',.85),('weakened badly',.1)]*55)
    v,n=nlp.predict(['kept on strongly','hampered and weakened'])
    assert v is not None and n==2 and nlp.trained==110
    print('PASS STAGE137: frozen 8 pillars, split dates, subtype/tier separation, guarded NLP, weight search')


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
    sub.add_parser('self-test');q=sub.add_parser('walkforward')
    q.add_argument('--db',type=pathlib.Path,required=True);q.add_argument('--reference',type=pathlib.Path,required=True)
    q.add_argument('--out',type=pathlib.Path,required=True)
    args=p.parse_args()
    if args.mode=='self-test':self_test();return
    research(args.db,args.reference,args.out)

if __name__=='__main__':main()
