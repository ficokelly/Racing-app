#!/usr/bin/env python3
"""Stage65J: compare original Model D history with recovered Stage65E source. READ ONLY.
No training, merging, backfill or live data writes. Differences are leads, not proof of missing races.
"""
import argparse, collections, csv, datetime as dt, hashlib, json, pathlib, re, sqlite3

def norm(x):
    return re.sub(r'[^a-z0-9]','',re.sub(r'\([^)]*\)','',str(x or '').casefold()))

def connect(path):
    p=pathlib.Path(path).resolve()
    if not p.is_file():raise RuntimeError('Missing source: '+str(p))
    c=sqlite3.connect(f'file:{p}?mode=ro',uri=True)
    c.execute('pragma query_only=ON')
    if c.execute('pragma quick_check').fetchone()[0][0:2]!='ok':raise RuntimeError('Integrity check failed: '+str(p))
    return c

def rows(c,sql,params=()):return c.execute(sql,params)

def columns(c,table):return {r[1] for r in c.execute('pragma table_info("'+table+'")')}

def month_rollup(c,kind,start,end):
    if kind=='live':
        sql='''select substr(r.race_date,1,7), count(distinct r.id),count(*),
        sum(case when u.official_rating is not null then 1 else 0 end),
        sum(case when u.sp_decimal is not null then 1 else 0 end)
        from historical_races r join historical_runs u on u.race_id=r.id
        where r.race_date between ? and ? group by substr(r.race_date,1,7)'''
    else:
        sql='''select substr(race_date,1,7),count(distinct race_date||'|'||course||'|'||race_id),count(*),
        sum(case when trim(coalesce(official_rating,''))<>'' then 1 else 0 end),
        sum(case when trim(coalesce(sp,''))<>'' then 1 else 0 end)
        from historical_runs where race_date between ? and ? and length(race_date)=10
        group by substr(race_date,1,7)'''
    return {str(a):[int(v or 0) for v in (b,c,d,e)] for a,b,c,d,e in rows(c,sql,(start,end))}

