#!/usr/bin/env python3
"""Reference-led RECURRENT EVENT ID proposals for GB/IE race results, 1988-2026.

Uses a manually curated register based on reference lists of named annual events,
plus the previously extracted original race names. It does NOT claim any edition
or winner is verified. Does not scrape or import any proprietary results.

Safety:
 - no guessing between annual races from course/distance alone;
 - no overwriting documented/curated previous identities;
 - one edition per event and year; any collisions quarantined;
 - retain all 505k original race keys unchanged;
 - no use of future information to predict racing outcomes.
"""
import argparse, csv, collections, datetime, json, re, unicodedata, pathlib, os

FIELDS=['region_scope','event_id','match_status','method','confidence','edition_year','date','course','race_type','distance','race_name','stage2_race_id','source_race_id','evidence','review_reason']
def norm(v):
 s=unicodedata.normalize('NFKD',str(v or '').lower()).encode('ascii','ignore').decode()
 s=re.sub(r'(?<=\d),(?=\d{3}\b)','',s)
 s=re.sub(r'[^a-z0-9]+',' ',s)
 return ' '.join(s.split())
def cname(v):
 s=norm(v)
 return re.sub(r'\b(?:aw|ire|gb|nh)\b','',s).strip()
def month_gap(a,b):
 return min((a-b)%12,(b-a)%12)
def is_match(r,t):
 if r['region_scope']!=t['region']:return False
 where=cname(r['course']); venue=cname(t['course'])
 if where!=venue and not (venue=='dundalk' and where.startswith('dundalk')):return False
 typ=norm(r['race_type'])
 if typ!=norm(t['race_type']):return False
 try:
  if month_gap(int(r['date'][5:7]),int(t['month']))>1:return False
 except (ValueError,IndexError):return False
 name=norm(r['race_name']);phrase=norm(t['identity_phrase'])
 if not re.search(r'(?<!\w)'+re.escape(phrase)+r'(?!\w)',name):return False
 # An annual Classic and its preparation trial must never be mixed.
 if 'trial' in name.split() and 'trial' not in norm(t['canonical_name']).split():return False
 if 'clonmel oil chase day' in name or 'clonmel oil raceday' in name or 'clonmel oil service station' in name:return False
 # Avoid generic sponsorship and similarly named promotional races entering graded series.
 if any(phrase in name for phrase in ('free entry to', 'yearling stakes', 'yearling sales', 'hospital handicap', 'hospital stakes', 'jump jockeys nunthorpe', 'school handicap')):return False
 if t['event_id']=='GB_FLAT_CHAMPION_STAKES' and 'fillies' in name:return False
 # Be careful with similarly named races such as Irish Champion Chase and its novices.
 if 'novice' in name.split() and 'novice' not in norm(t['canonical_name']).split() and 'novice' not in phrase.split():return False
 return True

