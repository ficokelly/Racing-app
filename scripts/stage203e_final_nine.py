"""Final nine scoped repairs. Public result fields independently corroborated; pinned source rows reconstructed privately."""
import collections as C, datetime as D, json, os, pathlib as P, sqlite3, subprocess, hashlib, re
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
from scripts import stage203c_consolidated_history as V,stage203d_archive_recovery as W,stage203_identity_recovery as E,stage201_recent_history as S,stage171_recent_history as M
from scripts.rhp_runner_eligibility import eligibility
ROOT=P.Path('data'); LEDGER=ROOT/'stage203e-history'; n=V.n; digest=V.digest
# Dated results: full starter field, terminal states, official race metadata, corroborating URLs.
def spec(day,course,off,title,typ,surface,dist,going,cl,order,terminal,urls,targets,sp='',nr=()):
    names=order.split('|'); states={n(h):('FINISHED',i) for i,h in enumerate(names,1)};states.update({n(h):(s,None) for h,s in terminal.items()})
    prices={n(h):v for h,v in zip(names+list(terminal),sp.split('|'))} if sp else {}
    return dict(day=day,course=course,off=off,title=title,type=typ,surface=surface,dist=dist,going=going,cl=cl,states=states,prices=prices,urls=urls,targets=targets,nr=[n(x) for x in nr])
