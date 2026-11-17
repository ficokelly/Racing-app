#!/usr/bin/env python3
"""Stage147 daily controller: dispatch + independently audit all available UK/IE model paper workflows.

The controller has no horse-selection algorithm. It never fabricates picks:
GitHub job success is only 'complete' when dated pre-off selections are found.
Model variants that lack a current scorer remain explicit NOT_READY rows.
All accepted selection rows carry provenance from their original child run.
"""
from __future__ import annotations
import argparse,base64,collections,csv,datetime as dt,io,json,os,pathlib,re,subprocess,sys,time,urllib.error,urllib.parse,urllib.request
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Europe/Dublin')
API='https://api.github.com'
OUT=pathlib.Path('reports/stage147/morning')
# Each workflow's name and dispatch inputs are verified against current Racing-app.
# Research baselines without genuine daily scoring are listed further below.
ENABLED=(
 {'id':'A_B','models':['A','B'],'workflow':'stage13-strategy-learning.yml','inputs':{},'source':'published'},
 {'id':'C','models':['C'],'workflow':'stage21-model-c-live.yml','inputs':{},'source':'published'},
 {'id':'D_Z','models':['D','Z'],'workflow':'stage38-model-d-repair.yml','inputs':{'day':'$DAY'},'source':'published'},
 {'id':'Dc','models':['D(c)'],'workflow':'rhp-stage69-dc-live.yml','inputs':{'day':'$DAY'},'source':'published'},
 {'id':'G','models':['G'],'workflow':'stage107-model-g-paper.yml','inputs':{'selection_date':'$DAY','bookmaker':'williamhill'},'source':'artifact','csv':'stage107-paper-all-rankings.csv'},
 {'id':'G2_G3','models':['G2_FROZEN','G3_LEARNED'],'workflow':'stage109-g3-paper.yml','inputs':{'selection_date':'$DAY','bookmaker':'williamhill'},'source':'artifact','csv':'stage109-paper-all-horses.csv'},
 {'id':'G4','models':['G4_SPECIALIST'],'workflow':'stage111b-g4-paper-fix.yml','inputs':{'selection_date':'$DAY'},'source':'artifact','csv':'stage111-paper-all-horses.csv'},
 {'id':'G5','models':['G5_STRENGTH_QUARTERLY','G5_NETWORK_QUARTERLY','G5_COMBINED_QUARTERLY','G5_ADAPTIVE_QUARTERLY'],'workflow':'stage128-g5-paper.yml','inputs':{'selection_date':'$DAY'},'source':'artifact','csv':'stage128-g5-all-horses.csv'},
 {'id':'Stage141','models':['D137_HPR_ABILITY_OR_WEIGHT'],'workflow':'stage141-today-or-weight-paper.yml','inputs':{'selection_date':'$DAY'},'source':'artifact','csv':'stage141-today-rank1-rank2.csv'},
 {'id':'Stage143_146','models':['STAGE143_TYPE_EVIDENCE','STAGE143_TYPE_INTERACTIONS','STAGE146_A_RETRAINED_TRENDS','STAGE146_B_RETRAINED_TRENDS'],'workflow':'stage147-daily-143-146.yml','inputs':{'selection_date':'$DAY'},'source':'artifact','csv':'stage146-today-rank1-rank2.csv'},
)
# These had historical tests but lack an independently verified genuine *daily* Rank1 scorer.
NOT_READY={
 'E_HPR':'Historical HPR archive is not a complete daily ranked model',
 'E_75_25':'No verified general daily scorer (earlier one-day NH handicap test only)',
 'F':'Research-only; no verified daily paper scorer',
 'Stage137_140':'Historical training/backtest controls, not separate daily selection workflows',
 'Stage142':'Historical model; no verified daily full-field scorer',
 'Stage144_145':'Trend research/replay, not standalone daily model predictions',
 'Original_D_variants_A_B_C':'Archived A/B/C D variants beyond the current live A/B/C are research-only',
}

def date_dublin():return dt.datetime.now(ZONE).date().isoformat()
def dt_parse(s):
    try:
        x=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return x.astimezone(dt.timezone.utc) if x.tzinfo else None
    except (TypeError,ValueError):return None

