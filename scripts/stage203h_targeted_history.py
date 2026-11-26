"""Eight scoped November 27 history repairs. Public results corroborate immutable archived identities.
No synthetic finishes, no replacement of existing history, no model formula changes.
"""
import collections as C, datetime as D, hashlib, json, os, pathlib as P, sqlite3, subprocess
from zoneinfo import ZoneInfo
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
from scripts import stage203c_consolidated_history as V, stage203_identity_recovery as E, stage201_recent_history as S, stage171_recent_history as M
from scripts.rhp_runner_eligibility import eligibility
ROOT=P.Path('data'); LEDGER=ROOT/'stage203h-history'; TARGET='2026-11-27'
ARCHIVE_COMMIT='b236f31d012564613d5dd9d9d7553d77a567cee5'
# Each public result row is a factual tuple: horse, finishing position/outcome, SP,
# trainer, jockey, weight in st-lb, OR, draw, total beaten lengths (where verified).
# October races already have rich actual-result metadata in the app archive.
def rich(horses,prices,beaten):
    hs=horses.split('|'); ps=prices.split('|'); bs=beaten.split('|')
    assert len(hs)==len(ps)==len(bs)
    return [[h,i+1,p,None,None,None,None,None,b] for i,(h,p,b) in enumerate(zip(hs,ps,bs))]
def sparse(raw):
    out=[]
    for line in raw.strip().splitlines():
        h,p,sp,t,j,w,o,draw,b=line.split('|')
        out.append([h,int(p) if p.isdigit() else p,sp,t,j,w,o,draw,b])
    return out

def spec(day,venue,off,typ,surface,dist,going,cl,horse,hid,url,rows,pins=(),nr=()):
    return dict(day=day,venue=venue,off=off,type=typ,surface=surface,distance=dist,going=going,race_class=cl,horse=horse,horse_id=hid,url=url,rows=rows,pins=list(pins),nr=list(nr))

