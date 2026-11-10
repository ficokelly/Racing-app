#!/usr/bin/env python3
"""Stage97: outcome-blind UK/IE National Hunt research replay, 1 Oct–10 Nov 2026.

PREDICT PHASE: no source results files exist in the working-tree during this
phase; both historical SQLite and HPR ledgers have been physically embargoed
at 2026-10-01. Race-day, pre-off card declarations are used, but card form
strings (which encode post-cutoff results) are masked. The pre-off prediction
CSV is written and hashed *before* any test results are revealed.

SETTLE PHASE: only afterward load final result records, match exact dated
race/time, verify full actual starter names are a subset of pre-off declared
runners, and settle SP without changing picks or model probability shares.

Original D and D(c) are frozen recipes, while both E75 models and HPRv2 overlays
were retrospectively developed: this is an INFORMATION-BLIND replay, not proof
of genuinely prospective pre-registered models or calibrated win probabilities.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, gzip, hashlib, io
import json, math, pathlib, re, sqlite3, statistics, subprocess, sys, unicodedata

VERSION='stage97-nh-information-blind-research-v1'
START='2026-10-01'
END='2026-11-10'
CUTOFF='2026-10-01'
SHARE_TEMPERATURE=1.3 # FIXED research display parameter; NOT calibrated win probabilities
BASE=('D_ORIGINAL','D_C','E_75_25_NO_HPR','E_75_25_WITH_HPR')
MODELS=tuple(v for b in BASE for v in (b,b+'_HPRV2'))
THRESHOLDS=(20,25,30,34,40,42,45,50)
HEADER=('date','course','time_utc','snapshot_sha','snapshot_collected_utc',
        'race_id','race_name','discipline','race_type','declared_field',
        'model','rank','horse','model_implied_win_pct','hpr_v2_score',
        'hpr_v2_field_coverage_pct','hpr_overlay_active','research_only')
RESULT_HEADER=(*HEADER,'result_race_id','result_status','finish','won',
               'sp','sp_decimal','sp_source','profit_1u','settlement_status')
RESULT_SUBTYPES=('OVERALL_NH','HURDLE','CHASE','NH_FLAT')


def norm(x):
    s=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().upper()
    s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$','',s)
    return re.sub('[^A-Z0-9]','',s)


def venue(x):
    return norm(re.sub(r'\((?:AW|IRE|GB|UK)\)','',str(x or ''),flags=re.I))


def num(x):
    try:
        if x is None or str(x).strip()=='':return None
        y=float(x)
        return y if math.isfinite(y) else None
    except (ValueError,TypeError):return None


def zscores(vals):
    z=[float(x) for x in vals]
    if len(z)<2 or any(not math.isfinite(x) for x in z):raise ValueError('Scores require 2+ finite declarations')
    mu=statistics.mean(z);s=statistics.pstdev(z)
    return [(x-mu)/(s if s>1e-9 else 1.) for x in z]


def probabilities(vals,temperature=SHARE_TEMPERATURE):
    if temperature<=0:raise ValueError('Unusable display temperature')
    w=[math.exp((float(x)-max(vals))/temperature) for x in vals]
    total=sum(w)
    return [x/total for x in w]


def apply_hpr(vals, hpr):
    """Fixed Stage96 HPRv2 adjustment (not tuned using this test's winners)."""
    if len(vals)!=len(hpr):raise ValueError('HPR cardinality mismatch')
    rated=[x for x in hpr if x is not None]
    coverage=len(rated)/len(hpr)
    if len(rated)<2 or coverage<.70:return list(vals),False,coverage
    mid=statistics.median(rated)
    out=[]
    for score,form in zip(vals,hpr):
        bump=0. if form is None else max(-.70,min(.70,.35*(form-mid)/20.))
        out.append(score+bump)
    return out,True,coverage


def code(raw):
    s=str(raw or '').lower()
    if 'chase' in s or 'steeple' in s:return 'CHASE'
    if 'hurdle' in s:return 'HURDLE'
    if 'nh flat' in s or 'national hunt flat' in s or 'bumper' in s:return 'NH_FLAT'
    return ''


def discipline(raw):
    return {'CHASE':'CHASE','HURDLE':'HURDLE','NH_FLAT':'NH_FLAT'}.get(code(raw),'')


def cleaned(x):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(x or '')).lower().replace('’',"'")).strip()


