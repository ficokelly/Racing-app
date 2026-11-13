#!/usr/bin/env python3
"""Stage 38: independent Model D and frozen Model Z course-candidate paper forecasts.

Reuses the exact Stage 30 eight-feature calculation and pre-2025 chosen weights.
No current/final odds or results are consumed as predictive features. Betting
ROI is NOT validated; a historical SP-only holdout was negative.

This file does not import, overwrite, or update the operational A/B/C models.
"""
from __future__ import annotations
import argparse, ast, collections, datetime as dt, inspect, json, math, re, sqlite3, sys, textwrap
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage30_gap_aware_abcd as OLD

UTC=dt.timezone.utc
IE=ZoneInfo('Europe/Dublin')
VERSION='model-d-stage38-2023-24-frozen-gap-aware'
ZVERSION='model-z-stage38-frozen-stage34-candidate-paper-v1'
METRICS=tuple(OLD.METRICS)
if METRICS!=('Ability','Trainer','Jockey','Course','Draw','Distance','Ground','Jumping'):
    raise RuntimeError('Historical Model D factor list was changed; refusing to publish')


def read_json(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):return {} if default is None else default


def write_json(path,obj):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix(p.suffix+'.tmp')
    temp.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    temp.replace(p)


def name(x):return OLD.normal(str(x or ''))


def date_key(s):return str(s or '')[:10]


def time_utc(s):
    if not s:return None
    try:
        d=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return d.replace(tzinfo=UTC) if not d.tzinfo else d.astimezone(UTC)
    except ValueError:return None


def clock(s):
    d=time_utc(s)
    return d.astimezone(IE).strftime('%H:%M') if d else str(s or '')[:5]


def nonrunner(r):
    status=re.sub('[^a-z]','',str(r.get('runner_status') or r.get('status') or '').lower())
    return bool(r.get('non_runner') is True or r.get('is_non_runner') is True or
                str(r.get('number') or '').upper()=='NR' or status in ('nr','nonrunner','withdrawn','scratched'))


def source_score():
    """Clone the reviewed Stage 30 scorer with only the LIVE eligibility guard.

    Safety: insist on exactly the expected source expressions before changing.
    No fabricated result / winning_index is inserted in historical or live data.
    """
    code=textwrap.dedent(inspect.getsource(OLD.Past.score))
    old1='def score(self,rows):'
    old2='if len(starters)<2 or len(winners)!=1:return None'
    old3='if day.year not in (2023,2024,2025):return None'
    old4="'winning_index':winners[0],'exact_event'"
    for token in (old1,old2,old3,old4):
        if code.count(token)!=1:raise RuntimeError('Stage30 scoring engine changed: '+token)
    code=code.replace(old1,'def score(self,rows,live=False):',1)
    code=code.replace(old2,'if len(starters)<2 or (not live and len(winners)!=1):return None',1)
    code=code.replace(old3,'if not live and day.year not in (2023,2024,2025):return None',1)
    code=code.replace(old4,"'winning_index':(winners[0] if winners else None),'exact_event'",1)
    assert not any(isinstance(n,(ast.Import,ast.ImportFrom)) for n in ast.parse(code).body)
    ns=dict(vars(OLD));exec(compile(code,'<reviewed-stage30-live-score>','exec'),ns)
    return ns['score']


class LivePast(OLD.Past):
    score=source_score()
    def scan_until(self,db,day):
        """Read every available UK/Irish historical starter before forecast day.

        Same-day results are excluded wholesale. Replays are strictly date ordered.
        """
        cx=sqlite3.connect('file:'+str(Path(db).resolve())+'?mode=ro',uri=True)
        sql='''SELECT r.id,r.race_date,r.course,r.race_type,r.surface,r.distance_f,r.going,r.race_class,
            u.horse_name_norm,u.horse_name,u.horse_country,u.horse_source_id,u.finish_pos,u.outcome,
            u.trainer,u.jockey,u.official_rating,u.age,u.sire,u.dam,u.sp_decimal,u.draw,u.race_comment
            FROM historical_races r JOIN historical_runs u ON u.race_id=r.id
            WHERE r.race_date < ? ORDER BY r.race_date,r.id'''
        last_race=None;group=[];seen=0;kept=0;last_date=None
        def flush():
            nonlocal kept
            if group and OLD.track(group[0][2]) in OLD.TRACKS:
                self.update(group);kept+=1
        for row in cx.execute(sql,(day,)):
            if group and (row[0]!=last_race):flush();group=[]
            group.append(row);last_race=row[0];last_date=row[1];seen+=1
            if seen%1000000==0:print('Historical rows',seen,'UK/Irish races',kept,flush=True)
        flush();cx.close()
        return {'runner_rows_scanned':seen,'historical_uk_ie_races':kept,'last_result_date':last_date}


