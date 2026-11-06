#!/usr/bin/env python3
"""Research-only frozen Model D replay with recovered race metadata.

No trend scoring, no weight changes, no production writes. Only race_type
metadata is patched; race names are retained for audit but are NOT consumed
by the frozen D scorer. Requires the existing Stage49 runner replay.
"""
import argparse,csv,json,sqlite3,shutil,subprocess,sys,re
from pathlib import Path
from collections import Counter

def classify(name,code,conditions):
    s=' '.join(str(x or '') for x in (name,code,conditions)).lower()
    # Only categorical types supported by the existing frozen scorer.
    if re.search(r'\b(chase|steeplechase|steeple)\b',s): kind='Chase'
    elif re.search(r'\bhurdles?\b',s): kind='Hurdle'
    elif re.search(r'\b(bumper|national hunt flat|nh flat)\b',s):kind='National Hunt Flat'
    elif re.search(r'\b(flat|maiden|handicap|stakes|nursery|novice)\b',s):kind='Flat'
    else:return None
    tags=[]
    for key in ('handicap','novice','maiden','juvenile','seller','claimer','listed','graded','group'):
        if re.search(r'\b'+key+r'\b',s):tags.append(key)
    return ' '.join(tags+[kind])

def load(mapping):
    found={};audit=Counter()
    with mapping.open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            rid=row.get('stage2_race_id') or row.get('race_id')
            if not rid:continue
            kind=classify(row.get('race_name'),row.get('code'),row.get('conditions'))
            if not kind:audit['unclassified']+=1;continue
            if rid in found and found[rid][0]!=kind:
                audit['conflicts']+=1;found.pop(rid,None);continue
            found[rid]=(kind,row.get('race_name') or '')
            audit['mapped']+=1
    return found,dict(audit)

def metrics(rows,col):
    a=[r for r in rows if r.get(col+'_sp') not in ('',None)]
    wins=sum(str(r.get(col+'_finish'))=='1' for r in a)
    gross=sum(float(r[col+'_sp']) for r in a if str(r.get(col+'_finish'))=='1')
    return {'races':len(rows),'priced_stakes':len(a),'wins':wins,'strike_rate_pct':round(100*wins/len(a),3) if a else None,'gross_return':round(gross,3),'roi_pct':round(100*(gross/len(a)-1),3) if a else None}

def main(a):
    a.out.mkdir(parents=True,exist_ok=True)
    lookup,audit=load(a.mapping)
    if not lookup:raise SystemExit('No usable metadata mapping')
    # Rebuild a research-only SQLite copy, never mutate the input DB.
    patched=a.out/'stage62_research_copy.sqlite'
    shutil.copy2(a.db,patched)
    cx=sqlite3.connect(patched)
    ids={r[0]:r[1] for r in cx.execute('SELECT id,race_type FROM historical_races')}
    updates=[];same=0;missing=0;changes=Counter()
    for rid,(new,name) in lookup.items():
        if rid not in ids:missing+=1;continue
        old=ids[rid] or ''
        if old.strip().lower()==new.lower():same+=1;continue
        # Preserve clearly identified original jump categories if recovery disagrees.
        jump=lambda s: 'chase' if 'chase' in s.lower() else 'hurdle' if 'hurdle' in s.lower() else None
        if jump(old) and jump(new) and jump(old)!=jump(new):
            changes['contradictory_jump_type_skipped']+=1;continue
        updates.append((new,rid));changes['updated']+=1
    cx.executemany('UPDATE historical_races SET race_type=? WHERE id=?',updates)
    cx.commit();cx.close()
    # Run exact original Stage49 scoring implementation, not Stage60/61.
    subprocess.run([sys.executable,'-u','scripts/stage49_model_d_top_two.py','--db',str(patched),'--output',str(a.out/'patched')],check=True)
    patched.unlink(missing_ok=True)
    with a.baseline.open(encoding='utf-8-sig',newline='') as f:before=list(csv.DictReader(f))
    with (a.out/'patched'/'model_d_top_two_2025_2026_per_race.csv').open(encoding='utf-8-sig',newline='') as f:after=list(csv.DictReader(f))
    key=lambda r:(r['date'],r['race_id'])
    b={key(r):r for r in before};c={key(r):r for r in after};common=sorted(b.keys()&c.keys())
    pairs=[]
    for k in common:
        x,y=b[k],c[k]
        pairs.append({'date':k[0],'race_id':k[1],'discipline_before':x['discipline'],'discipline_after':y['discipline'],
                      'rank1_before':x['rank1_horse'],'rank1_after':y['rank1_horse'],
                      'rank1_finish_before':x['rank1_finish'],'rank1_finish_after':y['rank1_finish'],
                      'rank1_sp_before':x['rank1_sp'],'rank1_sp_after':y['rank1_sp'],
                      'changed':int(x['rank1_horse']!=y['rank1_horse'])})
    with (a.out/'stage62_comparison.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=pairs[0].keys() if pairs else ['date']);w.writeheader();w.writerows(pairs)
    report={'status':'RESEARCH_ONLY','experiment':'exact frozen Stage49 D rerun with race_type metadata patched',
            'mapping_audit':audit,'mapping_ids_absent_from_database':missing,'already_same':same,'patch_audit':dict(changes),
            'baseline_races':len(before),'patched_races':len(after),'common_races':len(common),
            'unmatched_baseline':len(b)-len(common),'unmatched_patched':len(c)-len(common),
            'rank1_changed':sum(r['changed'] for r in pairs),
            'baseline_rank1':metrics([b[k] for k in common],'rank1'),
            'patched_rank1':metrics([c[k] for k in common],'rank1'),
            'baseline_rank2':metrics([b[k] for k in common],'rank2'),
            'patched_rank2':metrics([c[k] for k in common],'rank2'),
            'limitations':['Recovered names are audited but not used by frozen D scorer; only race_type changes can affect its existing three-way discipline classification.',
                           'Stage59 metadata covers a subset of historical races and contains candidate identities, not all independently verified.',
                           'Stage49 replay requires same historical source as baseline for a fair comparison.',
                           'No trend adjustments, no retraining, no live app changes.']}
    (a.out/'stage62_summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--mapping',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--out',type=Path,default=Path('reports/stage62'));main(p.parse_args())