def pedigree_candidate_hashes(card):
    """Never horse-name-only match; sire and dam are mandatory on both sides.

    The Stage91 hash may have a country suffix while a provider's card may
    omit it. Try only explicit same-parent spelling variants; ambiguous hits
    are refused by `hpr_for`.
    """
    sire=cleaned(card.get('sire'));dam=cleaned(card.get('dam'))
    name=cleaned(card.get('horse'))
    if not (name and sire and dam):return set()
    base=re.sub(r'\s*\((?:ire|gb|fr|usa|ger|aus|nz|it|jpn)\)\s*$','',name)
    names={name,base}
    country=str(card.get('horse_country') or card.get('country') or '').strip().lower()
    if country in ('ire','gb','fr','usa','ger','aus','nz','it','jpn'):
        names.add(base+' ('+country+')')
    # Source may preserve country suffix although the card's country is empty.
    names.update(base+' ('+c+')' for c in ('ire','gb'))
    return {'PEDIGREE_SHA256_'+hashlib.sha256('|'.join((n,sire,dam)).encode()).hexdigest()[:24] for n in names}


def load_pre_cutoff_hpr(path):
    """The workflow first extracts ONLY dates before 1 October to this file."""
    records=collections.defaultdict(list)
    with gzip.open(path,'rt',encoding='utf-8',newline='') as fp:
        reader=csv.DictReader(fp)
        needed={'date','horse_identity','race_type','post_race_hpr_v2'}
        if not needed.issubset(reader.fieldnames or []):raise RuntimeError('Invalid HPR input fields')
        for r in reader:
            if not r['date']<CUTOFF:raise RuntimeError('HPR embargo violated; post-cutoff performance loaded')
            n=num(r.get('post_race_hpr_v2'))
            c=code(r.get('race_type'))
            key=r.get('horse_identity','')
            if n is None or not c or not key.startswith('PEDIGREE_SHA256_'):continue
            records[(key,c)].append((r['date'],n))
    last={}
    for k,vals in records.items():
        vals.sort()
        recent=[v for d,v in vals if d==vals[-1][0]]
        # More than one disputed HPR on one horse's most recent day => unknown.
        if len({round(v,5) for v in recent})==1:last[k]=recent[0]
    return last


def verified_pre_cutoff_pedigrees(db):
    """Recover static sire and dam only through an exact pre-cutoff provider ID.

    Horse names alone do not authorize the join. Ambiguous, missing and
    conflicting parentage is ignored, so HPR v2 remains neutral when unknown.
    """
    con=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    candidates=collections.defaultdict(set)
    query='''SELECT u.horse_source_id,u.horse_name,u.sire,u.dam
      FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
      WHERE r.race_date<? AND u.horse_source_id IS NOT NULL
        AND u.horse_source_id!='' AND u.sire IS NOT NULL AND u.dam IS NOT NULL
        AND u.sire!='' AND u.dam!='' '''
    for horse_id,horse,sire,dam in con.execute(query,(CUTOFF,)):
        if horse_id and horse and sire and dam:
            candidates[str(horse_id)].add((norm(horse),cleaned(sire),cleaned(dam)))
    con.close()
    return {sid:next(iter(matches)) for sid,matches in candidates.items() if len(matches)==1}


def with_verified_parents(card,source_id_map):
    """Fill missing parents only where source ID and horse name agree."""
    if card.get('sire') and card.get('dam'):return card,'CARD_PARENTAGE'
    source_id=str(card.get('horse_id') or card.get('horse_source_id') or '')
    match=source_id_map.get(source_id)
    if not match:return card,'NO_PREOCT_PROVIDER_ID_PARENTAGE'
    horse,sire,dam=match
    if norm(card.get('horse'))!=horse:return card,'PROVIDER_ID_NAME_CONFLICT'
    return {**card,'sire':sire,'dam':dam},'PREOCT_PROVIDER_ID_PARENTAGE'


def hpr_for(card,idx):
    c=code(card.get('type') or card.get('race_type') or card.get('race_name'))
    if not c:return None
    matched={idx[k,c] for k in pedigree_candidate_hashes(card) if (k,c) in idx}
    return next(iter(matched)) if len(matched)==1 else None


