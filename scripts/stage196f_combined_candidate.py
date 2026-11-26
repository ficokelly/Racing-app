# Stage196F scoring with Stage206 input checks and the Stage207 G coverage exception.
import csv,datetime as dt,json,os,pathlib as P,re,subprocess as S,sys,unicodedata,collections as C,sqlite3,hashlib,shutil
from zoneinfo import ZoneInfo
ROOT=P.Path.cwd(); W=(ROOT/'stage196-work').resolve(); O=(ROOT/'stage196-output').resolve(); O.mkdir(exist_ok=True); W.mkdir(exist_ok=True)
DAY=os.environ['TARGET_DAY']; UTC=dt.timezone.utc
REPLAY=os.environ.get('STAGE196F_REPLAY','')
REPLAY_MODES={'stage206','stage196f','mullins-before','history-before','identity','stage208'}
if REPLAY and (REPLAY not in REPLAY_MODES or DAY!='2026-11-26'):
 raise ValueError('Unexpected controlled replay configuration')
REPLAY_CLOCK='2026-11-26T09:38:00+00:00'
NOW=lambda:dt.datetime.fromisoformat(REPLAY_CLOCK) if REPLAY else dt.datetime.now(UTC)
REPAIR_IDENTITIES=REPLAY in ('','identity','stage208')
USE_STAGE208=REPLAY in ('','stage208')
from stage196f_identity import IdentityPlan, MULLINS_ID, MULLINS_NAMES, archive_people, stage208_events
# Stage189c static simulation route evidence (rank 1, 25 Oct–23 Nov 2026).
routes={
 'AW_AUCTION_RESTRICTED_SALES': {'model': 'D137_HPR_ABILITY_GRADE', 'roi_pct': 129.2361, 'strike_rate_pct': 55.0, 'priced': 20},
 'AW_HANDICAP': {'model': 'E92_PRESET_PEOPLE_HEAVY', 'roi_pct': 4.6116, 'strike_rate_pct': 25.3247, 'priced': 153},
 'AW_LISTED_PATTERN': {'model': 'D137_HPR_ABILITY_GRADE', 'roi_pct': 266.6667, 'strike_rate_pct': 66.6667, 'priced': 3},
 'AW_MAIDEN': {'model': 'Z', 'roi_pct': 181.4815, 'strike_rate_pct': 66.6667, 'priced': 3},
 'AW_NOVICE': {'model': 'D_FORM_DEBUT_EIGHT_PILLARS__SR', 'roi_pct': 103.4156, 'strike_rate_pct': 40.7407, 'priced': 27},
 'AW_NURSERY': {'model': 'D_FORM_WEIGHT_OR_GRADE__ROI', 'roi_pct': 49.4048, 'strike_rate_pct': 30.4348, 'priced': 21},
 'AW_OTHER_OPEN': {'model': 'E92_PRESET_FROZEN_D_PRIOR', 'roi_pct': 775.0, 'strike_rate_pct': 100.0, 'priced': 2},
 'AW_SELLING_CLAIMING': {'model': 'D2_ENHANCED_ALL', 'roi_pct': 200.0, 'strike_rate_pct': 50.0, 'priced': 2},
 'CHASE_BEGINNERS': {'model': 'G5_ADAPTIVE_QUARTERLY', 'roi_pct': 43.259, 'strike_rate_pct': 62.5, 'priced': 16},
 'CHASE_GRADE_1': {'model': 'D2_ENHANCED_ALL', 'roi_pct': 270.0, 'strike_rate_pct': 66.6667, 'priced': 3},
 'CHASE_GRADE_2': {'model': 'D_FORM_WEIGHT_OR_GRADE__ROI', 'roi_pct': 138.0682, 'strike_rate_pct': 83.3333, 'priced': 6},
 'CHASE_GRADE_3': {'model': 'D2_ENHANCED_ALL', 'roi_pct': 250.0, 'strike_rate_pct': 50.0, 'priced': 2},
 'CHASE_HANDICAP': {'model': 'G5_COMBINED_QUARTERLY', 'roi_pct': 26.4682, 'strike_rate_pct': 26.6667, 'priced': 135},
 'CHASE_LISTED': {'model': 'G2_FROZEN', 'roi_pct': 261.1111, 'strike_rate_pct': 66.6667, 'priced': 3},
 'CHASE_NOVICE': {'model': 'D137_HPR_ABILITY_OR', 'roi_pct': 216.6667, 'strike_rate_pct': 100.0, 'priced': 2},
 'CHASE_NOVICE_HANDICAP': {'model': 'STAGE143_TYPE_EVIDENCE', 'roi_pct': 187.5, 'strike_rate_pct': 100.0, 'priced': 1},
 'CHASE_OTHER_OPEN': {'model': 'G', 'roi_pct': 195.0, 'strike_rate_pct': 100.0, 'priced': 2},
 'HURDLE_GRADE_1': {'model': 'C_HISTORICAL_CORE', 'roi_pct': 16.6667, 'strike_rate_pct': 100.0, 'priced': 1},
 'HURDLE_GRADE_2': {'model': 'G4_BLENDED', 'roi_pct': 137.5, 'strike_rate_pct': 40.0, 'priced': 5},
 'HURDLE_GRADE_3': {'model': 'G4_BLENDED', 'roi_pct': 155.7692, 'strike_rate_pct': 100.0, 'priced': 2},
 'HURDLE_HANDICAP': {'model': 'E_75_25_NO_HPR', 'roi_pct': -1.1163, 'strike_rate_pct': 21.0191, 'priced': 156},
 'HURDLE_JUVENILE': {'model': 'G4_SPECIALIST', 'roi_pct': 136.7347, 'strike_rate_pct': 28.5714, 'priced': 7},
 'HURDLE_LISTED': {'model': 'G5_ADAPTIVE_QUARTERLY', 'roi_pct': 230.0, 'strike_rate_pct': 80.0, 'priced': 5},
 'HURDLE_MAIDEN': {'model': 'G2_FROZEN', 'roi_pct': 5.7408, 'strike_rate_pct': 46.2687, 'priced': 66},
 'HURDLE_NOVICE': {'model': 'D2_ENHANCED_ALL', 'roi_pct': 166.0, 'strike_rate_pct': 60.0, 'priced': 10},
 'HURDLE_NOVICE_HANDICAP': {'model': 'G5_STRENGTH_QUARTERLY', 'roi_pct': 375.0, 'strike_rate_pct': 50.0, 'priced': 4},
 'HURDLE_OTHER_OPEN': {'model': 'E_75_25_WITH_HPR', 'roi_pct': 18.7275, 'strike_rate_pct': 58.3333, 'priced': 36},
 'HURDLE_RATED': {'model': 'D137_HPR_ABILITY_GRADE', 'roi_pct': 175.0, 'strike_rate_pct': 100.0, 'priced': 1},
 'NH_FLAT_MAIDEN': {'model': 'G3_LEARNED', 'roi_pct': 225.0, 'strike_rate_pct': 50.0, 'priced': 2},
 'NH_FLAT_OPEN': {'model': 'D137_HPR_ABILITY_OR', 'roi_pct': 22.0551, 'strike_rate_pct': 41.0256, 'priced': 39},
 'NH_FLAT_PATTERN': {'model': 'D_FROZEN_RANK1', 'roi_pct': 225.0, 'strike_rate_pct': 100.0, 'priced': 1},
 'TURF_AUCTION_RESTRICTED_SALES': {'model': 'C_HISTORICAL_CORE', 'roi_pct': -100.0, 'strike_rate_pct': 0.0, 'priced': 1},
 'TURF_GROUP_3': {'model': 'C_HISTORICAL_CORE', 'roi_pct': -4.5455, 'strike_rate_pct': 50.0, 'priced': 2},
 'TURF_HANDICAP': {'model': 'D137_HPR_ABILITY_OR_WEIGHT', 'roi_pct': 81.8841, 'strike_rate_pct': 23.4043, 'priced': 46},
 'TURF_LISTED': {'model': 'D2_ENHANCED_DISCIPLINE', 'roi_pct': 57.5, 'strike_rate_pct': 40.0, 'priced': 5},
 'TURF_MAIDEN': {'model': 'G', 'roi_pct': 158.3333, 'strike_rate_pct': 50.0, 'priced': 4},
 'TURF_NOVICE': {'model': 'D2_ENHANCED_ALL', 'roi_pct': 11.2603, 'strike_rate_pct': 36.3636, 'priced': 11},
 'TURF_NURSERY': {'model': 'G5_ADAPTIVE_QUARTERLY', 'roi_pct': 152.5, 'strike_rate_pct': 60.0, 'priced': 5},
}
dundalk_maiden_fallback={'model': 'E92_PRESET_PEOPLE_HEAVY', 'roi_pct': 56.615, 'strike_rate_pct': 45.4545, 'priced': 22}
pin='a5987838b0e3f28b53686bac60d52cd164738718'; workflow_pin='278c2d1a3065f6b32ae705c68be221f47b201724'
assert len(routes)==38 and not any('D138' in v['model'] for v in routes.values())