RACES=[
 spec('2026-11-16','Wolverhampton (AW)','20:30','Make The Move To Midnite Handicap Stakes (Rider Restricted Race)','Flat','AW',12,'Standard','Class 6',"It's A Love Thing|Sea Of Charm|Heart Sign|Saxophonic|Man Of Desert|The Bay Warrior|Rogue Sea|Kathleen Mary|Havachoc|Tiger Beetle|Hellion",{},['https://www.racingpost.com/results/513/wolverhampton-aw/2026-11-16/931753/','https://racingbrief.com/results/2026-11-16/wolverhampton-aw/8-30'],['Sea of Charm'],'7/1|22/1|11/1|11/10|12/1|12/1|50/1|8/1|6/1|16/1|66/1'),
 spec('2026-11-16','Plumpton','13:15',"Paul Ferguson's Jumpers To Follow Amateur Jockeys' Handicap Hurdle",'Hurdle','Turf',20.5,'Good To Soft','Class 5','Prairie Diamond|Rip Wheeler|Market House|Bread And Butter|Kandor|Glitz And Glamour|Cadillac Ranch|Whatsgoingonmarvin|Nelson Gate|Jefe Triunfo',{'No No Fizz':'PU','Girl Of Gold':'PU'},['https://www.racingpost.com/results/44/plumpton/2026-11-16/931537/','https://racingbrief.com/results/2026-11-16/plumpton/1-15'],['Rip Wheeler','Kandor'],'100/30|9/2|100/30|17/2|17/2|25/1|5/1|16/1|50/1|11/1|50/1|40/1'),
 spec('2026-11-16','Plumpton','14:45',"More Radio Novices' Limited Handicap Hurdle",'Hurdle','Turf',25,'Good To Soft','Class 5',"Of Corse I Can|Golly Gosh|Ephyra|Athena's Dream|Good Girl Rachel|Jurassic Pet",{'Anothermanicmonday':'PU'},['https://www.racingpost.com/results/44/plumpton/2026-11-16/931536/','https://www.racingtv.com/results/2026-11-16/plumpton/1445'],['Anothermanicmonday'],'7/1|15/8|10/1|17/2|13/2|6/1|4/1',('Alan Bresil',)),
 spec('2026-11-01','Cork','13:05',"Paddy Power Did You Watch The Breeders Cup At All Mares Maiden Hurdle",'Hurdle','Turf',16,'Good To Yielding',None,"Cornelienne|Lennon Grove|I Dont Really Care|Filly Butler|Weaponized|Kildinan Lass|Streamhill|Westie's Eile|Cosmic Front|Hillview Lass|Miss Stargazer|Lennora|Eagle River|Not A Whisper",{'Afloat':'F','Soldier Power':'F','Cerulean Summer':'PU','Potters Queen':'PU','Eagle Doll':'UR'},['https://www.racingpost.com/results/596/cork/2026-11-01/932263/','https://racingbrief.com/results/2026-11-01/cork'],['Cosmic Front'],'5/2|8/15|7/1|125/1|50/1|50/1|12/1|40/1|25/1|125/1|125/1|150/1|125/1|150/1|66/1|200/1|150/1|100/1|150/1',('Burning Like Fire','Tintara')),
 spec('2024-04-13','Chepstow','13:10',"DragonBet Born From The Betting Ring Mares' Novices' Hurdle",'Hurdle','Turf',16.05,'Good To Soft','Class 4',"Casa No Mento|She's My Shadow|Either Or|Jaminska|Get Sky High|Ellies Sister|Gone Away Madam|Extra Cover",{},['https://www.racingpost.com/results/12/chepstow/2024-04-13/863224/','https://www.irishracing.com/raceresults/Sat-13th-Apr-2024/Chepstow/1310'],['Gone Away Madam'],'10/11|40/1|9/4|11/2|15/2|150/1|50/1|125/1'),
 spec('2025-05-14','Newton Abbot','14:50',"Terry Wheeler Birthday Novices' Handicap Hurdle",'Hurdle','Turf',18.5,'Good','Class 5','Get The Value|Stinginhisstep|Square Du Roule|Oval Street|Inchagoill Lady|Byzantium|Mr Zee|Kintaro|Ventara',{'Pink Eyed Pancho':'PU'},['https://insights.betfred.com/horse-racing/results/2025-05-14/newton-abbot/892971/14-50/','https://www.sportinglife.com/racing/results/2025-05-14/newton-abbot/857345/terry-wheeler-birthday-novices-handicap-hurdle'],['Ventara'],'9/2|40/1|9/1|50/1|9/1|14/1|11/2|14/1|9/2|5/2',('Thankyouandplease',)),
 spec('2025-04-20','Market Rasen','16:20','Malton Marquees Handicap Hurdle','Hurdle','Turf',18.63636,'Good','Class 5','Robbers Bridge|Greatness Awaits|Livano Bello|Aquitaine Boy|Gms Prince|Zucayan|Ikigai Star|Sambezi|Macho Sun|Takes Time',{'Red Trail':'PU'},['https://gg.co.uk/racecards/2025-04-20/market-rasen-1620/','https://www.irishracing.com/raceresults/Sun-20th-Apr-2025/Market-Rasen/1620'],['Takes Time'],'6/1|13/2|12/1|18/5|8/1|4/1|9/1|7/2|150/1|100/1|66/1')]
# Independent profile pedigree (including two forms of the same Girl Of Gold dam name).
PARENTS={n(k):v for k,v in {
 'Gone Away Madam':('Blue Bresil','Hope Royal','https://www.irishracing.com/horse/Gone-Away-Madam-GB/682624'),
 'Ventara':('Kayf Tara','Venceremos','https://www.irishracing.com/horse/Ventara-GB/642846'),
 'Takes Time':('Time Test','On A Whim','https://www.irishracing.com/horse/Takes-Time-GB/752739'),
 'Sea of Charm':('Charm Spirit','Sea Meets Sky','https://www.irishracing.com/horse/Sea-Of-Charm-FR/604457'),
 'Anothermanicmonday':('Masterstroke','The Prime Viper','https://www.irishracing.com/horse/Anothermanicmonday-IRE/822269'),
 'Cosmic Front':('Siyouni','Bureaucracy','https://www.racingandsports.co.uk/thoroughbred/horse/cosmic-front/2101567'),
 'Casa No Mento':('Casamento','Red Hibiscus','https://www.racingandsports.co.uk/amp/thoroughbred/horse/casa-no-mento/1964473'),
 'Byzantium':('Magneticjim','Ticket Holder','https://optimaracing.co.uk/horse/byzantium-fr'),
 'Aquitaine Boy':('Walk In The Park','Dolce Vita Yug','https://www.irishracing.com/raceresults/Sun-20th-Apr-2025/Market-Rasen/1620'),
 'Girl Of Gold':('Golden Horn','Surrealism I','https://www.horseracinguk.co.uk/horses/girl-of-gold/')}.items()}