def live_row(r,day,rid,unique_history_country=None):
    # Exact Stage30 23-column schema; all outcome/SP fields remain genuinely empty.
    h=str(r.get('horse') or '')
    country=r.get('horse_country') or r.get('country') or ''
    match=re.search(r'\(([A-Z]{2,3})\)\s*$',h)
    if not country and match:country=match.group(1)
    # Recover horse identity only when exactly one country matches historic starts.
    if not country and unique_history_country is not None:
        country=unique_history_country.get(name(h)) or ''
    course=str(r.get('course') or '')
    surface=r.get('surface') or r.get('track_surface') or ''
    race_type=r.get('race_type') or r.get('type') or r.get('race_name') or ''
    return (rid,day,course,race_type,surface,OLD.number(r.get('distance_f')),
        r.get('going'),r.get('race_class'),name(h),h,name(country),r.get('horse_id'),
        None,'declared',r.get('trainer'),r.get('jockey'),OLD.number(r.get('official_rating') or r.get('ofr') or r.get('or')),
        OLD.number(r.get('age')),r.get('sire'),r.get('dam'),None,OLD.number(r.get('draw')),None)


def prediction_rows(race,weights):
    active=np.asarray(weights,dtype=float).copy()
    if race['kind']=='jumps':active[4]=0.  # No draw effect in jumps
    else:active[7]=0.  # No jumping effect in flat / all-weather
    active/=max(1e-8,float(active.sum()))
    scores=np.asarray(race['x'][:,:8],dtype=float)@active
    order=np.argsort(-scores,kind='stable')
    gap=float(scores[order[0]]-scores[order[1]])
    return scores,order,gap


