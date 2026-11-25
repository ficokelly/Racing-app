"""Reusable targeted recovery; no raw Kaggle records are published. Rebuild approved races from pinned source + hashed archived facts."""
import collections as C, csv, datetime as D, hashlib, json, os, pathlib as P, re, sqlite3
import import_historical_ukie as I
import stage4_match_all_recurring_races as R
import stage108_model_g2 as G
from scripts import stage201_recent_history as S, stage203_identity_recovery as E, stage171_recent_history as M
from scripts.rhp_runner_eligibility import eligibility
ROOT=P.Path('data'); LEDGER=ROOT/'stage203c-history'; SOURCE='STAGE203C_CORROBORATED_HISTORY'
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def dump(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,ensure_ascii=False,sort_keys=True)+'\n')
def n(x):return G.norm(x)
def st(x):return S.state({'position':x[7],'status':x[8]})
def aid(x):return {'horse':x[3],'horse_id':x[2],'sire':x[17],'dam':x[18]}
def conflict(a,b):return any(n(a.get(k)) and n(b.get(k)) and n(a[k])!=n(b[k]) for k in ('sire','dam'))
def blank(v):return v is None or v==''
def num(v):return I.number(v)
def load_source(src,dates):
    groups=C.defaultdict(lambda:C.defaultdict(list)); meta=C.defaultdict(list)
    for fn,opener in I.source_iter(src):
      with opener() as f:
        rd=csv.DictReader(f); m=I.mapping(rd.fieldnames or [])
        if not all(m.get(k) for k in ('race_date','race_id','course','horse','finish')):continue
        for row in rd:
          d=I.value(row,m,'race_date')[:10]
          if d not in dates:continue
          def v(k):return I.value(row,m,k) or None
          def number(k):return num(v(k))
          def integer(k):return int(number(k)) if number(k) is not None else None
          raw,co,h=v('race_id'),v('course'),v('horse')
          if not all((raw,co,h)):continue
          rid=I.SOURCE+':'+d+':'+I.normhead(co)+':'+re.sub('[^a-zA-Z0-9_-]','',raw)[:90]
          hid=v('horse_id'); key='id:'+hid if hid else 'name:'+I.normname(h)
          pos,o=I.intpos(v('finish')); dist=I.distance_f(v('distance'),m['distance'] or '') if m.get('distance') else None
          title=next((str(row.get(k) or '').strip() for k in rd.fieldnames if I.normhead(k) in ('race_name','race_title','race_description','title') and str(row.get(k) or '').strip()),None)
          facts=[rid,d,co,v('race_type'),v('surface'),dist,v('going'),v('race_class'),I.SOURCE,title,raw]
          if facts not in meta[rid]:meta[rid].append(facts)
          run=[rid,key,hid,h,I.normname(h),v('country'),v('foaled'),pos,o,v('trainer'),v('jockey'),number('rating'),I.weight_lbs(v('weight')),I.odds(v('sp')),integer('draw'),integer('age'),v('sex'),v('sire'),v('dam'),number('rpr'),number('ts'),v('comment'),v('beaten')]
          if run not in groups[rid][key]:groups[rid][key].append(run)
    return groups,meta

def archives(root,dates):
    out=C.defaultdict(list)
    for d in sorted(dates):
      p=root/'racing-results'/(d+'.json')
      if not p.exists():continue
      for r in json.loads(p.read_text()).get('results',[]):
        # Same verified source used in Stage182; do not treat declarations as results.
        if not S.trusted(r) or str(r.get('date') or '')[:10]!=d:continue
        rr=r.get('runners') or []; names=[n(h.get('horse')) for h in rr]
        if not rr or not all(names) or len(set(names))!=len(names):continue
        active={n(h['horse']):h for h in rr if S.state(h)[0]!='NR'}
        if not active:continue
        out[d].append((r,active))
    return out

