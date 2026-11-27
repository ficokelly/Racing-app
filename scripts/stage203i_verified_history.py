import collections as C, datetime as D, json, os, pathlib as P, subprocess
from scripts import stage203h_targeted_history as H, stage203c_consolidated_history as V, stage203_identity_recovery as E, stage171_recent_history as M
from scripts.rhp_runner_eligibility import eligibility
TARGET='2026-11-28'
ROOT=P.Path('data')
LEDGER=ROOT/'stage203i-history'/'approved-2026-11-28.json'
PIN='8f67fc4dd377b58e4eb7ab39bef78365a1b37db3'
RACES=[
  H.spec('2026-11-21','Ascot','15:18','Chase','Turf',16+172/220,'Good To Firm','Class 1',"Ryan's Rocket",'hrs_29283590','https://www.racingpost.com/results/2/ascot/2026-11-21/930672/',H.sparse("""
Vanderpoel|1|6/5|Ben Pauling|Ben Jones|12-0|148||0
Sans Bruit|2|11/2|Paul Nicholls|Jay Tidball(5)|10-8|133||
Martator|3|10/1|Venetia Williams|Charlie Deutsch|11-13|147||2.5
Gabriel's Getaway|4|16/1|Anthony Honeyball|Sam Twiston-Davies|10-2|122||13.5
Shakeyatailfeather|5|8/1|Dan Skelton|Harry Atkins(5)|9-12|123||16.25
Ryan's Rocket|BD|11/2|Fergal O'Brien|Jonathan Burke|11-5|139||
Calico|UR|17/2|Dan Skelton|Dominic Lewis(10)|11-4|148||
"""),pins=('b3dd9cbae6384096be4efe47b1e4dfcfc05ad908',)),
  H.spec('2026-11-21','Haydock','15:00','Chase','Turf',25+125/220,'Good','Class 1','Konfusion','hrs_30709889','https://www.racingpost.com/results/23/haydock/2026-11-21/930673/',H.sparse("""
Grey Dawning|1|11/10|Dan Skelton|Harry Skelton|11-10|166||0
Jagwar|2|17/2|Oliver Greenall & Josh Guerriero|Harry Cobden|11-10|155||3.75
Handstands|3|22/1|Ben Pauling|Kielan Woods|11-10|153||6.25
Salver|4|12/1|Jamie Moore & Josh Moore|Caoilin Quinn|11-10|150||8.5
Affordale Fury|5|7/2|Noel Meade|Sam Ewing|11-10|167||37.5
Gorgeous Tom|PU|9/2|Henry De Bromhead|Jordan Gainford|11-10|154||
Konfusion|PU|28/1|Joel Parkinson & Sue Smith|Gavin Sheehan|11-10|144||
"""),pins=('b3dd9cbae6384096be4efe47b1e4dfcfc05ad908',)),
  H.spec('2026-11-27','Doncaster','12:00','Chase','Turf',23+214/220,'Good','Class 3','Palamon','hrs_31479336','https://www.racingpost.com/results/15/doncaster/2026-11-27/931689/',H.sparse("""
Palamon|1|2/1|Dan Skelton|Dominic Lewis(10)|11-0|131||0
Kent De Thaix|2|11/8|Sam England|Richie McLernon|11-1|122||21
Kayf Dancer|3|2/1|Mickey Bowen|Shane Fenelon(5)|10-5|117||75
"""),pins=('e3170a54e212f741b40ede981b463303bff1fc7e',))
]
def load(root=ROOT):
    p=root/'stage203i-history'/'approved-2026-11-28.json'
    if not p.exists():return []
    d=json.loads(p.read_text());assert d['schema']=='stage203i-v1'
    for e in d['races']:assert V.digest(e['data'])==e['data_sha256']
    return d['races']
def append(db,ref,src,target,root=ROOT):
    report=H.apply_entries(db,ref,load(root),target)
    print('STAGE203I_SHARED_HISTORY',dict(C.Counter(x['status'] for x in report)),flush=True)
    return report
def audit(db,rows):
    a,b=E.scan(db,rows,TARGET)
    return {x['horse_id']:x for x in E.classify(rows,a,b,TARGET)}
def main():
    from stage167_validated_history import refreshed_inputs
    out=P.Path('reports/stage203i/output');out.mkdir(parents=True,exist_ok=True)
    rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r['date']==TARGET and eligibility(r)=='ACTIVE']
    cards={r['horse_id']:r for r in rows};assert rows and len(cards)==len(rows)
    refs=list(P.Path('reports/stage203i/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(refs)==1
    base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite')
    old={e['target_horse_id']:e for e in load()}
    decisions=[];candidates=[]
    with refreshed_inputs(base,refs[0],TARGET) as (db,ref):
        before=audit(db,rows);H.ARCHIVE_COMMIT=PIN
        for s in RACES:
            hid=s['horse_id'];card=cards.get(hid)
            if not card or H.n(card['horse'])!=H.n(s['horse']) or E.expected(card,TARGET)!=s['day']:
                decisions.append({'horse':s['horse'],'status':'CARD_CHANGED'});continue
            if before[hid]['last_run_matched'] is True:
                decisions.append({'horse':s['horse'],'status':'ALREADY_MATCHED'});continue
            try:
                expected=H.public_fields(s);_,archive=H.historical_archive(s,expected)
                assert all(M.sp_decimal(archive[k].get('sp'))==M.sp_decimal(r[2]) for k,r in expected.items()),'Archive price conflict'
                candidates.append(H.build(s,card))
            except (AssertionError,ValueError,TypeError,KeyError,subprocess.CalledProcessError) as ex:
                decisions.append({'horse':s['horse'],'status':'HELD','reason':str(ex)})
        applied=H.apply_entries(db,ref,candidates,TARGET);decisions.extend(applied)
        after=audit(db,rows)
        assert all(after[k]['starts']>=before[k]['starts'] and (before[k]['last_run_matched'] is not True or after[k]['last_run_matched'] is True) for k in before)
        added={x['horse_id'] for x in applied if x['status']=='ADDED'}
        assert all(after[k]['last_run_matched'] is True for k in added)
        approved=[e for e in candidates if e['target_horse_id'] in added]
        again=H.apply_entries(db,ref,approved,TARGET);assert all(x['status']=='ALREADY_PRESENT' for x in again)
        for e in approved:old[e['target_horse_id']]=e
        if approved:
            LEDGER.parent.mkdir(parents=True,exist_ok=True)
            V.dump(LEDGER,{'schema':'stage203i-v1','races':list(old.values())})
        raw=[x for x in after.values() if x['last_run_matched'] is False or x['known_no_history']]
        summary={'target':TARGET,'active':len(rows),'added_races':len(approved),'added_starters':sum(len(e['data'][1]) for e in approved),'decisions':decisions,'raw_gaps':len(raw),'remaining':raw,'idempotence_passed':True,'hpr_replay_required':True}
        V.dump(out/'summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Stage203I results\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
if __name__=='__main__':main()