def make_forecast(repo,db,day=None):
    base=Path(repo);data=base/'data'
    day=day or dt.datetime.now(IE).date().isoformat()
    dt.date.fromisoformat(day)
    audit=read_json(base/'reports/stage30/stage30-summary.json')
    m=audit.get('model_results',{}).get('D',{})
    if audit.get('test_year')!=2025 or audit.get('status')!='RESEARCH_ONLY' or m.get('chosen_combination')!='D-064-gap14':
        raise RuntimeError('Refusing to publish: exact frozen 2023/24 Model D combination not verified')
    weights=[float(m['weights_pct'][k])/100 for k in METRICS]
    gate=float(m['selection_margin'])
    if len(weights)!=8 or abs(sum(weights)-1)>0.002 or abs(gate-0.14)>1e-9:
        raise RuntimeError('Historical Model D weights or decision threshold changed')
    cards=read_json(data/'racecards.json')
    fields=collections.defaultdict(list)
    for r in cards.get('runners',[]):
        if date_key(r.get('date') or r.get('race_time'))!=day or not r.get('course') or not r.get('race_time') or not r.get('horse') or nonrunner(r):continue
        fields[(name(r.get('course')),clock(r['race_time']))].append(r)
    fields={k:v for k,v in fields.items() if len(v)>=2 and len({name(r['horse']) for r in v})==len(v)}
    now=dt.datetime.now(UTC)
    zregistry=read_json(data/'model-z-strategies.json')
    zprofiles=read_json(base/'reports/stage34/stage34-frozen-track-profiles.json')
    z_by_course={}
    for item in zregistry.get('strategies',[]):
        if item.get('status') not in ('CANDIDATE','ACTIVE') or not item.get('paper_tracking_enabled'):continue
        key=item['frozen_profile_key']
        frozen=zprofiles.get('profiles',{}).get('courses',{}).get(key)
        if not frozen or any(abs(float(frozen['weights_pct'][k])-float(item['weights_pct'][k]))>1.e-6 for k in METRICS):
            raise RuntimeError('Model Z frozen registry and original Stage34 profile disagree: '+key)
        if abs(float(item['selection_margin'])-float(frozen['selection_margin']))>1.e-9:
            raise RuntimeError('Model Z stage34 frozen threshold mismatch: '+key)
        z_by_course[(item['discipline'],key.split('|',1)[1])]={'item':item,'weights':[float(frozen['weights_pct'][k])/100 for k in METRICS], 'gate':float(frozen['selection_margin'])}
    zoutput={'version':1,'model':'Z','model_version':ZVERSION,'day':day,'generated_at':now.isoformat(),
      'state':'NO_COMPLETE_FIELDS','races':[],'mode':'FROZEN_CANDIDATES_PAPER_ONLY',
      'eligible_courses':list(zregistry.get('strategies',[])), 'automatic_paper_only':True,
      'no_automatic_promotion':True,'live_models_changed':False,
      'message':'Every race is checked for specialist eligibility; no specialist outside the frozen candidate courses. Paper records only, no real bets.'}
    output={'version':1,'model':'D','model_version':VERSION,'day':day,'generated_at':now.isoformat(),
       'state':'NO_COMPLETE_FIELDS','historical_data_through':None,'races':[],
       'selection_policy':'RESEARCH_ONLY_NOT_PROVEN_PROFITABLE','selection_margin':gate,
       'metrics':METRICS,'weights_pct':m['weights_pct'],
       'holdout_2025':{'all_races':m['all_races_2025'],'selected_races':m['selected_races_2025']},
       'warning':'2025 selected SP ROI -12.88%; uncalibrated ranking scores, not winning probabilities or betting advice. No Betfair integration.'}
    if not fields:
        write_json(data/'strategy-d-probabilities.json',output)
        archive_and_settle(data,output)
        write_json(data/'strategy-z-probabilities.json',zoutput)
        archive_and_settle_z(data,zoutput)
        return output
    from stage116_verified_form_live import ensure_fresh_result_db,resolve_verified,apply_d_row
    db,_stage116_source=ensure_fresh_result_db(db,day,base)
    history=LivePast({});history_info=history.scan_until(db,day)
    _stage116_cards=[z for _group in fields.values() for z in _group]
    _stage116_aliases,_stage116_audit=resolve_verified(db,_stage116_cards,day)
    output["verified_form_recovery"]=_stage116_audit
    output["verified_form_source"]=_stage116_source
    output.update(historical_data_through=history_info['last_result_date'],historical_races_scanned=history_info['historical_uk_ie_races'])
    unique_history_country={}
    for historic_name,historic_country in history.last:
        if historic_name not in unique_history_country:unique_history_country[historic_name]=historic_country
        elif unique_history_country[historic_name]!=historic_country:unique_history_country[historic_name]=None
    for (_place,t),rs in sorted(fields.items(),key=lambda e:(e[0][1],e[0][0])):
        off=time_utc(rs[0]['race_time'])
        if not off or off<=now+dt.timedelta(minutes=2):continue
        rid='MODEL-D-LIVE|'+day+'|'+str(rs[0]['course'])+'|'+t
        rows=[apply_d_row(live_row(r,day,rid,unique_history_country),r,_stage116_aliases) for r in rs]
        race=history.score(rows,live=True)
        if race is None:continue
        scores,order,gap=prediction_rows(race,weights)
        runners=[]
        for ix,r in enumerate(rs):
            signals={k:{'score':round(float(race['x'][ix][j]),5),
                    'source':race['sources'][ix][j],
                    'sample_evidence_count':int(race['evidence_counts'][ix][j])}
                for j,k in enumerate(METRICS)}
            runners.append({'horse':r['horse'],'horse_id':r.get('horse_id'),
                'research_score':round(float(scores[ix]),5),'rank':int(np.where(order==ix)[0][0])+1,
                'category_signals':signals,'evidence_count':sum(v['sample_evidence_count'] for v in signals.values())})
        runners.sort(key=lambda r:r['rank'])
        top,second=runners[:2]
        selected=gap>=gate
        text=(f"Model D ranks {top['horse']} ahead of {second['horse']} using the eight historical evidence categories. "
              f"Research score lead {gap:.3f}, minimum {gate:.2f}. "
              f"{'Research shortlist only; unproven betting ROI.' if selected else 'No research shortlist: the lead is too small.'} "
              "The score is not a calibrated winning percentage and is not derived from bookmaker prices.")
        # Model Z checks ALL complete races; only original Stage34 specialists
        # can create immutable, pre-off candidate paper entries.
        course_key=OLD.track(rs[0]['course'])
        zprofile=z_by_course.get((race['kind'],course_key))
        if zprofile:
            zm=zprofile['item']
            zs,zo,zgap=prediction_rows(race,zprofile['weights'])
            zr=[]
            for k,rr in enumerate(rs):
                zr.append({'horse':rr['horse'],'horse_id':rr.get('horse_id'),
                           'research_score':round(float(zs[k]),6),
                           'rank':int(np.where(zo==k)[0][0])+1})
            zr.sort(key=lambda q:q['rank'])
            qualifies=zgap>=zprofile['gate']
            zoutput['races'].append({'model':'Z','date':day,'course':rs[0]['course'],
                'time':t,'race_time':rs[0]['race_time'],'race_type':race['kind'],
                'race_name':rs[0].get('race_name'),'field_size':len(rs),
                'strategy_id':zm['id'],'strategy_name':zm['name'],
                'strategy_version':zm['model_version'],'candidate':zm['status']=='CANDIDATE',
                'runners':zr,'selection':zr[0] if qualifies else None,
                'selection_status':'PAPER_ONLY' if qualifies else 'NO_SELECTION',
                'decision':{'leader':zr[0]['horse'],'second':zr[1]['horse'],
                            'gap':round(zgap,6),'minimum_gap':zprofile['gate'],
                            'metric':'UNCALIBRATED_STAGE34_SCORE'},
                'complete_field':True,'published_at':now.isoformat(),
                'verdict':('Frozen specialist ranks '+zr[0]['horse']+' first. '+
                    ('Candidate PAPER selection only; not approved as profitable.' if qualifies
                     else 'The frozen confidence threshold is not met; no paper selection.') )})
        output['races'].append({'model':'D','model_version':VERSION,'date':day,'course':rs[0]['course'],
            'time':t,'race_time':rs[0]['race_time'],'race_type':race['kind'],
            'race_name':rs[0].get('race_name'),'field_size':len(rs),'runners':runners,
            'ranking':[{'horse':r['horse'],'research_score':r['research_score']} for r in runners],
            'selection':top if selected else None,'selection_status':'RESEARCH_ONLY' if selected else 'NO_SELECTION',
            'decision':{'leader':top['horse'],'second':second['horse'],'gap':round(gap,5),
                        'minimum_gap':gate,'metric':'UNCALIBRATED_STRENGTH_SCORE'},
            'verdict':text,'published_at':now.isoformat(),'complete_field':True})
    output['state']='READY' if output['races'] else 'NO_UPCOMING_FIELDS'
    output['research_shortlists']=sum(r['selection_status']=='RESEARCH_ONLY' for r in output['races'])
    write_json(data/'strategy-d-probabilities.json',output)
    archive_and_settle(data,output)
    zoutput['state']='READY' if zoutput['races'] else 'NO_APPLICABLE_COURSE_RACES'
    zoutput['paper_qualifiers']=sum(q['selection_status']=='PAPER_ONLY' for q in zoutput['races'])
    write_json(data/'strategy-z-probabilities.json',zoutput)
    archive_and_settle_z(data,zoutput)
    print('Model D:',output['state'],'races:',len(output['races']),'research shortlists:',output['research_shortlists'],flush=True)
    return output