RACES=[
 spec('2026-10-09','Newmarket','16:10','Flat','Turf',6,'Good','Class 1','Hollywood Treasure','hrs_51342284','https://www.racingpost.com/results/38/newmarket/2026-10-09/928522/',rich("Ruby's Angel|Zen Diva|Magic Basma|Cherry Baker|Sing The Blues|Lady Youmzain|Midnight Tango|Golden Palace|Dance In The Storm|Isle Of Fernandez|Hollywood Treasure|The Prettiest Star|Rogue Attraction",'16/1|4/1|9/1|150/1|16/1|9/1|22/1|66/1|2/1|50/1|16/1|3/1|66/1','0||2.5|2.75|3|3.75|4.25|5.75|6.25|6.5|6.75|12.25|'),nr=('Forty Years On',)),
 spec('2026-10-19','Gowran Park (IRE)','17:10','Flat','Turf',12,'Soft',None,'Mister Adam','hrs_46697343','https://www.racingpost.com/results/184/gowran-park/2026-10-19/931162/',rich("Lord Aus|No More Hiding|Shay's Dream|Mr Rango|Kimy|Lady Plimsoll|Amplitude|Mister Adam|Love Orchid|Serienmond|Neo Smart|Vantage Code|Victors Spirit|Palace Rock",'13/8|12/1|7/2|50/1|16/1|7/1|10/1|40/1|8/1|28/1|8/1|40/1|150/1|150/1','0||6|6.5|7.75|7.75|9.5|11.5|12.75|14|15.25|26.25|45.25|46.5'),nr=("Prophet's Path",'Saliba','Satono Chevalier')),
 spec('2026-11-16','Wolverhampton (AW)','18:00','Flat','AW',8.5,'Standard','Class 3','Graceful Gracie','hrs_17394842878','https://www.racingpost.com/results/513/wolverhampton-aw/2026-11-16/933466/',sparse('''
Splintered|1|125/1|Jonathan Portman|Olivia Tubb|9-2||8|0
Laurel Crown|2|28/1|Andrew Balding|William Carver|9-7||9|2.75
Kings Medal|3|3/1|John & Thady Gosden|Tyler Heard|9-7||5|3.5
Al Lahbab|4|8/13|Charlie Appleby|Connor Planas|9-7||7|3.75
Vulcanist|5|13/2|Ralph Beckett|Rhys Clutterbuck|9-7||2|6.5
Graceful Gracie|6|125/1|Mark Loughnane|Jonny Peate|9-2||4|7.25
Silent Wisdom|7|40/1|Ed Walker|George Downing|9-7||3|8
Adventus Saxonum|8|9/1|John & Thady Gosden|Luke Catton|9-4||1|8.5
Simply George|9|14/1|Marco Botti|Dougie Costello|9-7||6|9.25
''')),
 spec('2026-11-16','Wolverhampton (AW)','18:30','Flat','AW',5,'Standard','Class 3','Betsen','hrs_35040019','https://www.racingpost.com/results/513/wolverhampton-aw/2026-11-16/931746/',sparse('''
Law Of Average|1|11/1|Chelsea Banham|Joey Haynes|9-4|89|5|0
Fiorella Princess|2|11/1|Robert Cowell|Jack Dace|8-9|85|1|0.75
Tan Rapido|3|7/2|Charlie Fellowes|Mikey Sheehy|8-10|81|9|0.75
Rhythm N Hooves|4|4/1|Robert Cowell|Barry McHugh|9-4|89|7|1.5
El Bufalo|5|16/1|Thomas Faulkner|Zoe Lewis|7-13|77|2|2
Knebworth|6|9/2|Richard Hughes|Donagh Murphy|9-0|90|6|2.25
Almaty Star|7|13/2|Robert Cowell|Connor Planas|8-6|77|4|3.25
Rosenpur|8|18/1|Jennie Candlish|Liam Wright|8-8|82|3|3.5
Atomic Force|9|6/1|Edward Bethell|Ryan Sexton|9-9|94|10|4
Alligator Alley|10|40/1|Ruth Carr|William Pyle|9-0|85|8|4.5
Betsen|11|25/1|Tony Carroll|Myla Coppins|8-6|82|11|5
''')),
 spec('2026-11-21','Lingfield (AW)','15:40','Flat','AW',12,'Standard','Class 6','Off Spin','hrs_44199232','https://www.racingpost.com/results/393/lingfield-aw/2026-11-21/931806/',sparse('''
Adalo|1|10/3|William Haggas|Cieren Fallon|9-6|65|2|0
Brave Leader|2|11/8|James Owen|Luke Morris|9-6|65|4|
Off Spin|3|15/2|Gay Kelleway|Billy Loughnane|9-9|63|8|0.25
Tortured Soul|4|10/1|Tony Carroll|Neil Callan|9-11|65|9|0.75
Somkiyr|4|66/1|Andy Irvine|Rhys Clutterbuck|9-8|62|10|0.75
Perzaaneh|6|80/1|Patrick Chamings|Callum Hutchinson|9-4|58|3|1.75
Raintown|7|8/1|J S Moore|David Egan|9-11|65|5|2.5
Soy Loco|8|100/1|Simon Dow|Paddy Bradley|9-6|60|6|3.25
Little Miss India|9|6/1|James Owen|Marco Ghiani|9-10|64|12|5
Letmebetheboss|10|66/1|Tony Carroll|William Carson|9-5|59|1|7
Masterofperfection|11|28/1|Dr Richard Newland|Gina Mangan|9-10|64|7|13
Liberate|12|80/1|Jim & Suzi Best|Tom Queally|9-11|65|11|22.5
''')),
 spec('2026-11-24','Southwell','12:26','Chase','Turf',24.5,'Good','Class 5','Sledgehammer','hrs_58565304','https://www.racingpost.com/results/61/southwell/2026-11-24/931638/',sparse('''
Sledgehammer|1|3/1|Adrian Paul Keatley|Oakley Brown|11-8|99||0
Peckforton Hills|2|9/2|Oliver Greenall & Josh Guerriero|Jack Tudor|11-5|91||12
Could Be You|3|10/3|Neil Mulholland|Richie McLernon|10-9|81||20
Bells Of Ufford|4|17/2|Mitchell Hunt & Tim Vaughan|David Noonan|11-2|88||20.25
Scoro|5|50/1|L J Morgan|Harry Reed|10-2|74||34.25
Danger Nap|6|8/1|Jonjo & A J O'Neill|Jonjo O'Neill Jr|11-13|99||44.25
Vicomte Le Brave|7|80/1|Cynthia Woods|Rian Corcoran|11-9|100||45
Illico De Montave|PU|80/1|Rosemary Gasson|Ben Poste|10-2|74||
Kastellion|PU|3/1|Mel Rowley|Charlie Hammond|11-13|99||
'''),pins=('81445062e962e81f97d46a66cdc75bca77ab94ef','dbd21c73d63d73ff0d413ee67ab447bbbb118ab1','bdaa99ad7863bd1929396036e7f034e34ec3a0f7')),
 spec('2026-11-24','Southwell','13:56','Hurdle','Turf',16,'Good','Class 5','Nobody Told Me','hrs_31259193','https://www.racingpost.com/results/61/southwell/2026-11-24/931641/',sparse('''
Nobody Told Me|1|13/8|Dan Skelton|Harry Skelton|10-5|74||0
Yourcardismarked|2|7/4|Jennie Candlish|Sean Quinlan|11-7|90||8
Laplander|3|16/1|Tom Ward|Sean Bowen|11-6|89||8.25
Milady Du Breuil|4|66/1|Christian Williams|Jack Tudor|11-7|90||9.5
Gaelic Glow|5|12/1|Sam England|Jonathan England|11-12|95||10.5
Tenadaay|6|40/1|Fergal O'Brien|Kielan Woods|11-3|86||17
Orbys River|7|66/1|Sam Allwood|Toby Wynne|10-13|82||23.5
Troy Story|8|100/1|Dave Roberts|Lee Edwards|11-8|91||25.25
Cheap Sandwiches|9|15/2|Chris Gordon|Freddie Gordon|11-1|84||25.5
Everest|10|16/1|Jamie Moore & Josh Moore|Caoilin Quinn|11-5|88||31.5
Little Lady Lucy|11|25/1|Neil Mulholland|Laurie Gallacher|10-6|85||34.25
Dino Bellagio|F|9/1|Donald McCain|Brian Hughes|11-7|90||
Trackman|PU|150/1|Simon Pearce|Jack Quinlan|11-8|91||
On Subs|PU|80/1|Christian Williams|Adam Wedge|10-11|80||
'''),pins=('81445062e962e81f97d46a66cdc75bca77ab94ef','dbd21c73d63d73ff0d413ee67ab447bbbb118ab1','bdaa99ad7863bd1929396036e7f034e34ec3a0f7')),
 spec('2026-11-26','Market Rasen','13:04','Chase','Turf',23.86818,'Good To Soft','Class 1','Bluey','hrs_34457402','https://www.sportinglife.com/racing/results/2026-11-26/market-rasen/950399/pricedup-boosted-prices-daily-bud-booth-mares-chase-listed-gbb-race',sparse('''
Bluey|1|85/40|Miss E C Lavelle|Harry Cobden|11-4|136||0
Jasmine Bliss|2|14/1|Harry Derham|J J Burke|11-6|||0.5
Ruby Island|3|5/1|N G Richards|Danny McMenamin|11-2|136||
Overabottleofred|4|28/1|T R Gretton|Sean Bowen|11-2|||
Kala Conti|F|10/11|G Elliott|Danny Gilligan|11-8|||
'''),pins=('983beb4aa9beb82715dad736e30d52ff84ca1151','9a16bff4e395000e0bdff04d73a38f5b9ac724df','cefe504da3d5f365fe7057efe2de8478c3a4470d')),
]
# These are scoped prior-run explanations, not fabricated domestic racing history.
EXTERNAL=[
 ('hrs_17393682019','Invino Honnetete','2026-10-08','OVERSEAS','Bordeaux','https://www.journalduturf.com/fiche-cheval/invino_honnetete.html'),
 ('hrs_17394662439','Dshipdatnevrsailed','2026-04-26','POINT_TO_POINT','Dromahane','https://p2p.ie/results.php?fixture_id=5306&race_id=29280'),
 ('hrs_57717947','Guevara','2026-07-05','OVERSEAS','Hamburg','https://www.schiergen.de/news/starter-resultate/'),
 ('hrs_60342933','Northern Man','2026-05-14','OVERSEAS','Dortmund','https://3forone.com/en/network/raceresults/2604519/2026-05-14/DE/preis-der-filialen-horde-und-hochsten?order=desc&orderKey=object_runner.placeDifference'),
 ('hrs_17396103865','Brand New Man','2026-10-10','POINT_TO_POINT','Portrush','https://www.irishracing.com/horse//Brand-New-Man-IRE/864768'),
 ('hrs_52204572','Novak','2026-05-30','OVERSEAS','Auteuil','https://www.racingpost.com/results/205/auteuil/2026-05-30/921926/'),
 ('hrs_51185967','Loughmourne','2026-02-15','POINT_TO_POINT','Nenagh','https://www.tabtouch.com.au/racing/2026-11-27/srp/1'),
 ('hrs_32104240','Military Order','2026-11-13','OVERSEAS','Bahrain','https://www.racingpost.com/results/869/bahrain/2026-11-13/933259/'),
]

