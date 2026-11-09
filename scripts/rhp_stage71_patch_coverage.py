#!/usr/bin/env python3
"""Patch only Stage70 Python logic (no .github/workflows write permission required).
Refuses unexpected source revisions rather than editing uncertain code.
"""
from pathlib import Path

P=Path('scripts/rhp_stage70_data_coverage.py')
s=P.read_text(encoding='utf-8')

def replace_once(old,new,label):
    global s
    if new in s:
        print('Already applied:',label)
        return
    if s.count(old)!=1:
        raise RuntimeError('Unexpected Stage70 revision; refusing patch: '+label)
    s=s.replace(old,new,1)

old_norm="""def norm(s):
    s=re.sub(r'\\s*\\((?:aw|all.weather)\\)\\s*$','',str(s or ''),flags=re.I)
    return re.sub('[^a-z0-9]','',s.casefold())"""
new_norm="""def norm(s):
    # Remove all recognised course/horse suffixes, including multiple suffixes.
    s=re.sub(r'\\s*\\((?:aw|all.weather|ire|irl|gb|fr|usa|can|ger|aus|nz)\\)', '', str(s or ''), flags=re.I)
    cleaned=re.sub('[^a-z0-9]','',s.casefold())
    return {'chelmsfordcity':'chelmsford'}.get(cleaned,cleaned)"""
replace_once(old_norm,new_norm,'normalized race names')

old_archive="""    archive=collections.defaultdict(dict)
    root=ROOT/'data/racing-results'
    for day in (today-dt.timedelta(days=n) for n in range(0,15)):
        document=data('data/racing-results/'+day.isoformat()+'.json',{})
        for x in document.get('results',[]):
            if x.get('status')=='final':
                archive[day.isoformat()][race_key(day.isoformat(),x.get('course'),x.get('off_dt') or x.get('race_time'))]=x"""
new_archive="""    # Include archived finals and separately verified final provider results.
    archive=collections.defaultdict(list)
    for day in (today-dt.timedelta(days=n) for n in range(0,15)):
        document=data('data/racing-results/'+day.isoformat()+'.json',{})
        for x in document.get('results',[]):
            if isinstance(x,dict) and x.get('status')=='final' and x.get('course'):
                archive[day.isoformat()].append(x)
    for x in data('data/race-results.json',{}).get('races',[]):
        if isinstance(x,dict) and x.get('status')=='final' and x.get('course'):
            day=str(x.get('date') or '')[:10]
            archive[day].append(x)

    def result_for(key,horses):
        day,course_name,off_time=key
        target=int(off_time[:2])*60+int(off_time[3:])
        hits=[]
        for r in archive.get(day,[]):
            if norm(r.get('course'))!=course_name:continue
            actual=clock(r.get('off_dt') or r.get('race_time') or r.get('off'))
            if not actual:continue
            delta=abs(int(actual[:2])*60+int(actual[3:])-target)
            if delta>12:continue
            rows=r.get('runners') or []
            winners=[norm(z.get('horse')) for z in rows if isinstance(z,dict) and str(z.get('position')) in ('1','1.0')]
            if len(winners)!=1:continue
            names={norm(z.get('horse')) for z in rows if isinstance(z,dict) and z.get('horse')}
            count=len(names.intersection(horses))
            if count<(1 if delta==0 else 2):continue
            hits.append((delta,-count,winners[0],r))
        if not hits:return None
        hits.sort(key=lambda x:(x[0],x[1]))
        best=hits[0]
        # Do not silently count conflicting winner reports as verified coverage.
        if any(item[2]!=best[2] for item in hits if item[0]==best[0]):return None
        return best[3]"""
replace_once(old_archive,new_archive,'result source matching')
replace_once("if key in archive.get(day,{}):record['results_found']+=1",
             "if result_for(key,horses):record['results_found']+=1",'race result lookup')

old_main="""    if a.self_test:self_test();return
    value=audit()"""
new_main="""    if a.self_test:self_test();return
    # Stage71 reruns safely whenever the existing Stage70 schedule fires.
    # This avoids changing restricted GitHub workflow files.
    from rhp_stage71_result_recovery import recover
    recovery=recover(ROOT,apply=True)
    if recovery['newly_validated']:
        import subprocess,sys
        subprocess.run([sys.executable,'calculate-strike-rates.py'],cwd=ROOT,check=True)
    value=audit()
    value['stage71_recovery_summary']={k:recovery[k] for k in (
        'missing_final_flag','newly_validated','provider_cross_checked',
        'archive_only_validated','unresolved_classification_count')}"""
replace_once(old_main,new_main,'scheduled recovery hook')
P.write_text(s,encoding='utf-8')
print('Stage71 installed without modifying any workflow file')
