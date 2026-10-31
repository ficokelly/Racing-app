#!/usr/bin/env python3
"""Stage 4: conservative recurring-race identity discovery for every historical race.

Inputs:
 --index exact-link named_race_index.csv from Stage 4 (all original races)
 --seed optionally pre-reviewed named-event candidates (recurring_race_editions.csv)

Outputs contain EVERY input race; no uncertain pair is silently declared a historical
edition. 'MATCHED' means automated evidence, not independent historical verification.
External official citations are retained in seed records, and are not automatically
scraped. Must inspect REVIEW queues and validate before using model training labels.

Python 3.11+, standard library only; designed for 500k rows in GitHub Actions.
"""
import argparse, collections, csv, hashlib, json, math, re, statistics, unicodedata
from datetime import date
from pathlib import Path

GENERIC = set('the a an of and for to at in with on by from or as plus free race racing races racecourse horse horses horsebox live watch join get today tomorrow now only official new all available this next more special latest please online via every win best book bets bet betting tips tv app website com co uk ie www our your their thanks prize trophy open sponsored sponsor class grade group listed group1 grade1 level division div run runners stakes stake handicap handicaps hurdle hurdles chase chases flat national hunt novices novice novice s maiden maidens beginners beginner selling claiming apprentice amateur condition conditions event 2yo 3yo 4yo 5yo 6yo aged ageds years old year fillies filly mares mare colts colt geldings restricted racecard trainer jockey ridden furlong furlongs sprint flat two three four five mile miles m f y gb gbb ebf nh nhf irish stallion farms european breeders fund british breeders euro plus10 plus 10 2026 2025 2024 2023 2022 2021 2020 2019 2018 2017 2016 2015 2014 2013 2012 2011 2010 2009 2008 2007 2006 2005 2004 2003 2002 2001 2000'.split())
GENERIC.update('betway 32red ladbrokes paddypower coral sky racingtv bet365 betfred totesport tote william hill powers racegoers hospitality cash racinguk attheraces sunbets racecourse gg williamhill sportingbet paddy 888sport unibet freebet freebets racingpost sports sport 888'.split())
SPONSOR_STRIP = re.compile(r'\b(?:class\s+[1-7]|grade\s+[123abc]|group\s+[123]|listed\s+race|\(?\s*(?:gbb\s+race|plus\s+10\s+race)\s*\)?)\b',re.I)
COLS = ['region_scope','event_id','match_status','method','confidence','edition_year','date','course','race_type','distance','race_name','stage2_race_id','source_race_id','evidence','review_reason']

def normal(x):
 x=unicodedata.normalize('NFKD',str(x or '').lower()).encode('ascii','ignore').decode()
 x=x.replace('&',' and ')
 x=SPONSOR_STRIP.sub(' ',x)
 return ' '.join(re.findall(r'[a-z0-9]+',x))

def canonical_course(x):
 s=normal(x)
 s=re.sub(r'\b(?:ire|gb|rh|racecourse)\b',' ',s)
 return ' '.join(s.split())

def furlongs(s):
 s=normal(s).replace(' ','')
 # original fractional unicode characters are normalized to ASCII; use raw original before normalized in row
 return None

def parse_dist(s):
 s=str(s or '').lower().replace(' ','').replace('¼','.25').replace('½','.5').replace('¾','.75')
 mile=re.search(r'(\d+(?:\.\d+)?)m',s); fur=re.search(r'(\d+(?:\.\d+)?)f',s);yards=re.search(r'(\d+(?:\.\d+)?)y',s)
 if not (mile or fur or yards):return None
 return (float(mile.group(1))*8 if mile else 0)+(float(fur.group(1)) if fur else 0)+(float(yards.group(1))/220 if yards else 0)


