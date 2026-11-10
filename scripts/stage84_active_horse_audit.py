#!/usr/bin/env python3
"""Stage 84: active UK/Irish horse data coverage audit for research Model E.

Read-only, odds-free, model-free. Does not edit the historical DB or app.
The active cohort is a proxy: horses declared on recent racecards, NOT a
complete register of all horses currently in training.

A blank prior form for a genuine debutant is not a gap. A novice is not a
presumed debutant; past runs in ANY discipline or category are assessed.
Historical source identity matches MUST be ID- or pedigree-supported before
using a record to assert high-confidence coverage. Name-only = provisional.

Limitations: public racecard 'form' provides a lower bound on recent past runs,
NOT an independently verified lifetime number of starts. A satisfactory lower
bound therefore does NOT prove complete career-history coverage.
"""
from __future__ import annotations
import argparse, collections, csv, datetime as dt, json, math, pathlib, re, sqlite3, sys, tempfile, unicodedata

FIELDS = ('horse_identity', 'trainer', 'jockey', 'course', 'distance', 'going',
          'weight', 'age', 'sex', 'sire', 'dam', 'official_rating_handicaps',
          'race_category', 'form_when_previously_raced', 'last_run_when_previously_raced')
HISTORY_FIELDS = ('finish_pos', 'trainer', 'jockey', 'official_rating',
                  'weight_lbs', 'beaten', 'rpr', 'topspeed', 'race_comment',
                  'sire', 'dam', 'sp_decimal', 'age', 'sex')
NON_RUNNER = {'nr','nonrunner','non-runner','scratched','withdrawn','abandoned','did not start','dns'}


def norm(v):
    s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().upper()
    s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|ITY|JPN)\)\s*$','',s)
    return re.sub(r'[^A-Z0-9]','',s)


def text(v):return str(v or '').strip()
def valid(v):return v is not None and text(v).lower() not in ('','-','none','null','unknown','n/a','na','tba')
def num(v):
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None


def read_json(path,default=None):
    p=pathlib.Path(path)
    if not p.exists():return default
    with p.open('r',encoding='utf-8') as f:return json.load(f)


def is_runner(row):
    return isinstance(row,dict) and bool(row.get('horse')) and bool(row.get('course')) and bool(row.get('date') or row.get('race_time'))


def discover_cards(obj, inherited_date='', depth=0):
    if depth>10:return
    if isinstance(obj,list):
        for z in obj:yield from discover_cards(z,inherited_date,depth+1)
    elif isinstance(obj,dict):
        if is_runner(obj):
            item=dict(obj)
            item['date']=text(item.get('date') or item.get('race_time'))[:10] or inherited_date
            yield item
            return
        if obj.get('runners') and obj.get('course') and (obj.get('date') or obj.get('race_time')):
            for z in obj['runners']:
                if isinstance(z,dict) and z.get('horse'):
                    item={k:v for k,v in obj.items() if k!='runners'}
                    item.update(z)
                    item['date']=text(item.get('date') or item.get('race_time'))[:10]
                    yield item
            return
        for k,v in obj.items():
            d=k[:10] if re.match(r'^20\d\d-\d\d-\d\d$',k[:10]) else inherited_date
            if isinstance(v,(list,dict)):yield from discover_cards(v,d,depth+1)


def form_marks(form):
    """Count indicated previous starts in compact last-form figures.

    Form is NOT a complete career ledger. A dash may divide seasons; a 0 means
    unplaced. Ignore old form artifacts such as '/'. Unknown notation is tracked.
    """
    s=text(form).upper().replace(' ','')
    if not s or s in ('-', 'DEBUT','NEW') :return 0
    return sum(ch in '0123456789FPU RBS' for ch in s)


def handicap(card):
    name=(text(card.get('race_name'))+' '+text(card.get('race_type'))).lower()
    return ('handicap' in name or 'nursery' in name or bool(re.search(r'\bhcap\b',name)))


def is_jumps(card):
    s=(' '.join(text(card.get(k)) for k in ('type','race_type','race_name'))).lower()
    return any(w in s for w in ('hurdle','chase','steeple','bumper','national hunt flat','nh flat','nhflat'))


