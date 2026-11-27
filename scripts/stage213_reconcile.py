EXCEPTIONS={'2026-11-16': {'unmatched': [], 'differences': [{'course': 'plumpton', 'irish_time': '14:45', 'sydney_date': '2026-11-17', 'query_dates': ['2026-11-17'], 'provider_full_field': True, 'provider_named_rows': 8, 'archive_winners': ['ofcorseican'], 'external_winners': ['ofcorseican'], 'archive_only_starters': ['anothermanicmonday'], 'external_only_starters': []}, {'course': 'plumpton', 'irish_time': '13:15', 'sydney_date': '2026-11-17', 'query_dates': ['2026-11-17'], 'provider_full_field': True, 'provider_named_rows': 12, 'archive_winners': ['prairiediamond'], 'external_winners': ['prairiediamond'], 'archive_only_starters': ['girlofgold', 'nonofizz'], 'external_only_starters': []}]}, '2026-11-20': {'unmatched': [{'course': 'Ascot', 'off_dt': '2026-11-20T12:10:00Z', 'runners': 3}, {'course': 'Wolverhampton (AW)', 'off_dt': '2026-11-20T20:30:00Z', 'runners': 13}], 'differences': []}, '2026-11-21': {'unmatched': [{'course': 'Newcastle (AW)', 'off_dt': '2026-11-21T19:00:00Z', 'runners': 14}], 'differences': [{'course': 'ascot', 'irish_time': '14:05', 'sydney_date': '2026-11-22', 'query_dates': ['2026-11-22'], 'provider_full_field': True, 'provider_named_rows': 10, 'archive_winners': ['missgoldfire'], 'external_winners': ['missgoldfire'], 'archive_only_starters': ['popova'], 'external_only_starters': []}]}, '2026-11-22': {'unmatched': [{'course': 'Cork', 'off_dt': '2026-11-22T15:49:00Z', 'runners': 19}], 'differences': [{'course': 'exeter', 'irish_time': '15:07', 'sydney_date': '2026-11-23', 'query_dates': ['2026-11-23'], 'provider_full_field': True, 'provider_named_rows': 7, 'archive_winners': ['modernstyle'], 'external_winners': ['modernstyle'], 'archive_only_starters': ['arbitration', 'outofaces'], 'external_only_starters': []}, {'course': 'cork', 'irish_time': '13:19', 'sydney_date': '2026-11-23', 'query_dates': ['2026-11-23'], 'provider_full_field': True, 'provider_named_rows': 18, 'archive_winners': ['soulasylum'], 'external_winners': ['soulasylum'], 'archive_only_starters': ['daddyscupcake', 'jessberswish'], 'external_only_starters': []}, {'course': 'exeter', 'irish_time': '13:07', 'sydney_date': '2026-11-23', 'query_dates': ['2026-11-23'], 'provider_full_field': True, 'provider_named_rows': 6, 'archive_winners': ['seventeenothree'], 'external_winners': ['seventeenothree'], 'archive_only_starters': ['stavangerrose'], 'external_only_starters': []}]}, '2026-11-23': {'unmatched': [{'course': 'Wolverhampton (AW)', 'off_dt': '2026-11-23T20:30:00Z', 'runners': 10}], 'differences': [{'course': 'bangorondee', 'irish_time': '13:05', 'sydney_date': '2026-11-24', 'query_dates': ['2026-11-24'], 'provider_full_field': True, 'provider_named_rows': 11, 'archive_winners': ['digdeep', 'theywantme'], 'external_winners': ['digdeep', 'theywantme'], 'archive_only_starters': ['cedarrow', 'frenchemperor', 'harddealt', 'kingbeldulion', 'lordroco'], 'external_only_starters': []}]}, '2026-11-24': {'unmatched': [{'course': 'Wolverhampton (AW)', 'off_dt': '2026-11-24T20:30:00Z', 'runners': 10}], 'differences': []}, '2026-11-25': {'unmatched': [{'course': 'Kempton (AW)', 'off_dt': '2026-11-25T20:30:00Z', 'runners': 14}], 'differences': []}, '2026-11-26': {'unmatched': [{'course': 'Chelmsford (AW)', 'off_dt': '2026-11-26T20:30:00Z', 'runners': 10}], 'differences': []}, '2026-11-27': {'unmatched': [{'course': 'Dundalk (AW) (IRE)', 'off_dt': '2026-11-27T20:30:00Z', 'runners': 15}], 'differences': []}}
import collections, datetime as dt, json, os, pathlib, sys, urllib.error
from scripts import stage32_form_rebuild as F
from scripts.stage213_collection import DUBLIN,SYDNEY,UTC,timestamp,sydney_dates,fetch_pages
from scripts import rhp_stage74_external_gap_recovery as E
P=pathlib.Path; OUT=P('reports/stage213');OUT.mkdir(parents=True,exist_ok=True)
def read(p):return json.loads(P(p).read_text()) if P(p).exists() else {}
def save(p,x):
    p=P(p);p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.stage213.tmp');t.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');t.replace(p)