def load_snapshots(root,start=START,end=END):
    """Read an actual Git snapshot committed by 09:45 UTC *on the date*.

    No results file, post-race JSON or historical results database is touched.
    Return all in-window declarations, keeping actual pre-off nonrunner flags.
    """
    import stage86_hpr_engine as H
    bydate={};meta={};audit=[]
    first=dt.date.fromisoformat(start);last=dt.date.fromisoformat(end)
    for i in range((last-first).days+1):
        date=(first+dt.timedelta(days=i)).isoformat()
        cutoff=dt.datetime.fromisoformat(date+'T09:45:00+00:00')
        p=subprocess.run(['git','log','-1','--format=%H','--until',date+' 09:45:00 +0000',
                         '--','data/racecards.json'],cwd=root,text=True,capture_output=True,check=True)
        sha=p.stdout.strip()
        info={'date':date,'snapshot_sha':sha,'snapshot_collected_utc':'',
              'eligible_nh_races':0,'racecards':0,'status':''}
        if not sha:
            info['status']='NO_ARCHIVED_PREOFF_SNAPSHOT';audit.append(info);continue
        raw=subprocess.run(['git','show',sha+':data/racecards.json'],cwd=root,text=True,capture_output=True,check=True)
        content=json.loads(raw.stdout)
        collected=str(content.get('collected_at') or '')
        info['snapshot_collected_utc']=collected
        try:
            stamp=dt.datetime.fromisoformat(collected.replace('Z','+00:00'))
            if stamp.tzinfo is None:raise ValueError('No collection timezone')
            stamp=stamp.astimezone(dt.timezone.utc)
        except (ValueError,TypeError):
            info['status']='UNVERIFIED_COLLECTION_TIMESTAMP';audit.append(info);continue
        if stamp.date().isoformat()!=date or stamp>cutoff:
            info['status']='STALE_OR_POST_SNAPSHOT';audit.append(info);continue
        cards=list(H.flat_cards(content))
        cards=[c for c in cards if str(H.day(c.get('date') or c.get('race_time')))==date]
        bydate[date]=cards
        info['racecards']=len(cards)
        info['status']='PREOFF_SOURCE_VERIFIED'
        meta[date]=(sha,collected)
        audit.append(info)
    return bydate,meta,audit


def model_groups(day, cards):
    import stage86_hpr_engine as H
    import stage94_dc_probability_replay as S94
    groups=collections.defaultdict(list)
    for c in cards:
        if str(c.get('race_status') or '').strip().lower() in H.STOP:continue
        if S94.race_disc(c)!='Jumps':continue
        country=norm(c.get('region') or c.get('country') or '')
        if country and country not in ('GB','UK','GBR','IRE','IE','IRL','NORTHERNIRELAND'):
            continue
        if venue(c.get('course')) not in {venue(t) for t in H.UKIE_COURSES} and not country:
            continue
        groups[H.race_key(c)].append(c)
    okay=[];reject=collections.Counter()
    for key, field in sorted(groups.items()):
        names=[norm(c.get('horse')) for c in field]
        if len(field)<2 or '' in names or len(names)!=len(set(names)):
            reject['MALFORMED_OR_DUPLICATE_FIELD']+=1;continue
        if any(not c.get('trainer') or not c.get('jockey') for c in field):
            reject['MISSING_TRAINER_OR_JOCKEY']+=1;continue
        cc={code(c.get('type') or c.get('race_type') or c.get('race_name')) for c in field}
        if len(cc)!=1 or not next(iter(cc)):
            reject['CONFLICTING_NH_RACE_DISCIPLINE']+=1;continue
        if any(not c.get('race_time') for c in field):
            reject['MISSING_PREOFF_TIME']+=1;continue
        okay.append((key,field))
    return okay,reject


def people_before_cutoff(db,earliest='2025-10-01'):
    """Copy of historical E people/draw evidence logic with a hard <=Sep30 gate."""
    import stage92_model_e_winprob as E
    cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    sql='''SELECT r.id,r.race_date,r.course,r.surface,r.distance_f,
      u.trainer,u.jockey,u.finish_pos,u.draw FROM historical_runs u
      JOIN historical_races r ON r.id=u.race_id
      WHERE r.race_date>=? AND r.race_date<? ORDER BY r.id'''
    by=collections.defaultdict(list)
    for rid,date,course,surf,trip,tr,jk,pos,draw in cx.execute(sql,(earliest,CUTOFF)):
        by[str(rid)].append((date,E.course(course),str(surf or ''),E.tripband(trip),
                             E.norm(tr),E.norm(jk),num(pos),num(draw)))
    cx.close()
    people=[];draw_hist=collections.Counter()
    for field in by.values():
        n=len(field);starts=sorted((float(z[7]),i) for i,z in enumerate(field) if z[7] and z[7]>0)
        bins={i:min(2,int(j*3/len(starts))) for j,(_,i) in enumerate(starts)} if len(starts)>=5 else {}
        for i,(date,course,surf,trip,tr,jk,pos,draw) in enumerate(field):
            if pos is None:continue
            assert date<CUTOFF
            people.append((date,tr,jk,pos==1))
            if i in bins:
                name='AW' if any(t in surf.lower() for t in ('aw','standard','tapeta','polytrack')) else 'Flat'
                draw_hist[(course,name,trip,bins[i],int(pos==1))]+=1
    return E.aggregate_people(people,None),draw_hist,len(people)