def norm(s):return re.sub('[^a-z0-9]','',unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower())
def horse(s):return norm(re.sub(r'\s*\([A-Za-z]{2,3}\)\s*$','',str(s or '')))
def course(s):
 n=norm(re.sub(r'\s*\((?:AW|GB|IRE|UK)\)\s*$','',str(s or ''),flags=re.I));return {'galwayire':'galway','wexfordire':'wexford','dundalkawire':'dundalk','dundalkire':'dundalk','dundalkaw':'dundalk','fairyhouseire':'fairyhouse','clonmelire':'clonmel','gowranparkire':'gowranpark','kemptonpark':'kempton','chelmsfordcity':'chelmsford','bangor':'bangorondee'}.get(n,n)
def stamp(s):
 t=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'));assert t.tzinfo;return t.astimezone(UTC)
def cardkey(r):return (DAY,course(r['course']),stamp(r['race_time']).strftime('%H:%M'))
def rowkey(r):return (str(r.get('date','')),course(r.get('course') or r.get('meeting')),str(r.get('time_utc') or r.get('off_utc') or r.get('time') or '')[:5])
def dump(p,x):p.write_text(json.dumps(x,indent=2,default=str))
def writecsv(p,rows,fields=None):
 fields=fields or list(dict.fromkeys(k for r in rows for k in r)) or ['date','course','time','status'];
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
def git(*args):return S.check_output(['git',*args],text=True)
def digest(path):
 with P.Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def scoring_history_digest(path):
 # SQLite page layout and provenance download timestamps can differ between
 # runners. Hash the complete rows read by scorers in primary-key order.
 checksum=hashlib.sha256(b'stage196f-scoring-history-v1\n')
 queries={
  'historical_races':'SELECT * FROM historical_races ORDER BY id',
  'historical_runs':'SELECT * FROM historical_runs ORDER BY race_id,runner_key',
 }
 with sqlite3.connect(P.Path(path).resolve().as_uri()+'?mode=ro',uri=True) as connection:
  for table,query in queries.items():
   rows=connection.execute(query)
   columns=[column[0] for column in rows.description]
   checksum.update(json.dumps([table,columns],separators=(',',':')).encode()+b'\n')
   for record in rows:
    checksum.update(json.dumps(record,separators=(',',':'),ensure_ascii=False).encode()+b'\n')
 return checksum.hexdigest()
def download(run,name,dest):
 dest.mkdir(parents=True,exist_ok=True)
 S.run(['gh','run','download',str(run),'--repo',os.environ['GITHUB_REPOSITORY'],'--name',name,'--dir',str(dest)],check=True,timeout=1200)
 if REPLAY:
  manifest=audit.setdefault('artifact_inputs',{}).setdefault(str(run)+'/'+name,{})
  for p in sorted(dest.rglob('*')):
   if p.is_file() and (p.suffix in ('.joblib','.sqlite','.pkl') or p.name=='all_races_with_reference_event_ids.csv'):
    manifest[str(p.relative_to(dest))]=digest(p)
def exactly(root,name):
 a=list(root.rglob(name));assert len(a)==1,(root,name,len(a));return a[0].resolve()
def extract(workflow,filename):
 # Extract the complete original Python heredoc verbatim from its immutable workflow revision.
 text=git('show',workflow_pin+':.github/workflows/'+workflow)
 pattern=r"(?m)^([ ]*)cat > scripts/"+re.escape(filename)+r" <<'PY'\n([\s\S]*?)^\1PY[ ]*$"
 m=re.search(pattern,text);assert m,('missing exact adapter',workflow,filename)
 indent=len(m.group(1));body='\n'.join(x[indent:] if x.startswith(' '*indent) else x for x in m.group(2).splitlines())+'\n';compile(body,filename,'exec');p=ROOT/'scripts'/filename;p.write_text(body);return p

E_DRIVER_MODULE = """\
import warnings

import numpy as np

PILLARS = (
    'ability pillar', 'trainer pillar', 'jockey pillar', 'course pillar',
    'draw pillar', 'distance pillar', 'ground pillar', 'jumping pillar',
)


def explain(scores, order, components, labels):
    matrix = np.asarray(components, dtype=float)
    centered = matrix - matrix.mean(axis=0)
    values = np.asarray(scores, dtype=float)
    if not np.allclose(centered.sum(axis=1), values - values.mean(), atol=1e-7):
        warnings.warn('E explanation omitted: components did not reproduce the frozen score')
        return dict.fromkeys(map(int, order))

    def groups(vector):
        pairs = sorted(zip(labels, map(float, vector)), key=lambda pair: -abs(pair[1]))
        return {
            'positive': [[label, amount] for label, amount in pairs if amount > 1e-5],
            'negative': [[label, amount] for label, amount in pairs if amount < -1e-5],
        }

    output = {}
    for place, runner in enumerate(order):
        rival = order[1] if place == 0 else order[place - 1]
        output[int(runner)] = {
            'method': 'exact_weighted_components',
            **groups(centered[runner]),
            'rank_comparison': groups(matrix[runner] - matrix[rival]),
        }
    return output
"""


def add_e_score_explanations(scripts):
    """Capture score components in the pinned E adapters without changing predictions."""
    helper = scripts / '_stage196f_e_drivers.py'
    helper.write_text(E_DRIVER_MODULE)

    def patch(filename, replacements):
        path = scripts / filename
        source = path.read_text()
        for original, replacement in replacements:
            if source.count(original) != 1:
                raise RuntimeError(f'Pinned E commentary hook changed in {filename}: {original}')
            source = source.replace(original, replacement)
        compile(source, str(path), 'exec')
        path.write_text(source)

    patch('stage95_five_model_paired_replay.py', [
        ('def e75_preoff(cards,original,learned,people,draw_hist,hr,date,cache):',
         'def e75_preoff(cards,original,learned,people,draw_hist,hr,date,cache,components=None):'),
        ('results_off=[];results_on=[];available=[];hprn=[]',
         'results_off=[];results_on=[];available=[];hprn=[]\n off_parts=[];on_parts=[]'),
        ('  results_off.append(off);results_on.append(on)\n c_z=', '''  results_off.append(off);results_on.append(on)
  if components is not None:
   off_parts.append([scores[k]*wc[k] for k in E92.PILLARS])
   on_parts.append([on_scores[k]*wc[k] for k in E92.PILLARS])
 c_z='''),
        (' return eoff,eon,available,hprn', ''' if components is not None:
  for label,parts,raw in (
   ('E_75_25_NO_HPR',off_parts,results_off),
   ('E_75_25_WITH_HPR',on_parts,results_on),
  ):
   matrix=np.asarray(parts)
   weighted=(matrix-matrix.mean(axis=0))*.75/(np.std(raw)+1e-8)
   components[label]=np.column_stack((weighted,.25*c_z))
 return eoff,eon,available,hprn'''),
        ('  eno,eon,hprflags,hprn=e75_preoff(cards,original,cprob,people,draw_hist,hr,day,cache)',
         '  components={}\n  eno,eon,hprflags,hprn=e75_preoff(cards,original,cprob,people,draw_hist,hr,day,cache,components)'),
        ("'scores_by_model':scores,'hpr_flags':hprflags,",
         "'scores_by_model':scores,'score_components':components,'hpr_flags':hprflags,"),
    ])
    patch('stage101_all_models_compare.py', [
        ("out[key][label]={'scores':values,'probabilities':None,'score_share':p,'version':S95.VERSION,",
         "out[key][label]={'scores':values,'probabilities':None,'score_share':p,'version':S95.VERSION,\n"
         "        'score_components':row.get('score_components',{}).get(label),"),
    ])
    patch('stage185a_local_replay.py', [
        ('import stage101_all_models_compare as M',
         'import stage101_all_models_compare as M\nfrom _stage196f_e_drivers import explain,PILLARS'),
        ("  for rank,i in enumerate(np.argsort(-v,kind='stable'),1):",
         "  order=np.argsort(-v,kind='stable')\n"
         "  labels=(*PILLARS,'learned form model')\n"
         "  if model=='E_75_25_WITH_HPR':labels=('historical performance pillar',*labels[1:])\n"
         "  drivers=explain(v,order,x['score_components'],labels)\n"
         "  for rank,i in enumerate(order,1):"),
        ("model=model,rank=rank,horse=r['horse']))",
         "model=model,rank=rank,horse=r['horse'],research_score=float(v[i]),driver_json=json.dumps(drivers[int(i)])))"),
    ])
    patch('stage185b_local_replay.py', [
        ('import stage92_model_e_winprob as E',
         'import stage92_model_e_winprob as E\nfrom _stage196f_e_drivers import explain,PILLARS'),
        ('   for rank,i in enumerate(order,1):rows.append(', '''   weights=E.weights(preset,disp)
   if typ=='NH Flat/Bumper':
    weights['jumping']=0.
    total=sum(weights.values())
    weights={key:value/total for key,value in weights.items()}
   parts=[[runner['pillars'][key]*weights[key] for key in E.PILLARS] for runner in rc['runners']]
   drivers=explain(scores,order,parts,PILLARS)
   for rank,i in enumerate(order,1):rows.append('''),
        ("rank=rank,horse=field[i]['horse']))",
         "rank=rank,horse=field[i]['horse'],research_score=float(scores[i]),driver_json=json.dumps(drivers[i])))"),
    ])


