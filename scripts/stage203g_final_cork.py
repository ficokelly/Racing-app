"""Repair the single corroborated Cork race, with fingerprinted replay and full runner gap audit."""
import collections as C, json, os, pathlib as P, sqlite3
from scripts import stage203f_final_four as F, stage203e_final_nine as Q, stage203c_consolidated_history as V, stage203_identity_recovery as E, stage201_recent_history as S
from scripts.rhp_runner_eligibility import eligibility
import stage4_match_all_recurring_races as R
ROOT=P.Path('data'); LEDGER=ROOT/'stage203g-history'; TARGET='2026-11-26'; DAY='2026-11-01'; HID=Q.EXPECTED_IDS['Cosmic Front']; RID=F.SPECS[3][0]; n=V.n; digest=V.digest
# Scope comes from the completed 203D and 203E classification reports, not an inference from a blank form row.
OUTSIDE={'hrs_17396040242':'2026-02-22','hrs_17396043371':'2026-03-08','hrs_17396044414':'2026-01-25','hrs_17395399840':'2026-02-01','hrs_17394063757':'2026-03-08','hrs_56559538':'2026-04-19','hrs_51491811':'2026-10-29','hrs_17394539365':'2026-04-26','hrs_17396009995':'2026-02-21','hrs_17396014167':'2026-11-08','hrs_17395958888':'2025-11-01','hrs_64554315':'2026-11-01'}

# STAGE203H: externally verified last-run explanations, scoped by horse ID and exact date.
from scripts.stage203h_targeted_history import load_exceptions as _stage203h_exceptions
OUTSIDE.update(_stage203h_exceptions())
SOURCES=['https://www.racingpost.com/results/596/cork/2026-11-01/932263/','https://www.racingtv.com/results/2026-11-01/cork/1305','https://www.sportinglife.com/racing/results/2026-11-01/cork/946530/paddy-power-did-you-watch-the-breeders-cup-at-all-mares-maiden-hurdle']
def verified(src,root):
    s=Q.RACES[3]; groups,meta=V.load_source(src,{DAY}); data,changes,why=F.reconstruct(3,groups,meta,root)
    assert data and data[0][0]==RID,why
    assert len(data[1])==19 and len(s['nr'])==2
    expected={n(x[3]):F.state(x) for x in data[1]}; assert expected=={k:tuple(v) for k,v in s['states'].items()}
    # Match complete archive race including declared nonrunners; this is a second stored evidence source.
    candidates=[r for r in json.loads((root/'racing-results'/f'{DAY}.json').read_text())['results'] if Q.ck(r.get('course'))==Q.ck('Cork') and str(r.get('off_dt',''))[11:16]=='13:05' and r.get('status')=='final']
    agree=[]
    for r in candidates:
        states={n(h['horse']):S.state(h) for h in r['runners']}; assert len(states)==len(r['runners'])
        if states=={**expected,**{k:('NR',None) for k in s['nr']}}:agree.append(r)
    assert agree,'Archived result does not corroborate complete field'
    return data,expected,digest(agree[0])

def plan(c,data,expected,refids):
    race,runs,title,raw=data; wanted={n(x[3]):x for x in runs}; allowed=set(wanted)|set(Q.RACES[3]['nr']); hits=[]
    for rid,course in c.execute('select id,course from historical_races where race_date=?',(DAY,)):
        if Q.ck(course)!=Q.ck('Cork'):continue
        rows=F.race_rows(c,rid); names={n(x[3]) for x in rows}
        if rid==RID or len(names&set(wanted))>=10:hits.append((rid,rows))
    assert len(hits)==1,('Ambiguous Cork race',[(r,len(rows)) for r,rows in hits])
    rid,rows=hits[0]; old={n(x[3]):x for x in rows}
    assert len(old)==len(rows) and set(old)<=allowed,('Unexpected existing field',rid,sorted(set(old)-allowed))
    assert len(set(old)&set(wanted))>=10, 'Insufficient overlap'
    # Ensure no duplicate runner elsewhere that would be created by adding a missing starter.
    others=c.execute('select u.horse_name,u.horse_source_id from historical_runs u join historical_races r on r.id=u.race_id where r.race_date=? and r.id<>?',(DAY,rid)).fetchall()
    missing=set(wanted)-set(old)
    assert not ({n(x[0]) for x in others}&missing) and not ({x[1] for x in others if x[1]}&{wanted[k][2] for k in missing if wanted[k][2]})
    edits=[]; inserts=[]
    for k,x in sorted(old.items()):
        y=list(x); st=expected[k] if k in expected else ('NR',None)
        if F.state(x)!=st:
            y[7],y[8]=st[1],st[0]
        if k==n('Cosmic Front'):
            w=wanted[k]; assert n(x[17])==n(w[17]) or (x[2] and x[2]==w[2]), 'Cosmic Front existing identity conflict'
            y[17],y[18]=w[17],w[18]
        if y!=x:edits.append(dict(key=x[1],horse=x[3],before=digest(x),after=digest(y),old_state=list(F.state(x)),new_state=list(st),position=y[7],outcome=y[8],sire=y[17],dam=y[18]))
    for k in sorted(missing):
        y=list(wanted[k]);y[0]=rid;assert all(y[i] for i in (9,10,17,18));assert y[1] not in {x[1] for x in rows};inserts.append(dict(horse=y[3],key=y[1],sha256=digest(y)))
    ref=None if rid in refids else dict(stage2_race_id=rid,date=DAY,course=race[2],race_type=race[3],race_name=title,region_scope=R.region(race[2]),source_race_id=raw)
    return dict(id=rid,edits=edits,inserts=inserts,reference=ref)