def now_utc():return dt.datetime.now(dt.timezone.utc)
def nice_time(row):return str(row.get('time_utc') or row.get('off_utc') or row.get('time') or row.get('race_time') or '').strip()[:5]
def normalize(v):return re.sub('[^A-Z0-9]','',str(v or '').upper())

def endpoint(url,method='GET',body=None):
    key=os.getenv('GH_TOKEN') or os.getenv('GITHUB_TOKEN')
    if not key:raise RuntimeError('Missing token in GitHub Actions environment')
    h={'Authorization':'Bearer '+key,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'Racing-app-Stage147'}
    req=urllib.request.Request(url,headers=h,method=method,data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req,timeout=35) as response:
            raw=response.read();return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'GitHub {method} {url} failed {e.code}: {e.read()[:400]!r}') from e

def repo():return os.environ.get('GITHUB_REPOSITORY','ficokelly/Racing-app')

def api(path,**params):
    url=API+'/repos/'+repo()+'/'+path.lstrip('/')
    if params:url+='?'+urllib.parse.urlencode(params)
    return endpoint(url)

def main_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    cols=list(dict.fromkeys(k for row in rows for k in row)) if rows else ['date','model','meeting','time_utc','horse','rank','run_url']
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)

def check_cards(path,target):
    z=json.loads(path.read_text());age=(now_utc()-(dt_parse(z['collected_at']) or now_utc())).total_seconds()
    if age<0 or age>2*3600:raise RuntimeError(f'Racecard snapshot too old/new: {age/60:.1f} min')
    events=collections.defaultdict(list)
    for x in z['runners']:
        if x.get('date')!=target or not x.get('horse') or not x.get('race_time'):continue
        if str(x.get('is_non_runner') or '').lower() in ('true','1','yes'):continue
        if str(x.get('is_withdrawn') or '').lower() in ('true','1','yes'):continue
        if str(x.get('runner_status') or '').lower() in ('nr','scratched','withdrawn','nonrunner'):continue
        off=dt_parse(x['race_time'])
        if not off:continue
        events[(str(x['course']),off.isoformat(),str(x['race_id']))].append(x['horse'])
    races=[(a,b,c) for (a,b,c),horses in events.items() if len(set(horses))>=3]
    if not races:raise RuntimeError('No declared valid race fields for '+target)
    return {'date':target,'races':len(races),'meeting_count':len(set(r[0] for r in races)),'cards_collected_at':z['collected_at'],'race_keys':races}

def dry_run(day):
    status={'day':day,'enabled_jobs':len(ENABLED),'enabled_models':sum(len(z['models']) for z in ENABLED),
        'run_at':'04:37 Europe/Dublin','deadline':'09:45 Europe/Dublin','known_not_ready':NOT_READY,
        'groups':[{**z,'inputs':{k:(day if v=='$DAY' else v) for k,v in z['inputs'].items()}} for z in ENABLED]}
    return status

def fetch_runs(workflow,since):
    arr=api(f'actions/workflows/{workflow}/runs',event='workflow_dispatch',per_page=35).get('workflow_runs',[])
    found=[]
    for x in arr:
        created=dt_parse(x.get('created_at'))
        if created and created>=since-dt.timedelta(seconds=5) and x.get('head_branch')=='main':found.append(x)
    return sorted(found,key=lambda r:r['created_at'])

def dispatch(day):
    records=[]
    for task in ENABLED:
        start=now_utc()
        rec={'group':task['id'],'workflow':task['workflow'],'models':task['models'],'status':'DISPATCHED','requested_at':start.isoformat(), 'run_id':None,'url':None,'reason':''}
        try:
            body={'ref':'main','inputs':{k:(day if v=='$DAY' else v) for k,v in task['inputs'].items()}}
            endpoint(API+'/repos/'+repo()+'/actions/workflows/'+task['workflow']+'/dispatches','POST',body)
        except Exception as exc:rec['status']='DISPATCH_FAILED';rec['reason']=str(exc)
        records.append(rec)
        print('STAGE147_DISPATCH',rec['group'],rec['status'],rec['reason'],flush=True)
        time.sleep(.8)
    return records