def run(name,script,args,filename,env=None,label=None):
 dest=W/name;dest.mkdir(exist_ok=True); record={'family':name,'script':str(script),'status':'FAILED','started':NOW().isoformat()}; result=[]
 try:
  command=[sys.executable,'-u',str(script),*map(str,args),'--out',str(dest)];record['command']=command
  if REPLAY:
   wrapper=("import os, runpy, sys; from freezegun import freeze_time\n"
            "path=sys.argv.pop(1); sys.argv[0]=path\n"
            "with freeze_time(os.environ['STAGE196F_REPLAY_CLOCK'], real_asyncio=True):\n"
            "    runpy.run_path(path, run_name='__main__')\n")
   command=[sys.executable,'-u','-c',wrapper,*command[2:]]
  child_env={**os.environ,**(env or {}),'STAGE196F_FAMILY':name,'STAGE196F_REPLAY_CLOCK':REPLAY_CLOCK}
  with (O/(name+'.log')).open('w') as f:
   ret=S.run(command,stdout=f,stderr=S.STDOUT,env=child_env,timeout=7200)
  if ret.returncode:raise RuntimeError('scorer exit '+str(ret.returncode))
  with (dest/filename).open(newline='',encoding='utf-8-sig') as f:result=list(csv.DictReader(f))
  if label:
   for r in result:r['model']=label
  if not result:raise RuntimeError('no scorer rows')
  if REPLAY and name=='G2_G3':
   keys=('date','course','off_utc','model','horse','prior_runs','trainer_n60','trainer_form60',
         'jockey_n60','jockey_form60','trainer_changed','raw_probability','win_probability','rank')
   writecsv(O/'g2-g3-feature-evidence.csv',result,keys)
  record.update(status='SUCCESS',rows=len(result),models=sorted({r['model'] for r in result}))
 except Exception as e:record['error']=repr(e)
 finally:
  record['finished']=NOW().isoformat();audit['families'].append(record);dump(O/'audit.json',audit)
  # Keep the rank output in memory and copy small provenance reports; release private multi-GB scratch files.
  for report in dest.glob('*audit.json'):
   if report.stat().st_size<2_000_000:shutil.copy2(report,O/(name+'-'+report.name))
  shutil.rmtree(dest,ignore_errors=True)
 return result

audit={'version':'STAGE196F_AUDITED_ROUTE_FIRST_CANDIDATE','day':DAY,'mode':'VALIDATION_CANDIDATE_ONLY','historical_period':['2026-10-25','2026-11-23'],'historical_replay_source':pin,'adapter_workflow_source':workflow_pin,'route_policy':'highest historical ROI among verified Stage196F models; no minimum priced sample; Model Z eligibility limited to Dundalk','excluded_replays':['stage184b incomplete','stage186d deferred','unverified A/B and D(c) proxies'],'families':[],'issues':[]}
if REPLAY:
 audit.update(mode='CONTROLLED_REPLAY_ONLY',replay_variant=REPLAY,decision_clock=REPLAY_CLOCK)
# These files contain no target-day results; use the existing validated source builder once.
base=W/'base'; refdir=W/'ref'; download(43089770857,'stage2-historical-staging-database',base);download(43089770857,'stage4-reference-named-events-and-winners',refdir)
# The helper recognises the canonical Stage152 source path. Use exactly that path in this disposable checkout.
canonical=ROOT/'reports/stage152-specialists/source/stage2';canonical.mkdir(parents=True,exist_ok=True)
basefile=exactly(base,'historical_ukie_staging.sqlite');target=canonical/basefile.name;shutil.move(str(basefile),target)
reference=exactly(refdir,'all_races_with_reference_event_ids.csv')
import stage167_validated_history as History
from scripts.stage203_identity_recovery import scan as scan_form, classify as classify_form
from scripts.stage203g_final_cork import OUTSIDE
from rhp_runner_eligibility import active_runner
# Copy the original G5 baseline before overlays; reconstruct Kaggle once only.
legacy_db=W/'g5-original.sqlite';legacy_ref=W/'g5-original-reference.csv'
original_build=History.build
def capture(*args,**kwargs):
 p,q=original_build(*args,**kwargs);shutil.copy2(p,legacy_db);shutil.copy2(q,legacy_ref);return p,q
History.build=capture
try:
 with History.refreshed_inputs(target,reference,DAY) as (db0,ref0):
  db=W/'prior.sqlite';ref=W/'prior-reference.csv';shutil.copy2(db0,db);shutil.copy2(ref0,ref)
finally:History.build=original_build
pre=json.loads((ROOT/'data/racecards.json').read_text());active=[r for r in pre['runners'] if r.get('date')==DAY and active_runner(r)]
assert active and len({r['horse_id'] for r in active})==len(active)
assert all(r.get('horse_id') and r.get('sire') and r.get('dam') and r.get('trainer') and r.get('jockey') for r in active)
assert all(stamp(r['race_time'])>NOW()+dt.timedelta(minutes=10) for r in active),'Not entirely pre-off'
assert 0<=(NOW()-stamp(pre['collected_at'])).total_seconds()<=4*3600
hits,others=scan_form(db,active,DAY);review=classify_form(active,hits,others,DAY)
gaps=[x for x in review if x['last_run_matched'] is False or x['known_no_history']]
unresolved=[x for x in gaps if OUTSIDE.get(x['horse_id'])!=x['expected']]
with sqlite3.connect(db) as con:
 assert con.execute('pragma quick_check').fetchone()[0]=='ok'
 assert con.execute('select max(race_date) from historical_races').fetchone()[0]<DAY
with sqlite3.connect(legacy_db) as con:
 assert con.execute('select max(race_date) from historical_races').fetchone()[0]<DAY
