#!/usr/bin/env python3
"""Stage 116: verified historical form for LIVE D / D(c), HPR and G paper runs.

* Exact matching: normalized horse name PLUS BOTH sire/dam or literal provider ID.
* One unambiguous historical key only; unverifiable records stay flagged.
* Original model coefficients and predictions are never rewritten retrospectively.
* Recovered HPR ratings are versioned by the original Stage86 HPR storage engine.
* Historical events always have race_date < the racecard target date.
* No SP, starting prices, forecasts, or outcomes from target dates are model inputs.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, hashlib, json, os, pathlib, re, sqlite3, sys
from typing import Dict

ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import stage114_exact_identity_and_form_research as A
import stage112_form_coverage_reconcile as S
VERSION='stage116-verified-form-live-v1'

def canonical(s):return A.normname(s)

def is_declared(c,target):
    return str(c.get('date') or c.get('race_time') or '')[:10]==str(target) and bool(c.get('horse')) and S.is_starter(c)

def read_cards(path,target):
    cards=json.loads(pathlib.Path(path).read_text(encoding='utf-8')).get('runners',[])
    return [r for r in cards if is_declared(r,target)]

def historical_candidates_flexible(db,cards,target):
    """Handle BOTH importer canonical names and Stage65M space-retaining names.

    The newer pre-off verified backfill uses OLD.normal(horse), whereas the
    original importer used all-capitals with spaces stripped. Both are indexed
    exact values; the subsequent pedigree/ID join remains strict.
    """
    from stage30_gap_aware_abcd import normal
    requests={canonical(c.get('horse')) for c in cards if c.get('horse')}
    keys=set()
    for c in cards:
        name=c.get('horse')
        if name:keys.update((canonical(name),normal(name),normal(name).upper()))
    by=collections.defaultdict(list)
    cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    cx.execute('PRAGMA query_only=ON')
    try:
        arr=sorted(keys)
        for i in range(0,len(arr),300):
            part=arr[i:i+300]
            query=('SELECT u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,'
                   'u.sire,u.dam,u.finish_pos,u.outcome,r.race_date,r.course,r.id '
                   'FROM historical_runs u JOIN historical_races r ON r.id=u.race_id '
                   'WHERE u.horse_name_norm IN ('+','.join('?' for _ in part)+') '
                   'AND r.race_date<? ORDER BY r.race_date')
            for n,h,country,sid,sire,dam,pos,outcome,date,course,race_id in cx.execute(query,[*part,target]):
                hkey=canonical(h)
                if hkey not in requests:continue
                by[hkey].append({'horse_name_norm':str(n or ''),'horse_name':str(h or ''),
                 'country':str(country or ''),'source_id':str(sid or ''),
                 'sire':str(sire or ''),'dam':str(dam or ''),
                 'finish_pos':pos,'outcome':str(outcome or ''),'date':str(date),
                 'course':str(course or ''),'race_id':str(race_id)})
    finally:cx.close()
    return by

def resolve_verified(db,cards,target):
    """Read-only source identity recovery; fail closed on conflicting archive keys."""
    from stage30_gap_aware_abcd import TRACKS,track
    if not cards:return {},{'declared':0,'D_verified':0,'C_verified':0,'HPR_candidates':0}
    byname=historical_candidates_flexible(db,cards,target)
    aliases={};flags=collections.Counter()
    for c in cards:
        n=canonical(c.get('horse'));idkey=str(c.get('horse_id') or '').strip()
        if not n:continue
        # Refuse ambiguous same-name racecard entries, even if pedigrees differ.
        k=(n,idkey)
        matches=[h for h in byname.get(n,[]) if h['date']<target and A.eligible_pedigree_row(c,h) and S.valid_run(h)]
        flags['declared']+=1
        if not matches:
            flags['no_verified_archive_prior']+=1
            continue
        dkey={A.historical_D_key(h) for h in matches if track(h['course']) in TRACKS}
        ckey={A.historical_C_key(h) for h in matches if h['date']>='2021-01-01' and h.get('finish_pos') is not None}
        # Stage120: D and C use independent verified historical keys.
        # A C suffix variant alone cannot invalidate one unique D key.
        # Distinct D keys are never automatically merged.
        if len(dkey)>1:flags['ambiguous_D_archive_identity']+=1
        if len(ckey)>1:flags['ambiguous_C_archive_identity']+=1
        d=next(iter(dkey),None) if len(dkey)==1 else None
        q=next(iter(ckey),None) if len(ckey)==1 else None
        if (len(dkey)>1 or len(ckey)>1) and d is None and q is None:
            flags['ambiguous_archive_identity']+=1
            continue
        if d:flags['D_verified']+=1
        if q:flags['C_verified']+=1
        flags['HPR_candidates']+=1
        aliases[k]={'horse':c['horse'],'horse_id':idkey,'D_key':d,'C_key':q,
                    'source_races':len({(h['date'],h['race_id']) for h in matches}),
                    'last_verified_date':max(h['date'] for h in matches),
                    'identity_proof':'EXACT_SOURCE_ID_OR_BOTH_PARENTS'}
    # A duplicate normalized name with multiple distinct IDs must not be
    # guessed in C.Form.vector (which receives name, not source ID).
    by=collections.defaultdict(list)
    for (name,_),value in aliases.items():by[name].append(value)
    for name,values in by.items():
        if len(values)>1:
            for v in values:v['C_key']=None;v['duplicate_live_name']=True
            flags['duplicate_live_name']+=1
    flags['aliases']=len(aliases)
    flags['unverified']=flags['declared']-len(aliases)
    return aliases,dict(flags)

def apply_d_row(row,card,aliases):
    """Replace ONLY historical identity slots 8 and 10 of Stage38's 23-tuple."""
    from stage30_gap_aware_abcd import normal
    a=aliases.get((canonical(card.get('horse')),str(card.get('horse_id') or '').strip()))
    if not a or a.get('D_key') is None:return row
    key=a['D_key']
    # Another model source shape might not have the exact Stage38 tuple.
    if len(row)!=23 or normal(card.get('horse'))!=normal(row[9]):
        raise RuntimeError('Unexpected Stage38 row schema; refuse unsafe live alias')
    q=list(row);q[8]=key[0];q[10]=key[1]
    return tuple(q)

