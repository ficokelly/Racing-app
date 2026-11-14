#!/usr/bin/env python3
"""Stage126: evidence-driven, non-publishing UK/IE historical recovery and debut-status audit.

Executed in an isolated Actions checkout. Inserts only completed pre-cutoff races into a
DISPOSABLE copy of the archive, validates every complete historic race and exact ID,
and preserves all previously proven D/C identity keys. Never updates production.
"""
from __future__ import annotations
import collections
import csv
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys

ROOT=pathlib.Path('.').resolve()
sys.path.insert(0,str(ROOT/'scripts'))
import stage116_verified_form_live as V
import stage114_exact_identity_and_form_research as A
import stage125_verified_history as H
import stage38_model_dz_live as D
import stage65m_backfill as B
import stage65k_two_day_replay as K

DAY='2026-11-14'
PIN='93f8e09e2b60b447a6285d85aab29c783d0076f0'
OUT=ROOT/'reports/stage126'
OUT.mkdir(parents=True,exist_ok=True)
SOURCE=OUT/'input'
LIMITED={'Moon Mission','Rough And Tough','Don Dandy','Ocean View','The Rarest Hour'}
TRY={'Iconic Elsa','Eastern Whisper','Velvet Outlaw','Folie Golith','Bateleur','Tunisya','Kahavari'}
COUNTRY=re.compile(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN|POL)\)\s*$',re.I)

def identity(s):
    return A.normname(COUNTRY.sub('',str(s or '').strip()))

def track(s):return D.OLD.track(s)

def val(s):
    try:
        n=float(str(s or '').strip())
        return n if n==n else None
    except (TypeError,ValueError):return None

def length_f(p, fallback):
    for z in (p,fallback):
        s=str(z or '').lower().strip().removesuffix('f').strip()
        v=val(s)
        if v and v>=4:return v
    return None

def npos(z):
    try:
        n=int(str(z)); return n if n>0 else None
    except (ValueError,TypeError):return None

def country(p,f):
    for r in (p,f):
        v=str(r.get('horse_country') or '').strip().lower()
        if v:return v
        s=str(r.get('horse') or '')
        q=re.search(r'\(([A-Z]{2,3})\)\s*$',s,re.I)
        if q:return q.group(1).lower()
    return ''

def pretty(rows, dest):
    cols=sorted({k for z in rows for k in z}) if rows else ['horse','status']
    with dest.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rows)

def keymap(aliases,cards):
    return {(A.normname(r['horse']),str(r.get('horse_id') or '').strip()):aliases.get((A.normname(r['horse']),str(r.get('horse_id') or '').strip()),{}) for r in cards}

def compare_aliases(pre,post,cards):
    reg=[]; dnew=[]; cnew=[]
    for r in cards:
        k=(A.normname(r['horse']),str(r.get('horse_id') or '').strip()); b=pre.get(k,{}) or {}; a=post.get(k,{}) or {}
        for model in ('D','C'):
            field=model+'_key'
            if b.get(field) and b[field]!=a.get(field):
                reg.append({'horse':r['horse'],'model':model,'previous':str(b[field]),'after':str(a.get(field))})
            if not b.get(field) and a.get(field):
                (dnew if model=='D' else cnew).append(r['horse'])
    return reg,sorted(set(dnew)),sorted(set(cnew))

def source_race(c):
    loc=ROOT/c['result_source_file']
    doc=json.loads(loc.read_text())
    ans=[r for r in doc.get('results',[]) if str(r.get('race_id') or '')==c['source_race_id'] and str(r.get('date') or '')[:10]==c['source_date']]
    if len(ans)!=1:raise ValueError('Final race ID and date not uniquely located')
    return ans[0],loc

def starters(src):
    return [r for r in src['runners'] if str(r.get('status') or '').lower() not in ('nr','nonrunner','non-runner','withdrawn','scratched')
            and str(r.get('position') or '').upper() not in ('NR','DNS')]

