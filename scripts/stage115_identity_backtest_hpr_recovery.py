#!/usr/bin/env python3
"""Stage115 read-only paired racing research: verified D identity, D(c) fixed C, HPR proposal.

- No production state/model writes, no retraining frozen models.
- Historical D evaluations only from saved PREOFF git card snapshots, unique full-field
  final results and prices verified in archived final results.
- Date-aware historical inputs, preserving strict < race day requirement.
- Corrected D identity needs identical horse name plus exact sire+dam (or identical ID).
- D(c): uses recorded published per-runner C probabilities unchanged. Reconstructs
  baseline and repaired D component using identical archived D research history.
  Explicitly NOT the complete exact published D(c) pipeline or corrected C model.
- HPR: genuine archived finished runs ONLY -> hypothetical research HPR ratings;
  NEVER writes the existing ledger and NEVER uses target-date finishes.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, sqlite3, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import stage114_exact_identity_and_form_research as A

VERSION='stage115-v1-2026-11-13'

def write_csv(path,rows,fields=None):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if fields is None:fields=list(rows[0]) if rows else ['record']
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        w.writeheader();w.writerows(rows)

def save_json(path,value):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')

def norm(s):return A.normname(s)

def dweights(root):
    import stage38_model_dz_live as D
    audit=json.loads((pathlib.Path(root)/'reports/stage30/stage30-summary.json').read_text())
    m=audit['model_results']['D']
    if audit.get('test_year')!=2025 or m.get('chosen_combination')!='D-064-gap14' or abs(float(m.get('selection_margin',0))-.14)>1e-9:
        raise RuntimeError('Frozen D provenance/threshold changed')
    w=[float(m['weights_pct'][x])/100 for x in D.METRICS]
    if len(w)!=8 or abs(sum(w)-1)>.002:raise RuntimeError('Frozen eight D factor weights mismatch')
    return w

def canonical_key(card):
    import stage86_hpr_engine as H
    return H.race_key(card)

def prepared_history(db,starting_date):
    import stage38_model_dz_live as D
    past=D.LivePast({})
    info=past.scan_until(db,starting_date)
    return past,info

def group_incremental_d(db,start,end):
    """Read-only completed archive updates, with exact Stage38 tuple layout."""
    by_day=collections.defaultdict(list)
    con=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
     u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
     u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment
     FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
     WHERE r.race_date>=? AND r.race_date<=? ORDER BY r.race_date,r.id'''
    try:
        current=None;grp=[]
        for row in con.execute(sql,(start,end)):
            if current is not None and row[0]!=current:
                by_day[grp[0][1]].append(grp);grp=[]
            grp.append(tuple(row));current=row[0]
        if grp:by_day[grp[0][1]].append(grp)
    finally:con.close()
    return by_day

def get_countries(past):
    known={}
    for name,country in past.last:
        if name not in known:known[name]=country
        elif known[name]!=country:known[name]=None
    return known

def paired_d_cards(cards,past,day,archive_index,weights):
    """Use same frozen eight D scores; only safe verified identity changes."""
    import stage38_model_dz_live as D
    ctry=get_countries(past)
    source=[D.live_row(c,day,'STAGE115|'+day+'|'+str(c.get('course'))+'|'+str(c.get('race_time')),ctry) for c in cards]
    fixed=[];swaps=[]
    for r,z in zip(cards,source):
        h=A.candidate_keys(r,archive_index.get(norm(r['horse']),[]),day)
        identity=h['D_verified_key_candidate']
        patch=list(z)
        if identity and identity in past.last and tuple(past.identity(z))!=identity:
            patch[8]=identity[0];patch[10]=identity[1];swaps.append(r['horse'])
        fixed.append(tuple(patch))
    b=past.score(source,live=True);c=past.score(fixed,live=True)
    if b is None or c is None:return None
    old,oldorder,_=D.prediction_rows(b,weights)
    corrected,neworder,_=D.prediction_rows(c,weights)
    return {'old':old,'corrected':corrected,'oldorder':list(oldorder),'neworder':list(neworder),
            'identity_repaired':swaps,'cards':cards}

def zscores(a):
    n=len(a);average=sum(float(x) for x in a)/n
    variance=sum((float(x)-average)**2 for x in a)/n
    sd=variance**.5+1e-8
    return [(float(x)-average)/sd for x in a]

def combine_dc(d,c):
    zd=zscores(d);zc=zscores(c)
    return [.75*a+.25*b for a,b in zip(zd,zc)]

def ranks(s):return sorted(range(len(s)),key=lambda i:(-float(s[i]),i))

def comparable_rank_rows(day,course,time,cards,b):
    old=b['oldorder'];new=b['neworder'];out=[]
    for j,horse in enumerate(cards):
        out.append({'date':day,'course':course,'time':time,'horse':horse['horse'],
                    'original_rank':old.index(j)+1,'corrected_rank':new.index(j)+1,
                    'original_D_score':round(float(b['old'][j]),6),'corrected_D_score':round(float(b['corrected'][j]),6),
                    'pedigree_repair_applied':int(horse['horse'] in b['identity_repaired'])})
    return out

def full_field_check(race,preoff):
    import stage94_dc_probability_replay as S94
    import stage86_hpr_engine as H
    cards,status=S94.verify_preoff_field(preoff,race)
    if status!='OK':return None,status
    named={H.norm(z['horse']):z for z in race.get('runners',[]) if z.get('horse')}
    if len(named)<len(cards) or not all(H.norm(c['horse']) in named for c in cards):return None,'RESULT_FIELD_INCONSISTENT'
    wins=[i for i,c in enumerate(cards) if H.win_status(named[H.norm(c['horse'])])]
    if len(wins)!=1:return None,'WINNER_AMBIGUOUS'
    return (cards,wins[0]),'OK'

def recorded_dc_preoff(root,start,end):
    """Read published per-runner C scores from historical Git revisions only.

    A missing or incomplete C snapshot makes that race ineligible for a
    historically settled D(c) comparison; never backfill from today's feed.
    """
    from stage86_hpr_engine import course
    source={};aud={}
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    for n in range((b-a).days+1):
        day=(a+dt.timedelta(days=n)).isoformat()
        proc=subprocess.run(['git','log','-1','--format=%H','--until',day+' 09:45:00 +0000',
            '--','data/strategy-dc-probabilities.json'],cwd=root,capture_output=True,text=True)
        sha=proc.stdout.strip() if proc.returncode==0 else ''
        if not sha:aud[day]='NO_PREOFF_DC_COMMIT';continue
        j=subprocess.run(['git','show',sha+':data/strategy-dc-probabilities.json'],
             cwd=root,capture_output=True,text=True)
        if j.returncode!=0:aud[day]='PREOFF_DC_READ_FAILED';continue
        try:payload=json.loads(j.stdout)
        except ValueError:aud[day]='PREOFF_DC_INVALID_JSON';continue
        accepted=0
        for race in payload.get('races',[]):
            if race.get('date')!=day:continue
            timestamp=str(race.get('published_at') or payload.get('generated_at') or '')
            # The commit itself was already published before 09:45, but this
            # secondary check detects inconsistent future-dated metadata.
            if timestamp[:10]>day or (timestamp[:10]==day and timestamp[11:16]>'09:45'):
                continue
            key=(day,course(race.get('course')),str(race.get('time') or ''))
            items=race.get('runners',[])
            if len(items)<2 or any(not row.get('horse') or row.get('race_type_score') is None for row in items):continue
            if len({norm(row['horse']) for row in items})!=len(items):continue
            source[key]=race;accepted+=1
        aud[day]={'git_commit':sha,'race_fields':accepted}
    return source,aud


def stat_rows(records,prefix):
    """Same intersection-priced rules for frozen and corrected paired arms."""
    if not records:return []
    index=collections.defaultdict(dict)
    for r in records:index[(r['race_key'],r['rank'])][r['method']]=r
    cohort=set(k for k,z in index.items() if len(z)==2 and all(v['sp_decimal']!='' for v in z.values()))
    methods=sorted(set(r['method'] for r in records))
    all_rows=[]
    for grouping in ('ALL','DAY','DISCIPLINE','RACE_TYPE'):
        categories=['ALL'] if grouping=='ALL' else sorted(set(str(r['date'] if grouping=='DAY' else r['discipline'] if grouping=='DISCIPLINE' else r['race_type']) for r in records))
        for category in categories:
            for rank in (1,2,'1+2'):
                for model in methods:
                    selected=[r for r in records if r['method']==model and (rank=='1+2' or r['rank']==rank) and
                       (grouping=='ALL' or str(r['date'] if grouping=='DAY' else r['discipline'] if grouping=='DISCIPLINE' else r['race_type'])==category)]
                    priced=[r for r in selected if (r['race_key'],r['rank']) in cohort]
                    stake=len(priced);win=sum(int(r['won']) for r in selected)
                    profit=sum(float(r['pnl_1unit']) for r in priced)
                    all_rows.append({'group':grouping,'value':category,'rank':rank,'model':model,
                        'race_picks':len(selected),'wins':win,
                        'strike_rate_pct':round(100*win/len(selected),2) if selected else '',
                        'paired_sp_verified_picks':stake,'paired_sp_wins':sum(int(r['won']) for r in priced),
                        'roi_pct':round(100*profit/stake,2) if stake else '',
                        'profit_1unit':round(profit,5) if stake else ''})
    return all_rows


def backtest(root,db,start,end,out):
    """No inferred pre-off cards or results. Small honest sample is acceptable."""
    import stage30_gap_aware_abcd as OLD
    import stage86_hpr_engine as H
    import stage94_dc_probability_replay as S94
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    if b<a or b>=dt.date(2026,11,14):raise ValueError('Only completed before 14 Nov dates')
    with sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True) as c:
        latest=c.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0]
    if latest<end:raise RuntimeError('Frozen archived D history missing dates in backtest; end must be <= '+str(latest))
    weights=dweights(root)
    resultdb,_=H.load_results(pathlib.Path(root),a,b)
    cards_by_date=H.card_dates(pathlib.Path(root),a,b,git_preoff=True)
    sources=S94.price_candidates(root,a,b)
    historical_dc, dc_audit=recorded_dc_preoff(root,start,end)
    past,scan=prepared_history(db,start)
    increment=group_incremental_d(db,start,end)
    # Locate candidates only among historical pre-target rows. No name-only merges.
    all_cards=[z for ds in cards_by_date.values() for z in ds if z.get('horse')]
    idx=A.historical_candidates(db,all_cards,end)
    records=[];runner=[];reject=collections.Counter();stage=[]
    dc_records=[];dc_races=[];dc_reject=collections.Counter()
    days=[(a+dt.timedelta(days=i)).isoformat() for i in range((b-a).days+1)]
    for day in days:
        day_cards=H.by_race_cards(cards_by_date.get(day,[]))
        races=sorted((k,r) for k,r in resultdb.items() if str(k).startswith(day+'|'))
        for race_key,race in races:
            field,matched_status=H.card_match(race,day_cards)
            if matched_status!='PREOFF_FIELD_VERIFIED':reject[matched_status]+=1;continue
            checked,reason=full_field_check(race,field)
            if checked is None:reject[reason]+=1;continue
            cards,winner_idx=checked
            paired=paired_d_cards(cards,past,day,idx,weights)
            if paired is None:reject['D_SCORE_UNAVAILABLE']+=1;continue
            o=paired['oldorder'];n=paired['neworder'];day_time=H.off(race.get('off_dt') or race.get('off'))
            outcomes=[]
            for method,order in [('FROZEN_D',o),('VERIFIED_ID_D',n)]:
                chosen=[]
                for position,chosen_idx in enumerate(order[:2],1):
                    picked=cards[chosen_idx]
                    price,decimal,price_source=S94.choose_price(sources,race_key,picked['horse'])
                    # Equivalent, explicit both rank1 and rank2 independent win bets.
                    won=(chosen_idx==winner_idx)
                    row={'date':day,'course':picked.get('course'),'time':day_time,
                         'race_key':race_key,'race_type':S94.race_cat(picked),
                         'discipline':S94.race_disc(picked),
                         'method':method,'rank':position,'horse':picked['horse'],
                         'won':int(won),'finish':'WINNER' if won else 'NOT_WINNER',
                         'sp_decimal':round(decimal,6) if decimal is not None else '',
                         'sp_source':price_source if decimal is not None else '',
                         'pnl_1unit':round((decimal-1) if won else -1.,6) if decimal is not None else '',
                         'preoff_verified':True,
                         'score':round(float((paired['old'] if method=='FROZEN_D' else paired['corrected'])[chosen_idx]),6)}
                    records.append(row);chosen.append(row)
                outcomes.append(chosen)
            stage.append({'date':day,'course':cards[0]['course'],'time':day_time,
                          'race_key':race_key,'runners':len(cards),'winner':cards[winner_idx]['horse'],
                          'original_rank1':cards[o[0]]['horse'],'reconciled_rank1':cards[n[0]]['horse'],
                          'rank1_changed':int(o[0]!=n[0]),
                          'original_rank2':cards[o[1]]['horse'],'reconciled_rank2':cards[n[1]]['horse'],
                          'rank2_changed':int(o[1]!=n[1]),'identity_repairs':len(paired['identity_repaired'])})
            runner.extend(comparable_rank_rows(day,cards[0]['course'],day_time,cards,paired))
            # Independent frozen D(c) comparison using only C scores actually
            # published BEFORE the historical race. Never recalculate C from
            # later training data or use the modern feed retroactively.
            dc=historical_dc.get((day,H.course(cards[0].get('course')),day_time))
            if dc is None:
                dc_reject['NO_PREOFF_PUBLISHED_C_SCORES']+=1
            else:
                named={norm(z['horse']):z for z in dc.get('runners',[])}
                if len(named)!=len(cards) or not all(norm(z['horse']) in named for z in cards):
                    dc_reject['PUBLISHED_C_FIELD_DIFFERS']+=1
                else:
                    cx=[float(named[norm(c['horse'])]['race_type_score']) for c in cards]
                    o_dc=ranks(combine_dc(paired['old'],cx))
                    n_dc=ranks(combine_dc(paired['corrected'],cx))
                    pub_dc=sorted(range(len(cards)),key=lambda i:int(named[norm(cards[i]['horse'])]['rank']))
                    dc_races.append({'date':day,'course':cards[0]['course'],'time':day_time,
                        'race_key':race_key,'field_size':len(cards),
                        'frozen_published_rank1':cards[pub_dc[0]]['horse'],
                        'independent_original_rank1':cards[o_dc[0]]['horse'],
                        'independent_repaired_rank1':cards[n_dc[0]]['horse'],
                        'original_reconstruction_matches_published_rank1':int(o_dc[0]==pub_dc[0]),
                        'D_component_rank1_changed':int(o_dc[0]!=n_dc[0]),
                        'D_component_rank2_changed':int(o_dc[1]!=n_dc[1]),
                        'C_probabilities_from_preoff_commit':True,'full_C_identity_correction':False})
                    for method,order in [('DC_PUBLISHED_C_BASELINE',o_dc),('DC_VERIFIED_D_COMPONENT',n_dc)]:
                        for rank,ch in enumerate(order[:2],1):
                            horse=cards[ch]
                            price,decimal,src=S94.choose_price(sources,race_key,horse['horse'])
                            won=int(ch==winner_idx)
                            dc_records.append({'date':day,'course':horse.get('course'),'time':day_time,
                                'race_key':race_key,'race_type':S94.race_cat(horse),
                                'discipline':S94.race_disc(horse),'method':method,
                                'rank':rank,'horse':horse['horse'],'won':won,
                                'sp_decimal':round(decimal,6) if decimal is not None else '',
                                'sp_source':src if decimal is not None else '',
                                'pnl_1unit':round((decimal-1) if won else -1.,6) if decimal is not None else '',
                                'preoff_verified':True})
        for group in increment.get(day,[]):
            if OLD.track(group[0][2]) in OLD.TRACKS:past.update(group)
    write_csv(out/'stage115-D-historical-paired-positions.csv',stage)
    write_csv(out/'stage115-D-historical-all-runners.csv',runner)
    write_csv(out/'stage115-D-historical-paired-results.csv',records,
              ['date','course','time','race_key','race_type','discipline','method','rank','horse','won','finish',
               'sp_decimal','sp_source','pnl_1unit','preoff_verified','score'])
    write_csv(out/'stage115-Dc-historical-paired-races.csv',dc_races)
    write_csv(out/'stage115-Dc-historical-paired-results.csv',dc_records,
             ['date','course','time','race_key','race_type','discipline','method','rank','horse','won',
              'sp_decimal','sp_source','pnl_1unit','preoff_verified'])
    write_csv(out/'stage115-Dc-historical-strike-ROI.csv',stat_rows(dc_records,'DC'))
    dc_summary={'matched_completed_races':len(dc_races),'original_rank1_reconstruction_agrees_published':
        sum(x['original_reconstruction_matches_published_rank1'] for x in dc_races),
        'rank1_changes_D_component_only':sum(x['D_component_rank1_changed'] for x in dc_races),
        'rank2_changes_D_component_only':sum(x['D_component_rank2_changed'] for x in dc_races),
        'preoff_C_commit_audit':dc_audit,'excluded':dict(dc_reject),
        'C_training_weights_or_history_modified':False,'not_full_C_repair':True}
    save_json(out/'stage115-Dc-historical-summary.json',dc_summary)
    # For ROI, compare ONLY the intersection with known SP on both arms of pair.
    paired_prices=set()
    index=collections.defaultdict(dict)
    for r in records:index[(r['race_key'],r['rank'])][r['method']]=r
    for key,z in index.items():
        if len(z)==2 and all(q['sp_decimal']!='' for q in z.values()):paired_prices.add(key)
    stats=[]
    for grouping in ('ALL','DAY','DISCIPLINE','RACE_TYPE'):
        keys=(['ALL'] if grouping=='ALL' else sorted(set(str(r['date'] if grouping=='DAY' else r['discipline'] if grouping=='DISCIPLINE' else r['race_type']) for r in records)))
        for key in keys:
            for rank in (1,2,'1+2'):
                for method in ('FROZEN_D','VERIFIED_ID_D'):
                    selected=[r for r in records if (r['rank']==rank or rank=='1+2') and r['method']==method and
                              (grouping=='ALL' or str(r['date'] if grouping=='DAY' else r['discipline'] if grouping=='DISCIPLINE' else r['race_type'])==key)]
                    priced=[r for r in selected if (r['race_key'],r['rank']) in paired_prices]
                    stake=len(priced)
                    pnl=sum(float(r['pnl_1unit']) for r in priced)
                    stats.append({'group':grouping,'value':key,'rank':rank,'model':method,
                                  'race_picks':len(selected),'wins':sum(r['won'] for r in selected),
                                  'strike_rate_pct':round(100*sum(r['won'] for r in selected)/len(selected),2) if selected else '',
                                  'paired_sp_verified_picks':stake,
                                  'paired_sp_wins':sum(r['won'] for r in priced),
                                  'roi_pct':round(100*pnl/stake,2) if stake else '',
                                  'profit_1unit':round(pnl,5) if stake else ''})
    write_csv(out/'stage115-D-historical-strike-ROI.csv',stats)
    summary={'status':'COMPLETED_BACKTEST_PAPER_ONLY','start':start,'end':end,
             'race_pairs_evaluated':len(stage),'race_rank1_changes':sum(x['rank1_changed'] for x in stage),
             'race_rank2_changes':sum(x['rank2_changed'] for x in stage),
             'paired_sp_rank1_count':sum(k[1]==1 for k in paired_prices),
             'paired_sp_rank2_count':sum(k[1]==2 for k in paired_prices),
             'rejected':dict(reject),'source_state':scan,'D_c_historical_pair':dc_summary,
             'strictly_preoff_git_snapshot_required':True,'same_day_results_not_used_for_predicting':True,
             'odds_or_sp_not_used_in_selection':True,'backtest_scope_limitations':
               'Only preoff archived cards with full verified final fields; incomplete SP pairs excluded equally from ROI.'}
    save_json(out/'stage115-D-historical-summary.json',summary)
    return summary

def tomorrow_dc(root,source_db,stage114_csv,feed,out):
    """Frozen C probabilities, paired Stage114 D scores. D(c) full C repair is NOT implied."""
    source=list(csv.DictReader(open(stage114_csv,newline='',encoding='utf-8')))
    feed=json.loads(pathlib.Path(feed).read_text())
    bykey=collections.defaultdict(dict)
    for z in source:
        if z['date']=='2026-11-14':bykey[(A.normname(z['course']),z['time'])][A.normname(z['horse'])]=z
    results=[];runner=[];reason=collections.Counter()
    for race in feed.get('races',[]):
        if race.get('date')!='2026-11-14':continue
        key=(A.normname(race['course']),str(race.get('time')))
        old=bykey.get(key,{})
        arr=race.get('runners',[])
        if len(arr)<2 or len(old)!=len(arr) or not all(A.normname(h['horse']) in old for h in arr):
            reason['NO_COMPLETE_STAGE114_PAIRED_FIELD']+=1;continue
        # Stage114 independent frozen D source may be a different archive than
        # published D(c); avoid pretending to replicate its live baseline.
        o=[float(old[A.normname(h['horse'])]['original_score']) for h in arr]
        n=[float(old[A.normname(h['horse'])]['reconciled_score']) for h in arr]
        c=[float(h['race_type_score']) for h in arr]
        published=[float(h['original_d_score']) for h in arr]
        base=ranks(combine_dc(o,c));repaired=ranks(combine_dc(n,c))
        pub=sorted(range(len(arr)),key=lambda i:(int(arr[i]['rank']),i))
        # Compare published component score's original D to independent Stage114
        # rather than silently assert the same frozen source DB.
        delta=sum(abs(o[i]-published[i]) for i in range(len(arr)))/len(arr)
        changed=[arr[i]['horse'] for i in range(len(arr)) if int(old[A.normname(arr[i]['horse'])]['identity_repaired'])]
        results.append({'date':'2026-11-14','course':race['course'],'time':race['time'],
                        'runners':len(arr),'identity_repairs':len(changed),
                        'published_dc_rank1':arr[pub[0]]['horse'],
                        'reconstructed_dc_rank1':arr[base[0]]['horse'],
                        'D_component_repaired_dc_rank1':arr[repaired[0]]['horse'],
                        'D_component_rank1_changed':int(base[0]!=repaired[0]),
                        'published_dc_rank2':arr[pub[1]]['horse'],
                        'reconstructed_dc_rank2':arr[base[1]]['horse'],
                        'D_component_repaired_dc_rank2':arr[repaired[1]]['horse'],
                        'D_component_rank2_changed':int(base[1]!=repaired[1]),
                        'reconstructed_baseline_agrees_with_published_rank1':int(base[0]==pub[0]),
                        'mean_D_score_difference_from_published':round(delta,6),
                        'C_probabilities_frozen':True,'C_history_unrepaired':True})
        for i,h in enumerate(arr):
            runner.append({'date':'2026-11-14','course':race['course'],'time':race['time'],'horse':h['horse'],
                           'published_dc_rank':int(h['rank']),'reconstructed_dc_rank':base.index(i)+1,
                           'D_component_repaired_dc_rank':repaired.index(i)+1,
                           'published_C_probability':round(c[i],7),'original_published_D_score':round(published[i],6),
                           'independent_D_old_score':round(o[i],6),'independent_D_repaired_score':round(n[i],6),
                           'identity_repaired':int(h['horse'] in changed)})
    write_csv(out/'stage115-Dc-14Nov-frozen-C-paired-races.csv',results)
    write_csv(out/'stage115-Dc-14Nov-all-runners.csv',runner)
    summ={'status':'D_C_D_COMPONENT_ONLY','races_compared':len(results),
          'repaired_rank1_changes':sum(r['D_component_rank1_changed'] for r in results),
          'repaired_rank2_changes':sum(r['D_component_rank2_changed'] for r in results),
          'baseline_rank1_agrees_with_published':sum(r['reconstructed_baseline_agrees_with_published_rank1'] for r in results),
          'rejections':dict(reason),'not_full_D_c_correction':True,'C_probabilities_reused_unmodified':True,
          'does_not_claim_reproduction_of_published_D_c':True}
    save_json(out/'stage115-Dc-summary.json',summ)
    return summ

def audit_dc_c_history(db,stage114_csv,cards_path,out):
    """Read C history unchanged, test uniquely verified name/country aliases.

    No classifier is refitted. This reports recoverable C history, not revised
    C probabilities or complete D(c) live scores.
    """
    import ast
    import stage65p_race_type_c_only as C
    from stage112_form_coverage_reconcile import is_starter,race_key
    by_card={}
    data=json.loads(pathlib.Path(cards_path).read_text())
    for q in data.get('runners',[]):
        if str(q.get('date'))=='2026-11-14' and is_starter(q):
            by_card[('|'.join(race_key(q)),A.normname(q['horse']))]=q
    audited=list(csv.DictReader(open(stage114_csv,newline='',encoding='utf-8')))
    form=C.Form();conn=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    query="""SELECT r.race_date,r.id,r.course,r.race_type,r.surface,r.race_class,
             u.horse_name,u.horse_country,u.trainer,u.jockey,u.official_rating,
             u.weight_lbs,u.finish_pos,u.beaten,u.rpr
             FROM historical_runs u JOIN historical_races r ON u.race_id=r.id
             WHERE r.race_date >= '2021-01-01' AND r.race_date < '2026-11-14'
             ORDER BY r.race_date,r.id"""
    schema=('date','race_id','course','race_type','surface','race_class','horse',
            'country','trainer','jockey','or','weight','finish','beaten','rpr')
    n=0
    try:
        current=None;group=[]
        def addgroup():
            nonlocal n
            valid=[z for z in group if C.num(z.get('finish')) is not None and C.num(z['finish'])>0]
            if len(valid)<2:return
            for z in valid:
                z['field_size']=len(valid);form.update(z)
            n+=1
        for raw in conn.execute(query):
            if current is not None and current!=raw[1]:
                addgroup();group=[]
            group.append(dict(zip(schema,raw)));current=raw[1]
        if group:addgroup()
    finally:conn.close()
    rows=[];scores=collections.Counter()
    for r in audited:
        if r['date']!='2026-11-14':continue
        card=by_card.get((r['race_key'],A.normname(r['horse'])))
        if not card:continue
        old=(C.name(card.get('horse')),C.name(card.get('horse_country')))
        alias=r.get('D_c_C_verified_key_candidate') or ''
        try:fixed=ast.literal_eval(alias) if alias.strip() else None
        except (ValueError,SyntaxError):fixed=None
        if not (isinstance(fixed,tuple) and len(fixed)==2 and all(isinstance(z,str) for z in fixed)):
            fixed=None
        orig_n=len(form.h.get(old,[]))
        verified_n=len(form.h.get(fixed,[])) if fixed else orig_n
        patch=bool(fixed and verified_n>0 and fixed!=old and not orig_n)
        rows.append({'date':'2026-11-14','horse':card['horse'],'course':card.get('course'),
           'time':race_key(card)[-1],'prior_runs_original_C':orig_n,
           'prior_runs_with_verified_C_key':verified_n if patch else orig_n,
           'C_key_change_verifiable':int(patch),'C_exact_alias':str(fixed) if patch else '',
           'model_C_classifier_not_rerun':True})
        scores['runners']+=1;scores['original_C_matched']+=int(orig_n>0)
        scores['C_recoverable_newly']+=int(patch)
        scores['C_matched_after_proposed_alias']+=int(orig_n>0 or patch)
    write_csv(out/'stage115-Dc-C-alias-candidates.csv',rows)
    result={'source':'immutable archived results; original Stage65P C historical updater',
            'historical_races_replayed':n,**dict(scores),'C_classifier_was_not_retrained':True,
            'C_feature_and_win_probability_changes_NOT_claimed':True,
            'original_live_D_c_unmodified':True}
    save_json(out/'stage115-Dc-C-coverage-summary.json',result)
    return result


def propose_hpr(repo,db,hpr_path,cards_path,source_audit,out,target='2026-11-14'):
    """Propose missing archive HPR results with exact pedigree only; no store writes.

    Existing HPR ratings cannot be retroactively modified. The report is
    research-only and overlays only a separate, generated candidate list.
    """
    import stage86_hpr_engine as H
    cards=json.loads(pathlib.Path(cards_path).read_text()).get('runners',[])
    cards=[z for z in cards if str(z.get('date'))==target and z.get('horse') and z.get('sire') and z.get('dam')]
    profiles={H.identity(z):z for z in cards if H.identity(z)}
    # Read only: historical_matches requires matching full pedigree or ID, and
    # rejects unverified name-only records.
    archival,query_audit=H.historical_matches(db,profiles,target)
    keyed=collections.defaultdict(list)
    for e in archival:
        if str(e.get('date',''))<'2025-01-01':continue
        if not e.get('horse_key') or not e.get('race_key') or not H.number(e.get('position')):continue
        keyed[e['horse_key']].append(e)
    cx=sqlite3.connect('file:'+str(pathlib.Path(hpr_path).resolve())+'?mode=ro',uri=True)
    cx.execute('PRAGMA query_only=ON')
    candidates=[];per=[];counts=collections.Counter();report=[]
    try:
        for key,p in sorted(profiles.items()):
            existing=H.previous_ratings(cx,key,target)
            prior=sorted((z for z in existing if z['date']<target),key=lambda z:z['date'])
            observed={(str(z.get('date')),H.course(z.get('course'))) for z in prior}
            generated=[]
            for row in sorted(keyed.get(key,[]),key=lambda z:(z['date'],z['race_key'])):
                same=(str(row['date']),H.course(row.get('course')))
                if same in observed:
                    counts['already_rated_same_date_course']+=1;continue
                # Never use later ratings when scoring an earlier completed race.
                before=[z for z in [*prior,*generated] if z['date']<row['date']]
                rating,reason=H.score_event(row,before)
                if rating is None:counts['unscorable_'+str(reason)]+=1;continue
                rating['source']='STAGE115_HISTORICAL_PEDIGREE_RESEARCH_PROPOSAL'
                rating['staging_only']=True
                generated.append(rating)
                observed.add(same);counts['scorable_verified_missing_archive_run']+=1
                candidates.append({'horse_key':key,'horse':p['horse'],'race_date':row['date'],
                                   'course':row.get('course'),'race_id':row.get('source_race_id'),
                                   'hpr_candidate':rating['hpr'],'quality_status':rating['quality_status'],
                                   'confidence':rating['confidence'],
                                   'prior_count':rating['prior_hpr_count'],
                                   'identity_evidence':'FULL_PEDIGREE_OR_IDENTICAL_ID',
                                   'source':'archive_pedigree_or_id','research_only':True})
            before=H.preoff_hpr(prior,p) if prior else None
            afterprior=sorted([*prior,*generated],key=lambda z:z['date'])
            after=H.preoff_hpr(afterprior,p) if afterprior else None
            per.append({'horse':p['horse'],'horse_key':key,'declared_previous_form':bool(p.get('form')),
                        'original_HPR_history_count':len(prior),'proposed_additional_rating_count':len(generated),
                        'HPR_before':before if before is not None else '',
                        'HPR_after_research_only':after if after is not None else '',
                        'rated_before':int(before is not None),'rated_after_research_only':int(after is not None),
                        'source_confirmed':bool(keyed.get(key))})
    finally:cx.close()
    write_csv(out/'stage115-HPR-proposed-history-ratings.csv',candidates)
    write_csv(out/'stage115-HPR-coverage-before-after.csv',per)
    s={'target':target,'archive_matched_events':query_audit,
       'new_proposed_ratings':len(candidates),'horses_with_new_proposed_ratings':sum(z['proposed_additional_rating_count']>0 for z in per),
       'HPR_preoff_available_before':sum(z['rated_before'] for z in per),
       'HPR_preoff_available_after_research_only':sum(z['rated_after_research_only'] for z in per),
       'unrated_after_proposal':sum(not z['rated_after_research_only'] for z in per),
       'rejections':dict(counts),'source_database_modified':False,'persistent_HPR_ledger_modified':False,
       'research_proposals_not_official_HPR':True,
       'only_exact_pedigree_or_identical_horse_id':True,'source_runs_prior_to_target':True}
    save_json(out/'stage115-HPR-summary.json',s)
    return s

def self_test():
    assert ranks([5,5,1])==[0,1,2]
    assert ranks(combine_dc([1,2,3],[3,2,1]))==[2,1,0]
    assert A.normname('Little Ledgend (IRE)')==A.normname('Little Ledgend')
    assert round(sum(combine_dc([1,1,1],[3,2,1])),8)==0.0
    old=[1,0,-1];c=[0,1,-1]
    z=combine_dc(old,c);assert len(z)==3 and max(z)>min(z)
    print('Stage115 PASS: frozen-C blend, deterministic ranks, exact identity parsing, no synthetic racing outcome')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--repo',default='.')
    p.add_argument('--db');p.add_argument('--hpr');p.add_argument('--cards');p.add_argument('--stage114')
    p.add_argument('--dc-feed');p.add_argument('--out')
    p.add_argument('--backtest-start',default='2026-11-04')
    p.add_argument('--backtest-end',default='2026-11-10')
    args=p.parse_args()
    if args.self_test:self_test();return
    if any(not getattr(args,x) for x in ('db','hpr','cards','stage114','dc_feed','out')):p.error('Required --db --hpr --cards --stage114 --dc-feed --out')
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    summ={}
    summ['D_backtest']=backtest(args.repo,args.db,args.backtest_start,args.backtest_end,out)
    summ['D_c_frozen_C']=tomorrow_dc(args.repo,args.db,args.stage114,args.dc_feed,out)
    summ['D_c_C_alias_coverage']=audit_dc_c_history(args.db,args.stage114,args.cards,out)
    summ['HPR_candidacy']=propose_hpr(args.repo,args.db,args.hpr,args.cards,args.stage114,out)
    summ['status']='PAPER_ONLY_NO_LIVE_CHANGE';summ['version']=VERSION
    save_json(out/'stage115-master-summary.json',summ)
    print(json.dumps(summ,indent=2))

if __name__=='__main__':main()