preaudit={'active':len(active),'races':len({r['race_id'] for r in active}),'raw_gaps':len(gaps),'external_classified':len(gaps)-len(unresolved),'unexplained':unresolved,'missing_or':sum(not r.get('ofr') or str(r['ofr']).strip() in ('-','0') for r in active),'missing_weight':sum(not r.get('lbs') for r in active),'G_policy':'original HPR adapters; G5 original Stage167 baseline plus HPR','scope':'input gate, not full production certification'}
dump(O/'preflight.json',preaudit);print('STAGE206_PREFLIGHT',json.dumps(preaudit),flush=True)
assert not unresolved,'Unexplained form gaps; stop before scoring'
assert not preaudit['missing_weight'],'Missing declared weights' 
# The refreshed private database is independent; discard the restored baseline copy to conserve runner disk.
target.unlink(missing_ok=True)
with sqlite3.connect(db) as c:
 c.execute('DELETE FROM historical_runs WHERE race_id IN (SELECT id FROM historical_races WHERE race_date>=?)',(DAY,));c.execute('DELETE FROM historical_races WHERE race_date>=?',(DAY,));c.commit();latest=c.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0];assert latest and latest<DAY
ref_tmp=W/'filtered-reference.csv'
with ref.open(encoding='utf-8-sig',newline='') as f,ref_tmp.open('w',newline='') as g:
 rr=csv.DictReader(f);assert 'date' in rr.fieldnames;ww=csv.DictWriter(g,fieldnames=rr.fieldnames);ww.writeheader();ww.writerows(r for r in rr if r['date']<DAY)
ref_tmp.replace(ref);audit['history_through']=latest
# Use the current HPR ledger, not a later result or a reconstructed target-day rating.
hpr=W/'hpr.sqlite';S.run([sys.executable,'scripts/stage87_hpr_storage.py','restore','--dir','data/hpr/shards','--db',str(hpr)],check=True,timeout=1200)
with sqlite3.connect(hpr) as hc:
 assert hc.execute('pragma quick_check').fetchone()[0]=='ok'
 assert not hc.execute('select 1 from hpr_ratings where race_date>=? limit 1',(DAY,)).fetchone(),'Target-date HPR present'
# Freeze model source and root imports to original replay commit, while retaining current validated data and Git history.
# Keep the current script files outside the source tree for provenance; replace tracked Python code only in this disposable job.
paths=git('ls-tree','-r','--name-only',pin).splitlines();frozen=[p for p in paths if p.endswith('.py') and (p.startswith('scripts/') or '/' not in p)]
S.run(['git','restore','--source',pin,'--worktree','--',*frozen],check=True)
# Exact Stage207 candidate-source.patch from Actions artifact 15102171725.
debutant_patch = (
 'diff --git a/scripts/stage107_model_g.py b/scripts/stage107_model_g.py\n'
 'index a75ac57e..19f46143 100644\n'
 '--- a/scripts/stage107_model_g.py\n'
 '+++ b/scripts/stage107_model_g.py\n'
 '@@ -536,6 +536,12 @@ def get_target_state(history_db,hpr_db,target,state_path=None):\n'
 "                   'saved_historical_state_used':bool(state_path)}\n"
 ' \n'
 ' \n'
 '+def debutant_eligible(entries):\n'
 "+    title=' '.join(str(entries[0].get(k) or '') for k in ('race_name','race_type','type')).lower()\n"
 "+    if re.search(r'\\b(?:handicap|nursery)\\b',title):return False\n"
 "+    if re.search(r'\\b(?:maiden|novices?|beginners?|juvenile|junior|bumper|academy|introductory|restricted|auction|sales|conditions)\\b|national hunt flat|nh flat',title):return True\n"
 "+    return any(not str(r.get('form') or '').strip('-/ ') and not num(r.get('last_run')) for r in entries)\n"
 '+\n'
 ' def score_card_rows(state,model,temperature,cards,cutoff):\n'
 '     from rhp_runner_eligibility import active_runner\n'
 '     grouped=defaultdict(list)\n'
 '@@ -562,7 +568,12 @@ def score_card_rows(state,model,temperature,cards,cutoff):\n'
 "                        'horse':r.get('horse'),'prior_runs':prior,'identity':identity(r)})\n"
 '         if len(hh)<4:continue\n'
 "         coverage=sum(bool(z['identity']) and z['prior_runs']>0 for z in hh)/len(hh)\n"
 "-        if coverage<.55:rejected.append({'race':k,'reason':f'PRIOR_FORM_COVERAGE_{coverage:.2f}'});continue\n"
 '+        if coverage<.55:\n'
 '+            if not debutant_eligible(entries):\n'
 "+                rejected.append({'race':k,'reason':f'PRIOR_FORM_COVERAGE_{coverage:.2f}'});continue\n"
 "+            if len(hh)!=len(entries) or not all(z['identity'] for z in hh) or len({z['identity'] for z in hh})!=len(hh) or not all(r.get('horse_id') and r.get('sire') and r.get('dam') for r in entries):\n"
 "+                rejected.append({'race':k,'reason':'DEBUTANT_EXCEPTION_IDENTITY_INCOMPLETE'});continue\n"
 "+            print('DEBUTANT_COVERAGE_EXCEPTION',k,coverage,flush=True)\n"
 '         tmp=pd.DataFrame(hh)\n'
 "         tmp['raw_probability']=model.predict_proba(Xframe(tmp))[:,1]\n"
 '         tmp=softmax_races(tmp,temperature)\n'
 'diff --git a/scripts/stage108_model_g2.py b/scripts/stage108_model_g2.py\n'
 'index ef1a25f8..c5c24d53 100644\n'
 '--- a/scripts/stage108_model_g2.py\n'
 '+++ b/scripts/stage108_model_g2.py\n'
 '@@ -682,6 +682,12 @@ def get_target_state(history_db,hpr_db,target,state_path=None):\n'
 "                   'saved_historical_state_used':bool(state_path)}\n"
 ' \n'
 ' \n'
 '+def debutant_eligible(entries):\n'
 "+    title=' '.join(str(entries[0].get(k) or '') for k in ('race_name','race_type','type')).lower()\n"
 "+    if re.search(r'\\b(?:handicap|nursery)\\b',title):return False\n"
 "+    if re.search(r'\\b(?:maiden|novices?|beginners?|juvenile|junior|bumper|academy|introductory|restricted|auction|sales|conditions)\\b|national hunt flat|nh flat',title):return True\n"
 "+    return any(not str(r.get('form') or '').strip('-/ ') and not num(r.get('last_run')) for r in entries)\n"
 '+\n'
 ' def score_card_rows(state,model,temperature,cards,cutoff):\n'
 '     from rhp_runner_eligibility import active_runner\n'
 '     grouped=defaultdict(list)\n'
 '@@ -715,7 +721,12 @@ def score_card_rows(state,model,temperature,cards,cutoff):\n'
 "                        'horse':r.get('horse'),'prior_runs':prior,'identity':identity(r)})\n"
 '         if len(hh)<4:continue\n'
 "         coverage=sum(bool(z['identity']) and z['prior_runs']>0 for z in hh)/len(hh)\n"
 "-        if coverage<.55:rejected.append({'race':k,'reason':f'PRIOR_FORM_COVERAGE_{coverage:.2f}'});continue\n"
 '+        if coverage<.55:\n'
 '+            if not debutant_eligible(entries):\n'
 "+                rejected.append({'race':k,'reason':f'PRIOR_FORM_COVERAGE_{coverage:.2f}'});continue\n"
 "+            if len(hh)!=len(entries) or not all(z['identity'] for z in hh) or len({z['identity'] for z in hh})!=len(hh) or not all(r.get('horse_id') and r.get('sire') and r.get('dam') for r in entries):\n"
 "+                rejected.append({'race':k,'reason':'DEBUTANT_EXCEPTION_IDENTITY_INCOMPLETE'});continue\n"
 "+            print('DEBUTANT_COVERAGE_EXCEPTION',k,coverage,flush=True)\n"
 '         tmp=pd.DataFrame(hh)\n'
 "         tmp['raw_probability']=model.predict_proba(Xframe(tmp))[:,1]\n"
 '         tmp=softmax_races(tmp,temperature)\n'
)
if REPLAY!='stage206':
 for action in (['--check'], []):
  S.run(['git','apply',*action,'-'],input=debutant_patch,text=True,check=True)