def snapshot_candidates(shas,cache,src):
    off=D.time_utc(src.get('off_dt'))
    if not off:raise ValueError('No independently recorded final off time')
    cutoff=int(off.timestamp())
    relevant=[(t,sha) for t,sha in shas if t<cutoff and t>cutoff-4*24*3600]
    # Try the most recently available snapshot first; try earlier preoff versions
    # only where source runner IDs or metadata did not match.
    for t,sha in relevant[-35:][::-1]:
        if sha not in cache:
            try:cache[sha]=json.loads(K.git('show',sha+':data/racecards.json'))
            except Exception:cache[sha]={}
        pre=cache[sha]
        cards=[u for u in pre.get('runners',[]) if str(u.get('date') or u.get('race_time') or '')[:10]==src['date']
               and track(u.get('course'))==track(src['course'])
               and B.normtime(u.get('race_time'))==off.strftime('%H:%M') and not D.nonrunner(u)]
        if cards:yield cards,sha,t

def report_row(c,status,detail='',**extra):
    return {'horse':c['horse'],'horse_id':c['horse_id'],'course':c['current_course'],
            'source_date':c['source_date'],'source_race_id':c['source_race_id'],
            'status':status,'detail':detail,**extra}

def extract_safe_candidate(c,shas,cache,current_byid):
    if c['horse'] not in TRY:
        return None,report_row(c,'HELD_ALREADY_PROMOTED_OR_PREVIOUS_IDENTITY_CONFLICT')
    card=current_byid.get(str(c['horse_id']))
    if not card or identity(card['horse'])!=identity(c['horse']):
        return None,report_row(c,'NO_UNIQUE_CURRENT_PROVIDER_CARD_ID')
    src,srcfile=source_race(c)
    if src.get('status')!='final' or src['date']>=DAY or track(src.get('course')) not in D.OLD.TRACKS:
        return None,report_row(c,'NOT_FINAL_BEFORE_DAY_OR_NOT_UK_IE_RULES')
    result=starters(src)
    if len(result)<2 or len({identity(r['horse']) for r in result})!=len(result):
        return None,report_row(c,'FINAL_RESULT_MISSING_OR_DUPLICATE_RUNNERS')
    finalids=[str(r.get('horse_id') or '') for r in result]
    if any(not s for s in finalids) or len(set(finalids))!=len(finalids):
        return None,report_row(c,'FINAL_RESULT_MISSING_IDS')
    if sum(npos(r.get('position'))==1 for r in result)!=1:
        return None,report_row(c,'FINAL_RESULT_WINNER_NOT_UNIQUE')
    target=[r for r in result if str(r['horse_id'])==c['horse_id']]
    if len(target)!=1 or identity(target[0].get('horse'))!=identity(card['horse']):
        return None,report_row(c,'TARGET_PROVIDER_ID_NAME_CONFLICT')
    if not all(identity(card.get(z)) and identity(target[0].get(z)) and
               identity(card[z])==identity(target[0][z]) for z in ('sire','dam')):
        return None,report_row(c,'PEDIGREE_NOT_BOTH_PROVEN',
                               card_sire=str(card.get('sire')),final_sire=str(target[0].get('sire')),
                               card_dam=str(card.get('dam')),final_dam=str(target[0].get('dam')))
    # A complete final result is a valid historical record for a later race,
    # but pre-off snapshot must independently prove that THESE horses were declared.
    # Do not fabricate withdrawn runners, missing horses or results.
    final_by_id={str(r['horse_id']):r for r in result}
    good=[];possible=[]
    for cards,sha,ts in snapshot_candidates(shas,cache,src):
        prior_ids={str(r.get('horse_id') or '') for r in cards}
        if prior_ids!=set(finalids):
            possible.append({'commit':sha,'pre':len(cards),'final':len(result),'ids_match':False})
            continue
        if len(cards)!=len(result) or any(identity(r.get('horse'))!=identity(final_by_id[str(r['horse_id'])].get('horse')) for r in cards):
            possible.append({'commit':sha,'pre':len(cards),'final':len(result),'ids_match':False})
            continue
        good.append((cards,sha,ts))
        if len(good)>=1:break
    if not good:
        return None,report_row(c,'NO_EXACT_PREOFF_STARTER_SET',f'Latest candidates {possible[:2]}')
    cards,sha,ts=good[0]
    meta=[];fallback=collections.Counter()
    for u in cards:
        r=final_by_id[str(u['horse_id'])]
        # Final metadata describes the completed historical race; it is not an outcome or future signal.
        # Only use final fields if the corresponding pre-off snapshot field is absent.
        item=dict(u)
        for name in ('trainer','jockey','age','sex','sire','dam','weight_lbs','or'):
            if not item.get(name) and r.get(name):item[name]=r[name];fallback[name]+=1
        if not item.get('distance_f'):
            item['distance_f']=length_f(src.get('dist_f'),src.get('distance_f'));fallback['distance_f']+=1
        if not (item.get('type') or item.get('race_name')):
            item['type']=src.get('type') or src.get('race_name');fallback['race_type']+=1
        for name,source_name in (('going','going'),('surface','surface'),('race_class','class')):
            if not item.get(name) and src.get(source_name):item[name]=src[source_name];fallback[name]+=1
        if not item.get('trainer') or not item.get('jockey') or not length_f(item.get('distance_f'),src.get('dist_f')) or not (item.get('type') or item.get('race_name')):
            return None,report_row(c,'UNRECOVERABLE_OFFICIAL_METADATA',f"Missing for {u['horse']}")
        for z in ('sire','dam'):
            if identity(u.get(z)) and identity(r.get(z)) and identity(u[z])!=identity(r[z]):
                return None,report_row(c,'STARTER_PEDIGREE_DISAGREEMENT',f"{u['horse']} {z}")
        item['distance_f']=length_f(item.get('distance_f'),src.get('dist_f'))
        if not item.get('sire'):item['sire']=r.get('sire')
        if not item.get('dam'):item['dam']=r.get('dam')
        meta.append(item)
    for r in result:
        p=str(r.get('position') or '').upper()
        if npos(p) is None and p not in ('PU','F','UR','BD','RO','RR','REF','DSQ','DNF'):
            return None,report_row(c,'UNKNOWN_HISTORICAL_RESULT_OUTCOME',p)
    event={'target_horse':c['horse'],'target_horse_id':c['horse_id'],
           'preoff_commit':sha,'preoff_ts':ts,'results':src,
           'preoff_runners':cards,'enriched_preoff_runners':meta,
           'source_result_path':str(srcfile.relative_to(ROOT)),
           'result_sha256':hashlib.sha256(srcfile.read_bytes()).hexdigest(),
           'final_metadata_fallback_counts':dict(fallback)}
    return event,report_row(c,'SOURCE_PREOFF_IDS_AND_PEDIGREE_VERIFIED',
                            f"{len(cards)} complete starters; official final metadata fallback: {dict(fallback)}",
                            preoff_commit=sha,full_field_size=len(cards))

