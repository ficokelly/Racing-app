"""Validated, prior-date backfill of complete races into the private model database.
Never replace existing rows, infer results, change frozen model rules, or publish raw Kaggle records.
"""
import collections as C,csv,datetime as D,hashlib,json,pathlib as P,sqlite3,tempfile
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
from scripts import stage32_form_rebuild as F
from scripts import stage171_recent_history as M
from scripts import audit_kaggle_form as A

def read(p):return json.loads(P.Path(p).read_text())
def key(day,co,h):return (day,F.course_key(co),F.horse_key(h))
def finish(pos,st):return A.outcome(pos,st)
def append(db,ref,source,target,root=P.Path('data')):
    root=P.Path(root)
    # Fixed approval from the verified V6 report; a new release cannot silently alter this historical repair.
    approved=dict(zip((f'2026-11-{n:02d}' for n in range(11,21)),(202,192,205,298,235,151,258,325,346,373)))
    days=sorted(d for d in approved if d<target)
    # Recover the entire pinned V132 approved date window, not only the API sample.
    kdays={}; names={}; counts=C.Counter(); details=[]; added=[]
    if days:
        kdays,issues,_,_,_=A.fetch_rows(source,min(days),max(days))
        assert all(len(kdays[d])==approved[d] for d in days), 'Pinned Kaggle approved field totals changed'
        assert all(not any(k not in ('excluded_explicit_foreign_course','excluded_redundant_dash_row','excluded_verified_nonrunner','excluded_verified_duplicate_declaration') for k in issues[d]) for d in days), 'Pinned approved dates now contain unresolved source records'
        for file,opener in I.source_iter(source):
            with opener() as f:
                rd=csv.DictReader(f); m=I.mapping(rd.fieldnames or [])
                if not all(m.get(k) for k in ('race_date','course','race_id')):continue
                for row in rd:
                    d=I.value(row,m,'race_date')[:10]
                    if d not in days:continue
                    co=I.value(row,m,'course');rid=I.value(row,m,'race_id'); title=str(row.get('race_name') or '').strip()
                    if title:names.setdefault((d,F.course_key(co),rid),set()).add(title)
    with P.Path(ref).open(encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f);fields=rd.fieldnames;refids={x['stage2_race_id'] for x in rd}
    assert {'stage2_race_id','date','region_scope','course','race_type','race_name'}<=set(fields)
    with sqlite3.connect(db) as c,tempfile.TemporaryDirectory(prefix='stage196-') as tmp:
        c.execute('pragma foreign_keys=on')
        assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        slots=C.defaultdict(list); racefields=C.defaultdict(set)
        floor=min(days+['2026-11-21'])
        for rid,d,co,h,p,st in c.execute('select r.id,r.race_date,r.course,h.horse_name,h.finish_pos,h.outcome from historical_races r join historical_runs h on r.id=h.race_id where r.race_date>=? and r.race_date<?',(floor,target)):
            o=finish(p,st)
            if o!='NR':
                k=key(d,co,h);slots[k].append((rid,o));racefields[rid].add(k)
        def exact_present(keys,expected):
            return bool(keys and all(len(slots.get(k,[]))==1 and slots[k][0][1]==expected[k] for k in keys) and len({slots[k][0][0] for k in keys})==1 and racefields[slots[keys[0]][0][0]]==set(keys))
        def add(race,rows,title,source_id,origin):
            rid,d,co,typ,surface,dist,going,cls,src=race
            ks=[key(d,co,x[3]) for x in rows]; expected={k:finish(x[7],x[8]) for k,x in zip(ks,rows)}
            if not(d<target and typ and surface and dist and 0<float(dist)<50 and title and R.region(co) in ('GB','IRELAND')) or len(set(ks))!=len(rows) or not all(x[2] and x[17] and x[18] and x[9] and x[10] for x in rows) or 'UNKNOWN' in expected.values() or 'NR' in expected.values() or sum(o==1 for o in expected.values())!=1:
                counts[origin+'_held_metadata_or_outcome']+=1;return False
            occupied=[k for k in ks if slots.get(k)]
            if occupied:
                exact=exact_present(ks,expected)
                counts[origin+('_already_present' if exact else '_held_overlap')]+=1;return exact
            if rid in refids or c.execute('select 1 from historical_races where id=?',(rid,)).fetchone():counts[origin+'_held_id_collision']+=1;return False
            c.execute('insert into historical_races values(?,?,?,?,?,?,?,?,?)',race)
            c.executemany('insert into historical_runs values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
            for k,o in expected.items():slots[k].append((rid,o));racefields[rid].add(k)
            added.append(dict(stage2_race_id=rid,date=d,region_scope=R.region(co),course=co,race_type=typ,race_name=title,source_race_id=source_id));refids.add(rid)
            counts[origin+'_added_races']+=1;counts[origin+'_added_runners']+=len(rows);return True
        if days:
            delta=P.Path(tmp)/'source.sqlite';I.process(source,delta,min(days),max(days),'import')
            with sqlite3.connect(delta) as inc:
                for d in days:
                    groups=C.defaultdict(dict)
                    for (co,h),x in kdays[d].items():groups[(co,str(x['race']))][h]=x['outcome']
                    for (co,raw),expected in groups.items():
                        keys=[(d,co,h) for h in expected]
                        if exact_present(keys,{k:expected[k[2]] for k in keys}):counts['kaggle_already_present']+=1;continue
                        if any(slots.get(k) for k in keys):counts['kaggle_held_partial_or_conflict']+=1;continue
                        matches=[r for r in inc.execute('select * from historical_races where race_date=?',(d,)) if F.course_key(r[2])==co and r[0].endswith(':'+raw)]
                        if len(matches)!=1 or len(names.get((d,co,raw),set()))!=1:counts['kaggle_held_source_identity']+=1;continue
                        race=matches[0];rr=[x for x in inc.execute('select * from historical_runs where race_id=?',(race[0],)) if finish(x[7],x[8])!='NR']
                        actual={F.horse_key(x[3]):finish(x[7],x[8]) for x in rr}
                        if len(actual)!=len(rr) or actual!=expected:counts['kaggle_held_field_mismatch']+=1;continue
                        add(race,rr,next(iter(names[(d,co,raw)])),raw,'kaggle')
        # For recent recovered final results, use actual result names and SP, never invent IDs or pedigree.
        registry=read(root/'form-dated-evidence.json'); cards=C.defaultdict(lambda:C.defaultdict(list))
        for p in (root/'racecards-archive.json',root/'racecards.json'):
            if p.exists():
                for row in M.rows(read(p)):
                    d=str(row['date'])[:10];rid=str(row.get('race_id') or '')
                    if '2026-11-20'<d<target and rid:cards[(d,F.course_key(row['course']),M.off(row))][rid].append(row)
        for ev in registry['final_results']:
            d=ev['date'];co=ev['course'];off=M.off(ev)
            if not '2026-11-20'<d<target:continue
            label=f'{d}|{co}|{off}';matches=[r for r in M.load_day(root,d) if r==ev]
            starters=[h for h in ev['runners'] if F.is_starter(h)];ks=[key(d,co,h['horse']) for h in starters]
            if exact_present(ks,{k:finish(h.get('position'),h.get('status')) for k,h in zip(ks,starters)}):counts['api_already_present']+=1;details.append({'race':label,'status':'ALREADY_PRESENT','starters':len(ks)});continue
            if len(matches)!=1 or any(slots.get(k) for k in ks):counts['api_held_archive_or_overlap']+=1;details.append({'race':label,'status':'HELD_ARCHIVE_OR_OVERLAP'});continue
            assert ev['external_verification']['starters']==len(starters)
            allnames={F.horse_key(h['horse']) for h in ev['runners']}; fields0=('horse_id','sire','dam','race_name','type','surface','distance_f','going','race_class','ofr','lbs','draw','age','sex')
            options=[]
            for rid,group in cards[(d,F.course_key(co),off)].items():
                by=C.defaultdict(list)
                for h in group:by[F.horse_key(h['horse'])].append(h)
                if not allnames<=set(by) or any(not all(M.nonrunner(x) for x in by[h]) for h in set(by)-allnames):continue
                if any(len({tuple(str(x.get(f) or '') for f in fields0) for x in by[h]})!=1 for h in (k[2] for k in ks)):continue
                card={h:rr[0] for h,rr in by.items()};options.append((rid,card))
            if len(options)!=1:counts['api_held_card_match']+=1;details.append({'race':label,'status':'HELD_CARD_MATCH','candidates':len(options)});continue
            _,card=options[0];first=card[ks[0][2]];meta=('race_name','type','surface','distance_f','going','race_class')
            if len({tuple(str(card[k[2]].get(x) or '') for x in meta) for k in ks})!=1:counts['api_held_race_metadata']+=1;continue
            source_id=str(ev['external_verification']['source_race_id']);rid='stage196:'+d+':'+hashlib.sha256((co+source_id).encode()).hexdigest()[:24];src='VERIFIED_APP_RESULT_STAGE196'
            race=(rid,d,co,first.get('type'),first.get('surface'),I.number(first.get('distance_f')),first.get('going'),first.get('race_class'),src)
            rr=[]
            for h,k in zip(starters,ks):
                x=card[k[2]];o=finish(h.get('position'),h.get('status'));pos=o if isinstance(o,int) else None
                st='FINISHED' if pos else M.TERMINAL.get(M.norm(h.get('status'))) or M.TERMINAL.get(M.norm(h.get('position')))
                if not st or not all(x.get(f) for f in ('horse_id','sire','dam')) or not h.get('trainer') or not h.get('jockey'):break
                rr.append((rid,'id:'+str(x['horse_id']),str(x['horse_id']),h['horse'],I.normname(h['horse']),None,None,pos,st,h['trainer'],h['jockey'],I.number(x.get('ofr')),I.weight_lbs(x.get('lbs')),M.sp_decimal(h.get('sp')),I.number(x.get('draw')),I.number(x.get('age')),x.get('sex'),x['sire'],x['dam'],None,None,h.get('comment'),h.get('overall_beaten')))
            if len(rr)!=len(starters) or len({x[2] for x in rr})!=len(rr):counts['api_held_runner_metadata']+=1;details.append({'race':label,'status':'HELD_RUNNER_METADATA'});continue
            ok=add(race,rr,first.get('race_name'),source_id,'api');details.append({'race':label,'status':'BACKFILLED' if ok else 'HELD','starters':len(rr)})
        c.execute('insert or ignore into data_provenance values(?,?,?,?,?)',('VERIFIED_APP_RESULT_STAGE196','Existing dated result archive and verified racecards','Existing published app facts',D.datetime.now(D.timezone.utc).isoformat(),'Actual result position and people; racecard metadata is declaration only.'))
        assert c.execute('pragma quick_check').fetchone()[0]=='ok'
        assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
        c.commit()
        latest=c.execute('select max(race_date) from historical_races').fetchone()[0]
        coverage={}; api_coverage={}
        for p in sorted((root/'racing-results').glob('????-??-??.json')):
            d=p.stem
            if not '2026-11-20'<d<target:continue
            expected={}; duplicate=set()
            for race in read(p).get('results',[]):
                if race.get('status')!='final' or R.region(race.get('course')) not in ('GB','IRELAND'):continue
                for h in race.get('runners',[]):
                    if not F.is_starter(h):continue
                    k=key(d,race['course'],h.get('horse'));o=finish(h.get('position'),h.get('status'))
                    if k in expected:duplicate.add(k)
                    expected[k]=o
            matched=sum(k not in duplicate and o!='UNKNOWN' and len(slots.get(k,[]))==1 and slots[k][0][1]==o for k,o in expected.items())
            missing=sum(not slots.get(k) for k in expected)
            api_coverage[d]={'archive_starters':len(expected),'matched':matched,'missing':missing,'conflicting_or_unknown':len(expected)-matched-missing}
        for d in days:
            missing=[];conflicts=[]
            for (co,h),v in kdays[d].items():
                s=slots.get((d,co,h),[])
                if not s:missing.append((co,h))
                elif len(s)!=1 or s[0][1]!=v['outcome']:conflicts.append((co,h))
            coverage[d]={'expected':len(kdays[d]),'matched':len(kdays[d])-len(missing)-len(conflicts),'missing':len(missing),'conflicts':len(conflicts)}
    if added:
        with P.Path(ref).open('a',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=fields,extrasaction='ignore').writerows(added)
    report={'status':'BACKFILLED_WITH_COVERAGE_AUDIT','target_exclusive':target,'history_through':latest,'counts':dict(counts),'kaggle_coverage':coverage,'api_recovered_races':details,'api_coverage':api_coverage,'note':'Only complete source races added; held rows remain unresolved. Frozen model scores, weights and HPR stores unchanged.'}
    print('STAGE196_BACKFILL',json.dumps(report),flush=True)
    return report