def fractional(s):
    s=str(s or '').strip().replace(' ','')
    if re.fullmatch(r'\d+/\d+',s):
        a,b=map(int,s.split('/'));return 1+a/b if b else None
    if s.upper() in ('EVS','EVENS','EVEN'):return 2.
    return None



def recorded_price(r):
    """Only use clearly identified pre-race source/time; no result-derived SP as pre-off quote."""
    for k in ('paddypower_decimal','paddy_power_decimal'):
        try:
            odds=float(r.get(k))
            if 1.01<odds<1000:return {'bookmaker':'Paddy Power','decimal':odds,
                 'observed_at':r.get('odds_updated_at') or r.get('odds_timestamp'),
                 'source':'racecard_fixed_odds'}
        except (TypeError,ValueError):pass
    return None


def archive_and_settle_z(data,feed):
    """Immutable pre-off horse, strategy/weight version and initial price snapshot.

    Unmatched/missing odds remain absent, and post-off rows are NEVER inserted.
    A changed field may supersede a pre-off entry but not rewrite its horse.
    """
    source=data/'strategy-z-paper-history.json'
    hist=read_json(source,{'version':1,'model':'Z','mode':'PAPER_ONLY','selections':[]})
    prior=hist.setdefault('selections',[])
    existing={(x.get('date'),name(x.get('course')),x.get('time'),x.get('strategy_id')):x
              for x in prior if x.get('status') in ('Pending','Superseded','Non-runner','Won','Lost')}
    now=dt.datetime.now(UTC)
    cards=read_json(data/'racecards.json')
    by_horse={(date_key(r.get('date') or r.get('race_time')),name(r.get('course')),
               clock(r.get('race_time')),name(r.get('horse'))):r
              for r in cards.get('runners',[]) if r.get('horse') and r.get('race_time')}
    for race in feed.get('races',[]):
        selected=race.get('selection')
        if race.get('selection_status')!='PAPER_ONLY' or not selected:continue
        off=time_utc(race.get('race_time'))
        if not off or off<=now+dt.timedelta(minutes=2):continue
        key=(race['date'],name(race['course']),race['time'],race['strategy_id'])
        old=existing.get(key)
        if old:
            if name(old.get('horse'))==name(selected.get('horse')):continue
            # Preserve and explicitly mark the earlier paper decision as superseded.
            if old.get('status')=='Pending':old['status']='Superseded';old['superseded_at']=feed['generated_at']
            else:continue
        card=by_horse.get((*key[:3],name(selected['horse'])))
        if card and nonrunner(card):continue
        snap=recorded_price(card or {})
        row={'date':race['date'],'course':race['course'],'time':race['time'],
             'race_time':race['race_time'],'race_type':race['race_type'],
             'horse':selected['horse'],'horse_id':selected.get('horse_id'),
             'strategy_id':race['strategy_id'],'strategy_name':race['strategy_name'],
             'strategy_version':race['strategy_version'],'model':'Z','mode':'PAPER_ONLY',
             'frozen_gate':race['decision']['minimum_gap'],
             'gap':race['decision']['gap'],'score':selected['research_score'],
             'published_at':feed['generated_at'],'source_preoff':True,
             'status':'Pending','odds':snap['decimal'] if snap else None,
             'bookmaker':snap['bookmaker'] if snap else None,
             'odds_observed_at':snap['observed_at'] if snap else None,
             'preoff_price_verified':bool(snap and snap['observed_at']),
             'price_source':snap['source'] if snap else None,
             'no_bet_placed':True}
        prior.append(row);existing[key]=row
    settled=[r for r in read_json(data/'race-results.json').get('races',[]) if r.get('status')=='final']
    for row in prior:
        if row.get('status')!='Pending':continue
        choices=[r for r in settled if r.get('date')==row['date']
             and name(r.get('course'))==name(row['course'])
             and clock(r.get('off_dt') or r.get('race_time') or '')==row['time']]
        if len(choices)!=1:continue
        match=next((r for r in choices[0].get('runners',[])
                    if name(r.get('horse'))==name(row['horse'])),None)
        if match is None:continue
        if name(match.get('status')) in ('nr','nonrunner','non runner','withdrawn','scratched'):
            row['status']='Non-runner';continue
        pos=OLD.number(match.get('position'))
        if pos is None:continue
        row['position']=int(pos);row['status']='Won' if pos==1 else 'Lost'
        row['official_sp_decimal']=fractional(match.get('sp'))
        row['settlement_source']='verified_race_result'
        row['settled_at']=now.isoformat()
    write_json(source,hist)
    write_json(data/'strategy-z-results.json',hist)