def prepare_model_state(root,db,ratings):
    """All result-dependent features obtained only from physically cut-off DBs."""
    import stage38_model_dz_live as D
    import stage30_gap_aware_abcd as OLD
    import stage65p_race_type_c_only as C
    import rhp_stage83_dc_integrated as S83
    import stage95_five_model_paired_replay as S95
    import stage92_model_e_winprob as E
    summary=json.loads((root/'reports/stage30/stage30-summary.json').read_text())
    frozen=summary['model_results']['D']
    if frozen.get('chosen_combination')!='D-064-gap14' or abs(float(frozen.get('selection_margin',0))-.14)>1e-8:
        raise RuntimeError('Frozen D provenance failed')
    weights=[float(frozen['weights_pct'][k])/100 for k in OLD.METRICS]
    C.rows=S83.corrected_rows
    clf=S83.patched_classifier(None,C,str(db))
    past=D.LivePast({});scan=past.scan_until(str(db),CUTOFF)
    form=C.Form()
    n_form=0
    for group in C.rows(str(db),CUTOFF):
        for c in group:
            c['field_size']=len(group);form.update(c);n_form+=1
    people,draw,people_rows=people_before_cutoff(db)
    conn=sqlite3.connect('file:'+str(pathlib.Path(ratings).resolve())+'?mode=ro',uri=True)
    nlate=conn.execute('select count(*) from hpr_ratings where race_date>=?',(CUTOFF,)).fetchone()[0]
    if nlate:raise RuntimeError('HPR v0 result embargo violated: '+str(nlate))
    return (past,form,clf,weights,people,draw,conn),{'old_form_entries':n_form,
        'people_entries':people_rows,'frozen_D_scan':scan,'late_original_HPR_rows':nlate}


