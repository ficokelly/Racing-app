#!/usr/bin/env python3
"""Stage 30: gap-aware A/B/C/D research-only historical optimisation.
Only pre-race, previous-calendar-day evidence is scored. 2023 development,
2024 validation/weight selection, untouched 2025 results. SP settles only.
D uses EXACTLY Ability/Trainer/Jockey/Course/Draw/Distance/Ground/Jumping.
A/B/C include a ninth prior comparable-race trend signal, retaining their
historical/trend-oriented identities. These are *challenger* configurations;
no live model is overwritten or represented as retrained in place.
"""
from __future__ import annotations
import argparse, collections as C, csv, datetime as dt, json, math, re, sqlite3
from pathlib import Path
import numpy as np

METRICS=('Ability','Trainer','Jockey','Course','Draw','Distance','Ground','Jumping')
TRACKS=set('''Aintree|Ascot|Ayr|Ballinrobe|Bangor-on-Dee|Bath|Bellewstown|Beverley|Brighton|Carlisle|Cartmel|Catterick|Chelmsford|Cheltenham|Chepstow|Chester|Clonmel|Cork|Curragh|Doncaster|Down Royal|Downpatrick|Dundalk|Epsom|Exeter|Fairyhouse|Fakenham|Ffos Las|Fontwell|Galway|Goodwood|Gowran Park|Hamilton|Haydock|Hereford|Hexham|Huntingdon|Kelso|Kempton|Kilbeggan|Killarney|Leicester|Leopardstown|Limerick|Lingfield|Listowel|Ludlow|Market Rasen|Musselburgh|Naas|Navan|Newbury|Newcastle|Newmarket|Newton Abbot|Nottingham|Perth|Plumpton|Pontefract|Punchestown|Redcar|Ripon|Roscommon|Salisbury|Sandown|Sedgefield|Sligo|Southwell|Stratford|Taunton|Thirsk|Thurles|Tipperary|Tramore|Uttoxeter|Warwick|Wetherby|Wexford|Wincanton|Windsor|Wolverhampton|Worcester|Yarmouth|York'''.lower().split('|'))
TRACKS.update(('catterick bridge','sandown park','newmarket july','epsom downs','the curragh'))
FOREIGN=re.compile(r'\((?:AUS|USA|FR|GER|HK|BHR|QA|UAE|ITY|ARG|URU|JER|SWI|SWE|SAF)\)',re.I)

def normal(x):return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 -]','',str(x or '').lower())).strip()
def track(x):
    if FOREIGN.search(str(x or '')):return '!foreign'
    return normal(re.sub(r'\([^)]*\)','',str(x or '')))