# Explicit actual connections for archive fallbacks and missing unrelated metadata. Declared weights never substituted for actual carried weights.
CONNECTIONS={}
def conn(names,trainers,jockeys):
    assert len(names.split('|'))==len(trainers.split('|'))==len(jockeys.split('|'))
    CONNECTIONS.update({n(h):(t,j) for h,t,j in zip(names.split('|'),trainers.split('|'),jockeys.split('|'))})
conn("It's A Love Thing|Sea Of Charm|Heart Sign|Saxophonic|Man Of Desert|The Bay Warrior|Rogue Sea|Kathleen Mary|Havachoc|Tiger Beetle|Hellion","Sam England|Georgina Nicholls|Tony Carroll|David Simcock|Chris Dwyer|Mark Usher|John & Rhys Flint|Gihan Arnolda|Patrick Morris|Thomas Faulkner|Donald McCain","Jonny Peate|William Cox|Myla Coppins|Jack Dace|Jake Dickson|Rose Dawes|Taylor Fisher|Laura Pearson|Rhys Clutterbuck|Zoe Lewis|Dougie Costello")
conn('Prairie Diamond|Rip Wheeler|Market House|Bread And Butter|Kandor|Glitz And Glamour|Cadillac Ranch|Whatsgoingonmarvin|Nelson Gate|Jefe Triunfo|No No Fizz|Girl Of Gold',"Martin Keighley|Jamie Moore & Josh Moore|James Owen|Cynthia Woods|Nick Scholfield|Neil King|L J Morgan|Mickey Bowen|Jamie Moore & Josh Moore|Chris Gordon|Fergal O'Brien|Andy Irvine","Mr Harry Hoyles|Mr Hezekiah March|Mr Alex Chadwick|Miss Molly Legg|Mr Daniel Williams|Mr Evan Lonergan|Miss Ellie Callwood|Miss Abigail Voyce|Miss Elizabeth Feakes|Miss Milica Jovana Dusanovic|Mr Liam O'Hara|Mr James King")
conn("Of Corse I Can|Golly Gosh|Ephyra|Athena's Dream|Good Girl Rachel|Jurassic Pet|Anothermanicmonday","Neil Mulholland|T Ellis|Andy Irvine|Harriet Dickin|Ben Clarke|Mrs Sara V Bradstock|Sue Gardner","Bradley Harris|Jack Andrews|Tom Cannon|Isabelle Ryder|Sam Twiston-Davies|Miss Lily Bradstock|Sean Houlihan")
conn("Cornelienne|Lennon Grove|I Dont Really Care|Filly Butler|Weaponized|Kildinan Lass|Streamhill|Westie's Eile|Cosmic Front|Hillview Lass|Miss Stargazer|Lennora|Eagle River|Not A Whisper|Afloat|Soldier Power|Cerulean Summer|Potters Queen|Eagle Doll","Joseph Patrick O'Brien|Gavin Cromwell|Ray Hackett|G Ahern|Sean Thomas Doyle|Jonathan Sweeney|Padraig Butler|Jonathan Sweeney|W P Mullins|Donncha Duggan|E Bolger|Daniel Joseph O'Leary|Ms M Flynn|Peter Fahey|Gavin Cromwell|Mrs Y Dunleavy|Lawrence Walshe|Gerard Michael Mangan|Eoin Christopher McCarthy","Harry Cobden|Keith Donoghue|Daniel King|John Shinnick|Ross Sugrue|Aidan Kelly|Danny Mullins|Alan O'Sullivan|Paul Townend|Niall Moore|Rian Lane|Sean Flanagan|Liam Quinlan|Sam Ewing|Conor Stone-Walsh|Josh Williamson|John Aherne|Phillip Enright|G B Noonan")
conn('Casa No Mento|Byzantium|Aquitaine Boy|Gone Away Madam|Ventara|Takes Time','Nigel Twiston-Davies|Paul Nicholls|Ruth Jefferson|Neil King|Joe Tizzard|Mike Sowersby','Toby McCain-Mitchell|Jay Tidball|Jamie Hamilton|Jack Quinlan|Brendan Powell|Charlie Maggs')
# Independent pre-2026 career profile anchors, each date/venue/outcome exact, not a name-only merge.
CAREER={
 'Gone Away Madam': [('2024-04-13','Chepstow',7),('2024-03-02','Newbury',8),('2023-07-28','Uttoxeter',4),('2023-06-17','Uttoxeter',1)],
 'Ventara':[('2025-05-14','Newton Abbot',9),('2024-11-28','Taunton',6),('2024-10-29','Chepstow',2),('2023-11-23','Wincanton',4),('2023-10-31','Chepstow',2),('2023-04-16','Wincanton',4),('2023-03-27','Wincanton',5),('2023-03-02','Taunton','F')],
 'Takes Time':[('2025-04-20','Market Rasen',10),('2025-03-01','Doncaster',8),('2025-02-18','Market Rasen',6),('2024-12-17','Catterick',8),('2024-10-08','Leicester',5),('2024-09-19','Chelmsford',12),('2024-09-13','Chester',2),('2024-09-09','Wolverhampton',7),('2024-08-20','Nottingham',6),('2024-07-29','Wolverhampton',7),('2024-07-11','Carlisle',5),('2024-05-24','Haydock',3),('2024-05-09','Chester',9),('2024-04-27','Haydock',2),('2024-04-13','Wolverhampton',9),('2024-04-02','Wolverhampton',7)]}