def predict(args):
    import numpy as np
    import stage86_hpr_engine as H
    import stage38_model_dz_live as D
    import stage65p_race_type_c_only as C
    import stage92_model_e_winprob as E
    import stage95_five_model_paired_replay as S95
    import stage94_dc_probability_replay as S94
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    root=pathlib.Path(args.root);db=pathlib.Path(args.db);ratings=pathlib.Path(args.ratings)
    # Physically fail if embargoed outcome files are visible during forecasts.
    if (root/'data/racing-results').exists() or (root/'data/race-results.json').exists():
        raise RuntimeError('Test-period results are visible; hide them before prediction')
    for f in (db,ratings,pathlib.Path(args.hpr)):
        if not f.is_file():raise RuntimeError('Missing frozen source: '+str(f))
    cx=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
    nlate=cx.execute('select count(*) from historical_races where race_date>=?',(CUTOFF,)).fetchone()[0]
    cx.close()
    if nlate:raise RuntimeError('Post-cutoff historical races have not been removed: '+str(nlate))
    frozen_hpr=load_pre_cutoff_hpr(args.hpr)
    parent_links=verified_pre_cutoff_pedigrees(db)
    (past,form,(scaler,clf),weights,people,draw,hr),state=prepare_model_state(root,db,ratings)
    snapshots,meta,coverage=load_snapshots(root)
    cache={};allrows=[];rejections=collections.Counter()
    byday=collections.Counter()
    parent_audit=collections.Counter()
    hpr_matched_runners=0
    overlay_active_races=0
    try:
        for date in sorted(snapshots):
            races,bad=model_groups(date,snapshots[date]);rejections.update(bad)
            for key,field in races:
                # Source snapshots may embed prior October results in form string.
                # Mask form entirely: no such information is allowed to the E scorer.
                field=[{**c,'form':''} for c in field]
                unique={}
                for h,country in past.last:
                    if h not in unique:unique[h]=country
                    elif unique[h]!=country:unique[h]=None
                try:
                    x=[D.live_row(c,date,'STAGE97|'+key,unique) for c in field]
                    scored=past.score(x,live=True)
                    if scored is None:
                        rejections['FROZEN_D_UNABLE_TO_SCORE']+=1;continue
                    dvals,_,_=D.prediction_rows(scored,weights)
                    ors=[C.num(c.get('official_rating') or c.get('or') or c.get('ofr')) for c in field]
                    lbs=[C.num(c.get('weight_lbs')) for c in field]
                    avgo=statistics.mean(v for v in ors if v is not None) if any(v is not None for v in ors) else None
                    avgw=statistics.mean(v for v in lbs if v is not None) if any(v is not None for v in lbs) else None
                    feats=[]
                    for c in field:
                        feat={'date':date,'horse':c.get('horse'),'country':c.get('horse_country'),
                          'trainer':c.get('trainer'),'jockey':c.get('jockey'),
                          'race_type':c.get('race_name') or c.get('race_type') or c.get('type'),
                          'surface':c.get('surface'),'race_class':c.get('race_class'),
                          'or':c.get('official_rating') or c.get('or') or c.get('ofr'),
                          'weight':c.get('weight_lbs')}
                        vec,_=form.vector(feat,avgo,avgw);feats.append(vec)
                    cprob=clf.predict_proba(scaler.transform(np.asarray(feats)))[:,1]
                    dcz=.75*np.asarray(S95.standardized(dvals))+.25*np.asarray(S95.standardized(cprob))
                    # Pass cutoff for old HPR querying, even though physical ledger is cut off.
                    eoff,eon,old_hpr_flags,_=S95.e75_preoff(
                        field,scored,cprob,people,draw,hr,CUTOFF,cache)
                    base={'D_ORIGINAL':zscores(dvals),'D_C':zscores(dcz),
                          'E_75_25_NO_HPR':zscores(eoff),
                          'E_75_25_WITH_HPR':zscores(eon)}
                    hruns=[]
                    for c in field:
                        resolved,source=with_verified_parents(c,parent_links)
                        parent_audit[source]+=1
                        hruns.append(hpr_for(resolved,frozen_hpr))
                    hpr_matched_runners+=sum(v is not None for v in hruns)
                    model_raw={}
                    for keymodel,scores in base.items():
                        model_raw[keymodel]=scores
                        model_raw[keymodel+'_HPRV2']=apply_hpr(scores,hruns)[0]
                    cover=sum(x is not None for x in hruns)/len(hruns)
                    overlay_active_races+=int(cover>=.70 and sum(v is not None for v in hruns)>=2)
                    snapsha,collection=meta[date]
                    entries=[]
                    for model in MODELS:
                        ps=probabilities(model_raw[model])
                        if abs(sum(ps)-1.)>1e-10:raise RuntimeError('Probability mass mismatch')
                        order=sorted(range(len(field)),key=lambda i:(-ps[i],norm(field[i]['horse'])))
                        for rank,i in enumerate(order[:2],1):
                            r=field[i]
                            entries.append({'date':date,'course':r['course'],
                              'time_utc':key.rsplit('|',1)[-1],
                              'snapshot_sha':snapsha,'snapshot_collected_utc':collection,
                              'race_id':str(r.get('race_id') or ''),
                              'race_name':str(r.get('race_name') or ''),
                              'discipline':code(r.get('type') or r.get('race_type') or r.get('race_name')),
                              'race_type':S94.race_cat(r),
                              'declared_field':len(field),'model':model,'rank':rank,
                              'horse':r['horse'],'model_implied_win_pct':round(ps[i]*100,4),
                              'hpr_v2_score':hruns[i] if hruns[i] is not None else '',
                              'hpr_v2_field_coverage_pct':round(100*cover,2),
                              'hpr_overlay_active':int(bool(model.endswith('_HPRV2') and cover>=.70 and sum(x is not None for x in hruns)>=2)),
                              'research_only':True})
                    allrows.extend(entries);byday[date]+=1
                except (ValueError,ArithmeticError) as exc:
                    rejections['MODEL_NUMERIC_OR_FEATURE_ERROR']+=1
                    print('Skipping invalid pre-off score:',date,key,type(exc).__name__,flush=True)
            print('Blind predictions',date,byday[date],'NH races, cumulative',sum(byday.values()),flush=True)
    finally:hr.close()
    if not allrows:raise RuntimeError('Zero eligible NH predictions; source audit required')
    path=out/'stage97-blind-rank1-rank2.csv.gz'
    with gzip.open(path,'wt',encoding='utf-8',newline='') as fp:
        writer=csv.DictWriter(fp,fieldnames=HEADER);writer.writeheader();writer.writerows(allrows)
    h=hashlib.sha256(path.read_bytes()).hexdigest()
    for d in coverage:d['eligible_nh_races']=byday[d['date']]
    with (out/'stage97-coverage.csv').open('w',encoding='utf-8',newline='') as fp:
        writer=csv.DictWriter(fp,fieldnames=list(coverage[0]));writer.writeheader();writer.writerows(coverage)
    audit={'stage':97,'version':VERSION,'phase':'PREDICT_BEFORE_RESULTS_REVEAL',
      'cutoff_exclusive':CUTOFF,'range':[START,END],'predictions_sha256':h,
      'predictions':len(allrows),'nh_races':sum(byday.values()),'by_day':dict(byday),
      'coverage':coverage,'model_rejections':dict(rejections),
      'history_state':state,'frozen_hpr_identity_discipline_pairs':len(frozen_hpr),
      'verified_precutoff_provider_id_pedigrees':len(parent_links),
      'pedigree_link_audit':dict(parent_audit),
      'runners_with_matching_pre_oct_hpr':hpr_matched_runners,
      'hpr_overlay_active_races':overlay_active_races,
      'not_calibrated_probabilities':True,'form_masked':True,
      'results_not_available_during_prediction':True,
      'models':list(MODELS),'no_new_rolling_results_updates':True,
      'retrospective_model_design_caveat':True}
    (out/'stage97-blind-manifest.json').write_text(json.dumps(audit,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'phase':'PREDICT','nh_races':audit['nh_races'],'sha256':h,
                      'frozen_hpr_pairs':len(frozen_hpr),
                      'hpr_active_races':overlay_active_races,
                      'pedigree_sources':dict(parent_audit),'rejections':dict(rejections)},indent=2),flush=True)
    return audit


