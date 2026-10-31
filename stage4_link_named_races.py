#!/usr/bin/env python3
"""Audit and link the ORIGINAL Kaggle race titles to Stage 2 SQLite race IDs.

Read-only: does not modify the 2+ GB staging database. Uses EXACT Stage 2
race-ID construction, not fuzzy course/date matching. Handles source_race_id
collisions across tracks. A named-race identity is certified ONLY for the
Cheltenham Festival Gold Cup example; other titles are preserved as evidence.
"""
import argparse
import collections
import csv
import json
import re
import sqlite3
from pathlib import Path

from import_historical_ukie import SOURCE, dateparse, normhead

FIELDS = ['source_race_id', 'race_name', 'off', 'race_type', 'distance', 'race_class', 'source_file']


def original_key(day, course, identifier):
    safe = re.sub('[^a-zA-Z0-9_-]', '', identifier)[:90]
    return SOURCE + ':' + day + ':' + normhead(course) + ':' + safe


def is_festival_gold_cup(row):
    name = row['race_name'].casefold()
    return (normhead(row['course']) == 'cheltenham'
            and row['date'][5:7] == '03'
            and 'cheltenham gold cup' in name
            and 'handicap' not in name
            and ('chase' in name or row['race_type'].strip().casefold() == 'chase'))


def source_records(directory):
    for path in sorted(Path(directory).rglob('*.csv')):
        with path.open('r', encoding='utf-8-sig', errors='replace', newline='') as file:
            reader = csv.DictReader(file)
            if not {'date','course','race_id','race_name'}.issubset(reader.fieldnames or []):
                continue
            for row in reader:
                day = dateparse(row.get('date'))
                course = (row.get('course') or '').strip()
                rid = (row.get('race_id') or '').strip()
                if not day or not course or not rid:
                    continue
                yield original_key(day,course,rid), {
                    'date':day,'course':course, 'source_race_id':rid,
                    'race_name':(row.get('race_name') or '').strip(),
                    'off':(row.get('off') or '').strip(),
                    'race_type':(row.get('type') or '').strip(),
                    'distance':(row.get('dist') or '').strip(),
                    'race_class':(row.get('class') or '').strip(),
                    'source_file':path.name,
                }


def writer(path, header):
    f = path.open('w', encoding='utf-8', newline='')
    w = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
    w.writeheader()
    return f, w