def slot(r):return (str(r.get('date') or '')[:10],F.course_key(r.get('course')),F.off_key(r.get('off_dt') or r.get('race_time') or r.get('off')))
def rows(r):return {F.horse_key(x.get('horse')):x for x in r.get('runners',[]) if x.get('horse')}
def signatures(r):return sorted((k,str(x.get('position') or '').upper().removesuffix('.0'),F.is_starter(x)) for k,x in rows(r).items())
def find(day,co,off):
    p=F.RESULTS_DIR/(day+'.json');doc=read(p); match=[r for r in doc.get('results',[]) if slot(r)==(day,co,off) and r.get('status')=='final'];return match[0] if len(match)==1 else None
# Independently checked statuses of the disputed nonfinishers.
TERMINAL={
('2026-11-16','plumpton','14:45'):('https://www.racingpost.com/results/44/plumpton/2026-11-16/931536/',{'anothermanicmonday':'pu'}),
('2026-11-16','plumpton','13:15'):('https://www.racingpost.com/results/44/plumpton/2026-11-16/931537/',{'girlofgold':'pu','nonofizz':'pu'}),
('2026-11-21','ascot','14:05'):('https://www.racingpost.com/results/2/ascot/2026-11-21/931602/',{'popova':'f'}),
('2026-11-22','exeter','15:07'):('https://www.racingpost.com/results/14/exeter/2026-11-22/931620/',{'arbitration':'f','outofaces':'pu'}),
('2026-11-22','cork','13:19'):('https://www.racingpost.com/results/596/cork/2026-11-22/933626/',{'daddyscupcake':'ro','jessberswish':'pu'}),
('2026-11-22','exeter','13:07'):('https://www.racingpost.com/results/14/exeter/2026-11-22/931617/',{'stavangerrose':'pu'}),
('2026-11-23','bangorondee','13:05'):('https://www.racingpost.com/results/4/bangor-on-dee/2026-11-23/933647/',{'cedarrow':'f','frenchemperor':'pu','harddealt':'f','kingbeldulion':'f','lordroco':'bd'})}
STATUS={'pulled up':'pu','fell':'f','ran out':'ro','brought down':'bd','unseated rider':'ur','refused':'r'}