def race_category(label, code_str):
    """Broad code plus granular type (novice, maiden, handicap, graded)."""
    typ=str(label or '').lower()
    c=code_str
    if c=='NH_FLAT':return 'NH Flat/Bumper'
    if 'handicap' in typ:
        return ('Novice Handicap ' if 'novice' in typ else 'Handicap ')+('Hurdle' if c=='HURDLE' else 'Chase')
    if 'hunter' in typ:return 'Hunter Chase'
    if 'maiden' in typ:return 'Maiden '+('Hurdle' if c=='HURDLE' else 'Chase')
    if 'beginner' in typ:return 'Beginners Chase'
    if 'novice' in typ:return 'Novice '+('Hurdle' if c=='HURDLE' else 'Chase')
    if any(z in typ for z in ('graded','group','grade 1','grade 2','grade 3')):return 'Graded/Group'
    if 'listed' in typ:return 'Listed'
    return 'Other '+('Hurdle' if c=='HURDLE' else 'Chase')


def settle(args):
    """No refitting, no ranking updates: load sealed forecast and reveal outcomes."""
    import stage86_hpr_engine as H
    import stage94_dc_probability_replay as S94
    out=pathlib.Path(args.out);src=out/'stage97-blind-rank1-rank2.csv.gz'
    manifest=json.loads((out/'stage97-blind-manifest.json').read_text())
    actual_sha=hashlib.sha256(src.read_bytes()).hexdigest()
    if actual_sha!=manifest['predictions_sha256']:raise RuntimeError('Prediction file altered after forecast; refusing settlement')
    if not (pathlib.Path(args.root)/'data/racing-results').is_dir():
        raise RuntimeError('Final race outcomes not unsealed for settlement')
    results,bad=H.load_results(pathlib.Path(args.root),dt.date.fromisoformat(START),dt.date.fromisoformat(END))
    price=S94.price_candidates(pathlib.Path(args.root),dt.date.fromisoformat(START),dt.date.fromisoformat(END))
    with gzip.open(src,'rt',encoding='utf-8',newline='') as fp:
        selections=list(csv.DictReader(fp))
    # Group snapshots by date/course/off; use the stored candidate ranks to
    # prevent cherry-picking/fixing any predictions after results are read.
    grouped=collections.defaultdict(list)
    for r in selections:grouped[(r['date'],venue(r['course']),r['time_utc'])].append(r)
    coverage=collections.Counter();settled=[];seen_nh=set()
    for (day,course,off),rows in sorted(grouped.items()):
        key=f'{day}|{course}|{off}'
        result=results.get(key)
        seen_nh.add(key)
        # Validate whole actual-starter field via source morning declaration
        # that is still available from the precise stored Git SHA.
        sha=rows[0]['snapshot_sha']
        content=json.loads(subprocess.run(['git','show',sha+':data/racecards.json'],
                  cwd=args.root,capture_output=True,text=True,check=True).stdout)
        morning_list=[norm(c['horse']) for c in H.flat_cards(content)
                      if H.race_key(c)==key and c.get('horse')]
        morning=set(morning_list)
        current_result={norm(c['horse']):c for c in result['runners']} if result else {}
        recorded_race_ids={r['race_id'] for r in rows if r['race_id']}
        result_id=str(result.get('race_id') or '') if result else ''
        ids_agree=(len(recorded_race_ids)<=1 and
                   (not result_id or not recorded_race_ids or result_id in recorded_race_ids))
        full_valid=(bool(result) and bool(morning) and
           len(morning)==len(morning_list) and ids_agree and
           set(current_result).issubset(morning) and len(current_result)>=2 and
           sum(H.win_status(c) for c in current_result.values())==1)
        if not result:status='RESULT_MISSING_OR_UNFINALIZED'
        elif not full_valid:status='RACE_FIELD_RESULT_CONFLICT'
        else:status='VERIFIED_SETTLED_RACE'
        coverage[status]+=1
        for r in rows:
            s=norm(r['horse'])
            rr=current_result.get(s) if full_valid else None
            if not full_valid:
                finish='';won='';sp='';decimal='';source='';pnl='';state=status;result_status=''
            elif rr is None:
                finish='NR_OR_RESULT_ABSENT';won='';sp='';decimal='';source='';pnl='';state='UNVERIFIED_NON_STARTER';result_status=''
            else:
                finish,won_bool=S94.parse_finish(rr)
                result_status=rr.get('status') or ''
                if finish=='NR':
                    won='';sp='';decimal='';source='';pnl='';state='NON_RUNNER'
                else:
                    won=int(won_bool)
                    sp,dec,source=S94.choose_price(price,key,r['horse'])
                    decimal=dec if dec is not None else ''
                    pnl=round(dec-1 if won else -1,5) if dec is not None else ''
                    state='PRICED_AND_SETTLED' if dec is not None else 'FINISHED_SP_MISSING'
            settled.append({**r,'result_race_id':result.get('race_id','') if result else '',
               'result_status':result_status,'finish':finish,'won':won,'sp':sp,'sp_decimal':decimal,
               'sp_source':source,'profit_1u':pnl,'settlement_status':state})
    def metrics(rr):
        evaluated=[r for r in rr if r['won'] not in ('',None)]
        priced=[r for r in evaluated if num(r['sp_decimal']) is not None]
        wins=sum(int(r['won']) for r in evaluated)
        money=sum(float(r['profit_1u']) for r in priced)
        return dict(selections=len(rr),settled=len(evaluated),priced=len(priced),
          unpriced_or_unsettled=len(rr)-len(priced),winners=wins,
          strike_rate_pct=round(100*wins/len(evaluated),2) if evaluated else None,
          profit_1u=round(money,3),sp_roi_pct=round(100*money/len(priced),2) if priced else None,
          mean_model_implied_win_pct=round(statistics.mean(float(r['model_implied_win_pct']) for r in evaluated),2) if evaluated else None)
    bymodel=[];bycode=[];bytype=[];byday=[];thresholds=[]
    for model in MODELS:
        mm=[r for r in settled if r['model']==model]
        for rank in ('1','2','1+2'):
            ss=[r for r in mm if rank=='1+2' or r['rank']==rank]
            bymodel.append(dict(model=model,rank=rank,**metrics(ss)))
            for group in RESULT_SUBTYPES:
                subset=[r for r in ss if group=='OVERALL_NH' or r['discipline']==group]
                bycode.append(dict(model=model,rank=rank,discipline=group,**metrics(subset)))
            for typ in sorted(set(r['race_type'] for r in ss)):
                sub=[r for r in ss if r['race_type']==typ]
                bytype.append(dict(model=model,rank=rank,race_type=typ,**metrics(sub)))
            for day in sorted(set(r['date'] for r in ss)):
                sub=[r for r in ss if r['date']==day]
                byday.append(dict(model=model,rank=rank,date=day,**metrics(sub)))
            for th in THRESHOLDS:
                sub=[r for r in ss if float(r['model_implied_win_pct'])>=th]
                thresholds.append(dict(model=model,rank=rank,minimum_uncalibrated_win_share_pct=th,**metrics(sub)))
    # The metric column deliberately says 'model-implied' rather than claiming
    # historically validated/calibrated per-horse win probabilities.
    def save(path,rows):
        if not rows:return
        with (out/path).open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    save('stage97-scorecard.csv',bymodel)
    save('stage97-by-discipline.csv',bycode)
    save('stage97-by-racetype.csv',bytype)
    save('stage97-by-day.csv',byday)
    save('stage97-thresholds.csv',thresholds)
    save('stage97-all-settled-rank1-rank2.csv',settled)
    # Count ALL provider-final National Hunt race results (including uncovered
    # Oct1–9 dates) so excluded dates never vanish from the coverage report.
    total_nh=sum(S94.race_disc({'type':r.get('type'), 'race_name':r.get('race_name'),
              'surface':r.get('surface'),'course':r.get('course')})=='Jumps' for r in results.values())
    missing_nh=[]
    for key,r in sorted(results.items()):
        if S94.race_disc({'type':r.get('type'),'race_name':r.get('race_name'),
              'surface':r.get('surface'),'course':r.get('course')})=='Jumps' and key not in seen_nh:
            missing_nh.append(dict(date=key[:10],course=r.get('course'),off=key.rsplit('|',1)[-1],
                                  race_name=r.get('race_name'),reason='NO_ELIGIBLE_PREOFF_PREDICTION'))
    save('stage97-nh-not-predicted.csv',missing_nh)
    report={'stage':97,'version':VERSION,'status':'RESEARCH_ONLY_NO_LIVE_CHANGES',
      'cutoff_exclusive':CUTOFF,'test_start':START,'test_end':END,
      'predictions_sha256_verified_unchanged':True,'prediction_sha256':actual_sha,
      'nh_prediction_races':manifest['nh_races'],'settled_race_counts':dict(coverage),
      'nh_final_result_races_known':total_nh,'nh_final_races_without_prediction':len(missing_nh),
      'models':list(MODELS),'scorecard':bymodel,'by_discipline':bycode,
      'unpriced_selection_count':sum(r['settlement_status']=='FINISHED_SP_MISSING' for r in settled),
      'history_state':manifest['history_state'],
      'hpr_v2_pairs_from_september_30_or_earlier':manifest['frozen_hpr_identity_discipline_pairs'],
      'hpr_overlay_active_races':manifest.get('hpr_overlay_active_races',0),
      'pre_oct_provider_id_parentage_links':manifest.get('verified_precutoff_provider_id_pedigrees',0),
      'pedigree_join_source_audit':manifest.get('pedigree_link_audit',{}),
      'limitations':[
        'Outcome blind from October 1 only; model development for E75/HPRv2 happened retrospectively after the period.',
        'Official rating, race type, ground and other race-day declarations remain available on the original morning card; direct form strings are masked.',
        'Original D/D(c) are frozen recipes, but race-day inputs may contain indirectly updated official handicap marks.',
        'Uncalibrated fixed-temperature model-implied win shares sum to 100% per race; do not treat as calibrated probabilities.',
        'Only dates and races with verified before-09:45 Git pre-off cards are predicted; many early October races are uncovered.',
        'Results are revealed after the prediction file is written and hashed; rankings are NEVER changed after nonrunners are known.',
        'Race names and winning outcomes are used in settlement only, not to pick races in the prediction phase.',
        'Missing SP excluded from ROI, not assigned odds; 1 unit staked per priced selection.',
        'HPR v2 past September scores use Stage94 research formula, retrospectively designed; no postcutoff observations included.',
        'Many racecards omit sire/dam; HPRv2 is unavailable unless verified pre-cutoff provider IDs supply them. If no overlays are active, the comparison does not test their effect.',
        'The quality/coverage of the historical snapshots limits this comparison; not a prospective trading track record.',
      ]}
    (out/'stage97-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'phase':'SETTLE','nh_prediction_races':manifest['nh_races'],
       'settlement':dict(coverage),'national_hunt_final_races':total_nh,
       'without_blind_picks':len(missing_nh),
       'rank1': [r for r in bymodel if r['rank']=='1']},indent=2),flush=True)
    return report