def n(x):
    # Some older provider fields carry SAF and other country suffixes.
    # Strip only explicit country codes for full-field matching.
    import re
    return V.n(re.sub(r"\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN|SAF|CAN)\)\s*$", "", str(x or ""), flags=re.I))
def ck(x):return S.course(x)
def minute(t):
    h,m=map(int,t.split(':'));return h*60+m

def state(p):return ('FINISHED',p) if isinstance(p,int) else (p,None)
def value(x):return I.number(x) if x not in ('',None,'-') else None

def lbs(w):
    if '-' in str(w):
        a,b=map(int,w.split('-'));return 14*a+b
    return value(w)

def gitdoc(commit,path):
    return json.loads(subprocess.check_output(['git','show',commit+':'+path],text=True,timeout=60))

def index(rows,nr=()):
    exclude={n(x) for x in nr};out={}
    for h in rows:
        k=n(h.get('horse'))
        if k in exclude:continue
        if not k or k in out:raise ValueError('duplicate or blank horse: '+k)
        out[k]=h
    return out

def public_fields(s):
    rows={n(r[0]):r for r in s['rows']}
    assert len(rows)==len(s['rows']) and n(s['horse']) in rows
    assert all(state(r[1])[0] in ('FINISHED','PU','F','UR','BD','RO','SU','DSQ') for r in rows.values())
    counts=C.Counter(r[1] for r in rows.values() if isinstance(r[1],int));pos=1
    for p,count in sorted(counts.items()):
        assert p==pos,('invalid competition ranking',p,pos)
        pos+=count
    assert counts[1]==1
    return rows

