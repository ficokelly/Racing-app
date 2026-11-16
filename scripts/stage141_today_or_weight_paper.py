#!/usr/bin/env python3
"""Same-day, odds-blind Stage141 OR_WEIGHT paper picks (Q4 frozen recipe).

Scores declared racecard fields using Stage141's unchanged original HPR ability,
OR, winning-mark and carried-weight features. An isolated temporary historic DB
adds today's UNRESULTED fields with dummy result labels, solely because original
Stage137's feature extractor requires one finish_pos==1 per race. Dummy labels
are neither input features nor used for training, ranking or result reporting.
The source historical DB, live app, frozen D and original Stage141 stay untouched.
"""
import argparse, collections, csv, datetime as dt, json, pathlib, shutil, sqlite3
import numpy as np
import import_historical_ukie as I
import stage30_gap_aware_abcd as D
import stage108_model_g2 as G
import stage137_frozen_d_race_type_walkforward as S
import stage139_hpr_137_138_walkforward as H
import stage141_grade_weight_2026 as M

UTC=dt.timezone.utc

def utcnow(): return dt.datetime.now(UTC)
def timeval(s):
    x=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
    if x.tzinfo is None: raise ValueError('Race time has no UTC offset: '+str(s))
    return x.astimezone(UTC)
def stopped(x):
    return x is True or str(x or '').strip().lower() in ('true','yes','1','nr','withdrawn','nonrunner','non-runner','scratched','dns','abandoned','cancelled')
def missing_or_number(x): return M.number(x)

def today_cards(path,date):
    obj=json.loads(path.read_text())
    collected=timeval(obj['collected_at']);now=utcnow()
    if collected>now+dt.timedelta(minutes=2): raise ValueError('Future racecard timestamp')
    if not (now-collected)<dt.timedelta(hours=26): raise ValueError('Racecards are more than 26 hours old')
    grouped=collections.defaultdict(list);excluded=collections.Counter()
    for z in obj.get('runners',[]):
        if str(z.get('date'))!=date:continue
        if any(stopped(z.get(k)) for k in ('is_non_runner','is_withdrawn','runner_status','race_status','reserve_status')):
            excluded['withdrawn_or_not_running']+=1;continue
        try:off=timeval(z['race_time'])
        except (ValueError,KeyError):excluded['invalid_off']+=1;continue
        if off.date().isoformat()!=date:excluded['wrong_date']+=1;continue
        if not z.get('horse'):excluded['missing_name']+=1;continue
        grouped[(str(z['race_id']),str(z['course']),off.isoformat())].append(z)
    races=[]
    for (rid,course,off),field in sorted(grouped.items(),key=lambda kv:kv[0][2]):
        country=G.paper_region(field[0])
        if not country:excluded['unsupported_country']+=1;continue
        keys=[G.identity({'horse':r.get('horse'),'horse_id':r.get('horse_id'),
                          'sire':r.get('sire'),'dam':r.get('dam')}) for r in field]
        if len(field)<4 or not all(keys) or len(set(keys))!=len(keys):
            excluded['small_field_or_unverifiable_identity']+=1;continue
        if len({str(r.get('race_name')) for r in field})!=1:
            excluded['race_title_conflict']+=1;continue
        races.append({'id':rid,'course':course,'off':timeval(off),'field':field,'region':country})
    if not races:raise RuntimeError('No identity-verifiable UK/Irish declared fields for '+date)
    return races,{'collected_at':obj['collected_at'],
                  'snapshot_hours_old':round((now-collected).total_seconds()/3600,2),
                  'race_groups':len(grouped),'usable_fields':len(races),'excluded':dict(excluded)}

