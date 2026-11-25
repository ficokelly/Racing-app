"""Reconcile prior-day complete verified fields; never guess outcomes or overwrite history."""
import collections as C, csv, datetime as D, hashlib, json, pathlib as P, re, sqlite3
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
from scripts import stage171_recent_history as M
from scripts import stage32_form_rebuild as F
from scripts.rhp_runner_eligibility import eligibility
BASE='2026-11-20'
SOURCE='STAGE201_VERIFIED_FINAL_AND_DECLARATION_IDENTITY'
LEDGER='stage201-history'
def read(p): return json.loads(p.read_text())
def norm(s): return F.horse_key(s)
def course(s):
    s=F.course_key(s)
    return {'kemptonpark':'kempton','bangor':'bangorondee','chelmsfordcity':'chelmsford'}.get(s,s)
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def state(h):
    s=M.norm(h.get('status') or h.get('outcome')); p=h.get('position',h.get('finish_pos')); t=M.norm(p)
    if s in M.NR or t in M.NR: return ('NR',None) if p in (None,'') or t in M.NR else ('UNKNOWN',None)
    if s in M.TERMINAL or t in M.TERMINAL:
        return (M.TERMINAL.get(s) or M.TERMINAL[t],None) if p in (None,'') or t in M.TERMINAL else ('UNKNOWN',None)
    if not isinstance(p,bool) and re.fullmatch(r'[0-9]+(?:\.0+)?',str(p)) and float(p)>0:return ('FINISHED',int(float(p)))
    return ('UNKNOWN',None)
def trusted(r):
    if r.get('status')!='final':return False
    return M.verified_source(r) or str(r.get('source') or '').casefold()=='verified puntersedge final results' or r.get('result_classification_source')=='stage71_archive_only_validated'
def signature(r):return sorted((norm(h.get('horse')), *state(h)) for h in r.get('runners',[]))
def final_field(r,day):
    if not trusted(r) or str(r.get('date'))[:10]!=day or M.off(r) is None or R.region(r.get('course')) not in ('GB','IRELAND'):return None
    rr=r.get('runners') or []; names=[norm(h.get('horse')) for h in rr]; sts=[state(h) for h in rr]
    if not names or not all(names) or len(set(names))!=len(names) or any(s=='UNKNOWN' for s,p in sts):return None
    starters=[h for h in rr if state(h)[0]!='NR']; positions=sorted(p for s,p in sts if p is not None)
    if not starters or not positions or positions!=list(range(1,len(positions)+1)):return None
    return starters
META=('race_name','type','surface','distance_f','going','race_class')
FIELDS=('horse_id','sire','dam','trainer','jockey','ofr','lbs','draw','age','sex')
def card_signature(rows):
    return tuple(sorted((norm(h['horse']),str(h.get('horse_id') or ''),norm(h.get('sire')),norm(h.get('dam'))) for h in rows))
