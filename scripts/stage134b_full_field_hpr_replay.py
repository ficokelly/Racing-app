#!/usr/bin/env python3
"""Stage134B: Strict full-field historical HPR v0 vs v3.1 RECONSTRUCTION.

This is deliberately different from comparing sparse pre-existing Stage132 HPR rows:
  - all qualifying historical UK/IE runners in exactly referenced races are scored
    in race-date order, using only form from *earlier calendar days*;
  - original Stage86 HPR v0 formula is called directly;
  - Stage132 HPR v3.1 class/field OR/relative-weight adjustment is replicated
    without post-race or odds inputs;
  - identical eligible full fields, historical SP settlement only;
  - no live model, database or application files are modified.

The reconstructed old HPR score is NOT the historical persisted HPR ledger.
Do not mix the resulting performance claims with the sparse archived-ledger replay.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, sqlite3, tempfile
from collections import defaultdict
import stage86_hpr_engine as H
import stage133_class_taxonomy_hpr_replay as T
import stage131_class_weight_hpr as S

VERSION='stage134b-full-field-hpr-v0-v31-reconstruction-v1'
STOP={H.norm(x) for x in H.STOP}
UNFINISHED={H.norm(x) for x in H.UNFINISHED}


def eligible_starter(r):
    return H.norm(r.get('outcome') or '') not in STOP


def verified_horse_key(r):
    # Prefer exact supplied horse ID if present; pedigree + full name otherwise.
    # Never infer a horse identity from name alone or from per-race runner_key.
    name,sire,dam=(H.norm(r.get(x)) for x in ('horse','sire','dam'))
    if name and sire and dam:return 'P|'+name+'|'+sire+'|'+dam
    sid=str(r.get('horse_source_id') or '').strip()
    if sid and sid.lower() not in ('none','nan','0'):return 'ID|'+sid
    return ''


def race_identity(r,ref):
    date=str(r.get('date') or ''); course=str(r.get('course') or '')
    if not ref or date!=str(ref[0]) or H.course(course)!=H.course(ref[1]):
        return False
    return str(ref[4]).upper() in ('GB','IRELAND')


def field_context(starters):
    ors=[H.number(x.get('official_rating')) for x in starters]
    ors=[x for x in ors if x is not None and 0<x<190]
    w=[H.number(x.get('weight_lbs')) for x in starters]
    w=[x for x in w if x is not None and 80<x<220]
    # Stage132 uses at least three runners; unknown strength stays neutral.
    return ((sum(ors)/len(ors)) if len(ors)>=3 else None,
            (sum(w)/len(w)) if len(w)>=3 else None, len(ors),len(w))


def class_points(parsed):
    # Apply explicit UK class, numeric Grade / Group or Listed only.
    # Irish Grade A/B handicaps are not Group/Grade 1-3.
    tier=parsed.get('race_tier','')
    if tier.startswith('GRADE_GROUP_'):
        try:return S.GRADE_POINTS[int(tier.rsplit('_',1)[-1])]
        except (ValueError,KeyError):return None
    if tier=='LISTED':return 6.
    klass=parsed.get('explicit_uk_class')
    return S.CLASS_POINTS.get(klass) if klass else None


def v31(h0, event, rating_points, field_or, field_weight):
    """Reconstruct Stage132 formula with exactly the same bounded components."""
    raw=S.num(h0.get('raw_performance'))
    context=S.num(h0.get('pillar_composite'))
    if raw is None:raise ValueError('Cannot reconstruct without v0 raw performance')
    if context is None:context=raw
    cd=(rating_points-S.old_class_bonus(event)) if rating_points is not None else 0.
    o=S.clamp((field_or-105.)*.11,-4.,4.) if field_or is not None else 0.
    diff=S.relative(event.get('weight_lbs'),field_weight)
    w=S.clamp(diff*.2,-3.,3.) if diff is not None else 0.
    uplift=cd+o+w
    return round(S.clamp(.65*S.clamp(raw+uplift,0.,100.)+.35*S.clamp(context+uplift,0.,100.),0.,100.),2)


def average_last3(past,key):
    vals=[float(e[key]) for e in past[-3:]]
    return sum((i+1)*v for i,v in enumerate(vals))/sum(range(1,len(vals)+1)) if vals else None


def current_conditions_hpr(prior, mean, today, today_class):
    """Stage132-style adjustment for today's class, declared weight and mark.

    Missing evidence is neutral. Uses prior finished runs only; never today outcome.
    """
    if mean is None or not prior:return None
    last=prior[-3:]
    pastclass=S.avg([e.get('class_points') for e in last]) if hasattr(S,'avg') else None
    if pastclass is None:
        vals=[e.get('class_points') for e in last if e.get('class_points') is not None]
        pastclass=sum(vals)/len(vals) if vals else None
    classadj=S.clamp((pastclass-today_class)*.2,-3.,3.) if pastclass is not None and today_class is not None else 0.
    earlier_wt=H.number(last[-1].get('weight_lbs'))
    wt=H.number(today.get('weight_lbs'))
    weightadj=S.clamp((earlier_wt-wt)*.12,-2.,2.) if earlier_wt is not None and wt is not None else 0.
    earlier_or=H.number(last[-1].get('official_rating'))
    today_or=H.number(today.get('official_rating'))
    oradj=S.clamp((earlier_or-today_or)*.12,-2.,2.) if earlier_or is not None and today_or is not None else 0.
    return S.clamp(mean+classadj+weightadj+oradj,0.,100.)


def format_rows(rows,columns,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(rows)


def summaries(selections):
    group=defaultdict(list)
    for row in selections:
        period=str(row['date'])[:4]
        for key in [('ALL','ALL','ALL'),(period,'ALL','ALL'),('ALL',row['discipline'],row['type']),
                    (period,row['discipline'],row['type']),('ALL',row['country']+' '+row['discipline'],row['type'])]:
            group[key].append(row)
    out=[]
    for (period,kind,rtype),rs in sorted(group.items()):
        settled=[r for r in rs if r['paired_sp']]
        oldwins=sum(r['old_win'] for r in rs);newwins=sum(r['new_win'] for r in rs);targetwins=sum(r['target_win'] for r in rs)
        old_ret=sum(r['old_return'] for r in settled);new_ret=sum(r['new_return'] for r in settled);target_ret=sum(r['target_return'] for r in settled)
        out.append({'period':period,'discipline':kind,'type':rtype,'races':len(rs),
                    'old_wins':oldwins,'new_wins':newwins,'target_wins':targetwins,'old_strike_pct':round(oldwins/len(rs)*100,2),
                    'new_strike_pct':round(newwins/len(rs)*100,2),'target_strike_pct':round(targetwins/len(rs)*100,2),
                    'changed':sum(r['old_horse']!=r['new_horse'] for r in rs),
                    'target_changed':sum(r['old_horse']!=r['target_horse'] for r in rs),
                    'paired_sp_races':len(settled),
                    'old_roi_sp_pct':round((old_ret/len(settled)-1)*100,2) if settled else None,
                    'new_roi_sp_pct':round((new_ret/len(settled)-1)*100,2) if settled else None,
                    'target_roi_sp_pct':round((target_ret/len(settled)-1)*100,2) if settled else None})
    return out


def stream_archive(db_path,first,last):
    cx=sqlite3.connect('file:'+str(db_path.resolve())+'?mode=ro',uri=True)
    cx.row_factory=sqlite3.Row
    if cx.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Corrupt historical SQLite')
    required={'historical_races','historical_runs'}
    if not required<={x[0] for x in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}:raise RuntimeError('Wrong archive schema')
    # Race rows are streamed, never loaded into a multi-million runner dataframe.
    sql='''SELECT r.id AS race_id,r.race_date AS date,r.course,r.race_type,r.surface,
           r.distance_f,r.going,r.race_class,u.runner_key,u.horse_name AS horse,
           u.horse_source_id,u.sire,u.dam,u.trainer,u.jockey,u.official_rating,
           u.weight_lbs,u.draw,u.finish_pos,u.outcome,u.beaten,u.sp_decimal
           FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
           WHERE r.race_date>=? AND r.race_date<=?
           ORDER BY r.race_date,r.id,u.runner_key'''
    cur=cx.execute(sql,(first,last));id0=None;rowbuffer=[]
    try:
        for rr in cur:
            d=dict(rr);k=(d['date'],d['race_id'])
            if id0 is not None and k!=id0:
                yield rowbuffer;rowbuffer=[]
            id0=k;rowbuffer.append(d)
        if rowbuffer:yield rowbuffer
    finally:cx.close()


def replay(db,reference,out,first,start,end,min_field=4,max_races=0):
    import stage108_model_g2 as G
    if not first<=start<=end:raise ValueError('chronological date order invalid')
    if end>=dt.date.today().isoformat():
        # Workflows only score completed historical years and fail closed on today.
        raise ValueError('end date must be before current date')
    refs,refaudit=G.load_reference(reference,first=first,last=end)
    per=defaultdict(list);queue=[];last_date=None
    audit=collections.Counter();picked=[];sample_exclusions=[]
    categories=collections.Counter();race_num=0
    for entries in stream_archive(db,first,end):
        race_num+=1;date=entries[0]['date'];rid=entries[0]['race_id'];nrows=len(entries)
        if date!=last_date:
            # Ratings scored on the previous calendar day now become visible.
            for key,obj in queue:per[key].append(obj)
            queue=[];last_date=date
        if max_races and race_num>max_races:break
        meta=refs.get(rid)
        if not meta or not race_identity(entries[0],meta):
            audit['non_verifiable_ukie_race']+=1;continue
        audit['verified_races']+=1
        country=str(meta[4]).upper();title=meta[3];rtype=meta[2];parsed=T.parse(title,rtype,country)
        code=parsed['race_code'];kind=parsed['race_type']
        starters=[r for r in entries if eligible_starter(r)]
        n=len(starters)
        audit['total_starters_scanned']+=n
        if n<min_field:
            audit['field_too_small']+=1;continue
        # Require all declared identities to be verifiable from source facts.
        ids=[verified_horse_key(r) for r in starters]
        ident_ok=all(ids) and len(set(ids))==len(ids)
        if not ident_ok:
            audit['missing_or_duplicate_identity']+=1
            # cannot safely use forms in next races from ambiguous identities
            continue
        field_or,field_weight,or_known,wt_known=field_context(starters)
        if parsed.get('explicit_uk_class') is not None:audit['races_with_explicit_uk_class']+=1
        if parsed.get('race_tier') in ('UNSPECIFIED','IRISH_HANDICAP_GRADE_A','IRISH_HANDICAP_GRADE_B','IRISH_HANDICAP_GRADE_C'):
            audit['races_without_numeric_tier']+=1
        audit['country_'+country]+=1
        winnerkeys=[ids[i] for i,r in enumerate(starters) if H.number(r.get('finish_pos'))==1]
        # Compute BEFORE adding current race's outcomes to per-horse history.
        info=[]
        pts=class_points(parsed)
        for idx,r in enumerate(starters):
            key=ids[idx];prior=[p for p in per[key] if p.get('code')==code]
            old=average_last3(prior,'old') if prior else None
            new=average_last3(prior,'new') if prior else None
            target=current_conditions_hpr(prior,new,r,class_points(parsed))
            info.append({'r':r,'key':key,'old':old,'new':new,'target':target,'prior':len(prior)})
        if start<=date<=end:
            audit['evaluation_races_seen']+=1
            if len(winnerkeys)!=1:audit['invalid_winner_count']+=1
            elif code=='UNKNOWN':audit['code_unknown']+=1
            elif any(x['old'] is None or x['new'] is None or x['target'] is None for x in info):
                audit['one_or_more_runners_no_same_code_prior']+=1
                if len(sample_exclusions)<20:sample_exclusions.append({'date':date,'race_id':rid,'course':meta[1],
                            'type':kind,'reason':'ONE_OR_MORE_NO_PRIOR','total':n,'with_prior':sum(x['old'] is not None for x in info)})
            else:
                oldidx=max(range(n),key=lambda i:(info[i]['old'],info[i]['key']))
                newidx=max(range(n),key=lambda i:(info[i]['new'],info[i]['key']))
                targetidx=max(range(n),key=lambda i:(info[i]['target'],info[i]['key']))
                oldrunner=starters[oldidx];newrunner=starters[newidx];targetrunner=starters[targetidx]
                oldsp=T.parse_sp(oldrunner.get('sp_decimal'));newsp=T.parse_sp(newrunner.get('sp_decimal'));targetsp=T.parse_sp(targetrunner.get('sp_decimal'))
                os=oldidx==ids.index(winnerkeys[0]);ns=newidx==ids.index(winnerkeys[0]);ts=targetidx==ids.index(winnerkeys[0]);settled=oldsp is not None and newsp is not None and targetsp is not None
                row={'date':date,'country':country,'course':meta[1],'race_id':rid,'race_name':title,
                     'discipline':code,'type':'Handicap' if kind=='Handicap' else 'Non-handicap',
                     'race_subtype':kind,'race_tier':parsed['race_tier'],'field_size':n,
                     'old_horse':oldrunner['horse'],'new_horse':newrunner['horse'],'target_horse':targetrunner['horse'],
                     'old_preoff_hpr':round(info[oldidx]['old'],2),'new_preoff_hpr':round(info[newidx]['new'],2),
                     'target_preoff_hpr':round(info[targetidx]['target'],2),
                     'old_prior_runs':info[oldidx]['prior'],'new_prior_runs':info[newidx]['prior'],
                     'old_win':int(os),'new_win':int(ns),'target_win':int(ts),'old_sp':oldsp,'new_sp':newsp,'target_sp':targetsp,
                     'paired_sp':int(settled),'old_return':(oldsp if os else 0.) if settled else None,
                     'new_return':(newsp if ns else 0.) if settled else None,
                     'target_return':(targetsp if ts else 0.) if settled else None}
                picked.append(row);audit['eligible_complete_fields']+=1
                categories[(code,row['type'])]+=1
        # AFTER selections: score every starter's just-finished race using only
        # previously known scores. Written to a queue released on NEXT date.
        # A horse that does not finish but has a verified outcome may still be scored by v0.
        for item in info:
            r=item['r'];key=item['key']
            e={'horse_key':key,'horse':r['horse'],'race_key':'HIST|'+str(rid),'date':date,
                'course':r['course'],'race_type':str(title)+' '+str(rtype),'surface':r['surface'],
                'distance_f':r['distance_f'],'going':r['going'],'race_class':r['race_class'],
                'trainer':r['trainer'],'jockey':r['jockey'],'weight_lbs':r['weight_lbs'],
                'official_rating':r['official_rating'],'draw':r['draw'],'position':r['finish_pos'],
                'status':r['outcome'],'beaten':r['beaten'],'field_size':n,
                'field_or':field_or if or_known>=max(2,math.ceil(n*.5)) else None,
                'source':'HISTORICAL_EXACT_ARCHIVE_PREVIOUS_DAY'}
            try:
                # Full v0 pillars refer to all earlier starts, not only the same code.
                old_prior=[p['v0_event'] for p in per[key]]
                v0,status=H.score_event(e,old_prior)
            except (TypeError,ValueError,KeyError,ZeroDivisionError) as err:
                if len(sample_exclusions)<20:sample_exclusions.append({'date':date,'race_id':rid,'horse':r['horse'],'reason':'QUALITY_INPUT_INVALID','detail':str(err)[:110]})
                audit['v0_scoring_error']+=1;continue
            if v0 is None:
                audit['v0_unscored_'+str(status)]+=1;continue
            new_val=v31(v0,e,pts,field_or,field_weight)
            obj={'date':date,'code':code,'old':float(v0['hpr']),'new':float(new_val),'v0_event':v0,
                 'class_points':pts,'weight_lbs':e.get('weight_lbs'),'official_rating':e.get('official_rating')}
            queue.append((key,obj));audit['historical_performances_scored']+=1
        if race_num%10000==0:print('Progress:',race_num,'races',audit['eligible_complete_fields'],'paired full fields',flush=True)
    if not picked:
        result={'version':VERSION,'status':'INSUFFICIENT_ELIGIBLE_RACES','audit':dict(audit),'history_from':first,'period':[start,end],
                'eligible_races':0,'sample_rejections':sample_exclusions,'safe':'No live writes; do not claim performance'}
    else:
        summary=summaries(picked)
        result={'version':VERSION,'status':'RESEARCH_ONLY_FULL_ARCHIVE_RECONSTRUCTION',
                'eligible_races':len(picked),'period':[start,end],'history_from':first,
                'audit':dict(audit),'reference_audit':refaudit,'summary':summary,'sample_rejections':sample_exclusions,
                'notes':['Original Stage86 HPR scoring algorithm is recomputed from archived runs; not the previously stored sparse ledger.',
                        'Stage132 v3.1 uplift applied to reconstructed v0 raw/pillars; separate today-adjusted v3.1 candidate uses current weight, mark and explicit race tier.',
                        'All preoff ranks use ratings from strictly PRIOR CALENDAR DAYS.',
                        'Only verified UK/Irish full starter fields with unique winner and same-code history for all runners are included.',
                        'HPR results are NOT calibrated win probabilities; SP ROI uses settlement hindsight, not bettable morning odds.',
                        'Class, grade and tier assigned only from explicit Kaggle metadata/reference titles; Irish Grade A/B is NOT graded/group racing.',
                        'Test outcomes are used only for historical performance scoring AFTER the race and for settlement.'],
                'no_live_updates':True}
        format_rows(picked,list(picked[0]),out/'stage134-hpr-paired-races.csv')
        format_rows(summary,list(summary[0]),out/'stage134-hpr-by-discipline-racetype.csv')
    out.mkdir(parents=True,exist_ok=True)
    (out/'stage134-hpr-replay-audit.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({'status':result['status'],'eligible_races':result['eligible_races'],
                      'audit':dict(audit),'overall':next((x for x in result.get('summary',[]) if x['period']=='ALL' and x['discipline']=='ALL'),None)},indent=2),flush=True)
    return result


def self_test():
    assert class_points({'race_tier':'IRISH_HANDICAP_GRADE_A','explicit_uk_class':None}) is None
    assert class_points(T.parse('Listed Handicap Chase','', 'IRELAND'))==6.
    assert class_points(T.parse('Class 2 Handicap Hurdle','', 'GB'))==7.
    assert verified_horse_key({'horse':'Same Name','horse_source_id':'123'})=='ID|123'
    assert not verified_horse_key({'horse':'Name Alone'})
    assert average_last3([{'old':10},{'old':20},{'old':40}],'old')==(10+40+120)/6
    assert v31({'raw_performance':50,'pillar_composite':60},{'race_class':'Class 4','weight_lbs':160},7,115,155)>55
    assert current_conditions_hpr([{'class_points':7.,'weight_lbs':160,'official_rating':120}],70.,{'weight_lbs':154,'official_rating':113},0.)>70.
    # Real chronological fixture: all four have earlier run, no same-day leakage.
    with tempfile.TemporaryDirectory() as d:
        p=pathlib.Path(d);db=p/'hist.sqlite';ref=p/'ref.csv';out=p/'out'
        conn=sqlite3.connect(db)
        conn.executescript('''CREATE TABLE historical_races(id TEXT PRIMARY KEY,race_date TEXT,course TEXT,race_type TEXT,surface TEXT,distance_f REAL,going TEXT,race_class TEXT);
            CREATE TABLE historical_runs(race_id TEXT,runner_key TEXT,horse_name TEXT,horse_source_id TEXT,sire TEXT,dam TEXT,trainer TEXT,jockey TEXT,official_rating REAL,weight_lbs REAL,draw INTEGER,finish_pos INTEGER,outcome TEXT,beaten TEXT,sp_decimal REAL);''')
        for date,rid in [('2023-08-02','R1'),('2024-03-01','R2')]:
            conn.execute('INSERT INTO historical_races VALUES (?,?,?,?,?,?,?,?)',(rid,date,'Test Course','Hurdle','Turf',16.,'Good','Class 4'))
            for i in range(4):
                conn.execute('INSERT INTO historical_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (rid,str(i),f'Horse {i}',f'ID{i}','S','D','Trainer','Jockey',100+i,154+i,0,
                     (i+1 if rid=='R1' else (2,1,3,4)[i]),'ran',str(i*3)+'L',float(3+i)))
        conn.commit()
        # Self-test fields must be valid: a four-runner race cannot have a fifth-place finisher.
        for test_race in ('R1','R2'):
            positions=sorted(row[0] for row in conn.execute(
                'SELECT finish_pos FROM historical_runs WHERE race_id=?',(test_race,)))
            assert positions==[1,2,3,4],(test_race,positions)
        conn.close()
        with ref.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['stage2_race_id','date','region_scope','course','race_type','race_name']);w.writeheader()
            for d,rid in [('2023-08-02','R1'),('2024-03-01','R2')]:
                w.writerow({'stage2_race_id':rid,'date':d,'region_scope':'GB','course':'Test Course','race_type':'Hurdle','race_name':'Class 4 Handicap Hurdle'})
        result=replay(db,ref,out,'2023-01-01','2024-01-01','2024-12-31')
        assert result['eligible_races']==1,result
        assert result['audit']['historical_performances_scored']==8,result
        assert (out/'stage134-hpr-paired-races.csv').exists()
    print('PASS Stage134: full-field reconstructions, no future-day leakage, proper Irish grade, exact ID, same-code priors')


def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='mode',required=True)
    sub.add_parser('self-test')
    r=sub.add_parser('replay');r.add_argument('--history',type=pathlib.Path,required=True)
    r.add_argument('--reference',type=pathlib.Path,required=True);r.add_argument('--out',type=pathlib.Path,required=True)
    r.add_argument('--history-start',default='2022-01-01');r.add_argument('--start',default='2024-01-01');r.add_argument('--end',default='2025-12-31')
    r.add_argument('--max-races',type=int,default=0)
    x=ap.parse_args()
    if x.mode=='self-test':self_test()
    else:replay(x.history,x.reference,x.out,x.history_start,x.start,x.end,max_races=x.max_races)

if __name__=='__main__':main()
