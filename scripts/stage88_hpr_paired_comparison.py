#!/usr/bin/env python3
"""Compare original E v0 vs explicit-DNF E v1 on exactly matching races.

Rank comparisons and SP ROI are derived from historical outcomes after pre-off
rankings. Zero matched races or unpriced races must NOT be displayed as 0% ROI.
"""
from __future__ import annotations
import argparse,collections,csv,json,pathlib,re

def norm(s):return re.sub('[^A-Z0-9]','',str(s or '').upper())
def decimal(v):
    try:
        x=float(v)
        return x if x>=1 else None
    except (ValueError,TypeError):return None

def read(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def collect(rows):
    out=collections.defaultdict(dict)
    for r in rows:
        try:rank=int(r['rank'])
        except (KeyError,ValueError,TypeError):continue
        if rank not in (1,2):continue
        key=(r['date'],norm(r['course']),r['time'])
        if rank in out[key]:raise ValueError(f'Duplicate rank {rank} on {key}')
        out[key][rank]=r
    return {k:v for k,v in out.items() if 1 in v and 2 in v and norm(v[1]['horse'])!=norm(v[2]['horse'])}

def metrics(rows,model):
    out=[]
    for label,n in (('Rank 1',1),('Rank 2',2),('Combined',0)):
        selected=[r for r in rows if n==0 or int(r['rank'])==n]
        priced=[r for r in selected if decimal(r.get('sp_decimal')) is not None]
        wins=sum(str(r.get('win')) in ('1','True','true') for r in selected)
        gross=sum(decimal(r['sp_decimal']) if str(r.get('win')) in ('1','True','true') else 0 for r in priced)
        out.append({'model':model,'group':label,'selections':len(selected),'wins':wins,
          'strike_rate_pct':round(100*wins/len(selected),2) if selected else None,
          'priced_selections':len(priced),
          'sp_roi_pct':round(100*(gross/len(priced)-1),2) if priced else None})
    return out

def compare(baseline,candidate):
    a,b=collect(baseline),collect(candidate)
    both=sorted(a.keys() & b.keys())
    rows=[];selections=[]
    for version,data in [('E_v0_frozen_research',a),('E_v1_explicit_DNF_research',b)]:
        arr=[]
        for key in both:
            for rank in (1,2):
                r=dict(data[key][rank],model=version)
                arr.append(r);selections.append(r)
        rows.extend(metrics(arr,version))
    return {'scope':'Only exactly matched preoff races: UK/IE, same date/course/off time',
      'matched_races':len(both),'v0_eligible_races':len(a),'v1_eligible_races':len(b),
      'newly_eligible_v1':len(b.keys()-a.keys()),'v0_not_eligible_v1':len(a.keys()-b.keys()),
      'model_comparison':rows,
      'warning':'Research-only: no change to live D or D(c). Results in matched subset do not establish predictive improvement.'},selections

def test():
    def mk(day,name,rank,win,sp):return {'date':day,'course':'Naas (IRE)','time':'12:30','rank':str(rank),'horse':name,'win':str(win),'sp_decimal':str(sp),'position':'1' if win else '2'}
    a=[mk('2026-11-06','Alpha',1,1,4),mk('2026-11-06','Beta',2,0,5)]
    b=[mk('2026-11-06','Beta',1,0,5),mk('2026-11-06','Alpha',2,1,4)]
    summary,rows=compare(a,b)
    assert summary['matched_races']==1
    assert len(rows)==4
    assert summary['model_comparison'][0]['sp_roi_pct']==300
    assert summary['model_comparison'][3]['wins']==0
    assert summary['model_comparison'][4]['sp_roi_pct']==300
    assert compare([],[])[0]['model_comparison'][0]['sp_roi_pct'] is None
    print('PASS: same-race, no invented ROI, ranked SP settlement')

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--baseline',type=pathlib.Path)
    ap.add_argument('--candidate',type=pathlib.Path)
    ap.add_argument('--output',type=pathlib.Path)
    ap.add_argument('--self-test',action='store_true')
    a=ap.parse_args()
    if a.self_test:test();return
    if not a.baseline or not a.candidate or not a.output:ap.error('baseline, candidate and output required')
    report,rows=compare(read(a.baseline),read(a.candidate))
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'stage88-model-e-v0-v1-comparison.json').write_text(json.dumps(report,indent=2)+'\n')
    fields=['model','date','course','time','rank','horse','hpr_preoff','position','win','sp_decimal','return_units','source','applicable_form_covered']
    with open(a.output/'stage88-paired-selections.csv','w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
