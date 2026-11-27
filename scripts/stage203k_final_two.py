"""Scoped, full-field verified repairs. No model or HPR formula changes."""
import collections as C, datetime as D, json, os, pathlib as P, sqlite3
from scripts import stage203j_domestic_recovery as J, stage203c_consolidated_history as V, stage203_identity_recovery as E
from scripts.rhp_runner_eligibility import eligibility
import import_historical_ukie as I
TARGET='2026-11-28'; ROOT=P.Path('data'); LEDGER=ROOT/'stage203k-history'/'approved-2026-11-28.json'
RESULT='https://www.sportinglife.com/racing/results/2023-06-22/chelmsford-city/744029/ccr-ladies-day-2023-restricted-maiden-fillies-stakes-gbb-race'
PROFILE='https://www.sportinglife.com/racing/profiles/horse/'
# Full weighed-in field; pedigree from individually linked profiles. No invented provider IDs.
# name, profile ID, sire, dam, SP, actual trainer, actual jockey, carried lb, draw
FIELD=[
 ('Paradise Row','1066326','Zoffany','Poplar Close','4/1','William Knight','Callum Shepherd',126,7),
 ('Eloped','1084210','Golden Horn',"Mary's Choice",'28/1','Hugo Palmer','Ross Coakley',128,2),
 ('Third Batch','1082493','Expert Eye','Amanda Carter','10/1','S Woods','Robert Tart',128,11),
 ('Oh So Grand','1096572','Postponed','Lady Zonda','12/1','S Crisford','Harry Burns',126,8),
 ('Pourquoi','1102163','Pour Moi',"St Mary's",'18/5','David Simcock','Joey Haynes',128,1),
 ('I Am Legend','1107386','Barocci','Rock The Legend','33/1','Jane Chapple-Hyam','Dayverson De Barros',126,9),
 ('Style Of Life','1078377','New Bay','Caterina Di Cesi','14/1','J A Osborne','Nicola Currie',128,6),
 ('Huda Star','1097389','Teofilo','Bingfling','2/1','Richard Hughes','Finley Marsh',128,10),
 ('Hill Filly','1079320','Kingston Hill','Respectfilly','17/2','Hugh Morrison','Jason Hart',128,5),
 ('Portraitist','1072050','Awtaad','Lyric Piece','18/1','David Loughnane','Jack Mitchell',128,3),
 ("Arlecchino's Star",'1061196','Zoustar','Sandy Cay','200/1','Mark Usher',"Dane O'Neill",126,4),
]
SPECS={s['horse']:s for s in J.SPECS}
def load(root=ROOT):
 p=root/'stage203k-history'/'approved-2026-11-28.json'
 if not p.exists():return []
 z=json.loads(p.read_text());assert z['schema']=='stage203k-v1'
 for e in z['entries']:assert V.digest(e['payload'])==e['sha256']
 return z['entries']
def append(db,ref,src,target,root=ROOT):
 r=J.apply(db,ref,load(root),target);print('STAGE203K_SHARED_HISTORY',r,flush=True);return r
def audit(db,rows):return J.audit(db,rows)
def candidate_liege(c,s,card):
 found=J.existing(c,s)
 if len(found)!=1:return None,{'reason':'FULL_FIELD_NOT_UNIQUE','count':len(found)}
 rid,by=found[0];x=by[V.n(s['horse'])]
 # Scoped alias only: exact full field, fifth place, named sire and independently verified dam.
 # Null source ID is allowed because historic Kaggle rows often lack it. Never overwrite another known ID.
 oldid=x[2];sire=V.n(x[17]);dam=V.n(x[18]);
 if oldid not in (None,'',s['oldid'],s['hid']) or sire!=V.n(s['sire']) or dam not in (V.n(s['olddam']),V.n(s['dam'])):
  return None,{'reason':'IDENTITY_STILL_CONFLICTS','old_id':oldid,'sire':x[17],'dam':x[18],'race':rid}
 y=list(x);y[2]=card['horse_id'];y[17]=card['sire'];y[18]=card['dam']
 e=J.entry(s,'repair',[x,y]);e['identity_sources']=['https://france-sire.com/cheval-391909-liege_ardent.php','https://www.irishracing.com/horse/Liege-Ardent-FR/800922'];return e,{}