if REPAIR_IDENTITIES:
 # The original G history loaders run first. Reconcile their in-memory person
 # keys immediately before feature construction, leaving fitted artifacts alone.
 for module in ('stage107_model_g','stage108_model_g2'):
  scorer=ROOT/'scripts'/(module+'.py')
  code=scorer.read_text()
  anchor='def score_card_rows(state,model,temperature,cards,cutoff):\n'
  if code.count(anchor)!=1:raise RuntimeError('Unexpected frozen G scoring entry point: '+module)
  hook=('    from stage196f_identity import prepare_g_state\n'
        '    import sys\n'
        '    prepare_g_state(sys.modules[__name__],state,cards)\n')
  code=code.replace(anchor,anchor+hook)
  compile(code,str(scorer),'exec');scorer.write_text(code)
for p in ['data/model-z-strategies.json','reports/stage34/stage34-frozen-track-profiles.json','data/strategy-historical-model.json','data/strategy-model-b-historical.json','data/strategy-model-c-historical.json','data/roi-racetype-route-map.json','reports/stage30/stage30-summary.json']:
 if p in paths:S.run(['git','restore','--source',pin,'--worktree','--',p],check=True)
for workflow,filename in [('stage185a-e75-hpr-replay.yml','stage185a_local_replay.py'),('stage185b-e92-presets-replay.yml','stage185b_local_replay.py'),('stage186b-remaining-specialists-replay.yml','stage186b_local_replay.py'),('stage188a-enhanced-d2-replay.yml','stage188a_local_replay.py')]:extract(workflow,filename)
add_e_score_explanations(ROOT/'scripts')
# Preserve original G scoring; use the validated complete-field G5 small-card guard.
gp=ROOT/'scripts/stage128_g5_today_paper.py'
gs=gp.read_text();guard="    if len(data)<40:raise RuntimeError('Too few horses scored; refuse misleading partial card output')"
assert gs.count(guard)==1
replacement="    if data.empty:raise RuntimeError('No complete race scored')\n    for rid, group in data.groupby('race_id'):\n        expected=Counter(G.identity(r) for r in groups.get(str(rid),[]))\n        actual=Counter(str(x) for x in group['identity'])\n        if len(group)<4 or '' in expected or expected!=actual or any(n!=1 for n in expected.values()):\n            raise RuntimeError('Incomplete or duplicate scored field: '+str(rid))"
gs=gs.replace(guard,replacement);compile(gs,str(gp),'exec');gp.write_text(gs)
# Capture only genuine, fresh declarations for the target day. Remove settlement fields and current price inputs.
from rhp_runner_eligibility import active_runner
cards=json.loads((ROOT/'data/racecards.json').read_text());collected=stamp(cards['collected_at']);assert 0<= (NOW()-collected).total_seconds()<=4*3600,'Stale or future racecard snapshot'
fields=C.defaultdict(list);outcome={'position','finish_pos','result','outcome','sp','sp_decimal','starting_price','beaten','race_result','result_status','overall_beaten','distance_beaten_total'};prices={'odds','price','bookmaker_odds','current_odds','paddypower_decimal','paddy_power_decimal'}
for r in cards.get('runners',[]):
 if str(r.get('date'))!=DAY or not r.get('race_time') or not r.get('horse') or not active_runner(r):continue
 if norm(r.get('race_status')) in {'finished','resulted','final','completed','abandoned','cancelled','canceled','void'}:continue
 if any(r.get(k) not in (None,'',[],{}) for k in outcome):continue
 if stamp(r['race_time'])<=NOW()+dt.timedelta(minutes=2):continue
 fields[cardkey(r)].append({k:v for k,v in r.items() if k not in outcome|prices})
fields={k:v for k,v in fields.items() if len(v)>=2 and len({horse(r['horse']) for r in v})==len(v)};assert fields,'No verified remaining fields'
declared_fields=fields
if REPLAY:
 audit['frozen_inputs']={name:digest(path) for name,path in {
  'declarations':ROOT/'data/racecards.json','reference':ref,
  'original_g5_history':legacy_db,'original_g5_reference':legacy_ref,'hpr':hpr,
 }.items()}
 audit['frozen_inputs']['shared_history']=scoring_history_digest(db)
 audit['shared_history_checksum_basis']='historical_races and historical_runs, primary-key order'
 audit['shared_history_sqlite_file_sha256']=digest(db)
if REPLAY=='mullins-before':
 fields={k:[{**r,'trainer':MULLINS_NAMES[0]} if r.get('trainer_id')==MULLINS_ID else r
            for r in v] for k,v in fields.items()}
if REPAIR_IDENTITIES:
 registry=json.loads((ROOT/'data/form-person-aliases.json').read_text())
 original=[r for v in fields.values() for r in v]
 evidence=ROOT/'data/racecards-archive.json'
 archived=[]
 if evidence.exists():
  archive=json.loads(evidence.read_text())
  if archive.get('source')=='The Racing API':
   archived=[r for r in archive.get('runners',[]) if r.get('date') and r['date']<DAY]
 archived.extend(archive_people(ROOT/'data/racing-results',DAY))
 identity_plan=IdentityPlan.build(original,archived,registry)
 fields={k:[identity_plan.scoring_row(r) for r in v] for k,v in fields.items()}
 people_diff=[dict(course=r['course'],time=cardkey(r)[2],horse=r['horse'],
                   role=role,source_id=r.get(role+'_id'),declared=r.get(role),scoring=s.get(role))
              for k,entries in declared_fields.items() for r,s in zip(entries,fields[k])
              for role in ('trainer','jockey') if r.get(role)!=s.get(role)]
 writecsv(O/'identity-changes.csv',people_diff,
          ['course','time','horse','role','source_id','declared','scoring'])
 audit['identity']={'declaration_changes':len(people_diff),
                    'verified_ids':{role:len(mapping['ids']) for role,mapping in identity_plan.roles.items()},
                    'ambiguous_names':{role:mapping['conflicts'] for role,mapping in identity_plan.roles.items()},
                    'shared_history':identity_plan.apply_history(db),
                    'legacy_history':identity_plan.apply_history(legacy_db),
                    'hpr_payloads':identity_plan.apply_hpr(hpr,DAY)}
cardpath=W/'cards.json';dump(cardpath,dict(cards,runners=[r for v in fields.values() for r in v]));audit.update(card_asof=collected.isoformat(),remaining_fields=len(fields));dump(O/'audit.json',audit)
common=['today','--db',db,'--reference',ref,'--cards',cardpath,'--date',DAY]
allrows=[]
# Classification-only: use original Stage137 race catalogue plus Stage143's 59-type classifier.
# Stage141's synthetic race inserts have no official pattern overlay for the target day.
import stage137_frozen_d_race_type_walkforward as S137
import stage143_specialist_evidence_2026 as A143
import stage108_model_g2 as G108
from stage153_roi_combined import type_for,STAGE156_ROUTES
config=json.loads((ROOT/'scripts/stage143_race_type_specialist_config.json').read_text())
assert config['type_count']==59
legacy={'maiden hurdle':'HURDLE_MAIDEN','handicap hurdle':'HURDLE_HANDICAP','novice hurdle':'HURDLE_NOVICE','novice handicap hurdle':'HURDLE_NOVICE_HANDICAP','beginners chase':'CHASE_BEGINNERS','handicap chase':'CHASE_HANDICAP','novice handicap chase':'CHASE_NOVICE_HANDICAP','nh flat open':'NH_FLAT_OPEN','aw maiden':'AW_MAIDEN','aw nursery':'AW_NURSERY','aw handicap':'AW_HANDICAP','auction restricted':'AW_AUCTION_RESTRICTED_SALES'}
assigned={}; pending=[]; classaudit=[]
for k,v in sorted(fields.items()):
 first=v[0]; title=str(first.get('race_name') or '');typ=None;reason='';region=G108.paper_region(first)
 try:
  assert region in ('GB','IRELAND'),('unverified region',region)
  assert len({str(r.get('race_id')) for r in v})==1 and len({str(r.get('race_name')) for r in v})==1
  # The indices mirror the exact Stage137 read_races and Stage141 scratch_archive schema.
  e=[str(first['race_id']),DAY,str(first['course']),str(first.get('type') or ''),str(first.get('surface') or ''),first.get('distance_f'),first.get('going'),str(first.get('race_class') or '')]
  starters=[[None]*16+[r.get('ofr')] for r in v]
  info,broad,group,subtype=S137.race_catalogue(e,region,title,starters)
  meta=dict(race_id=e[0],date=DAY,course=e[2],race_name=title,kind=info['discipline'],broad=broad,subtype=subtype,official_tier=info['classification_code'])
  result,reason=A143.classified(meta,{},config)
  if 'junior' in title.lower() and 'hurdle' in title.lower():typ='HURDLE_JUVENILE';reason=''
  elif result and result['route'] in STAGE156_ROUTES:typ=result['route'];reason=''
  else:typ=legacy.get(type_for(v));reason='' if typ else (reason or 'NO_VERIFIED_CLASSIFICATION')
 except Exception as ex:reason=repr(ex)
 assigned[k]=typ;classaudit.append(dict(course=first['course'],time=k[2],race_type=typ,reason=reason,model=routes.get(typ,{}).get('model','')))
 if typ not in routes:pending.append(dict(date=DAY,course=first['course'],time=k[2],race_type=typ or '',status=reason or 'NO_HISTORICAL_ROUTE'))
