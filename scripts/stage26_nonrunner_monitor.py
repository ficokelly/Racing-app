#!/usr/bin/env python3
"""Stage 26 — conservative, idempotent pre-off non-runner change detector.

Uses explicit official racecard or odds-provider withdrawal flags immediately.
A disappearing horse is confirmed only after TWO DISTINCT refreshed full racecard
snapshots for the same still-declared race. No missing odds quote alone proves NR.
A/N/R/C model scoring is performed in their existing workflows, NEVER simulated
by probability renormalisation. Frozen pre-change decisions are archived.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Europe/Dublin');UTC=dt.timezone.utc
BAD={'nr','nonrunner','nonstarter','withdrawn','scratched','didnotstart','dns','wd','nonrunnerdeclared','notrunning'}
TRUE={'1','yes','true','y'}

def norm(x):return ''.join(c for c in str(x or '').casefold() if c.isalnum())
def venue(x):return norm(re.sub(r'\([^)]*\)','',str(x or '')))
def localtime(x):
    try:
        z=dt.datetime.fromisoformat(str(x).replace('Z','+00:00'))
        if not z.tzinfo:z=z.replace(tzinfo=UTC)
        return z.astimezone(TZ)
    except (ValueError,TypeError):return None

def key(row):
    race=localtime(row.get('race_time') or row.get('start_time') or row.get('off_dt'))
    return '|'.join([str(row.get('date') or (race.date().isoformat() if race else '')),venue(row.get('course') or row.get('venue')),race.strftime('%H:%M') if race else ''])
def horsekey(row):return norm(row.get('horse') or row.get('name'))
def withdrawn(row):
    for k in ('is_non_runner','non_runner','is_withdrawn','withdrawn','scratched','isNonRunner','nonRunner','isWithdrawn','isScratched'):
        val=row.get(k)
        if val is True or str(val).casefold().strip() in TRUE:return True
    for k in ('runner_status','status','outcome','result_status','horse_status','runnerStatus','horseStatus'):
        if norm(row.get(k)) in BAD:return True
    return False

def load(path,default):
    try:return json.loads(path.read_text(encoding='utf8'))
    except (ValueError,OSError):return default

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf8');os.replace(tmp,path)

def snapshot_decisions(data,changed,now):
    """Short, immutable record of pre-rescore ranks and selections, not future results."""
    files={"A/B":'strategy-ab-selections.json',"A":'strategy-probabilities.json',"B":'strategy-b-probabilities.json',"C":'strategy-c-probabilities.json'}
    result=[]
    for model,name in files.items():
        raw=load(data/name,{})
        if model=='A/B':
            packs=[(m,p.get('races',[])) for m,p in raw.get('models',{}).items()]
        else:packs=[(model,raw.get('races',[]))]
        for model_name,races in packs:
            for r in races:
                rk=key(r)
                if rk not in changed:continue
                selection=r.get('selection')
                if isinstance(selection,dict):selection={k:selection.get(k) for k in ('horse','price_decimal','predicted_win_probability','score')}
                runners=r.get('runners') or r.get('ranked_runners') or r.get('ranking') or r.get('model_a_probability_ranking') or []
                ranks=[]
                for item in runners[:35]:
                    if isinstance(item,dict):ranks.append({x:item.get(x) for x in ('horse','score','probability','win_probability','form_runs') if x in item})
                result.append({'model':model_name,'race':rk,'source':name,'model_version':r.get('model_version') or raw.get('model_version'),
                               'selection':selection,'selection_status':r.get('selection_status'),'ranking':ranks,'published_before_change':raw.get('generated_at')})
    return {'detected_at':now.isoformat(),'races':sorted(changed),'decisions':result,'reason':'Non-runner declaration; historical pre-change evidence only'}

def run(root,now=None):
    root=Path(root); data=root/'data';now=now or dt.datetime.now(UTC)
    cards_path=data/'racecards.json';cards=load(cards_path,{'runners':[]});rows=cards.get('runners',[])
    if not isinstance(rows,list) or len(rows)<2:raise RuntimeError('Refusing to monitor partial/empty current racecards')
    published=load(data/'nonrunner-monitor.json',{'version':1,'known':{},'missing_seen':{},'dispatch_sequence':0})
    before=json.dumps(published,sort_keys=True)
    known=dict(published.get('known',{}));missing_seen=dict(published.get('missing_seen',{}))
    odds=load(data/'latest-odds.json',{});status=odds.get('runners_status',[])
    by_race=defaultdict(dict)
    for r in rows:
        rk=key(r); h=horsekey(r)
        if rk and h:by_race[rk][h]=r
    archives=load(data/'racecards-archive.json',{'runners':[]})
    older=defaultdict(dict)
    for r in archives.get('runners',[]):
        if isinstance(r,dict):older[key(r)][horsekey(r)]=r
    day=now.astimezone(TZ).date().isoformat();newly={};changed=set()
    def race_future(r):
        ts=localtime(r.get('race_time'))
        return bool(ts and ts.astimezone(UTC)>now+dt.timedelta(minutes=2) and ts.date().isoformat()==day)
    def mark(rk,h,source):
        label=rk+'|'+h
        if label not in known:
            newly[label]={'race':rk,'horse':by_race.get(rk,{}).get(h,older.get(rk,{}).get(h,{})).get('horse') or h,'source':source,'detected_at':now.isoformat()}
            changed.add(rk)
        known[label]=known.get(label,newly.get(label,{}))
    for rk,horsemap in by_race.items():
        if not rk.startswith(day+'|') or not horsemap:continue
        if not race_future(next(iter(horsemap.values()))):continue
        for h,r in horsemap.items():
            if withdrawn(r):mark(rk,h,'EXPLICIT_RACECARD_STATUS')
    # Price feeds contain an explicit status channel even for runners with no quotes.
    if isinstance(status,list):
        for r in status:
            if not isinstance(r,dict) or not withdrawn(r):continue
            rk=key(r);h=horsekey(r)
            if rk in by_race and h in by_race[rk] and race_future(by_race[rk][h]):mark(rk,h,'EXPLICIT_ODDS_RUNNER_STATUS')
    # A horse omitted from a racecard isn't assumed NR until separate collection
    # timestamps independently report it absent while the race itself is present.
    stamp=str(cards.get('collected_at') or '')
    for rk,horses in older.items():
        if rk not in by_race or not rk.startswith(day+'|'):continue
        if not race_future(next(iter(by_race[rk].values()))):continue
        for h,old in horses.items():
            label=rk+'|'+h
            if h in by_race[rk]:missing_seen.pop(label,None);continue
            observation=missing_seen.setdefault(label,{'distinct_snapshots':[]})
            ss=observation.setdefault('distinct_snapshots',[])
            if stamp and stamp not in ss:ss.append(stamp)
            if len(ss)>=2:mark(rk,h,'TWO_DISTINCT_COMPLETE_RACECARDS_WITH_OMISSION')
    # Never delete the horse from the data archive. Retain an explicit NR row
    # in the live card so the UI can show the withdrawal. All engines exclude it.
    modified=0
    for label,info in known.items():
        rk,h=info.get('race'),label.split('|')[-1]
        if rk not in by_race or not rk.startswith(day+'|'):continue
        row=by_race[rk].get(h)
        if row is None:
            historic=older.get(rk,{}).get(h)
            if historic:
                row=dict(historic);rows.append(row);by_race[rk][h]=row
        if row is not None and not withdrawn(row):
            row['non_runner']=True;row['runner_status']='NR';row['non_runner_source']=info.get('source');modified+=1
    # Field-size metadata used by historical comparisons must describe starters,
    # not the original declared field. Preserve the raw NR row for audit/UI.
    affected={item.get('race') for item in known.values() if item.get('race') in by_race}
    for rk in affected:
        starters=sum(1 for rr in by_race[rk].values() if not withdrawn(rr))
        for rr in by_race[rk].values():
            if str(rr.get('field_size') or '')!=str(starters):
                rr['field_size']=str(starters);modified+=1
    if modified:save(cards_path,cards)
    # Persist positive declarations exactly once; if a model job fails, saved
    # alert still supports manual rerun. Never invent an alternative pick.
    if newly:
        history_path=data/'nonrunner-decision-history.json';history=load(history_path,{'version':1,'events':[]})
        event=snapshot_decisions(data,changed,now)
        event['non_runners']=list(newly.values())
        history['events'].append(event)
        save(history_path,history)
        published['dispatch_sequence']=int(published.get('dispatch_sequence') or 0)+1
        published['last_change_at']=now.isoformat()
        published['last_changed_races']=sorted(changed)
    published.update({'version':1,'known':known,'missing_seen':missing_seen})
    if json.dumps(published,sort_keys=True)!=before or not (data/'nonrunner-monitor.json').exists():
        published['updated_at']=now.isoformat()
        save(data/'nonrunner-monitor.json',published)
    print('STAGE26_NR_NEW='+str(len(newly)))
    print('STAGE26_NR_CHANGED_RACES='+str(len(changed)))
    print('STAGE26_NR_SEQUENCENO='+str(published['dispatch_sequence']))
    return len(newly),len(changed)

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',default='.');a=p.parse_args();run(Path(a.repo))
if __name__=='__main__':main()