def writecsv(p,fields,rows):
 with open(p,'w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--registry',required=True);ap.add_argument('--out',default='stage4_reference_expansion');opt=ap.parse_args()
 out=pathlib.Path(opt.out);out.mkdir(parents=True,exist_ok=True)
 with open(opt.registry,encoding='utf-8-sig') as f: registry=list(csv.DictReader(f))
 assert len({t['event_id'] for t in registry})==len(registry),'Duplicate registry event id'
 byplace=collections.defaultdict(list)
 for t in registry:byplace[(t['region'],cname(t['course']),norm(t['race_type']))].append(t)
 candidates=collections.defaultdict(list); original=[]; old_counts=collections.Counter()
 with open(opt.input,encoding='utf-8-sig',newline='') as f:
  for r in csv.DictReader(f):
   original.append(r);old_counts[r['match_status']]+=1
   k=(r['region_scope'],cname(r['course']),norm(r['race_type']))
   suggestions=[t for t in byplace.get(k,[]) if is_match(r,t)]
   if len(suggestions)==1:
    t=suggestions[0]
    candidates[t['event_id']].append((r,t))
   elif len(suggestions)>1:
    candidates['MULTIPLE'].append((r,suggestions))
 # Restrict event-year assignment to a unique original race. A collision is not resolved by selecting first.
 collisions=[]; permitted={}; event_counts=collections.Counter(); existing_recognized=collections.Counter(); covered=collections.Counter()
 for event_id,groups in candidates.items():
  if event_id=='MULTIPLE':
   for r,ts in groups:collisions.append({'event_id':'MULTIPLE','edition_year':r['date'][:4],'stage2_race_ids':r['stage2_race_id'],'reason':'Matched several reference series; manual inspection needed','original_titles':r['race_name']})
   continue
  years=collections.defaultdict(list)
  for r,t in groups:
   yr=int(r['edition_year'] or r['date'][:4])
   years[yr].append((r,t))
  for year,ls in years.items():
   if len(ls)>1:
    collisions.append({'event_id':event_id,'edition_year':year,'stage2_race_ids':'|'.join(r['stage2_race_id'] for r,t in ls),'reason':'Multiple races matched same annual reference event; leave unchanged','original_titles':'|'.join(r['race_name'] for r,t in ls)})
    continue
   r,t=ls[0]
   permitted[r['stage2_race_id']]=t
   event_counts[event_id]+=1
   if r['match_status'] in ('CURATED_CANDIDATE','DOCUMENTED_EVENT_CANDIDATE'):
    existing_recognized[event_id]+=1
 # Also protect existing IDs: if a prior curated event has one of the proposed editions, do not relabel it.
 # Reuse the SAME permanent ID when this annual event already has curated editions.
 aliases=collections.defaultdict(collections.Counter)
 for event_id,group in candidates.items():
  if event_id=='MULTIPLE':continue
  for r,t in group:
   if r['match_status'] in ('CURATED_CANDIDATE','DOCUMENTED_EVENT_CANDIDATE'):
    aliases[event_id][r['event_id']]+=1
 canonical={}
 for t in registry:
  votes=aliases[t['event_id']]
  if len(votes)>1:raise ValueError('Conflicting pre-existing curated IDs: '+t['event_id']+' '+str(votes))
  canonical[t['event_id']]=next(iter(votes)) if votes else t['event_id']
 matched=[]; overrides=[]; status=collections.Counter(); skipped=collections.Counter(); ids=set()
 with open(out/'all_races_with_reference_event_ids.csv','w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction='ignore');w.writeheader()
  for r in original:
   rid=r['stage2_race_id'];t=permitted.get(rid)
   if t:
    covered[t['event_id']]+=1
    if r['match_status'] in ('CURATED_CANDIDATE','DOCUMENTED_EVENT_CANDIDATE'):
     skipped['preexisting_curated_protected']+=1
    else:
     previous=r['event_id'];before=r['match_status']
     r.update(event_id=canonical[t['event_id']],match_status='REFERENCE_SERIES_CANDIDATE',method='named_race_reference_list_and_original_title_course_month',confidence='REFERENCE_EXISTS_EDITION_UNVERIFIED',evidence=t['reference_url'],review_reason='Matched named event reference; historical edition/winner not independently verified')
     matched.append({'stage2_race_id':rid,'date':r['date'],'original_race_name':r['race_name'],'course':r['course'],'previous_event_id':previous,'previous_status':before,'reference_event_id':canonical[t['event_id']],'reference_event_name':t['canonical_name'],'evidence_url':t['reference_url']})
     if previous!=canonical[t['event_id']]:overrides.append((before,previous))
   w.writerow({k:r.get(k,'') for k in FIELDS});status[r['match_status']]+=1
   if rid in ids:raise ValueError('Original race ID duplicated: '+rid)
   ids.add(rid)
 coverage=[]
 for t in registry:
  name=t['event_id'];total=event_counts[name];coverage.append({'event_id':name,'race_name':t['canonical_name'],'country':t['region'],'course':t['course'],'period_month':t['month'],'linked_candidates':total,'existing_curated_protected':existing_recognized[name],'canonical_id_used':canonical[name],'source_url':t['reference_url'],'status':'EDITION_UNVERIFIED' if total else 'NO_SOURCE_NAME_MATCH','warning':'Original race title may differ entirely under older sponsors; no inference from missing year'})
 writecsv(out/'reference_registry_coverage.csv',list(coverage[0]),sorted(coverage,key=lambda r:(-r['linked_candidates'],r['event_id'])))
 writecsv(out/'new_reference_candidates.csv',['stage2_race_id','date','original_race_name','course','previous_event_id','previous_status','reference_event_id','reference_event_name','evidence_url'],matched)
 writecsv(out/'reference_ambiguities.csv',['event_id','edition_year','stage2_race_ids','reason','original_titles'],collisions)
 report={'status':'COMPLETE','original_races_read':len(original),'original_race_ids_retained':len(ids),'reference_race_series':len(registry),'registry_series_with_at_least_one_source_match':sum(bool(covered[t['event_id']]) for t in registry),'matched_source_editions_in_reference_list':sum(event_counts.values()),'new_or_reclassified_race_records':len(matched),'previously_unmatched_races_linked_to_references':sum(r['previous_status']=='NO_RECURRING_MATCH' for r in matched),'previous_automated_candidates_with_reference_support':sum(r['previous_status'] in ('STRONG_AUTOMATED','EXPANDED_TITLE_CANDIDATE') for r in matched),'protected_prior_curated_editions':skipped['preexisting_curated_protected'],'reference_ambiguity_groups':len(collisions),'prior_curated_ids_reused':sum(canonical[t['event_id']]!=t['event_id'] for t in registry),'old_status_counts':dict(old_counts),'new_status_counts':dict(status),'limitations':['Published reference lists verify that named race SERIES exist; they do not independently prove each historic edition belongs to the same series.','The original CSV name must retain the reference phrase for automated matching. Completely renamed races remain unresolved, requiring documented historical aliases.','Venue transfers are not inferred; they require individually verified exceptions.','Irish 2000-2002 original source gap is not filled by this process.','Reference list uses predominantly current race names; renamed or discontinued historic races may be absent.','No independent verification of winners or runner records here.','Race names, date, course and IDs remain source faithful; there is no imputed result.']}
 (out/'reference_expansion_audit.json').write_text(json.dumps(report,indent=2))
 (out/'README.md').write_text('# Stage 4 — Named race reference expansion\n\nAll reference matches remain **candidates**. This is a structural identity audit, NOT an annual winner verification and NOT a backtest.\n\n'+json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if not k.endswith('counts')},indent=2))

if __name__=='__main__':main()