EXPECTED_IDS={'Gone Away Madam':'hrs_35416710','Ventara':'hrs_34401864','Takes Time':'hrs_40394361','Sea of Charm':'hrs_22327886','Rip Wheeler':'hrs_39431371','Kandor':'hrs_34253275','Anothermanicmonday':'hrs_52772209','Cosmic Front':'hrs_412652261','Mambo Du Large':'hrs_64554315'}
OUTSIDE={'horse':'Mambo Du Large','horse_id':EXPECTED_IDS['Mambo Du Large'],'date':'2026-11-01','type':'POINT_TO_POINT','venue':'Rathcannon','outcome':'PU','sources':['https://www.ihrb.ie/horse-hunt-single?date=2026-11-1&horseSearch=&huntSearch=&name=Limerick+%28F%29+Bruff+%40+Rathcannon&raceName=all','https://www.irishracing.com/raceresults/Sun-1st-Nov-2026/Rathcannon-P/1230']}

def ck(x):return W.keycourse(x)
def state_pair(v):return tuple(v)
def public_price(s,k):return M.sp_decimal(s['prices'].get(k))
def same_state(x,y):return tuple(x)==tuple(y)
def src_field(groups,rid,s):
    by=W.source_field(groups,rid)
    # Drop only explicitly verified non-runners, not an unexplained extra row.
    by={k:vs for k,vs in by.items() if k not in s['nr']}
    if set(by)!=set(s['states']):return None
    for k,vs in by.items():
        actual={V.st(x) for x in vs if V.st(x)[0]!='UNKNOWN'}
        if actual and actual!={tuple(s['states'][k])}:return None
    return by