race_routes={k:(dundalk_maiden_fallback if t=='AW_MAIDEN' and k[1]!='dundalk' else routes[t])
             for k,t in assigned.items() if t in routes}
for item in classaudit:
 k=(DAY,course(item['course']),item['time'])
 if k in race_routes:item['model']=race_routes[k]['model']
needed={r['model'] for r in race_routes.values()};audit['needed_models']=sorted(needed)
if REPLAY:
 if len(race_routes)!=35:
  raise RuntimeError('Controlled replay requires all 35 original race routes')
 expected=json.loads((ROOT/'stage196-reference-routes.json').read_text())
 expected={(entry['course'],entry['time']):(entry['race_type'],entry['model']) for entry in expected}
 actual={(entry['course'],entry['time']):(entry['race_type'],entry['model']) for entry in classaudit}
 if actual!=expected:raise RuntimeError('Stage206 and Stage196F routes do not agree on the archived card')
if REPAIR_IDENTITIES:
 overlay={}
 if USE_STAGE208:
  eligible=[r for k,v in fields.items() if assigned[k]=='HURDLE_MAIDEN'
            and race_routes[k]['model']=='G2_FROZEN' for r in v]
  overlay,quarantine=stage208_events(G108,db,ref,DAY,eligible)
  dump(O/'stage208-quarantine.json',quarantine)
  audit['stage208_eligible_runners']=len(eligible)
 scoring_plan=W/'scoring-plan.json'
 dump(scoring_plan,{'people':identity_plan.roles,'stage208':overlay})
 os.environ['STAGE196F_SCORING_PLAN']=str(scoring_plan)
 os.environ['STAGE196F_OVERLAY_AUDIT']=str(O/'stage208-added.json')
dump(O/'classification.json',classaudit);dump(O/'audit.json',audit)
print('ROUTED_FIELDS',json.dumps(classaudit),flush=True)
def family_for(model):
 if model.startswith('E92_'):return 'E92'
 if model.startswith('E_75_25'):return 'E75'
 if model.startswith('D2_ENHANCED'):return 'D2'
 if model in ('D137_HPR_ABILITY_GRADE','D137_HPR_ABILITY_OR','D_FORM_DEBUT_EIGHT_PILLARS__SR','D_FORM_WEIGHT_OR_GRADE__ROI'):return 'D_specialists'
 if model=='D137_HPR_ABILITY_OR_WEIGHT':return 'D137_OR_WEIGHT'
 if model=='D142_LONG_FORM_HPR':return 'D142_LONG_FORM'
 if model.startswith('G5_'):return 'G5'
 if model in ('G2_FROZEN','G3_LEARNED'):return 'G2_G3'
 if model=='G':return 'G'
 if model in ('G4_BLENDED','G4_SPECIALIST'):return 'G4'
 if model=='Z':return 'Z'
 if model=='STAGE143_TYPE_EVIDENCE':return 'classifier'
 if model in ('D_FROZEN_RANK1','C_HISTORICAL_CORE'):return model
 raise ValueError('Unknown family '+model)
family_fields=C.defaultdict(list)
for k,r in race_routes.items():
 family_fields[family_for(r['model'])].append(k)
family_paths={}
for family,keys in family_fields.items():
 p=W/('cards-'+family+'.json');dump(p,dict(cards,runners=[r for k in keys for r in fields[k]]));family_paths[family]=p
assert sum(map(len,family_fields.values()))==sum(t in routes for t in assigned.values())
assert sum(map(len,fields.values()))==preaudit['active'] and len(fields)==preaudit['races'],'Preflight/score field mismatch'
assert all(t in routes for t in assigned.values()),'Unmapped race type; stop before scoring' 
audit['family_input_fields']={f:[list(k) for k in keys] for f,keys in family_fields.items()};dump(O/'audit.json',audit)
# Replace the common full-day input with the allocated family-specific racecards.
original_run=run
def run(name,script,args,filename,env=None,label=None):
 assert name in family_paths,(name,sorted(family_paths));args=list(args);ix=args.index('--cards')+1;args[ix]=family_paths[name]
 print('FAMILY_START',name,len(family_fields[name]),flush=True)
 rows=original_run(name,script,args,filename,env,label)
 rows=[r for r in rows if rowkey(r) in family_fields[name] and r['model']==race_routes[rowkey(r)]['model']]
 print('FAMILY_DONE',name,len(rows),flush=True)
 return rows
empty=W/'empty-odds.json';empty.write_text('{}')
def family_E92():
 global allrows
 if any(m.startswith('E92_') for m in needed):allrows+=run('E92','scripts/stage185b_local_replay.py',[*common,'--hpr',hpr],'stage185b-all-runners.csv')
def family_E75():
 global allrows
 if any(m.startswith('E_75_25') for m in needed):allrows+=run('E75','scripts/stage185a_local_replay.py',[*common,'--hpr',hpr],'stage185a-all-runners.csv')
def family_D2():
 global allrows
 if any(m.startswith('D2_ENHANCED') for m in needed):
  try:
   download(42107290218,'stage50-model-d2-optimisation',ROOT/'restore50');download(42111552889,'stage51-model-d2-form-enhancements',ROOT/'restore51');allrows+=run('D2','scripts/stage188a_local_replay.py',common,'stage188a-all-runners.csv')
  except Exception as e:audit['issues'].append({'family':'D2','error':repr(e)})
def family_D_specialists():
 global allrows
 if any(m in needed for m in ['D137_HPR_ABILITY_GRADE','D137_HPR_ABILITY_OR','D_FORM_DEBUT_EIGHT_PILLARS__SR','D_FORM_WEIGHT_OR_GRADE__ROI']):allrows+=run('D_specialists','scripts/stage186b_local_replay.py',common,'stage186b-all-runners.csv')
def family_D137_OR_WEIGHT():
 global allrows
 if 'D137_HPR_ABILITY_OR_WEIGHT' in needed:allrows+=run('D137_OR_WEIGHT','scripts/stage141_today_or_weight_paper.py',common[1:],'stage141-today-all-runners.csv')
def family_D142_LONG_FORM():
 global allrows
 if 'D142_LONG_FORM_HPR' in needed:allrows+=run('D142_LONG_FORM','scripts/stage150_d142_long_form_today.py',common,'stage150-d142-all-runners.csv')
def family_G5():
 global allrows
 if any(m.startswith('G5_') for m in needed):
  try:
   g3=W/'g3';g5=W/'g5';download(43407036838,'stage109-g3-learning-43407036838',g3);download(43539599590,'stage127-g5-quarterly-43539599590',g5)
   allrows+=run('G5','scripts/stage128_g5_today_paper.py',['paper','--date',DAY,'--cutoff',NOW().isoformat(),'--state',exactly(g3,'stage109-prior-form-state.joblib'),'--g3',exactly(g3,'stage109-future-form-model.joblib'),'--models',g5,'--db',legacy_db,'--reference',legacy_ref,'--hpr',hpr,'--cards',cardpath],'stage128-g5-all-horses.csv')
  except Exception as e:audit['issues'].append({'family':'G5','error':repr(e)})