def scratch_archive(history,reference,races,date,out):
    scratch=out/'synthetic-preoff-scratch.sqlite';ref=out/'preoff-reference.csv'
    if scratch.exists():scratch.unlink()
    shutil.copy2(history,scratch)
    cx=sqlite3.connect(scratch)
    latest=cx.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0]
    if not latest or latest>=date:raise RuntimeError('Historical source overlaps today; refuse dummy records: '+str(latest))
    add=[]
    for rr in races:
        first=rr['field'][0];rid=rr['id'];rtype=str(first.get('type') or '')
        cx.execute('''INSERT INTO historical_races(id,race_date,course,race_type,surface,distance_f,going,race_class,source)
             VALUES(?,?,?,?,?,?,?,?,?)''',
             (rid,date,rr['course'],rtype,str(first.get('surface') or ''),
              missing_or_number(first.get('distance_f')),str(first.get('going') or ''),
              str(first.get('race_class') or ''),'UNRESULTED_PREOFF_FEATURE_ONLY'))
        for ix,r in enumerate(rr['field']):
            cx.execute('''INSERT INTO historical_runs(race_id,runner_key,horse_source_id,horse_name,horse_name_norm,
             horse_country,foaled,finish_pos,outcome,trainer,jockey,official_rating,weight_lbs,sp_decimal,
             draw,age,sex,sire,dam,rpr,topspeed,race_comment,beaten)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
             (rid,'PREOFF_'+str(ix).zfill(3),str(r.get('horse_id') or ''),str(r['horse']),I.normname(r['horse']),
              None,None,1 if ix==0 else 2,'FINISHED',r.get('trainer'),r.get('jockey'),
              missing_or_number(r.get('ofr')),missing_or_number(r.get('lbs')),
              None,missing_or_number(r.get('draw')),missing_or_number(r.get('age')),r.get('sex'),
              r.get('sire'),r.get('dam'),None,None,None,None))
        add.append({'stage2_race_id':rid,'date':date,'region_scope':rr['region'],
                    'course':rr['course'],'race_type':rtype,'race_name':first.get('race_name') or ''})
    cx.commit();cx.close()
    with reference.open('r',encoding='utf-8-sig',newline='') as src,ref.open('w',encoding='utf-8',newline='') as dest:
        reader=csv.DictReader(src);fields=reader.fieldnames or []
        assert {'stage2_race_id','date','region_scope','course','race_type','race_name'}.issubset(fields)
        writer=csv.DictWriter(dest,fieldnames=fields);writer.writeheader()
        for row in reader:writer.writerow(row)
        for row in add:writer.writerow(row)
    return scratch,ref,latest

def csvwrite(path,rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def run(args):
    date=args.date or utcnow().date().isoformat()
    if date!=utcnow().date().isoformat():raise RuntimeError('No backdated paper selections')
    args.out.mkdir(parents=True,exist_ok=True)
    cards,audit=today_cards(args.cards,date)
    scratch,ref,history_latest=scratch_archive(args.db,args.reference,cards,date,args.out)
    original=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D']
    assert original['chosen_combination']=='D-064-gap14'
    exact=np.asarray(D.candidates('D')[64],dtype=float);exact/=exact.sum()
    assert np.allclose([original['weights_pct'][p] for p in S.PILLARS],exact*100,atol=.011)
    S.FROZEN[:]=exact;S.CANDIDATES=S.candidate_weights();M.self_test()
    # Original Stage141 OR_WEIGHT arm: baseline race classification, unchanged HPR v3.1,
    # plus original 17 weight and 23 OR features. No grade-enhanced model substituted.
    base,source_audit,refaudit=M.construct_2026(scratch,ref,date)
    today={str(r['race_id']):r for r in base if r['date']==date}
    print('STAGE141_TODAY_FIELDS_CONSTRUCTED',len(today),'from',len(cards),flush=True)
    if not today:raise RuntimeError('No future race fields passed the frozen feature extractor')
    identities={h for r in base for h in r['identity'] if h}
    old_end=H.END
    try:
        H.END=date
        cached,hpr_audit=H.hpr_history(scratch,ref,args.out,identities)
    finally:H.END=old_end
    coverage=collections.Counter();H.attach_hpr(base,cached,coverage)
    weight_audit=M.weight_context(scratch,ref,base,args.out,date)
    # Exact Stage141 2026Q4 fit boundary. Today never enters training.
    training=[M.copy_race(r,weights=True,official=True) for r in base
              if '2023-01-01'<=r['date']<'2026-10-01']
    print('STAGE141_Q4_TRAINING_RACES',len(training),'through 2026-09-30',flush=True)
    banks=H.l137_ml_banks(training,ability=True)
    byid={r['id']:r for r in cards};top=[];full=[];skips=collections.Counter()
    for rid,r in sorted(today.items(),key=lambda kv:byid[kv[0]]['off']):
        off=byid[rid]['off']
        if off<=utcnow()+dt.timedelta(minutes=2):
            skips['already_off_or_imminent']+=1;continue
        prob,bank=S.predict_bank(banks,H.hpr_ability_137(M.copy_race(r,weights=True,official=True)))
        if not np.all(np.isfinite(prob)) or len(prob)!=len(r['horse']):raise RuntimeError('Invalid score '+rid)
        order=np.argsort(-prob,kind='stable')
        for rank,ix in enumerate(order,1):
            ix=int(ix)
            z={'date':date,'time_utc':off.strftime('%H:%M'),'meeting':r['course'],
               'race_type':r['subtype'],'official_tier':r['official_tier'],
               'horse':r['horse'][ix],'rank':rank,'raw_model_score_not_calibrated_win_pct':round(float(prob[ix]),6),
               'hpr':round(float(r['hpr_preoff'][ix]),2) if np.isfinite(r['hpr_preoff'][ix]) else '',
               'or':round(float(r['official_rating_today'][ix]),1) if np.isfinite(r['official_rating_today'][ix]) else '',
               'weight_lb':round(float(r['weight_today_lbs'][ix]),1) if np.isfinite(r['weight_today_lbs'][ix]) else '',
               'or_minus_weight_vs_field':round(float(r['or_features'][ix,2]),2) if np.isfinite(r['or_features'][ix,2]) else '',
               'last_handicap_winning_mark':round(float(r['or_features'][ix,11]),1) if np.isfinite(r['or_features'][ix,11]) else '',
               'back_to_winning_mark_good_form':int(r['or_features'][ix,20]) if np.isfinite(r['or_features'][ix,20]) else '',
               'field_size':len(order),'model':'D137_HPR_ABILITY_OR_WEIGHT',
               'bank':bank,'decision_utc':utcnow().isoformat(),'odds_used':False,'result_known':False}
            full.append(z)
            if rank<=2:top.append(z)
    if not top:raise RuntimeError('No races remain before the off after scoring')
    csvwrite(args.out/'stage141-today-rank1-rank2.csv',top)
    csvwrite(args.out/'stage141-today-all-runners.csv',full)
    report={'date':date,'model':'D137_HPR_ABILITY_OR_WEIGHT','status':'UNRESULTED_PREOFF_PAPER_ONLY',
       'ranked_races':len(top)//2,'trained_through':'2026-09-30',
       'historical_source_latest':history_latest,'cards':audit,'rejected_during_run':dict(skips),
       'current_fields_in_features':len(today),'hpr_coverage':dict(coverage),
       'missing_or_and_weight_not_imputed':True,'no_current_result_used':True,
       'artificial_result_labels_only_in_isolated_scratch':True,
       'source_original_and_live_app_unchanged':True,
       'limitations':['Original historical source last result '+history_latest,
          'Racecard from '+audit['collected_at']+'; check updated non-runners, ground and weights',
          'Scores are uncalibrated binary win-probability model outputs, not race-normalised winning chances',
          'No betting prices used or recommended']}
    (args.out/'stage141-today-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print('STAGE141_TODAY_RANKS',json.dumps([x for x in top if x['rank']==1],default=str),flush=True)
    print('STAGE141_TODAY_AUDIT',json.dumps(report,default=str),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--db',required=True,type=pathlib.Path)
    p.add_argument('--reference',required=True,type=pathlib.Path)
    p.add_argument('--cards',required=True,type=pathlib.Path)
    p.add_argument('--out',required=True,type=pathlib.Path)
    p.add_argument('--date',default='')
    run(p.parse_args())