def duplicate_check(db,evt):
    s=evt['results']; names={identity(r['horse']) for r in starters(s)}
    cx=sqlite3.connect(db)
    try:
        rids=cx.execute('SELECT id,course FROM historical_races WHERE race_date=?',(s['date'],)).fetchall()
        for rid,co in rids:
            if track(co)!=track(s['course']):continue
            seen={identity(z[0]) for z in cx.execute('SELECT horse_name FROM historical_runs WHERE race_id=?',(rid,))}
            if seen==names:return 'FULL_HISTORICAL_RACE_ALREADY_PRESENT'
            if seen&names:return 'PARTIAL_RACE_OVERLAP_REQUIRES_RECONCILIATION'
        if cx.execute('SELECT 1 FROM historical_races WHERE id=?',('STAGE126|'+s['date']+'|'+s['race_id'],)).fetchone():
            return 'SOURCE_RACE_ID_ALREADY_PRESENT'
        if cx.execute('''SELECT 1 FROM historical_runs u JOIN historical_races r ON r.id=u.race_id
                         WHERE r.race_date=? AND u.horse_source_id=? LIMIT 1''', (s['date'],evt['target_horse_id'])).fetchone():
            return 'DUPLICATE_TARGET_PROVIDER_HORSE_START'
    finally:cx.close()
    return ''

