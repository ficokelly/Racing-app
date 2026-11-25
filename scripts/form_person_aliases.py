from pathlib import Path
import json,re,unicodedata
from functools import lru_cache
PATH=Path(__file__).resolve().parent.parent/'data/form-person-aliases.json'
def raw_key(s):
    s=re.sub(r'\s*\(\d+\)\s*$','',str(s or ''))
    s=unicodedata.normalize('NFKD',s)
    return re.sub(r'[^a-z0-9]+','', ''.join(c for c in s if not unicodedata.combining(c)).lower())
@lru_cache(maxsize=1)
def aliases():
    doc=json.loads(PATH.read_text())
    if doc.get('schema')!='form-person-aliases-v1':raise ValueError('Unexpected person registry schema')
    out={}
    for e in doc['entries']:
        assert e['kind'] in ('trainer','jockey') and e['evidence_url'].startswith('https://')
        k,v=raw_key(e['alias']),raw_key(e['canonical'])
        assert k and v and (k not in out or out[k]==v), ('Conflicting alias',k)
        out[k]=v
    assert all(out.get(v,v)==v for v in out.values()), 'Alias chains require explicit review'
    return out

def person_key(s):
    k=raw_key(s)
    return aliases().get(k,k)
