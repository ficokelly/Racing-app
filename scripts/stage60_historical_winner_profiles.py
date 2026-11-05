#!/usr/bin/env python3
"""Stage60: leak-safe historical winner profiles and frozen Model D top-two comparison.
Research only. Reranks the frozen D top two using prior editions; does NOT change top-two coverage.
"""
import argparse,csv,sqlite3,json,math,collections,datetime
from pathlib import Path

def num(x):
 try:return float(x)
 except (ValueError,TypeError):return None

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--mapping',required=True);p.add_argument('--baseline',required=True);p.add_argument('--out',required=True);p.add_argument('--min-editions',type=int,default=5);a=p.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 mapping={};bad=collections.Counter()
 with open(a.mapping,encoding='utf-8-sig') as f:
  for r in csv.DictReader(f):
   if r['status'] not in ('ANCHORED_CONTINUITY_CANDIDATE','ANCHORED_WITH_GAPS','TITLE_CONTINUITY_CANDIDATE'):continue
   rid=r['stage2_race_id'];event=r['recurring_event_id']
   if rid in mapping and mapping[rid]!=event:bad['duplicate_race_mapping']+=1;mapping.pop(rid,None)
   else:mapping[rid]=event
 cx=sqlite3.connect('file:'+str(Path(a.db).resolve())+'?mode=ro',uri=True)
 # Collect only mapped races, with runners and pre-race age/OR. Avoid huge Python per-runner archive.
 cx.execute('CREATE TEMP TABLE mapped(id TEXT PRIMARY KEY, event TEXT)')
 cx.executemany('INSERT OR IGNORE INTO mapped VALUES (?,?)',mapping.items())
 sql='''SELECT r.id,r.race_date,m.event,r.course,r.race_type,u.horse_name,u.age,u.official_rating,u.finish_pos,u.sp_decimal
 FROM mapped m JOIN historical_races r ON r.id=m.id JOIN historical_runs u ON u.race_id=r.id
 ORDER BY r.race_date,r.id'''
 history=collections.defaultdict(list); current_id=None;group=[];profiles={};race_lookup={};n=0
 def finish(group):
  nonlocal n
  if not group:return
  rid,day,event=group[0][:3]; starters=[r for r in group if r[8] is not None or (num(r[9]) or 0)>1]
  winners=[r for r in starters if r[8]==1]
  if len(winners)!=1 or len(starters)<2:return
  w=winners[0]; past=[x for x in history[event] if x['date']<day]
  # Each prior edition has one winner and all starters; winner-vs-starter baseline is essential.
  if len(past)>=a.min_editions:
   ages=[x['winner_age'] for x in past if x['winner_age'] is not None]
   all_ages=[z for x in past for z in x['ages'] if z is not None]
   ors=[x['winner_or'] for x in past if x['winner_or'] is not None]
   all_ors=[z for x in past for z in x['ors'] if z is not None]
   def lift(v, winners,starters,bucket):
    if v is None or len(winners)<3 or len(starters)<10:return 0.
    k=bucket(v);wc=sum(bucket(x)==k for x in winners);sc=sum(bucket(x)==k for x in starters)
    # smoothed ratio of winner share to runner share, shrink sparse evidence
    return max(-1.2,min(1.2,math.log(((wc+1)/(len(winners)+4))/((sc+1)/(len(starters)+4)))*len(winners)/(len(winners)+15)))
   age_bucket=lambda x:int(x)//2*2
   or_bucket=lambda x:int(x)//10*10
   for r in starters:
    horse=r[5];age=num(r[6]);rating=num(r[7]); key=(rid,horse.casefold().strip())
    race_lookup[key]={'event':event,'prior_editions':len(past),'age_lift':lift(age,ages,all_ages,age_bucket),'or_lift':lift(rating,ors,all_ors,or_bucket),'age':age,'or':rating,'date':day}
   profiles[rid]={'event':event,'date':day,'prior_editions':len(past),'winner_age_median':sorted(ages)[len(ages)//2] if ages else None,'winner_or_median':sorted(ors)[len(ors)//2] if ors else None,'prior_winners_with_age':len(ages),'prior_winners_with_or':len(ors)}
  history[event].append({'date':day,'winner_age':num(w[6]),'winner_or':num(w[7]),'ages':[num(x[6]) for x in starters],'ors':[num(x[7]) for x in starters]})
  n+=1
 for r in cx.execute(sql):
  if current_id is not None and r[0]!=current_id:finish(group);group=[]
  group.append(r);current_id=r[0]
 finish(group);cx.close()
 # Compare original D rank1 and rank2 with a strictly historical trend-assisted swap.
 rows=[];missing=collections.Counter()
 with open(a.baseline,encoding='utf-8-sig') as f:
  for r in csv.DictReader(f):
   rid=r['race_id'];profile=profiles.get(rid)
   if not profile:missing['no_eligible_prior_editions']+=1;continue
   h1=r['rank1_horse'];h2=r['rank2_horse'];v1=race_lookup.get((rid,h1.casefold().strip()));v2=race_lookup.get((rid,h2.casefold().strip()))
   if not v1 or not v2:missing['horse_name_join_failed']+=1;continue
   s1=v1['age_lift']+v1['or_lift'];s2=v2['age_lift']+v2['or_lift'];swap=s2-s1>0.25
   winner_rank=int(r['winner_rank']);winner_in_top2=winner_rank<=2
   sp1=num(r['rank1_sp']);sp2=num(r['rank2_sp']);basewin=winner_rank==1;newwin=winner_rank==(2 if swap else 1)
   rows.append({'date':r['date'],'discipline':r['discipline'],'race_id':rid,'event':profile['event'],'prior_editions':profile['prior_editions'],'d_rank1':h1,'d_rank2':h2,'d_rank1_trend':round(s1,4),'d_rank2_trend':round(s2,4),'swap':int(swap),'winner_in_top2':int(winner_in_top2),'d_rank1_won':int(basewin),'trend_rank1_won':int(newwin),'d_rank1_sp':sp1,'trend_rank1_sp':sp2 if swap else sp1,'d_rank1_return':sp1 if basewin and sp1 else 0,'trend_rank1_return':(sp2 if swap else sp1) if newwin and (sp2 if swap else sp1) else 0})
 def aggregate(rs):
  def roi(n,ret):return round(100*(ret/n-1),2) if n else None
  b=[x for x in rs if x['d_rank1_sp'] is not None];t=[x for x in rs if x['trend_rank1_sp'] is not None]
  return {'races':len(rs),'swaps':sum(x['swap'] for x in rs),'top2_winners':sum(x['winner_in_top2'] for x in rs),'top2_coverage_pct':round(100*sum(x['winner_in_top2'] for x in rs)/len(rs),2) if rs else None,'d_rank1_winners':sum(x['d_rank1_won'] for x in rs),'trend_rank1_winners':sum(x['trend_rank1_won'] for x in rs),'d_rank1_roi_pct':roi(len(b),sum(x['d_rank1_return'] for x in b)),'trend_rank1_roi_pct':roi(len(t),sum(x['trend_rank1_return'] for x in t))}
 by=collections.defaultdict(list)
 for x in rows:by[(x['date'][:4],x['discipline'])].append(x)
 report={'status':'RESEARCH_ONLY','race_groups_processed':n,'mapped_races':len(mapping),'eligible_historical_race_profiles':len(profiles),'evaluated_baseline_races':len(rows),'excluded':dict(missing),'overall':aggregate(rows),'by_year_discipline':{'|'.join(k):aggregate(v) for k,v in sorted(by.items())},'limitations':['Only reorders frozen D top two, not all runners; therefore top-two winner coverage cannot change.','Age and official rating are initial signals, not an exhaustive race-trend engine.','Prior editions strictly before race date; no future result leakage.','Candidate event IDs are not independently verified; results are exploratory.','Historical starters may differ from pre-off declarations.','No live code or database modified.']}
 with open(out/'stage60_per_race.csv','w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['race_id']);w.writeheader();w.writerows(rows)
 with open(out/'stage60_profiles.csv','w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=['race_id']+list(next(iter(profiles.values()))) if profiles else ['race_id']);w.writeheader();w.writerows([{'race_id':k,**v} for k,v in profiles.items()])
 (out/'stage60_summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
