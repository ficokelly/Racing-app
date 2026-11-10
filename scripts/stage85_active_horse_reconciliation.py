#!/usr/bin/env python3
"""Stage85: evidence-first triage of Stage84 horse-history reconciliation.

RESEARCH ONLY. This does not modify databases, historical form, model weights,
selection files, results or the app. It does not infer true debuts, lifetime
coverage, or recover any record from a name-only match.

Produces prioritized investigations and optional independent evidence references
from existing archived final race results. Evidence references do NOT by
themselves certify horse identity; only stable ID or concordant sire+dam can.
"""
from __future__ import annotations
import argparse,csv,datetime as dt,json,pathlib,re,unicodedata,collections,tempfile

PROBLEMS={
 'LAST_RUN_MISMATCH_POSSIBLE_GAP':('P1','RECONCILE_EXPECTED_LAST_RUN'),
 'EXPECTED_PRIOR_RUNS_NOT_CONFIRMED':('P1','VERIFY_IDENTITY_AND_RECOVER_HISTORY'),
 'POSSIBLE_MISSING_RECENT_RUNS':('P2','RECOVER_MISSING_PREVIOUS_RUNS'),
 'MATCHED_HISTORY_REQUIRES_RECENCY_CHECK':('P3','VERIFY_FORM_NOTATION_AND_RECENCY'),
 'MATCHED_HISTORY_CARD_FORM_NOT_AVAILABLE':('P3','VERIFY_RACECARD_FORM_COVERAGE'),
}
DEBUT='NO_PRIOR_RUNS_REPORTED_UNVERIFIED'
SUPPORTED='RECENT_HISTORY_SUPPORTED_NOT_FULL_CAREER_PROOF'


def name(v):
 s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().upper()
 s=re.sub(r'\s*\((?:GB|IRE|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$','',s)
 return re.sub(r'[^A-Z0-9]','',s)

def date(v):
 try:return dt.date.fromisoformat(str(v or '')[:10])
 except ValueError:return None

def read_json(path,default):
 try:return json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
 except (OSError,ValueError):return default

def number(v):
 try:return float(v)
 except (TypeError,ValueError):return None

def read_csv(path):
 with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def write_csv(path,rows,headers):
 with open(path,'w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore');w.writeheader();w.writerows(rows)

def source_index(recent_dir,verified_results):
 """All source examples must be final, dated and before target declaration."""
 acc=collections.defaultdict(list);seen=set()
 paths=sorted(pathlib.Path(recent_dir).glob('*.json')) if recent_dir else []
 if verified_results:paths.append(pathlib.Path(verified_results))
 for p in paths:
  item=read_json(p,{})
  if not isinstance(item,dict):continue
  for race in item.get('results') or item.get('races') or []:
   if not isinstance(race,dict) or str(race.get('status') or '').lower()!='final':continue
   day=date(race.get('date') or race.get('off_dt'))
   if not day:continue
   for horse in race.get('runners',[]):
    if not isinstance(horse,dict) or not horse.get('horse'):continue
    if str(horse.get('status') or '').lower() in ('nr','non-runner','nonrunner','scratched','withdrawn','dns'):continue
    key=(str(day),name(horse['horse']),name(race.get('course')))
    if key in seen:continue
    seen.add(key)
    acc[name(horse['horse'])].append({'day':day,'course':str(race.get('course') or ''),
      'horse_id':str(horse.get('horse_id') or ''),'sire':name(horse.get('sire')),
      'dam':name(horse.get('dam')),'position':horse.get('position'),
      'source':p.name})
 return acc

