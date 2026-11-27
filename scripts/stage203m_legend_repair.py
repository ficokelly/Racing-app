"""Repair the one verified missing Chelmsford runner, without duplicating a race."""
import json, os, pathlib as P, sqlite3, re, unicodedata
from scripts import stage203j_domestic_recovery as J, stage203c_consolidated_history as V, stage203_identity_recovery as E
from scripts.rhp_runner_eligibility import eligibility
import import_historical_ukie as I
TARGET='2026-11-28'; DAY='2023-06-22'
RID='kaggle-deltaromeo-ukie:2023-06-22:chelmsford_aw:842313'
HID='hrs_35938518'; NAME='I Am Legend'; SIRE='Barocci'; DAM='Rock The Legend'
ROOT=P.Path('data'); LEDGER=ROOT/'stage203m-history'/'approved-2026-11-28.json'
RESULT='https://www.sportinglife.com/racing/results/2023-06-22/chelmsford-city/744029/ccr-ladies-day-2023-restricted-maiden-fillies-stakes-gbb-race'
PROFILE='https://www.racingpost.com/profile/horse/5134074/i-am-legend/form/'
ORDER=['Paradise Row','Eloped','Third Batch','Oh So Grand','Pourquoi',NAME,'Style Of Life','Huda Star','Hill Filly','Portraitist',"Arlecchino's Star"]
def norm(x):
    s=re.sub(r'\s*\((?:IRE|GB|FR|SWE|JPN)\)\s*$', '', str(x or ''),flags=re.I)
    return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().casefold())
def field(c):return J.query_field(c,RID)
def check_race(c):
    r=c.execute('select * from historical_races where id=?',(RID,)).fetchone()
    assert r and r[1]==DAY and norm(r[2]).startswith('chelmsford'), 'Wrong historical race'
    # The precise race ID, date, venue and all ten peer results identify the race.
    # Classification is independently confirmed as Flat/AW (Polytrack).
    print('EXISTING_RACE_METADATA',json.dumps(list(r)),flush=True)
    return list(r)
def peers(rows):
    others=[x for x in rows if x[7]!=6 and J.norm_state(x)[0]!='NR']
    want={p:norm(n) for p,n in enumerate(ORDER,1) if p!=6}
    got={x[7]:norm(x[3]) for x in others}
    assert len(others)==10 and got==want, ('Peer field mismatch',got,want)
    assert all(J.norm_state(x)[0]=='FINISHED' for x in others)
    return others
def load(root=ROOT):
    p=root/'stage203m-history'/'approved-2026-11-28.json'
    if not p.exists():return None
    z=json.loads(p.read_text());assert z['schema']=='stage203m-v1' and V.digest(z['payload'])==z['sha256']
    return z
def apply(db,ref,entry,target):
    if not entry or DAY>=target:return 'SKIPPED'
    p=entry['payload'];assert V.digest(p)==entry['sha256'];old,new=p['old'],p['new']
    assert new[0]==RID and new[3]==NAME and new[7]==6 and new[8]=='FINISHED'
    assert new[17]==SIRE and new[18]==DAM
    with sqlite3.connect(db) as c:
        c.execute('pragma foreign_keys=on');assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        race=check_race(c)
        assert V.digest(race) in (p['race_hash'],p['corrected_race_hash']), 'Race metadata changed since validation'
        rows=field(c);other=peers(rows)
        assert V.digest(other)==p['peer_hash'], 'Peer records changed; no blind overwrite'
        slots=[x for x in rows if x[7]==6 or norm(x[3])==norm(NAME)]
        if len(slots)==1 and V.digest(slots[0])==V.digest(new):
            assert race[3:5]==['Flat','AW'], 'Runner present but metadata differs'
            return 'ALREADY_PRESENT'
        if old is None:
            if slots:return 'EXISTING_SLOT_CHANGED_HELD'
            if any(x[1]==new[1] or x[2]==HID for x in rows):return 'DUPLICATE_ID_HELD'
            # Refuse a second appearance on the same day under another race ID.
            q=c.execute('select u.horse_name,u.horse_source_id from historical_runs u join historical_races r on r.id=u.race_id where r.race_date=? and r.id<>?', (DAY,RID))
            if any(norm(n)==norm(NAME) or hid==HID for n,hid in q):return 'OTHER_RACE_OVERLAP_HELD'
            c.execute('insert into historical_runs values('+','.join('?' for _ in range(23))+')',new);status='ADDED_MISSING_RUNNER'
        else:
            if len(slots)!=1 or V.digest(slots[0])!=V.digest(old):return 'EXISTING_SLOT_CHANGED_HELD'
            assert old[:2]==new[:2] and all(old[i]==new[i] for i in range(23) if i not in (2,3,4,17,18))
            if any(x[1]!=old[1] and x[2]==new[2] and new[2] for x in rows):return 'DUPLICATE_ID_HELD'
            c.execute('update historical_runs set horse_source_id=?, horse_name=?, horse_name_norm=?, sire=?, dam=? where race_id=? and runner_key=?',(new[2],new[3],new[4],new[17],new[18],RID,old[1]));status='REPAIRED_EXISTING_RUNNER'
        c.execute('update historical_races set race_type=?, surface=? where id=?',('Flat','AW',RID))
        assert V.digest(check_race(c))==p['corrected_race_hash']
        final=field(c);peers(final)
        assert len([x for x in final if J.norm_state(x)[0]!='NR'])==11
        assert c.execute('pragma quick_check').fetchone()[0]=='ok';c.commit()
    return status
