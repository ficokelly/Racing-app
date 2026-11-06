#!/usr/bin/env python3
"""Research-only C revision. Frozen D is scored unchanged, with a separately
trained, race-type-aware form model. No production writes or future results.
Training: 2024; validation: 2025; evaluation: Nov 5/6 2026.
"""
import argparse,collections,csv,datetime as dt,json,math,pathlib,sqlite3,sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import stage65m_three_way_replay as M
import stage30_gap_aware_abcd as OLD
from stage38_model_dz_live import LivePast,live_row,prediction_rows,name

FEATURES=['category_experience','category_win_rate','category_place_rate','discipline_experience','discipline_win_rate','last3_place_rate','last3_finish_percentile','last3_improvement','days_since_last','or_change','best_win_mark_gap','or_vs_field','weight_vs_field','class_change','last3_beaten_lengths','recent_rpr_change','switch_to_chase','switch_to_hurdle','trainer_category_rate','jockey_category_rate']

def category(s):
 s=str(s or '').lower()
 if 'handicap' in s or 'nursery' in s:return 'handicap'
 if 'maiden' in s:return 'maiden'
 if 'novice' in s:return 'novice'
 if 'listed' in s:return 'listed'
 if 'group ' in s or 'grade ' in s:return 'graded'
 if 'bumper' in s or 'national hunt flat' in s:return 'bumper'
 if 'seller' in s or 'selling' in s:return 'seller'
 if 'claimer' in s or 'claiming' in s:return 'claimer'
 if 'conditions' in s:return 'conditions'
 return 'unknown'

def discipline(s,typ):
 s=(str(s or '')+' '+str(typ or '')).lower()
 if 'chase' in s or 'steeple' in s:return 'chase'
 if 'hurdle' in s:return 'hurdle'
 if 'bumper' in s or 'national hunt flat' in s:return 'bumper'
 if 'all weather' in s or 'all-weather' in s or 'polytrack' in s or 'tapeta' in s or 'standard' in s:return 'aw'
 return 'flat'

def num(x):
 try:return float(x) if x is not None and str(x).strip() else None
 except (ValueError,TypeError):return None

def cls(x):
 s=str(x or '').lower()
 import re
 m=re.search(r'(?:class|grade)\s*([1-6])',s)
 return float(m.group(1)) if m else None

def beaten(x):
 v=num(x)
 if v is not None:return v
 import re
 s=str(x or '').lower().strip()
 if s in ('nk','neck'):return .3
 if s in ('sh','short head'):return .1
 if s in ('hd','head'):return .2
 m=re.match(r'^(\d+(?:\.\d+)?)\s*(?:l|lengths?)?$',s)
 return float(m.group(1)) if m else None