def apply(c,data,expected,p,ids):
    rid=p['id']; wanted={n(x[3]):x for x in data[1]}; old={x[1]:x for x in F.race_rows(c,rid)}; count=0
    for e in p['edits']:
        x=old[e['key']];h=digest(x)
        if h==e['after']:continue
        assert h==e['before'],('Existing row changed since validation',e['horse'])
        c.execute('update historical_runs set finish_pos=?,outcome=?,sire=?,dam=? where race_id=? and runner_key=?',(e['position'],e['outcome'],e['sire'],e['dam'],rid,e['key']));assert c.execute('select changes()').fetchone()[0]==1;count+=1
        assert digest(list(c.execute('select * from historical_runs where race_id=? and runner_key=?',(rid,e['key'])).fetchone()))==e['after']
    for e in p['inserts']:
        x=list(wanted[n(e['horse'])]);x[0]=rid;assert digest(x)==e['sha256']
        if e['key'] in old:
            z=old[e['key']];assert z[:22]==x[:22] and str(z[22])==str(x[22]);continue
        c.execute('insert into historical_runs values('+','.join('?' for _ in range(23))+')',x);count+=1
    final=F.race_rows(c,rid); active={n(x[3]):F.state(x) for x in final if F.state(x)[0]!='NR'}
    assert len(active)==sum(F.state(x)[0]!='NR' for x in final)==19 and active==expected
    assert all(n(x[3]) in Q.RACES[3]['nr'] for x in final if F.state(x)[0]=='NR')
    ref=p['reference'] if rid not in ids else None
    if ref:ids.add(rid)
    assert rid in ids
    return count,ref

def append(db,ref,src,target,root=ROOT):
    if target<=DAY:return []
    p=root/'stage203g-history'/'approved-cork.json'
    if not p.exists():return []
    e=json.loads(p.read_text());assert e['schema']=='stage203g-v1';data,expected,arch=verified(src,root);assert digest(data)==e['source_sha256'] and arch==e['archive_sha256'];fields,ids=V.refs(ref)
    with sqlite3.connect(db) as c:
        c.execute('pragma foreign_keys=on'); count,ar=apply(c,data,expected,e['plan'],ids);assert c.execute('pragma quick_check').fetchone()[0]=='ok'; c.commit()
    V.write_refs(ref,fields,[ar] if ar else []);print('STAGE203G_SHARED',count,flush=True);return count

def run():
    import kagglehub
    from stage167_validated_history import refreshed_inputs
    out=P.Path('reports/stage203g/output');out.mkdir(parents=True,exist_ok=True);LEDGER.mkdir(parents=True,exist_ok=True)
    rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r['date']==TARGET and eligibility(r)=='ACTIVE'];assert rows and len({r['horse_id'] for r in rows})==len(rows) and all(r.get('sire') and r.get('dam') for r in rows)
    target_card=next(r for r in rows if r['horse_id']==HID);src=P.Path(kagglehub.dataset_download('deltaromeo/horse-racing-results-ukireland-2015-2025/versions/132'));refs=list(P.Path('reports/stage203g/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(refs)==1
    base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite');data,expected,arch=verified(src,ROOT);assert E.exact(target_card,V.aid(next(x for x in data[1] if n(x[3])==n('Cosmic Front'))))
    with refreshed_inputs(base,refs[0],TARGET) as (db,rf):
        h,o=E.scan(db,rows,TARGET);before={x['horse_id']:x for x in E.classify(rows,h,o,TARGET)};fields,ids=V.refs(rf)
        with sqlite3.connect(db) as c:
            c.execute('pragma foreign_keys=on');before_counts=c.execute('select count(*) from historical_runs').fetchone()[0];p=plan(c,data,expected,ids);V.dump(out/'repair-plan.json',p)
            count,ar=apply(c,data,expected,p,ids);again,ar2=apply(c,data,expected,p,ids);assert again==0 and ar2 is None
            assert c.execute('select count(*) from historical_runs').fetchone()[0]>=before_counts and c.execute('pragma quick_check').fetchone()[0]=='ok' and c.execute('select max(race_date) from historical_races').fetchone()[0]<TARGET;c.commit()
        V.write_refs(rf,fields,[ar] if ar else []);h,o=E.scan(db,rows,TARGET);after={x['horse_id']:x for x in E.classify(rows,h,o,TARGET)}
        assert after[HID]['last_run_matched'] and all(not x['last_run_matched'] or after[k]['last_run_matched'] for k,x in before.items()),'Missing target or regression'
        raw=[x for x in after.values() if x['last_run_matched'] is False or x['known_no_history']];unresolved=[x for x in raw if OUTSIDE.get(x['horse_id'])!=x['expected']];summary=dict(target=TARGET,active=len(rows),cosmic_front='RECOVERED',rows_changed=count,raw_gaps=len(raw),classified_external=len(raw)-len(unresolved),unexplained=len(unresolved),idempotence_passed=True,full_production_audit=False)
        V.dump(out/'summary.json',summary);V.dump(out/'remaining.json',unresolved);V.dump(out/'cosmic-front.json',after[HID]);print(json.dumps(summary,indent=2),flush=True)
        V.dump(LEDGER/'approved-cork.json',dict(schema='stage203g-v1',date=DAY,source_sha256=digest(data),archive_sha256=arch,plan=p,sources=SOURCES));P.Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('## Cork targeted repair\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
if __name__=='__main__':run()
