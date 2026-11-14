#!/usr/bin/env python3
"""Stage125: apply two provenance-verified 2026 historical races to disposable SQLite.

Immutable source fixture lives in data/stage125-verified-prior-races.json.
No model scoring weights are changed, and the original source DB is never written.
Only use a race before the prediction day. This is not a general data scraper.
"""
import datetime as dt
import json
import pathlib
import re
import shutil
import sqlite3
import tempfile

SOURCE_FILE = 'data/stage125-verified-prior-races.json'
RECOVERY_ID = 'stage125-verified-races-v1'
APPROVED = {
    ('2026-10-31', 'rac_32302976524', 'The Rarest Hour', 'hrs_17394329722'),
    ('2026-11-02', 'rac_32303193468', 'Ocean View', 'hrs_17394794900'),
}


def canonical(v):
    import unicodedata
    s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().upper()
    s=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$', '',s)
    return re.sub('[^A-Z0-9]','',s)


def country_of(card, result):
    co=str(card.get('horse_country') or '').strip().lower()
    if co:return co
    for z in (card.get('horse'), result.get('horse')):
        m=re.search(r'\(([A-Z]{2,3})\)\s*$',str(z or ''),re.I)
        if m:return m.group(1).lower()
    return ''


def npos(s):
    try:
        n=int(str(s))
        return n if n>0 else None
    except (ValueError,TypeError):return None


def value(v):
    try:return float(str(v).strip())
    except (ValueError,TypeError):return None


def sp_decimal(res):
    s=str(res.get('sp') or '').strip().upper().replace(' ','')
    m=re.fullmatch(r'(\d+)/(\d+)(?:F|JF|CF)?',s)
    if m and int(m.group(2))>0:return 1+int(m.group(1))/int(m.group(2))
    if s in ('EVS','EVENS','EVEN','EVSF'):return 2.0
    return value(res.get('sp_decimal'))


def _oldnorm(z):
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 -]','',str(z or '').lower())).strip()


def _track(v):
    return _oldnorm(re.sub(r'\([^)]*\)','',str(v or '')))


def _source_starters(e):
    final=e['results']['runners']
    return [r for r in final if str(r.get('status') or '').lower() not in
      ('nr','nonrunner','non-runner','withdrawn','scratched') and
      str(r.get('position') or '').upper() not in ('NR','DNS')]


def validate_bundle(bundle):
    if bundle.get('version')!=RECOVERY_ID or len(bundle.get('races',[]))!=2:
        raise ValueError('Unexpected Stage125 recovery bundle version or number of races')
    seen=set()
    for e in bundle['races']:
        s=e['results']
        key=(s['date'],s['race_id'],e['target_horse'],e['target_horse_id'])
        if key not in APPROVED or key in seen:raise ValueError('Unapproved or duplicate Stage125 recovery race')
        seen.add(key)
        assert s['status']=='final' and s['off_dt'] and s['date']<='2026-11-02'
        assert e['preoff_commit'] and re.fullmatch(r'[0-9a-f]{40}',e['preoff_commit'])
        assert len(s.get('runners',[]))>=2
        rs=_source_starters(e)
        ps=e['preoff_runners']
        if len(rs)<2 or len(ps)!=len(rs) or len({canonical(r['horse']) for r in rs})!=len(rs):
            raise ValueError('Missing, duplicated or unmatched full race starters')
        if sum(npos(z.get('position'))==1 for z in rs)!=1:raise ValueError('One winner required')
        if {canonical(z['horse']) for z in ps}!={canonical(z['horse']) for z in rs}:
            raise ValueError('Preoff/official final runner identity mismatch')
        if {str(z['horse_id']) for z in ps}!={str(z['horse_id']) for z in rs}:
            raise ValueError('Preoff/official final provider IDs differ')
        ids=[str(z.get('horse_id') or '') for z in rs]
        if not all(ids) or len(ids)!=len(set(ids)):
            raise ValueError('Missing or duplicated source provider ID')
        matches=[z for z in rs if str(z['horse_id'])==e['target_horse_id']]
        if len(matches)!=1 or canonical(matches[0]['horse'])!=canonical(e['target_horse']):
            raise ValueError('Target identity does not match independent final result')
        for p in ps:
            if not all(p.get(x) for x in ('horse','horse_id','trainer','jockey','distance_f')):
                raise ValueError('Pre-off runner missing scorer fields')
            if not (p.get('type') or p.get('race_name')):
                raise ValueError('Pre-off race type missing')
            if str(p.get('date') or '')[:10]!=s['date'] or _track(p.get('course'))!=_track(s['course']):
                raise ValueError('Pre-off date/course mismatched')
        for r in rs:
            pos=str(r.get('position') or '').upper()
            if npos(pos) is None and pos not in ('PU','F','UR','BD','RO','RR','REF','DSQ','DNF'):
                raise ValueError('Unrecognised final result outcome')
    assert seen==APPROVED