def historical_archive(s,expected):
    doc=gitdoc(ARCHIVE_COMMIT,'data/racing-results/'+s['day']+'.json')
    options=[]
    for race in doc.get('results',[]):
        if str(race.get('date'))[:10]!=s['day'] or ck(race.get('course'))!=ck(s['venue']) or M.off(race)!=minute(s['off']):continue
        field=index(race.get('runners',[]),s['nr'])
        if set(field)!=set(expected):continue
        good=True
        for k,r in expected.items():
            recorded=S.state(field[k]); verified=state(r[1])
            # Fill only a genuinely unknown terminal result from the independently published result.
            if recorded!=verified and not (recorded==('UNKNOWN',None) and verified[0] in ('F','PU','UR','BD','RO','SU')):
                good=False;break
        if good:options.append((race,field))
    if len(options)!=1:raise ValueError('no unique corroborated archive field: '+str(len(options)))
    return options[0]

def identity_field(s,expected,race,archive):
    if all(h.get('horse_id') and h.get('sire') and h.get('dam') for h in archive.values()) and str(race.get('race_id','')).startswith('rac_'):
        return archive,race['race_id'],{'kind':'rich_actual_result','commit':ARCHIVE_COMMIT}
    options=[]
    # Stage178 data has exact recorded collection timestamps and immutable pre-off commit references.
    try: doc=gitdoc(ARCHIVE_COMMIT,'data/stage178-history-evidence/'+s['day']+'.json')
    except (subprocess.CalledProcessError,KeyError):doc={}
    for e in doc.get('races',[]):
        key=e.get('key',[]);rows=e.get('declared_field',[])
        if len(key)!=3 or key[0]!=s['day'] or ck(key[1])!=ck(s['venue']) or key[2]!=s['off'] or not rows or not e.get('source_commit'):continue
        options.append((rows,e.get('source_asof'),e['source_commit']))
    for commit in s['pins']:
        try: d=gitdoc(commit,'data/racecards.json')
        except subprocess.CalledProcessError:continue
        rows=[h for h in d.get('runners',[]) if h.get('date')==s['day'] and ck(h.get('course'))==ck(s['venue']) and M.off(h)==minute(s['off'])]
        if rows:options.append((rows,d.get('collected_at'),commit))
    verified=[]
    for rows,asof,commit in options:
        try:
            t=D.datetime.fromisoformat(str(asof).replace('Z','+00:00'))
            off=D.datetime.combine(D.date.fromisoformat(s['day']),D.time.fromisoformat(s['off']),ZoneInfo('Europe/Dublin'))
            if t.tzinfo is None or t>=off:continue
            # Only explicitly identified nonrunners may be excluded. Active starters must agree exactly.
            field=index(rows,s['nr'])
            if set(field)!=set(expected):continue
            if not all(h.get('horse_id') and h.get('sire') and h.get('dam') and h.get('race_id') for h in field.values()):continue
            ids={h['race_id'] for h in field.values()}
            if len(ids)!=1:continue
            verified.append((field,next(iter(ids)),{'kind':'pinned_preoff_declarations','commit':commit,'asof':asof}))
        except (ValueError,TypeError,KeyError):continue
    if not verified:raise ValueError('no complete pinned preoff identity field')
    def sig(v):return sorted((k,h['horse_id'],E.parent(h['sire']),E.parent(h['dam'])) for k,h in v[0].items())
    if len({V.digest(sig(v)) for v in verified})!=1:raise ValueError('conflicting pinned declarations')
    return verified[0]

