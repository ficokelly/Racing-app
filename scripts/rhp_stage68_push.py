#!/usr/bin/env python3
"""OneSignal push for NEW or CHANGED D(c) research shortlists only.
Old Power Fav alerts are intentionally never sent by this script.
"""
import datetime as dt,json,os,pathlib,re,urllib.error,urllib.request
from zoneinfo import ZoneInfo
DATA=pathlib.Path(__file__).resolve().parent.parent/'data'
FEED=DATA/'strategy-dc-probabilities.json'; STATE=DATA/'hot-pots-dc-push-state.json'
APP_ID='1bbabde1-23d9-4e6a-9c97-eacf144d332b'
TARGET='https://ficokelly.github.io/Racing-app/?tab=power'
IE=ZoneInfo('Europe/Dublin')

def read(path,default):
    try:return json.loads(path.read_text())
    except (FileNotFoundError,ValueError):return default

def main():
    feed=read(FEED,{})
    state=read(STATE,{'sent':{}})
    sent=state.setdefault('sent',{})
    now=dt.datetime.now(dt.timezone.utc)
    changes=[]
    for r in feed.get('races',[]):
        try:off=dt.datetime.fromisoformat(r['race_time'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError):continue
        if off.tzinfo is None:off=off.replace(tzinfo=dt.timezone.utc)
        if off<=now+dt.timedelta(minutes=3):continue
        if not r.get('complete_field'):continue
        key='|'.join((r['date'],re.sub('[^a-z0-9]','',r['course'].lower()),r['time']))
        selected=r['selection']['horse'] if r.get('selection_status')=='RESEARCH_ONLY' and r.get('selection') else None
        old=sent.get(key,'__NOT_SEEN__')
        if old==selected:continue
        if old=='__NOT_SEEN__' and not selected:continue
        kind='new' if old in ('__NOT_SEEN__',None) and selected else 'changed'
        changes.append((key,old,selected,r,kind))
    if not changes:
        print('No new or changed Hot Pots research selections')
        return
    api_key=os.environ.get('ONESIGNAL_API_KEY','').strip()
    if not api_key:raise RuntimeError('ONESIGNAL_API_KEY is missing; do not record unsent notifications')
    subscription=os.environ.get('ONESIGNAL_SUBSCRIPTION_ID','').strip()
    lines=[]
    for _,old,new,r,kind in changes[:6]:
        off=dt.datetime.fromisoformat(r['race_time'].replace('Z','+00:00')).astimezone(IE)
        where=f"{r['course']} {off.strftime('%H:%M')}"
        lines.append(f"{where}: {new or 'No selection'}" if kind=='new' else f"{where}: {old or 'None'} → {new or 'No selection'}")
    if len(changes)>6:lines.append(f"Plus {len(changes)-6} more changes in the app")
    count_new=sum(x[4]=='new' for x in changes)
    count_changed=len(changes)-count_new
    title=('🎯 Hot Pots selections published' if not count_changed else
           '🔄 Hot Pots selection update' if not count_new else '🎯 Hot Pots selections & changes')
    message=' | '.join(lines)
    payload={
        'app_id':APP_ID,
        **({'include_subscription_ids':[subscription]} if subscription else {'included_segments':['Subscribed Users']}),
        'headings':{'en':title},'contents':{'en':message},'web_url':TARGET,
        'data':{'tab':'power','model':'D(c)','new':count_new,'changed':count_changed}
    }
    req=urllib.request.Request('https://api.onesignal.com/notifications',
        data=json.dumps(payload).encode(),headers={'Authorization':'Key '+api_key,
        'Content-Type':'application/json','Accept':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=30) as response:result=json.load(response)
    except (urllib.error.URLError,TimeoutError) as exc:raise RuntimeError('Push delivery failed; leave state unchanged') from exc
    if result.get('errors') or not result.get('id'):
        raise RuntimeError('Push provider rejected the notification: '+str(result)[:400])
    for key,old,new,_,_ in changes:sent[key]=new
    cutoff=(now.astimezone(IE).date()-dt.timedelta(days=14)).isoformat()
    state['sent']={k:v for k,v in sent.items() if k[:10]>=cutoff}
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    print('OneSignal accepted',count_new,'new and',count_changed,'changed Hot Pots research selections')

if __name__=='__main__':main()
