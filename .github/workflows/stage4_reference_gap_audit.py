#!/usr/bin/env python3
"""Report missing source-linked annual editions WITHOUT asserting races were cancelled."""
import csv, collections, datetime, json, pathlib, argparse

def writecsv(p,cols,rows):
 with open(p,'w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--mapping',required=True);ap.add_argument('--registry',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
 out=pathlib.Path(a.out);out.mkdir(exist_ok=True,parents=True)
 refs=list(csv.DictReader(open(a.registry,encoding='utf-8-sig')))
 source_coverage=collections.defaultdict(collections.Counter)
 # Canonical ID is already in the output coverage CSV from the preceding step.
 canonical={r['event_id']:r['canonical_id_used'] for r in csv.DictReader(open(out/'reference_registry_coverage.csv',encoding='utf-8-sig'))}
 rmap=collections.defaultdict(list)
 for r in csv.DictReader(open(a.mapping,encoding='utf-8-sig')):
  year=int(r['date'][:4]);source_coverage[r['region_scope']][year]+=1
  # Curated and reference evidence are audited separately; auto name matches do not count.
  if r['match_status'] in ('REFERENCE_SERIES_CANDIDATE','CURATED_CANDIDATE','DOCUMENTED_EVENT_CANDIDATE'):
   rmap[r['event_id']].append(r)
 output=[];missing=[]
 for t in refs:
  cid=canonical[t['event_id']];ls=[r for r in rmap[cid] if r['region_scope']==t['region']]
  # A preexisting curated event ID may cover records from another venue or name: fine.
  years=collections.Counter(int(r.get('edition_year') or r['date'][:4]) for r in ls)
  ys=sorted(years)
  span=range(ys[0],ys[-1]+1) if ys else []
  gaps=[]
  for y in span:
   if y in years:continue
   flag='IRISH_SOURCE_COVERAGE_ZERO' if t['region']=='IRELAND' and source_coverage['IRELAND'][y]==0 else 'MISSING_FROM_MATCHED_EDITION_INDEX'
   missing.append({'event_id':cid,'canonical_name':t['canonical_name'],'edition_year':y,'flag':flag,'country_races_available':source_coverage[t['region']][y],'warning':'Race may have been run under an entirely different name/course or may not have been scheduled; requires research'})
   gaps.append(y)
  output.append({'reference_event_id':t['event_id'],'canonical_event_id':cid,'race_name':t['canonical_name'],'reference_url':t['reference_url'],'linked_editions':len(ls),'distinct_edition_years':len(ys),'earliest_link':ys[0] if ys else '', 'latest_link':ys[-1] if ys else '', 'gaps_between_linked_years':len(gaps),'note':'MISSING DOES NOT MEAN CANCELLED'})
 writecsv(out/'reference_history_coverage.csv',list(output[0]),output)
 writecsv(out/'missing_reference_event_years.csv',list(missing[0]) if missing else ['event_id','canonical_name','edition_year','flag','country_races_available','warning'],missing)
 (out/'gap_audit_summary.json').write_text(json.dumps({'reference_events':len(output),'references_with_at_least_one_link':sum(x['linked_editions']>0 for x in output),'unresolved_year_gaps':len(missing),'irish_2000_2002_source_gaps_for_reference_races':sum(x['flag']=='IRISH_SOURCE_COVERAGE_ZERO' for x in missing)},indent=2))
 print((out/'gap_audit_summary.json').read_text())
if __name__=='__main__':main()