def apply_verified_history(db, target, repo):
    """Return (database path, audit). Only the returned COPY contains additions."""
    date=dt.date.fromisoformat(target).isoformat()
    root=pathlib.Path(repo).resolve()
    f=root/SOURCE_FILE
    if not f.is_file():return db,{'status':'NOT_INSTALLED'}
    bundle=json.loads(f.read_text(encoding='utf-8'))
    validate_bundle(bundle)
    events=[e for e in bundle['races'] if e['results']['date']<date]
    if not events:return db,{'status':'ALL_EVENTS_POST_TARGET','added_races':0}
    base=pathlib.Path(db).resolve()
    cx=sqlite3.connect('file:'+str(base)+'?mode=ro',uri=True)
    todo=[];skips=[]
    try:
        for e in events:
            s=e['results']; day=s['date']; co=s['course']
            runners=_source_starters(e)
            keys={canonical(z['horse']) for z in runners}
            prior=cx.execute('SELECT id,course FROM historical_races WHERE race_date=?',(day,)).fetchall()
            already=False
            for rid,course in prior:
                if _track(course)!=_track(co):continue
                have={canonical(z[0]) for z in cx.execute('SELECT horse_name FROM historical_runs WHERE race_id=?',(rid,))}
                if keys==have:
                    already=True;skips.append({'race_id':s['race_id'],'reason':'EXACT_FULL_FIELD_ALREADY_IN_DB'});break
                if keys & have:
                    raise ValueError('Partial overlapping race detected; refuse unsafe Stage125 repair '+s['race_id'])
            if already:continue
            target_id=e['target_horse_id']
            dup=cx.execute('SELECT 1 FROM historical_runs u JOIN historical_races r ON r.id=u.race_id WHERE r.race_date=? AND u.horse_source_id=? LIMIT 1',(day,target_id)).fetchone()
            if dup:raise ValueError('Target runner already present on date under another race ID')
            todo.append(e)
    finally:cx.close()
    if not todo:return str(base),{'status':'ALREADY_PRESENT','added_races':0,'skipped':skips}
    p=pathlib.Path(tempfile.mkdtemp(prefix='stage125-verified-form-'))/'verified-history.sqlite'
    shutil.copy2(base,p)
    connection=sqlite3.connect(p)
    connection.execute('PRAGMA foreign_keys=ON')
    inserted=[]
    try:
        with connection:
            for e in todo:
                s=e['results'];day=s['date']; cards=e['preoff_runners'];finals={str(r['horse_id']):r for r in _source_starters(e)}
                rid='STAGE125|'+day+'|'+str(s['race_id'])
                first=cards[0]
                connection.execute('''INSERT INTO historical_races (id,race_date,course,race_type,surface,distance_f,going,race_class,source)
                  VALUES (?,?,?,?,?,?,?,?,?)''',(rid,day,s['course'],first.get('type') or first.get('race_name'),first.get('surface'),float(first['distance_f']),first.get('going'),first.get('race_class'),RECOVERY_ID))
                for i,card in enumerate(cards):
                    f=finals[str(card['horse_id'])]
                    pos=npos(f.get('position'))
                    outcome='FINISHED' if pos else str(f.get('position') or '').upper()
                    racehorse=re.sub(r'\s*\((?:IRE|GB|FR|USA|GER|AUS|NZ|IT|JPN)\)\s*$','',str(card['horse']),flags=re.I).strip()
                    country=country_of(card,f)
                    weight=value(card.get('weight_lbs') or card.get('lbs'))
                    draw=npos(card.get('draw'))
                    age=npos(card.get('age'))
                    connection.execute('''INSERT INTO historical_runs (race_id,runner_key,horse_source_id,horse_name,horse_name_norm,horse_country,finish_pos,outcome,trainer,jockey,official_rating,weight_lbs,sp_decimal,draw,age,sex,sire,dam)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
                        rid,str(i),str(card['horse_id']),racehorse,canonical(racehorse),country,pos,outcome,
                        card.get('trainer'),card.get('jockey'),value(card.get('official_rating') or card.get('ofr') or card.get('or')),
                        weight,sp_decimal(f),draw,age,card.get('sex'),card.get('sire'),card.get('dam')))
                inserted.append(s['race_id'])
    except Exception:
        connection.close()
        p.unlink(missing_ok=True)
        raise
    finally:connection.close()
    return str(p),{'status':'ADDED_VERIFIED_FULL_RACES','added_races':len(inserted),'race_ids':inserted,'skipped':skips,'source':SOURCE_FILE,'master_untouched':True}


if __name__=='__main__':
    assert canonical('The Rarest Hour (IRE)')==canonical('The Rarest Hour')
    assert canonical('Ocean View (GB)')==canonical('Ocean View')
    assert npos('PU') is None
    print('Stage125 verified historical source guard self-test PASS')
