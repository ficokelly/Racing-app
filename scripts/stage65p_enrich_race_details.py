#!/usr/bin/env python3
"""Research-only source CSV enrichment. Preserve original race_type discipline."""
import argparse,csv,json,pathlib,sqlite3,re,collections
p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--source',required=True);p.add_argument('--out',required=True);a=p.parse_args()
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
c=sqlite3.connect(a.db);c.execute('CREATE TABLE IF NOT EXISTS stage65p_race_details (race_id TEXT PRIMARY KEY,race_name TEXT,pattern TEXT,rating_band TEXT,age_band TEXT,sex_rest TEXT,source_file TEXT)')
# Exact imported race ID constructed by importer; race identity is never inferred from date alone.
bykey={}
for rid,date in c.execute('SELECT id,race_date FROM historical_races'):
 bykey[(date,rid.rsplit(':',1)[-1])]=rid
counts=collections.Counter();seen=set();batch=[]
for f in sorted(pathlib.Path(a.source).rglob('*.csv')):
 try:
  with f.open(encoding='utf-8-sig',errors='replace',newline='') as h:
   reader=csv.DictReader(h)
   if not reader.fieldnames or not {'date','race_id','race_name'}.issubset(set(reader.fieldnames)):continue
   for row in reader:
    day=str(row.get('date') or '').strip()
    # Date parser matches imported normalized YYYY-MM-DD; alternate formats are intentionally rejected.
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
     from datetime import datetime
     parsed=None
     for fmt in ('%d/%m/%Y','%Y/%m/%d','%d-%m-%Y'):
      try:parsed=datetime.strptime(day,fmt).date().isoformat();break
      except ValueError:pass
     day=parsed or day
    source_id=re.sub('[^a-zA-Z0-9_-]','',str(row.get('race_id') or ''))[:90]
    rid=bykey.get((day,source_id))
    if not rid:counts['unmatched_rows']+=1;continue
    if rid in seen:continue
    seen.add(rid)
    batch.append((rid,*[(row.get(k) or '').strip() or None for k in ('race_name','pattern','rating_band','age_band','sex_rest')],str(f.relative_to(a.source))))
    if len(batch)>=10000:
     c.executemany('INSERT OR REPLACE INTO stage65p_race_details VALUES (?,?,?,?,?,?,?)',batch);counts['matched_races']+=len(batch);batch.clear()
 except (OSError,UnicodeError) as e:counts['source_errors']+=1;print('Skipping',f,str(e),flush=True)
if batch:c.executemany('INSERT OR REPLACE INTO stage65p_race_details VALUES (?,?,?,?,?,?,?)',batch);counts['matched_races']+=len(batch)
c.commit()
for yr,typ,n in c.execute('''SELECT substr(r.race_date,1,4),r.race_type,count(*) FROM historical_races r JOIN stage65p_race_details d ON r.id=d.race_id GROUP BY 1,2'''):
 counts['year_'+yr+'_'+str(typ)]=n
counts['database_races']=c.execute('SELECT count(*) FROM historical_races').fetchone()[0]
counts['enriched_races']=c.execute('SELECT count(*) FROM stage65p_race_details').fetchone()[0]
counts['nonempty_race_names']=c.execute("SELECT count(*) FROM stage65p_race_details WHERE race_name IS NOT NULL").fetchone()[0]
(out/'enrichment_audit.json').write_text(json.dumps(dict(counts),indent=2)+'\n')
print(json.dumps({k:counts[k] for k in ('database_races','enriched_races','nonempty_race_names','unmatched_rows')},indent=2),flush=True)
if counts['enriched_races']<100000:raise RuntimeError('Too few exact race matches: refuse C training')
c.close()