def cards_for(root,s):
    p=root/'stage203e-history'/'declarations-2026-11-16.json';opts=[]
    if p.exists() and s['day']=='2026-11-16':
        doc=json.loads(p.read_text());rows=[h for h in doc['runners'] if ck(h['course'])==ck(s['course']) and h['race_time'][11:16]==s['off']]
        if rows:opts.append(rows)
    p=root/'stage178-history-evidence'/(s['day']+'.json')
    if p.exists():
        for e in json.loads(p.read_text()).get('races',[]):
            k=e.get('key',[]);rr=e.get('declared_field',[])
            if len(k)==3 and ck(k[1])==ck(s['course']) and k[2]==s['off'] and rr and e.get('source_commit') and e.get('source_asof'):
                try:
                    if D.datetime.fromisoformat(e['source_asof'].replace('Z','+00:00'))>=D.datetime.fromisoformat(rr[0]['race_time'].replace('Z','+00:00')):continue
                except (ValueError,KeyError,TypeError):continue
                opts.append(rr)
    out=[]
    for rr in opts:
        by={n(h['horse']):h for h in rr if n(h['horse']) not in s['nr']}
        if len(by)==len([h for h in rr if n(h['horse']) not in s['nr']]) and set(by)==set(s['states']) and all(h.get('horse_id') and h.get('sire') and h.get('dam') for h in by.values()):out.append(by)
    if not out:return {}
    sig=lambda z:sorted((k,h['horse_id'],n(h['sire']),n(h['dam'])) for k,h in z.items())
    assert len({digest(sig(z)) for z in out})==1,'Conflicting archived declarations'
    return out[0]

def build(s,rid,groups,meta,root):
    cards=cards_for(root,s)
    by=src_field(groups,rid,s) if rid in groups else None
    if by is None and not cards:return None,'NO_COMPLETE_SOURCE_OR_PINNED_DECLARATION'
    if by is None:
        if not rid.startswith('stage203e:'):return None,'SOURCE_FIELD_NOT_CORROBORATED'
        by={k:[] for k in s['states']}
    # Source rows only if their complete field and outcomes agree with two published results.
    # Public facts fill explicitly verified metadata, not speculative values.
    race=[rid,s['day'],s['course'],s['type'],s['surface'],s['dist'],s['going'],s['cl'],'STAGE203E_PUBLIC_CORROBORATED']
    if rid in meta:
        r,conf=V.merge_values(meta[rid],ignore=(3,4,5,6,7,9))
        if conf or ck(r[2])!=ck(s['course']):return None,'SOURCE_RACE_IDENTITY_CONFLICT'
        race[8]=r[8];race[7]=s['cl'] or r[7]
    rr=[]
    for k,vs in sorted(by.items()):
        card=cards.get(k,{});pub=PARENTS.get(k)
        if vs:
            x,diff=V.merge_values(vs,ignore=(7,8,13,17,18))
            if diff:return None,'SOURCE_ROW_CONFLICT:'+k+':'+str(diff)
            # Preserve all genuinely present data. If a parent is conflicting, only the documented Girl Of Gold alias is automatic.
            for z in vs:
                for i,f in ((17,'sire'),(18,'dam')):
                    old=n(z[i]);new=n(card.get(f)) or (n(pub[i-17]) if pub else '')
                    if old and new and old!=new and not(k==n('Girl Of Gold') and i==18 and {old,new}=={'SURREALISM','SURREALISMI'}):return None,'PARENT_CONFLICT:'+k+':'+f
            for i,f in ((17,'sire'),(18,'dam')):
                val=card.get(f) or (pub[i-17] if pub else None)
                if val:x[i]=val
                elif not x[i]:x[i]=next((z[i] for z in vs if z[i]),None)
            if card:
                # Verified historical card ID; retain immutable source row identity in original data.
                x[2]=card['horse_id'];x[1]='id:'+str(x[2]);x[3]=card['horse'];x[4]=I.normname(x[3])
        else:
            x=[rid,'id:'+card['horse_id'],card['horse_id'],card['horse'],I.normname(card['horse']),card.get('country'),None,None,None,None,None,I.number(card.get('ofr')),None,None,I.number(card.get('draw')),I.number(card.get('age')),card.get('sex'),card['sire'],card['dam'],None,None,None,None]
        x[0]=rid;x[7],x[8]=tuple(s['states'][k])[1],tuple(s['states'][k])[0]
        if k in CONNECTIONS:
            t,j=CONNECTIONS[k]
            # Connections from published result, not pre-off assumptions.
            if not x[9] or not vs:x[9]=t
            if not x[10] or not vs:x[10]=j
        price=public_price(s,k)
        if price is not None:x[13]=price
        if not all(x[i] for i in (9,10,17,18)):return None,'MISSING_VERIFIED_RUNNER_METADATA:'+k
        # A pedigree pair is the existing model's primary identity; absent source ID is not fabricated.
        if not x[2]:x[1]='name:'+I.normname(x[3])
        rr.append(x)
    if len({x[1] for x in rr})!=len(rr):return None,'DUPLICATE_RUNNER_KEY'
    raw=rid.rsplit(':',1)[-1] if rid in groups else next(iter(cards.values()))['race_id']
    return (race,sorted(rr,key=lambda x:x[1]),s['title'],raw),'VERIFIED'

