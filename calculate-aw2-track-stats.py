#!/usr/bin/env python3
"""AW (2) historical track statistics; no API requests or additional subscriptions.
Uses archived William Hill quotes observed before scheduled off and verified result races.
"""
import json,re,glob,datetime
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
def norm(s):return re.sub(r'[^a-z0-9]+',' ',re.sub(r'\s*\((?:aw|all.weather)\)\s*',' ',str(s or ''),flags=re.I).lower()).strip()
def horse(s):return norm(re.sub(r'\s*\((?:ire|gb|fr|usa|ger|aus|nz|ita|saf)\)\s*$','',str(s or ''),flags=re.I))
def dt(s):
 try:return datetime.datetime.fromisoformat(str(s).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return None
race_info={}
for path in sorted(glob.glob(str(DATA/'racing-results'/'*.json'))):
 try:races=json.loads(Path(path).read_text()).get('results',[])
 except (ValueError,OSError):continue
 for r in races:
  course=norm(r.get('course'));date=r.get('date');tm=dt(r.get('off_dt'))
  if not (course and date and tm):continue
  info={'course':course,'date':date,'time':tm,'type':r.get('type',''),'surface':r.get('surface',''),'class':r.get('class',''),'name':r.get('race_name',''),'results':{horse(h.get('horse')):str(h.get('position','')).strip() for h in r.get('runners',[])}}
  race_info[(date,course,round(tm/60))]=info
quotes={}
for path in sorted(glob.glob(str(DATA/'odds-archive'/'*.json'))):
 try:snaps=json.loads(Path(path).read_text())
 except (ValueError,OSError):continue
 for snap in snaps if isinstance(snaps,list) else []:
  stamp=dt(snap.get('collected_at'))
  for p in snap.get('prices',[]):
   if norm(p.get('bookmaker')) not in ('williamhill','william hill') or p.get('stale') is True:continue
   off=dt(p.get('race_time'));qstamp=dt(p.get('collected_at')) or stamp
   if not off or not qstamp or qstamp>off:continue
   try:od=float(p['decimal_odds'])
   except (ValueError,TypeError,KeyError):continue
   if od<=1:continue
   course=norm(p.get('course'));date=datetime.datetime.fromtimestamp(off,datetime.timezone.utc).date().isoformat()
   key=(date,course,round(off/60));h=horse(p.get('horse'))
   if not h:continue
   k=(key,h)
   record=quotes.get(k)
   if record is None:
    quotes[k]={'first':od,'last':od,'first_at':qstamp,'last_at':qstamp,'opening':p.get('opening_odds')}
   else:
    if qstamp<record['first_at']:record['first']=od;record['first_at']=qstamp
    if qstamp>=record['last_at']:record['last']=od;record['last_at']=qstamp
    if record.get('opening') is None:record['opening']=p.get('opening_odds')
byrace=defaultdict(list)
for (race,h),record in quotes.items():byrace[race].append((h,record))
counts=defaultdict(lambda:{'wins':0,'settled':0,'pending':0})
for race,runners in byrace.items():
 info=race_info.get(race)
 if not info:continue
 if norm(info['surface']) not in ('aw','all weather','synthetic','polytrack','tapeta','fibresand'):continue
 if not re.search(r'\bclass\s*[56]\b',str(info['class']),re.I):continue
 if not re.search('handicap',str(info['name']),re.I):continue
 eligible=sorted(runners,key=lambda a:a[1]['last'])
 if len(eligible)<2 or eligible[0][1]['last']==eligible[1][1]['last']:continue
 h,r=eligible[0]
 try:opening=float(r['opening'])
 except (ValueError,TypeError):opening=None
 baseline=r['first'] if r['first_at']<r['last_at'] else opening
 if not baseline or baseline<=1 or (baseline-r['last'])/(baseline-1)*100<5:continue
 pos=info['results'].get(h)
 track=info['course']
 if pos and pos.isdigit():
  counts[track]['settled']+=1
  if int(pos)==1:counts[track]['wins']+=1
 else:counts[track]['pending']+=1
out={'method':'One recorded unique William Hill favourite per race; Class 5/6 AW handicap, shortened >=5% using earliest historical quote or API opening price where necessary; last archived pre-off quote; verified finishing positions. Archived coverage may be incomplete.', 'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'tracks':dict(sorted(counts.items()))}
(DATA/'aw2-track-stats.json').write_text(json.dumps(out,indent=2)+'\n')
print('AW2 stats',sum(x['settled'] for x in counts.values()),'settled across',len(counts),'tracks')