def family_G2_G3():
 global allrows
 if needed&{'G2_FROZEN','G3_LEARNED'}:
  try:
   g3=W/'g3';
   if not g3.exists():download(43407036838,'stage109-g3-learning-43407036838',g3)
   allrows+=run('G2_G3','scripts/stage109_model_g3.py',['paper','--model',exactly(g3,'stage109-future-form-model.joblib'),'--state',exactly(g3,'stage109-prior-form-state.joblib'),'--g2-frozen',exactly(g3,'stage109-frozen-g2-original.joblib'),'--cards',cardpath,'--date',DAY,'--cutoff',NOW().isoformat(),'--hpr',hpr,'--odds',empty,'--bookmaker','williamhill'],'stage109-paper-all-horses.csv')
  except Exception as e:audit['issues'].append({'family':'G2_G3','error':repr(e)})
def family_G():
 global allrows
 if 'G' in needed:
  try:
   g=W/'g';download(43292937252,'stage107-model-g-43292937252',g)
   allrows+=run('G','scripts/stage107_model_g.py',['paper','--model',exactly(g,'stage107-frozen-form-model.joblib'),'--state',exactly(g,'stage107-prior-form-state.joblib'),'--cards',cardpath,'--date',DAY,'--cutoff',NOW().isoformat(),'--hpr',hpr,'--odds',empty,'--bookmaker','williamhill'],'stage107-paper-all-rankings.csv',label='G')
  except Exception as e:audit['issues'].append({'family':'G','error':repr(e)})
def family_G4():
 global allrows
 if needed&{'G4_BLENDED','G4_SPECIALIST'}:
  try:
   g=W/'g4';download(43422655754,'stage111-g4-specialists-43422655754',g)
   allrows+=run('G4','scripts/stage111_model_g4.py',['paper','--model',exactly(g,'stage111-paper-specialists.joblib'),'--state',exactly(g,'stage111-prior-form-state.joblib'),'--cards',cardpath,'--date',DAY,'--cutoff',NOW().isoformat(),'--hpr',hpr],'stage111-paper-all-horses.csv')
  except Exception as e:audit['issues'].append({'family':'G4','error':repr(e)})
def family_D_FROZEN_RANK1():
 global allrows
 if 'D_FROZEN_RANK1' in needed:
  try:
   import numpy as np
   import stage38_model_dz_live as D
   from stage116_verified_form_live import resolve_verified,apply_d_row
   cfg=json.loads((ROOT/'reports/stage30/stage30-summary.json').read_text());d=cfg['model_results']['D'];assert cfg['test_year']==2025 and cfg['status']=='RESEARCH_ONLY' and d['chosen_combination']=='D-064-gap14'
   w=np.array([float(d['weights_pct'][k])/100 for k in D.METRICS]);assert len(w)==8 and np.isfinite(w).all() and abs(w.sum()-1)<.002
   history=D.LivePast({});history.scan_until(db,DAY);countries={}
   for n,c in history.last:
    if n not in countries:countries[n]=c
    elif countries[n]!=c:countries[n]=None
   aliases,_=resolve_verified(str(db),[r for v in fields.values() for r in v],DAY)
   count=0
   for k,v in fields.items():
    if race_routes.get(k,{}).get('model')!='D_FROZEN_RANK1':continue
    inputs=[apply_d_row(D.live_row(r,DAY,'MODEL-D-LIVE|'+DAY+'|'+str(v[0]['course'])+'|'+D.clock(v[0]['race_time']),countries),r,aliases) for r in v];race=history.score(inputs,live=True)
    if race is None or len(race['x'])!=len(v):continue
    scores,order,gap=D.prediction_rows(race,w);assert np.isfinite(scores).all()
    for rank,i in enumerate(order,1):allrows.append(dict(date=DAY,course=v[0]['course'],time=k[2],model='D_FROZEN_RANK1',horse=v[int(i)]['horse'],rank=rank));count+=1
   audit['families'].append(dict(family='D_FROZEN_RANK1',status='SUCCESS',rows=count))
  except Exception as e:audit['issues'].append({'family':'D_FROZEN_RANK1','error':repr(e)})
def family_C_HISTORICAL_CORE():
 global allrows
 if 'C_HISTORICAL_CORE' in needed:
  try:
   import numpy as np
   # The pinned installer reconstructs only the original historical scoring modules.
   installer='.github/workflows/stage21-model-c-live.yml'; S.run(['git','restore','--source',pin,'--worktree','--',installer],check=True)
   S.run([sys.executable,'scripts/stage101_all_models_compare.py','install-engines'],check=True,timeout=300)
   import stage101_all_models_compare as M
   entries=[dict(date=DAY,course=v[0]['course'],time=k[2],key=(DAY,M.norm(v[0]['course']),M.clock(v[0]['race_time'])),cards=v) for k,v in fields.items() if race_routes.get(k,{}).get('model')=='C_HISTORICAL_CORE']
   issues=[]; scored=M.card_models_original(db,ref,entries,issues);count=0
   for entry in entries:
    model=scored.get(entry['key'],{}).get(M.REPLAY_C)
    if not model:continue
    scores=np.asarray(model['scores'],dtype=float);assert len(scores)==len(entry['cards']) and np.isfinite(scores).all()
    for rank,i in enumerate(np.argsort(-scores,kind='stable'),1):allrows.append(dict(date=DAY,course=entry['course'],time=entry['time'],model='C_HISTORICAL_CORE',horse=entry['cards'][int(i)]['horse'],rank=rank));count+=1
   audit['families'].append(dict(family='C_HISTORICAL_CORE',status='RESEARCH_CORE_ONLY',rows=count,issues=issues))
  except Exception as e:audit['issues'].append({'family':'C_HISTORICAL_CORE','error':repr(e)})
def family_Z():
 import numpy as np
 from stage38_model_dz_live import LivePast,live_row,prediction_rows,METRICS,OLD
 from stage116_verified_form_live import resolve_verified,apply_d_row
 registry=json.loads((ROOT/'data/model-z-strategies.json').read_text())
 profile=json.loads((ROOT/'reports/stage34/stage34-frozen-track-profiles.json').read_text())['profiles']['courses']['aw|dundalk']
 item=next(s for s in registry['strategies'] if s['frozen_profile_key']=='aw|dundalk')
 assert all(abs(float(profile['weights_pct'][k])-float(item['weights_pct'][k]))<1e-6 for k in METRICS)
 assert abs(float(profile['selection_margin'])-float(item['selection_margin']))<1e-9
 weights=[float(profile['weights_pct'][k])/100 for k in METRICS]
 history=LivePast({});info=history.scan_until(db,DAY);assert info['last_result_date']<DAY
 countries={}
 for n,c in history.last:
  if n not in countries:countries[n]=c
  elif countries[n]!=c:countries[n]=None
 allocated=[fields[k] for k in family_fields['Z']]
 aliases,_=resolve_verified(str(db),[r for group in allocated for r in group],DAY)
 for k,entries in zip(family_fields['Z'],allocated):
  rid='MODEL-D-LIVE|'+DAY+'|'+entries[0]['course']+'|'+k[2]
  rows=[apply_d_row(live_row(r,DAY,rid,countries),r,aliases) for r in entries]
  assert all(row[12] is None and row[20] is None for row in rows)
  race=history.score(rows,live=True)
  if race is None or race['kind']!='aw' or OLD.track(entries[0]['course'])!='dundalk' or len(race['x'])!=len(entries):continue
  scores,order,gap=prediction_rows(race,weights);assert np.isfinite(scores).all()
  for rank,i in enumerate(order,1):
   allrows.append(dict(date=DAY,course=entries[0]['course'],time=k[2],model='Z',horse=entries[int(i)]['horse'],rank=rank,
                       research_score=float(scores[int(i)]),gate_qualified=bool(gap>=float(profile['selection_margin']))))
def family_classifier():
 global allrows
 allrows+=run('classifier','scripts/stage147_daily_143_146.py',[*common,'--config',ROOT/'scripts/stage143_race_type_specialist_config.json'],'stage146-today-all-runners.csv')
