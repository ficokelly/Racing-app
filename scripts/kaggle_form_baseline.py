"""Use approved daily Kaggle aggregates as a replacement, never an additive overlay.
Raw historical rows remain in the workflow runner; no raw CSVs are published.
"""
from collections import Counter, defaultdict
from pathlib import Path
import datetime as dt, json

BASELINE=Path('data/kaggle-form-baseline.json')

def merge(output, rows, dates, as_of, audit, build_form, compact):
    if not BASELINE.exists(): return output
    doc=json.loads(BASELINE.read_text())
    if doc.get('schema')!='kaggle-form-baseline-v1': raise ValueError('Invalid baseline schema')
    lower=(as_of-dt.timedelta(days=13)).isoformat(); end=as_of.isoformat()
    approved={d:v for d,v in doc['approved_days'].items() if lower<=d<=end}
    if not approved: return output
    api_rows=[r for r in rows if r['date'] not in approved]
    result=build_form(api_rows,as_of,set(dates)-approved.keys(),audit)
    for n,period in result['periods'].items():
        start=(as_of-dt.timedelta(days=int(n)-1)).isoformat()
        use={d:v for d,v in approved.items() if d>=start}
        for kind,group,denom in [('trainer','trainers','runs'),('jockey','jockeys','rides')]:
            names=defaultdict(list)
            for key,stat in output['periods']['14'][group].items(): names[compact(stat['name'])].append(key)
            merged={compact(stat['name']):dict(stat) for stat in period[group].values()}
            ids={compact(stat['name']):key for key,stat in period[group].items()}
            for day in use.values():
                for nm,stat in day[group].items():
                    item=merged.setdefault(nm,{'name':stat['name'],denom:0,'wins':0})
                    item[denom]+=stat['runs']; item['wins']+=stat['wins']
            new={}
            for nm,stat in sorted(merged.items()):
                key=ids.get(nm) or (names[nm][0] if len(names[nm])==1 else 'name:'+nm)
                if key in new: key='name:'+nm
                if key in new: raise ValueError('Duplicate aggregate identity')
                assert 0<=stat['wins']<=stat[denom] and stat[denom]>0
                stat['strike_rate']=round(100*stat['wins']/stat[denom],1)
                new[key]=stat
            period[group]=new
        period['runners']+=sum(v['starters'] for v in use.values())
        period['dates_available']=sorted(set(period['dates_available'])|use.keys())
        # Each approved Kaggle day was required to have zero missing identities.
        period['kaggle_days']=sorted(use)
        period['api_days']=sorted(set(period['dates_available'])-use.keys())
        for kind,group,denom in [('trainer','trainers','runs'),('jockey','jockeys','rides')]:
            assert sum(x[denom] for x in period[group].values())+period['identity_gaps'][kind]==period['runners'], (n,kind)
    result['source']='Validated Kaggle historical day aggregates + daily API results bridge'
    result['kaggle_version']=doc['version']; result['kaggle_cutoff']=doc['cutoff']
    result['methodology']='Whole-day replacement after runner/finish/person and race coverage checks; no duplicate days; API retained separately.'
    return result