def build(s,targetcard):
    expected=public_fields(s); race,archive=historical_archive(s,expected)
    field,raw,evidence=identity_field(s,expected,race,archive)
    h=field[n(s['horse'])]
    if h['horse_id']!=s['horse_id'] or not E.exact(targetcard,h):raise ValueError('target horse identity/pedigree disagreement')
    if not str(raw).startswith('rac_'):raise ValueError('missing source race id')
    rid='stage203:stage203h:'+s['day']+':'+str(raw)
    title=race.get('race_name') or h.get('race_name')
    if not title or R.region(s['venue']) not in ('GB','IRELAND'):raise ValueError('missing race metadata')
    rich=evidence['kind']=='rich_actual_result';runs=[]
    for k,r in sorted(expected.items()):
        name,p,sp,t,j,w,o,draw,b=r;ident=field[k]
        actual=archive[k]
        for parent in ('sire','dam'):
            if actual.get(parent) and E.parent(actual[parent])!=E.parent(ident[parent]):raise ValueError('archive pedigree conflict '+name)
        if actual.get('horse_id') and actual['horse_id']!=ident['horse_id']:raise ValueError('archive id conflict '+name)
        # For sparse archives, actual connections and carried weights come from the public result,
        # not from pre-off declarations. For rich results these come from the archived result.
        t=t or (actual.get('trainer') if rich else None);j=j or (actual.get('jockey') if rich else None)
        w=lbs(w) if w else (I.weight_lbs(actual.get('weight_lbs') or actual.get('weight')) if rich else None)
        o=value(o) if o not in ('',None) else (value(actual.get('or') or actual.get('ofr')) if rich else None)
        dr=value(draw) if draw not in ('',None) else (value(actual.get('draw')) if rich else None)
        if not t or not j or w is None:raise ValueError('missing actual connections or weight '+name)
        st,pos=state(p)
        runs.append([rid,'id:'+ident['horse_id'],ident['horse_id'],ident['horse'],I.normname(ident['horse']),ident.get('country'),None,pos,st,t,j,o,w,M.sp_decimal(sp),dr,value(ident.get('age')),ident.get('sex'),ident['sire'],ident['dam'],None,None,None,value(b)])
    if len({r[1] for r in runs})!=len(runs):raise ValueError('duplicate source IDs')
    data=[[rid,s['day'],s['venue'],s['type'],s['surface'],s['distance'],s['going'],s['race_class'],'STAGE203H_PUBLIC_CORROBORATED'],sorted(runs,key=lambda r:r[1]),title,raw]
    return dict(data=data,data_sha256=V.digest(data),source_url=s['url'],archive_commit=ARCHIVE_COMMIT,archive_sha256=V.digest(race),identity_evidence=evidence,target_horse=s['horse'],target_horse_id=s['horse_id'])

