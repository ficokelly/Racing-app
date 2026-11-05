#!/usr/bin/env python3
"""Stage 48: test whether publicly linked Betfair SP daily CSVs can be read.
No Betfair API, authentication, scraping of login pages, or bulk collection.
No horse or price rows are published; output contains aggregate field metadata only.
These archives are subject to Betfair's data rights and reuse terms.

    python scripts/stage48_betfair_market_probe.py --self-test
    python scripts/stage48_betfair_market_probe.py --date 2025-06-14 --out market-probe.json
"""
from __future__ import annotations
import argparse,csv,datetime as dt,io,json,re,time,urllib.request,urllib.error
from collections import Counter
from pathlib import Path

BASE='https://promo.betfair.com/betfairsp/prices/'
EXPECTED={'EVENT_ID','EVENT_DT','SELECTION_NAME','MORNINGWAP','PPWAP','BSP'}
MAX_BYTES=10_000_000

def validate_date(s):
    value=dt.date.fromisoformat(s)
    if value<dt.date(2008,5,28) or value>dt.date.today():
        raise ValueError('Date is outside the historical archive or is in the future')
    return value

def csv_summary(data:bytes,region:str):
    s=data.decode('utf-8-sig',errors='replace')
    reader=csv.DictReader(io.StringIO(s))
    headers=set(reader.fieldnames or [])
    missing=sorted(EXPECTED-headers)
    if missing:raise ValueError('missing CSV columns: '+', '.join(missing))
    count=0;events=set();n_morning=n_pp=n_volume=0
    for row in reader:
        count+=1
        if row.get('EVENT_ID'):events.add((row['EVENT_ID'],row.get('EVENT_DT','')))
        if row.get('MORNINGWAP') and row['MORNINGWAP'] not in ('0','0.0'):n_morning+=1
        if row.get('PPWAP') and row['PPWAP'] not in ('0','0.0'):n_pp+=1
        if row.get('PPTRADEDVOL') and row['PPTRADEDVOL'] not in ('0','0.0'):n_volume+=1
    if count<3:raise ValueError('not enough runner rows')
    return {'status':'available','region':region,'race_markets':len(events),'runners':count,'with_morningwap':n_morning,'with_ppwap':n_pp,'with_preoff_volume':n_volume,'headers':sorted(headers),
        'timing_warning':'PPWAP is full pre-off aggregate; cannot be used as known T-minus-10 price.'}

def fetch_one(region:str,day:dt.date, timeout:int=15):
    fn=f'dwbfprices{region}win{day:%d%m%Y}.csv'
    url=BASE+fn
    request=urllib.request.Request(url,headers={'User-Agent':'RacingHotPotsResearch/1.0 (manual personal-data verification)','Accept':'text/csv,text/plain,*/*'})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as resp:
            length=resp.headers.get('Content-Length')
            if length is not None and int(length)>MAX_BYTES:raise ValueError('file exceeds safety limit')
            data=resp.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise ValueError('file exceeds safety limit')
            content_type=resp.headers.get('Content-Type','')
        if data.lstrip().lower().startswith((b'<html',b'<!doctype')):raise ValueError('Received HTML rather than expected CSV')
        return {**csv_summary(data,region),'file_name':fn,'content_type':content_type}
    except (urllib.error.URLError,TimeoutError,OSError,ValueError,UnicodeError) as e:
        return {'status':'unavailable_or_invalid','region':region,'file_name':fn,'error':f'{type(e).__name__}: {str(e)[:200]}'}

def self_test():
    import unittest.mock
    body='EVENT_ID,EVENT_DT,EVENT_NAME,SELECTION_NAME,MORNINGWAP,PPWAP,BSP,PPTRADEDVOL\n'
    body+='a,2025-06-14 12:30:00,Race,A Horse,10,5,4.9,100\n'
    body+='a,2025-06-14 12:30:00,Race,B Horse,3,4,5,90\n'
    body+='b,2025-06-14 13:00:00,Another,C Horse,8,9,10,10\n'
    t=csv_summary(body.encode(),'uk')
    assert t['status']=='available' and t['runners']==3 and t['race_markets']==2 and t['with_morningwap']==3
    assert validate_date('2025-06-14')==dt.date(2025,6,14)
    try:csv_summary(b'<html>Unavailable</html>','uk')
    except ValueError:pass
    else:raise AssertionError('non-CSV passed')
    print('PASS: CSV metadata parsing and invalid-format rejection; no live download performed')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',default='2025-06-14')
    parser.add_argument('--out',default='stage48-market-source-probe.json')
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args()
    if args.self_test:return self_test()
    day=validate_date(args.date)
    result={'date':day.isoformat(),'purpose':'verify external historical data accessibility before model training',
         'download_authentication':'none','retains_raw_csv':False,'notes':['Research-only verification.','The original trained models and database remain unchanged.'],
         'files':[]}
    for region in ('uk','ire'):
        v=fetch_one(region,day);result['files'].append(v)
        print(region,v['status'],'runners:',v.get('runners'),'error:',v.get('error',''),flush=True)
    result['ready_for_real_market_training']=all(v['status']=='available' for v in result['files'])
    Path(args.out).parent.mkdir(parents=True,exist_ok=True)
    Path(args.out).write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print('Final status:', 'both UK/IE sources accessible' if result['ready_for_real_market_training'] else 'archive source needs review before model integration')
if __name__=='__main__':main()