def archive_and_settle(data,forecast):
    hist=read_json(data/'strategy-d-history.json',{'version':1,'model':'D','selections':[]})
    old=hist.get('selections') or []
    ids={(r.get('date'),name(r.get('course')),r.get('time')) for r in old}
    for r in forecast['races']:
        if r['selection_status']!='RESEARCH_ONLY':continue
        k=r['date'],name(r['course']),r['time']
        if k in ids:continue
        if (time_utc(r['race_time']) or dt.datetime.min.replace(tzinfo=UTC))<=dt.datetime.now(UTC):continue
        old.append({'date':r['date'],'course':r['course'],'time':r['time'],'race_time':r['race_time'],
                'race_type':r['race_type'],'horse':r['selection']['horse'],
                'published_at':forecast['generated_at'],'research_score':r['selection']['research_score'],
                'status':'Pending','odds':None,'model':'D'})
        ids.add(k)
    results=read_json(data/'race-results.json')
    finalized=[x for x in results.get('races',[]) if x.get('status')=='final']
    for x in old:
        if x.get('status')!='Pending':continue
        possibilities=[z for z in finalized if z.get('date')==x['date'] and name(z.get('course'))==name(x['course'])
            and any(name(h.get('horse'))==name(x['horse']) for h in z.get('runners',[]))]
        if len(possibilities)!=1:continue
        match=next((h for h in possibilities[0].get('runners',[]) if name(h.get('horse'))==name(x['horse'])),None)
        if not match:continue
        s=name(match.get('status'))
        if s in ('nr','nonrunner','non runner','withdrawn','scratched'):
            x['status']='Non-runner';continue
        pos=OLD.number(match.get('position'))
        if pos is None:continue
        x['status']='Won' if pos==1 else 'Lost';x['position']=int(pos)
        x['odds']=fractional(match.get('sp'))
        x['settlement_source']='verified finished race / recorded SP (not live bookmaker price)'
    hist['selections']=old
    write_json(data/'strategy-d-history.json',hist)
    write_json(data/'strategy-d-results.json',hist)