def load_manifest(root=ROOT):
    p=root/'stage203h-history'/'approved-2026-11-27.json'
    if not p.exists():return {'schema':'stage203h-v1','races':[],'external':[]}
    d=json.loads(p.read_text());assert d['schema']=='stage203h-v1'
    for e in d['races']:assert V.digest(e['data'])==e['data_sha256']
    return d

def load_exceptions(root=ROOT):
    # Accept only explicitly scoped, evidence-backed ID/date pairs, not a blanket missing-form exception.
    d=load_manifest(root);allowed={(h,day): (name,kind,venue,url) for h,name,day,kind,venue,url in EXTERNAL}
    out={}
    for e in d['external']:
        key=(e['horse_id'],e['expected']); assert allowed[key]==(e['horse'],e['type'],e['venue'],e['source_url'])
        out[key[0]]=key[1]
    return out

def apply_entries(db,ref,entries,target):
    cols,ids=V.refs(ref);adds=[];report=[]
    with sqlite3.connect(db) as c:
        c.execute('pragma foreign_keys=on');assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        for e in sorted(entries,key=lambda x:x['data'][0][0]):
            data=e['data'];assert V.digest(data)==e['data_sha256']
            if data[0][1]>=target:continue
            status,ar=V.apply_one(c,data,ids)
            if ar:adds.append(ar)
            report.append({'horse':e['target_horse'],'horse_id':e['target_horse_id'],'race':data[0][0],'status':status})
        assert c.execute('pragma quick_check').fetchone()[0]=='ok';assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target;c.commit()
    V.write_refs(ref,cols,adds);return report

def append(db,ref,src,target,root=ROOT):
    report=apply_entries(db,ref,load_manifest(root)['races'],target)
    print('STAGE203H_SHARED_HISTORY',dict(C.Counter(x['status'] for x in report)),flush=True)
    return report

