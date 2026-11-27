"""Exact dated final result identities, verified independently of declaration cards."""
from pathlib import Path
from functools import lru_cache
import json

@lru_cache(maxsize=1)
def records():
    p=Path(__file__).resolve().parent.parent/'data/stage210-verified-person-identities.json'
    doc=json.loads(p.read_text())
    assert doc['schema']=='stage210-dated-final-identities-v1'
    out={x['key']:x for x in doc['records']}
    assert len(out)==len(doc['records'])==35
    return out

def apply(key,race,runner,ref,person_key):
    ev=records().get(key)
    if not ev or race.get('status')!='final' or str(runner.get('position')).upper()!=ev['position']:
        return runner,ref,False
    # Do not override a contradictory later explicit final result. Do not copy
    # stale declaration IDs for a replaced person. Only a name-confirmed ID survives.
    if any(runner.get(k) and person_key(runner[k])!=person_key(ev[k]) for k in ('trainer','jockey')):
        return runner,ref,False
    row=dict(runner)
    for k in ('trainer','jockey'):
        ident=''
        if runner.get(k) and person_key(runner[k])==person_key(ev[k]):
            ident=str(runner.get(k+'_id') or '')
        if not ident and ref and person_key(ref.get(k))==person_key(ev[k]):
            ident=str(ref.get(k+'_id') or '')
        row[k]=ev[k]
        if ident: row[k+'_id']=ident
        else: row.pop(k+'_id',None)
    row['stage210_person_evidence']=ev['source']
    return row,None,True