def row_fingerprint(row):return digest(list(row))
def apply_fixes(c,fixes,target):
    report=[]
    for f in fixes:
        if f['date']>=target:continue
        row=c.execute('select u.horse_name,u.horse_source_id,u.sire,u.dam,u.finish_pos,u.outcome,r.course,r.race_date from historical_runs u join historical_races r on r.id=u.race_id where u.race_id=? and u.runner_key=?',(f['race_id'],f['runner_key'])).fetchone()
        if not row:report.append('MISSING_EXISTING_ROW');continue
        if n(row[2])==n(f['sire']) and n(row[3])==n(f['dam']):report.append('ALREADY_PRESENT');continue
        assert row_fingerprint(row)==f['before_sha256'],('Identity source changed',f['race_id'])
        c.execute('update historical_runs set sire=?,dam=? where race_id=? and runner_key=?',(f['sire'],f['dam'],f['race_id'],f['runner_key']));assert c.execute('select changes()').fetchone()[0]==1;report.append('IDENTITY_REPAIRED')
    return report

def collect_fixes(c,target):
    out=[]
    for horse,events in CAREER.items():
        sire,dam,url=PARENTS[n(horse)]
        matches=[]
        for day,course,pos in events:
            if day>=target:continue
            st=('FINISHED',pos) if isinstance(pos,int) else (pos,None)
            rows=c.execute('select u.horse_name,u.horse_source_id,u.sire,u.dam,u.finish_pos,u.outcome,r.course,r.race_date,r.id,u.runner_key from historical_runs u join historical_races r on r.id=u.race_id where r.race_date=?',(day,)).fetchall()
            exact=[r for r in rows if n(r[0])==n(horse) and ck(r[6])==ck(course) and S.state({'position':r[4],'status':r[5]})==st]
            if len(exact)==1:matches.append(exact[0])
        # Require a repeated stable source identity or exact pedigree anchor across independently listed career events.
        ids=C.Counter(r[1] for r in matches if r[1]); stable={k for k,v in ids.items() if v>=2};
        for r in matches:
            if n(r[2])==n(sire) and n(r[3])==n(dam):continue
            compatible=not any(a and b and n(a)!=n(b) for a,b in ((r[2],sire),(r[3],dam)))
            if not compatible and r[1] not in stable:continue
            if not (r[1] in stable or compatible and (n(r[2])==n(sire) or n(r[3])==n(dam) or len(matches)>=2)):continue
            out.append(dict(race_id=r[8],runner_key=r[9],date=r[7],horse=horse,sire=sire,dam=dam,before_sha256=row_fingerprint(r[:8]),source=url))
    return out

