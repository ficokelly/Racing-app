#!/usr/bin/env python3
"""Read-only PuntersEdge GB/IE historical coverage probe. No production writes."""
import os,json,urllib.request,urllib.parse,urllib.error,datetime as dt,pathlib,time
BASE='https://api.puntersedge.online'
KEY=os.getenv('PUNTERSEDGE_API_KEY','')
OUT=pathlib.Path('reports/stage65b/puntersedge_probe');OUT.mkdir(parents=True,exist_ok=True)
if not KEY:raise SystemExit('Missing PUNTERSEDGE_API_KEY GitHub Actions secret')
def request(path,params):
 url=BASE+path+'?'+urllib.parse.urlencode(params)
 req=urllib.request.Request(url,headers={'X-API-Key':KEY,'Accept':'application/json','User-Agent':'RacingHotPots-coverage-probe/1.0'})
 try:
  with urllib.request.urlopen(req,timeout=35) as res:
   raw=res.read(2000000);return {'status':res.status,'payload':json.loads(raw),'truncated':len(raw)==2000000}
 except urllib.error.HTTPError as e:return {'status':e.code,'error':e.read(500).decode(errors='replace')[:250]}
 except Exception as e:return {'error':type(e).__name__+': '+str(e)[:250]}
def summarize(result):
 p=result.get('payload');items=p if isinstance(p,list) else (p.get('data',p.get('results',p.get('races',[]))) if isinstance(p,dict) else [])
 return {'http_status':result.get('status'),'error':result.get('error'),'shape':type(p).__name__,'top_keys':list(p)[:25] if isinstance(p,dict) else [],'records':len(items) if isinstance(items,list) else None,'sample_keys':list(items[0])[:35] if isinstance(items,list) and items and isinstance(items[0],dict) else [],'truncated':result.get('truncated',False)}
checks=[('results_coverage','/v1/racing/results/coverage',{'country':'GB,IE','category':'horse'}),('closing_coverage','/v1/racing/closing-lines/coverage',{'country':'GB,IE','category':'horse'})]
for d in ('2026-10-08','2026-10-15','2026-10-29','2026-11-05'):
 checks += [(f'results_{d}','/v1/racing/results',{'date':d,'country':'GB,IE','category':'horse','limit':200,'offset':0}), (f'closing_{d}','/v1/racing/closing-lines',{'date':d,'country':'GB,IE','category':'horse','closing_only':'true','limit':200,'offset':0})]
report={'checked_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'purpose':'probe only; not a completed backfill','checks':{}}
for name,path,params in checks:
 res=request(path,params);report['checks'][name]=summarize(res)
 # Preserve raw only for coverage endpoints, avoiding copyrighted bulk or account-sensitive data in artifacts.
 if name.endswith('coverage') and res.get('status')==200:(OUT/(name+'.json')).write_text(json.dumps(res.get('payload'),indent=2)[:500000])
 print(name,report['checks'][name],flush=True);time.sleep(.3)
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print('Probe complete. A 200 response does NOT prove full-day or all-runner coverage.',flush=True)