def get_artifact_csv(rec,task,out):
    runid=rec.get('run_id')
    if not runid:return [],'NO_RUN'
    artifacts=api(f'actions/runs/{runid}/artifacts',per_page=50).get('artifacts',[])
    # Only download small daily artifacts; never restore the training DB through the controller.
    candidates=[z for z in artifacts if not z.get('expired') and (z.get('size_in_bytes') or 0)<5_000_000]
    if not candidates:return [],'NO_DAILY_ARTIFACT'
    last_error='EXPECTED_CSV_NOT_FOUND'
    for item in candidates:
        folder=out/'children'/rec['group']
        folder.mkdir(parents=True,exist_ok=True)
        result=subprocess.run(['gh','run','download',str(runid),'--repo',repo(),'--name',item['name'],'--dir',str(folder)],capture_output=True,text=True)
        if result.returncode!=0:last_error='ARTIFACT_DOWNLOAD_FAILED: '+result.stderr[-220:];continue
        matches=list(folder.rglob(task['csv']))
        if not matches:continue
        try:
            with matches[0].open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f)),'OK'
        except (OSError,UnicodeDecodeError,csv.Error) as e:last_error='ARTIFACT_PARSE_FAILED: '+str(e)
    return [],last_error

def extract_archive(rows,task,day,run_url):
    out=[]
    expected=set(task['models']);seen=set()
    for r in rows:
        model=str(r.get('model') or '').strip()
        if task['id']=='G' and not model:model='G'  # Stage107 paper file has no model field.
        if model not in expected or str(r.get('date') or '').strip()!=day:continue
        rank=str(r.get('rank') or '').strip()
        if rank not in ('1','2'):continue
        horse=r.get('horse')
        if not horse:continue
        rkey=str(r.get('race_id') or '')
        if not rkey:rkey='|'.join((day,str(r.get('course') or r.get('meeting') or ''),nice_time(r)))
        sig=(model,rkey,rank)
        if sig in seen:raise RuntimeError(f'Duplicate model-race-rank {sig} in artifact')
        seen.add(sig)
        out.append({'date':day,'model':model,'meeting':r.get('course') or r.get('meeting') or '',
          'time_utc':nice_time(r),'race_id':rkey,'horse':horse,'rank':int(rank),
          'win_probability':r.get('model_win_probability') or r.get('win_probability') or '',
          'trend_source':r.get('trend_source') or '', 'source':'IMMUTABLE_PAPER_ARTIFACT','run_url':run_url})
    return out

def get_repo_json(path):
    z=api('contents/'+path,ref='main')
    if z.get('encoding')!='base64':raise RuntimeError('GitHub JSON encoding changed')
    return json.loads(base64.b64decode(z['content']))

def extract_published(task,day,rec):
    # Only use the new target day; these published JSON files contain historical cards too.
    out=[]
    if task['id']=='A_B':
        source=get_repo_json('data/strategy-ab-selections.json')
        for model in ('A','B'):
            for row in source.get('models',{}).get(model,{}).get('races',[]):
                if row.get('date')!=day:continue
                selected=row.get('selection')
                if not isinstance(selected,dict) or not selected.get('horse') or selected.get('provisional') is True:continue
                stamp=dt_parse(selected.get('published_at') or row.get('published_at'))
                off=dt_parse(row.get('race_time'))
                if not stamp or (off and stamp>=off):continue
                out.append({'date':day,'model':model,'meeting':row.get('course',''),
                  'time_utc':row.get('time',''),'race_id':row.get('race_id') or (day+'|'+str(row.get('course'))+'|'+str(row.get('time'))),
                  'horse':selected['horse'],'rank':1,'win_probability':selected.get('winning_probability',''),
                  'trend_source':'','source':'PREOFF_QUALIFIED_SELECTION_ONLY', 'run_url':rec['url']})
        return out
    if task['id']=='C':files=[('C','data/strategy-c-probabilities.json')]
    elif task['id']=='D_Z':files=[('D','data/strategy-d-probabilities.json'),('Z','data/strategy-z-probabilities.json')]
    elif task['id']=='Dc':files=[('D(c)','data/strategy-dc-probabilities.json')]
    else:return out
    for name,path in files:
        try:j=get_repo_json(path)
        except Exception as ex:
            print('STAGE147_PUBLISHED_ERROR',name,path,str(ex),flush=True);continue
        for race in j.get('races',[]):
            if race.get('date')!=day:continue
            for z in race.get('runners',[]):
                rank=str(z.get('rank') or '')
                if rank not in ('1','2'):continue
                stamp=dt_parse(race.get('published_at') or race.get('scored_at') or j.get('generated_at'))
                off=dt_parse(race.get('race_time'))
                if stamp and off and stamp>=off:continue
                out.append({'date':day,'model':name,'meeting':race.get('course',''),
                  'time_utc':race.get('time',''),'race_id':race.get('race_id',''),
                  'horse':z['horse'],'rank':int(rank),
                  'win_probability':z.get('win_probability') or z.get('probability') or '',
                  'trend_source':'','source':'COMMITTED_PREOFF_RANKINGS','run_url':rec['url']})
    return out