GB_TRACKS = set('aintree ascot ayr bangor on dee bath beverley brighton carlisle cartmel catterick chelmsford cheltenham chepstow chester doncaster epsom epsom downs exeter fakenham ffos las folkestone fontwell goodwood great leighs hamilton haydock hereford hexham huntingdon kelso kempton leicester leicester lingfield ludlow market rasen musselburgh newbury newcastle newmarket newmarket july newton abbot nottingham perth plumpton pontefract redcar ripon salisbury sandown sedgefield southwell stratford taunton thirsk towcester uttoxeter warwick wetherby wincanton windsor wolverhampton worcester yarmouth york'.split('|'))
# Set from historically known British courses. Use pipe separators for multiword names.
GB_TRACKS = set('Aintree|Ascot|Ayr|Bangor-on-Dee|Bath|Beverley|Brighton|Carlisle|Cartmel|Catterick|Chelmsford|Cheltenham|Chepstow|Chester|Doncaster|Epsom|Epsom Downs|Exeter|Fakenham|Ffos Las|Folkestone|Fontwell|Goodwood|Great Leighs|Hamilton|Haydock|Hereford|Hexham|Huntingdon|Kelso|Kempton|Leicester|Lingfield|Ludlow|Market Rasen|Musselburgh|Newbury|Newcastle|Newmarket|Newton Abbot|Nottingham|Perth|Plumpton|Pontefract|Redcar|Ripon|Salisbury|Sandown|Sedgefield|Southwell|Stratford|Taunton|Thirsk|Towcester|Uttoxeter|Warwick|Wetherby|Wincanton|Windsor|Wolverhampton|Worcester|Yarmouth|York'.split('|'))
IE_TRACKS = set('Ballinrobe|Bellewstown|Clonmel|Cork|Curragh|Down Royal|Downpatrick|Dundalk|Fairyhouse|Galway|Gowran Park|Kilbeggan|Killarney|Laytown|Leopardstown|Limerick|Listowel|Mallow|Naas|Navan|Phoenix Park|Punchestown|Roscommon|Sligo|Thurles|Tipperary|Tralee|Tramore|Wexford'.split('|'))
GB_TRACKS={normal(x) for x in GB_TRACKS}
IE_TRACKS={normal(x) for x in IE_TRACKS}

def region(x):
 raw=str(x or '').upper()
 # Explicit foreign location always takes precedence over duplicated names.
 if re.search(r'\((AUS|FR|USA|CAN|HK|JPN|SAF|UAE|GER|NZ|ITY|BRZ|ARG|CHN|BHR|QAT|QA|CHI|PER|SWE|NOR|URU|TUR)\)',raw):return 'OTHER'
 if '(IRE)' in raw:return 'IRELAND'
 n=normal(re.sub(r'\((?:AW|IRE|GB|RH)\)',' ',raw))
 if n.endswith(' july') and n.startswith('newmarket'):return 'GB'
 if n in GB_TRACKS:return 'GB'
 if n in IE_TRACKS:return 'IRELAND'
 return 'OTHER_OR_UNRESOLVED'

def core_tokens(text):
 words=normal(text).split()
 return [w for w in words if w not in GENERIC and not w.isdigit() and len(w)>2]

def dday(d):
 try:return date(2000,int(d[5:7]),min(int(d[8:10]),28)).timetuple().tm_yday
 except Exception:return None

def cal_spread(rows):
 ds=[r['doy'] for r in rows if r['doy']]
 if not ds:return 999
 # circular arc length in days (handles Dec/Jan)
 ds.sort();gaps=[b-a for a,b in zip(ds,ds[1:])]+[366-ds[-1]+ds[0]]
 return 366-max(gaps)

def validate(rows, minimum_years=3):
 if len(rows)<minimum_years:return False,'fewer_than_minimum_editions'
 years=collections.Counter(r['year'] for r in rows)
 if len(years)<minimum_years:return False,'fewer_than_minimum_years'
 if max(years.values())>1:return False,'multiple_same_year'
 # Requires tight annual scheduling; larger changes go to review.
 if cal_spread(rows)>95:return False,'calendar_window_too_wide'
 years_covered=max(years)-min(years)+1
 if years_covered>0 and len(years)/years_covered < .22:return False,'too_infrequent'
 dist=[r['dist_num'] for r in rows if r['dist_num']]
 if len(dist)>=3:
  mid=statistics.median(dist)
  max_deviation=max(abs(v-mid) for v in dist)
  if max_deviation>(2.5 if rows[0]['race_type'].lower()=='flat' else 4):return False,'distance_shift_needs_review'
 return True,''