def live_eligible(k,card):
    had_previous=form_marks(card.get('form'))>0 or (num(card.get('last_run')) is not None and num(card.get('last_run'))>=0)
    if k=='horse_identity':return True, bool(card.get('horse_id'))
    if k in ('trainer','jockey','course','going','sire','dam','age','sex'):return True,valid(card.get(k))
    if k=='distance':return True,(num(card.get('distance_f')) or 0)>0
    if k=='weight':return True,(num(card.get('lbs') or card.get('weight_lbs')) or 0)>0
    if k=='official_rating_handicaps':
        if not handicap(card):return False,None
        return True,(num(card.get('ofr') or card.get('official_rating') or card.get('or')) or 0)>0
    if k=='race_category':return True,valid(card.get('race_name') or card.get('race_type'))
    if k=='form_when_previously_raced':return had_previous,form_marks(card.get('form'))>0
    if k=='last_run_when_previously_raced':return had_previous,num(card.get('last_run')) is not None
    raise KeyError(k)


def iso(s):
    try:return dt.date.fromisoformat(str(s)[:10])
    except (ValueError,TypeError):return None


def recents_for_horses(dates, recent_dir, validated_results):
    """Read independent recent result archives; only final actual starters.

    Use race+horse key for deduplication (archives can overlap). The date
    prevents using the future result of today's race as historical form.
    """
    seen=set();output=collections.defaultdict(list)
    files=sorted(pathlib.Path(recent_dir).glob('*.json')) if pathlib.Path(recent_dir).exists() else []
    minimum=min(dates)-dt.timedelta(days=365)
    for p in files+[pathlib.Path(validated_results)]:
        try:data=read_json(p,{})
        except (ValueError,OSError):continue
        if not isinstance(data,dict):continue
        races=data.get('results') or data.get('races') or []
        for race in races:
            day=iso(race.get('date') or race.get('off_dt'))
            if day is None or day<minimum or day>max(dates):continue
            if text(race.get('status')).lower()!='final':continue
            course=text(race.get('course'))
            # Always retain actual course+time+date to keep distinct races separate.
            time=text(race.get('off_dt') or race.get('race_time') or race.get('off'))
            off=time[11:16] if 'T' in time else time
            rid=text(race.get('race_id'))
            for horse in race.get('runners',[]):
                if not isinstance(horse,dict):continue
                status=text(horse.get('status')).lower()
                if status in NON_RUNNER:continue
                horse_name=text(horse.get('horse'))
                if not horse_name:continue
                key=(str(day),norm(course),rid or off,norm(horse_name))
                if key in seen:continue
                seen.add(key)
                entry={'race_date':str(day),'course':course,'race_id':rid or str(day)+'|'+norm(course)+'|'+off,
                       'horse_name_norm':norm(horse_name),'horse_source_id':text(horse.get('horse_id')),
                       'horse_country':'','sire':text(horse.get('sire')),'dam':text(horse.get('dam')),
                       'finish_pos':num(horse.get('position')),'outcome':status,
                       'trainer':text(horse.get('trainer')),'jockey':text(horse.get('jockey')),
                       'official_rating':num(horse.get('or')),'weight_lbs':num(horse.get('weight_lbs')),
                       'beaten':None,'rpr':None,'topspeed':None,'race_comment':None,
                       'sp_decimal':None,'age':num(horse.get('age')),'sex':text(horse.get('sex')),
                       'race_type':text(race.get('type')),'surface':text(race.get('surface')),
                       'source':'verified_daily_result'}
                output[entry['horse_name_norm']].append(entry)
    return output


