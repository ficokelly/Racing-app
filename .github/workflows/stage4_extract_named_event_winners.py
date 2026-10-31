#!/usr/bin/env python3
"""Stage 4: Link prioritized named annual event editions to source DB winners.

Runner names, SP and finishes are sourced from Stage2; NOT externally verified.
Only CURATED_CANDIDATE, DOCUMENTED_EVENT_CANDIDATE and
REFERENCE_SERIES_CANDIDATE are included. No model training or betting tests.
"""
import argparse,csv,json,sqlite3,collections, pathlib

def main():
 a=argparse.ArgumentParser();a.add_argument('--db',required=True);a.add_argument('--mapping',required=True);a.add_argument('--out',default='stage4_winner_identity_check');o=a.parse_args()
 out=pathlib.Path(o.out);out.mkdir(parents=True,exist_ok=True)
 con=sqlite3.connect('file:'+pathlib.Path(o.db).resolve().as_posix()+'?mode=ro',uri=True)
 con.row_factory=sqlite3.Row
 cols={r[1] for r in con.execute('PRAGMA table_info(historical_runs)')}
 required={'race_id','horse_name','finish_pos','sp_decimal'}
 if required-cols:raise SystemExit('Stage 2 database is missing columns '+str(required-cols))
 sql='SELECT horse_name,finish_pos,sp_decimal FROM historical_runs WHERE race_id=?'
 fields=['event_id','edition_year','actual_date','course','original_race_name','stage2_race_id','match_status','winner_from_database','winning_sp_decimal','runner_records','winners_with_position_1','identity_check','external_winner_verified','race_identity_evidence']
 output=out/'named_event_winner_audit.csv'
 count=collections.Counter(); anomaly=[]; coverage=collections.defaultdict(lambda:collections.Counter())
 with open(output,'w',newline='',encoding='utf-8') as dest,open(o.mapping,encoding='utf-8-sig',newline='') as src:
  w=csv.DictWriter(dest,fieldnames=fields);w.writeheader()
  for r in csv.DictReader(src):
   if r['match_status'] not in ('REFERENCE_SERIES_CANDIDATE','CURATED_CANDIDATE','DOCUMENTED_EVENT_CANDIDATE'):continue
   horse=list(con.execute(sql,(r['stage2_race_id'],)))
   winners=[x for x in horse if x['finish_pos']==1]
   state='ONE_WINNER' if len(winners)==1 else ('MISSING_RACE' if not horse else 'ZERO_WINNERS' if not winners else 'MULTIPLE_WINNERS')
   winner=winners[0] if len(winners)==1 else None
   row={'event_id':r['event_id'],'edition_year':r['edition_year'],'actual_date':r['date'],'course':r['course'],'original_race_name':r['race_name'],'stage2_race_id':r['stage2_race_id'],'match_status':r['match_status'],'winner_from_database':winner['horse_name'] if winner else '', 'winning_sp_decimal':winner['sp_decimal'] if winner else '', 'runner_records':len(horse),'winners_with_position_1':len(winners),'identity_check':state,'external_winner_verified':'NO','race_identity_evidence':r['evidence']}
   w.writerow(row);count[state]+=1
   if state!='ONE_WINNER':anomaly.append(row)
   coverage[r['event_id']]['editions']+=1;coverage[r['event_id']]['linked_winners']+=int(state=='ONE_WINNER')
 with open(out/'winner_anomalies.csv','w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(anomaly)
 with open(out/'series_runner_coverage.csv','w',newline='',encoding='utf-8') as f:
  w=csv.writer(f);w.writerow(['event_id','candidate_editions','editions_with_one_database_winner'])
  for key,v in sorted(coverage.items()):w.writerow([key,v['editions'],v['linked_winners']])
 report={'status':'COMPLETE','candidate_editions_checked':sum(count.values()),'source_winner_status':dict(count),'candidate_series':len(coverage),'limitation':'Winner extraction verifies source table linkage and internal finishing data only; it does NOT independently authenticate winner or recurring-event identity against published historical records.'}
 (out/'winner_audit.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