def verify_old():
    evidence=read('data/form-dated-evidence.json').get('final_results',[]);result=[]
    for day,section in EXCEPTIONS.items():
        for x in section['unmatched']:
            co=F.course_key(x['course']);off=F.off_key(x['off_dt']);k=(day,co,off); r=find(*k); ok=False;url=None;reason='ARCHIVE_OR_EVIDENCE_CHANGED'
            if r and k==('2026-11-20','ascot','12:10'):
                url='https://www.racingpost.com/results/2/ascot/2026-11-20/931595/'
                rr=rows(r);ok=set(rr)=={'etnabianco','crystalisland','geeforceflyer'} and F.is_win(rr['etnabianco']) and not any(F.is_starter(rr[h]) for h in ('crystalisland','geeforceflyer'))
                reason='VERIFIED_WALKOVER' if ok else reason
            elif r:
                ev=[v for v in evidence if slot(v)==k]
                if len(ev)==1:
                    url=ev[0].get('external_verification',{}).get('source_url');ok=bool(url) and signatures(r)==signatures(ev[0]);reason='PRIOR_INDEPENDENT_FINAL_PRESERVED' if ok else reason
            result.append({'race':'|'.join(k),'kind':'unmatched','verified':ok,'disposition':reason,'source_url':url})
        for x in section['differences']:
            k=(day,x['course'],x['irish_time']);r=find(*k);url,expected=TERMINAL[k];rr=rows(r or {})
            ok=bool(r) and set(expected)==set(x['archive_only_starters']) and not x['external_only_starters'] and x['archive_winners']==x['external_winners'] and set(x['archive_winners'])=={h for h,z in rr.items() if F.is_win(z)}
            ok=ok and all(h in rr and F.is_starter(rr[h]) and STATUS.get(str(rr[h].get('status')).lower(),str(rr[h].get('status')).lower())==status for h,status in expected.items())
            result.append({'race':'|'.join(k),'kind':'difference','verified':ok,'disposition':'ARCHIVE_NONFINISHERS_CORRECT' if ok else 'REVIEW_ARCHIVE_CHANGED','horses':expected,'source_url':url})
    assert len(result)==16
    return result