def append(db,ref,src,target,root=ROOT):
    entries={};fixes={}
    for p in sorted((root/'stage203e-history').glob('approved-*.json')):
        doc=json.loads(p.read_text());assert doc['schema']=='stage203e-v1'
        for e in doc['races']:
            if e['date']<target:entries[e['id']]=e
        for f in doc['identity_fixes']:
            if f['date']<target:fixes[(f['race_id'],f['runner_key'])]=f
    if not entries and not fixes:return []
    groups,meta=V.load_source(src,{e['date'] for e in entries.values()});fields,ids=V.refs(ref);adds=[];report=[]
    with sqlite3.connect(db) as c:
        c.execute('pragma foreign_keys=on');report.extend(apply_fixes(c,list(fixes.values()),target))
        for e in sorted(entries.values(),key=lambda x:x['id']):
            s=RACES[e['spec']];data,why=build(s,e['id'],groups,meta,root);assert data and digest(data)==e['sha256'],(e['id'],why)
            status,ar=V.apply_one(c,data,ids);report.append(status)
            if ar:adds.append(ar)
        assert c.execute('pragma quick_check').fetchone()[0]=='ok' and c.execute('select max(race_date) from historical_races').fetchone()[0]<target;c.commit()
    V.write_refs(ref,fields,adds);print('STAGE203E_SHARED',dict(C.Counter(report)),flush=True);return report