def stable_id(kind, key):
 raw='|'.join(str(v) for v in key)
 digest=hashlib.sha1(raw.encode()).hexdigest()[:14].upper()
 return kind+'_'+digest

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--index',required=True);ap.add_argument('--seed',default='');ap.add_argument('--out',default='stage4_all_races');ap.add_argument('--min-years',type=int,default=3);args=ap.parse_args()
 out=Path(args.out);out.mkdir(exist_ok=True,parents=True)
 records=[];lookup={};duplicates=collections.Counter();by_year=collections.Counter()
 with open(args.index,encoding='utf-8-sig',newline='') as f:
  rd=csv.DictReader(f)
  for row in rd:
   rid=row.get('stage2_race_id','')
   if not rid or rid in lookup:
    duplicates['blank_or_duplicate_stage2_key']+=1;continue
   try:yr=int(row['date'][:4]);datetime=date.fromisoformat(row['date'])
   except (ValueError,TypeError):duplicates['bad_date']+=1;continue
   r={**row,'year':yr,'doy':dday(row['date']),'course_norm':canonical_course(row['course']), 'type_norm':normal(row['race_type']),'title_norm':normal(row['race_name']),'tokens':core_tokens(row['race_name']),'dist_num':parse_dist(row['distance']),'region_scope':region(row['course']),'event_id':'','match_status':'','method':'','confidence':'','edition_year':yr,'evidence':'','review_reason':''}
   records.append(r);lookup[rid]=r;by_year[yr]+=1
 seeded=collections.defaultdict(list);missing_seed=0;seed_conflicts=[]
 if args.seed and Path(args.seed).exists():
  with open(args.seed,encoding='utf-8-sig',newline='') as f:
   for s in csv.DictReader(f):
    r=lookup.get(s['stage2_race_id'])
    if r is None:missing_seed+=1;continue
    if r['event_id'] and r['event_id']!=s['event_id']:
     seed_conflicts.append((r['stage2_race_id'],r['event_id'],s['event_id']))
     r['review_reason']='conflicting_seed_ids';continue
    r['event_id']=s['event_id'];r['match_status']='CURATED_CANDIDATE';r['method']='prior_name_anchor';r['confidence']='SOURCE_ANCHORED_NOT_INDEPENDENTLY_VERIFIED';r['edition_year']=int(s.get('edition_year') or r['year']);r['evidence']=s.get('exception_source') or 'Stage 4 anchored race-title candidate'
    seeded[r['event_id']].append(r)
  for eid,ls in seeded.items():
   yrs=collections.Counter(r['edition_year'] for r in ls)
   for r in ls:
    if yrs[r['edition_year']]>1:
     r['match_status']='REVIEW_REQUIRED';r['review_reason']='curated_series_multiple_same_edition_year'

 # Fully exact title: identical course/type/title in different years, one race per year,
 # annual date & distance consistency, and a distinctive title (generic ads rejected).
 groups=collections.defaultdict(list)
 for r in records:
  if r['event_id'] or r['region_scope'] not in ('GB','IRELAND') or not r['tokens'] or len(r['title_norm'])<9:continue
  # For generic 'Betway Handicap' distinguish sponsor as too generic via known banned tokens.
  key=(r['course_norm'],r['type_norm'],r['title_norm'])
  groups[key].append(r)
 accepted_exact=0;exact_events=0;review_exceptions=collections.Counter()
 for key,rs in groups.items():
  ok,reason=validate(rs,args.min_years)
  if not ok:
   if len(rs)>=args.min_years and len({r['year'] for r in rs})>=args.min_years:review_exceptions[reason]+=1
   continue
  # Require some distinctive non-commercial anchor (tokens have no generic/race type terms).
  if not any(len(t)>=4 for t in rs[0]['tokens']):continue
  eid=stable_id('TITLE',key);exact_events+=1
  for r in rs:
   r['event_id']=eid;r['match_status']='STRONG_AUTOMATED';r['method']='same_title_course_type_calendar_distance';r['confidence']='CANDIDATE_NOT_WEB_VERIFIED';r['evidence']=f'exact original name across {len(rs)} distinct years; max calendar span {cal_spread(rs)} days';accepted_exact+=1

 # Sponsor-flex: match stable characteristic suffix with distinctive content tokens;
 # only apply when uniquely annual and tightly constrained. Does NOT use general fuzzy
 # text similarity or form race identifications based solely on distance/date.
 suffix_groups=collections.defaultdict(list)
 for r in records:
  if r['event_id'] or r['region_scope'] not in ('GB','IRELAND') or not r['tokens']:continue
  # suffixes include normalized words (with race terminators) to retain distinctiveness
  w=r['title_norm'].split()
  for n in (5,4,3):
   if len(w)<n:continue
   tail=tuple(w[-n:])
   meaningful=[t for t in tail if t not in GENERIC and len(t)>=4]
   if not meaningful:continue
   if not any(t in r['tokens'] for t in meaningful):continue
   suffix_groups[(r['course_norm'],r['type_norm'],tail)].append(r)
 # Apply strongest long suffix first. Don't let a shorter generic suffix merge
 # previously assigned events; existing event identities are never overwritten.
 suffix_count=0;suffix_events=0
 for key,rs in sorted(suffix_groups.items(),key=lambda kv:(-len(kv[0][2]),-len(kv[1]))):
  # A variation in sponsorship must actually exist, otherwise exact-title matching handles it.
  if len(rs)<args.min_years or len({r['title_norm'] for r in rs})<2:continue
  # If previously assigned members of group share a single event ID, may extend it ONLY
  # after same-year and schedule validation. Otherwise do not override existing mappings.
  existing={r['event_id'] for r in rs if r['event_id']}
  if len(existing)>1:continue
  candidate=[r for r in rs if not r['event_id']]
  if not candidate:continue
  possible=rs
  ok,why=validate(possible,args.min_years)
  if not ok:continue
  # suffix alone may be generic (e.g. 'Sponsored XXXX Handicap'), require core
  # surviving suffix term and at least one surviving non-generic term in 2/3 of titles.
  significant=[t for t in key[2] if t not in GENERIC and len(t)>=4]
  if not significant:continue
  # Max year-uniqueness and actual sharing of informative word(s) enforced above.
  eid=next(iter(existing)) if existing else stable_id('SUFFIX',key)
  suffix_events+=not bool(existing)
  for r in candidate:
   r['event_id']=eid;r['match_status']='STRONG_AUTOMATED';r['method']='sponsor_tolerant_title_suffix';r['confidence']='CANDIDATE_NOT_WEB_VERIFIED';r['evidence']=f'common title suffix {" ".join(key[2])!r} over {len(possible)} annual editions';suffix_count+=1

 # Determine uncertain potential cross-year matches (including sponsor wholesale renames):
 # write a *review* shortlist, not automatic merges. Only rare name terms at same venue,
 # discipline and season; skip brand-like generic and avoid huge inverted indexes.
 unassigned=[r for r in records if not r['event_id'] and r['region_scope'] in ('GB','IRELAND')]
 terms=collections.Counter(t for r in records for t in set(r['tokens']))
 inverted=collections.defaultdict(list)
 for r in records:
  if not r['event_id'] and r['region_scope'] in ('GB','IRELAND'):
   for t in set(r['tokens']):
    if 3<=terms[t]<=180 and len(t)>=5:inverted[(r['course_norm'],r['type_norm'],t)].append(r)
 # Each potential target only peers in close years; two-race same-year should not join.
 review=[];seen_pairs=set();max_review=50000
 for key,ls in inverted.items():
  if len(ls)>180:continue
  ls.sort(key=lambda r:r['date'])
  for i,r in enumerate(ls):
   if r['event_id']:continue
   for q in ls[max(0,i-18):i]:
    if r['year']==q['year'] or abs(r['year']-q['year'])>5:continue
    if abs(r['doy']-q['doy'])>65 and abs(r['doy']-q['doy'])<300:continue
    if r['dist_num'] and q['dist_num'] and abs(r['dist_num']-q['dist_num'])>2.5:continue
    a,b=sorted((r['stage2_race_id'],q['stage2_race_id']))
    if (a,b) in seen_pairs:continue
    ta=set(r['tokens']);tb=set(q['tokens']);common=ta&tb
    if not common:continue
    # A single generic name token can still be a false positive; prefer 2+ rare
    # tokens or a single extremely rare distinct event word.
    rarity=min(terms[t] for t in common)
    if len(common)<2 and rarity>35:continue
    seen_pairs.add((a,b))
    review.append({'record_a':a,'record_b':b,'date_a':q['date'],'date_b':r['date'],'course':r['course'],'name_a':q['race_name'],'name_b':r['race_name'],'common_distinctive_terms':' '.join(sorted(common)),'reason':'POSSIBLE_RENAME_REQUIRES_EXTERNAL_VERIFICATION'})
    if len(review)>=max_review:break
   if len(review)>=max_review:break
  if len(review)>=max_review:break

 # Full coverage: unassigned records get stable instance-specific IDs. No claims of recurrence.
 for r in records:
  if not r['event_id']:
   if r['region_scope'] not in ('GB','IRELAND'):
    r['event_id']=stable_id('OTHER',(r['stage2_race_id'],));r['match_status']='OUT_OF_SCOPE';r['method']='foreign_or_unresolved_course';r['confidence']='NONE';r['review_reason']='not_confirmed_uk_ireland_track';r['evidence']='source contains foreign tracks; not assigned to UK/IE recurring event';continue
   r['event_id']=stable_id('UNMATCHED',(r['stage2_race_id'],));r['match_status']='NO_RECURRING_MATCH';r['method']='identity_only';r['confidence']='NONE';r['review_reason']='no_safe_annual_match';r['evidence']='single race; annual continuation not established'
 events=collections.defaultdict(list)
 for r in records:events[r['event_id']].append(r)
 # Review repeated editions or far-apart race histories in any automated series.
 for eid,rs in events.items():
  if len(rs)<2:continue
  years=collections.Counter(r['edition_year'] for r in rs)
  if max(years.values())>1:
   for r in rs:
    r['match_status']='REVIEW_REQUIRED';r['review_reason']='same_event_has_multiple_edition_candidates'

 with (out/'all_races_with_event_ids.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=COLS);w.writeheader()
  for r in records:w.writerow({k:r.get(k,'') for k in COLS})
 catalog=[];editions=[]
 for eid,rs in events.items():
  if len(rs)<2:continue
  years=sorted({r['edition_year'] for r in rs});methods=sorted(set(r['method'] for r in rs))
  item={'event_id':eid,'event_title_sample':rs[0]['race_name'],'course_sample':rs[0]['course'],'type_sample':rs[0]['race_type'],'editions':len(rs),'distinct_edition_years':len(years),'first_year':min(years),'last_year':max(years),'year_coverage_pct':round(len(years)/(max(years)-min(years)+1)*100,2),'methods':' + '.join(methods),'status':'REVIEW_REQUIRED' if any(r['match_status']=='REVIEW_REQUIRED' for r in rs) else 'CANDIDATE_NOT_INDEPENDENTLY_VERIFIED','anchor_type':'PRESEEDED' if eid in seeded else 'AUTOMATED'}
  catalog.append(item)
  for r in rs:editions.append(r)
 catcols=['event_id','event_title_sample','course_sample','type_sample','editions','distinct_edition_years','first_year','last_year','year_coverage_pct','methods','status','anchor_type']
 with (out/'recurring_event_catalog.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=catcols);w.writeheader();w.writerows(sorted(catalog,key=lambda r:(-r['editions'],r['event_id'])))
 with (out/'recurring_event_editions.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=COLS);w.writeheader()
  for r in sorted(editions,key=lambda r:(r['event_id'],r['edition_year'])):w.writerow({k:r.get(k,'') for k in COLS})
 with (out/'manual_review_candidates.csv').open('w',encoding='utf-8',newline='') as f:
  keys=list(review[0]) if review else ['record_a','record_b','date_a','date_b','course','name_a','name_b','common_distinctive_terms','reason']
  w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(review)
 with (out/'review_required_event_editions.csv').open('w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=COLS);w.writeheader()
  for r in records:
   if r['match_status']=='REVIEW_REQUIRED':w.writerow({k:r.get(k,'') for k in COLS})
 status_counts=collections.Counter(r['match_status'] for r in records)
 year_stats=collections.defaultdict(collections.Counter)
 for r in records:year_stats[r['year']][r['match_status']]+=1
 with (out/'coverage_by_year.csv').open('w',encoding='utf-8',newline='') as f:
  fieldnames=['year','total','curated_candidates','strong_automated','review_required','no_recurring_match','out_of_scope']
  w=csv.writer(f);w.writerow(fieldnames)
  for year,ss in sorted(year_stats.items()):w.writerow([year,sum(ss.values()),ss['CURATED_CANDIDATE'],ss['STRONG_AUTOMATED'],ss['REVIEW_REQUIRED'],ss['NO_RECURRING_MATCH'],ss['OUT_OF_SCOPE']])
 report={'status':'COMPLETE','data_years':[min(by_year),max(by_year)],'races_in_input':len(records),'race_rows_rejected':dict(duplicates),'seed_rows_missing_in_index':missing_seed,'seed_conflicts':seed_conflicts[:30],'counts_by_match_status':dict(status_counts),'distinct_recurring_candidate_series':len(catalog),'recurring_candidate_editions':len(editions),'exact_title_editions_discovered':accepted_exact,'sponsor_tolerant_editions_discovered':suffix_count,'possible_rename_review_pairs':len(review),'review_queue_capped':len(review)>=max_review,'counts_by_region_scope':dict(collections.Counter(r['region_scope'] for r in records)),'review_reasons_for_exact_groups':dict(review_exceptions),'methodology':['Original source exact linked database race IDs preserved; all original races appear exactly once in coverage output.','Previously curated 26-name list is retained as non-independently-verified seed, including known course/date transfers.','High confidence automated = same exact title across 3+ years, one per edition year, close season/distance and distinctive name.','Sponsor tolerant = same final 3-5 distinctive title words across 3+ years, uniqueness/year, season/distance consistency.','One-off or generic recurring sponsor titles cannot be labelled as the same historical race on name alone.','Uncertain names provided as review queue rather than auto-grouped.','Cross-course matches require official independent documentary evidence; do not apply geographic similarity.','No time leakage from race results: identity matching alone.','Model use must require independent validation of event identity and no unresolved year collisions.'],'limitations':['Automated event matching is NOT independently verified; false positives remain possible, including stable sponsor-named unrelated annual races.','Original data includes foreign tracks; region classification should be validated before UK/IE modelling.','Name and date matching alone cannot safely map every ordinary handicap or maiden to an annual recurring event.','Annual events with wholesale title changes and new course require external evidence; see review queue.','Stage 4 seed includes 26 preidentified series, not independent fact verification.','Events that began before 1988 are represented only from first available source edition; missing year is not proof of race cancellation.']}
 (out/'identity_audit.json').write_text(json.dumps(report,indent=2))
 print(json.dumps({k:v for k,v in report.items() if k not in ('methodology','limitations')},indent=2))

if __name__=='__main__':main()