def merge_values(rows,ignore=()):
    result=list(rows[0]); diffs=[]
    for i in range(len(result)):
      if i in ignore:continue
      vals=[]
      for row in rows:
        v=row[i]
        if not blank(v) and v not in vals:vals.append(v)
      if len(vals)>1:diffs.append(i)
      elif vals:result[i]=vals[0]
    return result,diffs

def archive_state(h,patch):
    return tuple(patch.get(n(h.get('horse')),S.state(h)))

def verify_archive(r,af,patch):
    states={k:archive_state(h,patch) for k,h in af.items()}
    positions=sorted(p for s,p in states.values() if p is not None)
    return bool(states and positions and positions==list(range(1,len(positions)+1)) and all(s not in ('UNKNOWN','NR') for s,p in states.values()))

def provider_patches(archive,focus,token):
    """Use existing PuntersEdge endpoint only for incomplete archived terminal outcomes; no guessed fields."""
    from scripts.stage65c_puntersedge_recovery import get_results
    from zoneinfo import ZoneInfo
    dates={d for d,pairs in archive.items() if any(set(af)&focus[d] and not verify_archive(r,af,{}) for r,af in pairs)}
    query=sorted(dates|{(D.date.fromisoformat(d)+D.timedelta(days=1)).isoformat() for d in dates})
    results={}; errors={}; budget={'requests':0,'max_requests':max(4,min(60,len(query)*2+2))}
    if not token:return {},{'status':'PUNTERSEDGE_SECRET_UNAVAILABLE'}
    for day in query:
      try:results[day],_=get_results(day,token,budget)
      except Exception as e:errors[day]=type(e).__name__+': '+str(e)[:150]
      if any(x in errors.get(day,'') for x in ('HTTP 401','HTTP 403','HTTP 429')):break
    patches={}
    for d in dates:
      for r,af in archive[d]:
        if not set(af)&focus[d] or verify_archive(r,af,{}):continue
        options=[]
        for p in results.get(d,[])+results.get((D.date.fromisoformat(d)+D.timedelta(days=1)).isoformat(),[]):
          try:t=D.datetime.fromisoformat(p['start_time'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Dublin'))
          except (KeyError,ValueError,TypeError):continue
          if t.date().isoformat()!=d or t.hour*60+t.minute!=M.off(r) or S.course(p.get('venue_canonical') or p.get('venue'))!=S.course(r.get('course')):continue
          if p.get('category')!='horse' or p.get('country') not in ('GB','IE') or str(p.get('status','')).lower() not in ('final','finished','resulted','complete','completed','official') or not p.get('race_id'):continue
          active=[h for h in p.get('runners',[]) if S.state(h)[0]!='NR']; pf={n(h.get('horse') or h.get('name')):S.state(h) for h in active}
          if len(pf)!=len(active) or pf.keys()!=af.keys() or any(S.state(h)[0]!='UNKNOWN' and S.state(h)!=pf[k] for k,h in af.items()):continue
          patch={k:v for k,v in pf.items() if S.state(af[k])[0]=='UNKNOWN'}
          if verify_archive(r,af,patch):options.append((str(p['race_id']),patch))
        if options and len({digest(x) for x in options})==1:patches[digest(r)]={'provider_id':options[0][0],'patch':options[0][1]}
    return patches,{'requests':budget['requests'],'errors':errors}

def build_source(rid,groups,meta,r,af,patch):
    # Corroborate complete active field, all known outcomes, and compatible pedigrees.
    if not verify_archive(r,af,patch):return None,'ARCHIVE_OUTCOME_INCOMPLETE'
    race,mdiff=merge_values(meta[rid]);
    if mdiff:return None,'SOURCE_RACE_METADATA_CONFLICT:'+str(mdiff)
    raw=groups[rid]; by=C.defaultdict(list)
    for variants in raw.values():
      active=[x for x in variants if st(x)[0]!='NR']
      if not active:continue
      if len(active)!=len(variants):return None,'CONFLICTING_NONRUNNER'
      by[n(active[0][3])].extend(active)
    if set(by)!=set(af):return None,'FULL_FIELD_MISMATCH'
    runs=[]
    for k,variants in by.items():
      ah=af[k]; expected=archive_state(ah,patch)
      if any(conflict(aid(x),ah) for x in variants):return None,'PEDIGREE_CONFLICT:'+k
      # Distinct source runner IDs or other nonblank conflicting fields are never merged.
      x,ds=merge_values(variants,ignore=(7,8))
      if ds:return None,'SOURCE_RUNNER_CONFLICT:'+k+':'+str(ds)
      known={st(v) for v in variants if st(v)[0]!='UNKNOWN'}
      if known and known!={expected}:return None,'SOURCE_OUTCOME_CONFLICT:'+k
      # Missing outcomes can be filled from the independently verified complete result.
      x[7],x[8]=expected[1],expected[0]
      for i,key in ((2,'horse_id'),(9,'trainer'),(10,'jockey'),(17,'sire'),(18,'dam')):
        if blank(x[i]) and ah.get(key):x[i]=ah[key]
      if x[2] and x[1].startswith('name:'):x[1]='id:'+str(x[2])
      if x[13] is None:x[13]=M.sp_decimal(ah.get('sp'))
      if not all(x[i] for i in (2,9,10,17,18)):return None,'RUNNER_METADATA_INCOMPLETE:'+k
      runs.append(x)
    if len({x[1] for x in runs})!=len(runs):return None,'DUPLICATE_RUNNER_ID'
    if S.course(race[2])!=S.course(r.get('course')):return None,'COURSE_CONFLICT'
    dist=I.distance_f(r.get('dist_f') or r.get('distance_f'),'distance_f')
    if race[5] and dist and abs(race[5]-dist)>.5:return None,'DISTANCE_CONFLICT'
    # A blank type/surface/title is filled only from the corroborating final race.
    for i,v in [(3,r.get('type') or r.get('race_type')),(4,r.get('surface')),(5,dist),(6,r.get('going')),(7,r.get('class') or r.get('race_class')),(9,r.get('race_name') or r.get('name'))]:
      if blank(race[i]):race[i]=v
    if not (race[9] and race[3] and race[4] and race[5] and 0<race[5]<50 and race[6] and R.region(race[2]) in ('GB','IRELAND')):return None,'RACE_METADATA_INCOMPLETE'
    return (race[:9],sorted(runs,key=lambda x:x[1]),race[9],race[10]),'VERIFIED_SOURCE_ARCHIVE'

def build_archive(r,af,patch):
    rr=json.loads(json.dumps(r));
    for h in rr['runners']:
      k=n(h.get('horse'))
      if k in patch:h['status'],h['position']=patch[k]
    fact,why=E.full_race(rr,{})
    if not fact:return None,'ARCHIVE_ONLY_'+why
    d=fact['date']; rid='stage203c:'+d+':'+hashlib.sha256(fact['race_id'].encode()).hexdigest()[:24]
    race=[rid,d,fact['course'],fact['type'],fact['surface'],fact['distance'],fact['going'],fact['race_class'],SOURCE]
    runs=[]
    for h in fact['runners']:
      runs.append([rid,'id:'+h['horse_id'],h['horse_id'],h['horse'],I.normname(h['horse']),None,None,h['position'],h['outcome'],h['trainer'],h['jockey'],h['ofr'],h['weight'],h['sp'],h['draw'],h['age'],h['sex'],h['sire'],h['dam'],h['rpr'],h['ts'],h['comment'],h['beaten']])
    return (race,sorted(runs,key=lambda x:x[1]),fact['race_name'],fact['race_id']),'VERIFIED_COMPLETE_ARCHIVE'

def reconstruct(entry,groups,meta,arch):
    pairs=[(r,af) for r,af in arch[entry['date']] if digest(r)==entry['archive_sha256']]
    if len(pairs)!=1:raise RuntimeError('Approved archived result unavailable or changed: '+entry['id'])
    r,af=pairs[0]; patch=entry.get('provider_patch',{}).get('patch',{})
    data,why=build_source(entry['id'],groups,meta,r,af,patch) if entry['kind']=='source' else build_archive(r,af,patch)
    if data is None or digest(data)!=entry['sha256']:raise RuntimeError('Approved race signature changed: '+entry['id']+' '+why)
    return data

def apply_one(c,data,refids):
    race,runs,title,raw=data; rid,d,co=race[:3]
    if c.execute('select 1 from historical_races where id=?',(rid,)).fetchone():
      old=[list(x) for x in c.execute('select * from historical_runs where race_id=? order by runner_key',(rid,))]
      oldrace=list(c.execute('select * from historical_races where id=?',(rid,)).fetchone())
      # SQLite affinity may convert numeric text; compare semantic equality via normalized values.
      if oldrace==race and len(old)==len(runs) and all(a[:22]==b[:22] and (a[22]==b[22] or str(a[22])==str(b[22])) for a,b in zip(old,runs)) and rid in refids:return 'ALREADY_PRESENT',None
      return 'EXISTING_RACE_REQUIRES_REVIEW',None
    if rid in refids:return 'ORPHAN_REFERENCE',None
    old=list(c.execute('select u.horse_name,u.horse_source_id from historical_runs u join historical_races r on r.id=u.race_id where r.race_date=?',(d,)))
    if {n(h) for h,_ in old}&{n(h[3]) for h in runs} or {i for _,i in old if i}&{h[2] for h in runs}:return 'OVERLAP_REQUIRES_REVIEW',None
    c.execute('insert into historical_races values(?,?,?,?,?,?,?,?,?)',race)
    c.executemany('insert into historical_runs values('+','.join('?' for _ in range(23))+')',runs)
    refids.add(rid)
    return 'ADDED',dict(stage2_race_id=rid,date=d,course=co,race_type=race[3],race_name=title,region_scope=R.region(co),source_race_id=raw)

def refs(ref):
    with P.Path(ref).open(encoding='utf-8-sig',newline='') as f:
      rd=csv.DictReader(f);return rd.fieldnames,{r['stage2_race_id'] for r in rd}
def write_refs(ref,fields,add):
    if add:
      with P.Path(ref).open('a',encoding='utf8',newline='') as f:csv.DictWriter(f,fieldnames=fields,extrasaction='ignore').writerows(add)
def append(db,ref,src,target,root=ROOT):
    """Daily shared-history hook. Re-read pinned raw source; only approved signatures allowed."""
    ledger=root/'stage203c-history'; entries={}
    for p in sorted(ledger.glob('*.json')):
      doc=json.loads(p.read_text());assert doc['schema']=='stage203c-v1' and doc['source_version']==132
      for e in doc['races']:
        if e['date']>=target:continue
        if e['id'] in entries:assert entries[e['id']]==e
        entries[e['id']]=e
    if not entries:return []
    dates={e['date'] for e in entries.values()}; groups,meta=load_source(src,{e['date'] for e in entries.values() if e['kind']=='source'}); arch=archives(root,dates)
    fields,refids=refs(ref); add=[]; report=[]
    with sqlite3.connect(db) as c:
      c.execute('pragma foreign_keys=on'); assert c.execute('select max(race_date) from historical_races').fetchone()[0]<target
      for e in sorted(entries.values(),key=lambda x:x['id']):
        data=reconstruct(e,groups,meta,arch); status,ar=apply_one(c,data,refids)
        # Do not overwrite if a later validated import has superseded the ledger.
        if ar:add.append(ar)
        report.append({'id':e['id'],'status':status})
      assert c.execute('pragma quick_check').fetchone()[0]=='ok'; c.commit()
    write_refs(ref,fields,add);print('STAGE203C_SHARED_HISTORY',dict(C.Counter(x['status'] for x in report)),flush=True);return report

def run():
    import kagglehub
    from zoneinfo import ZoneInfo
    from stage167_validated_history import refreshed_inputs
    now=D.datetime.now(ZoneInfo('Europe/Dublin')).date(); target=os.getenv('ASKED_DATE','').strip() or (now+D.timedelta(days=1)).isoformat()
    assert D.date.fromisoformat(target) in (now,now+D.timedelta(days=1))
    out=P.Path('reports/stage203c/output');out.mkdir(parents=True,exist_ok=True)
    rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r['date']==target and eligibility(r)=='ACTIVE']; assert rows and len({r['horse_id'] for r in rows})==len(rows)
    cards={r['horse_id']:r for r in rows}; src=P.Path(kagglehub.dataset_download('deltaromeo/horse-racing-results-ukireland-2015-2025/versions/132'))
    ref=list(P.Path('reports/stage203c/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(ref)==1
    base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite')
    with refreshed_inputs(base,ref[0],target) as (db,reference):
      hits,others=E.scan(db,rows,target); before=E.classify(rows,hits,others,target); focus=[x for x in before if x['last_run_matched'] is False or x['known_no_history']]; dates={x['expected'] for x in focus if x['expected']}; wanted=C.defaultdict(set)
      for x in focus:
        if x['expected']:wanted[x['expected']].add(n(x['horse']))
      groups,meta=load_source(src,dates); arch=archives(ROOT,dates); patches,provider=provider_patches(arch,wanted,os.getenv('PUNTERSEDGE_API_KEY','').strip())
      source_index=C.defaultdict(list)
      for rid,variants in groups.items():
        for vv in variants.values():
          for x in vv:
            if st(x)[0]!='NR':source_index[meta[rid][0][1],n(x[3])].append(rid)
      candidates={}; review={}; fields,refids=refs(reference)
      for x in focus:
        hid=x['horse_id']; d=x['expected']; name=n(x['horse']); card=cards[hid]; choices={}; rejected=[]
        for r,af in arch.get(d,[]):
          if name not in af:continue
          ar=af[name]; patchinfo=patches.get(digest(r),{}); patch=patchinfo.get('patch',{})
          if not verify_archive(r,af,patch):rejected.append('ARCHIVE_INCOMPLETE');continue
          for rid in set(source_index[d,name]):
            if S.course(meta[rid][0][2])!=S.course(r.get('course')):continue
            targetrows=[z for vv in groups[rid].values() for z in vv if n(z[3])==name]
            if not any(E.exact(card,aid(z)) for z in targetrows) and not E.exact(card,ar):rejected.append('TARGET_IDENTITY_CONFLICT');continue
            if any(conflict(card,aid(z)) for z in targetrows) or conflict(card,ar):rejected.append('TARGET_PEDIGREE_CONFLICT');continue
            data,why=build_source(rid,groups,meta,r,af,patch)
            if data:
              tr=[z for z in data[1] if n(z[3])==name][0]
              # A known exact prior provider identity can supply missing pedigree, not replace conflicting parents.
              if not E.exact(card,aid(tr)):
                rejected.append('MODEL_IDENTITY_NOT_RECONCILED');continue
              entry={'id':rid,'date':d,'kind':'source','archive_sha256':digest(r),'provider_patch':patchinfo,'sha256':digest(data)};choices[rid]=(entry,data)
            else:rejected.append(why)
          # Fallback to complete, trusted app result if no validated source candidate.
          if not choices and E.exact(card,ar) and not any(S.course(meta[sid][0][2])==S.course(r.get('course')) for sid in set(source_index[d,name])):
            data,why=build_archive(r,af,patch)
            if data:
              rid=data[0][0];entry={'id':rid,'date':d,'kind':'archive','archive_sha256':digest(r),'provider_patch':patchinfo,'sha256':digest(data)};choices[rid]=(entry,data)
            else:rejected.append(why)
        review[hid]={'horse':x['horse'],'expected':d,'source_candidates':sorted(set(source_index[d,name])),'archive_candidates':sum(name in af for _,af in arch.get(d,[])),'reasons':sorted(set(rejected)),'status':'UNIQUE_CANDIDATE' if len(choices)==1 else 'AMBIGUOUS_CANDIDATES' if choices else 'NO_CORROBORATED_COMPLETE_FIELD'}
        if len(choices)==1:
          rid,(entry,data)=next(iter(choices.items()));review[hid]['candidate']=rid
          if rid in candidates and candidates[rid][0]!=entry:raise RuntimeError('Conflicting candidate evidence')
          candidates[rid]=(entry,data)
      # Reject intersecting candidate races before adding either, rather than first-wins.
      occupied=C.defaultdict(set)
      for rid,(entry,data) in candidates.items():
        for h in data[1]:occupied[(entry['date'],'name',n(h[3]))].add(rid);occupied[(entry['date'],'id',h[2])].add(rid)
      collisions={rid for ids in occupied.values() if len(ids)>1 for rid in ids}; approved=[]; additions=[]; decisions={}
      with sqlite3.connect(db) as c:
        c.execute('pragma foreign_keys=on')
        for rid,(entry,data) in sorted(candidates.items()):
          if rid in collisions:decisions[rid]='CANDIDATE_OVERLAP';continue
          status,ar=apply_one(c,data,refids);decisions[rid]=status
          if ar:approved.append(entry);additions.append(ar)
        assert c.execute('pragma quick_check').fetchone()[0]=='ok' and c.execute('select max(race_date) from historical_races').fetchone()[0]<target;c.commit()
      write_refs(reference,fields,additions)
      hits2,others2=E.scan(db,rows,target);after=E.classify(rows,hits2,others2,target); b={x['horse_id']:x for x in before};a={x['horse_id']:x for x in after}
      assert all(a[k]['starts']>=b[k]['starts'] for k in b)
      for hid,x in review.items():
        x['decision']=decisions.get(x.get('candidate')); x['last_run_matched_after']=a[hid]['last_run_matched'];x['starts_before']=b[hid]['starts'];x['starts_after']=a[hid]['starts']
        if a[hid]['last_run_matched'] is True or (b[hid]['known_no_history'] and not a[hid]['known_no_history'] and a[hid]['last_run_matched'] is not False):x['status']='RECOVERED_VERIFIED_HISTORY'
      # Publish only checksum manifest and existing archive references, not source rows.
      path=LEDGER/(target+'.json'); prev=json.loads(path.read_text())['races'] if path.exists() else [];pool={x['id']:x for x in prev}
      for e in approved:
        if e['id'] in pool:assert pool[e['id']]==e
        pool[e['id']]=e
      # Confirm deterministic reconstruction and no duplicate insert before publishing.
      for e in approved:assert digest(reconstruct(e,groups,meta,arch))==e['sha256']
      with sqlite3.connect(db) as c:
        count=c.execute('select count(*) from historical_runs').fetchone()[0]
        for e in approved:
          data=reconstruct(e,groups,meta,arch);s,_=apply_one(c,data,refids);assert s=='ALREADY_PRESENT',(e['id'],s)
        assert c.execute('select count(*) from historical_runs').fetchone()[0]==count
      if approved or path.exists():dump(path,{'schema':'stage203c-v1','source_version':132,'target':target,'races':list(pool.values())})
      report={'target':target,'active':len(rows),'focus_before':len(focus),'gaps_after':sum(x['last_run_matched'] is False or x['known_no_history'] for x in after),'new_races':len(approved),'recovered_horses':sum(x['status']=='RECOVERED_VERIFIED_HISTORY' for x in review.values()),'status_counts':dict(C.Counter(x['status'] for x in review.values())),'decision_counts':dict(C.Counter(decisions.values())),'provider':provider,'idempotence_passed':True,'note':'Uncorroborated cases, point-to-point and overseas history not silently imported; only approved source checksums and archive evidence published. No HPR/model changes.'}
      dump(out/'summary.json',report);dump(out/'horse-review.json',list(review.values()));dump(out/'race-decisions.json',decisions);dump(out/'approved-manifest.json',approved);print(json.dumps(report,indent=2));P.Path(os.environ['GITHUB_STEP_SUMMARY']).write_text('## Stage203C results\n\n```json\n'+json.dumps(report,indent=2)+'\n```\n')
if __name__=='__main__':run()
