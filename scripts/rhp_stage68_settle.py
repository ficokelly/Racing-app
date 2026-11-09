#!/usr/bin/env python3
"""Idempotent settlement for frozen pre-off D(c) research decisions.
Uses verified final GB/IE results only; never infers SP from pre-off bookmaker odds.
"""
import datetime as dt,json,pathlib,re,unicodedata
from zoneinfo import ZoneInfo

ROOT=pathlib.Path(__file__).resolve().parent.parent
DATA=ROOT/'data'
FEED=DATA/'strategy-dc-probabilities.json'
DEST=DATA/'strategy-dc-results.json'


def read(p,default):
    try:return json.loads(pathlib.Path(p).read_text())
    except (FileNotFoundError,ValueError):return default

def save(p,data):
    pathlib.Path(p).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')

def compact(v):
    v=unicodedata.normalize('NFKD',str(v or ''))
    v=''.join(c for c in v if not unicodedata.combining(c))
    return re.sub('[^a-z0-9]','',v.lower())

def course(v):
    v=re.sub(r'\s*\((?:aw|ire|gb|irl|all weather)\)','',str(v or ''),flags=re.I)
    return compact(v)

def horse(v):
    return compact(re.sub(r'\s*\((?:ire|gb|fr|usa|ger|aus|nz|saf|ita|ity)\)\s*$','',str(v or ''),flags=re.I))

def when(s):
    try:
        x=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)
    except (ValueError,TypeError):return None

def lookup(races,row):
    off=when(row.get('race_time'));day=row.get('date');co=course(row.get('course'))
    candidates=[]
    for r in races:
        if str(r.get('date'))!=str(day) or course(r.get('course'))!=co:continue
        off2=when(r.get('off_dt') or r.get('race_time'))
        if not (off and off2):continue
        minutes=abs((off-off2).total_seconds())/60
        if minutes<=12:candidates.append((minutes,r))
    if not candidates:return None
    candidates.sort(key=lambda x:x[0])
    if len(candidates)>1 and candidates[0][0]==candidates[1][0]:return None
    return candidates[0][1]

def status_of(result):
    status=compact(result.get('status'))
    pos=result.get('position')
    try:pos=int(pos) if pos is not None else None
    except (TypeError,ValueError):pos=None
    if status in ('nr','nonrunner','withdrawn','scratched','dns') :return 'Non-runner'
    if status in ('fell','f'):return 'Fell'
    if status in ('pulledup','pu'):return 'Pulled Up'
    if status in ('unseatedrider','ur'):return 'Unseated Rider'
    if status in ('broughtdown','bd'):return 'Brought Down'
    if status in ('refused','ref'):return 'Refused'
    if pos==1:return 'Won'
    if pos in (2,3):return 'Placed'
    if isinstance(pos,int) and pos>3:return 'Unplaced'
    # A generic 'ran' or 'finished' is not evidence of the official placing.
    # Only explicitly unplaced status (or an actual numeric finish >3) can settle.
    if status == 'unplaced':return 'Unplaced'
    return 'Unclassified'

def racing_api_fallback():
    rows=[]
    for p in sorted((DATA/'racing-results').glob('????-??-??.json'))[-45:]:
        src=read(p,{})
        for r in src.get('results',[]):
            if not r.get('course'):continue
            x={'date':r.get('date') or p.stem,'course':r['course'],'off_dt':r.get('off_dt'),
               'status':'final','runners':[{'horse':z.get('horse'),'position':z.get('position'),
               'status':z.get('status') or '', 'sp':None} for z in r.get('runners',[]) if z.get('horse')]}
            if any(z.get('position') for z in x['runners']):rows.append(x)
    return rows

def main():
    forecast=read(FEED,{})
    old=read(DEST,{'selections':[]})
    old_selections={r['id']:r for r in old.get('selections',[]) if r.get('id')}
    current={}
    for race in forecast.get('races',[]):
        pick=race.get('selection')
        if race.get('selection_status')!='RESEARCH_ONLY' or not pick:continue
        ident='|'.join((str(race['date']),course(race['course']),str(race['time']),horse(pick['horse'])))
        current[(race['date'],course(race['course']),race['time'])]=ident
        if ident not in old_selections:
            old_selections[ident]={'id':ident,'model':'D(c)','model_version':forecast.get('model_version'),
                 'horse':pick['horse'],'date':race['date'],'course':race['course'],'time':race['time'],
                 'race_time':race['race_time'],'race_type':race.get('race_type'),
                 'race_name':race.get('race_name'),'published_at':race.get('published_at'),
                 'status':'Pending','position':None,'sp':None,'result_source':None}
    # Mark obsolete research picks, but retain their original pre-off audit record.
    for r in old_selections.values():
        key=(r['date'],course(r['course']),r['time'])
        if key in current and current[key]!=r['id'] and r.get('status')=='Pending':
            r['status']='Superseded'
    published=read(DATA/'race-results.json',{}).get('races',[])
    fallback=racing_api_fallback()
    for row in old_selections.values():
        if row.get('status') not in ('Pending','Superseded'):continue
        verified=lookup(published,row)
        source='PuntersEdge final result'
        if not verified:
            verified=lookup(fallback,row);source='Racing API final result'
        if not verified:continue
        matched=next((r for r in verified.get('runners',[]) if horse(r.get('horse'))==horse(row['horse'])),None)
        if not matched:continue
        recorded=status_of(matched)
        if recorded=='Unclassified':
            continue  # Source does not disclose a verified position or terminal status.
        if row.get('status')=='Superseded':
            row['underlying_finish']=recorded
        else:row['status']=recorded
        row['position']=matched.get('position')
        row['sp']=matched.get('sp')
        row['result_source']=source
        row['settled_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        if verified.get('runners'):
            row['finishers']=[{'horse':r.get('horse'),'position':r.get('position'),
                'status':status_of(r),'sp':r.get('sp')}
               for r in verified['runners'] if r.get('horse')]
    out={'model':'D(c)','updated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
         'selections':sorted(old_selections.values(),key=lambda r:(r['date'],r['time'],r['course'],r['horse']),reverse=True)}
    if out.get('selections')!=old.get('selections'):
        save(DEST,out)
        print('D(c) research results:',len(out['selections']),'records;',sum(x['status']=='Pending' for x in out['selections']),'pending')
    else:print('No new D(c) settlements')

if __name__=='__main__':main()