def append(db,ref,src,target,root=ROOT):
    status=apply(db,ref,load(root),target);print('STAGE203M_SHARED_HISTORY',status,flush=True);return status
def audit(db,rows):return J.audit(db,rows)
def main():
    from stage167_validated_history import refreshed_inputs
    out=P.Path('reports/stage203m/output');out.mkdir(parents=True,exist_ok=True)
    rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r['date']==TARGET and eligibility(r)=='ACTIVE'];cards={r['horse_id']:r for r in rows};assert rows and len(cards)==len(rows)
    card=cards[HID];assert norm(card['horse'])==norm(NAME) and norm(card['sire'])==norm(SIRE) and norm(card['dam'])==norm(DAM) and E.expected(card,TARGET)==DAY
    refs=list(P.Path('reports/stage203m/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(refs)==1
    base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite')
    status='HELD';reason=None
    with refreshed_inputs(base,refs[0],TARGET) as (db,ref):
        before=audit(db,rows)
        if before[HID]['last_run_matched'] is True:status='ALREADY_MATCHED'
        else:
            try:
                with sqlite3.connect(db) as c:
                    race=check_race(c);rr=field(c);other=peers(rr)
                    V.dump(out/'existing-field.json',rr)
                    slots=[x for x in rr if x[7]==6 or norm(x[3])==norm(NAME)]
                    assert len(slots)<=1, 'Multiple potential slots'
                    if not slots:
                        old=None
                        new=[RID,'id:'+HID,HID,NAME,I.normname(NAME),'SWE',None,6,'FINISHED','Jane Chapple-Hyam','Dayverson De Barros',None,126.0,34.0,9,3,'F',SIRE,DAM,None,None,None,'1.25']
                    else:
                        old=slots[0];assert old[7]==6 and J.norm_state(old)==('FINISHED',6)
                        # Country suffix or exact name only; no guessing a differently named horse.
                        assert norm(old[3])==norm(NAME), ('Different sixth-place name',old[3])
                        assert all(not old[i] or norm(old[i])==norm(v) for i,v in [(17,SIRE),(18,DAM)]), 'Pedigree conflict'
                        assert old[2] in (None,'',HID) or (norm(old[17])==norm(SIRE) and norm(old[18])==norm(DAM)), 'Unverified historical ID'
                        assert old[13] in (None,34.0) and old[12] in (None,126.0) and old[14] in (None,9), 'SP, weight or draw conflict'
                        new=list(old);new[2]=old[2] or HID;new[3]=NAME;new[4]=I.normname(NAME);new[17]=SIRE;new[18]=DAM
                    corrected=list(race);corrected[3:5]=['Flat','AW']
                    payload={'old':old,'new':new,'race_hash':V.digest(race),'corrected_race_hash':V.digest(corrected),'peer_hash':V.digest(other),'original_race_type':race[3],'original_surface':race[4]}
                    entry={'schema':'stage203m-v1','payload':payload,'sha256':V.digest(payload),'sources':[RESULT,PROFILE,'https://paddocksense.com/horses/i-am-legend-35938518/','https://globalformbook.com/Home/RaceResultDetail/rac_10950069']}
                status=apply(db,ref,entry,TARGET)
                if status in ('ADDED_MISSING_RUNNER','REPAIRED_EXISTING_RUNNER'):
                    assert audit(db,rows)[HID]['last_run_matched'] is True, 'Repair not visible in model history'
                    assert apply(db,ref,entry,TARGET)=='ALREADY_PRESENT'
                    LEDGER.parent.mkdir(parents=True,exist_ok=True);V.dump(LEDGER,entry)
            except (AssertionError,ValueError,TypeError,KeyError) as ex:reason=str(ex);status='HELD'
        after=audit(db,rows)
        assert all(after[k]['starts']>=before[k]['starts'] and (before[k]['last_run_matched'] is not True or after[k]['last_run_matched'] is True) for k in before)
        summary={'target':TARGET,'active':len(rows),'horse':NAME,'status':status,'reason':reason,'last_run_matched':after[HID]['last_run_matched'],'raw_gaps':sum(x['last_run_matched'] is False or x['known_no_history'] for x in after.values()),'remaining':[x for x in after.values() if x['last_run_matched'] is False or x['known_no_history']],'hpr_replay_required':True}
        V.dump(out/'summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Stage203M\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
if __name__=='__main__':main()