tasks={'Z':family_Z,'E92':family_E92,'E75':family_E75,'D2':family_D2,'D_specialists':family_D_specialists,'D137_OR_WEIGHT':family_D137_OR_WEIGHT,'D142_LONG_FORM':family_D142_LONG_FORM,'G5':family_G5,'G2_G3':family_G2_G3,'G':family_G,'G4':family_G4,'D_FROZEN_RANK1':family_D_FROZEN_RANK1,'C_HISTORICAL_CORE':family_C_HISTORICAL_CORE,'classifier':family_classifier}
verified_cache={}
def checkpoint():
 # Reconcile exact model IDs and full fields. Do not rerank after non-runners; any disagreement is an explicit no-selection.
 rankings=C.defaultdict(list)
 for r in allrows:
  k=rowkey(r);m=r.get('model')
  if k in fields and m in needed:rankings[(k,m)].append(r)
 results=[];full=[]
 for k,v in sorted(fields.items()):
  typ=assigned[k];base=dict(date=DAY,course=v[0]['course'],time=k[2],race_name=v[0].get('race_name',''),race_type=typ or '',discipline=('All Weather' if (typ or '').startswith('AW_') else 'Flat Turf' if (typ or '').startswith('TURF_') else 'National Hunt' if (typ or '').startswith(('HURDLE_','CHASE_','NH_FLAT_')) else ''),model='',rank1='',rank2='',historical_roi_pct='',historical_priced='',historical_strike_rate_pct='',sample_status='',status='',verified_at=NOW().isoformat())
  if typ not in routes:base['status']='NO_HISTORICAL_ROUTE' if typ else 'NO_VERIFIED_CLASSIFICATION';results.append(base);continue
  route=race_routes[k];m=route['model'];base.update(model=m,model_basis='HISTORICAL_CORE_NO_LIVE_TIPS' if m=='C_HISTORICAL_CORE' else 'ORIGINAL_REPLAY_RECIPE',historical_roi_pct=route['roi_pct'],historical_priced=route['priced'],historical_strike_rate_pct=route['strike_rate_pct'],sample_status='SPARSE' if route['priced']<10 else 'MIN10')
  raw=rankings[(k,m)]; expected={horse(r['horse']) for r in v};reason=''
  if k in verified_cache:
   previous,previous_full=verified_cache[k];results.append(previous);full.extend(previous_full);continue
  if stamp(v[0]['race_time'])<=NOW()+dt.timedelta(minutes=2):reason='RACE_OFF_OR_IMMINENT_AT_PUBLICATION'
  elif not raw:reason='NO_EXACT_MODEL_OUTPUT'
  else:
   try:
    ordered=sorted(raw,key=lambda r:int(float(r['rank'])));assert [int(float(r['rank'])) for r in ordered]==list(range(1,len(expected)+1));assert len(ordered)==len(expected) and {horse(r['horse']) for r in ordered}==expected
    base.update(rank1=ordered[0]['horse'],rank2=ordered[1]['horse'],status='PAPER_SELECTION')
    # Do not relabel raw scores as probabilities. Only preserve explicitly named source probability fields.
    if m.startswith('G5_') or m in ('G','G2_FROZEN','G3_LEARNED','G4_BLENDED','G4_SPECIALIST'):
     base['source_win_probability']=ordered[0].get('win_probability','');base['rank2_source_win_probability']=ordered[1].get('win_probability','')
    racefull=[dict(date=DAY,course=v[0]['course'],time=k[2],race_type=typ,model=m,rank=r['rank'],horse=r['horse'],raw_source_probability=r.get('win_probability','') if m.startswith('G5_') or m in ('G','G2_FROZEN','G3_LEARNED','G4_BLENDED','G4_SPECIALIST') else '',research_score=r.get('research_score'),driver_json=r.get('driver_json'),gate_qualified=r.get('gate_qualified')) for r in ordered];full.extend(racefull);verified_cache[k]=(dict(base),racefull)
   except (AssertionError,KeyError,ValueError,TypeError):reason='FIELD_OR_RANK_MISMATCH'
  if reason:base['status']=reason
  results.append(base)
 writecsv(O/'interim-combined.csv',results);writecsv(O/'all-runners.csv',full);dump(O/'routes.json',routes)
 audit['coverage']=dict(C.Counter(r['status'] for r in results));audit['selected']=sum(r['status']=='PAPER_SELECTION' for r in results);dump(O/'audit.json',audit)
 print('STAGE196_COVERAGE',json.dumps(audit['coverage']),flush=True)
 return results
for family in sorted(family_fields,key=lambda f:min(k[2] for k in family_fields[f])):
 try:tasks[family]()
 except Exception as ex:audit['issues'].append(dict(family=family,error=repr(ex)))
 results=checkpoint()
 print('CHECKPOINT_SELECTIONS',json.dumps([r for r in results if r['status']=='PAPER_SELECTION']),flush=True)
results=checkpoint()
by_race={(course(r['course']),r['time']):r for r in results}
feed=[]
for k,entries in sorted(fields.items()):
 row=by_race[(k[1],k[2])]; model=row['model']; selected=row['status']=='PAPER_SELECTION'
 off=stamp(entries[0]['race_time']); h=[]; selection=None
 if selected:
  assert stamp(row['verified_at'])<off
  ranked=verified_cache[k][1]
  cards_by_horse={horse(r['horse']):r for r in entries}
  for result in ranked:
   card=cards_by_horse[horse(result['horse'])]
   item=dict(horse=card['horse'],rank=int(float(result['rank'])),weight=card.get('lbs'),official_rating=card.get('ofr'))
   if result['research_score'] not in (None,''):item['research_score']=float(result['research_score'])
   if result['driver_json']:
    item['model_drivers']=json.loads(result['driver_json'])
   if result['raw_source_probability']:
    probability=float(result['raw_source_probability'])
    assert 0<=probability<=1
    item['win_probability']=probability
   h.append(item)
  probabilities=[v['win_probability'] for v in h if 'win_probability' in v]
  if probabilities:
   assert len(probabilities)==len(h) and abs(sum(probabilities)-1)<.001,'Incomplete model probability distribution'
  selection=dict(horse=h[0]['horse'],kind='VALIDATION_ONLY',published_at=row['verified_at'],pre_off_valid=True)
  if model=='Z' and not ranked[0]['gate_qualified']:selection=None
 feed.append(dict(date=DAY,time=k[2],course=entries[0]['course'],race_time=off.isoformat(),
                  race_name=row['race_name'],race_type=row['race_type'],source_model=model,
                  runners=h,field_size=len(entries),selection=selection,
                  selection_status='RESEARCH_ONLY' if selection else 'ASSESSMENT_PENDING',
                  reason=('Model Z did not pass its frozen margin' if selected and not selection else row['status']),
                  historical_simulation=dict(start='2026-10-25',end='2026-11-23',rank=1,
                    roi_pct=row['historical_roi_pct'],strike_rate_pct=row['historical_strike_rate_pct'],
                    priced_races=row['historical_priced'])))
dump(O/('replay-candidate.json' if REPLAY else 'combined-candidate.json'),
     dict(version=1,model='Controlled replay' if REPLAY else 'Stage196F combined candidate',
          day=DAY,generated_at=NOW().isoformat(),races=feed))
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
 f.write('# Stage196F route-first paper engine\n\nHistorical leaders, no production write. Verification timestamps are internal checkpoints; artifact becomes accessible only after upload.\n\n|Time UTC|Meeting|Race type|Model|Rank 1|Rank 2|Status|\n|---|---|---|---|---|---|---|\n')
 for r in results:f.write('| '+' | '.join(str(r[k]).replace('|','/') for k in ('time','course','race_type','model','rank1','rank2','status'))+' |\n')
assert audit['selected']>0,'No verified picks; inspect audit'
if REPLAY:
 if audit['issues'] or any(row['status']!='SUCCESS' for row in audit['families']):
  raise SystemExit('Controlled replay scorer error; inspect audit')
elif audit['selected']!=len(results):
 raise SystemExit('PARTIAL COVERAGE: valid selections preserved; inspect missing races')

