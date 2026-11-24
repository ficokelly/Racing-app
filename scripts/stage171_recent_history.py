#!/usr/bin/env python3
"""Stage171: conservative, prior-date full-field bridge using existing app data.
Not a replacement for complete detailed results. Never impute comments or margins.
Saved compact records are derived from already-published app archive data only.
"""
import collections, csv, datetime as dt, hashlib, json, pathlib, re, sqlite3, unicodedata
from zoneinfo import ZoneInfo
from scripts.stage68_result_reconciler import sp_decimal
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
BASE='2026-11-20'
SOURCE='APP_VERIFIED_FINAL_PLUS_DECLARATIONS_STAGE171'

def verified_source(race):
    v=race.get('external_verification') or {}
    return (race.get('status')=='final' and isinstance(v,dict)
            and v.get('full_field_confirmed') is True and bool(v.get('source_race_id'))
            and 'puntersedge' in str(v.get('source') or race.get('source') or '').casefold())

NR={'nr','nonrunner','withdrawn','scratched','dns'}
TERMINAL={'pu':'PU','pulledup':'PU','f':'F','fell':'F','ur':'UR','unseatedrider':'UR','bd':'BD','broughtdown':'BD','ro':'RO','ranout':'RO','refused':'REF','ref':'REF','r':'REF','dq':'DQ','dsq':'DQ','disqualified':'DQ','su':'SU','slippedup':'SU'}
def norm(x):
    s=unicodedata.normalize('NFKD',str(x or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]','',s)
def horse(x):return norm(re.sub(r'\s*\((?:ire|gb|fr|usa|ger|aus|nz)\)\s*$','',str(x or ''),flags=re.I))
def course(x):return norm(re.sub(r'\s*\((?:aw|ire|gb)\)\s*$','',str(x or ''),flags=re.I))
def off(x):
    raw=str(x.get('race_time') or x.get('off_dt') or x.get('off') or '')
    try:
        d=dt.datetime.fromisoformat(raw.replace('Z','+00:00'))
        if d.tzinfo is None:return None
        d=d.astimezone(ZoneInfo('Europe/Dublin'))
        return d.hour*60+d.minute
    except ValueError:
        if re.fullmatch(r'\d\d:\d\d',raw):
            h,m=map(int,raw.split(':'));return h*60+m if h<24 and m<60 else None
        return None
def rows(obj):
    if isinstance(obj,list):
        for v in obj:yield from rows(v)
    elif isinstance(obj,dict):
        if obj.get('horse') and obj.get('course') and obj.get('date'):yield obj
        else:
            for v in obj.values():
                if isinstance(v,(dict,list)):yield from rows(v)
def nonrunner(r):
    return any(norm(r.get(k)) in NR|{'true','1','yes'} for k in ('is_non_runner','is_withdrawn','runner_status','status','withdrawn'))


def recover(ROOT, DAYS):
    fields=('horse_id','sire','dam','trainer','jockey','race_name','type','surface','going','distance_f','race_class','ofr','lbs','draw','age','sex')
    grouped=collections.defaultdict(lambda:collections.defaultdict(list))
    for p in (ROOT/'racecards-archive.json',ROOT/'racecards.json'):
        if not p.exists():continue
        for row in rows(json.loads(p.read_text())):
            day=str(row.get('date'))[:10]; venue=course(row.get('course')); rid=str(row.get('race_id') or '')
            if day in DAYS and venue and rid and off(row) is not None:
                grouped[(day,venue,rid)][horse(row['horse'])].append(row)
    card_index=collections.defaultdict(list)
    for (day,venue,rid),group in grouped.items():
        # Duplicate observations are safe only if all material metadata agrees.
        if any(not h or len({tuple(str(r.get(k) or '') for k in fields)+(str(off(r)),str(nonrunner(r))) for r in rr})!=1 for h,rr in group.items()):continue
        card={h:rr[0] for h,rr in group.items()}
        if len({off(r) for r in card.values()})!=1:continue
        if len({tuple(str(r.get(k) or '') for k in ('race_name','type','surface','going','distance_f','race_class')) for r in card.values()})!=1:continue
        card_index[(day,venue)].append((rid,card))
    def match(day,race):
        rr=race.get('runners') or []; names=[horse(r.get('horse')) for r in rr]
        if not names or not all(names) or len(names)!=len(set(names)):return None,'bad_result_field'
        actual=set(names); clock=off(race)
        if clock is None:return None,'missing_result_time'
        candidates=[]
        for rid,card in card_index.get((day,course(race.get('course'))),[]):
            cardnames=set(card)
            if not actual<=cardnames:continue
            if any(not nonrunner(card[h]) for h in cardnames-actual):continue
            gap=abs(clock-off(next(iter(card.values()))))
            if gap!=0:continue  # production bridge uses exact scheduled slot only
            candidates.append((rid,card,gap))
        if len(candidates)!=1:return None,'no_full_field_match' if not candidates else 'ambiguous_field_match'
        rid,card,gap=candidates[0]
        first=next(iter(card.values()));metadata=[];positions=[]
        if not all(first.get(k) for k in ('race_name','type','surface','distance_f')):return None,'missing_race_metadata'
        for r,h in zip(rr,names):
            c=card[h];status=norm(r.get('status'));pos=r.get('position')
            if status in NR:
                if pos not in (None,''):return None,'conflicting_nonrunner'
                continue
            if not all(c.get(k) for k in ('horse_id','sire','dam')):return None,'missing_identity'
            if isinstance(pos,bool):return None,'invalid_position'
            try:
                p=int(float(pos)) if re.fullmatch(r'[0-9]+(?:\.0+)?',str(pos)) and float(pos)>0 else None
            except (TypeError,ValueError,OverflowError):p=None
            if p is None and (status not in TERMINAL or pos not in (None,'')):return None,'unknown_terminal_status'
            if p is not None and status in TERMINAL:return None,'conflicting_position'
            if p is not None:positions.append(p)
            metadata.append({'horse':r['horse'],'position':p,'outcome':'FINISHED' if p is not None else TERMINAL[status],'sp_decimal':sp_decimal(r.get('sp')),'card':{k:c.get(k) for k in fields},'race_comment':r.get('race_comment') or r.get('comment') or None,'overall_beaten':r.get('overall_beaten') if r.get('overall_beaten') is not None else r.get('distance_beaten_total')})
        if len(metadata)<4 or positions.count(1)!=1 or len(positions)!=len(set(positions)):return None,'unverified_finishing_order'
        if max(positions)>len(metadata) or sorted(positions)!=list(range(1,len(positions)+1)):return None,'noncontiguous_finishing_order'
        return {'date':day,'course':first['course'],'racecard_id':rid,'result_off_minute':clock,'card_off_minute':off(first),'off_difference_minutes':gap,'race_metadata':{k:first.get(k) for k in ('race_name','type','surface','going','distance_f','race_class','region','country')},'result_source':race.get('source'),'external_verification':race.get('external_verification'),'runners':metadata},'matched'
    summary={};accepted=[]
    for day in DAYS:
        p=ROOT/'racing-results'/f'{day}.json';stats=collections.Counter();potential=[]
        for race in json.loads(p.read_text()).get('results',[]) if p.exists() else []:
            if race.get('status')!='final':continue
            if not verified_source(race):stats['unverified_source']+=1;continue
            if str(race.get('date') or '')[:10]!=day:stats['date_conflict']+=1;continue
            stats['final_races']+=1;stats['source_runner_rows']+=len(race.get('runners',[]))
            result,reason=match(day,race);stats[reason]+=1
            if result:potential.append(result)
        ids=collections.Counter(r['racecard_id'] for r in potential)
        starters=collections.Counter(horse(h['horse']) for r in potential for h in r['runners'])
        for r in potential:
            if ids[r['racecard_id']]!=1 or any(starters[horse(h['horse'])]!=1 for h in r['runners']):
                stats['duplicate_race_or_starter_quarantined']+=1;continue
            accepted.append(r);stats['candidate_races']+=1;stats['candidate_starters']+=len(r['runners'])
            stats['different_off_time_races']+=int(r['off_difference_minutes']!=0)
            stats['comments_present']+=sum(bool(h['race_comment']) for h in r['runners'])
            stats['overall_beaten_present']+=sum(h['overall_beaten'] is not None for h in r['runners'])
        summary[day]=dict(stats)
    return accepted,summary


def sid(r):
    return str((r.get('external_verification') or {}).get('source_race_id') or '')

def valid_record(r,day):
    if r.get('date')!=day or not BASE<day or not sid(r) or not r.get('racecard_id'):
        return False
    v=r.get('external_verification') or {}
    if v.get('full_field_confirmed') is not True:return False
    if R.region(r.get('course')) not in ('GB','IRELAND'):return False
    if r.get('off_difference_minutes')!=0:return False
    h=r.get('runners') or []
    names=[horse(x.get('horse')) for x in h]
    ids=[str((x.get('card') or {}).get('horse_id') or '') for x in h]
    pos=[x['position'] for x in h if x.get('position') is not None]
    return (len(h)>=4 and all(names) and len(set(names))==len(h) and all(ids)
            and len(set(ids))==len(h) and all((x.get('card') or {}).get('sire') and (x.get('card') or {}).get('dam') for x in h)
            and sorted(pos)==list(range(1,len(pos)+1)) and bool(pos)
            and all((x.get('position') is not None and x.get('outcome')=='FINISHED') or
                    (x.get('position') is None and x.get('outcome') in set(TERMINAL.values())) for x in h))

def result_signature(r):
    return tuple(sorted((horse(h['horse']),h.get('position') or 0,h.get('outcome')) for h in r['runners']))

def still_matches(r,archive):
    # Recheck saved facts against current final archive. A corrected result invalidates old bridge data.
    matches=[x for x in archive if verified_source(x) and sid(x)==sid(r) and
             str(x.get('date') or '')[:10]==r['date'] and course(x.get('course'))==course(r['course'])
             and off(x)==r['result_off_minute']]
    if len(matches)!=1:return False
    a=[];names=[]
    for h in matches[0].get('runners',[]):
        name=horse(h.get('horse'));names.append(name)
        status=norm(h.get('status'));p=h.get('position')
        if status in NR:
            if p not in (None,''):return False
            continue
        if isinstance(p,bool):return False
        if re.fullmatch(r'[0-9]+(?:\.0+)?',str(p)) and float(p)>0 and status not in TERMINAL:
            a.append((name,int(float(p)),'FINISHED'))
        elif status in TERMINAL and p in (None,''):
            a.append((name,0,TERMINAL[status]))
        else:return False
    return len(names)==len(set(names)) and tuple(sorted(a))==result_signature(r)

def load_day(root,day):
    p=root/'racing-results'/f'{day}.json'
    return json.loads(p.read_text()).get('results',[]) if p.exists() else []

def compact(r):
    # Retain necessary, already-published facts and their provenance only.
    fields=('horse_id','sire','dam','trainer','jockey','ofr','lbs','draw','age','sex')
    return {**r,'runners':[{**h,'card':{k:h['card'].get(k) for k in fields}} for h in r['runners']]}

def refresh(root,target):
    target=dt.date.fromisoformat(target)
    ledger=root/'stage171-history';ledger.mkdir(exist_ok=True)
    days=[p.stem for p in sorted((root/'racing-results').glob('????-??-??.json')) if BASE<p.stem<target.isoformat()]
    # Only current rolling card data need reconciliation; saved evidence stays usable after cards expire.
    candidates,stats=recover(root,days)
    fresh=collections.defaultdict(list)
    for r in candidates:
        if valid_record(r,r['date']):fresh[r['date']].append(compact(r))
    totals=collections.Counter();daystats={}
    for day in days:
        p=ledger/f'{day}.json';old=json.loads(p.read_text()).get('races',[]) if p.exists() else []
        archive=load_day(root,day)
        accepted={sid(r):r for r in old if valid_record(r,day) and still_matches(r,archive)}
        for r in fresh[day]:
            if still_matches(r,archive):accepted[sid(r)]=r
        # Refuse overlapping fields or racecards from distinct result identities.
        seen=collections.Counter(horse(h['horse']) for r in accepted.values() for h in r['runners'])
        cards=collections.Counter(r['racecard_id'] for r in accepted.values())
        accepted=[r for r in accepted.values() if cards[r['racecard_id']]==1 and all(seen[horse(h['horse'])]==1 for h in r['runners'])]
        accepted.sort(key=lambda r:(r['result_off_minute'],r['course'],sid(r)))
        doc={'schema':'stage171-verified-results-declaration-bridge-v1','date':day,'source':'Existing app result archive and preoff racecards; declaration metadata is not confirmed actual weights, going or jockeys.','races':accepted}
        if accepted or p.exists():
            content=json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
            if not p.exists() or p.read_text()!=content:p.write_text(content)
        d={'source_final_races':sum(x.get('status')=='final' for x in archive),'accepted_races':len(accepted),'accepted_starters':sum(len(r['runners']) for r in accepted)}
        d['missing_or_excluded_races']=d['source_final_races']-d['accepted_races'];daystats[day]=d;totals.update(d)
    report={'status':'PARTIAL_VERIFIED_RESULT_BRIDGE','target_exclusive':target.isoformat(),'days':daystats,'totals':dict(totals),'note':'No inference of missing positions or margins. Racecard metadata is declaration-only. G5 retains its existing HPR cutoff.'}
    print('STAGE171_LEDGER',json.dumps(report),flush=True)
    return report

def append_bridge(db,reference,target,root=pathlib.Path('data')):
    # Private per-run database already built by validated Stage167; only append prior-date rows.
    assert target>BASE
    ledger=root/'stage171-history';counts=collections.Counter();appended=[];daily=collections.Counter()
    with sqlite3.connect(db) as con:
        con.execute('PRAGMA foreign_keys=ON')
        baseline=con.execute('select max(race_date) from historical_races').fetchone()[0]
        assert baseline==BASE,('unexpected bridge baseline',baseline)
        # Date-scoped slot/name collision checks prevent duplicate results under another ID.
        existing=set((d,R.canonical_course(c),I.normname(h)) for d,c,h in con.execute('select r.race_date,r.course,u.horse_name from historical_races r join historical_runs u on u.race_id=r.id where r.race_date>? and r.race_date<?',(BASE,target)))
        for p in sorted(ledger.glob('????-??-??.json')):
            day=p.stem
            if not BASE<day<target:continue
            doc=json.loads(p.read_text());assert doc['date']==day and doc['schema']=='stage171-verified-results-declaration-bridge-v1'
            archive=load_day(root,day)
            for r in doc['races']:
                if not valid_record(r,day) or not still_matches(r,archive):counts['rejected_stale_or_invalid']+=1;continue
                meta=r['race_metadata'];region=R.region(r['course'])
                if not all(meta.get(k) for k in ('race_name','type','surface','distance_f')):counts['missing_race_metadata']+=1;continue
                dist=I.number(meta['distance_f'])
                if not dist or not 0<dist<50:counts['invalid_distance']+=1;continue
                slots={(day,R.canonical_course(r['course']),I.normname(h['horse'])) for h in r['runners']}
                if len(slots)!=len(r['runners']) or slots&existing:counts['duplicate_or_conflicting_race']+=1;continue
                rid='stage171:'+day+':'+hashlib.sha256(sid(r).encode()).hexdigest()[:24]
                con.execute('INSERT INTO historical_races VALUES(?,?,?,?,?,?,?,?,?)',(rid,day,r['course'],meta['type'],meta['surface'],dist,meta.get('going'),meta.get('race_class'),SOURCE))
                for h in r['runners']:
                    card=h['card']; runner='id:'+str(card['horse_id'])
                    # Actual finishing status/SP from results; other fields are known declarations.
                    con.execute('INSERT INTO historical_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (rid,runner,str(card['horse_id']),h['horse'],I.normname(h['horse']),None,None,h['position'],h['outcome'],card.get('trainer'),card.get('jockey'),I.number(card.get('ofr')),I.weight_lbs(card.get('lbs')),h.get('sp_decimal'),I.number(card.get('draw')),I.number(card.get('age')),card.get('sex'),card['sire'],card['dam'],None,None,h.get('race_comment'),h.get('overall_beaten')))
                existing.update(slots);counts['new_races']+=1;counts['new_runners']+=len(r['runners']);daily[day]+=1
                appended.append(dict(stage2_race_id=rid,date=day,region_scope=region,course=r['course'],race_type=meta['type'],race_name=meta['race_name'],source_race_id=sid(r)))
        con.execute('INSERT OR IGNORE INTO data_provenance VALUES(?,?,?,?,?)',(SOURCE,'Existing repository dated final results and racecards','Existing published app data',dt.datetime.now(dt.timezone.utc).isoformat(),'Incomplete coverage; preoff declaration metadata; no imputed margins/comments'))
        assert con.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0]<target
        con.commit()
    if appended:
        with pathlib.Path(reference).open(encoding='utf-8-sig',newline='') as f:fields=csv.DictReader(f).fieldnames
        assert {'stage2_race_id','date','region_scope','course','race_type','race_name'}<=set(fields)
        with pathlib.Path(reference).open('a',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=fields,extrasaction='ignore').writerows(appended)
    print('STAGE171_BRIDGE_APPLIED',json.dumps({'target':target,'base_through':BASE,'counts':dict(counts),'races_by_day':dict(daily),'coverage':'PARTIAL_NOT_FULL_DAILY_COVERAGE','declaration_metadata':True}),flush=True)
    return dict(counts)

if __name__=='__main__':
    import sys
    day=sys.argv[1] if len(sys.argv)>1 else (dt.datetime.now(ZoneInfo('Europe/Dublin')).date()+dt.timedelta(days=1)).isoformat()
    refresh(pathlib.Path('data'),day)