def record_outputs(rec,task,day,out):
    if task['source']=='artifact':
        source,status=get_artifact_csv(rec,task,out)
        if status!='OK':return [],status
        rows=extract_archive(source,task,day,rec['url'])
        return rows,'OK' if rows else 'EMPTY_OR_WRONG_DATE'
    else:
        rows=extract_published(task,day,rec)
        return rows,'OK' if rows else 'EMPTY_OR_WRONG_DATE'

def validate_rows(rows,day):
    seen=set()
    for r in rows:
        if r['date']!=day or r['rank'] not in (1,2):raise RuntimeError('Invalid dated pre-race row')
        sig=(r['model'],r['race_id'],r['rank'])
        if sig in seen:raise RuntimeError('Duplicate model/race/rank: '+str(sig))
        seen.add(sig)

def write_summary(manifest,rows):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'stage147-morning-status.json').write_text(json.dumps(manifest,indent=2,default=str)+'\n')
    main_csv(OUT/'stage147-all-models-rank1-rank2.csv',rows)
    models=collections.defaultdict(set)
    for r in rows:
        if r['rank']==1:models[r['model']].add(r['race_id'])
    md=['# Morning models — '+manifest['date'],'','**Cards:** '+str(manifest['cards']['races'])+' races / '+str(manifest['cards']['meeting_count'])+' meetings. '+
        '**Published at:** '+manifest['generated_at'],'','| Model | Races with Rank 1 | Status |','|---|---:|---|']
    for m in sorted(set(sum((x['models'] for x in ENABLED),[]))):
        n=len(models[m]);md.append(f'| {m} | {n} | {"Selections available" if n else "Missing / awaiting daily picks"} |')
    md+=['','**Not yet connected to daily scoring:** '+', '.join(NOT_READY),
         '', 'A green workflow does not imply every race or model was ranked. See `stage147-morning-status.json` for missing groups and exact child run links.',
         '', 'All research results are paper only, not confirmed betting value; no current result or SP is used in forecasts.']
    (OUT/'README.md').write_text('\n'.join(md)+'\n')
    print('STAGE147_SUMMARY',json.dumps({'date':manifest['date'],'status':manifest['status'],'modelsWithSelections':len(models),'rows':len(rows)}),flush=True)
    for line in md:print(line)

