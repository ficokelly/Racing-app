"""Stage213: bounded pagination and Dublin/Sydney calendar conversion."""
import datetime as dt, json, os, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo
DUBLIN=ZoneInfo('Europe/Dublin'); SYDNEY=ZoneInfo('Australia/Sydney'); UTC=dt.timezone.utc

def timestamp(value):
    try:
        t=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return t.astimezone(UTC) if t.tzinfo else None
    except (ValueError,TypeError):return None

def sydney_dates(day):
    first=dt.datetime.combine(day,dt.time.min,DUBLIN).astimezone(SYDNEY).date()
    last=(dt.datetime.combine(day+dt.timedelta(days=1),dt.time.min,DUBLIN)-dt.timedelta(microseconds=1)).astimezone(SYDNEY).date()
    return [first+dt.timedelta(days=i) for i in range((last-first).days+1)]

def fetch_pages(api_key, **query):
    rows={}; offset=0; remaining='unknown'; pages=[]
    for _ in range(12):
        params={'categories':'horse','country':'GB,IE','limit':200,**query,'offset':offset}
        req=urllib.request.Request('https://api.puntersedge.online/v1/racing/results?'+urllib.parse.urlencode(params),headers={'X-API-Key':api_key,'Accept':'application/json','User-Agent':'RacingHotPots-Stage213/1.0'})
        with urllib.request.urlopen(req,timeout=45) as response:
            raw=json.load(response); applied=response.headers.get('X-Limit-Applied');remaining=response.headers.get('X-Credits-Remaining','unknown')
        if isinstance(raw,dict):raw=next((raw[k] for k in ('results','races','data') if isinstance(raw.get(k),list)),None)
        if not isinstance(raw,list):raise ValueError('Unexpected results schema')
        if not raw:return list(rows.values()),remaining,pages
        before=len(rows)
        for r in raw:
            if not isinstance(r,dict):raise ValueError('Invalid race row')
            k=str(r.get('race_id') or '') or (str(r.get('venue') or r.get('venue_canonical')),str(r.get('start_time')))
            rows[k]=r
        pages.append({'offset':offset,'returned':len(raw),'new':len(rows)-before})
        if len(rows)==before:raise RuntimeError('Repeated page; no partial publish')
        if applied and str(applied).isdigit() and 0<len(raw)<int(applied):return list(rows.values()),remaining,pages
        if str(remaining).isdigit() and int(remaining)<=1:raise RuntimeError('Insufficient credits to finish pagination')
        offset+=len(raw);time.sleep(1.2)
    raise RuntimeError('Pagination cap reached; no partial publish')