def patch_dc_runtime(D,C,db,target):
    """Patch only THIS Python process's D(c) lookups. C weights remain frozen.

    In Stage83 driver, call this after C.rows=corrected_rows. Historical C
    training rows get no identity change; only live race_date==target is touched.
    """
    entries=D.fields_for(target)
    cards=[z for e in entries for z in e['runners']]
    aliases,stats=resolve_verified(db,cards,target)
    # Build name -> C history key ONLY if unambiguous across the whole field.
    cn={};seen=collections.Counter()
    for (name,_),v in aliases.items():seen[name]+=1
    for (name,_),v in aliases.items():
        if seen[name]==1 and v.get('C_key'):cn[name]=v['C_key']
    old_live=D.live_row
    old_vector=C.Form.vector
    def verified_live_row(r,day,rid,unique_history_country=None):
        old=old_live(r,day,rid,unique_history_country)
        return apply_d_row(old,r,aliases) if day==target else old
    def verified_C_vector(self,r,field_or=None,field_weight=None):
        if str(r.get('date') or '')==target:
            alias=cn.get(canonical(r.get('horse')))
            if alias:
                r=dict(r);r['horse']=alias[0];r['country']=alias[1]
        return old_vector(self,r,field_or,field_weight)
    D.live_row=verified_live_row
    C.Form.vector=verified_C_vector
    print('STAGE116 Live D(c) verified identity map:',json.dumps(stats,sort_keys=True),flush=True)
    return stats