def self_test():
    assert len(METRICS)==8
    assert nonrunner({'number':'NR'}) and nonrunner({'runner_status':'withdrawn'})
    assert not nonrunner({'status':'declared'})
    assert fractional('9/4')==3.25 and fractional('EVS')==2
    assert recorded_price({'paddypower_decimal':3.4,'odds_updated_at':'2026-11-03T11:00:00Z'})['decimal']==3.4
    assert recorded_price({'sp':'4/1'}) is None
    src=source_score()
    assert 'live' in inspect.signature(src).parameters
    t=LivePast({})
    rows=[live_row({'horse':h,'course':'Fairyhouse','race_time':'2026-11-04T12:00:00Z','race_type':'hurdle',
                    'distance_f':16,'trainer':'A','jockey':'J'},'2026-11-04','x') for h in ['Foo','Bar','Baz']]
    r=t.score(rows,live=True)
    assert r is not None and r['winning_index'] is None and r['x'].shape==(3,9)
    v=np.ones(8)/8;s,o,g=prediction_rows(r,v);assert len(o)==3 and g==0
    print('Stage38 independent D/Z self-test passed: live score, no fake results, 8 factors, non-runner guard')


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('live','self-test'))
    p.add_argument('--repo',default='.');p.add_argument('--db');p.add_argument('--day');a=p.parse_args()
    if a.command=='self-test':return self_test()
    if not a.db:p.error('--db required')
    make_forecast(a.repo,a.db,a.day)

if __name__=='__main__':main()