def run(horses,summary,archive=None):
 evidence=archive or {}
 findings=[];debuts=[];supported=[];counts=collections.Counter();evidence_counts=collections.Counter()
 for h in horses:
  status=str(h.get('coverage_status') or '')
  if status==DEBUT:
   debuts.append(h);continue
  if status==SUPPORTED:
   supported.append(h);continue
  if status not in PROBLEMS:raise RuntimeError('Unrecognised Stage84 status: '+status)
  priority,action=PROBLEMS[status]
  day=date(h.get('latest_declared_date'))
  if not day:raise RuntimeError('No target race date for '+str(h.get('horse')))
  last=number(h.get('last_run_field'))
  target=day-dt.timedelta(days=round(last)) if last is not None and 0<=last<5000 else None
  prior=[z for z in evidence.get(name(h.get('horse')),[]) if z['day']<day]
  same_date=[z for z in prior if target and abs((z['day']-target).days)<=3]
  # Source date and name can suggest a record for review but never promote identity.
  evidence_type='SOURCE_DAY_NAME_CANDIDATE_REVIEW' if same_date else 'NO_SOURCE_DAY_CANDIDATE'
  valid_card_id=str(h.get('horse_id') or '')
  id_confirmed=any(z.get('horse_id') and z['horse_id']==valid_card_id for z in same_date)
  if id_confirmed:evidence_type='SOURCE_DAY_EXACT_ID_CANDIDATE'
  counts[status]+=1;evidence_counts[evidence_type]+=1
  findings.append({'priority':priority,'action':action,'horse':h.get('horse'),
    'horse_id':h.get('horse_id'),'declared_date':str(day),'course':h.get('course'),
    'race_type':h.get('type'),'race_category':h.get('category'),
    'stage84_status':status,'identity_status':h.get('identity'),
    'confirmed_matched_runs':h.get('matched_prior_runs'),
    'unconfirmed_name_only_runs':h.get('possible_name_only_runs'),
    'form':h.get('form'),'form_min_prior_starts':h.get('form_min_prior_starts'),
    'days_since_last_run':h.get('last_run_field'),
    'expected_last_run_approx':str(target) if target else '',
    'last_confirmed_matched_date':h.get('last_matched_date'),
    'preexisting_source_review_evidence':evidence_type,
    'candidate_courses':';'.join(sorted(set(z['course'] for z in same_date))) if same_date else '',
    'candidate_source_files':';'.join(sorted(set(z['source'] for z in same_date))) if same_date else '',
    'verified_recovery':'NO','next_step':'Independent identity/date/field validation required',
  })
 findings.sort(key=lambda x:(x['priority'],x['declared_date'],x['course'],x['horse']))
 prior_evidence=int(summary.get('horses_with_prior_run_evidence',0))
 total=int(summary.get('active_horses_in_cohort',0))
 # In Stage84, an additional horse may have a strong historical match even though
 # the card's form and last-run fields are blank. Avoid hiding it.
 expected=total-len(debuts)
 covered=len(supported)
 deficit=max(0,__import__('math').ceil(.9*expected)-covered)
 result={'stage':85,'model':'E_RESEARCH_ONLY','source_stage84_as_of':summary.get('as_of'),
   'horse_count':total,'applicable_previous_history_cohort':expected,
   'current_recent_history_supported':covered,
   'current_recent_support_pct':round(100*covered/expected,2) if expected else None,
   'remaining_horses_to_reconcile_for_90pct':deficit,
   'investigate_count':len(findings),'candidate_not_confirmed_debuts':len(debuts),
   'status_breakdown':dict(counts),'source_day_candidate_breakdown':dict(evidence_counts),
   'strong_matching_not_lifetime_proof':True,'lifetime_90pct_verified':False,
   'method':'Stage84 status triage + independent dated final-result source references; NO merges',
   'notes':['This is a research investigation queue, not a confirmed missing-run count.',
    'A first-time handicap is never treated as a horse debut.',
    'Previous runs in every discipline and race type remain applicable.',
    'Horse names and similar dates alone are never sufficient for identity confirmation.',
    'No current bookmaker odds or past SP enters Model E rankings.',
    'No changes made to D, D(c), data, historical SQLite or the live app.']}
 return result,findings,debuts,supported