def candidate_legend(c,s,card):
 # First check exact whole-field race even when the venue spelling is unusual.
 exp=J.public(s);cands=[];overlaps=[]
 for rid,co in c.execute('select id,course from historical_races where race_date=?',(s['date'],)).fetchall():
  rows=J.query_field(c,rid)
  names={V.n(x[3]) for x in rows if J.norm_state(x)[0]!='NR'}
  if names & set(exp):overlaps.append({'race':rid,'course':co,'matched_names':len(names&set(exp))})
  if not V.n(co).startswith('chelmsford'):continue
  try:by=J.field(rows)
  except AssertionError:continue
  if J.compatible(s,by):cands.append((rid,by))
 if len(cands)==1:
  rid,by=cands[0];x=by[V.n(s['horse'])]
  if not E.exact(card,V.aid(x)) and not (not x[2] and V.n(x[17])==V.n(s['sire']) and V.n(x[18])==V.n(s['dam'])):
   return None,{'reason':'EXISTING_IDENTITY_CONFLICT','old_id':x[2],'sire':x[17],'dam':x[18]}
  y=list(x);y[2]=card['horse_id'];y[17]=card['sire'];y[18]=card['dam'];return J.entry(s,'repair',[x,y]),{}
 if cands or overlaps:return None,{'reason':'EXISTING_FIELD_OVERLAP_HELD','matches':overlaps}
 # Source race absent entirely: add the complete independently verified public field.
 # Other runners retain null source IDs unless an exact pedigree match provides a unique existing ID.
 rid='stage203:stage203k:2023-06-22:rac_10950069'
 race=[rid,s['date'],'Chelmsford','Flat','AW',10.0,'Standard','5','STAGE203K_PUBLIC_CORROBORATED'];runs=[]
 for pos,(name,profile,sire,dam,sp,trainer,jockey,w,draw) in enumerate(FIELD,1):
  hid=None
  if name==s['horse']:hid=card['horse_id'];sire=card['sire'];dam=card['dam']
  else:
   ids={x[0] for x in c.execute('select distinct horse_source_id,sire,dam from historical_runs where horse_name=? and horse_source_id is not null',(name,)) if V.n(x[1])==V.n(sire) and V.n(x[2])==V.n(dam)}
   if len(ids)==1:hid=next(iter(ids))
  key='id:'+hid if hid else 'name:'+I.normname(name)
  runs.append([rid,key,hid,name,I.normname(name),None,None,pos,'FINISHED',trainer,jockey,None,float(w),I.odds(sp),draw,3,'F',sire,dam,None,None,None,None])
 assert len({x[1] for x in runs})==11 and len({x[7] for x in runs})==11
 e=J.entry(s,'append',[race,sorted(runs,key=lambda x:x[1]),s['title'],'rac_10950069']);e['sources']=[RESULT,'https://globalformbook.com/Home/RaceResultDetail/rac_10950069']+[PROFILE+x[1] for x in FIELD];return e,{}
def main():
 from stage167_validated_history import refreshed_inputs
 out=P.Path('reports/stage203k/output');out.mkdir(parents=True,exist_ok=True)
 rows=[r for r in json.loads((ROOT/'racecards.json').read_text())['runners'] if r['date']==TARGET and eligibility(r)=='ACTIVE'];cards={r['horse_id']:r for r in rows};assert rows and len(cards)==len(rows)
 refs=list(P.Path('reports/stage203k/ref').rglob('all_races_with_reference_event_ids.csv'));assert len(refs)==1
 base=P.Path('reports/stage152-specialists/source/stage2/historical_ukie_staging.sqlite');old={e['horse']:e for e in load()};decisions=[];approved=[]
 with refreshed_inputs(base,refs[0],TARGET) as (db,ref):
  before=audit(db,rows)
  for h,fn in [('Liege Ardent',candidate_liege),('I Am Legend',candidate_legend)]:
   s=SPECS[h];card=cards.get(s['hid'])
   if not card or V.n(card['horse'])!=V.n(h) or E.expected(card,TARGET)!=s['date'] or V.n(card['sire'])!=V.n(s['sire']) or V.n(card['dam'])!=V.n(s['dam']):decisions.append({'horse':h,'status':'CARD_CHANGED'});continue
   if before[s['hid']]['last_run_matched'] is True:decisions.append({'horse':h,'status':'ALREADY_MATCHED'});continue
   with sqlite3.connect(db) as c:e,why=fn(c,s,card)
   if not e:decisions.append({'horse':h,'status':'HELD',**why});continue
   r=J.apply(db,ref,[e],TARGET)[0];decisions.append(r)
   if r['status'] in ('ADDED','REPAIRED_EXISTING'):
    assert audit(db,rows)[s['hid']]['last_run_matched'] is True
    assert J.apply(db,ref,[e],TARGET)[0]['status']=='ALREADY_PRESENT'
    approved.append(e);old[h]=e
  after=audit(db,rows);assert all(after[k]['starts']>=before[k]['starts'] and (before[k]['last_run_matched'] is not True or after[k]['last_run_matched'] is True) for k in before)
  if approved:
   LEDGER.parent.mkdir(parents=True,exist_ok=True);V.dump(LEDGER,{'schema':'stage203k-v1','entries':list(old.values())})
  summary={'target':TARGET,'active':len(rows),'published_candidates':len(approved),'decisions':decisions,'raw_gaps':sum(x['last_run_matched'] is False or x['known_no_history'] for x in after.values()),'remaining':[x for x in after.values() if x['last_run_matched'] is False or x['known_no_history']],'idempotence_passed':True,'hpr_replay_required':True}
  V.dump(out/'summary.json',summary);print(json.dumps(summary,indent=2),flush=True)
  with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Stage203K results\n```json\n'+json.dumps(summary,indent=2)+'\n```\n')
if __name__=='__main__':main()