class Form:
 def __init__(self):
  self.h=collections.defaultdict(list);self.tr=collections.defaultdict(lambda:[0,0]);self.jo=collections.defaultdict(lambda:[0,0]);self.audit=collections.Counter()
 def vector(self,r,field_or=None,field_weight=None):
  # Historical row keys: date,horse,country,trainer,jockey,race_type,surface,or,weight,class,finish,field_size,beaten,rpr.
  key=(name(r.get('horse')),name(r.get('country')));hist=self.h[key];cat=category(r.get('race_type'));disc=discipline(r.get('surface'),r.get('race_type'))
  same=[x for x in hist if x['cat']==cat];code=[x for x in hist if x['disc']==disc];recent=hist[-3:];last=hist[-1] if hist else None
  rate=lambda a,kind:sum(x[kind] for x in a)/len(a) if a else 0.
  fp=lambda x:1-(x['finish']-1)/max(1,x['field_size']-1) if x.get('finish') else 0.
  current_or=num(r.get('or'));current_weight=num(r.get('weight'))
  last_or=last['or'] if last else None
  winmarks=[x['or'] for x in hist if x['win'] and x['or'] is not None and x['cat']=='handicap']
  last_class=last['class'] if last else None;cur_class=cls(r.get('race_class'))
  rprs=[x['rpr'] for x in recent if x['rpr'] is not None]
  beats=[x['beaten'] for x in recent if x['beaten'] is not None]
  tr=self.tr.get((name(r.get('trainer')),cat),(0,0));jo=self.jo.get((name(r.get('jockey')),cat),(0,0))
  vals=[math.log1p(len(same)),rate(same,'win'),rate(same,'placed'),math.log1p(len(code)),rate(code,'win'),rate(recent,'placed'),sum(fp(x) for x in recent)/len(recent) if recent else 0.,fp(recent[-1])-fp(recent[0]) if len(recent)>1 else 0.,min(365,(dt.date.fromisoformat(r['date'])-dt.date.fromisoformat(last['date'])).days)/365 if last else 1.,(current_or-last_or)/20 if current_or is not None and last_or is not None else 0.,(max(winmarks)-current_or)/20 if winmarks and current_or is not None else 0.,(current_or-field_or)/20 if current_or is not None and field_or is not None else 0.,(current_weight-field_weight)/14 if current_weight is not None and field_weight is not None else 0.,(cur_class-last_class)/3 if cur_class is not None and last_class is not None else 0.,min(30,sum(beats)/len(beats))/30 if beats else 0.,(rprs[-1]-rprs[0])/25 if len(rprs)>1 else 0.,int(disc=='chase' and bool(hist) and not code),int(disc=='hurdle' and bool(hist) and not code),(tr[0]+2)/(tr[1]+16),(jo[0]+2)/(jo[1]+16)]
  # Crucial: use context only where relevant, never treat missing OR as zero-rated ability.
  if cat!='handicap':
   for idx in (9,10,11,12):vals[idx]=0.
  if cat not in ('maiden','novice'):
   vals[7]*=.5
  return vals,{'category':cat,'discipline':disc,'history_starts':len(hist),'category_starts':len(same),'or_available':current_or is not None,'weight_available':current_weight is not None,'beaten_available':bool(beats),'rpr_available':len(rprs)>1}
 def update(self,r):
  cat=category(r.get('race_type'));disc=discipline(r.get('surface'),r.get('race_type'));pos=num(r.get('finish'))
  if pos is None or pos<=0:return
  win=int(pos==1);place=int(pos<=3)
  key=(name(r.get('horse')),name(r.get('country')))
  self.h[key].append({'date':r['date'],'cat':cat,'disc':disc,'win':win,'placed':place,'finish':pos,'field_size':int(r.get('field_size') or 8),'or':num(r.get('or')),'class':cls(r.get('race_class')),'beaten':beaten(r.get('beaten')),'rpr':num(r.get('rpr'))})
  if len(self.h[key])>20:self.h[key]=self.h[key][-20:]
  for store,person in ((self.tr,r.get('trainer')),(self.jo,r.get('jockey'))):
   if person:
    x=store[(name(person),cat)];x[0]+=win;x[1]+=1

def rows(db,through):
 cx=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
 sql='''SELECT r.race_date,r.id,r.course,r.race_type,r.surface,r.race_class,u.horse_name,u.horse_country,u.trainer,u.jockey,u.official_rating,u.weight_lbs,u.finish_pos,u.beaten,u.rpr FROM historical_races r JOIN historical_runs u ON r.id=u.race_id WHERE r.race_date >= '2021-01-01' AND r.race_date < ? ORDER BY r.race_date,r.id'''
 current=None;group=[]
 for raw in cx.execute(sql,(through,)):
  if current and raw[1]!=current:
   yield group;group=[]
  current=raw[1]
  day,rid,course,typ,surf,klass,horse,country,tr,jo,orr,weight,finish,bn,rpr=raw
  group.append({'date':day,'race_id':rid,'course':course,'race_type':typ,'surface':surf,'race_class':klass,'horse':horse,'country':country,'trainer':tr,'jockey':jo,'or':orr,'weight':weight,'finish':finish,'beaten':bn,'rpr':rpr})
 if group:yield group
 cx.close()

def build_training(db):
 form=Form();X=[];y=[];years=[];counts=collections.Counter();missing=collections.Counter()
 for group in rows(db,'2026-01-01'):
  date=group[0]['date'];year=int(date[:4]);valid=[r for r in group if num(r.get('finish')) is not None and num(r.get('finish'))>0]
  if len(valid)<2:continue
  ors=[num(r.get('or')) for r in valid];weights=[num(r.get('weight')) for r in valid]
  avg_or=sum(z for z in ors if z is not None)/sum(z is not None for z in ors) if any(z is not None for z in ors) else None
  avg_w=sum(z for z in weights if z is not None)/sum(z is not None for z in weights) if any(z is not None for z in weights) else None
  pending=[]
  for r in valid:
   r['field_size']=len(valid);v,info=form.vector(r,avg_or,avg_w);pending.append((r,v,info))
  if year in (2024,2025) and sum(num(r['finish'])==1 for r in valid)==1:
   for r,v,info in pending:
    X.append(v);y.append(int(num(r['finish'])==1));years.append(year);counts[(year,info['category'])]+=1
    for field in ('or_available','weight_available','beaten_available','rpr_available'):
     if not info[field]:missing[(year,field)]+=1
  # Same race results are only made available AFTER all its runners' features were computed.
  for r,_,_ in pending:form.update(r)
 return np.asarray(X,dtype=float),np.asarray(y,dtype=int),np.asarray(years),counts,missing