def self_test():
    assert norm('Spec Of Light (GB)')==norm('Spec Of Light')
    assert venue('Southwell (AW)')=='SOUTHWELL'
    assert code('National Hunt Flat')=='NH_FLAT'
    assert code('Hurdle')=='HURDLE' and code('Chase')=='CHASE'
    assert zscores([2.,5.,8.])[2]>zscores([2.,5.,8.])[1]
    assert abs(sum(probabilities([.3,1.1,-.8]))-1)<1e-10
    scores=[.1,.3,1.2]
    assert apply_hpr(scores,[None]*3)[0]==scores
    assert not apply_hpr(scores,[10,30,None])[1]
    good,on,pct=apply_hpr(scores,[20,60,40])
    assert on and pct==1 and good[1]>scores[1]
    row={'horse':'Example (IRE)','sire':'Sire Example','dam':'Dam Example'}
    key=hashlib.sha256('|'.join(('example (ire)','sire example','dam example')).encode()).hexdigest()[:24]
    assert 'PEDIGREE_SHA256_'+key in pedigree_candidate_hashes(row)
    assert hpr_for({**row,'type':'Hurdle'}, {('PEDIGREE_SHA256_'+key,'HURDLE'):60.})==60.
    assert hpr_for({'horse':'Example','type':'Hurdle'},{('PEDIGREE_SHA256_'+key,'HURDLE'):60.}) is None
    recovered,kind=with_verified_parents({'horse':'Example (IRE)','horse_id':'hrs_1'},
            {'hrs_1':('EXAMPLE','sire example','dam example')})
    assert kind=='PREOCT_PROVIDER_ID_PARENTAGE' and recovered['sire']=='sire example'
    assert with_verified_parents({'horse':'Other','horse_id':'hrs_1'},
            {'hrs_1':('EXAMPLE','sire example','dam example')})[1]=='PROVIDER_ID_NAME_CONFLICT'
    print('PASS Stage97: hash-and-parent identity, frozen-time guards, rate math, no-HPR neutral, race code')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',required=True,choices=('predict','settle','self-test'))
    ap.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    ap.add_argument('--db',type=pathlib.Path,default=pathlib.Path('reports/stage97/base/backfilled_research_only.sqlite'))
    ap.add_argument('--ratings',type=pathlib.Path,default=pathlib.Path('reports/stage97/hpr-frozen.sqlite'))
    ap.add_argument('--hpr',type=pathlib.Path,default=pathlib.Path('reports/stage97/hpr-preoct01.csv.gz'))
    ap.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage97/output'))
    args=ap.parse_args()
    if args.mode=='self-test':self_test()
    elif args.mode=='predict':predict(args)
    else:settle(args)

if __name__=='__main__':main()