def recent_races(c,kind,start,end):
    if kind=='live':
        sql='''select r.race_date,r.course,cast(r.id as text),u.horse_name,
        u.finish_pos,u.sp_decimal from historical_races r
        join historical_runs u on u.race_id=r.id where r.race_date between ? and ?
        order by r.race_date,r.course,r.id'''
    else:
        sql='''select race_date,course,cast(race_id as text),horse,finish,sp
        from historical_runs where race_date between ? and ? and length(race_date)=10
        order by race_date,course,race_id'''
    out=collections.defaultdict(lambda: {'horses':set(),'priced':0})
    for day,course,rid,horse,finish,sp in rows(c,sql,(start,end)):
        if not day or not course or not rid:continue
        key=(day,norm(course),str(rid));item=out[key]
        if horse:item['horses'].add(norm(horse))
        if sp is not None and str(sp).strip():item['priced']+=1
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--live-db',required=True);p.add_argument('--recovered-db',required=True)
    p.add_argument('--out',default='reports/stage65j');p.add_argument('--start',default='2015-01-01')
    p.add_argument('--end',default='2026-11-06');p.add_argument('--recent-start',default='2026-01-01')
    a=p.parse_args();out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    live=connect(a.live_db);rec=connect(a.recovered_db)
    req_live_r={'id','race_date','course'};req_live_u={'race_id','horse_name','finish_pos','sp_decimal','official_rating'}
    req_rec={'race_date','course','race_id','horse','finish','sp','official_rating','rpr','ts'}
    if not req_live_r<=columns(live,'historical_races') or not req_live_u<=columns(live,'historical_runs'):
        raise RuntimeError('Original Model D database schema does not match Stage38 reader')
    if not req_rec<=columns(rec,'historical_runs'):raise RuntimeError('Recovered archive schema mismatch')
    # Model D does not query RPR or Topspeed in Stage38: report these as supplementary, not proven model blockers.
    original=month_rollup(live,'live',a.start,a.end);recovered=month_rollup(rec,'recovered',a.start,a.end)
    months=sorted(set(original)|set(recovered));monthly=[]
    for m in months:
        l=original.get(m,[0]*4);r=recovered.get(m,[0]*4)
        monthly.append(dict(month=m,live_races=l[0],recovered_races=r[0],race_difference=r[0]-l[0],live_runners=l[1],recovered_runners=r[1],runner_difference=r[1]-l[1],live_official_rating_pct=round(100*l[2]/l[1],2) if l[1] else None,recovered_official_rating_pct=round(100*r[2]/r[1],2) if r[1] else None,live_sp_pct=round(100*l[3]/l[1],2) if l[1] else None,recovered_sp_pct=round(100*r[3]/r[1],2) if r[1] else None))
    with (out/'monthly_live_vs_recovered.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(monthly[0]) if monthly else ['month']);w.writeheader();w.writerows(monthly)
    l=recent_races(live,'live',a.recent_start,a.end);r=recent_races(rec,'recovered',a.recent_start,a.end)
    # Match by date/course and *exact* set of runner names; race IDs are source-specific.
    li=collections.defaultdict(list);ri=collections.defaultdict(list)
    for key,val in l.items():li[key[:2]].append((key,val))
    for key,val in r.items():ri[key[:2]].append((key,val))
    details=[];states=collections.Counter()
    for day_course in sorted(set(li)|set(ri)):
        ll=li.get(day_course,[]);rr=ri.get(day_course,[])
        for lk,lv in ll:
            matches=[(rk,rv) for rk,rv in rr if lv['horses'] and lv['horses']==rv['horses']]
            if len(matches)==1:status='EXACT_RUNNER_SET_MATCH'
            elif len(matches)>1:status='AMBIGUOUS_EXACT_MATCH'
            elif not rr:status='LIVE_ONLY_COURSE_DAY'
            else:status='NO_EXACT_RUNNER_SET_MATCH'
            states[status]+=1
            details.append(dict(date=lk[0],course_normalized=lk[1],live_race_id=lk[2],live_horses=len(lv['horses']),recovered_course_day_races=len(rr),status=status,recovered_race_id=matches[0][0][2] if len(matches)==1 else ''))
        if not ll:
            for rk,rv in rr:
                states['RECOVERED_ONLY_COURSE_DAY']+=1
                details.append(dict(date=rk[0],course_normalized=rk[1],live_race_id='',live_horses=0,recovered_course_day_races=len(rr),status='RECOVERED_ONLY_COURSE_DAY',recovered_race_id=rk[2]))
    with (out/'recent_race_identity_comparison.csv').open('w',newline='') as f:
        fields=['date','course_normalized','live_race_id','live_horses','recovered_course_day_races','status','recovered_race_id'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(details)
    cfg=pathlib.Path('reports/stage30/stage30-summary.json');code=pathlib.Path('scripts/stage38_model_dz_live.py')
    config=json.loads(cfg.read_text()) if cfg.is_file() else {}
    model=config.get('model_results',{}).get('D',{})
    if model.get('chosen_combination')!='D-064-gap14' or abs(float(model.get('selection_margin',-1))-.14)>1e-8:
        raise RuntimeError('Frozen Model D config has changed: audit halted')
    summary={'status':'RESEARCH_ONLY_NO_LIVE_CHANGES','model_d_frozen_combination':model['chosen_combination'],
        'model_d_selection_margin':model['selection_margin'],
        'stage38_sha256':hashlib.sha256(code.read_bytes()).hexdigest() if code.is_file() else None,
        'stage30_config_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest(),
        'live_source':'original Stage2 historical staging SQLite (Stage38-compatible)',
        'recovered_source':'Stage65E detailed historical SQLite',
        'start':a.start,'end':a.end,'recent_start':a.recent_start,
        'months_compared':len(months),'recent_live_races':len(l),'recent_recovered_races':len(r),
        'recent_match_statuses':dict(states),
        'last_live_date':max((x for x in original),default=None),
        'last_recovered_month':max((x for x in recovered),default=None),
        'model_d_requires_rpr_ts':'NOT_IN_STAGE38_HISTORICAL_SQL_QUERY',
        'limitations':['Source race IDs are NOT assumed equivalent','Runner name sets can differ because of non-runners, formatting, or duplicate source rows',
        'Monthly counts do not independently certify that every UK/IE race is present',
        'This compares original Stage2 artifact, not necessarily the exact database used in the latest production run',
        'No training, model scoring, updates, file publishing, or data promotion is performed']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True);live.close();rec.close()

if __name__=='__main__':main()