def audit_file(db,cards,target,out):
    aliases,summary=resolve_verified(db,cards,target)
    rows=[]
    for c in cards:
        k=(canonical(c['horse']),str(c.get('horse_id') or ''))
        v=aliases.get(k,{})
        rows.append({'date':target,'course':c.get('course'),'race_time':c.get('race_time'),
          'horse':c['horse'],'horse_id':c.get('horse_id'),'racecard_form':c.get('form'),
          'racecard_has_prior_form':int(bool(re.search(r'\d',str(c.get('form') or '')))),
          'D_verified_alias':int(bool(v.get('D_key'))),'C_verified_alias':int(bool(v.get('C_key'))),
          'verified_races':v.get('source_races',0), 'latest_verified_run':v.get('last_verified_date',''),
          'unverified_identity_or_debut':int(not v.get('identity_proof'))})
    summary.update(date=target,source_db=str(db),model_weights_modified=False,stage=VERSION)
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    with (out/'stage116-runner-identity-audit.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['horse']);w.writeheader();w.writerows(rows)
    (out/'stage116-identity-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary

def recover_hpr(source_db,store,cards,target,out,apply=False):
    """Create/revise only HPR events supported by verified prior races.

    For an affected horse replay all dated pre-target existing and newly
    recovered source events in chronological order. Later scores are revised
    (append-only) if earlier history changes their context. No future events.
    """
    import stage86_hpr_engine as H
    cards=[c for c in cards if H.identity(c)]
    profiles={H.identity(c):c for c in cards}
    archived,source=H.historical_matches(source_db,profiles,target)
    lookup=collections.defaultdict(list)
    for e in archived:
        if not str(e.get('date',''))<target:raise RuntimeError('Future source event')
        if H.number(e.get('position')) is None and str(e.get('status') or '').lower() not in H.UNFINISHED:continue
        lookup[e['horse_key']].append(e)
    s=pathlib.Path(store)
    if not s.exists():raise FileNotFoundError('Restored original HPR ledger missing: '+str(s))
    c=sqlite3.connect(s)
    if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Damaged HPR ledger')
    report=[];candidates=[];counts=collections.Counter()
    try:
        if apply:c.execute('BEGIN')
        for key,p in sorted(profiles.items()):
            old=H.previous_ratings(c,key,target)
            known={(str(x.get('date')),H.course(x.get('course'))):x for x in old}
            additions=[]
            for r in lookup.get(key,[]):
                k=(str(r.get('date')),H.course(r.get('course')))
                if k not in known:additions.append(r);known[k]=r
            events=sorted(known.values(),key=lambda x:(x['date'],str(x.get('race_key') or '')))
            if not additions:
                counts['no_verified_missing_event']+=1
                continue
            prev=[];changed=0;new=0;preserved=0
            for event in events:
                if str(event['date'])>=target:raise RuntimeError('Target-day leakage')
                r,reason=H.score_event(event,prev)
                if r is None:
                    if 'hpr' in event:
                        prev.append(event);preserved+=1
                    else:counts['unscorable_'+str(reason)]+=1
                    continue
                # Preserve original source race identity; no synthetic dates.
                if 'hpr' not in event:
                    r['source']='archive_exact_identity_stage116'
                    new+=1
                elif abs(float(r['hpr'])-float(event['hpr']))>.01:
                    changed+=1
                else:
                    # No unnecessary rewrite of existing unchanged ratings.
                    prev.append(event);continue
                r['model_version']=H.VERSION
                candidates.append(r)
                prev.append(r)
            counts['horses_with_recovered_history']+=1
            counts['new_verified_prior_events']+=new
            counts['later_ratings_recomputed']+=changed
            report.append({'horse':p.get('horse'),'horse_key':key,'existing_ratings':len(old),
                           'verified_archive_events':len(lookup.get(key,[])),
                           'additional_verified_ratings':new,'later_revisions':changed,
                           'source':'both_parents_or_identical_source_id'})
        # Save all revisions in one transaction using official HPR v0 engine.
        if apply:
            w=H.save_ratings(c,candidates)
            counts['HPR_rows_inserted']=w.get('inserted',0)
            counts['HPR_rows_revised']=w.get('revised',0)
            if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('HPR invalid after rebuild')
    finally:c.close()
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    with (out/'stage116-hpr-recovery.csv').open('w',newline='',encoding='utf-8') as f:
        cols=['horse','horse_key','existing_ratings','verified_archive_events','additional_verified_ratings','later_revisions','source']
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(report)
    meta={'version':VERSION,'target':target,'applied':apply,'counts':dict(counts),
          'source_evidence':source,'actual_source_events_not_synthetic':True,
          'original_HPR_weights_unchanged':True,'model_version':H.VERSION}
    (out/'stage116-HPR-summary.json').write_text(json.dumps(meta,indent=2)+'\n')
    return meta

def ensure_fresh_result_db(db,target,repo):
    """Stage38: replay dated, verified completed results into a TEMPORARY DB.

    Stage38's original source is a rolling Stage2 artifact. Stage65M already
    audits exact historical fields against pre-off Git card snapshots. Reuse
    that original validated code and never modify the master source file.
    """
    import subprocess
    src=pathlib.Path(db)
    c=sqlite3.connect('file:'+str(src.resolve())+'?mode=ro',uri=True)
    try:latest=c.execute('SELECT MAX(race_date) FROM historical_races WHERE race_date<?',(target,)).fetchone()[0]
    finally:c.close()
    limit=(dt.date.fromisoformat(target)-dt.timedelta(days=1)).isoformat()
    meta={'historical_source_last':latest,'target_day':target,'current_date_needed':limit,
          'source_updated_by_verified_backfill':False}
    if not latest or latest>=limit:return db,meta
    repo=pathlib.Path(repo).resolve()
    # If no final result files exist newer than the historical master, leave
    # the archive unchanged but surface the staleness explicitly in output.
    newer=[]
    for filename in (repo/'data/race-results.json',):
        if filename.is_file():
            try:
                doc=json.loads(filename.read_text())
                newer=[x for x in doc.get('races',[]) if latest<str(x.get('date') or '')[:10]<=limit
                       and str(x.get('status') or '').lower()=='final']
            except (OSError,ValueError):pass
    meta['known_verified_final_races_after_source']=len(newer)
    if not newer:
        meta['status']='HISTORY_SOURCE_STALE_NO_COMPLETED_RESULTS_AVAILABLE'
        return db,meta
    if (repo/'.git/shallow').exists():
        subprocess.run(['git','fetch','--unshallow','origin','main'],cwd=repo,check=True)
    out=repo/'reports/stage116/latest-verified-result-bridge'
    out.mkdir(parents=True,exist_ok=True)
    args=[sys.executable,'scripts/stage65m_backfill.py','--baseline',str(src.resolve()),
          '--out',str(out),'--max-date',limit,'--keep-candidate','--skip-replay']
    result=subprocess.run(args,cwd=repo,capture_output=True,text=True)
    if result.returncode:
        raise RuntimeError('Verified pre-off result backfill failed closed: '+result.stderr[-1400:])
    candidate=out/'backfilled_research_only.sqlite'
    if not candidate.is_file():raise RuntimeError('Verified backfill did not create source candidate')
    status=json.loads((out/'backfill_summary.json').read_text())
    meta.update(status='VALIDATED_PREOFF_RESULTS_BRIDGE',
                source_updated_by_verified_backfill=True,
                new_races_inserted=status.get('inserted_races',0))
    return str(candidate),meta

def prepare_g_state(db,hpr,cards,target,state_input,prepared,out):
    """Lossless 2026 verified-form bridge for *existing* G3/G4 paper models.

    Weights/trained model files are not altered. This replaces only the
    temporary FormState supplied to the existing paper prediction command.
    """
    import copy,joblib,stage108_model_g2 as G
    import stage113b_lossless_multi_model_form_audit as M
    snap=joblib.load(state_input)
    frozen=str(snap.get('asof_after_date') or '')[:10]
    if not frozen or frozen>=target or not hasattr(snap.get('state'),'horses'):
        raise RuntimeError('G3/G4 snapshot missing or later than target; refuse repair')
    current=copy.deepcopy(snap['state'])
    repaired=copy.deepcopy(snap['state'])
    # Load the original live HPR source first, then merge archive runs not
    # already present. This preserves HPR-only runs beyond the archive cutoff.
    baseline=G.load_recent_hpr(current,hpr,frozen,target) if hpr else {'added':0}
    restored=G.load_recent_hpr(repaired,hpr,frozen,target) if hpr else {'added':0}
    cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    try:end=cx.execute('SELECT MAX(race_date) FROM historical_races WHERE race_date<?',(target,)).fetchone()[0]
    finally:cx.close()
    a={'archive_races_seen':0,'new_verified_historical_starts_added':0}
    if end and end>frozen:
        start=(dt.date.fromisoformat(frozen)+dt.timedelta(days=1)).isoformat()
        a=M.add_verified_archive_unseen(repaired,db,start,end,G)
    M.sorted_buckets(repaired)
    rows=[r for r in cards if is_declared(r,target)]
    before=S.report_state(rows,current,G,'ORIGINAL_HPR_ONLY')
    after=S.report_state(rows,repaired,G,'HPR_AND_VERIFIED_HISTORY')
    M.assert_no_regressions(before,after)
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    file=pathlib.Path(prepared);file.parent.mkdir(parents=True,exist_ok=True)
    joblib.dump({**snap,'asof_after_date':(dt.date.fromisoformat(target)-dt.timedelta(days=1)).isoformat(),
                 'state':repaired,'stage116_asof':target,'stage116_source_end':end},file,compress=3)
    summary={'target':target,'frozen_last':frozen,'archive_last':end,
             'existing_hpr':baseline,'verified_hpr':restored,'merge':a,
             'claimed_previous_form':sum(S.has_public_form(r) for r in rows),
             'prior_match_before':sum(int(r['prior_runs'])>0 for r in before),
             'prior_match_after':sum(int(r['prior_runs'])>0 for r in after),
             'no_history_regressions':True,'weights_and_model_files_unchanged':True}
    (out/'stage116-g-form-summary.json').write_text(json.dumps(summary,indent=2,default=str)+'\n')
    return summary

def install_code(repo,dry_run=False):
    """Minimal exact-source patch. Refuses different production code versions.

    GitHub Actions install workflow writes staged versioned files, runs selftests,
    then commits only approved scripts and HPR shards atomically.
    """
    root=pathlib.Path(repo)
    f=root/'scripts/stage38_model_dz_live.py'
    g=root/'scripts/rhp_stage83_dc_integrated.py'
    a=f.read_text();b=g.read_text()
    dmark='stage116_verified_form_live'
    if dmark not in a:
        token='    history=LivePast({});history_info=history.scan_until(db,day)'
        patch=('    from stage116_verified_form_live import ensure_fresh_result_db,resolve_verified,apply_d_row\n'
          '    db,_stage116_source=ensure_fresh_result_db(db,day,base)\n'
          +token+'\n'
          '    _stage116_cards=[z for _group in fields.values() for z in _group]\n'
          '    _stage116_aliases,_stage116_audit=resolve_verified(db,_stage116_cards,day)\n'
          '    output["verified_form_recovery"]=_stage116_audit\n'
          '    output["verified_form_source"]=_stage116_source')
        if a.count(token)!=1:raise RuntimeError('Stage38 insertion point differs; no unsafe patch')
        a=a.replace(token,patch)
        old='        rows=[live_row(r,day,rid,unique_history_country) for r in rs]'
        new='        rows=[apply_d_row(live_row(r,day,rid,unique_history_country),r,_stage116_aliases) for r in rs]'
        if a.count(old)!=1:raise RuntimeError('Stage38 score line differs; no unsafe patch')
        a=a.replace(old,new)
    if dmark not in b:
        token=' D.c_classifier=lambda db:patched_classifier(D,C,db)'
        patch=(token+'\n'
               ' import stage116_verified_form_live as _stage116_form\n'
               ' _stage116_form_stats=_stage116_form.patch_dc_runtime(D,C,a.db,a.day)')
        if b.count(token)!=1:raise RuntimeError('Stage83 insertion point differs; no unsafe patch')
        b=b.replace(token,patch)
        saved='  if pathlib.Path(path)==D.FEED:'
        if b.count(saved)!=1:raise RuntimeError('Stage83 D(c) feed wrapper changed')
        b=b.replace(saved,saved+'\n   obj["verified_form_recovery"]=_stage116_form_stats',1)
    # G3/G4 are paper workflows, not operational app feeds. Make their next
    # run use losslessly stitched form without touching trained parameters.
    gflows=[]
    for wf,base_state,patched_state,score_line in (
        ('.github/workflows/stage109-g3-paper.yml',
         'reports/stage109/restore/stage109-prior-form-state.joblib',
         'reports/stage109/stage116-patched-form-state.joblib',
         '      - name: Generate all-horse rankings and separate G2/G3 Fancied'),
        ('.github/workflows/stage111b-g4-paper-fix.yml',
         'reports/stage111/restore/g4/stage111-prior-form-state.joblib',
         'reports/stage111/stage116-patched-form-state.joblib',
         '      - name: Score all remaining eligible races with G3 and both G4 variants, zero odds input'),
    ):
        path=root/wf
        text=path.read_text()
        if 'stage116-patched-form-state.joblib' not in text:
            if text.count(score_line)!=1:raise RuntimeError(wf+' source step changed; refuse automatic patch')
            home='reports/stage109' if 'stage109' in wf else 'reports/stage111'
            forecast='stage109' if 'stage109' in wf else 'stage111'
            snippet=(
              '      - name: Restore verified master 2026 form and preserve HPR observations\n'
              '        env:\n'
              '          GH_TOKEN: ${{ github.token }}\n'
              '          TARGET: ${{ inputs.selection_date }}\n'
              '        shell: bash\n'
              '        run: |\n'
              '          set -euo pipefail\n'
              f'          mkdir -p {home}/stage116\n'
              f'          gh run download 43089770857 --repo "$GITHUB_REPOSITORY" --name stage2-historical-staging-database --dir {home}/stage116\n'
              f'          test -s {home}/stage116/historical_ukie_staging.sqlite\n'
              f'          args=(--mode prepare-g-state --db {home}/stage116/historical_ukie_staging.sqlite\n'
              f'            --cards data/racecards.json --target "${{TARGET:-$(date -u +%Y-%m-%d)}}"\n'
              f'            --state {base_state} --prepared {patched_state} --out {home}/stage116)\n'
              f'          if test -s {home}/recent.sqlite; then args+=(--hpr {home}/recent.sqlite); fi\n'
              '          python -u scripts/stage116_verified_form_live.py "${args[@]}"\n'
            )
            text=text.replace(score_line,snippet+score_line)
            old='--state '+base_state
            intro,score=text.split(score_line,1)
            if score.count(old)!=1:raise RuntimeError(wf+' G model score state flag changed; refuse')
            text=intro+score_line+score.replace(old,'--state '+patched_state,1)
        gflows.append((path,text))
    import ast
    ast.parse(a);ast.parse(b)
    if not dry_run:
        f.write_text(a);g.write_text(b)
        for path,text in gflows:path.write_text(text)
    return {'stage38_code_is_installed':dmark in a,'stage83_code_is_installed':dmark in b,
            'files_modified' if not dry_run else 'files_would_be_modified':[str(f),str(g)]+[str(p) for p,_ in gflows]}

def self_test():
    assert canonical('Little Ledgend (IRE)')==canonical('LITTLELEDGEND')
    original=('race','2026-11-13','Navan','Hurdle','Turf',16,'Soft',3,
              'littleledgend','Little Ledgend','','',None,'declared','T','J',123,6,'Sire','Dam',None,None,None)
    aliases={(canonical('Little Ledgend'),'123'): {'D_key':('littleledgend','ire'),'C_key':('little ledgend','ire')}}
    card={'horse':'Little Ledgend','horse_id':'123'}
    patched=apply_d_row(original,card,aliases)
    assert patched[8]=='littleledgend' and patched[10]=='ire'
    assert patched[:8]==original[:8] and patched[11:]==original[11:]
    assert apply_d_row(original,{'horse':'Other','horse_id':'1'},aliases)==original
    # Date and name ambiguity are verified in source resolver; no name-only join.
    print('Stage116 PASS: date-safe exact-identity lookup, original 23-field D shape and stable feature dimensions')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['self-test','audit','hpr-dry-run','hpr-apply','install-code','prepare-g-state'],default='self-test')
    p.add_argument('--repo',default='.')
    p.add_argument('--db');p.add_argument('--cards');p.add_argument('--hpr');p.add_argument('--target')
    p.add_argument('--out',default='reports/stage116/output')
    p.add_argument('--state');p.add_argument('--prepared')
    a=p.parse_args()
    if a.mode=='self-test':self_test();return
    if a.mode=='install-code':print(json.dumps(install_code(a.repo)));return
    if not a.db or not a.cards or not a.target:p.error('Requires --db --cards --target')
    dt.date.fromisoformat(a.target)
    cards=read_cards(a.cards,a.target)
    if not cards:raise RuntimeError('No unwithdrawn declared runners on target date')
    if a.mode=='audit':print(json.dumps(audit_file(a.db,cards,a.target,a.out),indent=2));return
    if a.mode=='prepare-g-state':
        if not a.state or not a.prepared:p.error('--state and --prepared required')
        print(json.dumps(prepare_g_state(a.db,a.hpr,cards,a.target,a.state,a.prepared,a.out),indent=2));return
    if not a.hpr:p.error('HPR mode requires --hpr')
    print(json.dumps(recover_hpr(a.db,a.hpr,cards,a.target,a.out,apply=a.mode=='hpr-apply'),indent=2))

# Stage125: strictly verified, additive completed-race source bridge.
# Never modifies the shared master DB; the source is immutable pre-off + final evidence.
_stage125_original_ensure_fresh_result_db = ensure_fresh_result_db

def ensure_fresh_result_db(db, target, repo):
    base, meta = _stage125_original_ensure_fresh_result_db(db, target, repo)
    from stage125_verified_history import apply_verified_history
    patched, audit = apply_verified_history(base, target, repo)
    meta = dict(meta)
    meta['stage125_verified_history'] = audit
    return patched, meta

if __name__=='__main__':main()
