#!/usr/bin/env python3
"""Read-only Stage66 A/B/C race type ROI; no post-off model reconstruction."""
import argparse,csv,json,pathlib,collections,re,datetime

def norm(s):return re.sub(r'[^a-z0-9]','',str(s or '').lower())
def category(s):
 s=str(s or '').lower()
 for key,pattern in [('handicap',r'handicap'),('maiden',r'maiden'),('novice',r'novice'),('beginner',r'beginner'),('bumper',r'bumper|national hunt flat'),('graded/group',r'grade [123]|group [123]|listed|graded')]:
  if re.search(pattern,s):return key
 return 'unclassified'
def kind(s):
 s=str(s or '').lower()
 if 'chase' in s or 'steeple' in s:return 'chase'
 if 'hurdle' in s:return 'hurdle'
 if 'bumper' in s or 'national hunt flat' in s:return 'bumper'
 if s in ('aw','all-weather','all weather'):return 'all-weather'
 if s=='flat':return 'flat (surface unknown)'
 return s or 'unknown'
def load(path):
 with open(path,newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))
def main():
 p=argparse.ArgumentParser();p.add_argument('--nov7',required=True);p.add_argument('--nov8',required=True);p.add_argument('--performance',required=True);p.add_argument('--out',required=True);a=p.parse_args()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 perf=json.loads(pathlib.Path(a.performance).read_text())['models']['D']
 lookup={(norm(r.get('course')),str(r.get('time') or str(r.get('race_time',''))[11:16])):r for r in perf}
 rows=[];audit=collections.Counter()
 for s in load(a.nov7):
  k=(norm(s['course']),s['time']);r=lookup.get(k)
  if not r or not r.get('pre_off_valid'):audit['nov7_missing_verified_A_or_result']+=1;continue
  runners=sorted([x for x in r.get('runners',[]) if isinstance(x.get('rank'),int)],key=lambda x:x['rank'])
  if len(runners)<2:audit['nov7_missing_ranked_runners']+=1;continue
  byhorse={norm(x.get('horse')):x for x in r['runners']}
  picks={'A':[runners[0]['horse'],runners[1]['horse']],'B':[s.get('b_rank1'),s.get('b_rank2')],'C':[s.get('c_rank1'),s.get('c_rank2')]}
  typ=category(s.get('race_name') or s.get('race_type'))
  if typ=='unclassified':typ=category(s.get('race_type'))
  discipline=kind(s.get('discipline') or r.get('race_type'))
  for model,pair in picks.items():
   for rank,horse in enumerate(pair,1):
    x=byhorse.get(norm(horse));sp=x.get('sp_decimal') if x else None
    if not x or not isinstance(sp,(float,int)) or sp<=0:audit['nov7_missing_runner_or_SP']+=1;continue
    win=x.get('position')==1 or x.get('status')=='Won'
    rows.append(dict(date='2026-11-07',course=s['course'],time=s['time'],model=model,rank=rank,horse=horse,race_type=typ,discipline=discipline,win=int(win),return_units=float(sp) if win else 0,source='stage66_preoff_BC_and_archived_preoff_D_A'))
 for s in load(a.nov8):
  if s.get('status')!='COMPARED':audit['nov8_excluded_'+s.get('status','unknown')]+=1;continue
  for model in 'ABC':
   for rank in (1,2):
    prefix=f'{model.lower()}_rank{rank}_';horse=s.get(prefix[:-1]);win=s.get(prefix+'win');ret=s.get(prefix+'return')
    if not horse or win not in ('0','1') or ret in ('',None):audit['nov8_missing_settlement']+=1;continue
    rows.append(dict(date='2026-11-08',course=s['course'],time=s['time'],model=model,rank=rank,horse=horse,race_type=category(s.get('race_type')),discipline=kind(s.get('discipline')),win=int(win),return_units=float(ret),source='stage66_nov8_settled_replay'))
 with (out/'race_by_race.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 stats=[]
 for groupby in ('overall','date','race_type','discipline','race_type_x_discipline','course'):
  groups=collections.defaultdict(list)
  for r in rows:
   label={'overall':'Overall','date':r['date'],'race_type':r['race_type'],'discipline':r['discipline'],'race_type_x_discipline':r['race_type']+' / '+r['discipline'],'course':r['course']}[groupby]
   groups[label].append(r)
  for label,rr in sorted(groups.items()):
   for model in 'ABC':
    for rank in ('1','2','Combined'):
     selected=[x for x in rr if x['model']==model and (rank=='Combined' or x['rank']==int(rank))]
     if not selected:continue
     bets=len(selected);wins=sum(x['win'] for x in selected);gross=sum(x['return_units'] for x in selected)
     stats.append(dict(grouping=groupby,category=label,model=model,rank=rank,bets=bets,wins=wins,strike_rate_pct=round(wins/bets*100,2),profit_units=round(gross-bets,2),roi_pct=round((gross/bets-1)*100,2)))
 with (out/'summary.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(stats[0]));w.writeheader();w.writerows(stats)
 report={'included_dates':sorted(set(r['date'] for r in rows)),'missing_date':'2026-11-06: Stage66 A/B/C race-level pre-off selections not supplied; not invented','exclusions':dict(audit),'model_A_nov7_source':'archived Model D pre-off ranking, not a Stage66 reconstructed A','warning':'7 November B/C selections from Stage66 pre-off snapshot. Model A uses archived D ranking. Compare only if model versions match intended A/B/C. No post-race selection generation. Nov 8 replay includes reconstructed A entries; inspect source before treating as live. Unclassified races not guessed.','rows':len(rows),'research_only':True,'production_unchanged':True}
 (out/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
