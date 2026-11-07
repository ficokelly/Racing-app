#!/usr/bin/env python3
"""Read-only inventory of historical race classification fields and source headers."""
import argparse,csv,json,sqlite3,collections,pathlib
p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--source');p.add_argument('--out',required=True);a=p.parse_args()
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
c=sqlite3.connect('file:'+str(pathlib.Path(a.db).resolve())+'?mode=ro',uri=True)
cols={t:[x[1] for x in c.execute('PRAGMA table_info('+t+')')] for t in ('historical_races','historical_runs')}
rows=c.execute('SELECT race_date,course,race_type,race_class,surface,source FROM historical_races').fetchall()
keys=['year','course','race_type','race_class','surface','source'];counts={k:collections.Counter() for k in keys};examples={};years=collections.defaultdict(lambda:collections.Counter())
for date,course,typ,cl,surf,src in rows:
    year=str(date or '')[:4];vals=dict(year=year,course=course,race_type=typ,race_class=cl,surface=surf,source=src)
    for k,v in vals.items():counts[k][str(v).strip() if v is not None and str(v).strip() else '<EMPTY>']+=1
    years[year]['total']+=1
    for k in ('race_type','race_class','surface'):
        if vals[k] is not None and str(vals[k]).strip():years[year][k+'_nonempty']+=1
    key=str(typ).strip() if typ else '<EMPTY>'
    if len(examples.get(key,[]))<5:examples.setdefault(key,[]).append({'date':date,'course':course,'race_type':typ,'race_class':cl,'surface':surf,'source':src})
source_files=[]
if a.source:
    root=pathlib.Path(a.source)
    for f in (root.rglob('*') if root.is_dir() else [root]):
        if not f.is_file() or f.suffix.lower() not in ('.csv','.tsv','.json','.parquet'):continue
        item={'path':str(f.relative_to(root)) if root.is_dir() else f.name,'size_bytes':f.stat().st_size}
        try:
            if f.suffix.lower() in ('.csv','.tsv'):
                with f.open(encoding='utf-8-sig',errors='replace',newline='') as h:
                    reader=csv.reader(h,delimiter='\t' if f.suffix.lower()=='.tsv' else ',');item['header']=next(reader,[])[:120];item['sample_rows']=[dict(zip(item['header'],r)) for _,r in zip(range(2),reader)]
            elif f.suffix.lower()=='.parquet':
                import pyarrow.parquet as pq
                item['header']=pq.read_schema(f).names
            else:
                with f.open(encoding='utf-8-sig',errors='replace') as h: item['sample_start']=h.read(300)
        except Exception as e:item['error']=str(e)[:200]
        source_files.append(item)
report={'total_races':len(rows),'schema':cols,'top_values':{k:v.most_common(35) for k,v in counts.items()},'by_year':dict(sorted(years.items())),'race_type_examples':examples,'source_files':source_files[:100]}
(out/'inventory.json').write_text(json.dumps(report,indent=2,default=str))
with (out/'year_coverage.csv').open('w',newline='') as h:
    w=csv.writer(h);w.writerow(['year','races','race_type_nonempty','race_class_nonempty','surface_nonempty']);w.writerows([[y,v['total'],v['race_type_nonempty'],v['race_class_nonempty'],v['surface_nonempty']] for y,v in sorted(years.items())])
with (out/'race_type_values.csv').open('w',newline='') as h:
    w=csv.writer(h);w.writerow(['race_type','race_count']);w.writerows(counts['race_type'].most_common())
print(json.dumps({'total_races':len(rows),'race_type_top':counts['race_type'].most_common(15),'race_class_top':counts['race_class'].most_common(10),'source_files':len(source_files)},indent=2))
