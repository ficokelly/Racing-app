#!/usr/bin/env python3
"""Stage114: source-grounded identity repair and prior-form audit. RESEARCH ONLY.

IMPORTANT: No production write, no mutation of Stage30/38 D, D(c), E, G3/G4/HPR,
no fabricated prior starts. D's archived historical horse_name_norm removes all
spaces whereas Stage38 live_row retains spaces. A correction is eligible ONLY
when one exact historic PEDIGREE or stable exact SOURCE ID proves the identity.
All outputs go into --out, and all model scoring uses unchanged frozen weights.

The Stage113b output is authoritative for original G3/G4 prior run counts and
for the coverage audit. No same-day completed results are used for predictions.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, pathlib, re, sqlite3, sys
from typing import Any

SCRIPT_DIR=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(SCRIPT_DIR))
import stage112_form_coverage_reconcile as S

DATE_MIN='2026-11-13'


def normname(s):
    import unicodedata
    t=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().upper()
    t=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$', '',t)
    return re.sub(r'[^A-Z0-9]','',t)


def read_csv(path):
    with open(path,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))


def write_csv(path,records,columns=None):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    keys=columns or (list(records[0]) if records else ['record'])
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore')
        w.writeheader();w.writerows(records)


def historical_candidates(db_path,cards,end_exclusive):
    """Only keyed names; never merge an unresolved name-only match.

    A set of G3-normalised names is used to locate POTENTIAL rows. Historic
    names, pedigree and provider IDs must then prove a unique exact identity.
    """
    req={normname(r.get('horse')) for r in cards if r.get('horse')}
    ans=collections.defaultdict(list)
    if not req:return ans
    db=sqlite3.connect('file:'+str(pathlib.Path(db_path).resolve())+'?mode=ro',uri=True)
    db.execute('PRAGMA query_only=ON')
    # The importer creates hist_name_idx, but batching also works if a revised
    # archive lacks the index. This is a read-only SELECT, no text inference.
    names=sorted(req)
    try:
        for start in range(0,len(names),300):
            subset=names[start:start+300]
            sql='''SELECT u.horse_name_norm,u.horse_name,u.horse_country,
                 u.horse_source_id,u.sire,u.dam,u.finish_pos,u.outcome,
                 r.race_date,r.course,r.id
                 FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
                 WHERE u.horse_name_norm IN ('''+','.join('?' for _ in subset)+''')
                   AND r.race_date < ? ORDER BY r.race_date'''
            for row in db.execute(sql,(*subset,end_exclusive)):
                name,raw,country,sid,sire,dam,finish,outcome,date,course,rid=row
                ans[str(name)].append({'horse_name_norm':str(name or ''),
                   'horse_name':str(raw or ''),'country':str(country or ''),
                   'source_id':str(sid or ''),'sire':str(sire or ''),
                   'dam':str(dam or ''),'finish_pos':finish,'outcome':str(outcome or ''),
                   'date':str(date),'course':str(course or ''),'race_id':str(rid)})
    finally:db.close()
    return ans


def eligible_pedigree_row(card, historic):
    """Name alone is never sufficient. Require exact pedigree or exact ID.

    Exact API IDs are cross-provider only when literally equal. An existing
    non-empty sire and dam from BOTH sides must match exactly after normalising.
    """
    if normname(historic['horse_name'])!=normname(card.get('horse')):return False
    rid=str(card.get('horse_id') or '').strip()
    id_ok=bool(rid and historic['source_id'] and rid==historic['source_id'])
    sire=normname(card.get('sire'));dam=normname(card.get('dam'))
    ped_ok=bool(sire and dam and normname(historic['sire'])==sire and normname(historic['dam'])==dam)
    return id_ok or ped_ok


def historical_D_key(h):
    # Stage30 Past.identity takes NORMALISED historical name at index 8,
    # then applies OLD.normal (which retains spaces). Use precisely that key.
    import stage30_gap_aware_abcd as OLD
    return OLD.normal(h['horse_name_norm']),OLD.normal(h['country'])


def historical_C_key(h):
    import stage30_gap_aware_abcd as OLD
    return OLD.normal(h['horse_name']),OLD.normal(h['country'])


def candidate_keys(card,rows,target):
    from stage30_gap_aware_abcd import track
    srcid=str(card.get('horse_id') or '').strip()
    eligible=[h for h in rows if h['date']<target and eligible_pedigree_row(card,h)]
    # A recorded prior start; not a NR or an empty source row.
    started=[h for h in eligible if S.valid_run(h)]
    ukie=[h for h in started if track(h['course']) in __import__('stage30_gap_aware_abcd').TRACKS]
    dkeys=set(historical_D_key(h) for h in ukie)
    ckeys=set(historical_C_key(h) for h in started if h['date']>='2021-01-01' and h.get('finish_pos') is not None)
    # The selection of D's entire-history record must be unique; otherwise
    # it would be dangerous to switch identity for a published model.
    byid=sum(bool(srcid and h['source_id']==srcid) for h in started)
    detail={'verified_pedigree_or_id_prior_rows':len(started),
            'verified_archive_UK_IE_runs':len(ukie),
            'verified_archive_recent_C_runs':sum(h['date']>='2021-01-01' for h in started),
            'matched_by_exact_source_id_rows':byid,
            'same_name_unverified_rows':len(rows)-len(eligible),
            'unique_archive_source_races':len({(x['date'],x['race_id']) for x in started}),
            'archive_first_verified_start':min((x['date'] for x in started),default=''),
            'archive_latest_verified_start':max((x['date'] for x in started),default=''),
            'D_verified_identity_keys':len(dkeys),
            'C_verified_identity_keys':len(ckeys),
            'D_verified_key_candidate':next(iter(dkeys)) if len(dkeys)==1 else None,
            'C_verified_key_candidate':next(iter(ckeys)) if len(ckeys)==1 else None,
            'D_conflicted_alias_or_country':int(len(dkeys)>1),
            'C_conflicted_alias_or_country':int(len(ckeys)>1)}
    return detail


def recent_result_hints(repo, cards, targets):
    """Find name-only results strictly BEFORE target. These are HINTS ONLY.

    Official point-to-point/overseas form cannot be assumed from the racecard.
    No name-only result contributes to matched prior starts or rating histories.
    """
    root=pathlib.Path(repo)
    by_day=collections.defaultdict(list)
    for day in sorted(set(targets)):
        cutoff=dt.date.fromisoformat(day)
        for offset in range(1,10):
            prior=(cutoff-dt.timedelta(days=offset)).isoformat()
            f=root/'data/racing-results'/f'{prior}.json'
            if not f.is_file():continue
            try: data=json.loads(f.read_text())
            except (ValueError,OSError):continue
            for race in data.get('results',[]):
                if str(race.get('status') or '').lower()!='final':continue
                for r in race.get('runners',[]):
                    if not r.get('horse'):continue
                    by_day[(day,normname(r['horse']))].append({
                        'date':prior,'course':race.get('course'),'horse':r['horse'],
                        'position':r.get('position'), 'record_has_horse_id':bool(r.get('horse_id')),
                        'race_id':race.get('race_id'),'source':race.get('source')})
    return by_day


def stage114_audit(args):
    from stage30_gap_aware_abcd import normal
    import stage38_model_dz_live as D
    cards=json.loads(pathlib.Path(args.cards).read_text())['runners']
    dayset={x.strip() for x in args.dates.split(',') if x.strip()}
    if not dayset:raise ValueError('No audit dates')
    if max(dayset)>='2026-11-15':
        raise ValueError('Only original 13/14 Nov 2026 frozen evaluation cohort supported; use a new dated version for later days')
    cards=[x for x in cards if str(x.get('date') or x.get('race_time') or '')[:10] in dayset and S.is_starter(x)]
    if not cards:raise RuntimeError('No selected declared runners')
    saved=read_csv(pathlib.Path(args.stage113)/'stage113-runners-by-model.csv')
    base={(r['race_key'],r['horse']):r for r in saved}
    byname=historical_candidates(args.db,cards,max(dayset))
    hints=recent_result_hints(args.repo,cards,dayset)
    rows=[];missing=[]
    summary=collections.defaultdict(lambda:collections.Counter())
    for card in cards:
        date=str(card['date']);key='|'.join(S.race_key(card));name=card['horse']
        prior=base.get((key,name))
        if prior is None:raise RuntimeError('Stage113 runner missing from input: '+key+' '+name)
        allhist=byname[normname(name)]
        detail=candidate_keys(card,allhist,date)
        expected=int(prior['has_previous_form_on_card'])
        g3=int(prior['prior_runs']);d_historic=int(prior.get('d_historical_start_found') or 0)
        dc_old=int(prior.get('d_c_C_live_original_prior_runs') or 0)
        hpr=int(prior.get('e_hpr_has_prior_rating') or 0)
        dk=detail['D_verified_key_candidate'];ck=detail['C_verified_key_candidate']
        original_key=tuple(D.LivePast({}).identity(D.live_row(card,date,'STAGE114|ID')))
        d_safe=(int(bool(dk and dk!=original_key and detail['verified_archive_UK_IE_runs']>0 and not d_historic)))
        c_safe=(int(bool(ck and ck!=(normal(name),normal(card.get('horse_country'))) and detail['verified_archive_recent_C_runs']>0 and not dc_old)))
        rhints=hints.get((date,normname(name)),[])
        g3_unmatched=expected and not g3
        if not g3_unmatched:reason='MATCHED_OR_NO_CLAIMED_FORM'
        elif detail['verified_pedigree_or_id_prior_rows']>0:reason='VERIFIED_ARCHIVE_RUN_EXISTS_BUT_G3_MISSED'
        elif allhist:reason='SAME_NAME_ARCHIVE_ROWS_NOT_PROVEN_SAME_PEDIGREE'
        elif rhints:reason='RECENT_NAME_ONLY_RESULT_UNPROVEN_IDENTITY'
        else:reason='NO_PRE_RACE_VERIFIED_UK_IE_RUN_IN_AVAILABLE_SOURCES'
        row={'date':date,'course':card.get('course'),'time':S.race_key(card)[-1],
             'race_key':key,'horse':name,'horse_id':card.get('horse_id'),
             'sire':card.get('sire'),'dam':card.get('dam'),'visible_previous_form':expected,
             'public_form':card.get('form'),
             'g3_g4_matched_prior_runs':g3,'g3_g4_prior_form_unmatched':int(g3_unmatched),
             'D_original_archive_history_present':d_historic,
             'D_original_history_key':str(original_key),
             'D_exact_pedigree_recoverable_candidate':d_safe,
             'D_verified_key_candidate':str(dk or ''),
             'D_candidate_multiple_keys':detail['D_conflicted_alias_or_country'],
             'D_c_C_original_prior_runs':dc_old,
             'D_c_C_exact_pedigree_recoverable_candidate':c_safe,
             'D_c_C_verified_key_candidate':str(ck or ''),
             'D_c_C_candidate_multiple_keys':detail['C_conflicted_alias_or_country'],
             'E_prior_HPR_rating_present':hpr,
             'E_HPR_missing_but_archive_history_exists':int(not hpr and detail['verified_pedigree_or_id_prior_rows']>0),
             'recent_result_name_only_hints':len(rhints),
             'recent_result_hints_dates':','.join(sorted(set(z['date'] for z in rhints))),
             'g3_unmatched_reason':reason,
             'identity_decision':'EXACT_PEDIGREE_OR_EXACT_ID' if detail['verified_pedigree_or_id_prior_rows'] else 'NO_VERIFIED_MATCH',
             **{k:v for k,v in detail.items() if not k.endswith('_candidate') and k not in ('D_conflicted_alias_or_country','C_conflicted_alias_or_country')}}
        rows.append(row)
        q=summary[date]
        q['runners']+=1;q['reported_form']+=expected;q['g3_unmatched']+=bool(g3_unmatched)
        q['D_original_has_form']+=d_historic;q['D_exact_pedigree_candidates']+=d_safe
        q['D_c_C_original_has_form']+=bool(dc_old);q['D_c_C_exact_pedigree_candidates']+=c_safe
        q['E_HPR_present']+=hpr;q['E_missing_HPR_with_confirmed_archive_runs']+=row['E_HPR_missing_but_archive_history_exists']
        if g3_unmatched:
            missing.append(row)
            q['unmatched_'+reason]+=1
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    write_csv(out/'stage114-all-runners-exact-pedigree-audit.csv',rows)
    write_csv(out/'stage114-G3-G4-unmatched-investigation.csv',missing,columns=list(rows[0]))
    (out/'stage114-summary.json').write_text(json.dumps({
      'scope':'RESEARCH_ONLY_IDENTITY_AUDIT','dates':{d:dict(c) for d,c in sorted(summary.items())},
      'source':{'historical_sqlite':str(args.db),'stage113b_prior_form_csv':str(args.stage113),
       'racecard_source':str(args.cards),'no_name_only_merges':True,
       'no_point_to_point_or_foreign_claim_without_verified_source':True,
       'D_historic_key':'NORMAL(historical_runs.horse_name_norm) + NORMAL(country)',
       'D_live_key':'NORMAL(racecard horse name) + inferred country',
       'reason_for_D_mismatch':'historical name_norm loses spaces; live name retains them'},
      'checks':{'no_app_or_frozen_models_modified':True,'no_2026_11_14_outcomes_used':True,
       'no_sp_or_odds_as_scoring_inputs':True,'ratings_not_manufactured':True}
    },indent=2)+'\n')
    return rows,summary,byname


def replay_original_D(args,rows,byname):
    """Re-score original D paper-only with exactly verified single-key replacements.

    This reuses the frozen Past instance and published eight weights; the sole
    change is live row's (name,country) key where a unique historical pedigree
    match is verified. All ambiguous cases remain unchanged.
    """
    import stage38_model_dz_live as D
    import stage30_gap_aware_abcd as OLD
    repo=pathlib.Path(args.repo)
    audit=json.loads((repo/'reports/stage30/stage30-summary.json').read_text())
    m=audit.get('model_results',{}).get('D',{})
    if m.get('chosen_combination')!='D-064-gap14' or audit.get('test_year')!=2025:
        raise RuntimeError('Frozen Stage30 Model D provenance failed; no replay')
    weights=[float(m['weights_pct'][k])/100 for k in D.METRICS]
    if abs(sum(weights)-1)>.002:raise RuntimeError('Frozen D weights mismatch')
    from collections import defaultdict
    cards=json.loads(pathlib.Path(args.cards).read_text()).get('runners',[])
    cards=[z for z in cards if str(z.get('date') or '')=='2026-11-14' and S.is_starter(z)]
    info={(r['race_key'],r['horse']):r for r in rows}
    history=D.LivePast({});history_info=history.scan_until(args.db,'2026-11-14')
    country={}
    for hn,c in history.last:
        if hn not in country:country[hn]=c
        elif country[hn]!=c:country[hn]=None
    races=defaultdict(list)
    for card in cards:races[S.race_key(card)].append(card)
    comparisons=[];runner_rows=[]
    for race_key,declared in sorted(races.items()):
        if len(declared)<2:continue
        original=[D.live_row(c,'2026-11-14','STAGE114|2026-11-14|'+'|'.join(race_key),country) for c in declared]
        fixed=[];changed=[]
        for card,z in zip(declared,original):
            rec=info[('|'.join(race_key),card['horse'])]
            hz=byname.get(normname(card['horse']),[])
            valid=candidate_keys(card,hz,'2026-11-14')
            key=valid['D_verified_key_candidate']
            a=list(z)
            if key and key in history.last and tuple(history.identity(z))!=key:
                a[8]=key[0];a[10]=key[1]
                changed.append(card['horse'])
            fixed.append(tuple(a))
        old=history.score(original,live=True)
        new=history.score(fixed,live=True)
        if old is None or new is None:continue
        oldsc,oldorder,_=D.prediction_rows(old,weights)
        newsc,neworder,_=D.prediction_rows(new,weights)
        for pos,card in enumerate(declared):
            runner_rows.append({'date':race_key[0],'course':card['course'],'time':race_key[-1],
               'horse':card['horse'],'original_rank':int(list(oldorder).index(pos)+1),
               'reconciled_rank':int(list(neworder).index(pos)+1),
               'original_score':round(float(oldsc[pos]),6),
               'reconciled_score':round(float(newsc[pos]),6),
               'identity_repaired':int(card['horse'] in changed),'original_identity_key':str(history.identity(original[pos])),
               'new_identity_key':str(history.identity(fixed[pos]))})
        comparisons.append({'date':race_key[0],'course':declared[0]['course'],'time':race_key[-1],
             'runners':len(declared),'identity_corrections':len(changed),
             'original_rank1':declared[int(oldorder[0])]['horse'],
             'reconciled_rank1':declared[int(neworder[0])]['horse'],
             'rank1_changed':int(oldorder[0]!=neworder[0]),
             'original_rank2':declared[int(oldorder[1])]['horse'],
             'reconciled_rank2':declared[int(neworder[1])]['horse'],
             'rank2_changed':int(oldorder[1]!=neworder[1]),
             'paper_only':True})
    out=pathlib.Path(args.out)
    write_csv(out/'stage114-D-frozen-vs-corrected-paired-rankings.csv',comparisons,
       ['date','course','time','runners','identity_corrections','original_rank1','reconciled_rank1','rank1_changed','original_rank2','reconciled_rank2','rank2_changed','paper_only'])
    write_csv(out/'stage114-D-all-runners-rank-comparison.csv',runner_rows,
      ['date','course','time','horse','original_rank','reconciled_rank','original_score','reconciled_score','identity_repaired','original_identity_key','new_identity_key'])
    summ={'original_D_history':history_info,'races_scored':len(comparisons),
          'races_with_rank1_change':sum(r['rank1_changed'] for r in comparisons),
          'races_with_rank2_change':sum(r['rank2_changed'] for r in comparisons),
          'runner_identity_replacements':sum(r['identity_repaired'] for r in runner_rows),
          'rankings_research_only':True,'original_model_weights_unchanged':True,
          'original_D_history_state_unchanged':True}
    (out/'stage114-D-replay-summary.json').write_text(json.dumps(summ,indent=2)+'\n')
    return summ


def self_test():
    # Test without requiring the entire repository checkout locally. The real
    # workflow imports the real Stage30 module, never this local fixture.
    try:import stage30_gap_aware_abcd as _stage30
    except ModuleNotFoundError:
        import types
        fake=types.ModuleType('stage30_gap_aware_abcd')
        fake.normal=lambda s:re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 -]','',str(s or '').lower())).strip()
        fake.track=lambda s:fake.normal(re.sub(r'\([^)]*\)','',str(s or '')))
        fake.TRACKS={'cheltenham','navan','dundalk','windsor'}
        sys.modules['stage30_gap_aware_abcd']=fake
    a={'horse':'Little Ledgend','horse_id':'hrs_123','sire':'Shirocco (GER)','dam':'A Dream (IRE)'}
    b={'horse_name_norm':'LITTLELEDGEND','horse_name':'Little Ledgend (IRE)',
       'country':'','source_id':'fake-provider-id','sire':'Shirocco','dam':'A Dream',
       'finish_pos':2,'outcome':'ran','date':'2026-10-05','course':'Cheltenham','race_id':'id1'}
    assert eligible_pedigree_row(a,b)
    assert historical_D_key(b)[0]=='littleledgend'
    assert historical_D_key(b)[0] != 'little ledgend'
    assert candidate_keys(a,[b],'2026-11-14')['D_verified_identity_keys']==1
    bad={**b,'dam':'OTHER DAM'}
    assert not eligible_pedigree_row(a,bad), 'pedigree mismatch must not promote'
    wrong={**a,'horse_id':'nope'}
    assert not eligible_pedigree_row(wrong,bad)
    exactid={**bad,'source_id':'hrs_123'}
    assert eligible_pedigree_row(a,exactid)
    mixed=candidate_keys(a,[b,{**b,'country':'IRE'}],'2026-11-14')
    assert mixed['D_conflicted_alias_or_country']==1 and mixed['D_verified_key_candidate'] is None
    assert normname('Horse (IRE)')==normname('Horse')
    print('Stage114 tests PASS: exact pedigree, no name-only mergers, safe unique D key, ambiguous-country guard')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--self-test',action='store_true')
    for k in ('repo','cards','db','stage113','dates','out'):p.add_argument('--'+k)
    p.add_argument('--paired-D',action='store_true')
    args=p.parse_args()
    if args.self_test:self_test();return
    if not all(getattr(args,k) for k in ('repo','cards','db','stage113','dates','out')):p.error('Missing source paths')
    rows,summary,byname=stage114_audit(args)
    print(json.dumps({'stage114_exact_history':{k:dict(v) for k,v in summary.items()}},indent=2))
    if args.paired_D:
        d=replay_original_D(args,rows,byname)
        print(json.dumps({'D_paired':d},indent=2))

if __name__=='__main__':main()