def build(root,target):
    ledger=root/LEDGER;ledger.mkdir(exist_ok=True)
    groups=C.defaultdict(list); reports=[]
    # Historical racecard snapshots are declarations, not verified actual jockey/weight.
    for p in [root/'racecards-archive.json',root/'racecards.json']:
        if not p.exists():continue
        by=C.defaultdict(list)
        for h in M.rows(read(p)):
            day=str(h.get('date') or '')[:10]; rid=h.get('race_id'); off=M.off(h)
            if BASE<day<target and rid and off is not None:by[(day,course(h['course']),off,str(rid))].append(h)
        for (d,c,t,rid),rr in by.items():
            # Multiple rows for same horse may be archived versions; reject inconsistent material fields.
            b=C.defaultdict(list)
            for h in rr:b[norm(h['horse'])].append(h)
            if any(len({tuple(str(h.get(k) or '') for k in (*META,*FIELDS)) for h in vv})!=1 for vv in b.values()):continue
            groups[d,c,t].append((rid,[v[0] for v in b.values()],set(),str(p.name)))
    # Dated evidence contains pinned source commits and prior-decision declarations.
    for p in sorted((root/'stage178-history-evidence').glob('????-??-??.json')):
        day=p.stem
        if not BASE<day<target:continue
        for e in read(p).get('races',[]):
            rr=e.get('declared_field') or []; k=e.get('key') or []
            # Stage178 stores venue on its race key, not every runner.
            if len(k)==3: rr=[{**h,'course':h.get('course') or k[1],'date':h.get('date') or k[0]} for h in rr]
            if len(k)!=3 or not rr or not e.get('source_commit') or not e.get('source_asof'):continue
            try:
                observed=D.datetime.fromisoformat(e['source_asof'].replace('Z','+00:00'))
                scheduled=D.datetime.fromisoformat(day+'T'+k[2]+':00+00:00')
                if observed>=scheduled:continue
            except (ValueError,TypeError):continue
            rid=str(rr[0].get('race_id') or '')
            if not rid or any(str(h.get('race_id') or '')!=rid or str(h.get('date') or day)[:10]!=day or course(h.get('course'))!=course(k[1]) or M.off(h)!=M.off({'off':k[2]}) for h in rr):continue
            excluded=set(norm(h.get('horse') if isinstance(h,dict) else h) for h in (e.get('settlement') or {}).get('declared_nonrunners',[]))
            groups[day,course(k[1]),M.off({'off':k[2]})].append((rid,rr,excluded,'stage178:'+e['source_commit']))
    for p in sorted((root/'racing-results').glob('????-??-??.json')):
        day=p.stem
        if not BASE<day<target:continue
        obj=read(p);archive=obj.get('results',[]); accepted=[]
        for r in archive:
            if r.get('status')!='final':continue
            label=f"{day}|{course(r.get('course'))}|{M.off(r)}";starters=final_field(r,day)
            status='HELD_UNVERIFIED_SOURCE_OR_OUTCOME'; candidate=None
            if starters is not None:
                actual={norm(h['horse']):h for h in starters}; all_result={norm(h['horse']) for h in r['runners']}; opts=[]
                for rid,rr,excluded,source in groups.get((day,course(r['course']),M.off(r)),[]):
                    by={norm(h['horse']):h for h in rr}
                    if len(by)!=len(rr) or not set(actual)<=set(by):continue
                    if any(n not in all_result and n not in excluded and eligibility(h)=='ACTIVE' for n,h in by.items() if n not in actual):continue
                    sel=[by[n] for n in actual]; first=sel[0]
                    if not all(all(h.get(k) for k in ('horse_id','sire','dam','trainer','jockey')) for h in sel):continue
                    if len({h['horse_id'] for h in sel})!=len(sel):continue
                    if len({tuple(str(h.get(k) or '') for k in META) for h in sel})!=1:continue
                    if not all(first.get(k) for k in ('race_name','type','surface','distance_f','going')):continue
                    dist=I.number(first['distance_f'])
                    if not dist or not 0<dist<50:continue
                    opts.append((rid,sel,source))
                # A unique race ID and complete identical identities are essential.
                if opts and len({x[0] for x in opts})==1 and len({card_signature(x[1]) for x in opts})==1:
                    # Prefer independently pinned preoff evidence, then current/archived declarations.
                    rid,sel,source=sorted(opts,key=lambda x:(not x[2].startswith('stage178:'),x[2]))[0]
                    first=sel[0]; runners=[]
                    for h in sel:
                        a=actual[norm(h['horse'])]; st,pos=state(a)
                        runners.append(dict(horse=a['horse'],position=pos,outcome=st,sp_decimal=M.sp_decimal(a.get('sp')),card={**{k:h.get(k) for k in FIELDS},'trainer':a.get('trainer') or h['trainer'],'jockey':a.get('jockey') or h['jockey']},race_comment=a.get('comment') or a.get('race_comment'),overall_beaten=a.get('overall_beaten')))
                    candidate=dict(date=day,course=first['course'],off=M.off(r),racecard_id=rid,archive_sha256=digest(r),source=source,race_metadata={k:first.get(k) for k in META},runners=runners)
                    accepted.append(candidate);status='CANDIDATE'
                else:status='HELD_NO_UNIQUE_COMPLETE_FIELD' if not opts else 'HELD_CONFLICTING_IDENTITY'
            reports.append(dict(date=day,course=r.get('course'),off=r.get('off_dt'),status=status,starters=len(starters) if starters is not None else None))
        # Preserve prior published evidence only when current source hash still matches; never silently replace.
        out=ledger/p.name; hashes={digest(r) for r in archive if final_field(r,day) is not None}
        old=read(out).get('races',[]) if out.exists() else []
        pool={x['archive_sha256']:x for x in old if x['archive_sha256'] in hashes}
        for x in accepted:pool.setdefault(x['archive_sha256'],x)
        names=C.Counter(norm(h['horse']) for x in pool.values() for h in x['runners']); ids=C.Counter(x['racecard_id'] for x in pool.values())
        good=[x for x in pool.values() if ids[x['racecard_id']]==1 and all(names[norm(h['horse'])]==1 for h in x['runners'])]
        if good or out.exists():out.write_text(json.dumps({'schema':'stage201-v1','date':day,'races':sorted(good,key=lambda x:(x['off'],x['course']))},sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n')
    return reports

def db_index(c,target):
    by=C.defaultdict(list); fields=C.defaultdict(set); person=C.defaultdict(set)
    for rid,d,co,h,hid,sire,dam,pos,st in c.execute('select r.id,r.race_date,r.course,u.horse_name,u.horse_source_id,u.sire,u.dam,u.finish_pos,u.outcome from historical_races r join historical_runs u on r.id=u.race_id where r.race_date>? and r.race_date<?',(BASE,target)):
        s,p=state({'position':pos,'status':st})
        if s=='NR':continue
        k=(d,course(co),norm(h)); by[k].append((rid,s,p,hid,norm(sire),norm(dam)));fields[rid].add(k);person[d,norm(h)].add(rid)
    return by,fields,person

def coverage(db,target,root=P.Path('data')):
    with sqlite3.connect(db) as c:by,_,_=db_index(c,target)
    out=[]
    for p in sorted((root/'racing-results').glob('????-??-??.json')):
        if not BASE<p.stem<target:continue
        counts=C.Counter()
        for r in read(p).get('results',[]):
            if r.get('status')!='final':continue
            for h in r.get('runners',[]):
                s,pos=state(h)
                if s=='NR':continue
                counts['archive_rows']+=1;k=(p.stem,course(r.get('course')),norm(h.get('horse')));v=by.get(k,[])
                if not v:counts['missing']+=1
                elif len(v)!=1:counts['ambiguous']+=1
                elif s=='UNKNOWN':counts['matched_unknown_outcome']+=1
                elif v[0][1:3]==(s,pos):counts['matched_outcome']+=1
                else:counts['conflicting_outcome']+=1
        out.append({'date':p.stem,**dict(counts)})
    return out

def append(db,reference,target,root=P.Path('data')):
    root=P.Path(root);added=[];report=[]
    with P.Path(reference).open(encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f); columns=rd.fieldnames;refids={x['stage2_race_id'] for x in rd}
    assert {'stage2_race_id','date','course','race_type','race_name','region_scope'}<=set(columns)
    with sqlite3.connect(db) as c:
        c.execute('PRAGMA foreign_keys=ON');assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        by,fields,person=db_index(c,target)
        for p in sorted((root/LEDGER).glob('????-??-??.json')):
            day=p.stem
            if not BASE<day<target:continue
            doc=read(p);assert doc['schema']=='stage201-v1' and doc['date']==day
            archive=M.load_day(root,day); hashes=C.defaultdict(list)
            for r in archive:hashes[digest(r)].append(r)
            for x in doc['races']:
                status='HELD_STALE_OR_INVALID'; rr=x['runners'];key=(day,course(x['course']),x['off']);matches=hashes[x['archive_sha256']]
                if len(matches)!=1 or x['date']!=day:report.append({'race':key,'status':status});continue
                source=matches[0]; starters=final_field(source,day)
                if starters is None or key!=(day,course(source['course']),M.off(source)) or sorted((norm(h['horse']),*state(h)) for h in starters)!=sorted((norm(h['horse']),h['outcome'],h['position']) for h in rr):report.append({'race':key,'status':status});continue
                ks=[(day,key[1],norm(h['horse'])) for h in rr]; occupied=[k for k in ks if by.get(k)]; meta=x['race_metadata'];dist=I.number(meta.get('distance_f'))
                if occupied:
                    rids={v[0] for k in occupied for v in by[k]};exact=len(rids)==1 and len(occupied)==len(ks) and fields[next(iter(rids))]==set(ks) and all(len(by[k])==1 and by[k][0][1:3]==(h['outcome'],h['position']) and by[k][0][4:]==(norm(h['card']['sire']),norm(h['card']['dam'])) for k,h in zip(ks,rr))
                    report.append({'race':key,'status':'ALREADY_PRESENT' if exact else 'HELD_OVERLAP_OR_CONFLICT','starters':len(ks)});continue
                if any(person.get((day,k[2])) for k in ks):report.append({'race':key,'status':'HELD_CROSS_COURSE_IDENTITY'});continue
                if len(set(ks))!=len(ks) or not all(all(h['card'].get(k) for k in ('horse_id','sire','dam','trainer','jockey')) for h in rr) or len({h['card']['horse_id'] for h in rr})!=len(rr) or not dist or not 0<dist<50 or not all(meta.get(k) for k in ('race_name','type','surface','going')):report.append({'race':key,'status':'HELD_METADATA'});continue
                rid='stage201:'+day+':'+hashlib.sha256((key[1]+'|'+str(key[2])+'|'+x['racecard_id']).encode()).hexdigest()[:24]
                if rid in refids or c.execute('select 1 from historical_races where id=?',(rid,)).fetchone():report.append({'race':key,'status':'HELD_ID_COLLISION'});continue
                c.execute('insert into historical_races values(?,?,?,?,?,?,?,?,?)',(rid,day,x['course'],meta['type'],meta['surface'],dist,meta['going'],meta.get('race_class'),SOURCE))
                for k,h in zip(ks,rr):
                    a=h['card']; c.execute('insert into historical_runs values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,'id:'+str(a['horse_id']),str(a['horse_id']),h['horse'],I.normname(h['horse']),None,None,h['position'],h['outcome'],a['trainer'],a['jockey'],I.number(a.get('ofr')),I.weight_lbs(a.get('lbs')),h.get('sp_decimal'),I.number(a.get('draw')),I.number(a.get('age')),a.get('sex'),a['sire'],a['dam'],None,None,h.get('race_comment'),h.get('overall_beaten')))
                    by[k].append((rid,h['outcome'],h['position'],a['horse_id'],norm(a['sire']),norm(a['dam'])));fields[rid].add(k);person[day,k[2]].add(rid)
                added.append(dict(stage2_race_id=rid,date=day,course=x['course'],region_scope=R.region(x['course']),race_type=meta['type'],race_name=meta['race_name'],source_race_id=x['racecard_id']));refids.add(rid);report.append({'race':key,'status':'ADDED','starters':len(ks)})
        c.execute('insert or ignore into data_provenance values(?,?,?,?,?)',(SOURCE,'Verified final app results and unique declared horse identities','Published repository evidence',D.datetime.now(D.timezone.utc).isoformat(),'Actual positions; other metadata from declarations if not present in results; no imputed finishes or margins'))
        assert c.execute('pragma quick_check').fetchone()[0]=='ok' and c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        c.commit()
    if added:
        with P.Path(reference).open('a',encoding='utf8',newline='') as f:csv.DictWriter(f,fieldnames=columns,extrasaction='ignore').writerows(added)
    print('STAGE201_OVERLAY',json.dumps(dict(C.Counter(r['status'] for r in report))),flush=True)
    return report

def selftest():
    assert course('Dundalk (AW)')==course('Dundalk') and course('Chelmsford City (AW)')==course('Chelmsford')
    assert state({'position':None,'status':'ran'})==('UNKNOWN',None)
    assert state({'position':None,'status':'refused'})==('REF',None)
    assert state({'position':None,'status':'scratched'})==('NR',None)
    assert state({'position':1,'status':'ran'})==('FINISHED',1)
    assert state({'position':1,'status':'scratched'})[0]=='UNKNOWN'