def number(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (ValueError,TypeError):return None

def date(x):
    try:return dt.date.fromisoformat(str(x)[:10])
    except (ValueError,TypeError):return None

def style(r):
    typ=normal(r[3]);surf=normal(r[4]);name=str(r[2] or '').lower()
    if any(q in typ for q in ('hurdle','chase','steeple','national hunt','jumps','nhflat','bumper')):return 'jumps'
    if '(aw)' in name or any(q in surf for q in ('tapeta','polytrack','allweather','synthetic','aw')):return 'aw'
    return 'flat'
def going(s):
    x=normal(s)
    if any(v in x for v in ('standard','tapeta','polytrack')):return 'standard'
    if 'heavy' in x:return 'heavy'
    if 'soft' in x or 'yielding' in x:return 'soft'
    if 'firm' in x:return 'firm'
    if 'good' in x:return 'good'
    return ''
def distband(s):
    d=number(s)
    return int(d//2)*2 if d and d>=4 else None

def started(r):
    result=normal(r[13])
    if any(s in result for s in ('nonrunner','non runner','scratched','withdrawn','did not start')):return False
    return r[12] is not None or (number(r[20]) or 0)>1 or bool(result and result!='nr')
def jumping_note(s):
    t=normal(s)
    if any(x in t for x in ('fell','unseated','blunder','mistake','pecked','jumped poorly')):return 0
    if any(x in t for x in ('jumped well','jumped fluently','jumped soundly','jumped cleanly','good jump')):return 1
    return None

def add(stats,key,win,placed):
    if key is None or any(x is None or x=='' or x==('', '') for x in key[1:]):return
    v=stats.get(key)
    if v is None:stats[key]=[1,int(win),int(placed)]
    else:v[0]+=1;v[1]+=int(win);v[2]+=int(placed)

def feature(stats,lookups,base,prior=24):
    """Posterior-shrunk log win-rate lift; back off through supplied evidence tiers."""
    numerator=denominator=0.;tag='population_prior';count=0
    for key,weight,label in lookups:
        if weight<=0 or key is None:continue
        n,w,_=stats.get(key,(0,0,0))
        if not n:continue
        prob=(w+base*prior)/(n+prior)
        lift=math.log(max(.004,prob)/max(.004,base))
        strength=weight*n/(n+prior)
        numerator+=strength*lift;denominator+=strength;count+=n
        if tag=='population_prior':tag=label
    return (max(-1.8,min(1.8,numerator/denominator)) if denominator else 0.),tag,count

def named_map(path):
    if not path or not Path(path).exists():return {},{'status':'not_available'}
    with open(path,encoding='utf-8-sig',errors='replace',newline='') as f:
        rows=csv.DictReader(f);cols=rows.fieldnames or []
        race=next((x for x in cols if normal(x).replace(' ','_') in ('race_id','historical_race_id','source_race_id')),None)
        event=next((x for x in cols if 'event' in normal(x) and 'id' in normal(x)),None)
        if not race or not event:return {},{'status':'unrecognised_format','columns':cols[:30]}
        lookup={str(row[race]):str(row[event]) for row in rows if row.get(race) and row.get(event) and normal(row[event]) not in ('none','null','unknown')}
    return lookup,{'status':'loaded','links':len(lookup),'race_col':race,'event_col':event}

class Past:
    def __init__(self,mapping):
        self.s={};self.last={};self.last_trainer={};self.map=mapping;self.audit=C.Counter();self.horse_recent=C.defaultdict(lambda:C.deque(maxlen=5));self.trainer_recent=C.defaultdict(C.deque)
    def identity(self,r):return (normal(r[8]),normal(r[10]))
    def score(self,rows):
        first=rows[0];day=date(first[1]);kind=style(first);place=track(first[2]);distance=distband(first[5]);ground=going(first[6]);event=self.map.get(str(first[0]))
        starters=[r for r in rows if started(r)];winners=[i for i,r in enumerate(starters) if r[12]==1]
        if len(starters)<2 or len(winners)!=1:return None
        # Very old records are used for historical evidence, not repeatedly scored.
        if day.year not in (2023,2024,2025):return None
        base=1/len(starters);official=[number(r[16]) for r in starters]
        valid=[n for n in official if n and n>0];average=sum(valid)/len(valid) if valid else None
        vectors=[];sources=[];evidence_counts=[]
        for idx,r in enumerate(starters):
            h=self.identity(r);trainer=normal(r[14]);jockey=normal(r[15]);sire=normal(r[18]);dam=normal(r[19]);age=int(r[17]) if number(r[17]) and 1<=int(r[17])<=25 else None
            debut=h not in self.last;previous=self.last.get(h);break_days=(day-previous).days if previous else None
            layoff=bool(break_days is not None and break_days>=90)
            layoff_bin=('90-179' if break_days<180 else '180-364' if break_days<365 else '365+') if layoff else None
            trainer_changed=bool(h in self.last_trainer and trainer and self.last_trainer[h]!=trainer)
            ability,aa,abn=feature(self.s,[
                (('horse',h),3,'horse_previous_form'),
                (('horse_layoff_band',h,layoff_bin),3,'horse_after_similar_break') if layoff else (None,0,''),
                (('horse_layoff',h),2.5,'horse_previous_returns_from_break') if layoff else (None,0,''),
                (('sire_2yo_debut',sire),2,'sire_2yo_first_out_offspring') if debut and age==2 and sire else (None,0,''),
                (('dam_2yo_debut',dam),1.5,'dam_2yo_first_out_offspring') if debut and age==2 and dam else (None,0,''),
                (('sire',sire),.8,'sire_progeny_general') if sire else (None,0,''),
                (('dam',dam),.7,'dam_progeny_general') if dam else (None,0,''),
            ],base)
            # Recent form is a sub-signal within Ability (never a ninth Model D dimension).
            recent=self.horse_recent.get(h)
            if recent:
                nw=len(recent); nwin=sum(z[1] for z in recent); nplace=sum(z[2] for z in recent)
                recent_lift=math.log(max(.005,(nwin+base*4)/(nw+4))/base)
                ability += .35*max(-1.,min(1.,recent_lift))
                if aa=='population_prior':aa='horse_last_five_runs'
            if average is not None and official[idx] is not None and len(valid)>1:
                ability+=max(-.65,min(.65,(official[idx]-average)/40))
                if aa=='population_prior':aa='pre_race_official_rating_comparison'
            tr,tt,trn=feature(self.s,[
                (('trainer_debut2',trainer),3,'trainer_first_out_two_year_old') if age==2 and debut else (None,0,''),
                (('trainer_layoff_band',trainer,kind,layoff_bin),3.3,'trainer_after_similar_length_break') if layoff else (None,0,''),
                (('trainer_layoff',trainer,kind),2.5,'trainer_horses_after_90day_break') if layoff else (None,0,''),
                (('trainer_new_stable',trainer,kind),2.0,'trainer_first_start_after_stable_change') if trainer_changed else (None,0,''),
                (('trainer_style',trainer,kind),2,'trainer_race_discipline'),
                (('trainer_track',trainer,place),1.2,'trainer_course'),
                (('trainer',trainer),.6,'trainer_total')],base)
            # Trainer 30-day strike rate is calculated only from earlier racing dates.
            trrecent=self.trainer_recent.get(trainer)
            if trrecent:
                cutoff=day-dt.timedelta(days=30)
                while trrecent and trrecent[0][0]<cutoff:trrecent.popleft()
                if trrecent:
                    nrun=len(trrecent);nwin=sum(v[1] for v in trrecent)
                    tr+=.3*max(-1.,min(1.,math.log(max(.005,(nwin+base*20)/(nrun+20))/base)))
                    if tt=='population_prior':tt='trainer_30_day_form'
            jo,jj,jon=feature(self.s,[(('jockey_trainer',jockey,trainer),1.5,'jockey_trainer_record'),
                (('jockey_style',jockey,kind),2,'jockey_race_discipline'),(('jockey',jockey),.8,'jockey_total')],base)
            co,cc,con=feature(self.s,[(('horse_course',h,place),3,'horse_course'),
                (('sire_course',sire,place),1.3,'sire_progeny_course') if sire else (None,0,''),
                (('trainer_track',trainer,place),1.2,'trainer_course'),
                (('course_age',place,kind,age),.7,'comparable_age_course') if age else (None,0,'')],base)
            draw=number(r[21]);lane='inside' if draw and draw<=len(starters)/3 else 'outside' if draw and draw>len(starters)*2/3 else 'middle'
            dr,dd,drn=feature(self.s,[(('draw_track_trip',place,distance,lane),2.5,'course_trip_draw'),
                (('draw_style',kind,lane),.7,'race_type_draw')],base) if kind!='jumps' and draw and draw>=1 else (0.,'not_applicable_or_unavailable',0)
            dist,ds,dn=feature(self.s,[(('horse_dist',h,distance),3,'horse_distance'),
                (('sire_dist',sire,distance),1.5,'sire_progeny_distance') if sire else (None,0,''),
                (('dam_dist',dam,distance),1.0,'dam_progeny_distance') if dam else (None,0,''),
                (('trainer_dist',trainer,distance),.7,'trainer_at_distance')],base)
            gr,gg,gn=feature(self.s,[(('horse_going',h,ground),3,'horse_on_going'),
                (('sire_going',sire,ground),1.5,'sire_progeny_going') if sire else (None,0,''),
                (('dam_going',dam,ground),1,'dam_progeny_going') if dam else (None,0,''),
                (('trainer_going',trainer,ground),.6,'trainer_on_going')],base)
            jp,jn,jpn=feature(self.s,[(('horse_jumps',h),2.5,'horse_jump_form'),
                (('sire_jumps',sire),1.5,'sire_progeny_jumps') if sire else (None,0,''),
                (('trainer_style',trainer,'jumps'),1,'trainer_jumps')],base) if kind=='jumps' else (0.,'not_applicable',0)
            if kind=='jumps':
                n,good,_=self.s.get(('jump_technique',h),(0,0,0))
                if n:jp+=max(-.5,min(.5,(good+5.6)/(n+7)-.8));jn='horse_previous_jumping_comments'
            age_group=int(age//2)*2 if age else None
            trend,ts,tn=feature(self.s,[
                (('named_event_age',event,age_group),3,'exact_recurrent_named_race') if event and age_group else (None,0,''),
                (('course_age',place,kind,age),1.8,'same_course_race_type') if age else (None,0,''),
                (('style_age',kind,age),.8,'same_type_age') if age else (None,0,'')],base)
            vector=[ability,tr,jo,co,dr,dist,gr,jp,trend]
            labels=[aa,tt,jj,cc,dd,ds,gg,jn,ts]
            vectors.append(vector);sources.append(labels)
            evidence_counts.append([abn,trn,jon,con,drn,dn,gn,jpn,tn])
            self.audit['2025_runner_rows' if day.year==2025 else 'training_runner_rows']+=1
            if debut:self.audit['observed_debut']+=1
            if debut and age==2:self.audit['observed_2yo_debut']+=1
            if layoff:self.audit['returning_after_90_days']+=1
            if layoff and self.s.get(('horse_layoff',h)):self.audit['horse_specific_prior_layoff_evidence']+=1
            if debut and sire and self.s.get(('sire_2yo_debut',sire)):self.audit['sire_2yo_debut_evidence']+=1
            if debut and dam and self.s.get(('dam_2yo_debut',dam)):self.audit['dam_2yo_debut_evidence']+=1
            for name,label in zip((*METRICS,'Comparable-race trend'),labels):
                if label=='population_prior':self.audit[name+'_only_population_prior']+=1
                elif label not in ('not_applicable','not_applicable_or_unavailable'):self.audit[name+'_has_evidence']+=1
        return {'date':str(day),'year':day.year,'race_id':str(first[0]),'course':str(first[2]),'kind':kind,
            'x':np.asarray(vectors,dtype=np.float32),'sources':sources,'evidence_counts':evidence_counts,'horse':[r[9] for r in starters],
            'sp':np.asarray([number(r[20]) if number(r[20]) is not None else np.nan for r in starters]),
            'winning_index':winners[0],'exact_event':bool(event)}
    def update(self,rows):
        r=rows[0];day=date(r[1]);kind=style(r);place=track(r[2]);distance=distband(r[5]);ground=going(r[6]);event=self.map.get(str(r[0]));starters=[q for q in rows if started(q)]
        if len(starters)<2:return
        for q in starters:
            h=self.identity(q);trainer=normal(q[14]);jockey=normal(q[15]);sire=normal(q[18]);dam=normal(q[19]);age=int(q[17]) if number(q[17]) and 1<=int(q[17])<=25 else None
            old=self.last.get(h);debut=old is None;break_days=(day-old).days if old else None
            layoff=bool(break_days is not None and break_days>=90)
            layoff_bin=('90-179' if break_days<180 else '180-364' if break_days<365 else '365+') if layoff else None
            trainer_changed=bool(h in self.last_trainer and trainer and self.last_trainer[h]!=trainer)
            win=q[12]==1;placed=q[12] is not None and 1<=q[12]<=3
            keys=[('horse',h),('trainer',trainer),('trainer_style',trainer,kind),('jockey',jockey),
                ('jockey_style',jockey,kind),('jockey_trainer',jockey,trainer),('trainer_track',trainer,place),
                ('horse_course',h,place),('sire_course',sire,place),('course_age',place,kind,age),('style_age',kind,age),
                ('horse_dist',h,distance),('sire_dist',sire,distance),('dam_dist',dam,distance),('trainer_dist',trainer,distance),
                ('horse_going',h,ground),('sire_going',sire,ground),('dam_going',dam,ground),('trainer_going',trainer,ground),
                ('sire',sire),('dam',dam)]
            if layoff:keys += [('horse_layoff',h),('trainer_layoff',trainer,kind),
                                 ('horse_layoff_band',h,layoff_bin),('trainer_layoff_band',trainer,kind,layoff_bin)]
            if trainer_changed:keys += [('trainer_new_stable',trainer,kind)]
            if debut and age==2:keys += [('trainer_debut2',trainer),('sire_2yo_debut',sire),('dam_2yo_debut',dam)]
            if kind=='jumps':keys += [('horse_jumps',h),('sire_jumps',sire)]
            if event and age:keys += [('named_event_age',event,int(age//2)*2)]
            draw=number(q[21]);lane='inside' if draw and draw<=len(starters)/3 else 'outside' if draw and draw>len(starters)*2/3 else 'middle'
            if kind!='jumps' and draw and draw>=1:keys += [('draw_track_trip',place,distance,lane),('draw_style',kind,lane)]
            for key in keys:add(self.s,key,win,placed)
            if kind=='jumps':
                j=jumping_note(q[22])
                if j is not None:add(self.s,('jump_technique',h),j,j)
            self.last[h]=day
            if trainer:self.last_trainer[h]=trainer
            self.horse_recent[h].append((day,int(win),int(placed)))
            if trainer:
                # Bounded recent-form retention while processing very long archives.
                previous_form=self.trainer_recent[trainer]
                cutoff=day-dt.timedelta(days=30)
                while previous_form and previous_form[0][0]<cutoff:previous_form.popleft()
                previous_form.append((day,int(win),int(placed)))
    def scan(self,path):
        cx=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
        cx.execute('PRAGMA temp_store=MEMORY')
        sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
        u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
        u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment
        FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
        WHERE r.race_date < '2026-01-01' ORDER BY r.race_date,r.id'''
        samples={2023:[],2024:[],2025:[]};pending=[];rows=[];prior_day=None;prior_race=None
        seen=0;kept=0;excluded=0
        def finish_race(group):
            nonlocal kept,excluded
            if not group:return
            if track(group[0][2]) not in TRACKS:excluded+=1;return
            kept+=1
            result=self.score(group)
            if result:samples[result['year']].append(result)
            pending.append(group)
        for row in cx.execute(sql):
            day=row[1]
            if prior_day is not None and day!=prior_day:
                if rows:finish_race(rows);rows=[]
                for old in pending:self.update(old)
                pending=[]
            if rows and row[0]!=prior_race:finish_race(rows);rows=[]
            rows.append(row);prior_day=day;prior_race=row[0];seen+=1
            if seen%1_000_000==0:print('Rows scanned',seen,'UK/IE races',kept,flush=True)
        if rows:finish_race(rows)
        for old in pending:self.update(old)
        cx.close()
        return samples,{'runner_rows':seen,'uk_ie_races_scanned':kept,'non_uk_ie_races_excluded':excluded,
                        'evaluated_races':{str(k):len(v) for k,v in samples.items()},'gap_sources':dict(self.audit)}

def candidates(model):
    rng=np.random.default_rng(2711+ord(model));n=8 if model=='D' else 9
    bases={'A':[1.6,1,.8,1.3,.6,1.1,1,.8,2.2],
           'B':[1,1.4,.8,1.5,.6,1.3,1.1,.9,2.5],
           'C':[1]*9,'D':[1]*8}
    base=np.asarray(bases[model],dtype=float);pool=[base/base.sum(),np.ones(n)/n]
    for i in range(n):
        w=np.ones(n)*.2;w[i]=4;pool.append(w/w.sum())
    for level in (.7,1.5,4):
        pool.extend(rng.dirichlet(base*level,size=24))
    return [np.asarray(w,dtype=np.float32) for w in pool]

def prediction(data,weight,model):
    out=[]
    for race in data:
        active=weight.copy()
        if race['kind']=='jumps':active[4]=0.
        else:active[7]=0.
        active=active/max(1e-8,float(active.sum()))
        scores=(race['x'][:,:8] if model=='D' else race['x'])@active
        order=np.argsort(-scores,kind='stable');idx=int(order[0]);second=int(order[1]);gap=float(scores[idx]-scores[second])
        sp=float(race['sp'][idx]);out.append((race,idx,int(idx==race['winning_index']),sp,gap))
    return out

def evaluate(pred,gate):
    selected=[x for x in pred if x[4]>=gate]
    priced=[x for x in selected if math.isfinite(x[3]) and 1.01<=x[3]<=5000]
    n=len(selected);wins=sum(x[2] for x in selected);b=len(priced);profit=sum(x[3]*x[2]-1 for x in priced)
    capped=sum((min(x[3],21.) if x[2] else 0)-1 for x in priced)
    winners=sorted((x[3]-1 for x in priced if x[2]),reverse=True)
    without_largest=profit-winners[0] if winners else profit
    weekcount=C.Counter((date(x[0]['date']).isocalendar().year,date(x[0]['date']).isocalendar().week) for x in selected)
    return {'races':n,'wins':wins,'strike_pct':round(100*wins/n,3) if n else None,'priced_bets':b,
        'price_coverage_pct':round(100*b/n,2) if n else None,'profit_units':round(profit,3),
        'roi_pct':round(100*profit/b,3) if b else None,
        'roi_capped_20to1_pct':round(100*capped/b,3) if b else None,
        'roi_without_largest_winner_pct':round(100*without_largest/b,3) if b else None,
        'average_weekly_bets':round(n/52.14,2),
        'weeks_ge10':sum(v>=10 for v in weekcount.values())}

def fmt(n):return float(n) if n is not None else -500.
def report(samples,output,mapping_info,audit):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if min(len(samples[y]) for y in (2023,2024,2025))<500:
        raise RuntimeError('Not enough real UK/IE races in each split: '+str(audit['evaluated_races']))
    weight_cols=METRICS+('Comparable-race trend',)
    summary={};combos=[];allp=[];picked=[]
    for model in 'ABCD':
        print('Evaluating combinations for',model,flush=True)
        trials=[]
        for wi,w in enumerate(candidates(model)):
            forecasts={year:prediction(samples[year],w,model) for year in (2023,2024,2025)}
            allmetrics={year:evaluate(forecasts[year],0.) for year in (2023,2024,2025)}
            for gate in (0.,.04,.08,.14):
                selected={year:evaluate(forecasts[year],gate) for year in (2023,2024,2025)}
                v=selected[2024];t=selected[2023]
                # Strictly pre-2025 objective; selection-volume floor penalised, not forced.
                # Hard minimum sample size: never choose a '150% ROI on one bet' combination.
                # At least 520 actual selections in EACH of 2023 and 2024 for ~10/week.
                eligible=(min(t['races'],v['races'])>=520 and
                          min(fmt(v['price_coverage_pct']),fmt(t['price_coverage_pct']))>=85)
                objective=.65*fmt(v['roi_pct'])+.35*fmt(t['roi_pct'])
                if eligible:
                    # Robustness check: big-priced single winners cannot dominate the choice.
                    objective+=.10*min(0.,fmt(v['roi_capped_20to1_pct']))
                    objective+=.10*min(0.,fmt(v['roi_without_largest_winner_pct']))
                    objective-=max(0,40-v['weeks_ge10'])*.5
                else:
                    objective=-1_000_000+min(t['races'],v['races'])
                if gate==0:objective-=1
                combo=f'{model}-{wi:03d}-gap{int(100*gate):02d}'
                row={'model':model,'combination_id':combo,'selection_margin':gate,
                     'selection_score_based_only_on_2023_24':round(objective,3),'chosen_pre_2025':0}
                row.update({col+'_pct':round(100*float(w[k]),2) if k<len(w) else 0 for k,col in enumerate(weight_cols)})
                for year in (2023,2024,2025):
                    for cls,metrics in [('all',allmetrics[year]),('selected',selected[year])]:
                        row.update({f'{year}_{cls}_{k}':v for k,v in metrics.items()})
                trials.append((objective,combo,w,gate,row))
        best=max(trials,key=lambda z:z[0]);best[4]['chosen_pre_2025']=1
        rows=[z[4] for z in trials];combos.extend(rows)
        w=best[2];gate=best[3]
        summary[model]={'chosen_combination':best[1],'training_and_validation_objective':round(best[0],3),
            'weights_pct':{name:round(100*float(w[i]),2) for i,name in enumerate(weight_cols[:len(w)])},
            'selection_margin':gate,'all_races_2025':{k:v for k,v in best[4].items() if k.startswith('2025_all_')},
            'selected_races_2025':{k:v for k,v in best[4].items() if k.startswith('2025_selected_')},
            'combinations_tested':len(trials)}
        for race,index,won,sp,gap in prediction(samples[2025],w,model):
            q={'model':model,'date':race['date'],'race_id':race['race_id'],'course':race['course'],
               'type':race['kind'],'horse':race['horse'][index],'winner':won,
               'sp_decimal':round(sp,5) if math.isfinite(sp) else '',
               'score_lead':round(gap,6),'selected':int(gap>=gate),
               'category_evidence_json':json.dumps(dict(zip(weight_cols,race['sources'][index][:len(w)]))),
               'category_signals_json':json.dumps({name:{'signal':round(float(race['x'][index][k]),5),
                    'direction':'strength' if race['x'][index][k]>0.005 else 'weakness' if race['x'][index][k]<-0.005 else 'neutral',
                    'sample_evidence_count':int(race['evidence_counts'][index][k])}
                    for k,name in enumerate(weight_cols[:len(w)])})}
            allp.append(q)
            if q['selected']:picked.append(q)
        print(model,best[1],summary[model]['all_races_2025'],summary[model]['selected_races_2025'],flush=True)
    def csvwrite(path,rows):
        if not rows:return
        with open(out/path,'w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0].keys()));writer.writeheader();writer.writerows(rows)
    csvwrite('stage30-all-weight-combinations.csv',combos)
    csvwrite('stage30-2025-all-races.csv',allp)
    csvwrite('stage30-2025-selected-races.csv',picked)
    doc={'status':'RESEARCH_ONLY','live_models_changed':False,'test_year':2025,
         'learning_years':[2023,2024], 'historical_form_start':'earliest_available',
         'all_model_gap_aware':True,'model_d_exclusive_metrics':METRICS,
         'models_abc_note':'A/B/C are new gap-aware challengers preserving a historical trends component, not in-place updates to existing live code.',
         'named_event_map':mapping_info,'source_coverage':audit,'model_results':summary,
         'limitations':['2025 never used to choose weights or selection cutoffs; viewing every combination on 2025 must not be used to re-optimise retrospectively.',
         'Candidate eligibility demands at least 520 selections and 85% settlement price coverage separately in 2023 and 2024; no profit percentage from a handful of bets can win selection.',
         'Horse last-five and trainer recent form, sire/dam progeny, trainer debut and layoff return records are used only if observed before race day; unverified claims are not invented.',
         'Observed debut is the first entry in an imperfect historical archive, not proof of a true career debut.',
         'Final SP is used solely for historical win-bet settlement (one unit per selection), never as a selection or training feature.',
         'When no direct or indirect evidence exists, a conditional neutral population prior remains and is audited rather than an invented estimate.',
         'Racecourse whitelist excludes non-UK/Irish races; incomplete and ambiguous historical identities affect reliability.',
         'Reported sample_evidence_count is raw supporting outcome occurrences, may overlap between fallback tiers, and is not an independent sample-size or confidence interval.',
         'RPR/Topspeed are excluded because they may contain future/post-race information.',
         'Flat/AW disables Jumping; Jumps disables Draw; weights redistribute within each race.',
         'This retrospective research does not claim live bookmaker execution, stable odds or profits.']}
    (out/'stage30-summary.json').write_text(json.dumps(doc,indent=2,ensure_ascii=False)+'\n')
    return doc

def self_test():
    assert track('Wolverhampton (AW)')=='wolverhampton'
    assert track('Newcastle (AUS)')=='!foreign'
    assert going('Good To Yielding')=='soft'
    assert jumping_note('blundered at the 4th')==0
    assert jumping_note('jumped fluently')==1
    assert len(candidates('D')[0])==8 and len(candidates('A')[0])==9
    s={};add(s,('horse','z'),1,1);add(s,('horse','z'),0,1)
    z,tag,n=feature(s,[(('horse','z'),1,'horse')],.25)
    assert z>0 and tag=='horse' and n==2
    print('Stage30 self-test passed: 8 metrics, evidence hierarchy, date/going/UKIE logic')

def main():
    p=argparse.ArgumentParser();p.add_argument('--db');p.add_argument('--mapping');p.add_argument('--output',default='stage30_report');p.add_argument('--self-test',action='store_true');a=p.parse_args()
    if a.self_test:self_test();return
    if not a.db:p.error('--db required')
    mapping,about=named_map(a.mapping)
    samples,audit=Past(mapping).scan(a.db)
    print('Split:',audit['evaluated_races'],'historical:',audit['uk_ie_races_scanned'],flush=True)
    report(samples,a.output,about,audit)

if __name__=='__main__':main()