def run(args):
    day=args.date or date_dublin()
    if day!=date_dublin():raise RuntimeError('Morning controller must target today in Europe/Dublin')
    OUT.mkdir(parents=True,exist_ok=True)
    card=check_cards(pathlib.Path(args.cards),day)
    mode='DRY_RUN' if args.dry_run else 'DISPATCHED'
    if args.dry_run:
        data=dry_run(day);data['cards']=card
        (OUT/'stage147-dry-run.json').write_text(json.dumps(data,indent=2)+'\n')
        print(json.dumps({**data,'groups':[{k:v for k,v in z.items() if k!='inputs'} for z in data['groups']]},indent=2));return
    records=dispatch(day)
    deadline=dt.datetime.combine(dt.date.fromisoformat(day),dt.time(9,45),ZONE).astimezone(dt.timezone.utc)
    if now_utc()>=deadline:
        print('STAGE147_LATE_MANUAL_RUN: deadline already passed; do not claim before 10AM',flush=True)
    # Start after dispatch, poll run records and fail closed on missing artifacts.
    status_by_id={r['group']:r for r in records};outputs={};results={}
    while now_utc()<deadline:
        terminal=0
        for task in ENABLED:
            r=status_by_id[task['id']]
            if r['status']=='DISPATCH_FAILED':terminal+=1;continue
            if not r.get('run_id'):
                try:
                    possible=fetch_runs(r['workflow'],dt_parse(r['requested_at']))
                    if possible:
                        x=possible[0];r['run_id']=x['id'];r['url']=x['html_url'];r['status']=x['status'];r['conclusion']=x.get('conclusion')
                except Exception as ex:r['reason']='RUN_LOOKUP: '+str(ex)
            else:
                try:
                    x=api('actions/runs/'+str(r['run_id']))
                    r['status']=x['status'];r['conclusion']=x.get('conclusion');r['url']=x['html_url']
                except Exception as ex:r['reason']='RUN_STATUS: '+str(ex)
            if r['status']=='completed':
                terminal+=1
                if r.get('conclusion')=='success' and task['id'] not in results:
                    try:
                        a,reason=record_outputs(r,task,day,OUT)
                        results[task['id']]=reason;outputs[task['id']]=a
                    except Exception as ex:results[task['id']]='OUTPUT_ERROR: '+str(ex)
                elif r.get('conclusion')!='success':results[task['id']]='WORKFLOW_'+str(r.get('conclusion')).upper()
        print('STAGE147_PROGRESS',terminal,'/',len(ENABLED),'groups done',flush=True)
        if terminal==len(ENABLED):break
        time.sleep(90)
    allrows=[r for task in ENABLED for r in outputs.get(task['id'],[])]
    validate_rows(allrows,day)
    bymodel=collections.Counter(r['model'] for r in allrows if r['rank']==1)
    for task in ENABLED:
        rec=status_by_id[task['id']]
        rec['output_status']=results.get(task['id'],'NOT_FINISHED_BEFORE_DEADLINE')
        rec['models_with_rank1']={model:bymodel[model] for model in task['models']}
    finished_before_deadline=now_utc()<=deadline
    healthy=all(r['status']=='completed' and r.get('conclusion')=='success' and rec_ok(r) and
        all(bymodel[m]>0 for m in r['models']) for r in records)
    coverage={m:{'rank1_races':bymodel[m], 'declared_races':card['races'],
                   'coverage_percent':round(100*bymodel[m]/card['races'],1) if card['races'] else 0} 
              for task in ENABLED for m in task['models']}
    manifest={'version':1,'date':day,'generated_at':now_utc().isoformat(),
      'deadline':'09:45 Europe/Dublin','before_deadline':finished_before_deadline,
      'status':'CONNECTED_MODELS_HAVE_SELECTIONS' if healthy and finished_before_deadline else 'INCOMPLETE_CHECK_REQUIRED',
      'cards':{k:v for k,v in card.items() if k!='race_keys'},'run_groups':records,
      'observed_rank1_races_by_model':dict(bymodel),'coverage_by_model':coverage,
      'models_without_daily_workflow':NOT_READY,
      'limit':'One Group success can include partial or late paper coverage; inspect per-race rows. Scheduled start is not a guaranteed SLA.'}
    write_summary(manifest,allrows)
    if manifest['status']!='CONNECTED_MODELS_HAVE_SELECTIONS':raise SystemExit('Stage147 incomplete: examine morning-status.json and failed/missing child workflows')

def rec_ok(r):return r.get('output_status')=='OK'

def self_test():
    assert len(ENABLED)==10
    assert sum(len(z['models']) for z in ENABLED)==19
    assert date_dublin() and '2026' in previous_day_test()
    assert normalize('D(c)')=='DC'
    x=[{'date':'2026-11-17','model':'G4_SPECIALIST','course':'Wolverhampton','race_id':'abc',
       'off_utc':'17:00','rank':'1','horse':'Example','win_probability':'.33'}]
    parsed=extract_archive(x,ENABLED[6],'2026-11-17','https://github.com/example/runs/1')
    assert len(parsed)==1 and parsed[0]['rank']==1 and parsed[0]['time_utc']=='17:00'
    validate_rows(parsed,'2026-11-17')
    assert len(extract_archive(x,ENABLED[6],'2026-11-16','fake'))==0
    print('STAGE147_SELFTEST_OK',len(ENABLED),'child workflows',sum(len(z['models']) for z in ENABLED),'daily model identifiers')

def previous_day_test():return (dt.date(2026,11,17)-dt.timedelta(days=1)).isoformat()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=('self-test','run'))
    parser.add_argument('--date',default='');parser.add_argument('--cards',default='data/racecards.json')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if args.command=='self-test':self_test()
    else:run(args)