def add_event_to_temp(db,evt):
    s=evt['results']; rid='STAGE126|'+s['date']+'|'+s['race_id']
    byid={str(u['horse_id']):u for u in starters(s)}
    first=evt['enriched_preoff_runners'][0]
    cx=sqlite3.connect(db)
    try:
        with cx:
            cx.execute('''INSERT INTO historical_races(id,race_date,course,race_type,surface,distance_f,going,race_class,source)
                       VALUES(?,?,?,?,?,?,?,?,?)''',
                       (rid,s['date'],s['course'],first.get('type') or first.get('race_name'),
                        first.get('surface') or s.get('surface'),
                        length_f(first.get('distance_f'),s.get('dist_f')),
                        first.get('going') or s.get('going'),first.get('race_class') or s.get('class'),
                        'STAGE126-RESEARCH-EXACT-PREOFF-ID-AND-OFFICIAL-FINAL'))
            for i,u in enumerate(evt['enriched_preoff_runners']):
                f=byid[str(u['horse_id'])]; pos=npos(f.get('position'))
                h=COUNTRY.sub('',str(u['horse'])).strip(); co=country(u,f)
                cx.execute('''INSERT INTO historical_runs(race_id,runner_key,horse_source_id,horse_name,horse_name_norm,
                           horse_country,finish_pos,outcome,trainer,jockey,official_rating,weight_lbs,sp_decimal,
                           draw,age,sex,sire,dam) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (rid,str(i),str(u['horse_id']),h,A.normname(h),co,pos,'FINISHED' if pos else str(f['position']).upper(),
                        u['trainer'],u['jockey'],val(u.get('official_rating') or u.get('ofr') or u.get('or')),
                        val(u.get('weight_lbs') or u.get('lbs')),H.sp_decimal(f),npos(u.get('draw')),
                        npos(u.get('age')),u.get('sex'),u.get('sire'),u.get('dam')))
    finally:cx.close()
    return rid

def undo_event(db,rid):
    cx=sqlite3.connect(db)
    try:
        with cx:
            cx.execute('DELETE FROM historical_runs WHERE race_id=?',(rid,))
            cx.execute('DELETE FROM historical_races WHERE id=?',(rid,))
    finally:cx.close()

def compare_published_forecast(published,candidate):
    races=lambda d:{(str(r['course']),str(r['time'])):r for r in d['races']}
    p=races(published);q=races(candidate)
    if set(p)!=set(q):raise RuntimeError('D scorer changed racecards/race count')
    changes=[];rankrows=[]
    for k in sorted(p):
        before=sorted(p[k]['runners'],key=lambda u:u['rank']);after=sorted(q[k]['runners'],key=lambda u:u['rank'])
        if {z['horse_id'] for z in before}!={z['horse_id'] for z in after}:
            raise RuntimeError('D scorer changed horse IDs or runners in '+str(k))
        a={z['horse_id']:z for z in after}
        for z in before:
            zz=a[z['horse_id']]
            if z['rank']!=zz['rank'] or z.get('evidence_count')!=zz.get('evidence_count'):
                rankrows.append({'course':k[0],'time':k[1],'horse':z['horse'],
                      'rank_before':z['rank'],'rank_after':zz['rank'],'evidence_before':z.get('evidence_count'),
                      'evidence_after':zz.get('evidence_count')})
        if before[0]['horse_id']!=after[0]['horse_id'] or before[1]['horse_id']!=after[1]['horse_id']:
            changes.append({'course':k[0],'time':k[1],'before_rank1':before[0]['horse'],
                            'after_rank1':after[0]['horse'],'before_rank2':before[1]['horse'],
                            'after_rank2':after[1]['horse']})
    return changes,rankrows

def main():
    assert os.environ.get('STAGE126_DB'), 'Stage126 historical master artifact not provided'
    assert (ROOT/'data/stage125-verified-prior-races.json').is_file(), 'Stage125 live bridge not installed'
    assert hasattr(V,'_stage125_original_ensure_fresh_result_db'), 'Live Stage125 shared hook not installed'
    assert all(identity(x)==identity(x+' (POL)') for x in ('Tunis','Tunisya'))
    assert identity('Porte D\'Auteuil (FR)')==identity('Porte D\'Auteuil')
    pinned_cards=OUT/'pinned-nov14-racecards.json'
    pinned_cards.write_bytes(subprocess.check_output(['git','show',PIN+':data/racecards.json']))
    published=json.loads(subprocess.check_output(['git','show',PIN+':data/strategy-d-probabilities.json'],text=True))
    cards=V.read_cards(pinned_cards,DAY)
    assert len(cards)==341 and published['day']==DAY and len(published['races'])==36
    assert published['verified_form_recovery']['D_verified']==283
    base,meta=V.ensure_fresh_result_db(os.environ['STAGE126_DB'],DAY,ROOT)
    prior,stats=V.resolve_verified(base,cards,DAY)
    assert stats['D_verified']==285 and stats['C_verified']==160, f'Stage125 live baseline drift; manual audit needed: {stats}'
    assert meta['stage125_verified_history']['status'] in ('ADDED_VERIFIED_FULL_RACES','ALREADY_PRESENT')
    scratch=OUT/'stage126-ephemeral-recovery.sqlite'
    shutil.copy2(base,scratch)
    assert scratch.stat().st_size>10_000_000
    current_byid={str(c.get('horse_id') or ''):c for c in cards}
    stage121=list(csv.DictReader((SOURCE/'unresolved-identity-detail.csv').open()))
    candidates=list(csv.DictReader((SOURCE/'strict-identity-candidates.csv').open()))
    assert len(stage121)==58 and len(candidates)==12
    target_names={r['horse'] for r in stage121}
    assert len(target_names)==58
    shas=K.snapshots();cache={};out=[];approved=[]
    baseline=stats; now=prior
    for c in candidates:
        try:evt,row=extract_safe_candidate(c,shas,cache,current_byid)
        except Exception as ex:
            out.append(report_row(c,'SOURCE_EXTRACTION_EXCEPTION',str(ex)[:250]));continue
        if not evt:
            out.append(row);continue
        duplicate=duplicate_check(scratch,evt)
        if duplicate:
            row.update(status=duplicate);out.append(row);continue
        rid=None
        try:
            rid=add_event_to_temp(scratch,evt)
            post,counts=V.resolve_verified(str(scratch),cards,DAY)
            regression,dgain,cgain=compare_aliases(now,post,cards)
            if regression:
                undo_event(scratch,rid);row.update(status='HELD_WOULD_REGRESS_VERIFIED_KEY',detail=json.dumps(regression[:3]));out.append(row);continue
            if counts['D_verified']<baseline['D_verified'] or counts['C_verified']<baseline['C_verified']:
                undo_event(scratch,rid);row.update(status='HELD_WOULD_LOSE_COVERAGE');out.append(row);continue
            if c['horse'] not in dgain and c['horse'] not in cgain:
                undo_event(scratch,rid);row.update(status='NO_TARGET_IDENTITY_RECOVERY',detail='Source complete but missing D/C coverage not repaired');out.append(row);continue
            row.update(status='TESTED_SAFE_FULL_RACE',D_gain='|'.join(dgain),C_gain='|'.join(cgain))
            out.append(row);approved.append(evt);now=post;baseline=counts
            print('STAGE126 SAFELY ADDED TO TEMP DB',c['horse'],rid,'D',counts['D_verified'],'C',counts['C_verified'],flush=True)
        except Exception as ex:
            if rid:
                try:undo_event(scratch,rid)
                except Exception:pass
            out.append(report_row(c,'RESEARCH_INSERT_EXCEPTION',str(ex)[:300]))
    # Check complete original identity preservation, not just adjacent candidates.
    finalreg,dnew,cnew=compare_aliases(prior,now,cards)
    assert not finalreg,finalreg
    assert len(dnew)==baseline['D_verified']-stats['D_verified']
    assert len(cnew)==baseline['C_verified']-stats['C_verified']
    assert len({e['target_horse_id'] for e in approved})==len(approved)
    pretty(out,OUT/'each-strict-source-proof.csv')

    tested={x['horse'] for x in out if x['status']=='TESTED_SAFE_FULL_RACE'}
    statuses=[]
    for r in stage121:
        horse=r['horse']
        original=r['classification']
        if horse in ('Ocean View','The Rarest Hour'):s='LIVE_RECOVERED_STAGE125'
        elif horse in tested:s='TEMP_RECOVERED_STAGE126'
        elif r['has_public_prior_form']=='0':s='POSSIBLE_DEBUT_NOT_PROVEN'
        elif original=='CONFLICTING_D_HISTORY_KEYS':s='PRIOR_ARCHIVE_KEY_CONFLICT_REQUIRES_IDENTITY_PROOF'
        elif original=='NAME_FOUND_BUT_ID_AND_PEDIGREE_UNVERIFIED':s='ARCHIVE_NAME_COLLISION_NEEDS_PEDIGREE'
        else:s='FORM_EXISTS_NOT_YET_RECOVERED'
        statuses.append({'horse':horse,'horse_id':r['horse_id'],'course':r['course'],'time':r['time'],
             'form':r['form'],'last_run':r['last_run'],'sire':r['sire'],'dam':r['dam'],
             'original_classification':original,'working_status':s,
             'prior_form_on_card':r['has_public_prior_form'],
             'is_verified_debutant':'NO',
             'note':'No archive record is NOT conclusive debutant proof' if s=='POSSIBLE_DEBUT_NOT_PROVEN' else ''})
    pretty(statuses,OUT/'full-58-runner-disposition.csv')
    byclass=dict(collections.Counter(x['working_status'] for x in statuses))
    # Save exact immutable final + source card evidence for a future promotion stage.
    fixture={'version':'stage126-verified-research-v1','target':DAY,
             'note':'Research-only; promote only after independent replay, no regressions and model D frozen-score parity',
             'evidence':[{'target_horse':e['target_horse'],'target_horse_id':e['target_horse_id'],
                 'preoff_commit':e['preoff_commit'],'source_result_path':e['source_result_path'],
                 'result_sha256':e['result_sha256'],'final_metadata_fallback_counts':e['final_metadata_fallback_counts'],
                 'results':e['results'],'preoff_runners':e['preoff_runners'],
                 'enriched_preoff_runners':e['enriched_preoff_runners']} for e in approved]}
    (OUT/'verified-recovery-fixtures.json').write_text(json.dumps(fixture,indent=2,ensure_ascii=False)+'\n')
    score={'status':'NO_ADDITIONS','local_rank1_rank2_changes':[],'local_runner_rank_or_evidence_changes':[]}
    if approved:
        # Score exactly the same 14 November declared card that yielded original D predictions.
        # GitHub runner checkout is disposable; this never commits live source data.
        shutil.copy2(pinned_cards,ROOT/'data/racecards.json')
        subprocess.run([sys.executable,'scripts/stage38_model_dz_live.py','live','--repo','.','--db',str(scratch),'--day',DAY],check=True)
        candidate=json.loads((ROOT/'data/strategy-d-probabilities.json').read_text())
        changes,rankrows=compare_published_forecast(published,candidate)
        assert candidate['model_version']==published['model_version'],'Frozen D source version changed'
        assert candidate['weights_pct']==published['weights_pct'],'Frozen D weights changed'
        assert candidate['verified_form_recovery']['D_verified']==baseline['D_verified'],'Scorer ignored new verified history'
        assert len(candidate['races'])==36 and sum(len(r['runners']) for r in candidate['races'])==341
        score={'status':'VALIDATED_LOCAL_SCORE','candidate_D_verified':baseline['D_verified'],
               'same_frozen_model_version':True,'same_weights':True,
               'local_rank1_rank2_changes':changes,
               'local_runner_rank_or_evidence_changes':rankrows,'published':False}
        pretty(rankrows,OUT/'all-rank-or-evidence-changes.csv')
    (OUT/'model-d-frozen-rank-comparison.json').write_text(json.dumps(score,indent=2)+'\n')
    summary={'stage':'126b','mode':'READ_ONLY_SAFETY_GATED_MULTI_SOURCE_AUDIT','day':DAY,
        'baseline_D':stats['D_verified'],'tested_D':baseline['D_verified'],
        'baseline_C':stats['C_verified'],'tested_C':baseline['C_verified'],
        'verified_D_gains':dnew,'verified_C_gains':cnew,
        'newly_safe_complete_races':len(approved),'newly_safe_horses':[e['target_horse'] for e in approved],
        'safe_recovery_statuses':dict(collections.Counter(r['status'] for r in out)),
        'all_58_disposition':byclass,
        'still_unverified_from_original_58':58-(stats['D_verified']-283)-len(dnew),
        'possible_debutants_NOT_confirmed':sum(x['working_status']=='POSSIBLE_DEBUT_NOT_PROVEN' for x in statuses),
        'original_D_C_identity_regressions':finalreg,'master_modified':False,
        'production_published':False,'ranking_smoke':score}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('STAGE126b FINAL RESEARCH AUDIT\n'+json.dumps(summary,indent=2),flush=True)
    assert not finalreg
    assert baseline['D_verified']>=285 and baseline['C_verified']>=160
    assert all(e['results']['date']<DAY for e in approved)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as fh:
        fh.write('## Stage 126b: all remaining form identities and debutant evidence\n\n')
        fh.write(f"- Verified D (test only): {stats['D_verified']} -> {baseline['D_verified']} /341\n")
        fh.write(f"- Verified C (test only): {stats['C_verified']} -> {baseline['C_verified']} /341\n")
        fh.write(f"- Safe additional completed races: {len(approved)}; horses: {dnew}\n")
        fh.write(f"- Still unverified potential debutants (NOT confirmed): {summary['possible_debutants_NOT_confirmed']}\n")
        fh.write('- Source master, original D weights and production files unchanged.\n')

if __name__=='__main__':main()


def apply_installed_verified_history(db,target,repo):
    """Merge only pre-cutoff, Stage126-proven full races into a DISPOSABLE SQLite copy."""
    import tempfile
    root=pathlib.Path(repo).resolve();fixture_path=root/'data/stage126-verified-prior-races.json'
    if not fixture_path.is_file():return db,{'status':'NOT_INSTALLED','added_races':0}
    bundle=json.loads(fixture_path.read_text())
    if bundle.get('version')!='stage126-verified-research-v1':raise RuntimeError('Unrecognized installed Stage126 bundle')
    events=bundle.get('evidence',[])
    if len(events)!=len({(str(e['results']['date']),str(e['results']['race_id'])) for e in events}):
        raise RuntimeError('Duplicate/ambiguous race in Stage126 live bundle')
    assert all(e['target_horse'] in TRY and e['target_horse'] not in LIMITED for e in events)
    assert all(e['target_horse_id'] and re.fullmatch(r'[a-f0-9]{40}',e['preoff_commit']) for e in events)
    live=[e for e in events if dt.date.fromisoformat(str(e['results']['date']))<dt.date.fromisoformat(target)]
    if not live:return db,{'status':'ALL_PRE_CUTOFF_HOLD','added_races':0}
    # Validate complete, exact-ID/whole-field fixture against the immutable final evidence.
    for e in live:
        s=e['results'];ps=e['preoff_runners'];en=e['enriched_preoff_runners']
        r=starters(s);ids={str(z['horse_id']) for z in r}
        if not(s['status']=='final' and len(r)>=2 and len(ids)==len(r)==len(ps)==len(en)):
            raise RuntimeError('Stage126 fixture race has inconsistent starters')
        if ids!={str(z.get('horse_id') or '') for z in ps}:raise RuntimeError('Preoff source provider IDs differ')
        if sum(npos(z.get('position'))==1 for z in r)!=1:raise RuntimeError('Source has no unique winner')
        if not any(z['horse_id']==e['target_horse_id'] and identity(z['horse'])==identity(e['target_horse']) for z in r):
            raise RuntimeError('Target exact provider ID or horse name differs')
        rids={str(z['horse_id']):z for z in r}
        for p in en:
            f=rids[str(p['horse_id'])]
            if identity(p['horse'])!=identity(f['horse']):raise RuntimeError('Historical runner name differs')
            if not p.get('trainer') or not p.get('jockey') or not length_f(p.get('distance_f'),s.get('dist_f')):
                raise RuntimeError('Verified historical event metadata incomplete')
            if not(p.get('type') or p.get('race_name')):raise RuntimeError('Verified historical race type missing')
            for z in ('sire','dam'):
                if identity(p.get(z)) and identity(f.get(z)) and identity(p[z])!=identity(f[z]):
                    raise RuntimeError('Verified runner pedigree disagreement')
    src=pathlib.Path(db).resolve()
    todo=[];skips=[]
    for e in live:
        reason=duplicate_check(src,e)
        if reason=='FULL_HISTORICAL_RACE_ALREADY_PRESENT':skips.append(e['results']['race_id']);continue
        if reason:raise RuntimeError('Unsafe Stage126 duplicate/overlap: '+reason)
        todo.append(e)
    if not todo:return str(src),{'status':'ALREADY_PRESENT','added_races':0,'skipped':skips}
    folder=pathlib.Path(tempfile.mkdtemp(prefix='stage126-added-precutoff-'))
    dest=folder/'history.sqlite';shutil.copy2(src,dest)
    inserted=[]
    for e in todo:
        reason=duplicate_check(dest,e)
        if reason:raise RuntimeError('Unsafe multi-event overlap in Stage126 live bridge: '+reason)
        inserted.append(add_event_to_temp(dest,e))
    return str(dest),{'status':'ADDED_PROVEN_HISTORICAL_FULL_RACES','added_races':len(inserted),
        'race_ids':inserted,'source':'data/stage126-verified-prior-races.json','original_master_untouched':True}