def run(db_path, source_dir, dest):
    out = Path(dest); out.mkdir(parents=True,exist_ok=True)
    titles = {}
    conflict = {}
    source_ids = {}
    id_collisions = set()
    counts = collections.Counter()
    # Store one record per ACTUAL Stage 2 ID. Never dedupe on source race ID alone.
    for key, record in source_records(source_dir):
        counts['source_runner_rows'] += 1
        ident = (record['date'],record['source_race_id'])
        earlier = source_ids.setdefault(ident,(record['course'],record['race_name']))
        if earlier[0] != record['course']:
            id_collisions.add((ident[0],ident[1],earlier[0],record['course']))
        old=titles.get(key)
        if old is None:
            titles[key]=record
        elif (old['race_name'],old['off'],old['course'])!=(record['race_name'],record['off'],record['course']):
            conflict[key]=(old,record)
    counts['unique_source_race_keys']=len(titles)
    counts['source_id_cross_course_collisions']=len(id_collisions)
    counts['conflicting_exact_race_keys']=len(conflict)
    counts['unnamed_source_races']=sum(not v['race_name'] for v in titles.values())

    collision_file,cw=writer(out/'source_id_collisions.csv', ['date','source_race_id','first_course','other_course'])
    with collision_file:
        for d,r,c1,c2 in sorted(id_collisions):cw.writerow({'date':d,'source_race_id':r,'first_course':c1,'other_course':c2})
    ambiguity_file,aw=writer(out/'ambiguous_exact_keys.csv', ['stage2_race_id','first_name','other_name','date','course'])
    with ambiguity_file:
        for k,(a,b) in sorted(conflict.items()):
            aw.writerow({'stage2_race_id':k,'first_name':a['race_name'],'other_name':b['race_name'],'date':a['date'],'course':a['course']})

    con = sqlite3.connect('file:' + str(Path(db_path).resolve()) + '?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    cols={r['name'] for r in con.execute('PRAGMA table_info(historical_races)')}
    if not {'id','race_date','course','race_type','distance_f','race_class'}.issubset(cols):
        raise ValueError('Stage 2 historical_races schema not recognised: '+str(cols))
    valid_ids=set()
    gold=[]
    index_header=['stage2_race_id','date','course','source_race_id','race_name','off','race_type','distance','race_class','source_file','identity_status']
    index_file,iw=writer(out/'named_race_index.csv',index_header)
    with index_file:
        for dbrow in con.execute('SELECT id,race_date,course,race_type,distance_f,race_class FROM historical_races ORDER BY race_date,id'):
            counts['stage2_db_races']+=1
            k=dbrow['id'];v=titles.get(k)
            if not v:
                counts['db_races_without_source_name_record']+=1
                continue
            valid_ids.add(k)
            counts['db_races_exactly_linked']+=1
            if not v['race_name']:counts['db_races_with_blank_names']+=1
            if k in conflict:
                counts['db_races_with_conflicting_metadata']+=1
                status='REVIEW_AMBIGUOUS'
            else:status='EXACT_STAGE2_ID'
            iw.writerow({'stage2_race_id':k,'identity_status':status,**v})
            if is_festival_gold_cup(v):
                gold.append((k,v,dict(dbrow),status))
    counts['named_source_races_outside_stage2']=len(titles)-len(valid_ids)
    counts['gold_cup_editions_in_stage2']=len(gold)
    years=collections.Counter(v['date'][:4] for _,v,_,_ in gold)

    gold_file,gw=writer(out/'cheltenham_gold_cup_verified_links.csv',
        ['year','date','canonical_identity','stage2_race_id','source_race_id','historical_name','off','distance','race_type','race_class','winner','winning_sp_decimal','winner_count','runners','match_status'])
    with gold_file:
        for k,v,r,status in sorted(gold,key=lambda q:q[1]['date']):
            runs=con.execute('SELECT horse_name,finish_pos,sp_decimal FROM historical_runs WHERE race_id=?',(k,)).fetchall()
            winners=[x for x in runs if x['finish_pos']==1]
            gw.writerow({'year':v['date'][:4],'date':v['date'],'canonical_identity':'CHELTENHAM_FESTIVAL_GOLD_CUP',
                'stage2_race_id':k,'source_race_id':v['source_race_id'],'historical_name':v['race_name'],
                'off':v['off'],'distance':v['distance'],'race_type':v['race_type'],'race_class':v['race_class'],
                'winner':' / '.join(x['horse_name'] for x in winners),
                'winning_sp_decimal':' / '.join(str(x['sp_decimal']) for x in winners),
                'winner_count':len(winners),'runners':len(runs),
                'match_status':status if len(winners)==1 else status+';REVIEW_WINNERS'})
            counts['gold_cup_runner_records']+=len(runs)
            if len(winners)!=1:counts['gold_cup_editions_needing_winner_review']+=1
    con.close()
    missing=[y for y in range(1988,2027) if str(y) not in years]
    summary={
      'status':'complete' if len(gold)>=35 and counts['db_races_exactly_linked']>500000 else 'review_required',
      'method':'EXACT importer-generated Stage 2 race ID, including course and date; zero fuzzy joins',
      'counts':dict(counts),
      'gold_cup_years_found':sorted(years),
      'gold_cup_years_absent':missing,
      'gold_cup_2001':'2001 Festival cancellation, external historical validation needed',
      'limitations':[
        'Exact race-to-title linking is not equivalent to assigning recurring event identities.',
        'CHELTENHAM_FESTIVAL_GOLD_CUP is the only named event explicitly canonicalised.',
        'Source race IDs can repeat across courses; a date + ID key alone is invalid.',
        'Stage 2 is read-only; stage2 historical_races table is not changed.',
        'Only years included in the Stage 2 artifact are linkable. Source may include later dates.',
        'Every edition needs validation against independent race history before using named-race trends.'
      ]
    }
    (out/'identity_link_audit.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf8')
    print(json.dumps(summary,indent=2))
    if counts['db_races_exactly_linked']<500000:
        raise SystemExit('Low join coverage; inspect audit and importer key before proceeding.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--out',default='named_race_links')
    p=parser.parse_args()
    run(p.db,p.source,p.out)

if __name__=='__main__':main()