def audit(db,rows):
    a,b=E.scan(db,rows,TARGET);return {x['horse_id']:x for x in E.classify(rows,a,b,TARGET)}

def main():
    from stage167_validated_history import refreshed_inputs
    today=D.datetime.now(ZoneInfo('Europe/Dublin')).date()
    assert D.date.fromisoformat(TARGET) in (today,today+D.timedelta(days=1)), 'Repair is scoped to the 27 November declarations'
    out=P.Path('reports/stage203h/output');out.mkdir(parents=True,exist_ok=True);LEDGER.mkdir(parents=True,exist_ok=True)
    rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r.get('date')==TARGET and eligibility(r)=='ACTIVE']
    cards={r['horse_id']:r for r in rows};assert rows and len(cards)==len(rows)
    refs=list(P.Path('reports/stage203h/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(refs)==1
    base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite')
    manifest=load_manifest();old={e['target_horse_id']:e for e in manifest['races']};external=[];decisions=[]
    with refreshed_inputs(base,refs[0],TARGET) as (db,ref):
        before=audit(db,rows)
        candidates=[]
        for s in RACES:
            hid=s['horse_id'];card=cards.get(hid)
            if not card:decisions.append({'horse':s['horse'],'status':'NOT_ACTIVE'});continue
            if n(card['horse'])!=n(s['horse']) or E.expected(card,TARGET)!=s['day']:
                decisions.append({'horse':s['horse'],'status':'TARGET_CARD_CHANGED'});continue
            if before[hid]['last_run_matched']:
                decisions.append({'horse':s['horse'],'status':'ALREADY_MATCHED'});continue
            try:candidates.append(build(s,card))
            except (ValueError,KeyError,TypeError,AssertionError,subprocess.CalledProcessError) as ex:decisions.append({'horse':s['horse'],'status':'HELD','reason':str(ex)})
        applied=apply_entries(db,ref,candidates,TARGET);decisions.extend(applied);after=audit(db,rows)
        # Never publish a repair unless exact target identity and last-run matching improve, and no existing coverage regresses.
        assert all(after[k]['starts']>=before[k]['starts'] and (before[k]['last_run_matched'] is not True or after[k]['last_run_matched'] is True) for k in before)
        added={x['horse_id'] for x in applied if x['status']=='ADDED'}
        assert all(after[k]['last_run_matched'] is True for k in added), 'Added field did not recover exact target identity'
        approved=[e for e in candidates if e['target_horse_id'] in added]
        again=apply_entries(db,ref,approved,TARGET);assert all(x['status']=='ALREADY_PRESENT' for x in again),'Repair is not idempotent'
        for e in approved:old[e['target_horse_id']]=e
        for hid,name,day,kind,venue,url in EXTERNAL:
            if hid in cards and n(cards[hid]['horse'])==n(name) and E.expected(cards[hid],TARGET)==day:
                external.append(dict(horse_id=hid,horse=name,expected=day,type=kind,venue=venue,source_url=url))
        outside={x['horse_id']:x['expected'] for x in external}
        raw=[x for x in after.values() if x['last_run_matched'] is False or x['known_no_history']]
        unresolved=[x for x in raw if outside.get(x['horse_id'])!=x['expected']]
        summary=dict(target=TARGET,active=len(rows),new_domestic_races=len(approved),new_starters=sum(len(e['data'][1]) for e in approved),verified_external_exceptions=len(external),raw_form_gaps=len(raw),unresolved_domestic=len(unresolved),decisions=decisions,remaining=unresolved,original_records_preserved=True,idempotence_passed=True,hpr_replay_required=True)
        V.dump(LEDGER/'approved-2026-11-27.json',{'schema':'stage203h-v1','races':list(old.values()),'external':external})
        assert len(load_exceptions())==len(external)
        V.dump(out/'summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Stage203H scoped repairs\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')

if __name__=='__main__':main()