def self_test():
 base={'active_horses_in_cohort':3,'as_of':'2026-11-10','horses_with_prior_run_evidence':2}
 rows=[{'horse':'Alpha','horse_id':'h1','coverage_status':'LAST_RUN_MISMATCH_POSSIBLE_GAP',
  'latest_declared_date':'2026-11-10','last_run_field':'10','course':'A','identity':'EXACT_HORSE_ID'},
 {'horse':'Beta','horse_id':'h2','coverage_status':DEBUT,'latest_declared_date':'2026-11-10'},
 {'horse':'Gamma','horse_id':'h3','coverage_status':SUPPORTED,'latest_declared_date':'2026-11-10'}]
 ev={'ALPHA':[{'day':dt.date(2026,10,31),'course':'Ascot','source':'test.json','horse_id':'h1'}]}
 s,q,d,g=run(rows,base,ev)
 assert s['investigate_count']==1 and len(d)==1 and len(g)==1
 assert s['applicable_previous_history_cohort']==2
 assert q[0]['preexisting_source_review_evidence']=='SOURCE_DAY_EXACT_ID_CANDIDATE'
 assert q[0]['verified_recovery']=='NO'
 print('Stage85 self-test PASS')

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--stage84-dir',type=pathlib.Path,default=pathlib.Path('reports/stage85/input'))
 p.add_argument('--recent-dir',type=pathlib.Path,default=pathlib.Path('data/racing-results'))
 p.add_argument('--verified-results',type=pathlib.Path,default=pathlib.Path('data/race-results.json'))
 p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage85/output'))
 p.add_argument('--self-test',action='store_true');a=p.parse_args()
 if a.self_test:self_test();return
 folder=a.stage84_dir
 summary=read_json(folder/'stage84_summary.json',{})
 if summary.get('stage')!=84:raise RuntimeError('Expected Stage84 summary in '+str(folder))
 people=read_csv(folder/'stage84_active_horses.csv')
 if len(people)!=summary['active_horses_in_cohort']:raise RuntimeError('Stage84 cohort count mismatch')
 sources=source_index(a.recent_dir,a.verified_results)
 report,queue,debuts,other=run(people,summary,sources)
 a.out.mkdir(parents=True,exist_ok=True)
 (a.out/'stage85_summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 fields=list(queue[0]) if queue else ['priority','horse','stage84_status']
 write_csv(a.out/'stage85_recovery_queue.csv',queue,fields)
 write_csv(a.out/'stage85_debut_unverified_not_gaps.csv',debuts,list(people[0]))
 write_csv(a.out/'stage85_recent_supported_not_lifetime_certified.csv',other,list(people[0]))
 lines=['# Model E — Stage 85 identity and recent form reconciliation', '',
  'Stage84 snapshot: '+str(report['source_stage84_as_of']),
  '',f"- Cohort: **{report['horse_count']}** distinct declared horses",
  f"- Form-applicable: **{report['applicable_previous_history_cohort']}**",
  f"- Recent history supported: **{report['current_recent_history_supported']} ({report['current_recent_support_pct']}%)**",
  f"- Further valid reconciliations needed to reach 90%: **{report['remaining_horses_to_reconcile_for_90pct']}**",
  f"- Investigation queue: **{report['investigate_count']}**",
  f"- Possible debutants (not treated as gaps): **{report['candidate_not_confirmed_debuts']}**",
  '', '## Priority queue', '']
 for status,n in sorted(report['status_breakdown'].items(),key=lambda q:-q[1]):lines.append(f'- {status}: {n}')
 lines+=['','## Existing independent final-result source candidates', '']
 for status,n in sorted(report['source_day_candidate_breakdown'].items(),key=lambda q:-q[1]):lines.append(f'- {status}: {n}')
 lines+=['','A candidate named runner on the expected date is **not** automatically a confirmed identity or a recovered form run.',
  'Stage85 is read-only; further source verification is required before Stage86 model training.', '']
 (a.out/'stage85_report.md').write_text('\n'.join(lines),encoding='utf-8')
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()