def live_features(db,day,entries):
 form=Form()
 for group in rows(db,day):
  for r in group:
   r['field_size']=len(group);form.update(r)
 result={}
 for e in entries:
  rr=e['runners'];ors=[num(z.get('official_rating') or z.get('or') or z.get('ofr')) for z in rr];ws=[num(z.get('weight_lbs')) for z in rr]
  avgo=sum(z for z in ors if z is not None)/sum(z is not None for z in ors) if any(z is not None for z in ors) else None
  avgw=sum(z for z in ws if z is not None)/sum(z is not None for z in ws) if any(z is not None for z in ws) else None
  vec=[];aud=[]
  for z in rr:
   r={'date':day,'horse':z.get('horse'),'country':z.get('horse_country'),'trainer':z.get('trainer'),'jockey':z.get('jockey'),'race_type':z.get('race_name') or z.get('race_type') or z.get('type'),'surface':z.get('surface'),'race_class':z.get('race_class'),'or':z.get('official_rating') or z.get('or') or z.get('ofr'),'weight':z.get('weight_lbs')}
   v,a=form.vector(r,avgo,avgw);vec.append(v);aud.append(a)
  result[(e['date'],name(e['course']),e['time'])]=(np.asarray(vec),aud)
 return result

def main():
 p=argparse.ArgumentParser();p.add_argument('--updated',required=True);p.add_argument('--out',required=True);a=p.parse_args();out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
 X,y,years,counts,missing=build_training(a.updated)
 train=years==2024;val=years==2025
 if train.sum()<100 or val.sum()<100 or y[train].sum()<10:raise RuntimeError('Insufficient time-separated historical training data')
 scaler=StandardScaler().fit(X[train]);model=LogisticRegression(C=.1,max_iter=500,class_weight=None).fit(scaler.transform(X[train]),y[train])
 val_pred=model.predict_proba(scaler.transform(X[val]))[:,1]
 # Use held-out 2025 for diagnostics only; no tuning on 2026 outcomes.
 from sklearn.metrics import roc_auc_score,log_loss
 validation={'2025_rows':int(val.sum()),'auc':float(roc_auc_score(y[val],val_pred)),'log_loss':float(log_loss(y[val],val_pred))}
 perf=M.K.performance();entries=M.load_cards(perf)
 cfg=json.loads(pathlib.Path('reports/stage30/stage30-summary.json').read_text())['model_results']['D'];weights=[float(cfg['weights_pct'][x])/100 for x in OLD.METRICS]
 detail=[];audit=collections.Counter()
 for day in M.K.DAYS:
  dayrows=[e for e in entries if e['date']==day and e['status']=='READY']
  if not dayrows:continue
  past=LivePast({});past.scan_until(a.updated,day)
  unique={}
  for h,c in past.last:
   if h not in unique:unique[h]=c
   elif unique[h]!=c:unique[h]=None
  feats=live_features(a.updated,day,dayrows)
  for e in dayrows:
   rr=e['runners'];key=(e['date'],name(e['course']),e['time']);mat,info=feats[key]
   race=past.score([live_row(z,day,'STAGE65N|'+day+'|'+e['course']+'|'+e['time'],unique) for z in rr],live=True)
   if race is None:e['status']='UNSCORABLE';continue
   ds,order,_=prediction_rows(race,weights)
   learned=model.predict_proba(scaler.transform(mat))[:,1]
   # Normalize both scores within each race; avoid arbitrary scale mismatches.
   dz=(ds-ds.mean())/(ds.std()+1e-8);lz=(learned-learned.mean())/(learned.std()+1e-8)
   # Conservative fixed blend, chosen before Nov 5/6 evaluation.
   combined=.75*dz+.25*lz
   rank=sorted(range(len(rr)),key=lambda i:(-combined[i],i))
   e['c_rank1']=rr[rank[0]]['horse'];e['c_rank2']=rr[rank[1]]['horse'];e['status']='COMPARED'
   for i,z in enumerate(rr):
    audit[info[i]['category']]+=1
    detail.append({'date':day,'course':e['course'],'time':e['time'],'horse':z['horse'],'race_category':info[i]['category'],'discipline':info[i]['discipline'],'history_starts':info[i]['history_starts'],'category_starts':info[i]['category_starts'],'or_available':info[i]['or_available'],'weight_available':info[i]['weight_available'],'beaten_available':info[i]['beaten_available'],'rpr_available':info[i]['rpr_available'],'frozen_d_score':float(ds[i]),'category_model_probability':float(learned[i]),'combined_score':float(combined[i]),'rank_c':rank.index(i)+1})
 for e in entries:
  if e['status']!='COMPARED':continue
  settlement={name(z['horse']):z for z in perf[(e['date'],name(e['course']),e['time'])].get('runners',[])}
  for rank in (1,2):
   z=settlement.get(name(e['c_rank'+str(rank)]))
   if not z:e['status']='MISSING_SETTLEMENT';break
   won=int(str(z.get('status','')).lower()=='won' or z.get('position')==1);sp=z.get('sp_decimal')
   if won and not isinstance(sp,(int,float)):e['status']='WINNER_SP_MISSING';break
   e['c_rank'+str(rank)+'_win']=won;e['c_rank'+str(rank)+'_return']=float(sp) if won else 0.
 def write(path,items):
  if not items:return
  with path.open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=list(items[0]),extrasaction='ignore');w.writeheader();w.writerows(items)
 public=[]
 for e in entries:
  public.append({k:v for k,v in e.items() if k!='runners'})
 write(out/'c_race_by_race.csv',public);write(out/'c_runner_scores.csv',detail)
 stats=[];valid=[e for e in entries if e['status']=='COMPARED'];groups={'Combined':valid,'2026-11-05':[e for e in valid if e['date']=='2026-11-05'],'2026-11-06':[e for e in valid if e['date']=='2026-11-06']}
 for typ in sorted({M.cat(e.get('race_name') or e.get('race_type')) for e in valid}):groups['Type: '+typ]=[e for e in valid if M.cat(e.get('race_name') or e.get('race_type'))==typ]
 for course in sorted({e['course'] for e in valid}):groups['Track: '+course]=[e for e in valid if e['course']==course]
 for group,rs in groups.items():
  for ranks,label in [([1],'1'),([2],'2'),([1,2],'combined')]:
   bets=len(rs)*len(ranks);wins=sum(e['c_rank'+str(k)+'_win'] for e in rs for k in ranks);gross=sum(e['c_rank'+str(k)+'_return'] for e in rs for k in ranks)
   stats.append({'group':group,'version':'C-race-type-trained','rank':label,'races':len(rs),'bets':bets,'wins':wins,'strike_rate_pct':round(100*wins/bets,2) if bets else '', 'sp_roi_pct':round(100*(gross/bets-1),2) if bets else ''})
 write(out/'c_comparison_summary.csv',stats)
 report={'research_only':True,'production_unchanged':True,'trained_on':'2024','validated_on':'2025','evaluated_on':['2026-11-05','2026-11-06'],'frozen_model_d_unchanged':True,'experimental_C_blend':'75% within-race normalized frozen D + 25% within-race normalized trained category-aware form probability','validation':validation,'training_categories':{str(k):v for k,v in counts.items()},'missing_training_fields':{str(k):v for k,v in missing.items()},'live_categories':dict(audit),'status_counts':dict(collections.Counter(e['status'] for e in entries)),'feature_names':FEATURES,'trained_coefficients':dict(zip(FEATURES,[float(v) for v in model.coef_[0]])),'caveats':['Race type is parsed from available race name/type, not verified official conditions','Some historic fields may be missing; missingness is audited','No named-event identity or genuine race video analysis','Only verified pre-off snapshots are evaluated','C is experimental and does not replace frozen D']}
 (out/'c_training_audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'validation':validation,'statuses':report['status_counts'],'live_categories':report['live_categories']},indent=2))
if __name__=='__main__':main()