def historical_by_name(db,names,latest):
    conn=sqlite3.connect('file:'+str(pathlib.Path(db).resolve())+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    needed={z[1] for z in conn.execute('PRAGMA table_info(historical_runs)')}
    cols={z[1] for z in conn.execute('PRAGMA table_info(historical_races)')}
    must={'horse_name_norm','horse_source_id','horse_country','sire','dam','finish_pos','outcome'}
    if not must<=needed or not {'id','race_date','course'}<=cols:
        raise RuntimeError('Historical schema unexpected; refusing to count uncertain records')
    attributes=['horse_name_norm','horse_source_id','horse_country','sire','dam','finish_pos','outcome',
                'trainer','jockey','official_rating','weight_lbs','beaten','rpr','topspeed','race_comment','sp_decimal','age','sex']
    select=[]
    for k in attributes:
        select.append(('u.'+k) if k in needed else ('NULL AS '+k))
    select += ['r.race_date','r.course','r.id AS race_id',
               'r.race_type' if 'race_type' in cols else 'NULL AS race_type',
               'r.surface' if 'surface' in cols else 'NULL AS surface']
    data=collections.defaultdict(list)
    unique=sorted(set(names))
    for i in range(0,len(unique),350):
        sub=unique[i:i+350]
        sql=('SELECT '+','.join(select)+' FROM historical_runs u JOIN historical_races r ON r.id=u.race_id '
             'WHERE u.horse_name_norm IN ('+','.join('?' for _ in sub)+') AND r.race_date < ? ORDER BY r.race_date')
        for row in conn.execute(sql,[*sub,str(latest+dt.timedelta(days=8))]):
            z=dict(row)
            if text(z.get('outcome')).lower() in NON_RUNNER:continue
            z['source']='historical_archive';data[z['horse_name_norm']].append(z)
    max_day=conn.execute('SELECT MAX(race_date) FROM historical_races').fetchone()[0]
    n_races=conn.execute('SELECT COUNT(*) FROM historical_races').fetchone()[0]
    conn.close()
    return data,{'source_max_date':max_day,'historical_races':n_races}


def choose_identity(card,rows):
    """Returns supported matches, provisional matches and match reason.

    Current Racing API horse_id generally differs from the imported source ID;
    do not consider a name-only join verified just because it is unique.
    """
    current_id=text(card.get('horse_id'))
    sire=norm(card.get('sire'));dam=norm(card.get('dam'))
    country=norm(card.get('horse_country') or card.get('country'))
    ids=[z for z in rows if current_id and text(z.get('horse_source_id'))==current_id]
    pedigree=[z for z in rows if sire and dam and norm(z.get('sire'))==sire and norm(z.get('dam'))==dam
              and (not country or not z.get('horse_country') or norm(z.get('horse_country'))==country)]
    if ids:return dedupe(ids+pedigree),[], 'EXACT_HORSE_ID'
    if pedigree:return pedigree,[], 'SIRE_AND_DAM_MATCH'
    # A mismatch of BOTH known sire and dam is suspicious even if the name matches.
    possible=[z for z in rows if not country or not z.get('horse_country') or norm(z.get('horse_country'))==country]
    return [],possible,('NAME_ONLY_PROVISIONAL' if possible else 'NO_PRIOR_MATCH')


def dedupe(rows):
    out={}
    for r in rows:
        # A horse has a single actual run per course/date. Source race IDs vary
        # between Kaggle and the Racing API, so do not count the same run twice.
        k=(str(r.get('race_date'))[:10],norm(r.get('course')))
        if k not in out or (out[k].get('source')=='verified_daily_result' and r.get('source')=='historical_archive'):
            out[k]=r
    return sorted(out.values(),key=lambda x:(text(x.get('race_date')),text(x.get('race_id'))))


def audit(cards,db,recent_dir,verified_path,asof,lookback,allow_upcoming=7):
    minimum=asof-dt.timedelta(days=lookback)
    maximum=asof+dt.timedelta(days=allow_upcoming)
    candidates=[x for x in cards if (iso(x.get('date')) or dt.date(1900,1,1))>=minimum and
                (iso(x.get('date')) or dt.date(9999,1,1))<=maximum and x.get('horse') and x.get('course')]
    # Distinct active horse IDs; keep most recent declaration for each horse.
    latest={}
    for x in candidates:
        key=text(x.get('horse_id')) or ('NAME|'+norm(x.get('horse'))+'|'+norm(x.get('sire'))+'|'+norm(x.get('dam')))
        prior=latest.get(key)
        if prior is None or text(x.get('date'))>text(prior.get('date')):latest[key]=x
    cards=list(latest.values())
    if not cards:raise RuntimeError('No eligible active declarations found; no coverage claims are possible')
    hist,dbinfo=historical_by_name(db,[norm(z.get('horse')) for z in cards],maximum)
    recent=recents_for_horses([minimum,asof],recent_dir,verified_path)
    live_fields=collections.defaultdict(lambda:{'applicable':0,'present':0,'not_applicable':0})
    historic_fields=collections.defaultdict(lambda:{'applicable':0,'present':0})
    per_horse=[]
    status=collections.Counter();by_type=collections.defaultdict(collections.Counter)
    for card in cards:
        name=norm(card['horse']);day=iso(card.get('date')) or asof
        older=[z for z in hist.get(name,[]) if iso(z.get('race_date')) and iso(z.get('race_date'))<day]
        fresh=[z for z in recent.get(name,[]) if iso(z.get('race_date')) and iso(z.get('race_date'))<day]
        strong_h,weak_h,identity_h=choose_identity(card,older)
        strong_r,weak_r,identity_r=choose_identity(card,fresh)
        strong=dedupe(strong_h+strong_r)
        weak=dedupe(weak_h+weak_r)
        identity='EXACT_HORSE_ID' if 'EXACT_HORSE_ID' in (identity_h,identity_r) else (
            'SIRE_AND_DAM_MATCH' if 'SIRE_AND_DAM_MATCH' in (identity_h,identity_r) else (
            'NAME_ONLY_PROVISIONAL' if weak else 'NO_PRIOR_MATCH'))
        # Never promote a name-only group to strong evidence automatically.
        n_form=form_marks(card.get('form'))
        last_days=num(card.get('last_run'))
        has_previous=n_form>0 or last_days is not None
        if has_previous and not strong:
            coverage='EXPECTED_PRIOR_RUNS_NOT_CONFIRMED'
        elif not has_previous and not strong:
            coverage='NO_PRIOR_RUNS_REPORTED_UNVERIFIED'
        elif has_previous:
            coverage='MATCHED_HISTORY_REQUIRES_RECENCY_CHECK'
        else:
            coverage='MATCHED_HISTORY_CARD_FORM_NOT_AVAILABLE'
        recency='NOT_APPLICABLE'
        if last_days is not None and strong:
            last=max(iso(z.get('race_date')) for z in strong)
            actual=(day-last).days
            recency=('ALIGNED' if abs(actual-last_days)<=3 else 'DISCREPANCY')
        if has_previous and strong and n_form>0:
            coverage=('RECENT_HISTORY_SUPPORTED_NOT_FULL_CAREER_PROOF'
                      if len(strong)>=n_form and recency!='DISCREPANCY'
                      else 'POSSIBLE_MISSING_RECENT_RUNS')
        if has_previous and strong and recency=='DISCREPANCY':
            coverage='LAST_RUN_MISMATCH_POSSIBLE_GAP'
        for k in FIELDS:
            applies,has=live_eligible(k,card)
            # Once we confirm older starts, missing racecard form is a real gap
            # even if the card itself has both form and last_run blank.
            if k in ('form_when_previously_raced','last_run_when_previously_raced') and strong:
                applies=True
            if not applies:live_fields[k]['not_applicable']+=1
            else:
                live_fields[k]['applicable']+=1
                live_fields[k]['present']+=int(bool(has))
        for run in strong:
            for k in HISTORY_FIELDS:
                applies=True
                outcome=text(run.get('outcome')).lower()
                pos=num(run.get('finish_pos'))
                unfinished=outcome in ('pu','pulled up','fell','f','ur','unseated','unseated rider','refused','bd','brought down','rr','ran out')
                # A non-finisher legitimately has no finishing position or
                # beaten-length distance; exclude those denominators.
                if k in ('finish_pos','beaten') and unfinished:applies=False
                if k=='beaten' and pos==1:applies=False
                if k=='official_rating' and 'handicap' not in text(run.get('race_type')).lower():applies=False
                if not applies:continue
                historic_fields[k]['applicable']+=1
                if k=='finish_pos':have=num(run.get(k)) is not None and num(run.get(k))>0
                elif k in ('official_rating','weight_lbs','sp_decimal','rpr','topspeed','age'):have=(num(run.get(k)) or 0)>0
                else:have=valid(run.get(k))
                historic_fields[k]['present']+=int(have)
        kind='HURDLES_OR_CHASES' if is_jumps(card) else ('ALL_WEATHER' if 'aw' in text(card.get('surface')).lower() or '(aw)' in text(card.get('course')).lower() else 'FLAT')
        category='HANDICAP' if handicap(card) else 'NON_HANDICAP'
        status[coverage]+=1;by_type[kind][coverage]+=1
        per_horse.append({'horse':card.get('horse'),'horse_id':text(card.get('horse_id')),
            'latest_declared_date':str(day),'course':card.get('course'),'race_name':card.get('race_name'),
            'type':kind,'category':category,'form':text(card.get('form')),
            'last_run_field':text(card.get('last_run')),'form_min_prior_starts':n_form,
            'prior_run_evidence':has_previous,'identity':identity,'matched_prior_runs':len(strong),
            'possible_name_only_runs':len(weak),'last_matched_date':text(strong[-1].get('race_date')) if strong else '',
            'last_run_reconciliation':recency,'coverage_status':coverage,
            'strong_match_sources':';'.join(sorted(set(text(z.get('source')) for z in strong))),
            'historical_lifetime_completeness_verified':False})
    def pct(n,d):return round(100*n/d,2) if d else None
    fieldrows=[]
    for k in FIELDS:
        f=live_fields[k]
        fieldrows.append({'group':'current_card','feature':k,**f,'coverage_pct':pct(f['present'],f['applicable']),
                          'threshold_pct':90,'meets_90_if_applicable':f['present']/f['applicable']>=.9 if f['applicable'] else None})
    for k in HISTORY_FIELDS:
        f=historic_fields[k]
        fieldrows.append({'group':'confirmed_historical_runs','feature':k,**f,
                          'not_applicable':0,'coverage_pct':pct(f['present'],f['applicable']),
                          'threshold_pct':90,'meets_90_if_applicable':f['present']/f['applicable']>=.9 if f['applicable'] else None})
    observed=sum(z['prior_run_evidence'] for z in per_horse)
    supported=sum(z['coverage_status']=='RECENT_HISTORY_SUPPORTED_NOT_FULL_CAREER_PROOF' for z in per_horse)
    unknown=sum(z['coverage_status']=='NO_PRIOR_RUNS_REPORTED_UNVERIFIED' for z in per_horse)
    summary={'stage':84,'model':'E_RESEARCH_ONLY','as_of':str(asof),'cohort_window_days':lookback,
       'active_cohort_definition':'Unique horses declared in available racecard snapshots during lookback window; not all horses physically in training',
       'active_horses_in_cohort':len(per_horse),'horses_with_prior_run_evidence':observed,
       'horses_without_prior_run_evidence_not_assumed_debutants':unknown,
       'horses_with_recent_form_supported_but_full_career_unverified':supported,
       'horse_coverage_status_counts':dict(status),'race_type_coverage':{k:dict(v) for k,v in by_type.items()},
       'source_baseline':dbinfo,'current_field_coverage':{r['feature']:r['coverage_pct'] for r in fieldrows if r['group']=='current_card'},
       'historical_field_coverage_on_strong_matches':{r['feature']:r['coverage_pct'] for r in fieldrows if r['group']=='confirmed_historical_runs'},
       'lifetime_90pct_verified':False,
       'lifetime_90pct_verification_blocker':'No independent official per-horse career start ledger is available in this input; form strings only indicate recent runs. Do not claim complete career coverage.',
       'rules':['Missing previous handicap starts is NOT a gap','Novice status is NOT equivalent to debutant',
                'Previous runs in any race category/discipline count toward past form',
                'Only applicable numerical ORs are assessed in current handicaps',
                'A missing race seen in a horse form string IS a gap unless reconciled',
                'Blank form/last_run does NOT prove true debut; classify unverified instead',
                'Name-only historical matches never count as confirmed identity',
                'No bookmaker odds or SP are used as predictive features',
                'Source reports include SP completeness solely as a data audit']}
    return summary,per_horse,fieldrows


def write_csv(path,rows,fields):
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def render_report(summary,fields):
    lines=['# Model E — Stage 84 active-horse data audit','',
         f"As of **{summary['as_of']}**; **{summary['active_horses_in_cohort']}** unique declared horses.",
         '', '## Scope','', summary['active_cohort_definition'], '',
         '**Important:** This is not a register of every horse currently in training. '
         'Nor can it certify complete lifetime records without an independent career-start ledger.', '',
         '## Horse history','',
         f"- Horses with evidence of at least one prior run: {summary['horses_with_prior_run_evidence']}",
         f"- Recent form supported (not full career certified): {summary['horses_with_recent_form_supported_but_full_career_unverified']}",
         f"- No prior runs reported / debut unverified: {summary['horses_without_prior_run_evidence_not_assumed_debutants']}",
         '', '### Reconciliation status','']
    for k,n in sorted(summary['horse_coverage_status_counts'].items()):lines.append(f'- {k}: **{n}**')
    lines+=['','## Applicable data-field coverage','', '| Dataset | Field | Applicable | Present | Coverage | ≥90% |','|---|---|---:|---:|---:|---|']
    for r in fields:
        cov='N/A' if r['coverage_pct'] is None else f"{r['coverage_pct']:.2f}%"
        passed='N/A' if r['meets_90_if_applicable'] is None else ('Yes' if r['meets_90_if_applicable'] else 'No')
        lines.append(f"| {r['group']} | {r['feature']} | {r['applicable']} | {r['present']} | {cov} | {passed} |")
    lines+=['','## Model E safeguards','',
            '- No historical result after the race being audited is allowed to support its pre-race form.',
            '- No missing class, OR, RPR, speed rating, or beaten distance is fabricated.',
            '- Do not penalise a horse for never having raced in today\'s category.',
            '- First-time handicap starters keep all prior maiden/novice races in the form history.',
            '- No bookmaker odds are model inputs.',
            '- Frozen D and D(c) files are never edited by Stage 84.', '']
    return '\n'.join(lines)


def self_test():
    assert form_marks('PP343-')==5
    assert form_marks('32-1F')==4
    assert form_marks('-')==0
    assert handicap({'race_name':'Novices Handicap Hurdle'})
    assert not handicap({'race_name':'Novices Hurdle'})
    assert live_eligible('official_rating_handicaps',{'race_name':'Novice Hurdle','ofr':'-'})==(False,None)
    assert live_eligible('official_rating_handicaps',{'race_name':'Handicap Hurdle','ofr':'123'})==(True,True)
    a=[{'horse_name_norm':'FOO','horse_source_id':'xxx','sire':'Sire','dam':'Dam'}]
    assert choose_identity({'horse_id':'current','sire':'Sire','dam':'Dam'},a)[2]=='SIRE_AND_DAM_MATCH'
    assert choose_identity({'horse_id':'current','sire':'Different','dam':'Dam'},a)[2]=='NAME_ONLY_PROVISIONAL'
    with tempfile.TemporaryDirectory() as tmp:
        db=pathlib.Path(tmp)/'t.sqlite';co=sqlite3.connect(db)
        co.executescript('''CREATE TABLE historical_races(id TEXT, race_date TEXT, course TEXT, race_type TEXT, surface TEXT);
        CREATE TABLE historical_runs(race_id TEXT, horse_name_norm TEXT, horse_source_id TEXT, horse_country TEXT,
         sire TEXT,dam TEXT,finish_pos INT,outcome TEXT,trainer TEXT,jockey TEXT,official_rating REAL, weight_lbs REAL,
         beaten TEXT, rpr REAL, topspeed REAL, race_comment TEXT,sp_decimal REAL,age REAL,sex TEXT);''')
        co.execute("INSERT INTO historical_races VALUES ('r1','2026-10-20','Ascot','maiden','Turf')")
        co.execute("INSERT INTO historical_runs VALUES ('r1','EXAMPLE','old-id','IRE','Sire','Dam',3,'placed','A','B',80,130,NULL,NULL,NULL,NULL,7,3,'gelding')")
        co.commit();co.close()
        a=[{'horse':'Example','horse_id':'new-id','date':'2026-11-10','course':'Fairyhouse','race_name':'Handicap Hurdle',
           'race_time':'2026-11-10T12:30:00+00:00','form':'3','last_run':'21','sire':'Sire','dam':'Dam','trainer':'A',
           'jockey':'B','ofr':'85','lbs':'130','age':'3','sex':'gelding','distance_f':'16','going':'Good'},
           {'horse':'Debutant','horse_id':'nr-id','date':'2026-11-10','course':'Fairyhouse','race_name':'Maiden Hurdle',
            'form':'-','last_run':'-','sire':'New Sire','dam':'New Dam','trainer':'A','jockey':'B','ofr':'-',
            'lbs':'130','age':'3','sex':'gelding','distance_f':'16','going':'Good'}]
        s,rs,fs=audit(a,db,pathlib.Path(tmp)/'none',pathlib.Path(tmp)/'none.json',dt.date(2026,11,10),30)
        assert s['active_horses_in_cohort']==2
        assert s['horses_with_prior_run_evidence']==1
        assert rs[0]['matched_prior_runs']==1 and rs[0]['coverage_status']=='RECENT_HISTORY_SUPPORTED_NOT_FULL_CAREER_PROOF'
        assert rs[1]['coverage_status']=='NO_PRIOR_RUNS_REPORTED_UNVERIFIED'
        assert next(z for z in fs if z['feature']=='official_rating_handicaps' and z['group']=='current_card')['applicable']==1
    print('Stage84 self-test PASS: applicable gaps, novice/handicap treatment, pedigree and date safeguards')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db',type=pathlib.Path)
    ap.add_argument('--cards',type=pathlib.Path,default=pathlib.Path('data/racecards.json'))
    ap.add_argument('--archive',type=pathlib.Path,default=pathlib.Path('data/racecards-archive.json'))
    ap.add_argument('--recent-dir',type=pathlib.Path,default=pathlib.Path('data/racing-results'))
    ap.add_argument('--verified-results',type=pathlib.Path,default=pathlib.Path('data/race-results.json'))
    ap.add_argument('--as-of',type=str)
    ap.add_argument('--lookback-days',type=int,default=30)
    ap.add_argument('--output',type=pathlib.Path,default=pathlib.Path('reports/stage84'))
    ap.add_argument('--self-test',action='store_true')
    a=ap.parse_args()
    if a.self_test:self_test();return
    if not a.db or not a.db.exists():ap.error('--db must point to the validated read-only historical SQLite file')
    asof=iso(a.as_of) if a.as_of else dt.datetime.now(dt.timezone.utc).date()
    if not asof:ap.error('Invalid --as-of; expected YYYY-MM-DD')
    if not 1<=a.lookback_days<=365:ap.error('--lookback-days must be 1 through 365')
    cards=list(discover_cards(read_json(a.cards,{})))
    warnings=[]
    if a.archive.exists():
        try:cards.extend(discover_cards(read_json(a.archive,{})))
        except (ValueError,OSError) as exc:warnings.append(f'Historical racecards archive could not be parsed: {exc}')
    summary,horses,fields=audit(cards,a.db,a.recent_dir,a.verified_results,asof,a.lookback_days)
    summary['warnings']=warnings
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/'stage84_summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (a.output/'stage84_report.md').write_text(render_report(summary,fields)+'\n',encoding='utf-8')
    write_csv(a.output/'stage84_active_horses.csv',horses,list(horses[0]))
    write_csv(a.output/'stage84_applicable_field_coverage.csv',fields,
              ['group','feature','applicable','present','not_applicable','coverage_pct','threshold_pct','meets_90_if_applicable'])
    print(json.dumps({k:summary[k] for k in ('stage','as_of','active_horses_in_cohort','horses_with_prior_run_evidence',
             'horses_with_recent_form_supported_but_full_career_unverified','horse_coverage_status_counts',
             'lifetime_90pct_verified')},indent=2))
    print('Reports:',a.output)

if __name__=='__main__':main()