def run():
    import kagglehub
    from stage167_validated_history import refreshed_inputs
    target=os.getenv('ASKED_DATE','').strip() or '2026-11-26';assert target=='2026-11-26','This is a specifically verified nine-case repair'
    out=P.Path('reports/stage203e/output');out.mkdir(parents=True,exist_ok=True);LEDGER.mkdir(parents=True,exist_ok=True)
    # Recover already-owned pre-off declaration snapshot, pinned to the same Stage178 source commit.
    p=LEDGER/'declarations-2026-11-16.json';doc=json.loads(subprocess.check_output(['git','show','ce3bbb19152ba996d196109277fc4a46456c5d90:data/racecards.json'],text=True));assert doc['collected_at']<'2026-11-16'
    decl=[h for h in doc['runners'] if h['date']=='2026-11-16' and any(s['day']=='2026-11-16' and ck(h['course'])==ck(s['course']) and h['race_time'][11:16]==s['off'] for s in RACES)]
    assert decl;V.dump(p,{'source_commit':'ce3bbb19152ba996d196109277fc4a46456c5d90','source_asof':doc['collected_at'],'runners':decl})
    rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r['date']==target and eligibility(r)=='ACTIVE'];assert rows
    cards={r['horse_id']:r for r in rows};assert all(x in cards for x in EXPECTED_IDS.values()),'Original target cards not available'
    for horse,hid in EXPECTED_IDS.items():
        if n(horse) in PARENTS:
            sire,dam,_=PARENTS[n(horse)]
            if n(cards[hid]['sire'])!=n(sire) or n(cards[hid]['dam'])!=n(dam):print('TARGET_PROFILE_DISAGREEMENT',horse,flush=True)
    src=P.Path(kagglehub.dataset_download('deltaromeo/horse-racing-results-ukireland-2015-2025/versions/132'));refs=list(P.Path('reports/stage203e/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(refs)==1
    base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite')
    with refreshed_inputs(base,refs[0],target) as (db,ref):
        hits,other=E.scan(db,rows,target);before={x['horse_id']:x for x in E.classify(rows,hits,other,target)}
        groups,meta=V.load_source(src,{s['day'] for s in RACES});fields,ids=V.refs(ref);adds=[];entries=[];decisions=[]
        with sqlite3.connect(db) as c:
            c.execute('pragma foreign_keys=on');fixes=collect_fixes(c,target);fixresult=apply_fixes(c,fixes,target)
            for i,s in enumerate(RACES):
                candidate=[];errors=[]
                for rid in groups:
                    if meta[rid][0][1]!=s['day'] or ck(meta[rid][0][2])!=ck(s['course']):continue
                    if src_field(groups,rid,s) is None:continue
                    data,why=build(s,rid,groups,meta,ROOT)
                    if data:candidate.append((rid,data))
                    else:errors.append([rid,why])
                if not candidate:
                    rid='stage203e:'+s['day']+':'+digest([s['course'],s['off']])[:24];data,why=build(s,rid,groups,meta,ROOT)
                    if data:candidate.append((rid,data))
                    else:errors.append([rid,why])
                status='NO_UNIQUE_VERIFIED_FIELD';rid=None
                if len(candidate)==1:
                    rid,data=candidate[0]
                    # Every target's actual model pedigree must agree before promotion.
                    if any(not any(E.exact(cards[EXPECTED_IDS[h]],V.aid(x)) for x in data[1] if n(x[3])==n(h)) for h in s['targets']):status='TARGET_IDENTITY_MISMATCH'
                    else:
                        status,ar=V.apply_one(c,data,ids)
                        if ar:
                            adds.append(ar);entries.append(dict(id=rid,date=s['day'],spec=i,sha256=digest(data),sources=s['urls']))
                            again,_=V.apply_one(c,data,ids);assert again=='ALREADY_PRESENT',(rid,again)
                decisions.append(dict(date=s['day'],course=s['course'],off=s['off'],targets=s['targets'],status=status,id=rid,errors=errors,sources=s['urls']))
            assert c.execute('pragma quick_check').fetchone()[0]=='ok' and c.execute('select max(race_date) from historical_races').fetchone()[0]<target;c.commit()
        V.write_refs(ref,fields,adds);h2,o2=E.scan(db,rows,target);after={x['horse_id']:x for x in E.classify(rows,h2,o2,target)};assert all(after[k]['starts']>=before[k]['starts'] for k in before)
        report=[dict(horse=h,before=before[hid],after=after[hid],status='POINT_TO_POINT' if h=='Mambo Du Large' else 'RECOVERED' if after[hid]['last_run_matched'] else 'UNRESOLVED') for h,hid in EXPECTED_IDS.items()]
        # Idempotence for identity repairs as well as race additions.
        with sqlite3.connect(db) as c:assert all(x=='ALREADY_PRESENT' for x in apply_fixes(c,fixes,target))
        path=LEDGER/('approved-'+target+'.json');prior=json.loads(path.read_text()) if path.exists() else {'races':[],'identity_fixes':[]};er={x['id']:x for x in prior['races']};er.update({x['id']:x for x in entries});fr={(x['race_id'],x['runner_key']):x for x in prior['identity_fixes']};fr.update({(x['race_id'],x['runner_key']):x for x in fixes});V.dump(path,dict(schema='stage203e-v1',source_version=132,races=list(er.values()),identity_fixes=list(fr.values()),point_to_point=OUTSIDE))
        summary=dict(target=target,active=len(rows),new_races=len(entries),identity_rows_repaired=fixresult.count('IDENTITY_REPAIRED'),status_counts=dict(C.Counter(x['status'] for x in report)),raw_last_run_gaps=sum(x['last_run_matched'] is False or x['known_no_history'] for x in after.values()),idempotence_passed=True,note='Public results and profiles independently corroborated. Only source/archived evidence promoted; no point-to-point as Rules, no synthetic missing ratings or weights, no model changes.')
        V.dump(out/'summary.json',summary);V.dump(out/'horse-review.json',report);V.dump(out/'race-decisions.json',decisions);V.dump(out/'identity-fixes.json',fixes);print(json.dumps(summary,indent=2),flush=True);print(json.dumps(decisions,indent=2),flush=True)
        P.Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('## Stage203E final nine\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
if __name__=='__main__':run()
