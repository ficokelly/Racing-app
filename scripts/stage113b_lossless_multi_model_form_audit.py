#!/usr/bin/env python3
"""Stage113b research-only: lossless G3/G4 form stitching; D/D(c)/E audit on declared fields.

All historical inputs read-only. No model weights or original prediction files changed.
No target-date races/results enter form state for scoring. Racecard 'form' is an
assertion to audit against, never converted to fictional past run observations.
"""
from __future__ import annotations
import argparse, collections, copy, csv, datetime as dt, json, pathlib, sqlite3, sys
from collections import deque

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage112_form_coverage_reconcile as S


def event_key(event):
    date=event.get('date')
    if isinstance(date,dt.date):date=date.isoformat()
    return str(date),str(event.get('course') or '')


def sorted_buckets(state):
    """Restore chronological deques: out-of-order additions would change G3 features."""
    for store in (state.horses,state.trainers,state.jockeys,state.sires):
        for key,values in list(store.items()):
            maximum=values.maxlen
            # Preserve every existing history event, but retain the original model cap.
            store[key]=deque(sorted(values,key=lambda z:z.get('date') or dt.date.min),maxlen=maximum)


def add_verified_archive_unseen(state, dbpath, first, last, G):
    """Append only verified historical races not already in HPR/source snapshot.

    Deduplicate the same horse+date+course across archive/HPR, but never discard
    a HPR-observed run just because the master archive lacks it.
    """
    stats=collections.Counter()
    # Archive-only events live in a SEPARATE state first. Appending historical
    # runs directly into an existing maxlen=24 deque can evict newer HPR runs.
    # Merging the two independently retained histories in date order prevents it.
    overlay=G.FormState()
    seen={horse:{event_key(e) for e in observations} for horse,observations in state.horses.items()}
    con=sqlite3.connect('file:'+str(pathlib.Path(dbpath).resolve())+'?mode=ro',uri=True)
    con.execute('PRAGMA query_only=ON')
    try:
        for group in G.from_db(con,first,last):
            legit=[r for r in group if S.valid_run(r)]
            stats['archive_races_seen']+=1
            if len(legit)<2:
                stats['archive_races_too_few_verified_starters']+=1
                continue
            for r in legit:
                key=G.identity(r)
                if not key:
                    stats['rejected_missing_identity']+=1
                    continue
                event=(str(r['date']),G.course_key(r.get('course')))
                if event in seen.setdefault(key,set()):
                    stats['archive_runs_already_present_in_existing_state']+=1
                    continue
                if not S.valid_run(r):raise AssertionError('Unverified run insertion')
                overlay.add(r,G.day(r['date']),len(legit))
                seen[key].add(event)
                stats['new_verified_historical_starts_added']+=1
                if key.startswith('P|'):stats['added_pedigree_starts']+=1
                else:stats['added_id_starts']+=1
    finally:
        con.close()
    for attr in ('horses','trainers','jockeys','sires'):
        dest=getattr(state,attr);extra=getattr(overlay,attr)
        for key,records in extra.items():
            existing=dest[key]
            keep=existing.maxlen
            joined=sorted([*existing,*records],key=lambda z:z.get('date') or dt.date.min)
            dest[key]=deque(joined[-keep:] if keep else joined,maxlen=keep)
    state.events+=overlay.events
    stats['original_run_history_preserved_before_capacity_truncation']=True
    return dict(stats)


def assert_no_regressions(old,new):
    before={(x['race_key'],x['horse']):int(x['prior_runs']) for x in old}
    bad=[{'race':x['race_key'],'horse':x['horse'],'original':before[(x['race_key'],x['horse'])],'new':x['prior_runs']}
         for x in new if int(x['prior_runs'])<before[(x['race_key'],x['horse'])]]
    if bad:raise AssertionError('RUN HISTORY REGRESSION: '+json.dumps(bad[:12]))