def morning():
    now=dt.datetime.now(DUBLIN);today=now.date();days=[today-dt.timedelta(days=i) for i in (1,2)]
    # On a manual evening install, also check the already published current date if late enough.
    if os.getenv('GITHUB_EVENT_NAME')=='workflow_dispatch' and now.hour>=22:days.insert(0,today)
    query_dates=sorted({d for day in days for d in sydney_dates(day)},reverse=True)
    key=os.environ.get('PUNTERSEDGE_API_KEY',''); external={};queries={}; notes=[]; ambiguous=set(); checked_late=[]
    if not key:return {'status':'NO_API_KEY','queries':{},'actions':[]}
    for day in query_dates:
        try:
            result,_,pages=fetch_pages(key,date=str(day));queries[str(day)]={'complete':True,'pages':pages,'rows':len(result)}
        except (OSError,ValueError,RuntimeError) as e:
            queries[str(day)]={'complete':False,'error':type(e).__name__};continue
        for x in result:
            st=timestamp(x.get('start_time'))
            if not st or st.astimezone(DUBLIN).date() not in days or x.get('status')!='final' or str(x.get('country')).upper() not in ('GB','IE','IRE'):continue
            k=(str(st.astimezone(DUBLIN).date()),F.course_key(x.get('venue_canonical') or x.get('venue')),st.strftime('%H:%M'))
            if k in external and external[k].get('race_id') and x.get('race_id') and external[k]['race_id']!=x['race_id']:ambiguous.add(k)
            if k not in external or len(x.get('runners') or [])>len(external[k].get('runners') or []):external[k]=x
    # Saved declarations are a floor, not an external fixture census.
    ledger=read(F.AUDIT_FILE).get('coverage_guard',{}).get('expected_races',{})
    from scripts.rhp_stage70_data_coverage import is_nr
    for p in ('data/racecards-archive.json','data/racecards.json'):
        for x in read(p).get('runners',[]):
            day=str(x.get('date') or x.get('race_time') or '')[:10];co=F.course_key(x.get('course'));off=F.off_key(x.get('race_time') or x.get('off_dt'));h=F.horse_key(x.get('horse'))
            if day in {str(d) for d in days} and co and off and h:ledger.setdefault('|'.join((day,co,off)),{})[h]='NR' if is_nr(x) else 'declared'
    for day in days:
        ds=str(day);p=F.RESULTS_DIR/(ds+'.json');doc=read(p);arch=doc.get('results',[]);idx=collections.defaultdict(list)
        for r in arch:
            if r.get('status')!='superseded':idx[slot(r)].append(r)
        complete=all(queries.get(str(d),{}).get('complete') for d in sydney_dates(day))
        for k,x in sorted(external.items()):
            if k[0]!=ds:continue
            name='|'.join(k)
            if k in ambiguous:notes.append({'race':name,'action':'HELD_AMBIGUOUS_PROVIDER_SLOT'});continue
            if timestamp(x.get('start_time')).astimezone(DUBLIN).strftime('%H:%M')>='20:00':checked_late.append({'race':name,'archive_final':any(r.get('status')=='final' for r in idx.get(k,[]))})
            if idx.get(k):
                # Never replace independently verified records or remove starters.
                finals=[r for r in idx[k] if r.get('status')=='final']
                norm=E.normalized_result(x)
                if len(finals)==1 and norm:
                    prior=rows(finals[0]); fresh=rows(norm); oldwins={h for h,z in prior.items() if F.is_win(z)}; newwins={h for h,z in fresh.items() if F.is_win(z)}
                    oldactive={h for h,z in prior.items() if F.is_starter(z)}; newactive={h for h,z in fresh.items() if F.is_starter(z)}
                    if oldwins!=newwins or oldactive!=newactive:notes.append({'race':name,'action':'EXISTING_FINAL_PRESERVED_REVIEW_PROVIDER_DIFFERENCE','archive_only_starters':sorted(oldactive-newactive),'provider_only_starters':sorted(newactive-oldactive),'archive_winners':sorted(oldwins),'provider_winners':sorted(newwins)})
                elif not finals:notes.append({'race':name,'action':'EXISTING_NONFINAL_REVIEW'})
                continue
            if not complete:notes.append({'race':name,'action':'HELD_INCOMPLETE_QUERY'});continue
            card=ledger.get(name,{})
            normalized=E.normalized_result(x)
            if not normalized or not card:notes.append({'race':name,'action':'HELD_NO_COMPLETE_FIELD_OR_CARD'});continue
            nr=rows(normalized);active={h for h,z in nr.items() if F.is_starter(z)}
            explicit_nr={'nr','n/r','scratched','non runner','non-runner','nonrunner','withdrawn','dns','reserve'}
            if not active or not active.issubset(card) or set(h for h,s in card.items() if s!='NR')-set(nr) or any(not F.is_starter(z) and str(z.get('status')).lower() not in explicit_nr for z in nr.values()):
                notes.append({'race':name,'action':'HELD_FIELD_OR_STATUS_MISMATCH'});continue
            if any(F.course_key(r.get('course'))==k[1] and active & set(rows(r)) for r in arch if r.get('status')!='superseded'):
                notes.append({'race':name,'action':'HELD_OVERLAPPING_RACE'});continue
            if not all(z.get('trainer') and z.get('jockey') for h,z in nr.items() if h in active):
                notes.append({'race':name,'action':'HELD_MISSING_EXPLICIT_PERSON_IDENTITIES'});continue
            # Only append a complete, uniquely matched final race. Existing finals unchanged.
            if slot(normalized)!=k:notes.append({'race':name,'action':'HELD_SLOT_MISMATCH'});continue
            arch.append(normalized);idx[k].append(normalized);doc['results']=arch;doc['date']=ds;save(p,doc);notes.append({'race':name,'action':'ADDED_VERIFIED_COMPLETE_FIELD','starters':len(active)})
        for name,card in ledger.items():
            if not name.startswith(ds+'|') or not any(s!='NR' for s in card.values()):continue
            k=tuple(name.split('|'))
            if not any(r.get('status')=='final' for r in idx.get(k,[])) and not any(n['race']==name for n in notes):notes.append({'race':name,'action':'MISSING_FINAL_REVIEW'})
    return {'status':'COMPLETE_QUERIES' if all(x['complete'] for x in queries.values()) else 'PARTIAL_QUERIES','irish_dates':[str(d) for d in days],'queries':queries,'actions':notes,'late_races_checked':checked_late,'provider_matched_scope_not_independent_census':True}

if __name__=='__main__':
    report={'historical':verify_old() if os.getenv('GITHUB_EVENT_NAME')=='workflow_dispatch' else read(OUT/'reconciliation.json').get('historical',[]),'morning':morning(),'independent_aggregate_validation':'OUTSTANDING: no complete independent person-level data with identical date boundaries','hpr_changed':False}
    save(OUT/'reconciliation.json',report)
    print(json.dumps({'historical_verified':sum(x['verified'] for x in report['historical']),'historical_total':len(report['historical']),'morning':report['morning']},indent=2))