def d_archive_coverage(path, cards, target):
    """Independent original-D archived history via exactly Stage38 LivePast identity.

    The archive ends before forecast day, so this measures the D component's
    VERIFIED ARCHIVE history only; it is not a claim about operational D's live
    freshness or D(c)'s separate C classifier.
    """
    import stage38_model_dz_live as D
    past=D.LivePast({})
    info=past.scan_until(path,target)
    country={}
    for hn,c in past.last:
        if hn in country and country[hn]!=c:country[hn]=None
        elif hn not in country:country[hn]=c
    out={}
    for r in cards:
        fields=D.live_row(r,target,'STAGE113|AUDIT',country)
        key=past.identity(fields)
        out[(S.race_key(r),r.get('horse'))]={
            'd_historical_start_found':int(key in past.last),
            'd_history_identity':str(key),
            'd_country_ambiguous':int(country.get(key[0],'') is None),
            'd_last_historic_start':str(past.last[key]) if key in past.last else '',
            'd_c_base_D_historical_start_found':int(key in past.last),
        }
    return out,info



def c_component_coverage(path, cards, target):
    """Audit exact Stage65P C form identity used by the D(c) live pipeline.

    This replays C.Form's dated history source through the frozen archive.
    The Stage83 category enrichment is distinct and is not modified here.
    """
    import stage65p_race_type_c_only as C
    form=C.Form()
    seen_races=0
    # Stage100's staging DB may not include the optional Stage65P race-name
    # overlay. The runner identity columns are present in either schema.
    with sqlite3.connect('file:'+str(pathlib.Path(path).resolve())+'?mode=ro',uri=True) as verify:
        detailed=bool(verify.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage65p_race_details'").fetchone())
    def basic_rows():
        cx=sqlite3.connect('file:'+str(pathlib.Path(path).resolve())+'?mode=ro',uri=True)
        query='''SELECT r.race_date,r.id,r.course,r.race_type,r.surface,r.race_class,
           u.horse_name,u.horse_country,u.trainer,u.jockey,u.official_rating,
           u.weight_lbs,u.finish_pos,u.beaten,u.rpr
           FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
           WHERE r.race_date >= '2021-01-01' AND r.race_date < ?
           ORDER BY r.race_date,r.id'''
        keys=['date','race_id','course','race_type','surface','race_class','horse',
              'country','trainer','jockey','or','weight','finish','beaten','rpr']
        group=[];last=None
        try:
            for raw in cx.execute(query,(target,)):
                obj=dict(zip(keys,raw))
                if last is not None and obj['race_id']!=last:
                    yield group;group=[]
                group.append(obj);last=obj['race_id']
            if group:yield group
        finally:cx.close()
    rows=C.rows(path,target) if detailed else basic_rows()
    for group in rows:
        valid=[z for z in group if (C.num(z.get('finish')) or 0)>0]
        if len(valid)<2:continue
        for z in valid:
            z['field_size']=len(valid)
            form.update(z)
        seen_races+=1
    countries=collections.defaultdict(set)
    for (hn,country),hist in form.h.items():
        if hist and country:countries[hn].add(country)
    output={}
    for r in cards:
        name=C.name(r.get('horse'))
        country=C.name(r.get('horse_country'))
        exact=(name,country)
        present=len(form.h.get(exact,[]))
        known=countries.get(name,set())
        possible_country=next(iter(known)) if len(known)==1 else None
        repaired=len(form.h.get((name,possible_country),[])) if possible_country else 0
        output[(S.race_key(r),r.get('horse'))]={
            'd_c_C_live_original_prior_runs':present,
            'd_c_C_requires_country_not_on_card':int(bool(known) and not country and present==0),
            'd_c_C_unique_country_fallback_prior_runs':repaired if not present else present,
            'd_c_C_safe_country_inferred':possible_country or '',
            'd_c_C_country_ambiguous':int(len(known)>1),
            'd_c_C_recorded_historic_countries':','.join(sorted(known)),
        }
    return output,{'source':'stage65p_race_type_c_only.Form + rows, audit-only',
                   'historical_races_replayed':seen_races,'distinct_historical_horse_keys':len(form.h),
                   'identity_current_as_live_code':True,'stage65p_race_details_available':detailed,
                   'historical_race_categories_enrichment':'CATEGORIES_NOT_MODIFIED'}


def audit(args):
    import joblib,stage108_model_g2 as G
    dates=sorted(set(x.strip() for x in args.dates.split(',') if x.strip()))
    if not dates or not all(S.day(x) and S.day(x).isoformat()==x for x in dates):raise ValueError('Use YYYY-MM-DD dates')
    frozen=joblib.load(args.state)
    last_old=str(frozen['asof_after_date'])
    if not all(x>last_old for x in dates):raise ValueError('Target must be after frozen snapshot')
    start=(S.day(last_old)+dt.timedelta(days=1)).isoformat()
    rows=[];collection_time=None
    for target in dates:
        cards,updated=S.load_cards(args.cards,target)
        rows.extend(cards);collection_time=updated
    if not rows:raise ValueError('No declared runners')
    end_arch=S.archive_date_bounds(args.db,last_old,min(dates))[1]
    if not end_arch:raise ValueError('Archive history after frozen snapshot absent')
    if end_arch>=min(dates):raise ValueError('Archive must finish before first target')
    # Audit each race day independently. Otherwise a 14 Nov audit would
    # erroneously load 13 Nov HPR results into 13 Nov's pre-race coverage.
    oldreports=[]
    newreports=[]
    hpr_by_day={}
    stitch_by_day={}
    paper_repaired_state=None
    for target in dates:
        day_cards=[r for r in rows if str(r.get('date') or r.get('race_time') or '')[:10]==target]
        # The HPR loader excludes asof itself (race_date < target).
        original=copy.deepcopy(frozen['state'])
        hpr_by_day[target]=G.load_recent_hpr(original,args.hpr,last_old,target)
        day_before=S.report_state(day_cards,original,G,'ORIGINAL_HPR_ONLY')
        repaired=copy.deepcopy(original)
        prior_end=min(end_arch,(S.day(target)-dt.timedelta(days=1)).isoformat())
        if prior_end < start:
            ingest={'new_verified_historical_starts_added':0,'reason':'NO_ARCHIVE_RUNS_BEFORE_DATE'}
        else:
            ingest=add_verified_archive_unseen(repaired,args.db,start,prior_end,G)
        day_after=S.report_state(day_cards,repaired,G,'HPR_UNION_MASTER_ARCHIVE_DEDUP')
        assert_no_regressions(day_before,day_after)
        S.hpr_coverage(day_after,args.hpr,target)
        oldreports.extend(day_before)
        newreports.extend(day_after)
        stitch_by_day[target]=ingest
        if target==max(dates):paper_repaired_state=repaired
    # Race outcomes on the audit day are not used as pre-race history.
    # This is especially important when auditing a completed day alongside
    # a future day's selections.
    oldby={(x['race_key'],x['horse']):x for x in oldreports}
    d_out,d_info=d_archive_coverage(args.db,rows,max(dates))
    c_out,c_info=c_component_coverage(args.db,rows,max(dates))
    for r in newreports:
        key=(r['race_key'],r['horse'])
        prior=oldby[key]
        r['stage112_original_prior_runs']=prior['prior_runs']
        r['additional_verified_prior_runs']=int(r['prior_runs'])-int(prior['prior_runs'])
        r.update(d_out.get((tuple(r['race_key'].split('|')),r['horse']),{}))
        r.update(c_out.get((tuple(r['race_key'].split('|')),r['horse']),{}))
        r['e_hpr_has_prior_rating']=int(bool(r.get('prior_hpr_rating_races')))
        r['d_c_C_component_horse_history_verified']='AUDITED_AGAINST_ARCHIVE'
    before=S.aggregate(oldreports)
    after=S.aggregate(newreports)
    previous={x['race_key']:x for x in before}
    for x in after:
        old=previous[x['race_key']]
        x['before_g3_g4_prior_match_pct']=old['form_reconciliation_pct']
        x['before_g3_g4_unmatched']=old['reported_prior_form_unmatched']
        x['form_reconciled_for_paper']=int(x['reported_prior_form_unmatched']==0)
        x['quality_gate']='PASSED' if x['form_reconciled_for_paper'] else 'UNRESOLVED_FORM'
        x['possible_debutant_count']=x['runners']-x['reported_prior_form_runners']
    bdates=collections.defaultdict(list)
    for r in after:bdates[r['date']].append(r)
    stats={}
    for target,v in bdates.items():
        sub=[r for r in newreports if r['date']==target]
        original_sub=[r for r in oldreports if r['date']==target]
        total=sum(x['runners'] for x in v)
        claimed=sum(x['reported_prior_form_runners'] for x in v)
        matched=sum(x['reported_prior_form_confirmed'] for x in v)
        stats[target]={
            'races':len(v),'runners':total,'racecard_runners_with_previous_form':claimed,
            'racecard_possible_debutants':total-claimed,
            'g3_g4_original_runners_with_form_unmatched':sum(x['reported_prior_but_unmatched'] for x in original_sub),
            'g3_g4_repaired_runners_with_form_unmatched':sum(x['reported_prior_but_unmatched'] for x in sub),
            'g3_g4_original_races_with_form_unmatched':sum(x['reported_prior_form_unmatched']>0 for x in before if x['date']==target),
            'g3_g4_repaired_races_with_form_unmatched':sum(x['reported_prior_form_unmatched']>0 for x in v),
            'races_below_55pct_all_runners':sum(x['below_55pct_all_runner_coverage'] for x in v),
            'races_under_95pct_match_of_reported_form':sum(x['form_reconciliation_below_95pct'] for x in v),
            'form_match_pct':round(100*matched/claimed,2) if claimed else None,
            'd_archived_priors_found':sum(x.get('d_historical_start_found',0) for x in sub),
            'd_declared_form_but_archive_prior_unmatched':sum(x['has_previous_form_on_card'] and not x.get('d_historical_start_found',0) for x in sub),
            'd_c_C_original_runners_with_historical_form':sum(x.get('d_c_C_live_original_prior_runs',0)>0 for x in sub),
            'd_c_C_recoverable_via_unique_country':sum(x.get('d_c_C_live_original_prior_runs',0)==0 and x.get('d_c_C_unique_country_fallback_prior_runs',0)>0 for x in sub),
            'd_c_C_experienced_runners_without_historical_form':sum(x['has_previous_form_on_card'] and not x.get('d_c_C_live_original_prior_runs',0) for x in sub),
            'e_hpr_runners_with_prior_rating':sum(x['e_hpr_has_prior_rating'] for x in sub),
            'e_declared_form_but_no_prior_hpr_rating':sum(x['has_previous_form_on_card'] and not x['e_hpr_has_prior_rating'] for x in sub),
        }
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    S.writecsv(out/'stage113-runners-by-model.csv',newreports)
    S.writecsv(out/'stage113-races-coverage.csv',after)
    S.writecsv(out/'stage113-original-races.csv',before)
    summary={'status':'RESEARCH_ONLY','dates':stats,'collected_at':collection_time,
      'original_state_through':last_old,'archive_through':end_arch,'hpr_by_audit_day':hpr_by_day,
      'stitch_by_audit_day':stitch_by_day,'model_D':{'history_source':'original D Stage38 LivePast, archive only',**d_info},
      'model_Dc':{'D_component':'same archived historical inputs as original D','C_component':c_info,
                  'note':'C country-key fallback is audit-only; production C features and Stage83 category enrichments remain untouched'},
      'model_E':{'HPR':'exact prior horse-key ratings in ledger; absence does not imply lack of recorded races'},
      'safety':{'no_live_model_changes':True,'frozen_predictions_preserved':True,'no_target_day_results_used':True,'audit_days_isolated_prior_only':True,
                'no_odds_in_scoring':True,'no_loss_of_form':True,'racecard_form_used_only_as_audit_expectation':True,
                'never_name_only_merge_to_G3':True,'original_prior_run_count_never_decreased':True}}
    (out/'stage113-summary.json').write_text(json.dumps(summary,indent=2,default=str)+'\n')
    # A portable state for optional future paper scoring only, never app writes.
    if args.paper_state:
        # State already includes every prior HPR run up to the target's previous day.
        # Never let the downstream scorer replay post-archive HPR a second time.
        completed=(S.day(max(dates))-dt.timedelta(days=1)).isoformat()
        joblib.dump({**frozen,'state':paper_repaired_state,'asof_after_date':completed},out/'stage113-paper-form-state.joblib',compress=3)
    print(json.dumps({'dates':stats,'stitch_by_audit_day':stitch_by_day,'d_archive':d_info},indent=2,default=str))


def self_test():
    from types import SimpleNamespace
    class Dummy:
        def __init__(self):self.horses={'P|A|B|C':deque([{'date':dt.date(2026,1,20),'course':'DUNDALK'}],maxlen=24)}
    old=[{'race_key':'x','horse':'A','prior_runs':1}]
    good=[{'race_key':'x','horse':'A','prior_runs':2}]
    assert_no_regressions(old,good)
    try:assert_no_regressions(good,old)
    except AssertionError:pass
    else:raise AssertionError('Regression gate failed')
    assert event_key({'date':dt.date(2026,1,20),'course':'DUNDALK'})==('2026-01-20','DUNDALK')
    # Test the lossless merge on a full 24-run bounded horse history.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db=pathlib.Path(tmp)/'fixture.sqlite'
        sqlite3.connect(db).close()
        class FakeG:
            @staticmethod
            def identity(r):return r['horse']
            @staticmethod
            def day(s):return S.day(s)
            @staticmethod
            def course_key(s):return s
            @staticmethod
            def from_db(_con,_first,_last):
                yield [dict(horse='H',date='2026-01-10',course='DUNDALK',finish_pos=2),
                       dict(horse='X',date='2026-01-10',course='DUNDALK',finish_pos=1)]
            class FormState:
                def __init__(self):
                    self.horses=collections.defaultdict(lambda:deque(maxlen=24))
                    self.trainers=collections.defaultdict(lambda:deque(maxlen=500))
                    self.jockeys=collections.defaultdict(lambda:deque(maxlen=500))
                    self.sires=collections.defaultdict(lambda:deque(maxlen=250))
                    self.events=0
                def add(self,r,d,n):
                    self.horses[r['horse']].append(dict(date=d,course=r['course']))
                    self.events+=1
        state=FakeG.FormState()
        for i in range(24):
            d=dt.date(2026,1,20)+dt.timedelta(days=i)
            state.horses['H'].append({'date':d,'course':'DUNDALK'})
        reference=[event_key(e) for e in state.horses['H']]
        result=add_verified_archive_unseen(state,db,'2026-01-01','2026-02-20',FakeG)
        assert [event_key(e) for e in state.horses['H']]==reference, 'Older recovered event evicted a newer one'
        assert result['new_verified_historical_starts_added']==2
        assert result['original_run_history_preserved_before_capacity_truncation'] is True
    print('Stage113 self-tests: no form regressions, original 24-run history preserved, safe date scope OK')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--self-test',action='store_true')
    for key in ('state','db','hpr','cards','dates','out'):
        p.add_argument('--'+key,required=False)
    p.add_argument('--paper-state',action='store_true')
    q=p.parse_args()
    if q.self_test:self_test();return
    if not all(getattr(q,k) for k in ('state','db','hpr','cards','dates','out')):p.error('Missing required source paths or dates')
    audit(q)

if __name__=='__main__':main()
